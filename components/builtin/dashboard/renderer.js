/**
 * Dashboard Canvas Renderer
 *
 * Renders the Dashboard split-panel canvas and inline chat cards for TDA_Dashboard.
 *
 * Exports:
 *   renderDashboard(containerId, spec)    — ComponentRenderers registry entry
 *   openDashboardPanel(spec)             — open the split panel with a dashboard
 *   closeDashboardPanel()               — close the panel
 *   isDashboardPanelActive()            — boolean
 *   addTileFromComponent(payload, meta) — global API for "Pin to Dashboard" button
 *
 * Architecture:
 *   - Pure vanilla JS + CSS Grid (no React, no CDN deps beyond what's already loaded)
 *   - _injectCSS() once per page load
 *   - CSS variables throughout (theme-aware)
 *   - IFOC colour constants match configurationHandler.js canonical mapping
 *   - MutationObserver in dashboardHandler.js injects pin buttons into chat charts
 */

'use strict';

// ── IFOC profile colours (canonical) ─────────────────────────────────────────
const IFOC = {
  tool_enabled: { color: '#9333ea', label: 'Optimize' },
  llm_only:     { color: '#4ade80', label: 'Ideate'   },
  rag_focused:  { color: '#3b82f6', label: 'Focus'    },
  genie:        { color: '#F15F22', label: 'Coordinate'},
};

// ── Colour constants ──────────────────────────────────────────────────────────
const C = {
  accent:     'var(--sp-accent, var(--teradata-orange, #F15F22))',  // the live component accent (manifest icon_accent); brand as the last fallback
  accentFade: 'color-mix(in srgb, var(--sp-accent, var(--teradata-orange, #F15F22)) 12%, transparent)',
  blue:       'var(--status-info, #3b82f6)',
  purple:     'var(--profile-tag-bg-tool, #9333ea)',   // edit-mode / Optimize accent (theme-aware)
  purpleFade: 'color-mix(in srgb, var(--profile-tag-bg-tool, #9333ea) 12%, transparent)',
  green:      'var(--status-success, #10b981)',
  border:     'var(--border-primary, rgba(255,255,255,0.08))',
  bg:         'var(--card-bg, rgba(20, 22, 25,0.6))',
  bgOverlay:  'var(--bg-overlay, rgba(20, 22, 25,0.85))',
  text:       'var(--text-primary, #f1f5f9)',
  muted:      'var(--text-muted, #94a3b8)',
  subtle:     'var(--text-subtle, #475569)',
  shadow:     'var(--shadow-xl, 0 20px 40px rgba(0,0,0,0.5))',
};

// ── Layout & timing constants ─────────────────────────────────────────────────
const GRID_COLS           = 12;    // columns in the CSS grid
const GRID_ROW_PX         = 80;    // height of each grid row in pixels
const GRID_GAP_PX         = 10;    // gap between grid tracks (must match .dash-grid gap)
const TILE_REFRESH_POLL_MS = 4000; // polling interval while waiting for a tile refresh task
const TOAST_DURATION_MS   = 3000;  // how long toast notifications remain visible

// Pixel height of a tile spanning `rows` grid rows, including inter-row gaps.
function _spanHeightPx(rows) {
  return rows * GRID_ROW_PX + Math.max(0, rows - 1) * GRID_GAP_PX;
}

// ── Module state ──────────────────────────────────────────────────────────────
let _currentDashboardId = null;
let _dashboardData      = null;   // { id, name, tiles: [...] }
let _editMode           = false;
let _isDragging         = false;
let _dragTileId         = null;
let _dragStartX         = 0;
let _dragStartY         = 0;
let _resizeTileId       = null;
let _resizeStartW       = 0;
let _resizeStartH       = 0;
let _resizeStartX       = 0;
let _resizeStartY       = 0;
let _autoRefreshTimer   = null;
let _tsUpdateTimer      = null;
let _cssInjected        = false;
let _isDashFullscreen   = false;

// ── CSS (once per page) ───────────────────────────────────────────────────────
function _injectCSS() {
  if (_cssInjected) return;
  _cssInjected = true;

  const s = document.createElement('style');
  s.id = 'dashboard-renderer-css';
  s.textContent = `
/* ── Dashboard split panel ─────────────────────────────────────────────────── */
#dashboard-split-panel {
  width: 50%;
  min-width: 400px;
  max-width: 900px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-left: 1px solid ${C.border};
  background: ${C.bg};
  height: 100%;
  overflow: hidden;
  transition: width 0.3s cubic-bezier(0.16,1,0.3,1), opacity 0.3s ease;
}
#dashboard-split-panel.dash-split--open { width: 50%; }

/* Header chrome + instance switcher (.sp-header/.sp-switcher/.sp-action-btn/
   .sp-switcher-menu) is in static/css/split-panels.css */

/* ── Panel body ──────────────────────────────────────────────────────────────*/
.dash-panel-body {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  position: relative;
  scrollbar-width: thin;
  scrollbar-color: rgba(255,255,255,0.12) transparent;
}
.dash-panel-body::-webkit-scrollbar { width: 5px; }
.dash-panel-body::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.12); border-radius: 3px; }

/* ── Edit mode toolbar ───────────────────────────────────────────────────────*/
.dash-edit-toolbar {
  display: none;
  align-items: center;
  gap: 8px;
  padding: 6px 14px;
  background: ${C.purpleFade};
  border-bottom: 1px solid color-mix(in srgb, ${C.purple} 25%, transparent);
  font-size: 0.75rem;
  color: ${C.purple};
}
.dash-edit-toolbar.visible { display: flex; }
.dash-edit-toolbar svg { width: 12px; height: 12px; }

/* ── Dashboard grid ──────────────────────────────────────────────────────────*/
.dash-grid {
  display: grid;
  grid-template-columns: repeat(${GRID_COLS}, 1fr);
  grid-auto-rows: ${GRID_ROW_PX}px;
  gap: 10px;
  padding: 14px;
  min-height: 100%;
  box-sizing: border-box;
}
.dash-grid.edit-mode .dash-tile {
  cursor: grab;
  box-shadow: 0 0 0 1px color-mix(in srgb, ${C.purple} 25%, transparent);
}
.dash-grid.edit-mode .dash-tile:hover {
  box-shadow: 0 0 0 1.5px color-mix(in srgb, ${C.purple} 60%, transparent), 0 4px 20px color-mix(in srgb, ${C.purple} 15%, transparent);
}

/* ── Empty state ─────────────────────────────────────────────────────────────*/
.dash-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 320px;
  gap: 14px;
  padding: 40px 24px;
  text-align: center;
}
.dash-empty-icon {
  width: 56px;
  height: 56px;
  border-radius: 16px;
  background: ${C.accentFade};
  border: 1px solid rgba(241,95,34,0.2);
  display: flex;
  align-items: center;
  justify-content: center;
  color: ${C.accent};
}
.dash-empty-icon svg { width: 26px; height: 26px; }
.dash-empty-title {
  font-size: 0.95rem;
  font-weight: 600;
  color: ${C.text};
}
.dash-empty-body {
  font-size: 0.8rem;
  color: ${C.muted};
  max-width: 280px;
  line-height: 1.5;
}
.dash-empty-cta {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  background: ${C.accent};
  color: #fff;
  border: none;
  border-radius: 8px;
  font-size: 0.82rem;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
  transition: opacity 0.2s, transform 0.15s;
}
.dash-empty-cta:hover { opacity: 0.88; transform: translateY(-1px); }
.dash-empty-cta svg { width: 14px; height: 14px; }

/* ── Tile ────────────────────────────────────────────────────────────────────*/
.dash-tile {
  background: ${C.bg};
  border: 1px solid ${C.border};
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  position: relative;
  transition: box-shadow 0.2s, border-color 0.2s, transform 0.15s;
  animation: dash-tile-in 0.4s cubic-bezier(0.16,1,0.3,1) both;
  animation-delay: var(--dash-tile-delay, 0ms);
}
@keyframes dash-tile-in {
  from { opacity: 0; transform: translateY(12px) scale(0.97); }
  to   { opacity: 1; transform: translateY(0) scale(1); }
}
.dash-tile:hover { box-shadow: 0 4px 20px rgba(0,0,0,0.25); border-color: rgba(255,255,255,0.14); }
/* Table tiles shrink to content height instead of stretching to fill grid rows */
.dash-tile.dash-tile-table { align-self: start; }

/* ── Tile header ─────────────────────────────────────────────────────────────*/
.dash-tile-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 10px 6px;
  border-bottom: 1px solid ${C.border};
  background: var(--bg-secondary, rgba(0,0,0,0.15));
  flex-shrink: 0;
  min-height: 34px;
}
.dash-tile-drag-handle {
  display: none;
  color: ${C.muted};
  cursor: grab;
  padding: 2px;
  border-radius: 4px;
  flex-shrink: 0;
}
.dash-tile-drag-handle:hover { color: ${C.text}; background: var(--hover-bg, rgba(255,255,255,0.06)); }
.dash-tile-drag-handle svg { width: 12px; height: 12px; }
.edit-mode .dash-tile-drag-handle { display: flex; }
.dash-tile-title {
  font-size: 0.78rem;
  font-weight: 600;
  color: ${C.text};
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.edit-mode .dash-tile-title {
  cursor: text;
  padding: 1px 4px;
  border-radius: 4px;
  border: 1px solid transparent;
  margin: -1px -4px;
}
.edit-mode .dash-tile-title:hover { border-color: ${C.border}; background: var(--hover-bg, rgba(255,255,255,0.04)); }
.edit-mode .dash-tile-title:focus { border-color: ${C.purple}; background: color-mix(in srgb, ${C.purple} 6%, transparent); outline: none; }
.dash-tile-profile {
  font-size: 0.65rem;
  font-weight: 600;
  padding: 2px 6px;
  border-radius: 10px;
  flex-shrink: 0;
  letter-spacing: 0.02em;
}
.dash-tile-ts {
  font-size: 0.68rem;
  color: ${C.subtle};
  flex-shrink: 0;
  white-space: nowrap;
}
.dash-tile-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
  opacity: 0;
  transition: opacity 0.15s;
}
.dash-tile:hover .dash-tile-actions { opacity: 1; }
.dash-tile-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 5px;
  border: none;
  background: none;
  color: ${C.muted};
  cursor: pointer;
  transition: color 0.15s, background 0.15s;
  font-size: 0;
}
.dash-tile-btn:hover { color: ${C.text}; background: var(--hover-bg-strong, rgba(128,128,128,0.1)); }
.dash-tile-btn svg { width: 12px; height: 12px; }
.dash-tile-btn.spinning svg { animation: dash-spin 0.8s linear infinite; }
@keyframes dash-spin { to { transform: rotate(360deg); } }
/* ── Fullscreen / expand ─────────────────────────────────────────────────────*/
.dash-fullscreen #dashboard-split-panel.dash-split--open {
  position: fixed !important;
  top: var(--dash-fullscreen-top, 0px);
  left: 0 !important;
  right: 0 !important;
  bottom: 0 !important;
  width: auto !important;
  height: auto !important;
  max-width: none;
  z-index: 50;
}

/* ── Tile body ───────────────────────────────────────────────────────────────*/
.dash-tile-body {
  flex: 1;
  overflow: hidden;
  position: relative;
  min-height: 0;
}
/* Bottom scroll-fade for table tiles — signals "more rows below" so the
   scroll boundary reads as intentional, not a clipped/cut-off table.
   Toggled by JS (dash-has-scroll-fade) only while there is content below the fold. */
.dash-tile-body.dash-has-scroll-fade::after {
  content: '';
  position: absolute;
  left: 0; right: 0; bottom: 0;
  height: 30px;
  pointer-events: none;
  background: linear-gradient(to bottom, transparent, ${C.bg});
}
.dash-tile-chart-container {
  width: 100%;
  height: 100%;
  padding: 4px;
  box-sizing: border-box;
}
.dash-tile-table-container {
  width: 100%;
  height: auto;
  max-height: 100%;
  overflow: auto;
  padding: 6px;
  box-sizing: border-box;
  font-size: 0.74rem;
}
.dash-tile-table-container table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.78rem;
}
.dash-tile-table-container th,
.dash-tile-table-container td {
  padding: 5px 10px;
  border: 1px solid ${C.border};
  text-align: left;
  white-space: nowrap;
}
.dash-tile-table-container th {
  background: rgba(0,0,0,0.2);
  font-weight: 600;
  color: var(--text-primary, ${C.text});
  position: sticky;
  top: 0;
  z-index: 1;
}
.dash-tile-table-container td { color: var(--text-muted, ${C.muted}); }
.dash-tile-kpi-container {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 10px;
  box-sizing: border-box;
}
.dash-tile-narrative-container {
  width: 100%;
  height: 100%;
  overflow: auto;
  padding: 10px 12px;
  box-sizing: border-box;
  font-size: 0.8rem;
  color: ${C.text};
  line-height: 1.6;
}

/* ── Tile skeleton (loading) ─────────────────────────────────────────────────*/
.dash-tile-skeleton {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px;
  min-height: 0;
}
.dash-skeleton-line {
  border-radius: 4px;
  background: linear-gradient(
    90deg,
    rgba(255,255,255,0.06) 0%,
    rgba(255,255,255,0.12) 50%,
    rgba(255,255,255,0.06) 100%
  );
  background-size: 200% 100%;
  animation: dash-shimmer 1.4s ease-in-out infinite;
}
@keyframes dash-shimmer {
  0%   { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

/* ── Tile resize grip ────────────────────────────────────────────────────────*/
.dash-tile-resize-grip {
  display: none;
  position: absolute;
  bottom: 0;
  right: 0;
  width: 16px;
  height: 16px;
  cursor: se-resize;
  z-index: 5;
}
.dash-tile-resize-grip::before {
  content: '';
  position: absolute;
  bottom: 3px; right: 3px;
  width: 8px; height: 8px;
  border-right: 2px solid color-mix(in srgb, ${C.purple} 60%, transparent);
  border-bottom: 2px solid color-mix(in srgb, ${C.purple} 60%, transparent);
  border-radius: 0 0 2px 0;
}
.edit-mode .dash-tile-resize-grip { display: block; }

/* ── Tile error state ────────────────────────────────────────────────────────*/
.dash-tile-error {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 100%;
  padding: 12px;
  text-align: center;
}
.dash-tile-error-icon { color: #f87171; }
.dash-tile-error-icon svg { width: 22px; height: 22px; }
.dash-tile-error-msg { font-size: 0.75rem; color: ${C.muted}; }
.dash-tile-error-retry {
  font-size: 0.72rem;
  padding: 4px 12px;
  background: rgba(248,113,113,0.1);
  border: 1px solid rgba(248,113,113,0.25);
  color: #fca5a5;
  border-radius: 6px;
  cursor: pointer;
  font-family: inherit;
  transition: background 0.2s;
}
.dash-tile-error-retry:hover { background: rgba(248,113,113,0.2); }

/* ── Tile context menu ───────────────────────────────────────────────────────*/
.dash-tile-menu {
  position: absolute;
  top: 30px;
  right: 8px;
  background: var(--bg-overlay, rgba(20, 22, 25,0.96));
  border: 1px solid ${C.border};
  border-radius: 8px;
  box-shadow: ${C.shadow};
  z-index: 200;
  min-width: 160px;
  overflow: hidden;
  animation: dash-dropdown-in 0.15s cubic-bezier(0.16,1,0.3,1);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
}
.dash-menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  font-size: 0.78rem;
  color: ${C.text};
  cursor: pointer;
  transition: background 0.12s;
  border-bottom: 1px solid rgba(255,255,255,0.04);
}
.dash-menu-item:last-child { border-bottom: none; }
.dash-menu-item:hover { background: var(--hover-bg, rgba(255,255,255,0.06)); }
.dash-menu-item.danger:hover { background: color-mix(in srgb, var(--status-error, #ef4444) 10%, transparent); color: var(--status-error, #ef4444); }
.dash-menu-item svg { width: 13px; height: 13px; color: ${C.muted}; flex-shrink: 0; }
.dash-menu-item.danger svg { color: var(--status-error, #ef4444); }
.dash-menu-divider { height: 1px; background: ${C.border}; margin: 2px 0; }

/* ── Dashboard picker modal ──────────────────────────────────────────────────*/
.dash-picker-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.5);
  z-index: 9000;
  display: flex;
  align-items: center;
  justify-content: center;
  animation: dash-fade-in 0.15s ease;
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
}
@keyframes dash-fade-in { from { opacity: 0; } to { opacity: 1; } }
.dash-picker-modal {
  background: var(--bg-overlay, rgba(20, 22, 25,0.96));
  border: 1px solid ${C.border};
  border-radius: 14px;
  box-shadow: 0 24px 64px rgba(0,0,0,0.6);
  width: 340px;
  max-width: 95vw;
  overflow: hidden;
  animation: dash-modal-in 0.22s cubic-bezier(0.16,1,0.3,1);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
}
@keyframes dash-modal-in {
  from { opacity: 0; transform: scale(0.95) translateY(8px); }
  to   { opacity: 1; transform: scale(1) translateY(0); }
}
.dash-picker-header {
  padding: 14px 16px 10px;
  border-bottom: 1px solid ${C.border};
}
.dash-picker-title {
  font-size: 0.88rem;
  font-weight: 700;
  color: ${C.text};
}
.dash-picker-subtitle {
  font-size: 0.73rem;
  color: ${C.muted};
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dash-picker-list {
  padding: 6px;
  max-height: 240px;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: rgba(255,255,255,0.12) transparent;
}
.dash-picker-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 10px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.12s;
}
.dash-picker-item:hover { background: var(--hover-bg, rgba(255,255,255,0.06)); }
.dash-picker-item.selected { background: ${C.accentFade}; }
.dash-picker-item-icon {
  width: 30px;
  height: 30px;
  border-radius: 8px;
  background: rgba(241,95,34,0.1);
  border: 1px solid rgba(241,95,34,0.2);
  display: flex;
  align-items: center;
  justify-content: center;
  color: ${C.accent};
  flex-shrink: 0;
}
.dash-picker-item-icon svg { width: 14px; height: 14px; }
.dash-picker-item-name { font-size: 0.82rem; font-weight: 500; color: ${C.text}; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dash-picker-item-count { font-size: 0.7rem; color: ${C.muted}; flex-shrink: 0; }
.dash-picker-new {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  color: ${C.muted};
  font-size: 0.78rem;
  font-weight: 400;
  transition: background 0.12s, color 0.12s;
  border-top: 1px solid ${C.border};
  margin-top: 2px;
}
.dash-picker-new:hover { background: ${C.accentFade}; color: ${C.accent}; }
.dash-picker-new svg { width: 9px; height: 9px; flex-shrink: 0; }
.dash-picker-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 10px 12px;
  border-top: 1px solid ${C.border};
}
.dash-picker-btn {
  padding: 6px 14px;
  border-radius: 7px;
  font-size: 0.8rem;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
  transition: opacity 0.15s, transform 0.1s;
  border: 1px solid ${C.border};
  color: ${C.muted};
  background: none;
}
.dash-picker-btn:hover { background: var(--hover-bg, rgba(255,255,255,0.06)); color: ${C.text}; }
.dash-picker-btn.primary {
  background: ${C.accent};
  border-color: ${C.accent};
  color: #fff;
}
.dash-picker-btn.primary:hover { opacity: 0.88; transform: translateY(-1px); }
.dash-picker-btn.primary:disabled { opacity: 0.4; cursor: not-allowed; transform: none; }

/* ── Inline chat card ────────────────────────────────────────────────────────*/
.dash-inline-card {
  border: 1px solid ${C.border};
  border-radius: 10px;
  background: var(--bg-secondary, rgba(0,0,0,0.15));
  overflow: hidden;
  font-size: 0.8rem;
}
.dash-inline-card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 12px;
  border-bottom: 1px solid ${C.border};
  background: rgba(0,0,0,0.2);
}
.dash-inline-icon {
  width: 26px;
  height: 26px;
  border-radius: 7px;
  background: ${C.accentFade};
  border: 1px solid rgba(241,95,34,0.2);
  display: flex;
  align-items: center;
  justify-content: center;
  color: ${C.accent};
  flex-shrink: 0;
}
.dash-inline-icon svg { width: 13px; height: 13px; }
.dash-inline-title { font-weight: 600; color: ${C.text}; flex: 1; }
.dash-inline-badge {
  font-size: 0.67rem;
  padding: 2px 7px;
  border-radius: 10px;
  background: ${C.accentFade};
  border: 1px solid rgba(241,95,34,0.2);
  color: ${C.accent};
  font-weight: 600;
}
.dash-inline-body { padding: 10px 12px; color: ${C.muted}; line-height: 1.5; }
.dash-inline-footer {
  display: flex;
  gap: 8px;
  padding: 8px 12px;
  border-top: 1px solid ${C.border};
  flex-wrap: wrap;
}
.dash-inline-btn {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 5px 12px;
  border-radius: 6px;
  font-size: 0.76rem;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.15s;
  border: 1px solid ${C.border};
  color: ${C.muted};
  background: none;
  white-space: nowrap;
  flex-shrink: 0;
}
.dash-inline-btn:hover { background: var(--hover-bg, rgba(255,255,255,0.06)); color: ${C.text}; }
.dash-inline-btn.primary {
  background: ${C.accent};
  border-color: ${C.accent};
  color: #fff;
}
.dash-inline-btn.primary:hover { opacity: 0.88; }
.dash-inline-btn.icon-only { padding: 5px 7px; min-width: 0; }
.dash-inline-btn svg { width: 12px; height: 12px; }
.dash-inline-list { padding: 0; margin: 0; list-style: none; }
.dash-inline-list-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 12px;
  border-bottom: 1px solid rgba(255,255,255,0.04);
  transition: background 0.12s;
}
.dash-inline-list-item:last-child { border-bottom: none; }
.dash-inline-list-item:hover { background: rgba(255,255,255,0.04); }
.dash-tile-content-preview {
  padding: 10px 12px;
  color: var(--text-primary, ${C.text});
  font-size: 0.8rem;
  line-height: 1.55;
  overflow-x: auto;
  max-height: 320px;
  overflow-y: auto;
}
.dash-tile-content-preview table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.78rem;
}
.dash-tile-content-preview th,
.dash-tile-content-preview td {
  padding: 5px 10px;
  border: 1px solid ${C.border};
  text-align: left;
}
.dash-tile-content-preview th {
  background: rgba(0,0,0,0.2);
  font-weight: 600;
  color: ${C.text};
}
.dash-tile-content-preview td { color: var(--text-muted, ${C.muted}); }
.dash-inline-list-item-name { color: ${C.text}; font-weight: 500; flex: 1; }
.dash-inline-list-item-count { color: ${C.muted}; font-size: 0.72rem; }
.dash-inline-list-item-ts { color: ${C.subtle}; font-size: 0.7rem; }

/* ── Drag overlay ────────────────────────────────────────────────────────────*/
.dash-drag-ghost {
  position: fixed;
  pointer-events: none;
  z-index: 9999;
  opacity: 0.7;
  border-radius: 12px;
  border: 2px solid ${C.purple};
  background: ${C.purpleFade};
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  transition: none;
}
.dash-drop-indicator {
  border-radius: 10px;
  background: ${C.purpleFade};
  border: 2px dashed rgba(147,51,234,0.5);
  pointer-events: none;
  animation: dash-drop-pulse 1s ease-in-out infinite alternate;
}
@keyframes dash-drop-pulse {
  from { border-color: rgba(147,51,234,0.3); background: rgba(147,51,234,0.06); }
  to   { border-color: rgba(147,51,234,0.7); background: rgba(147,51,234,0.14); }
}

/* ── Inline visualization card ───────────────────────────────────────────────*/
.dash-viz-card {
  border: 1px solid var(--border-primary, rgba(255,255,255,0.08));
  border-radius: 12px;
  background: transparent;
  overflow: hidden;
}
.dash-viz-card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 12px;
  border-bottom: 1px solid var(--border-primary, rgba(255,255,255,0.06));
  background: var(--hover-bg-strong, rgba(0,0,0,0.12));
}
.dash-viz-title {
  font-size: 0.84rem;
  font-weight: 600;
  color: var(--text-primary, #f1f5f9);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dash-viz-badge-auto {
  font-size: 0.64rem;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  padding: 2px 7px;
  border-radius: 20px;
  background: rgba(147,51,234,0.12);
  border: 1px solid rgba(147,51,234,0.25);
  color: #a78bfa;
  flex-shrink: 0;
}
.dash-viz-open-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  flex-shrink: 0;
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--text-muted, #94a3b8);
  border-radius: 4px;
  padding: 0;
  opacity: 0.55;
  transition: opacity 0.15s, background 0.15s;
}
.dash-viz-open-btn:hover { opacity: 1; background: var(--hover-bg-strong, rgba(255,255,255,0.08)); }
.dash-viz-open-btn svg { width: 13px; height: 13px; }
.dash-viz-chart-area {
  height: 280px;
  min-height: 160px;
  position: relative;
  overflow: hidden;
}
.dash-viz-footer {
  display: flex;
  gap: 8px;
  padding: 9px 12px;
  border-top: 1px solid var(--border-primary, rgba(255,255,255,0.06));
  justify-content: flex-end;
  align-items: center;
}
.dash-viz-dest-row {
  display: flex;
  gap: 6px;
  align-items: center;
  flex: 1;
  min-width: 0;
}
.dash-viz-dest-select {
  flex: 1;
  min-width: 0;
  background: var(--card-bg, rgba(20, 22, 25,0.6));
  color: var(--text-primary, #f1f5f9);
  border: 1px solid var(--border-primary, rgba(255,255,255,0.12));
  border-radius: 6px;
  padding: 5px 8px;
  font-size: 0.75rem;
  font-family: inherit;
  cursor: pointer;
  outline: none;
}
.dash-viz-dest-select:disabled { opacity: 0.5; cursor: default; }
.dash-viz-dest-select option { background: #1f2126; color: #f1f5f9; }
.dash-kpi-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  padding: 16px;
  justify-content: center;
  align-items: stretch;
}
.dash-kpi-item {
  flex: 1;
  min-width: 90px;
  max-width: 180px;
  background: rgba(255,255,255,0.04);
  border: 1px solid var(--border-primary, rgba(255,255,255,0.08));
  border-radius: 10px;
  padding: 14px 12px;
  text-align: center;
}
.dash-kpi-value {
  font-size: 1.45rem;
  font-weight: 800;
  color: var(--sp-accent, #F15F22);
  line-height: 1;
  margin-bottom: 5px;
}
.dash-kpi-label {
  font-size: 0.72rem;
  color: var(--text-muted, #94a3b8);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
`;
  document.head.appendChild(s);

  // ── Light-mode overrides ───────────────────────────────────────────────────
  // Applied via [data-theme="light"] so they update automatically when the
  // user switches themes — no re-injection needed.
  const lt = document.createElement('style');
  lt.id = 'dashboard-renderer-light-css';
  lt.textContent = `
[data-theme="light"] #dashboard-split-panel,
[data-theme="light"] .dash-panel-body {
  background: #f8fafc;
}
[data-theme="light"] .dash-tile {
  background: #ffffff;
  border-color: rgba(148,163,184,0.28);
  box-shadow: 0 1px 4px rgba(0,0,0,0.06);
}
[data-theme="light"] .dash-tile:hover {
  box-shadow: 0 4px 16px rgba(0,0,0,0.10);
  border-color: rgba(148,163,184,0.45);
}
[data-theme="light"] .dash-tile-header {
  background: #f1f5f9;
  border-bottom-color: rgba(148,163,184,0.25);
}
[data-theme="light"] .dash-tile-body.dash-has-scroll-fade::after {
  background: linear-gradient(to bottom, transparent, #ffffff);
}
[data-theme="light"] .dash-tile-table-container th {
  background: #f1f5f9;
}
[data-theme="light"] .dash-tile-table-container th,
[data-theme="light"] .dash-tile-table-container td {
  border-color: rgba(148,163,184,0.25);
}
[data-theme="light"] .dash-panel-header {
  background: #f8fafc;
  border-bottom-color: rgba(148,163,184,0.25);
}
[data-theme="light"] .dash-hdr-btn:hover {
  background: rgba(0,0,0,0.05);
  border-color: rgba(148,163,184,0.35);
}
[data-theme="light"] .dash-grid.edit-mode .dash-tile {
  box-shadow: 0 0 0 1px rgba(147,51,234,0.25);
}
[data-theme="light"] .dash-grid.edit-mode .dash-tile:hover {
  box-shadow: 0 0 0 1.5px rgba(147,51,234,0.6), 0 4px 16px rgba(147,51,234,0.10);
}
/* ── Inline chat cards ── */
[data-theme="light"] .dash-inline-card {
  background: #ffffff;
  border-color: rgba(148,163,184,0.3);
  box-shadow: 0 1px 4px rgba(0,0,0,0.06);
}
[data-theme="light"] .dash-inline-card-header {
  background: #f1f5f9;
  border-bottom-color: rgba(148,163,184,0.25);
}
[data-theme="light"] .dash-inline-body {
  color: #475569;
}
[data-theme="light"] .dash-inline-footer {
  border-top-color: rgba(148,163,184,0.2);
  background: #fafafa;
}
[data-theme="light"] .dash-inline-btn {
  border-color: rgba(148,163,184,0.35);
  color: #475569;
}
[data-theme="light"] .dash-inline-btn:hover {
  background: rgba(0,0,0,0.04);
  color: #0f172a;
}
[data-theme="light"] .dash-inline-btn.primary {
  color: #fff;
  background: var(--sp-accent, #f15f22);
  border-color: var(--sp-accent, #f15f22);
}
[data-theme="light"] .dash-inline-btn.primary:hover {
  color: #fff;
  opacity: 0.88;
  background: #f15f22;
}
[data-theme="light"] .dash-inline-list-item {
  border-bottom-color: rgba(148,163,184,0.15);
}
[data-theme="light"] .dash-inline-list-item:hover {
  background: rgba(0,0,0,0.03);
}
/* ── Inline visualization cards (chart/table auto-viz in chat) ── */
[data-theme="light"] .dash-viz-card {
  border-color: rgba(148,163,184,0.3);
  box-shadow: 0 1px 4px rgba(0,0,0,0.06);
}
[data-theme="light"] .dash-viz-card-header {
  background: #f1f5f9;
  border-bottom-color: rgba(148,163,184,0.2);
}
[data-theme="light"] .dash-viz-footer {
  border-top-color: rgba(148,163,184,0.2);
  background: #fafafa;
}
`;
  document.head.appendChild(lt);

  // ── Theme-change observer ──────────────────────────────────────────────────
  // G2Plot charts bake the legend/label text colour into the rendered SVG at
  // render time. CSS variable changes (which handle tile backgrounds etc.) do
  // NOT update SVG fill attributes. So when the user switches between light and
  // dark mode we must re-render every chart tile so _patchChartColorsForTheme()
  // runs again with the correct _isLightTheme() result.
  new MutationObserver((mutations) => {
    for (const m of mutations) {
      if (m.attributeName === 'data-theme' && _dashboardData) {
        _renderGrid();
        break;
      }
    }
  }).observe(document.body, { attributes: true, attributeFilter: ['data-theme'] });
}

// ── SVG icons (monochrome, inline) ───────────────────────────────────────────
const _picon = (k) => ((typeof window !== 'undefined' && window.PanelIcons && window.PanelIcons[k]) || '');
const ICONS = {
  grid:     `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>`,
  chevron:  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>`,
  plus:     `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>`,
  refresh:  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>`,
  edit:     `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>`,
  check:    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`,
  settings: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>`,
  get close() { return _picon('close'); },   // the shared panel glyph (static/js/handlers/panelIcons.js)
  more:     `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/></svg>`,
  drag:     `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="9" y1="6" x2="9" y2="6"/><line x1="15" y1="6" x2="15" y2="6"/><line x1="9" y1="12" x2="9" y2="12"/><line x1="15" y1="12" x2="15" y2="12"/><line x1="9" y1="18" x2="9" y2="18"/><line x1="15" y1="18" x2="15" y2="18"/></svg>`,
  pin:      `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="17" x2="12" y2="22"/><path d="M5 17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1v4.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V17z"/></svg>`,
  trash:    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>`,
  get copy() { return _picon('copy'); },
  run:      `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg>`,
  bolt:     `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>`,
  error:    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`,
  chart:    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>`,
};

// ── Auth helper ───────────────────────────────────────────────────────────────
function _token() {
  return localStorage.getItem('tda_auth_token') || '';
}

function _authHeaders() {
  return { 'Authorization': `Bearer ${_token()}`, 'Content-Type': 'application/json' };
}

// ── Relative time ─────────────────────────────────────────────────────────────
function _relTime(isoStr) {
  if (!isoStr) return 'Never';
  const diff = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000);
  if (diff < 60)  return 'Just now';
  if (diff < 3600) return `${Math.floor(diff/60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff/3600)}h ago`;
  return `${Math.floor(diff/86400)}d ago`;
}

function _startTsUpdater() {
  if (_tsUpdateTimer) clearInterval(_tsUpdateTimer);
  _tsUpdateTimer = setInterval(() => {
    document.querySelectorAll('[data-tile-refreshed]').forEach(el => {
      el.textContent = _relTime(el.dataset.tileRefreshed);
    });
  }, 60000);
}

// ── Uderia-style dialogs (replace native prompt / confirm) ──────────────────

/**
 * Show a Uderia-styled single-line input dialog.
 * @param {string} title       - Dialog heading
 * @param {string} defaultVal  - Pre-filled input value
 * @param {function} onOk      - Called with the trimmed value when user confirms
 * @param {string}  [placeholder] - Optional input placeholder
 * @param {boolean} [multiline]   - Use textarea instead of input (for long SQL)
 */
function _showInputDialog(title, defaultVal, onOk, { placeholder = '', multiline = false } = {}) {
  const backdrop = document.createElement('div');
  backdrop.style.cssText = `
    position:fixed;inset:0;z-index:99999;
    background:rgba(0,0,0,0.55);backdrop-filter:blur(2px);
    display:flex;align-items:center;justify-content:center;
  `;

  const fieldTag = multiline ? 'textarea' : 'input';
  const fieldAttrs = multiline
    ? `rows="6" style="resize:vertical;"`
    : `type="text"`;

  backdrop.innerHTML = `
    <div style="
      background:var(--card-bg,#1e2636);border:1px solid var(--border-primary,rgba(255,255,255,0.1));
      border-radius:12px;padding:20px 24px;min-width:360px;max-width:560px;width:90%;
      box-shadow:0 8px 32px rgba(0,0,0,0.4);display:flex;flex-direction:column;gap:14px;
    ">
      <div style="font-weight:600;font-size:0.9rem;color:var(--text-primary)">${_esc(title)}</div>
      <${fieldTag} ${fieldAttrs} placeholder="${_esc(placeholder)}"
        style="
          width:100%;padding:8px 10px;border-radius:7px;border:1px solid var(--border-primary,rgba(255,255,255,0.15));
          background:rgba(0,0,0,0.25);color:var(--text-primary);font-size:0.84rem;
          font-family:inherit;outline:none;box-sizing:border-box;
        "
      ></${fieldTag}>
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <button data-action="cancel" style="
          padding:6px 16px;border-radius:7px;border:1px solid var(--border-primary,rgba(255,255,255,0.15));
          background:transparent;color:var(--text-muted);cursor:pointer;font-size:0.82rem;
        ">Cancel</button>
        <button data-action="ok" style="
          padding:6px 16px;border-radius:7px;border:none;
          background:var(--teradata-orange,#F15F22);color:#fff;cursor:pointer;
          font-size:0.82rem;font-weight:600;
        ">OK</button>
      </div>
    </div>
  `;

  const field = backdrop.querySelector(fieldTag);
  field.value = defaultVal || '';

  const close = () => backdrop.remove();
  backdrop.querySelector('[data-action="cancel"]').addEventListener('click', close);
  backdrop.querySelector('[data-action="ok"]').addEventListener('click', () => {
    const val = field.value.trim();
    close();
    if (val) onOk(val);
  });
  field.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !multiline) { e.preventDefault(); backdrop.querySelector('[data-action="ok"]').click(); }
    if (e.key === 'Escape') close();
  });

  document.body.appendChild(backdrop);
  requestAnimationFrame(() => { field.focus(); if (!multiline) field.select(); });
}

// ── Panel open / close ────────────────────────────────────────────────────────

// ── PROP-008 §6 — act again from a pinned action tile ────────────────────────
// A pinned act is a RECORD of one invocation. "Prepare again" asks the same contract for a
// fresh consequence as the agent that prepared it (its grant is checked server-side, the
// database computes before → after in a transaction that is rolled back) and shows the SAME
// row the answer draws, with its Confirm. Nothing is ever performed without that click.

// The pinned row's invocation, or null when it cannot be prepared again (an undo, or a row
// without an action). Pure.
export function _actAgainSource(pendingAttr) {
  let p = null;
  try { p = JSON.parse(pendingAttr || '{}'); } catch (_) { return null; }
  if (!p || typeof p !== 'object' || !p.action || p.undo || p.undoes) return null;
  if (!p.invocation || typeof p.invocation !== 'object') return null;
  return p;
}

// The preview body: the pinned invocation, as the agent that prepared it (else the tile's
// own agent), through its connection, labelled as clicked on the dashboard. Pure.
export function _actAgainBody(p, tile) {
  const body = { invocation: p.invocation, face: 'dashboard' };
  if (p.kg_id) body.kg_id = p.kg_id;
  const agent = p.profile_id || (tile && tile.profile_id) || '';
  if (agent) body.profile_id = agent;
  if (p.connection_id) body.connection_id = p.connection_id;
  return body;
}

function _mountActAgain(host, tile) {
  const row = host.querySelector('.act-row[data-act-pending]');
  const p = row && _actAgainSource(row.getAttribute('data-act-pending'));
  if (!p) return;
  const bar = document.createElement('div');
  bar.className = 'dash-act-again';
  bar.style.cssText = 'display:flex;align-items:center;gap:8px;margin-top:8px;flex-wrap:wrap';
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'dash-act-again-btn';
  btn.textContent = 'Prepare again';
  btn.setAttribute('aria-label', `Prepare ${p.action} again — shows what it would change before you confirm`);
  // the row's own act-button look (action renderer: .act-row .act-acts button), so the
  // tile's acts read as one set
  btn.style.cssText = 'font-size:12px;font-weight:600;padding:4px 12px;border-radius:6px;cursor:pointer;'
    + 'background:transparent;color:var(--text-primary);border:1px solid var(--border-primary)';
  const note = document.createElement('span');
  note.className = 'dash-act-again-note';
  note.setAttribute('role', 'status');
  note.style.cssText = 'font-size:11px;color:var(--text-muted)';
  bar.appendChild(btn); bar.appendChild(note);
  host.appendChild(bar);
  // Offered only once the act has SETTLED: a row still awaiting its Confirm already offers
  // the owed act, and a second one beside it would not be owed. The row flips in place
  // (data-act-state), so the bar follows it.
  // the bar's own display:flex would override the hidden attribute — set display itself
  const sync = () => { const p = _actRowPending(row); bar.hidden = p; bar.style.display = p ? 'none' : 'flex'; };
  sync();
  let mo = null;
  if (typeof MutationObserver === 'function') {
    mo = new MutationObserver(sync);
    mo.observe(row, { attributes: true, attributeFilter: ['data-act-state'] });
  }
  btn.addEventListener('click', async () => {
    btn.disabled = true; note.textContent = 'Asking the database what would change…';
    let res = null, j = {};
    try {
      res = await fetch(`/api/v1/actions/${encodeURIComponent(p.action)}/preview`,
                        { method: 'POST', headers: _authHeaders(), body: JSON.stringify(_actAgainBody(p, tile)) });
      j = await res.json().catch(() => ({}));
    } catch (e) { j = { message: String(e) }; }
    if (res && res.ok && j.row_html) {
      if (mo) mo.disconnect();
      host.innerHTML = j.row_html;   // the fresh consequence and its Confirm — nothing written yet
      _mountActAgain(host, tile);    // offered again once THIS act settles
      return;
    }
    btn.disabled = false;
    note.style.color = 'var(--trust-danger, #f87171)';
    note.textContent = `Cannot prepare it again — ${j.refusal || j.message || 'the request failed'}. Nothing was written.`;
  });
}

// A row still awaiting its Confirm (or being confirmed). Pure.
export function _actRowPending(row) {
  const st = row && row.getAttribute ? row.getAttribute('data-act-state') : null;
  return !st || st === 'pending' || (row.getAttribute && row.getAttribute('aria-busy') === 'true');
}

export function openDashboardPanel(spec) {
  const panel = document.getElementById('dashboard-split-panel');
  if (!panel) { console.warn('[DashboardRenderer] #dashboard-split-panel not found'); return; }
  _injectCSS();

  _currentDashboardId = spec.dashboard_id || (spec.dashboard && spec.dashboard.id) || spec.id || null;
  _editMode = false;

  // Set header title
  const nameEl = document.getElementById('dashboard-split-title');
  const dashName = spec.name || (spec.dashboard && spec.dashboard.name) || 'Dashboard';
  if (nameEl) nameEl.textContent = dashName;

  panel.style.display = 'flex';
  panel.offsetHeight;
  panel.classList.add('dash-split--open');

  _wirePanelButtons();
  _reportDashShown(_currentDashboardId || '*');

  // Load and render
  if (spec.tiles !== undefined) {
    // Data already in spec (from TDA_Dashboard open action)
    _dashboardData = {
      ...((spec.dashboard) || { id: _currentDashboardId, name: dashName }),
      tiles: spec.tiles || [],
    };
    _renderGrid();
    // Auto-refresh pending tiles (same logic as _loadAndRenderDashboard path)
    (_dashboardData.tiles || [])
      .filter(t => t.refresh_status === 'pending')
      .forEach(t => _refreshTile(t.id));
  } else if (_currentDashboardId) {
    _loadAndRenderDashboard(_currentDashboardId);
  }

  _startTsUpdater();
}

// ── Open-in-panel control (shared: static/js/handlers/panelOpenControl.js) ─────
// The dashboard card and every row of the dashboard list use the one control; the
// panel reports which dashboard it shows (open, switch, close).
const _dashCardSpecs = new Map();
function _rememberDashSpec(id, spec) { if (id) _dashCardSpecs.set(String(id), spec); }
function _dashOpenControlHtml(id, name, row) {
  const poc = window.PanelOpenControl;
  if (!poc) return '';
  return poc.html({ component: 'dashboard', id: id || '*', accent: '#ec4899',
                    panelId: 'dashboard-split-panel', name: name || 'Dashboard', row: !!row });
}
function _reportDashShown(id) {
  try { window.PanelOpenControl && window.PanelOpenControl.shown('dashboard', id); } catch (_) {}
}
if (typeof window !== 'undefined') {
  // Registered through the queue: this renderer may load before panelOpenControl.js.
  (window.__panelOpenQueue = window.__panelOpenQueue || []).push(['dashboard', {
    open(id) {
      if (!id || id === '*') return;
      openDashboardPanel(_dashCardSpecs.get(String(id)) || { dashboard_id: id });
    },
    close() { closeDashboardPanel(); },
  }]);
  if (window.PanelOpenControl && window.PanelOpenControl.drain) window.PanelOpenControl.drain();
}

export function closeDashboardPanel(saveForSession) {
  const panel = document.getElementById('dashboard-split-panel');
  if (!panel) return;

  // Save which dashboard was open so the session can restore it on next switch
  if (saveForSession && _currentDashboardId) {
    localStorage.setItem(`dashboard_state_${saveForSession}`, _currentDashboardId);
  }

  _editMode = false;
  _currentDashboardId = null;
  _dashboardData = null;
  if (_autoRefreshTimer) { clearInterval(_autoRefreshTimer); _autoRefreshTimer = null; }
  if (_tsUpdateTimer) { clearInterval(_tsUpdateTimer); _tsUpdateTimer = null; }

  panel.classList.remove('dash-split--open');

  // Exit fullscreen if active
  if (_isDashFullscreen) {
    _isDashFullscreen = false;
    const mainArea = document.getElementById('main-content-area');
    if (mainArea) mainArea.classList.remove('dash-fullscreen');
    document.documentElement.style.removeProperty('--dash-fullscreen-top');
    const expandBtn = document.getElementById('dashboard-split-expand');
    if (expandBtn) expandBtn.title = 'Fullscreen dashboard';
  }

  // Every dashboard card and list row reads "View in Canvas" again.
  _reportDashShown(null);

  setTimeout(() => {
    if (!panel.classList.contains('dash-split--open')) {
      panel.style.display = 'none';
      const content = document.getElementById('dashboard-split-content');
      if (content) content.innerHTML = '';
    }
  }, 350);
}

export function isDashboardPanelActive() {
  return document.getElementById('dashboard-split-panel')?.classList.contains('dash-split--open') || false;
}

/** Returns the ID of the currently open dashboard, or null if panel is closed. */
export function getOpenDashboardState() {
  return _currentDashboardId || null;
}

// ── Load dashboard from API ───────────────────────────────────────────────────
// Component signals (components/signals.py): the open dashboard follows its changes — a tile an
// agent turn added, a pin from a card, a REST write, another tab. Not a reload: 'refreshed' (a
// refresh STARTED; its result lands as the tile's own update) and a layout save this page made
// itself (the bus marks its echo `own`) — a layout saved elsewhere IS applied, so two tabs agree.
// Nothing reloads while dragging, resizing or in edit mode.
if (typeof document !== 'undefined' && typeof UderiaBus !== 'undefined' && !window.__dashFollowBound) {
  window.__dashFollowBound = true;
  let _followTimer = null;
  UderiaBus.onComponent('dashboard', (e) => {
    const d = (e && e.detail) || {};
    const panel = document.getElementById('dashboard-split-panel');
    if (!panel || !panel.classList.contains('dash-split--open') || !_currentDashboardId) return;
    if (d.ids && d.ids.length && !d.ids.includes(String(_currentDashboardId))) return;
    if (d.action === 'deleted') { try { closeDashboardPanel(false); } catch (_) { /* closing is a courtesy */ } return; }
    if (d.action === 'refreshed') return;
    if (d.aspect === 'layout' && d.own) return;
    if (_isDragging || _resizeTileId || _editMode) return;
    clearTimeout(_followTimer);
    _followTimer = setTimeout(() => { try { _loadAndRenderDashboard(_currentDashboardId); } catch (_) { /* a reload is a courtesy */ } }, 300);
  });
}

async function _loadAndRenderDashboard(dashboardId) {
  const content = document.getElementById('dashboard-split-content');
  if (!content) return;

  // Show skeleton while loading
  content.innerHTML = `
    <div class="dash-grid" style="grid-auto-rows:${GRID_ROW_PX}px;">
      ${Array(4).fill(0).map(() => `
        <div class="dash-tile" style="grid-column:span 6; grid-row:span 4; animation:none;">
          <div class="dash-tile-header">
            <div class="dash-skeleton-line" style="height:10px;width:40%;"></div>
          </div>
          <div class="dash-tile-skeleton">
            <div class="dash-skeleton-line" style="height:8px;width:80%;"></div>
            <div class="dash-skeleton-line" style="height:8px;width:60%;"></div>
            <div class="dash-skeleton-line" style="flex:1;"></div>
          </div>
        </div>`).join('')}
    </div>`;

  try {
    const res = await fetch(`/api/v1/dashboards/${dashboardId}`, {
      headers: _authHeaders(),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    _dashboardData = await res.json();
    _renderGrid();

    // Auto-refresh pending tiles
    if (_dashboardData.tiles) {
      _dashboardData.tiles.filter(t => t.refresh_status === 'pending')
        .forEach(t => _refreshTile(t.id));
    }
  } catch (err) {
    content.innerHTML = `<div class="dash-empty"><div class="dash-empty-icon">${ICONS.error}</div>
      <div class="dash-empty-title">Could not load dashboard</div>
      <div class="dash-empty-body">${_esc(err.message)}</div></div>`;
  }
}

// ── Render the grid ───────────────────────────────────────────────────────────
function _renderGrid() {
  const content = document.getElementById('dashboard-split-content');
  if (!content || !_dashboardData) return;

  // Update switcher button name
  const nameEl = document.getElementById('dashboard-split-title');
  if (nameEl) nameEl.textContent = _dashboardData.name || 'Dashboard';

  const tiles = _dashboardData.tiles || [];

  if (tiles.length === 0) {
    content.innerHTML = `
      <div class="dash-empty">
        <div class="dash-empty-icon">${ICONS.grid}</div>
        <div class="dash-empty-title">Dashboard is empty</div>
        <div class="dash-empty-body">
          Hover over any chart or table in the conversation and click
          <strong>Pin to Dashboard</strong> to add it here.
        </div>
        <button class="dash-empty-cta" id="dash-empty-chat-btn">
          ${ICONS.plus} Start chatting
        </button>
      </div>`;
    document.getElementById('dash-empty-chat-btn')?.addEventListener('click', () => {
      document.getElementById('user-input')?.focus();
    });
    return;
  }

  const grid = document.createElement('div');
  grid.className = `dash-grid${_editMode ? ' edit-mode' : ''}`;
  grid.id = 'dash-grid-root';
  content.innerHTML = '';
  content.appendChild(grid);

  tiles.forEach((tile, idx) => {
    const el = _buildTileElement(tile, idx);
    grid.appendChild(el);
    // Render chart content after DOM insertion
    requestAnimationFrame(() => _renderTileContent(tile));
  });

  _wireGridDragDrop(grid);
}

// ── Build one tile DOM element ────────────────────────────────────────────────
function _buildTileElement(tile, idx) {
  const el = document.createElement('div');
  el.className = tile.component_id === 'table' ? 'dash-tile dash-tile-table' : 'dash-tile';
  el.id = `dash-tile-${tile.id}`;
  el.dataset.tileId = tile.id;
  el.style.setProperty('--dash-tile-delay', `${idx * 50}ms`);
  el.style.cssText += `
    grid-column: ${tile.pos_col + 1} / span ${tile.tile_width};
    grid-row: ${tile.pos_row + 1} / span ${tile.tile_height};
  `;
  // Table tiles use `align-self: start` to shrink short tables to content height.
  // Cap them to the grid-row span so tall tables scroll internally instead of
  // overflowing their track and overlapping the tile below.
  if (tile.component_id === 'table') {
    el.style.maxHeight = `${_spanHeightPx(tile.tile_height)}px`;
  }

  // Resolve the tile's profile for the badge. CRITICAL: resolve by TAG first so
  // the badge colour matches the DISPLAYED tag and handles overrides correctly —
  // an @OVERRIDE tile may carry a profile_id that diverges from the tag, and the
  // colour must follow the tag the user sees. Fall back to profile_id, then to a
  // tag-only badge. The colour is sourced from the profile's own `color` field —
  // the SAME source the chat profile chip uses (ui.js addMessage) — so the tile
  // badge is pixel-identical to the chip. (The old code returned type:null
  // whenever a tag was present, so the type was never resolved and the colour
  // fell through to the tool_enabled purple default — making every custom-tagged
  // profile look like Optimize.)
  const _resolvedProfile = (() => {
    const profiles = window?.configState?.profiles || [];
    let p = null;
    if (tile.profile_tag) p = profiles.find(pr => (pr.tag || '') === tile.profile_tag);
    if (!p && tile.profile_id) p = profiles.find(pr => (pr.id || pr.profile_id) === tile.profile_id);
    if (p) return { tag: tile.profile_tag || p.tag || '', type: p.profile_type || '', color: p.color || '' };
    if (tile.profile_tag) {
      console.debug(`[Dashboard] profile tag "${tile.profile_tag}" not found in configState.profiles — showing tag-only badge for tile "${tile.title}"`);
      return { tag: tile.profile_tag, type: '', color: '' };
    }
    return null;
  })();
  const _displayTag  = _resolvedProfile?.tag || '';
  const _profileType = _resolvedProfile?.type || '';
  const _ptag = _displayTag.toUpperCase();
  const profileColor = !_displayTag ? C.muted
    : (_resolvedProfile?.color                                  // same source as the chat chip
     || (_profileType === 'llm_only'     ? IFOC.llm_only.color
     :   _profileType === 'rag_focused'  ? IFOC.rag_focused.color
     :   _profileType === 'genie'        ? IFOC.genie.color
     :   _profileType === 'tool_enabled' ? IFOC.tool_enabled.color
     :   _ptag.includes('IDEAT') || _ptag.includes('CHAT')  ? IFOC.llm_only.color
     :   _ptag.includes('FOCUS') || _ptag.includes('RAG')   ? IFOC.rag_focused.color
     :   _ptag.includes('COORD') || _ptag.includes('GENIE') ? IFOC.genie.color
     :   _ptag.includes('OPTIM')                            ? IFOC.tool_enabled.color
     :   C.muted));                                            // unknown ≠ Optimize

  const refreshStatus = tile.refresh_status || 'ok';
  const isRefreshing = refreshStatus === 'refreshing' || refreshStatus === 'pending';

  el.innerHTML = `
    <div class="dash-tile-header">
      <div class="dash-tile-drag-handle" title="Drag to reorder">${ICONS.drag}</div>
      <span class="dash-tile-title"
            data-tile-field="title"
            ${_editMode ? 'contenteditable="true"' : ''}
            title="${_esc(tile.title)}">${_esc(tile.title)}</span>
      ${_displayTag ? `<span class="dash-tile-profile"
            style="color:${profileColor};background:${profileColor}22;border:1px solid ${profileColor}44;">
            ${_esc(_displayTag)}</span>` : ''}
      <span class="dash-tile-ts"
            data-tile-refreshed="${tile.last_refreshed || ''}">${_relTime(tile.last_refreshed)}</span>
      <div class="dash-tile-actions">
        <button class="dash-tile-btn${isRefreshing ? ' spinning' : ''}"
                title="${tile.component_id === 'flow' ? 'Sync with live flow' : tile.component_id === 'explain_plan' ? 'Redraw the pinned plan' : tile.component_id === 'action' ? 'Re-read how this act ended' : tile.mcp_tool_name ? 'Refresh data directly (no LLM)' : 'Re-run query'}"
                data-action="refresh"
                data-tile-id="${tile.id}">${tile.mcp_tool_name ? ICONS.bolt : ICONS.refresh}</button>
        <button class="dash-tile-btn"
                title="More options"
                data-action="menu"
                data-tile-id="${tile.id}">${ICONS.more}</button>
      </div>
    </div>
    <div class="dash-tile-body" id="dash-tile-body-${tile.id}">
      ${isRefreshing ? _skeletonHTML() : ''}
    </div>
    <div class="dash-tile-resize-grip" data-tile-id="${tile.id}"></div>
  `;

  // Wire tile header events
  el.querySelector('[data-action="refresh"]')?.addEventListener('click', (e) => {
    e.stopPropagation();
    _refreshTile(tile.id);
  });

  el.querySelector('[data-action="menu"]')?.addEventListener('click', (e) => {
    e.stopPropagation();
    _showTileMenu(tile.id, e.currentTarget);
  });

  // Edit mode: title edit
  const titleEl = el.querySelector('.dash-tile-title');
  if (_editMode && titleEl) {
    titleEl.addEventListener('blur', () => {
      const newTitle = titleEl.textContent.trim();
      if (newTitle && newTitle !== tile.title) {
        _patchTile(tile.id, { title: newTitle });
        tile.title = newTitle;
      }
    });
    titleEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); titleEl.blur(); }
    });
  }

  // Resize grip
  const grip = el.querySelector('.dash-tile-resize-grip');
  if (grip) grip.addEventListener('pointerdown', (e) => _startResize(e, tile, el));

  // Drag handle
  const handle = el.querySelector('.dash-tile-drag-handle');
  if (handle) handle.addEventListener('pointerdown', (e) => _startDrag(e, tile, el));

  return el;
}

function _skeletonHTML() {
  return `<div class="dash-tile-skeleton">
    <div class="dash-skeleton-line" style="height:8px;width:70%;"></div>
    <div class="dash-skeleton-line" style="height:8px;width:50%;"></div>
    <div class="dash-skeleton-line" style="flex:1;margin-top:8px;"></div>
  </div>`;
}

// ── Render tile content (chart / table / KPI / narrative) ─────────────────────
function _renderTileContent(tile) {
  const body = document.getElementById(`dash-tile-body-${tile.id}`);
  if (!body) return;

  const status = tile.refresh_status || 'ok';
  if (status === 'refreshing' || status === 'pending') return; // skeleton stays

  if (status === 'error') {
    body.innerHTML = `<div class="dash-tile-error">
      <div class="dash-tile-error-icon">${ICONS.error}</div>
      <div class="dash-tile-error-msg">${_esc(tile._refresh_error || 'Last refresh failed')}</div>
      <button class="dash-tile-error-retry" data-action="refresh" data-tile-id="${tile.id}">
        Retry
      </button>
    </div>`;
    body.querySelector('[data-action="refresh"]')?.addEventListener('click', () => _refreshTile(tile.id));
    return;
  }

  const spec = typeof tile.spec === 'string' ? _safeParse(tile.spec) : (tile.spec || {});

  if (tile.component_id === 'chart' && spec && spec.type) {
    // G2Plot chart
    const container = document.createElement('div');
    container.className = 'dash-tile-chart-container';
    container.id = `dash-chart-${tile.id}`;
    body.innerHTML = '';
    body.appendChild(container);

    try {
      if (typeof G2Plot !== 'undefined' && G2Plot[spec.type]) {
        // Re-inject theme colours at render time so tiles pinned in dark mode
        // display readable text when the user is in light mode (and vice-versa).
        const patchedOptions = _patchChartColorsForTheme(spec.options || {});
        const plot = new G2Plot[spec.type](container.id, {
          ...patchedOptions,
          padding: 'auto',
          autoFit: true,
        });
        plot.render();
      }
    } catch (err) {
      body.innerHTML = `<div class="dash-tile-error">
        <div class="dash-tile-error-icon">${ICONS.error}</div>
        <div class="dash-tile-error-msg">${_esc(err.message)}</div>
      </div>`;
    }
  } else if (tile.component_id === 'knowledge_graph' && spec && (spec.nodes || []).length) {
    // Re-render the KG preview FRESH from its spec via the live renderer — NOT the
    // frozen pin-time HTML (tile.html). The frozen HTML carries a stale "Expanded in
    // side panel →" label and a dead button. `action` is stripped so it renders a
    // read-only preview with a WORKING "Open in Graph →" button and does not auto-open.
    body.innerHTML = '';
    const kgHost = document.createElement('div');
    kgHost.id = `dash-kg-${tile.id}`;
    kgHost.style.cssText = 'width:100%;height:100%;overflow:hidden;';
    body.appendChild(kgHost);
    import('/api/v1/components/knowledge_graph/renderer')
      .then(m => {
        if (typeof m.renderKnowledgeGraph === 'function') {
          // _dashboardFill: render the compact preview stretched to fill the tile
          // (graph area grows with the tile height instead of a fixed 200px block).
          m.renderKnowledgeGraph(kgHost.id, { ...spec, action: undefined, _dashboardFill: true });
        } else if (tile.html) {
          kgHost.innerHTML = tile.html;
        }
      })
      .catch(() => { if (tile.html) kgHost.innerHTML = tile.html; });
  } else if (tile.component_id === 'flow' && spec && (spec.flow || spec.definition)) {
    // Re-render the flow preview FRESH via the live flow renderer (mirrors the KG
    // path). It draws a read-only schematic that fills the tile and a working
    // "View in Canvas →" button; it reconciles to the flow's live state by flow_id,
    // so a stale pinned snapshot (often missing node positions) self-heals.
    body.innerHTML = '';
    const flowHost = document.createElement('div');
    flowHost.id = `dash-flow-${tile.id}`;
    flowHost.style.cssText = 'width:100%;height:100%;overflow:hidden;';
    body.appendChild(flowHost);
    import('/api/v1/components/flow/renderer')
      .then(m => {
        if (typeof m.renderFlowPreview === 'function') {
          m.renderFlowPreview(flowHost.id, { ...spec, _dashboardFill: true });
        } else if (tile.html) {
          flowHost.innerHTML = tile.html;
        }
      })
      .catch(() => { if (tile.html) flowHost.innerHTML = tile.html; });
  } else if (tile.component_id === 'explain_plan' && spec && Array.isArray(spec.steps)) {
    // A pinned plan re-renders through the plan renderer itself, filling the tile.
    body.innerHTML = '';
    const planHost = document.createElement('div');
    planHost.id = `dash-plan-${tile.id}`;
    planHost.style.cssText = 'width:100%;height:100%;overflow:auto;';
    body.appendChild(planHost);
    import('/api/v1/components/explain_plan/renderer')
      .then(m => { m.renderExplainPlan(planHost.id, { ...spec, _dashboardFill: true }); })
      .catch(() => { if (tile.html) planHost.innerHTML = tile.html; });
  } else if (tile.component_id === 'action' && tile.html) {
    // OBDA-ACT — a pinned act: the same row (consequence + Confirm) as on the answer. Loading the
    // action renderer installs its ONE delegated listener and reconciles the row against the
    // turn's confirmations, so a tile never offers a Confirm for an act already done.
    body.innerHTML = '';
    const actHost = document.createElement('div');
    actHost.className = 'dash-tile-content-container';
    actHost.innerHTML = tile.html;
    body.appendChild(actHost);
    import('/api/v1/components/action/renderer')
      .then(m => { if (m.reconcile) m.reconcile([...actHost.querySelectorAll('.act-row[data-act-pending]')]); })
      .catch(() => { /* the row still reads its last state */ });
    _mountActAgain(actHost, tile);
  } else if (tile.html) {
    // Pre-rendered HTML or markdown (tables, KPIs, LLM answers).
    // Table tiles get a dedicated container with sticky-header CSS and overflow scroll.
    const isTableTile = tile.component_id === 'table';
    const container = document.createElement('div');
    container.className = isTableTile ? 'dash-tile-table-container' : 'dash-tile-content-container';
    // Detect markdown (doesn't start with '<') vs already-rendered HTML
    const raw = tile.html;
    container.innerHTML = (raw && !raw.trimStart().startsWith('<') && typeof marked !== 'undefined')
      ? marked.parse(raw)
      : raw;
    body.innerHTML = '';
    body.appendChild(container);

    // Table tiles: show a bottom scroll-fade while there are rows below the fold,
    // so the scroll boundary reads as intentional rather than a clipped table.
    if (isTableTile) {
      const _updateFade = () => {
        const more = container.scrollHeight - container.scrollTop - container.clientHeight > 2;
        body.classList.toggle('dash-has-scroll-fade', more);
      };
      container.addEventListener('scroll', _updateFade, { passive: true });
      requestAnimationFrame(_updateFade);
    }

    // For table tiles: after paint, measure the natural card height and, if it
    // needs fewer grid rows than allocated, recompute tile_height + restack all
    // tile pos_row values so the tile below moves up (no blank gap).
    if (isTableTile) {
      requestAnimationFrame(() => {
        const tileEl = document.getElementById(`dash-tile-${tile.id}`);
        if (!tileEl) return;
        const cardH = tileEl.scrollHeight;
        const rowH  = GRID_ROW_PX + 10;                         // 80px row + 10px gap
        const neededRows = Math.max(1, Math.ceil(cardH / rowH));
        if (neededRows >= (tile.tile_height || 4)) return;      // already fits

        // Update this tile's height in memory + DB
        tile.tile_height = neededRows;
        _patchTile(tile.id, { tile_height: neededRows });

        // Re-stack all tiles: group by pos_row band, then assign rows sequentially.
        // Tiles sharing the same pos_row are side-by-side and form one band.
        const tiles = _dashboardData?.tiles || [];
        const bands = [];
        const seen  = new Map(); // pos_row → band index
        tiles.slice().sort((a, b) => a.pos_row - b.pos_row).forEach(t => {
          if (!seen.has(t.pos_row)) { seen.set(t.pos_row, bands.length); bands.push([]); }
          bands[seen.get(t.pos_row)].push(t);
        });

        let nextRow = 0;
        bands.forEach(band => {
          const bandH = Math.max(...band.map(t => t.tile_height || 4));
          band.forEach(t => {
            if (t.pos_row !== nextRow) {
              t.pos_row = nextRow;
              _patchTile(t.id, { pos_row: nextRow });
            }
          });
          nextRow += bandH;
        });

        _renderGrid();  // re-render with corrected positions
      });
    }
  } else if (tile.component_id === 'narrative' && spec.text) {
    body.innerHTML = `<div class="dash-tile-narrative-container">${_esc(spec.text)}</div>`;
  } else {
    // Placeholder for pending tiles with no spec yet
    body.innerHTML = `<div class="dash-tile-error">
      <div class="dash-tile-error-msg" style="font-size:0.72rem;color:${C.subtle};">
        Data not yet available
      </div>
    </div>`;
  }
}

// ── Refresh a tile ────────────────────────────────────────────────────────────
async function _refreshTile(tileId) {
  if (!_currentDashboardId) return;
  const tile = (_dashboardData?.tiles || []).find(t => t.id === tileId);
  if (!tile) return;

  // Update UI to spinning/skeleton state
  const refreshBtn = document.querySelector(`[data-action="refresh"][data-tile-id="${tileId}"]`);
  if (refreshBtn) refreshBtn.classList.add('spinning');

  // ── Flow tiles: re-render only (no LLM). The flow's live state IS the source of
  // truth; the preview reconciles by flow_id on render. Re-running the chat prompt
  // that originally created the flow would re-create/modify it — never do that.
  // A pinned EXPLAIN plan is a snapshot of one statement's plan — redraw it, never re-run
  // the conversation that produced it.
  // A pinned act is a record of one invocation — refresh re-reads how it ended; re-running
  // the conversation would prepare a different act.
  if (tile.component_id === 'flow' || tile.component_id === 'explain_plan' || tile.component_id === 'action') {
    tile.refresh_status = 'ok';
    tile.last_refreshed = new Date().toISOString();
    _renderTileContent(tile);
    _patchTile(tile.id, { last_refreshed: tile.last_refreshed });
    if (refreshBtn) refreshBtn.classList.remove('spinning');
    return;
  }
  const body = document.getElementById(`dash-tile-body-${tileId}`);
  if (body) body.innerHTML = _skeletonHTML();

  // ── Pulse the MCP indicator dot in the status bar during direct data fetch
  const _mcpDot = tile.mcp_tool_name ? document.getElementById('mcp-status-dot') : null;
  const _stopMcpPulse = () => {
    if (!_mcpDot) return;
    _mcpDot.classList.remove('pulsing');
    if (_mcpDot.classList.contains('busy')) _mcpDot.classList.replace('busy', 'connected');
  };
  if (_mcpDot) {
    _mcpDot.classList.replace('connected', 'busy') || _mcpDot.classList.replace('idle', 'busy');
    _mcpDot.classList.add('pulsing');
  }

  try {
    let task_id;

    // ── Fast path: direct MCP tool replay (no LLM) ───────────────────────────
    // The tile was produced by a single data tool call — replay it directly.
    if (tile.mcp_tool_name) {
      const res = await fetch(
        `/api/v1/dashboards/${_currentDashboardId}/tiles/${tileId}/refresh-mcp`,
        { method: 'POST', headers: { ..._authHeaders(), 'Content-Type': 'application/json' }, body: '{}' },
      );
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);

      // Update tile in memory and re-render immediately — no polling needed
      const idx = (_dashboardData?.tiles || []).findIndex(t => t.id === tileId);
      if (idx >= 0) {
        _dashboardData.tiles[idx].html = data.html;
        _dashboardData.tiles[idx].spec = data.spec;
        _dashboardData.tiles[idx].refresh_status = 'ok';
        _renderTileContent(_dashboardData.tiles[idx]);
      }
      _stopMcpPulse();
      if (refreshBtn) refreshBtn.classList.remove('spinning');
      return;
    }

    // ── Regular path: session pipeline (LLM-orchestrated) ────────────────────
    // Prefer submitting through the current active session so the refresh appears
    // in the Live Status window and costs are tracked on the user's session.
    const sessionId = window.state?.currentSessionId;
    const prompt = tile.source_prompt;

    if (sessionId && prompt) {
      // Use the regular session query pipeline — same path as typing in the chat.
      // Pass profile_id so execution uses the profile that originally created the tile,
      // not the session's current default (which may differ after a profile override).
      const qRes = await fetch(`/api/v1/sessions/${sessionId}/query`, {
        method: 'POST',
        headers: { ..._authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt, ...(tile.profile_id ? { profile_id: tile.profile_id } : {}) }),
      });
      const qData = await qRes.json();
      if (!qRes.ok) throw new Error(qData.error || `HTTP ${qRes.status}`);
      task_id = qData.task_id;
    } else {
      // Fallback: tile-specific refresh endpoint (isolated internal session)
      const res = await fetch(
        `/api/v1/dashboards/${_currentDashboardId}/tiles/${tileId}/refresh`,
        { method: 'POST', headers: _authHeaders(), body: '{}' },
      );
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      task_id = data.task_id;
    }

    if (!task_id) throw new Error('No task_id returned');

    // Poll for completion
    _pollTileRefresh(tileId, task_id);
  } catch (err) {
    console.error('[DashboardRenderer] Tile refresh error:', err);
    _stopMcpPulse();
    if (refreshBtn) refreshBtn.classList.remove('spinning');
    tile.refresh_status = 'error';
    // Surface the server's specific message (e.g. "no valid source query — re-pin")
    // instead of a generic failure, so the user knows how to recover.
    tile._refresh_error = (err && err.message) || '';
    _renderTileContent(tile);
  }
}

async function _pollTileRefresh(tileId, taskId, attempts = 0) {
  if (attempts > 60) { // 5 min max (5s intervals)
    _markTileError(tileId, 'Refresh timed out');
    return;
  }
  await new Promise(r => setTimeout(r, TILE_REFRESH_POLL_MS));

  try {
    const res = await fetch(`/api/v1/tasks/${taskId}`, { headers: _authHeaders() });
    const data = await res.json();
    const status = data.status;

    if (status === 'complete' || status === 'completed') {
      // Snapshot the tile's current chart type BEFORE overwriting anything.
      // This is the key to preserving visualization style across refreshes.
      const currentTile = (_dashboardData?.tiles || []).find(t => t.id === tileId);
      const currentSpec = typeof currentTile?.spec === 'string'
        ? _safeParse(currentTile.spec) : (currentTile?.spec || {});
      const lockedChartType = currentSpec?.type; // e.g. 'Pie', 'Column', 'Bar', 'Line'

      // Extract component_render spec from events (chart/component tools)
      const compEvent = (data.events || []).find(e =>
        e.event_type === 'component_render' ||
        (e.event_type === 'notification' && e.event_data?.type === 'component_render')
      );

      let tileSpec = {};
      let tileHtml = '';

      if (compEvent) {
        tileSpec = compEvent.event_data?.spec || compEvent.spec || {};
        tileHtml = compEvent.event_data?.html || compEvent.html || '';
        // Event gave us no chart spec but tile had one — rebuild from HTML
        if (!tileSpec?.type && lockedChartType && tileHtml) {
          const rebuilt = _rebuildSpecFromHtml(tileHtml, lockedChartType);
          if (rebuilt) tileSpec = rebuilt;
        }
      } else if (data.result?.final_answer) {
        // Session pipeline (IDEAT/OPTIM) returned HTML.
        const _rawHtml = data.result.final_answer;

        // PILLAR 2 — component-aware extraction. A chart tile's fresh visual is
        // embedded in the answer as <div data-component-id="chart"
        // data-spec='{type,options:{data,...}}'>.  That spec carries fresh data
        // AND the exact visualization, so adopt it verbatim — the refreshed tile
        // becomes the SAME chart with new values.  (The legacy logic only matched
        // the dashboard's own visualize component, so a tile pinned from a chat
        // chart never recovered its chart and blanked to an empty box.)
        const _chartMatch = _rawHtml.match(
          /data-component-id=["']chart["'][^>]*?\sdata-spec=(?:'([^']*)'|"([^"]*)")/i
        );
        if (_chartMatch) {
          const _cs = _safeParse(_decodeEntities(_chartMatch[1] || _chartMatch[2] || ''));
          if (_cs && _cs.type) { tileSpec = _cs; tileHtml = ''; }
        }

        // Fallback: rebuild from the embedded dashboard table data-spec (tables).
        if (!tileSpec?.type) {
          let _extractedContent = null;
          let _extractedChartType = null;
          const _specMatch = _rawHtml.match(
            /data-component-id=["']dashboard["'][^>]*data-spec='([^']+)'|data-spec='([^']+)'[^>]*data-component-id=["']dashboard["']/
          );
          if (_specMatch) {
            try {
              const _emb = JSON.parse(_specMatch[1] || _specMatch[2]);
              _extractedContent  = _emb.content    || null;
              _extractedChartType = _emb.chart_type ? _extractVizHint(_emb.chart_type) : null;
            } catch (_) { /* ignore malformed spec */ }
          }
          // Use clean HTML table when available; fall back to the full answer HTML.
          tileHtml = _extractedContent || _rawHtml;
          // Honour tile's locked chart type first, then fall back to embedded hint.
          const _effectiveChartType = lockedChartType || _extractedChartType;
          if (_effectiveChartType) {
            const rebuilt = _rebuildSpecFromHtml(tileHtml, _effectiveChartType);
            if (rebuilt) tileSpec = rebuilt;
          }
        }
      } else {
        // No usable content in result
        _markTileError(tileId, 'Query returned no content');
        return;
      }

      // PILLAR 1 — non-destructive refresh. A refresh must NEVER destroy a
      // working visualization. If we could not extract a fresh chart but the
      // tile already had one, keep the prior spec (and its embedded data) so the
      // tile shows its last-good chart rather than blanking to an empty box.
      if (!tileSpec?.type && lockedChartType && currentSpec?.type) {
        tileSpec = currentSpec;
        if (!tileHtml) tileHtml = currentTile?.html || '';
        console.warn('[Dashboard] refresh produced no chart — preserved prior visualization for tile', tileId);
      }

      const now = new Date().toISOString();

      // Persist to server
      await _patchTile(tileId, { spec: tileSpec, html: tileHtml, refresh_status: 'ok', last_refreshed: now });

      // Update local data
      const tile = (_dashboardData?.tiles || []).find(t => t.id === tileId);
      if (tile) {
        tile.spec = tileSpec;
        tile.html = tileHtml;
        tile.refresh_status = 'ok';
        tile.last_refreshed = now;
      }

      // Update timestamp
      const tsEl = document.querySelector(`#dash-tile-${tileId} [data-tile-refreshed]`);
      if (tsEl) {
        const now = new Date().toISOString();
        tsEl.dataset.tileRefreshed = now;
        tsEl.textContent = _relTime(now);
      }

      // Stop spinning
      const refreshBtn = document.querySelector(`[data-action="refresh"][data-tile-id="${tileId}"]`);
      if (refreshBtn) refreshBtn.classList.remove('spinning');

      // Re-render tile content
      const tile2 = (_dashboardData?.tiles || []).find(t => t.id === tileId);
      if (tile2) _renderTileContent(tile2);

    } else if (status === 'failed' || status === 'error') {
      _markTileError(tileId, 'Query execution failed');
    } else {
      // Still running
      _pollTileRefresh(tileId, taskId, attempts + 1);
    }
  } catch (err) {
    _markTileError(tileId, err.message);
  }
}

function _markTileError(tileId, msg) {
  const refreshBtn = document.querySelector(`[data-action="refresh"][data-tile-id="${tileId}"]`);
  if (refreshBtn) refreshBtn.classList.remove('spinning');
  const tile = (_dashboardData?.tiles || []).find(t => t.id === tileId);
  if (tile) { tile.refresh_status = 'error'; }
  const body = document.getElementById(`dash-tile-body-${tileId}`);
  if (body) body.innerHTML = `<div class="dash-tile-error">
    <div class="dash-tile-error-icon">${ICONS.error}</div>
    <div class="dash-tile-error-msg">${_esc(msg)}</div>
    <button class="dash-tile-error-retry" data-action="refresh" data-tile-id="${tileId}">Retry</button>
  </div>`;
  body?.querySelector('[data-action="refresh"]')?.addEventListener('click', () => _refreshTile(tileId));
}

// ── PATCH tile via REST ───────────────────────────────────────────────────────
async function _patchTile(tileId, updates) {
  if (!_currentDashboardId) return;
  try {
    await fetch(
      `/api/v1/dashboards/${_currentDashboardId}/tiles/${tileId}`,
      { method: 'PUT', headers: _authHeaders(), body: JSON.stringify(updates) },
    );
  } catch (err) {
    console.warn('[DashboardRenderer] _patchTile failed:', err);
  }
}

// ── Tile context menu ─────────────────────────────────────────────────────────
function _showTileMenu(tileId, anchorEl) {
  document.querySelectorAll('.dash-tile-menu').forEach(m => m.remove());

  const tile = (_dashboardData?.tiles || []).find(t => t.id === tileId);
  if (!tile) return;

  const menu = document.createElement('div');
  menu.className = 'dash-tile-menu';
  menu.innerHTML = `
    <div class="dash-menu-item" data-action="refresh-tile">
      ${ICONS.refresh} Refresh tile
    </div>
    <div class="dash-menu-item" data-action="rename-tile">
      ${ICONS.edit} Rename
    </div>
    ${(tile.source_prompt || tile.mcp_tool_name) ? `<div class="dash-menu-item" data-action="edit-query">
      ${ICONS.run} Edit query
    </div>` : ''}
    <div class="dash-menu-item" data-action="duplicate-tile">
      ${ICONS.copy} Duplicate
    </div>
    <div class="dash-menu-divider"></div>
    <div class="dash-menu-item danger" data-action="delete-tile">
      ${ICONS.trash} Remove tile
    </div>
  `;

  const tileEl = document.getElementById(`dash-tile-${tileId}`);
  if (tileEl) tileEl.style.position = 'relative';
  const headerEl = tileEl?.querySelector('.dash-tile-header');
  if (headerEl) headerEl.appendChild(menu);
  else document.body.appendChild(menu);

  menu.addEventListener('click', async (e) => {
    const action = e.target.closest('[data-action]')?.dataset.action;
    menu.remove();
    if (!action) return;

    if (action === 'refresh-tile') { _refreshTile(tileId); }
    else if (action === 'rename-tile') { _promptRenameTile(tileId, tile); }
    else if (action === 'edit-query') { _promptEditQuery(tileId, tile); }
    else if (action === 'duplicate-tile') { _duplicateTile(tile); }
    else if (action === 'delete-tile') { _deleteTile(tileId); }
  });

  const dismiss = (e) => {
    if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener('click', dismiss); }
  };
  setTimeout(() => document.addEventListener('click', dismiss), 50);
}

function _promptRenameTile(tileId, tile) {
  const titleEl = document.querySelector(`#dash-tile-${tileId} .dash-tile-title`);
  if (titleEl && _editMode) { titleEl.focus(); return; }
  _showInputDialog('Rename tile', tile.title, (val) => {
    if (val !== tile.title) {
      tile.title = val;
      if (titleEl) titleEl.textContent = tile.title;
      _patchTile(tileId, { title: tile.title });
    }
  });
}

function _promptEditQuery(tileId, tile) {
  // MCP-only tile: edit the actual tool argument (e.g. SQL), not the NL prompt
  if (tile.mcp_tool_name) {
    const args = typeof tile.mcp_args_json === 'string'
      ? JSON.parse(tile.mcp_args_json || '{}')
      : (tile.mcp_args_json || {});
    const primaryKey = Object.keys(args).find(k => typeof args[k] === 'string') || Object.keys(args)[0];
    const currentVal = primaryKey ? String(args[primaryKey]) : JSON.stringify(args);
    const isSQL = primaryKey && /sql|query|statement/i.test(primaryKey);
    _showInputDialog(
      `Edit ${tile.mcp_tool_name} — ${primaryKey || 'argument'}`,
      currentVal,
      (val) => {
        if (val !== currentVal) {
          const newArgs = { ...args, [primaryKey]: val };
          tile.mcp_args_json = newArgs;
          _patchTile(tileId, { mcp_args_json: JSON.stringify(newArgs) });
          _refreshTile(tileId);
        }
      },
      { multiline: isSQL }
    );
    return;
  }
  // Regular NL prompt flow
  _showInputDialog('Edit query', tile.source_prompt, (val) => {
    if (val !== tile.source_prompt) {
      tile.source_prompt = val;
      _patchTile(tileId, { source_prompt: tile.source_prompt });
      _refreshTile(tileId);
    }
  });
}

async function _duplicateTile(tile) {
  if (!_currentDashboardId) return;
  try {
    const spec = typeof tile.spec === 'string' ? _safeParse(tile.spec) : tile.spec;
    const res = await fetch(`/api/v1/dashboards/${_currentDashboardId}/tiles`, {
      method: 'POST',
      headers: _authHeaders(),
      body: JSON.stringify({
        ...tile,
        title: tile.title + ' (copy)',
        id: undefined,
        spec,
      }),
    });
    if (res.ok) {
      _loadAndRenderDashboard(_currentDashboardId);
    }
  } catch (err) {
    console.error('[DashboardRenderer] Duplicate tile error:', err);
  }
}

/** Revert a viz card in the chat to unpinned state when its tile is deleted from the dashboard. */
function _revertPinnedCard(tileId) {
  const container = document.querySelector(`[data-pinned-tile-id="${CSS.escape(tileId)}"]`);
  if (!container) return;
  const spec = container._vizSpec;
  delete container.dataset.pinnedTileId;
  if (!spec || !container.id) return;
  // Re-render the card as an unpinned visualization so Add/New buttons return
  renderDashboard(container.id, { ...spec, view: 'visualization', dashboard_id: null });
}

async function _deleteTile(tileId) {
  if (!_currentDashboardId) return;
  const doDelete = async () => {
    try {
      await fetch(
        `/api/v1/dashboards/${_currentDashboardId}/tiles/${tileId}`,
        { method: 'DELETE', headers: _authHeaders() },
      );
      _revertPinnedCard(tileId); // revert any chat card pinned to this tile
      if (_dashboardData) {
        _dashboardData.tiles = _dashboardData.tiles.filter(t => t.id !== tileId);
        _repackTiles(); // close gaps left by the removed tile
      }
      _renderGrid();
      // Update tile count in all open viz-card pickers for this dashboard
      const newCount = _dashboardData ? _dashboardData.tiles.length : 0;
      document.querySelectorAll(`select[data-action="select-dash"] option[value="${CSS.escape(_currentDashboardId)}"]`)
        .forEach(opt => { opt.text = opt.text.replace(/\(\d+ tiles?\)/, `(${newCount} tile${newCount === 1 ? '' : 's'})`); });
    } catch (err) {
      console.error('[DashboardRenderer] Delete tile error:', err);
    }
  };
  if (window.showConfirmation) {
    window.showConfirmation('Remove Tile', 'Remove this tile from the dashboard?', doDelete);
  } else {
    if (confirm('Remove this tile from the dashboard?')) doDelete();
  }
}

// ── Repack tiles to eliminate gaps after deletion ─────────────────────────────
// Re-assigns pos_col / pos_row using a greedy 12-column bin-pack and persists
// changed positions to the server (fire-and-forget).
function _repackTiles() {
  const tiles = _dashboardData?.tiles;
  if (!tiles || tiles.length === 0) return;

  // Sort by visual reading order
  tiles.sort((a, b) => (a.pos_row - b.pos_row) || (a.pos_col - b.pos_col));

  const occupied = new Set();
  const isFree = (col, row, w, h) => {
    for (let r = row; r < row + h; r++) {
      for (let c = col; c < col + w; c++) {
        if (c >= 12 || occupied.has(`${c},${r}`)) return false;
      }
    }
    return true;
  };
  const occupy = (col, row, w, h) => {
    for (let r = row; r < row + h; r++) {
      for (let c = col; c < col + w; c++) occupied.add(`${c},${r}`);
    }
  };

  let maxRow = 0;
  const updates = [];

  tiles.forEach(tile => {
    const w = tile.tile_width  || 6;
    const h = tile.tile_height || 4;
    let placed = false;
    // maxRow is the bottom edge (one past the lowest occupied row), so search
    // every row down to it — a tile must be able to drop into a gap of ANY height
    // and, failing that, land at the very bottom (row === maxRow).
    for (let row = 0; row <= maxRow && !placed; row++) {
      for (let col = 0; col <= 12 - w && !placed; col++) {
        if (isFree(col, row, w, h)) {
          if (tile.pos_col !== col || tile.pos_row !== row) {
            updates.push({ id: tile.id, pos_col: col, pos_row: row });
            tile.pos_col = col;
            tile.pos_row = row;
          }
          occupy(col, row, w, h);
          maxRow = Math.max(maxRow, row + h);  // bottom edge accounts for height
          placed = true;
        }
      }
    }
  });

  // Persist new positions (non-blocking; failures are non-critical)
  updates.forEach(({ id, pos_col, pos_row }) => {
    fetch(`/api/v1/dashboards/${_currentDashboardId}/tiles/${id}`, {
      method: 'PUT',
      headers: _authHeaders(),
      body: JSON.stringify({ pos_col, pos_row }),
    }).catch(() => {});
  });
}

// ── Refresh all open viz-card dashboard selectors ─────────────────────────────
// Called whenever a dashboard is created so every card in the chat log picks it up.
// Preserves ✓ marks for dashboards already pinned from each card.
async function _refreshAllVizCardSelectors() {
  const selects = document.querySelectorAll('.dash-viz-dest-select');
  if (!selects.length) return;
  try {
    const res = await fetch('/api/v1/dashboards', { headers: _authHeaders() });
    if (!res.ok) return;
    const data = await res.json();
    const dashes = data.dashboards || [];
    selects.forEach(sel => {
      const row = sel.closest('.dash-viz-dest-row');
      if (!row) return;
      const addBtn     = row.querySelector('[data-action="add-to-dash"]');
      const openSelBtn = row.querySelector('[data-action="open-selected-dash"]');
      // Locate the card container to read its pinned-dashboard set
      const cnt = row.closest('[data-component-id]') || row.closest('.dash-viz-card')?.parentElement;
      const pinnedSet = cnt?._pinnedDashIds || new Set();
      const currentVal = sel.value;
      sel.innerHTML = dashes.length
        ? dashes.map(d => {
            const label = `${pinnedSet.has(d.id) ? '\u2713 ' : ''}${d.name} (${d.tile_count} tiles)`;
            return `<option value="${_esc(d.id)}">${_esc(label)}</option>`;
          }).join('')
        : `<option value="">No dashboards yet</option>`;
      sel.disabled = false;
      if (dashes.length) {
        // Restore previously selected value, or newest dashboard
        sel.value = dashes.find(d => d.id === currentVal) ? currentVal : dashes[dashes.length - 1].id;
        if (addBtn) addBtn.disabled = false;
        if (openSelBtn) openSelBtn.disabled = false;
      }
    });
  } catch (_) {}
}

// ── Reset all tiles to their default sizes ───────────────────────────────────
function _resetTileSizes() {
  const tiles = _dashboardData?.tiles;
  if (!tiles?.length) return;

  // Default sizes by component_id (mirrors creation defaults in _pinVizToExistingDashboard)
  const DEFAULT_W = { table: 12 }; // all others → 6
  const DEFAULT_H = { kpi: 2 };   // all others → 4

  tiles.forEach(tile => {
    const w = DEFAULT_W[tile.component_id] ?? 6;
    const h = DEFAULT_H[tile.component_id] ?? 4;
    if (tile.tile_width !== w || tile.tile_height !== h) {
      tile.tile_width  = w;
      tile.tile_height = h;
      _patchTile(tile.id, { tile_width: w, tile_height: h });
    }
  });

  _repackTiles(); // restack to close any gaps created by size changes
  _renderGrid();
}

// ── Edit mode drag-and-drop ───────────────────────────────────────────────────
function _wireGridDragDrop(grid) {
  if (!_editMode) return;
  // Simple pointer-based tile reorder within the grid
  // (positions updated on drop via bulk layout PATCH)
}

function _startDrag(e, tile, tileEl) {
  if (!_editMode) return;
  e.preventDefault();
  _isDragging = true;
  _dragTileId = tile.id;

  const grid = document.getElementById('dash-grid-root');
  if (!grid) return;

  // ── Grid metrics (accounts for padding + gap) ─────────────────────────────
  const PAD  = 14;  // grid padding (matches CSS padding: 14px)
  const GAP  = 10;  // grid gap    (matches CSS gap: 10px)
  const gridRect = grid.getBoundingClientRect();
  const innerW   = gridRect.width - 2 * PAD;
  const trackW   = (innerW - (GRID_COLS - 1) * GAP) / GRID_COLS;
  const colW     = trackW + GAP; // distance from column N start to column N+1 start
  const rowH     = GRID_ROW_PX + GAP;

  const tileW = tile.tile_width  || 6;
  const tileH = tile.tile_height || 4;

  // ── Snapshot original positions for cancel support ────────────────────────
  const origPositions = {};
  (_dashboardData?.tiles || []).forEach(t => {
    origPositions[t.id] = { pos_col: t.pos_col, pos_row: t.pos_row };
  });

  // ── Ghost (semi-transparent card that follows the cursor) ─────────────────
  const rect = tileEl.getBoundingClientRect();
  const ghost = document.createElement('div');
  ghost.className = 'dash-drag-ghost';
  ghost.style.cssText = `width:${rect.width}px;height:${rect.height}px;left:${rect.left}px;top:${rect.top}px;`;
  document.body.appendChild(ghost);

  // Cursor offset inside the tile so the ghost doesn't jump on pickup
  const offsetX = e.clientX - rect.left;
  const offsetY = e.clientY - rect.top;

  // ── Drop-zone indicator (dashed outline showing landing cell) ─────────────
  const dropZone = document.createElement('div');
  dropZone.className = 'dash-drop-indicator';
  dropZone.style.cssText = `position:absolute;pointer-events:none;box-sizing:border-box;transition:left 0.12s,top 0.12s;`;
  grid.style.position = 'relative';
  grid.appendChild(dropZone);

  const positionDropZone = (col, row) => {
    dropZone.style.left   = `${PAD + col * colW}px`;
    dropZone.style.top    = `${PAD + row * rowH}px`;
    dropZone.style.width  = `${tileW * colW - GAP}px`;
    dropZone.style.height = `${tileH * rowH - GAP}px`;
  };

  // ── Cancel running CSS animations on all tiles ────────────────────────────
  // The .dash-tile entry animation uses fill-mode:both which keeps its final
  // transform value active AFTER it completes, overriding any inline transform
  // we set. Cancelling the animation restores normal cascade behaviour.
  const allTileEls = grid.querySelectorAll('.dash-tile');
  allTileEls.forEach(el => {
    el.style.animation = 'none';
    if (el !== tileEl) el.style.transition = 'transform 0.18s cubic-bezier(0.25,0,0,1)';
  });
  tileEl.style.opacity = '0.25';
  tileEl.style.pointerEvents = 'none'; // let pointer pass through to grid

  // ── Compute displacement layout ───────────────────────────────────────────
  const computeLayout = (targetCol, targetRow) => {
    const occupied = new Set();
    const isFree = (col, row, w, h) => {
      for (let r = row; r < row + h; r++)
        for (let c = col; c < col + w; c++)
          if (c >= GRID_COLS || occupied.has(`${c},${r}`)) return false;
      return true;
    };
    const occupy = (col, row, w, h) => {
      for (let r = row; r < row + h; r++)
        for (let c = col; c < col + w; c++) occupied.add(`${c},${r}`);
    };

    // Reserve the dragged tile's target cell first
    occupy(targetCol, targetRow, tileW, tileH);

    // Pack remaining tiles in original reading order
    const others = (_dashboardData?.tiles || [])
      .filter(t => t.id !== tile.id)
      .slice()
      .sort((a, b) =>
        (origPositions[a.id].pos_row - origPositions[b.id].pos_row) ||
        (origPositions[a.id].pos_col - origPositions[b.id].pos_col)
      );

    const result = {};
    let maxRow = targetRow + tileH;
    others.forEach(t => {
      const w = t.tile_width  || 6;
      const h = t.tile_height || 4;
      let placed = false;
      for (let row = 0; row <= maxRow + 1 && !placed; row++) {
        for (let col = 0; col <= GRID_COLS - w && !placed; col++) {
          if (isFree(col, row, w, h)) {
            result[t.id] = { pos_col: col, pos_row: row };
            occupy(col, row, w, h);
            maxRow = Math.max(maxRow, row + h - 1);
            placed = true;
          }
        }
      }
    });
    return result;
  };

  // ── Apply live displacement for a given target cell ───────────────────────
  let lastTargetCol = tile.pos_col;
  let lastTargetRow = tile.pos_row;
  let lastLayout    = computeLayout(lastTargetCol, lastTargetRow);

  const applyLayout = (targetCol, targetRow) => {
    if (targetCol === lastTargetCol && targetRow === lastTargetRow) return;
    lastTargetCol = targetCol;
    lastTargetRow = targetRow;
    lastLayout    = computeLayout(targetCol, targetRow);

    positionDropZone(targetCol, targetRow);

    // Shift other tiles via CSS transform (no DOM rebuild — stays smooth)
    (_dashboardData?.tiles || []).forEach(t => {
      if (t.id === tile.id) return;
      const el = document.getElementById(`dash-tile-${t.id}`);
      if (!el) return;
      const newPos = lastLayout[t.id];
      if (!newPos) return;
      const orig = origPositions[t.id];
      const dx = (newPos.pos_col - orig.pos_col) * colW;
      const dy = (newPos.pos_row - orig.pos_row) * rowH;
      el.style.transform = (dx === 0 && dy === 0) ? '' : `translate(${dx}px,${dy}px)`;
    });
  };

  // Show initial drop-zone at the tile's current cell
  positionDropZone(lastTargetCol, lastTargetRow);

  // ── Cleanup helper ────────────────────────────────────────────────────────
  const cleanup = () => {
    ghost.remove();
    dropZone.remove();
    _isDragging = false;
    _dragTileId = null;
    tileEl.style.pointerEvents = '';
    document.removeEventListener('pointermove', move);
    document.removeEventListener('pointerup',   up);
    document.removeEventListener('keydown',     onKey);
    // Note: animation stays 'none' inline — _renderGrid() creates fresh
    // elements after commit; after cancel the entry animation has already played
  };

  // ── Pointermove: move ghost + compute live displacement ───────────────────
  const move = (ev) => {
    ghost.style.left = `${ev.clientX - offsetX}px`;
    ghost.style.top  = `${ev.clientY - offsetY}px`;

    // Map the ghost's top-left corner to a grid cell (subtract grid padding)
    const dropX = ev.clientX - offsetX - gridRect.left - PAD;
    const dropY = ev.clientY - offsetY - gridRect.top  - PAD;
    const newCol = Math.max(0, Math.min(GRID_COLS - tileW, Math.floor(dropX / colW)));
    const newRow = Math.max(0, Math.floor(dropY / rowH));
    applyLayout(newCol, newRow);
  };

  // ── Pointerup: commit the drop ────────────────────────────────────────────
  const up = () => {
    const committedLayout = lastLayout;
    const committedCol    = lastTargetCol;
    const committedRow    = lastTargetRow;
    cleanup();

    // Strip transforms before full re-render
    allTileEls.forEach(el => { el.style.transition = ''; el.style.transform = ''; });
    tileEl.style.opacity = '';

    // Apply committed positions to tile data and persist
    tile.pos_col = committedCol;
    tile.pos_row = committedRow;
    _patchTile(tile.id, { pos_col: committedCol, pos_row: committedRow });

    (_dashboardData?.tiles || []).forEach(t => {
      if (t.id === tile.id) return;
      const newPos = committedLayout[t.id];
      if (!newPos) return;
      const orig = origPositions[t.id];
      if (newPos.pos_col !== orig.pos_col || newPos.pos_row !== orig.pos_row) {
        t.pos_col = newPos.pos_col;
        t.pos_row = newPos.pos_row;
        _patchTile(t.id, { pos_col: newPos.pos_col, pos_row: newPos.pos_row });
      }
    });

    _renderGrid();
  };

  // ── Escape: cancel and animate everything back ────────────────────────────
  const onKey = (ev) => {
    if (ev.key !== 'Escape') return;
    cleanup();
    // Animate displaced tiles back to their grid positions
    allTileEls.forEach(el => {
      el.style.transition = 'transform 0.2s cubic-bezier(0.25,0,0,1)';
      el.style.transform  = '';
    });
    tileEl.style.opacity = '';
    setTimeout(() => allTileEls.forEach(el => { el.style.transition = ''; }), 220);
  };

  document.addEventListener('pointermove', move);
  document.addEventListener('pointerup',   up);
  document.addEventListener('keydown',     onKey);
}

// ── Resize grip ───────────────────────────────────────────────────────────────
function _startResize(e, tile, tileEl) {
  if (!_editMode) return;
  e.preventDefault();
  e.stopPropagation();
  _resizeTileId = tile.id;
  _resizeStartX = e.clientX;
  _resizeStartY = e.clientY;

  const grid = document.getElementById('dash-grid-root');
  const gridRect = grid?.getBoundingClientRect() || { width: 800 };
  const colW = (gridRect.width - 14 * 11) / 12; // approx column width
  const rowH = 80 + 10; // row height + gap

  const move = (ev) => {
    const dx = ev.clientX - _resizeStartX;
    const dy = ev.clientY - _resizeStartY;
    const newW = Math.max(2, Math.min(12 - tile.pos_col, tile.tile_width + Math.round(dx / colW)));
    const newH = Math.max(2, tile.tile_height + Math.round(dy / rowH));
    tileEl.style.gridColumn = `${tile.pos_col + 1} / span ${newW}`;
    tileEl.style.gridRow = `${tile.pos_row + 1} / span ${newH}`;
    // Keep the table-tile height cap in sync with the new span (see _buildTileElement).
    if (tile.component_id === 'table') tileEl.style.maxHeight = `${_spanHeightPx(newH)}px`;
  };
  const up = (ev) => {
    document.removeEventListener('pointermove', move);
    document.removeEventListener('pointerup', up);
    const dx = ev.clientX - _resizeStartX;
    const dy = ev.clientY - _resizeStartY;
    const newW = Math.max(2, Math.min(12 - tile.pos_col, tile.tile_width + Math.round(dx / colW)));
    const newH = Math.max(2, tile.tile_height + Math.round(dy / rowH));
    tile.tile_width = newW;
    tile.tile_height = newH;
    _patchTile(tile.id, { tile_width: newW, tile_height: newH });
    // Re-render chart in new size
    setTimeout(() => _renderTileContent(tile), 100);
    _resizeTileId = null;
  };
  document.addEventListener('pointermove', move);
  document.addEventListener('pointerup', up);
}

// ── Wire panel header buttons ─────────────────────────────────────────────────
function _wirePanelButtons() {
  const closeBtn = document.getElementById('dashboard-split-close');
  if (closeBtn) closeBtn.onclick = closeDashboardPanel;

  const editBtn = document.getElementById('dashboard-split-edit');
  if (editBtn) {
    editBtn.onclick = () => {
      _editMode = !_editMode;
      editBtn.classList.toggle('edit-active', _editMode);
      editBtn.title = _editMode ? 'Exit Edit mode' : 'Edit layout';
      const toolbar = document.getElementById('dash-edit-toolbar');
      if (toolbar) toolbar.classList.toggle('visible', _editMode);
      _renderGrid();
    };
  }

  const addBtn = document.getElementById('dashboard-split-add');
  if (addBtn) {
    addBtn.onclick = () => {
      document.getElementById('user-input')?.focus();
    };
  }

  const refreshAllBtn = document.getElementById('dashboard-split-refresh');
  if (refreshAllBtn) {
    refreshAllBtn.onclick = () => {
      if (_dashboardData?.tiles) {
        _dashboardData.tiles.forEach(t => _refreshTile(t.id));
      }
    };
  }

  const expandBtn = document.getElementById('dashboard-split-expand');
  if (expandBtn) expandBtn.onclick = _toggleDashFullscreen;

  // PROP-010 — Save snapshot to Files: shown only while Files is available to this user
  const snapBtn = document.getElementById('dashboard-split-snapshot');
  if (snapBtn) {
    snapBtn.onclick = _saveSnapshot;
    const avail = window.FilesKeep && window.FilesKeep.filesAvailable ? window.FilesKeep.filesAvailable() : Promise.resolve(false);
    avail.then(ok => { snapBtn.hidden = !ok; });
  }

  const resetBtn = document.getElementById('dashboard-reset-sizes');
  if (resetBtn) resetBtn.onclick = _resetTileSizes;

  const switcherBtn = document.getElementById('dashboard-switcher-btn');
  if (switcherBtn && typeof window.mountSplitPanelSwitcher === 'function') {
    window.mountSplitPanelSwitcher({
      btnEl: switcherBtn,
      listUrl: '/api/v1/dashboards',
      itemsKey: 'dashboards',
      idField: 'id',
      labelField: 'name',
      metaField: 'tile_count',
      metaSuffix: ' tiles',
      getCurrentId: () => _currentDashboardId,
      onSelect: (id) => { _currentDashboardId = id; _loadAndRenderDashboard(id); },
      newLabel: 'New dashboard',
      newPrompt: 'Name of the new dashboard',
      onNew: async (name) => (await _doCreateDashboard(name)) ? true : 'Could not create the dashboard',
      onRename: async (id, name) => {
        const res = await fetch(`/api/v1/dashboards/${id}`, {
          method: 'PUT', headers: _authHeaders(), body: JSON.stringify({ name }),
        });
        if (!res.ok) return 'Could not rename the dashboard';
        if (_dashboardData && _dashboardData.id === id) _dashboardData.name = name;
        return true;
      },
      emptyLabel: 'No dashboards yet',
    });
  }
}

// ── Save snapshot to Files (PROP-010 §4.10) ─────────────────────────────────
// The SERVER writes the PDF from the dashboard's own tiles — titles, order, refresh times,
// agents, tables and text. Only what the browser draws (a chart, a graph) is sent as a
// picture, keyed by tile id; a tile whose picture cannot be taken is written from its data.
// A chart tile is drawn AGAIN for the snapshot, off-screen and in light colours: a PDF is
// read on paper, whatever theme the person taking it works in. Its data is the tile's own spec.
async function _chartPicture(tile, body) {
  const spec = typeof tile.spec === 'string' ? _safeParse(tile.spec) : (tile.spec || {});
  if (!spec || !spec.type || typeof G2Plot === 'undefined' || !G2Plot[spec.type]) return null;
  const box = body.getBoundingClientRect();
  const w = Math.max(480, Math.round(box.width) || 0), h = Math.max(280, Math.round(box.height) || 0);
  const host = document.createElement('div');
  host.style.cssText = `position:fixed;left:-10000px;top:0;width:${w}px;height:${h}px;background:#ffffff;`;
  document.body.appendChild(host);
  let plot = null;
  try {
    plot = new G2Plot[spec.type](host, { ..._patchChartColorsForTheme(spec.options || {}, true), padding: 'auto',
      autoFit: false, width: w, height: h, animation: false, renderer: 'canvas' });
    plot.render();
    await new Promise(r => setTimeout(r, 60));
    const canvas = host.querySelector('canvas');
    if (!canvas) return null;
    const out = document.createElement('canvas');
    out.width = canvas.width; out.height = canvas.height;
    const ctx = out.getContext('2d');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, out.width, out.height);
    ctx.drawImage(canvas, 0, 0);
    return out.toDataURL('image/png');
  } catch (_) { return null; } finally {
    try { plot && plot.destroy(); } catch (_) { /* gone */ }
    host.remove();
  }
}

async function _tilePicture(body) {
  // the LARGEST drawing in the tile is its picture (a chart's canvas, a graph's svg) —
  // icons and badges are drawings too, so the first one found is not the tile
  const area = el => { const r = el.getBoundingClientRect(); return r.width >= 120 && r.height >= 80 ? r.width * r.height : 0; };
  const drawn = [...body.querySelectorAll('canvas, svg')].filter(el => !el.closest('svg') || el.tagName.toLowerCase() === 'canvas')
    .map(el => ({ el, a: area(el) })).filter(x => x.a).sort((x, y) => y.a - x.a);
  if (!drawn.length) return null;
  const el = drawn[0].el;
  const bg = _isLightTheme() ? '#ffffff' : '#0f172a';
  if (el.tagName.toLowerCase() === 'canvas') {
    try {
      const out = document.createElement('canvas');
      out.width = el.width; out.height = el.height;
      const ctx = out.getContext('2d');
      ctx.fillStyle = bg;
      ctx.fillRect(0, 0, out.width, out.height);
      ctx.drawImage(el, 0, 0);
      return out.toDataURL('image/png');
    } catch (_) { return null; }   // a tainted canvas cannot be read — the server writes the data
  }
  const box = el.getBoundingClientRect();
  const xml = new XMLSerializer().serializeToString(el);
  const url = URL.createObjectURL(new Blob([xml], { type: 'image/svg+xml;charset=utf-8' }));
  try {
    const img = await new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = url; });
    const out = document.createElement('canvas');
    out.width = Math.round(box.width * 2); out.height = Math.round(box.height * 2);
    const ctx = out.getContext('2d');
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, out.width, out.height);
    ctx.drawImage(img, 0, 0, out.width, out.height);
    return out.toDataURL('image/png');
  } catch (_) { return null; } finally { URL.revokeObjectURL(url); }
}

async function _saveSnapshot() {
  const btn = document.getElementById('dashboard-split-snapshot');
  if (!_dashboardData || !_currentDashboardId) return;
  if (btn) btn.disabled = true;
  try {
    const images = {};
    for (const t of (_dashboardData.tiles || [])) {
      const body = document.getElementById(`dash-tile-body-${t.id}`);
      if (!body) continue;
      const pic = (t.component_id === 'chart' ? await _chartPicture(t, body) : null) || await _tilePicture(body);
      if (pic) images[t.id] = pic;
    }
    const res = await fetch('/api/v1/files/dashboard-snapshot', {
      method: 'POST', headers: _authHeaders(),
      body: JSON.stringify({ dashboard_id: _currentDashboardId, images }),
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok || !d.file) {
      window.showAppBanner && window.showAppBanner(d.message || 'The snapshot could not be saved', 'error');
      return;
    }
    if (window.FilesKeep && window.FilesKeep.announceSaved) window.FilesKeep.announceSaved(d.file);
    else if (window.showAppBanner) window.showAppBanner(`Snapshot saved to Files: ${d.file.filename}`, 'success');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ── Dashboard fullscreen toggle ─────────────────────────────────────────────
function _toggleDashFullscreen() {
  const mainArea = document.getElementById('main-content-area');
  if (!mainArea) return;
  _isDashFullscreen = !_isDashFullscreen;
  if (_isDashFullscreen) {
    const topNav = document.querySelector('body > nav');
    const topOffset = topNav ? topNav.offsetHeight : 0;
    document.documentElement.style.setProperty('--dash-fullscreen-top', topOffset + 'px');
    mainArea.classList.add('dash-fullscreen');
  } else {
    mainArea.classList.remove('dash-fullscreen');
    document.documentElement.style.removeProperty('--dash-fullscreen-top');
  }
  const expandBtn = document.getElementById('dashboard-split-expand');
  if (expandBtn) expandBtn.title = _isDashFullscreen ? 'Exit fullscreen' : 'Fullscreen dashboard';
}

// Dashboard switcher dropdown is provided by the shared mountSplitPanelSwitcher
// helper (static/js/handlers/splitPanelChrome.js), wired in _wireHeaderButtons.

async function _doCreateDashboard(name) {
  if (!name?.trim()) return;
  try {
    const res = await fetch('/api/v1/dashboards', {
      method: 'POST',
      headers: _authHeaders(),
      body: JSON.stringify({ name: name.trim() }),
    });
    const data = await res.json();
    if (res.ok) {
      _currentDashboardId = data.dashboard_id;
      _dashboardData = { id: data.dashboard_id, name: name.trim(), tiles: [] };
      const nameEl = document.getElementById('dashboard-split-title');
      if (nameEl) nameEl.textContent = name.trim();
      _renderGrid();
      return true;
    }
  } catch (err) {
    console.error('[DashboardRenderer] Create dashboard error:', err);
  }
  return false;
}

// ── Public: add tile from a chat component (modal picker, legacy path) ────────
/**
 * Called by dashboardHandler.js when the user clicks "Pin to Dashboard"
 * on any chat chart, table, or KPI that does NOT have the new inline selector
 * (e.g. KPI cards, table cards rendered by other components).
 */
export function addTileFromComponent(payload, meta) {
  _injectCSS();
  _showDashboardPicker(payload, meta);
}

/**
 * Pin a viz-card's data to an existing dashboard via REST.
 * Returns a promise that rejects on failure.
 */
async function _pinVizToExistingDashboard(dashboardId, spec, card) {
  const isTable = card?._g2spec?._isTable === true;
  const res = await fetch(`/api/v1/dashboards/${dashboardId}/tiles`, {
    method: 'POST',
    headers: _authHeaders(),
    body: JSON.stringify({
      title:            spec.tile_title || (isTable ? 'Table' : 'Chart'),
      component_id:     isTable ? 'table' : 'chart',
      spec:             isTable ? {} : (card?._g2spec || {}),
      html:             spec.content || '',
      source_prompt:    spec.source_prompt || spec.query || '',
      source_session_id: spec.session_id || '',
      source_turn_id:   spec.source_turn_id || '',
      profile_id:       spec.profile_id || '',
      profile_tag:      spec.profile_tag || '',
      tile_width:       isTable ? 12 : 6,
      tile_height:      4,
      last_refreshed:   new Date().toISOString(),
    }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  return data.tile_id || null;
}

function _showDashboardPicker(payload, meta) {
  const backdrop = document.createElement('div');
  backdrop.className = 'dash-picker-backdrop';

  const modal = document.createElement('div');
  modal.className = 'dash-picker-modal';

  let selectedDashboardId = _currentDashboardId || null;

  const renderModal = async () => {
    let dashboards = [];
    try {
      const res = await fetch('/api/v1/dashboards', { headers: _authHeaders() });
      const data = await res.json();
      dashboards = data.dashboards || [];
      // Default to the most recently updated dashboard when no current dashboard is active
      if (!selectedDashboardId && dashboards.length) {
        selectedDashboardId = dashboards[0].id;
      }
    } catch (_) {}

    modal.innerHTML = `
      <div class="dash-picker-header">
        <div class="dash-picker-title">Add to Dashboard</div>
        <div class="dash-picker-subtitle">${_esc(payload.title || 'Visual')}</div>
      </div>
      <div class="dash-picker-list">
        ${dashboards.map(d => `
          <div class="dash-picker-item${d.id === selectedDashboardId ? ' selected' : ''}"
               data-dash-id="${d.id}">
            <div class="dash-picker-item-icon">${ICONS.grid}</div>
            <div class="dash-picker-item-name">${_esc(d.name)}</div>
            <div class="dash-picker-item-count">${d.tile_count} tiles</div>
          </div>`).join('')}
        ${dashboards.length === 0 ? `<div style="padding:12px;text-align:center;color:${C.muted};font-size:0.78rem;">No dashboards yet</div>` : ''}
        <div class="dash-picker-new" data-action="new-dash">
          ${ICONS.plus} Create new dashboard
        </div>
      </div>
      <div class="dash-picker-footer">
        <button class="dash-picker-btn" data-action="cancel">Cancel</button>
        <button class="dash-picker-btn primary" data-action="add"
                ${!selectedDashboardId ? 'disabled' : ''}>Add to Dashboard</button>
      </div>
    `;

    // Wire events
    modal.querySelectorAll('.dash-picker-item').forEach(item => {
      item.addEventListener('click', () => {
        modal.querySelectorAll('.dash-picker-item').forEach(i => i.classList.remove('selected'));
        item.classList.add('selected');
        selectedDashboardId = item.dataset.dashId;
        modal.querySelector('[data-action="add"]')?.removeAttribute('disabled');
      });
    });

    modal.querySelector('[data-action="new-dash"]')?.addEventListener('click', () => {
      // Use Uderia dialog for new dashboard name (in picker modal)
      _showInputDialog('New dashboard name', '', async (name) => {
        try {
          const res = await fetch('/api/v1/dashboards', {
            method: 'POST',
            headers: _authHeaders(),
            body: JSON.stringify({ name: name.trim() }),
          });
          const data = await res.json();
          if (res.ok) {
            selectedDashboardId = data.dashboard_id;
            await renderModal(); // re-render with new dashboard in list
          }
        } catch (err) { console.error(err); }
      });
    });

    modal.querySelector('[data-action="cancel"]')?.addEventListener('click', () => backdrop.remove());
    modal.querySelector('[data-action="add"]')?.addEventListener('click', () => _confirmAddTile(selectedDashboardId, payload, meta, backdrop));
  };

  renderModal();
  backdrop.appendChild(modal);
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) backdrop.remove(); });
  document.body.appendChild(backdrop);
}

async function _confirmAddTile(dashboardId, payload, meta, backdrop) {
  if (!dashboardId) return;
  const btn = backdrop.querySelector('[data-action="add"]');
  if (btn) { btn.disabled = true; btn.textContent = 'Adding...'; }

  try {
    const spec = typeof payload.spec === 'string' ? _safeParse(payload.spec) : payload.spec;
    const res = await fetch(`/api/v1/dashboards/${dashboardId}/tiles`, {
      method: 'POST',
      headers: _authHeaders(),
      body: JSON.stringify({
        title: payload.title || 'Chart',
        component_id: payload.component_id || 'chart',
        spec: spec || {},
        html: payload.html || '',
        source_prompt: meta?.source_prompt || '',
        source_session_id: meta?.source_session_id || '',
        source_turn_id: meta?.source_turn_id || '',
        profile_id: meta?.profile_id || '',
        profile_tag: meta?.profile_tag || '',
        tile_width: payload.component_id === 'table' ? 12 : 6,
        tile_height: payload.component_id === 'kpi' ? 2 : 4,
        last_refreshed: new Date().toISOString(),
      }),
    });

    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    backdrop.remove();

    // Show confirmation toast
    _showToast(`Pinned to dashboard`, 'success');

    // If the dashboard canvas is open with this dashboard, reload it
    if (isDashboardPanelActive() && _currentDashboardId === dashboardId) {
      _loadAndRenderDashboard(dashboardId);
    } else {
      // Open the panel with the target dashboard
      const dashRes = await fetch(`/api/v1/dashboards/${dashboardId}`, { headers: _authHeaders() });
      if (dashRes.ok) {
        const dashData = await dashRes.json();
        openDashboardPanel(dashData);
      }
    }
  } catch (err) {
    console.error('[DashboardRenderer] Add tile error:', err);
    if (btn) { btn.disabled = false; btn.textContent = 'Add to Dashboard'; }
    _showToast('Failed to add tile', 'error');
  }
}

// ── Toast notification ────────────────────────────────────────────────────────
function _showToast(message, type = 'success') {
  const toast = document.createElement('div');
  toast.style.cssText = `
    position:fixed;bottom:24px;right:24px;z-index:9999;
    display:flex;align-items:center;gap:8px;
    padding:10px 16px;border-radius:10px;
    font-size:0.8rem;font-weight:600;font-family:inherit;
    background:${type==='success'?'rgba(74,222,128,0.15)':'rgba(248,113,113,0.15)'};
    border:1px solid ${type==='success'?'rgba(74,222,128,0.35)':'rgba(248,113,113,0.35)'};
    color:${type==='success'?'#4ade80':'#f87171'};
    box-shadow:0 8px 24px rgba(0,0,0,0.3);
    backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
    animation:dash-fade-in 0.2s ease;
  `;
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), TOAST_DURATION_MS);
}

// ── Inline chat card renderer ─────────────────────────────────────────────────
export function renderDashboard(containerId, payload) {
  _injectCSS();
  const container = document.getElementById(containerId);
  if (!container) return;

  // payload may arrive as a JSON string (from data-spec attribute) or a plain object
  const spec = typeof payload === 'string' ? JSON.parse(payload) : payload;
  const view = spec?.view;

  if (view === 'card' || view === 'created') {
    _rememberDashSpec(spec.id || spec.dashboard_id, spec);
    container.innerHTML = _buildInlineCard(spec);
  } else if (view === 'open') {
    _rememberDashSpec((spec.dashboard || {}).id, { dashboard: spec.dashboard, tiles: spec.tiles });
    container.innerHTML = _buildInlineCard({ ...spec.dashboard, action: 'opened', tiles: spec.tiles });
    // Auto-open the panel — never while an old answer is replayed on a session reload
    // (the session's own restore decides whether the panel was open).
    if (!window.__suppressComponentReplayOpen) {
      setTimeout(() => { openDashboardPanel({ dashboard: spec.dashboard, tiles: spec.tiles }); }, 100);
    }
  } else if (view === 'list') {
    container.innerHTML = _buildInlineList(spec);
    (spec.dashboards || []).forEach(d => _rememberDashSpec(d.id, { dashboard_id: d.id, name: d.name }));
  } else if (view === 'panel_open') {
    // create action — open the panel silently, no card in chat
    openDashboardPanel({ dashboard_id: spec.dashboard_id, name: spec.name });
    container.innerHTML = '';
  } else if (view === 'tile_added' || view === 'visualization') {
    container.innerHTML = _buildVizCard(spec);
    container._vizSpec = spec; // stored for revert-on-tile-delete
    const card = container.querySelector('.dash-viz-card');

    // "Open canvas" header button — always visible, opens the dashboard panel
    container.querySelector('[data-action="open-canvas"]')?.addEventListener('click', async () => {
      const dashId = spec.dashboard_id || _currentDashboardId;
      if (dashId) {
        openDashboardPanel({ dashboard_id: dashId, name: spec.dashboard_name });
      } else {
        try {
          const res = await fetch('/api/v1/dashboards', { headers: _authHeaders() });
          const d   = await res.json();
          const first = (d.dashboards || [])[0];
          openDashboardPanel(first ? { dashboard_id: first.id, name: first.name } : {});
        } catch (_) { openDashboardPanel({}); }
      }
    });

    // Inline dashboard selector + Pin + Open buttons
    const select     = container.querySelector('[data-action="select-dash"]');
    const addBtn     = container.querySelector('[data-action="add-to-dash"]');
    const newBtn     = container.querySelector('[data-action="add-new-dash"]');
    const openSelBtn = container.querySelector('[data-action="open-selected-dash"]');

    // Track which dashboards this viz has been pinned to (client-side).
    // Pre-seed from server-known state when rendered after a backend add_tile.
    container._pinnedDashIds = new Set(spec.dashboard_id ? [spec.dashboard_id] : []);

    // Build option label — ✓ prefix for already-pinned dashboards
    const _optLabel = (d) =>
      `${d.name}${container._pinnedDashIds.has(d.id) ? ' \u2713' : ''} (${d.tile_count} tiles)`;

    // (Re)populate selector preserving ✓ marks; prefer preferredId then current value
    const _populateSelect = (sel, dashes, preferredId) => {
      const currentVal = preferredId || sel.value;
      sel.innerHTML = dashes.length
        ? dashes.map(d => `<option value="${_esc(d.id)}">${_esc(_optLabel(d))}</option>`).join('')
        : `<option value="">No dashboards yet</option>`;
      if (dashes.length) {
        sel.value = dashes.find(d => d.id === currentVal) ? currentVal : dashes[0].id;
      }
    };

    // Mark a dashboard as pinned and update its option text in-place
    const _markPinned = (dashId) => {
      container._pinnedDashIds.add(dashId);
      const opt = select?.querySelector(`option[value="${dashId}"]`);
      if (opt && !opt.text.startsWith('\u2713 ')) opt.text = '\u2713 ' + opt.text;
    };

    // Open the dashboard currently shown in the dropdown
    const _openSelected = async () => {
      const dashId = select?.value;
      if (!dashId) return;
      if (isDashboardPanelActive() && _currentDashboardId === dashId) {
        _loadAndRenderDashboard(dashId);
      } else {
        try {
          const dr = await fetch(`/api/v1/dashboards/${dashId}`, { headers: _authHeaders() });
          if (dr.ok) openDashboardPanel(await dr.json());
        } catch (_) {}
      }
    };

    if (select && addBtn && newBtn) {
      // Load dashboards asynchronously and populate the selector.
      // Pass the viz card's identity so the API can flag which dashboards
      // already contain a tile from this card (survives page refresh & server restart).
      (async () => {
        try {
          const _sid = encodeURIComponent(spec.session_id || '');
          const _sp  = encodeURIComponent(spec.source_prompt || spec.query || '');
          const res   = await fetch(
            `/api/v1/dashboards?source_session_id=${_sid}&source_prompt=${_sp}`,
            { headers: _authHeaders() }
          );
          const data  = await res.json();
          const dashes = data.dashboards || [];
          // Seed pinned set from server truth — accurate after page refresh / server restart
          dashes.forEach(d => { if (d.pinned) container._pinnedDashIds.add(d.id); });
          // Auto-select: most recently pinned board first, then spec default, then first in list
          const _lastPinned = dashes.slice().reverse().find(d => d.pinned);
          _populateSelect(select, dashes, _lastPinned?.id || spec.dashboard_id || null);
          if (dashes.length) {
            addBtn.disabled = false;
            if (openSelBtn) openSelBtn.disabled = false;
          }
        } catch (_) {
          select.innerHTML = `<option value="">Could not load dashboards</option>`;
        }
        select.disabled = false;
      })();

      // Enable Pin/Open buttons only when a dashboard is selected
      select.addEventListener('change', () => {
        const has = !!select.value;
        addBtn.disabled = !has;
        if (openSelBtn) openSelBtn.disabled = !has;
      });

      // Open icon button — opens the dashboard currently selected in the dropdown
      openSelBtn?.addEventListener('click', _openSelected);

      // "Pin" — pin to the selected dashboard; keep selector, add ✓ mark
      addBtn.addEventListener('click', async () => {
        const dashId = select.value;
        if (!dashId) return;
        addBtn.disabled = true;
        addBtn.innerHTML = 'Pinning\u2026';
        try {
          const tileId = await _pinVizToExistingDashboard(dashId, spec, card);
          if (tileId) container.dataset.pinnedTileId = tileId;
          // ✓ mark the pinned dashboard and keep it selected
          _markPinned(dashId);
          select.value = dashId;
          // Update badge on first pin
          const badge = container.querySelector('.dash-viz-badge-auto');
          if (badge) { badge.className = 'dash-inline-badge'; badge.textContent = 'Pinned'; }
          // Brief confirmation state, then restore
          addBtn.innerHTML = `${ICONS.check} Pinned`;
          addBtn.style.opacity = '0.7';
          setTimeout(() => {
            addBtn.innerHTML = `${ICONS.pin} Pin`;
            addBtn.style.opacity = '';
            addBtn.disabled = false;
          }, 1500);
          _showToast('Pinned to dashboard', 'success');

          // Refresh the dashboard panel so the new tile (with bolt icon) is visible
          // immediately — without needing a page reload.
          if (isDashboardPanelActive() && _currentDashboardId === dashId) {
            _loadAndRenderDashboard(dashId);
          } else {
            try {
              const dr = await fetch(`/api/v1/dashboards/${dashId}`, { headers: _authHeaders() });
              if (dr.ok) openDashboardPanel(await dr.json());
            } catch (_) {}
          }
        } catch (err) {
          addBtn.disabled = false;
          addBtn.innerHTML = `${ICONS.pin} Pin`;
          _showToast('Failed to pin', 'error');
        }
      });

      // "New" — inline name entry
      newBtn.addEventListener('click', () => {
        const row = container.querySelector('.dash-viz-dest-row');
        if (!row) return;
        row.innerHTML = `
          <input class="dash-viz-dest-input" type="text" placeholder="Dashboard name\u2026"
                 style="flex:1;min-width:0;background:var(--card-bg);color:var(--text-primary);
                        border:1px solid var(--border-primary);border-radius:6px;
                        padding:5px 8px;font-size:0.75rem;font-family:inherit;outline:none;">
          <button class="dash-inline-btn primary" data-action="create-and-add">${ICONS.pin} Create & Add</button>
          <button class="dash-inline-btn" data-action="cancel-new">Cancel</button>`;
        const input     = row.querySelector('.dash-viz-dest-input');
        const createBtn = row.querySelector('[data-action="create-and-add"]');
        const cancelBtn = row.querySelector('[data-action="cancel-new"]');
        input.focus();
        input.addEventListener('keydown', e => { if (e.key === 'Enter') createBtn.click(); });

        // Helper: restore the selector row and rebind all events
        const _restoreRow = async (preferredId) => {
          row.innerHTML = `
            <select class="dash-viz-dest-select" data-action="select-dash">
              <option value="">Loading dashboards\u2026</option>
            </select>
            <button class="dash-inline-btn" data-action="add-new-dash">${ICONS.plus} New</button>
            <button class="dash-inline-btn primary" data-action="add-to-dash" disabled>${ICONS.pin} Pin</button>
            <button class="dash-inline-btn icon-only" data-action="open-selected-dash" title="Open selected dashboard" disabled>${ICONS.grid}</button>`;
          const s2 = row.querySelector('[data-action="select-dash"]');
          const a2 = row.querySelector('[data-action="add-to-dash"]');
          const o2 = row.querySelector('[data-action="open-selected-dash"]');
          try {
            const res   = await fetch('/api/v1/dashboards', { headers: _authHeaders() });
            const data  = await res.json();
            const d2    = data.dashboards || [];
            _populateSelect(s2, d2, preferredId);
            if (d2.length) { a2.disabled = false; if (o2) o2.disabled = false; }
            s2.disabled = false;
          } catch (_) { s2.innerHTML = `<option value="">Could not load</option>`; s2.disabled = false; }
          s2.addEventListener('change', () => { const h = !!s2.value; a2.disabled = !h; if (o2) o2.disabled = !h; });
          o2?.addEventListener('click', _openSelected);
          a2.addEventListener('click', () => addBtn.click());
          row.querySelector('[data-action="add-new-dash"]')?.addEventListener('click', () => newBtn.click());
        };

        cancelBtn.addEventListener('click', () => _restoreRow(null));

        createBtn.addEventListener('click', async () => {
          const name = input.value.trim();
          if (!name) { input.focus(); return; }
          createBtn.disabled = true;
          createBtn.textContent = 'Creating\u2026';
          try {
            const cr = await fetch('/api/v1/dashboards', {
              method: 'POST', headers: _authHeaders(),
              body: JSON.stringify({ name }),
            });
            if (!cr.ok) throw new Error();
            const crData    = await cr.json();
            const newDashId = crData.dashboard_id;
            const tileId    = await _pinVizToExistingDashboard(newDashId, spec, card);
            if (tileId) container.dataset.pinnedTileId = tileId;
            // Mark the new dashboard as pinned before restoring the row
            container._pinnedDashIds.add(newDashId);
            // Refresh all other viz-card selectors to include the new dashboard
            _refreshAllVizCardSelectors();
            // Restore row with ✓ on the new dashboard, auto-selected
            await _restoreRow(newDashId);
            // Update badge
            const badge = container.querySelector('.dash-viz-badge-auto');
            if (badge) { badge.className = 'dash-inline-badge'; badge.textContent = 'Pinned'; }
            _showToast(`Created \u201c${name}\u201d and pinned`, 'success');
            openDashboardPanel({ dashboard_id: newDashId, name });
          } catch (_) {
            _showToast('Failed to create dashboard', 'error');
            createBtn.disabled = false;
            createBtn.textContent = `${ICONS.pin} Create & Add`;
          }
        });
      });
    }

    // Render chart after DOM is ready
    requestAnimationFrame(() => _renderVizIntoCard(container, spec));

  } else if (view === 'close') {
    closeDashboardPanel();
    container.innerHTML = `<div style="font-size:0.78rem;color:${C.muted};padding:6px 0;">Dashboard canvas closed.</div>`;
  } else if (view === 'deleted') {
    container.innerHTML = `<div class="dash-inline-card">
      <div class="dash-inline-card-header">
        <div class="dash-inline-icon">${ICONS.trash}</div>
        <div class="dash-inline-title">Dashboard deleted</div>
      </div>
      <div class="dash-inline-body" style="font-size:0.78rem;">
        <strong>${_esc(spec.name)}</strong> has been removed.
      </div>
    </div>`;
    if (isDashboardPanelActive() && _currentDashboardId === spec.dashboard_id) {
      closeDashboardPanel();
    }
  } else if (view === 'error') {
    container.innerHTML = `<div style="color:#f87171;font-size:0.78rem;padding:6px 0;display:flex;align-items:center;gap:6px;"><span style="flex-shrink:0;width:14px;height:14px;display:inline-flex;">${ICONS.error}</span>${_esc(spec.message || 'Error')}</div>`;
  } else if (view === 'refresh') {
    // Trigger refresh in open panel
    if (isDashboardPanelActive() && _currentDashboardId === spec.dashboard_id) {
      if (spec.tile_id) {
        _refreshTile(spec.tile_id);
      } else {
        (_dashboardData?.tiles || []).forEach(t => _refreshTile(t.id));
      }
    }
    container.innerHTML = `<div style="font-size:0.78rem;color:${C.muted};padding:6px 0;">Refreshing ${_esc(spec.dashboard_name)}...</div>`;
  }
}

function _buildInlineCard(spec) {
  const tileCount = spec.tile_count ?? (spec.tiles ? spec.tiles.length : 0);
  const action = spec.action === 'created' ? 'Created' : 'Dashboard';
  return `
    <div class="dash-inline-card">
      <div class="dash-inline-card-header">
        <div class="dash-inline-icon">${ICONS.grid}</div>
        <div class="dash-inline-title" data-ans-restates>${_esc(spec.name || 'Dashboard')}</div>
        ${spec.action === 'created' ? `<div class="dash-inline-badge" data-ans-restates>New</div>` : ''}
      </div>
      <div class="dash-inline-body">
        ${tileCount > 0
          ? `${tileCount} tile${tileCount !== 1 ? 's' : ''} — ${_esc(spec.description || 'Live dashboard canvas')}`
          : _esc(spec.description || 'Empty dashboard — add tiles by chatting and pinning visuals')}
      </div>
      <div class="dash-inline-footer">
        ${_dashOpenControlHtml(spec.id || spec.dashboard_id, spec.name, false)}
      </div>
    </div>`;
}

function _buildInlineList(spec) {
  const dashboards = spec.dashboards || [];
  if (dashboards.length === 0) {
    return `<div class="dash-inline-card">
      <div class="dash-inline-card-header">
        <div class="dash-inline-icon">${ICONS.grid}</div>
        <div class="dash-inline-title">Your Dashboards</div>
      </div>
      <div class="dash-inline-body">No dashboards yet. Create one to get started.</div>
    </div>`;
  }
  return `
    <div class="dash-inline-card">
      <div class="dash-inline-card-header">
        <div class="dash-inline-icon">${ICONS.grid}</div>
        <div class="dash-inline-title">Your Dashboards</div>
        <div class="dash-inline-badge">${dashboards.length}</div>
      </div>
      <ul class="dash-inline-list">
        ${dashboards.map(d => `
          <li class="dash-inline-list-item" data-dash-id="${_esc(d.id)}">
            <div class="dash-inline-list-item-name">${_esc(d.name)}</div>
            <div class="dash-inline-list-item-count">${d.tile_count} tiles</div>
            <div class="dash-inline-list-item-ts">${_relTime(d.updated_at)}</div>
            ${_dashOpenControlHtml(d.id, d.name, false)}
          </li>`).join('')}
      </ul>
    </div>`;
}

// ── Auto-Viz Intelligence Engine ──────────────────────────────────────────────
// Colours: Uderia brand first, then a varied accessible palette
const _VIZ_COLORS = [
  '#F15F22','#9333ea','#3b82f6','#4ade80',
  '#f59e0b','#ec4899','#06b6d4','#84cc16',
  '#f43f5e','#8b5cf6',
];

/** Extract a chart-type hint from title/query/chart_type text (keyword match). Returns null if no hint.
 *  Every keyword is word-bounded: "airline", "deadline", "timeline" and "share price" must not
 *  read as a chart request (they did — a query naming an airline forced a Line chart). */
function _extractVizHint(text) {
  const t = (text || '').toLowerCase();
  if (/\b(pie|donut|share|portion|breakdown)\b/.test(t))                                  return 'Pie';
  if (/\b(line|trend|trends|history|growth|monthly|weekly|daily|quarterly|yearly)\b|over.?time/.test(t)) return 'Line';
  if (/\b(scatter|correlation|versus)\b|\bvs\.?\s/.test(t))                               return 'Scatter';
  if (/\bbar\b/.test(t))                                                                  return 'Bar';    // horizontal bar
  if (/\b(column|compare|comparison|ranking)\b/.test(t))                                  return 'Column'; // vertical bar
  return null;
}

/** Normalise one table cell the way the database would not: a warehouse CHAR column
 *  arrives space-padded ("31-OCT-2023           "), a NULL arrives as the word null/None,
 *  and a multi-line cell collapses to one line. Returns '' for every absent-value spelling
 *  so the typing below never counts "None" as a category. */
const _NULL_TOKENS = new Set(['null', 'none', 'nan', 'n/a', 'na', 'nil', '(null)', '-', '—']);
function _cleanCell(v) {
  if (v == null) return '';
  const s = String(v).replace(/\s+/g, ' ').trim();
  return _NULL_TOKENS.has(s.toLowerCase()) ? '' : s;
}

/** Parse first HTML <table> in an HTML string → {columns, rows} or null */
function _parseHtmlTable(html) {
  if (!html) return null;
  const tmp = document.createElement('div');
  tmp.innerHTML = html;
  const table = tmp.querySelector('table');
  if (!table) return null;
  let columns = Array.from(table.querySelectorAll('thead th, thead td'))
    .map(el => _cleanCell(el.textContent));
  const rows = [];
  table.querySelectorAll('tbody tr').forEach(tr => {
    const cells = Array.from(tr.querySelectorAll('td')).map(td => _cleanCell(td.textContent));
    if (cells.length && cells.some(c => c !== '')) rows.push(cells);
  });
  // A header-less table (no <thead>): the first body row is the header when it carries
  // no numbers at all and at least one later row does — the shape a bare markdown→HTML
  // conversion or a CSV paste produces.
  if (!columns.length && rows.length >= 2) {
    const first = rows[0];
    const firstIsText = first.every(c => c !== '' && !_NUM_RE.test(_cleanNumText(c)));
    const laterHasNum = rows.slice(1).some(r => r.some(c => _NUM_RE.test(_cleanNumText(c))));
    if (firstIsText && laterHasNum) columns = rows.shift();
  }
  if (!columns.length) return null;
  return rows.length ? { columns, rows } : null;
}

// ── Column typing ────────────────────────────────────────────────────────────
// The date-form catalogue. Each form is a regex plus a parser to epoch ms, so the SAME
// table decides both "is this column temporal" and "which point comes first on the axis".
// The original detector knew ISO dates and English month names only; a Teradata
// DD-MON-YYYY date ("31-OCT-2023") fell through, and because the numeric test used
// parseFloat — which reads "31-OCT-2023" as 31 — a whole date column was typed NUMERIC and
// plotted as the numbers 28..31 on a 0..32 axis. Numeric now means the WHOLE string.
const _MON = { jan: 0, feb: 1, mar: 2, apr: 3, may: 4, jun: 5, jul: 6, aug: 7, sep: 8, sept: 8, oct: 9, nov: 10, dec: 11 };
const _MON_RE = '(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*';
const _yr = y => { const n = +y; return y.length === 2 ? (n < 70 ? 2000 + n : 1900 + n) : n; };
const _utc = (y, m, d) => Date.UTC(y, m, d);
const _DATE_FORMS = [
  // ISO 8601 date, optional time — 2023-10-31, 2023-10-31T08:15:00Z, 2023-10-31 08:15
  { re: /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?)?(?:Z|[+-]\d{2}:?\d{2})?$/i,
    ms: m => Date.UTC(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0)) },
  // YYYY-MM · YYYY/MM/DD · YYYYMMDD is deliberately NOT a form (indistinguishable from a count)
  { re: /^(\d{4})-(\d{2})$/,                    ms: m => _utc(+m[1], +m[2] - 1, 1) },
  { re: /^(\d{4})\/(\d{1,2})\/(\d{1,2})$/,      ms: m => _utc(+m[1], +m[2] - 1, +m[3]) },
  // DD-MON-YYYY / DD MON YYYY / DD.MON.YY — the Teradata and Oracle default date rendering
  { re: new RegExp(`^(\\d{1,2})[-\\s./]${_MON_RE}[-\\s./](\\d{2}|\\d{4})$`, 'i'),
    ms: m => _utc(_yr(m[3]), _MON[m[2].slice(0, 3).toLowerCase()], +m[1]) },
  // MON DD, YYYY / MON DD YYYY / MON-DD-YYYY — "Oct 31, 2023"
  { re: new RegExp(`^${_MON_RE}[-\\s./]?(\\d{1,2}),?[-\\s./](\\d{4})$`, 'i'),
    ms: m => _utc(+m[3], _MON[m[1].slice(0, 3).toLowerCase()], +m[2]) },
  // MON YYYY / MON-YYYY / MONTH YYYY — "Oct 2023", "October 2023"
  { re: new RegExp(`^${_MON_RE}[-\\s./]?(\\d{4})$`, 'i'),
    ms: m => _utc(+m[2], _MON[m[1].slice(0, 3).toLowerCase()], 1) },
  // MM/YYYY · MM-YYYY
  { re: /^(\d{1,2})[\/-](\d{4})$/,               ms: m => _utc(+m[2], +m[1] - 1, 1) },
  // DD/MM/YYYY or MM/DD/YYYY (also . and -). The order is decided PER COLUMN by
  // _slashOrder (any first part > 12 ⇒ day-first); the parser reads `order`.
  { re: /^(\d{1,2})[\/.-](\d{1,2})[\/.-](\d{2}|\d{4})$/, slash: true,
    ms: (m, order) => order === 'DMY' ? _utc(_yr(m[3]), +m[2] - 1, +m[1]) : _utc(_yr(m[3]), +m[1] - 1, +m[2]) },
  // Quarters, halves, ISO weeks — 2023Q4, Q4 2023, Q4-2023, Q4, H1 2023, 2023-W44
  { re: /^(\d{4})[-\s]?Q([1-4])$/i,             ms: m => _utc(+m[1], (+m[2] - 1) * 3, 1) },
  { re: /^Q([1-4])(?:[-\s/']?(\d{2}|\d{4}))?$/i, ms: m => _utc(m[2] ? _yr(m[2]) : 2000, (+m[1] - 1) * 3, 1) },
  { re: /^(?:H|S)([12])[-\s]?(\d{4})$/i,        ms: m => _utc(+m[2], (+m[1] - 1) * 6, 1) },
  { re: /^(\d{4})-?W(\d{2})$/i,                 ms: m => _utc(+m[1], 0, 1 + (+m[2] - 1) * 7) },
  // A bare month name — "January" / "Jan": ordered by month, no year
  { re: new RegExp(`^${_MON_RE}$`, 'i'),         ms: m => _utc(2000, _MON[m[1].slice(0, 3).toLowerCase()], 1) },
  // A bare year — only within a plausible range, so a column of 4-digit COUNTS stays numeric
  { re: /^(\d{4})$/, year: true,                 ms: m => _utc(+m[1], 0, 1) },
];
function _dateForm(v) {
  const s = String(v).trim();
  for (const f of _DATE_FORMS) {
    const m = f.re.exec(s);
    if (!m) continue;
    if (f.year && (+m[1] < 1800 || +m[1] > 2200)) return null;
    return { form: f, m };
  }
  return null;
}
/** Day-first or month-first for slash dates — decided once per column from the evidence:
 *  any first part above 12 proves day-first; any second part above 12 proves month-first;
 *  with no evidence either way the US reading is kept (the more common export form). */
function _slashOrder(values) {
  let dmy = false, mdy = false;
  for (const v of values) {
    const m = /^(\d{1,2})[\/.-](\d{1,2})[\/.-](\d{2}|\d{4})$/.exec(String(v || '').trim());
    if (!m) continue;
    if (+m[1] > 12) dmy = true;
    if (+m[2] > 12) mdy = true;
  }
  if (dmy && !mdy) return 'DMY';
  return 'MDY';
}
/** Epoch ms for a recognised date string, NaN otherwise. `order` = _slashOrder(column). */
function _parseDateValue(v, order) {
  const hit = _dateForm(v);
  if (!hit) return NaN;
  const ms = hit.form.ms(hit.m, order || 'MDY');
  return Number.isFinite(ms) ? ms : NaN;
}

// Numeric = the WHOLE cleaned string is a number. "1,234.5", "$12K"-style suffixes are
// not numbers here (a K/M suffix is display formatting, never a warehouse value), "(123)"
// is an accounting negative, "−5" carries the unicode minus.
const _NUM_RE = /^[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?$/;
function _cleanNumText(v) {
  let s = String(v == null ? '' : v).replace(/[\s$€£¥%,]/g, '').replace(/[−–]/g, '-');
  if (/^\(.*\)$/.test(s)) s = '-' + s.slice(1, -1);
  return s;
}

/** Classify a column as 'temporal' | 'numeric' | 'categorical'.
 *  Absent values (after _cleanCell) never vote. A column is temporal when EVERY sampled
 *  value is a recognised date form; numeric when EVERY sampled value is a whole-string
 *  number; else categorical. Dates are tested first so a year column reads as time. */
function _detectColType(values) {
  const s = values.map(_cleanCell).filter(v => v !== '');
  if (!s.length) return 'categorical';
  const sample = s.slice(0, 60);
  if (sample.every(v => _dateForm(v))) return 'temporal';
  if (sample.every(v => _NUM_RE.test(_cleanNumText(v)))) return 'numeric';
  return 'categorical';
}

function _parseNum(v) {
  if (typeof v === 'number') return v;
  const s = _cleanNumText(v);
  return _NUM_RE.test(s) ? parseFloat(s) : 0;
}
function _fmtNum(n) {
  const v = _parseNum(n);
  if (Math.abs(v) >= 1e9) return (v / 1e9).toFixed(1) + 'B';
  if (Math.abs(v) >= 1e6) return (v / 1e6).toFixed(1) + 'M';
  if (Math.abs(v) >= 1e3) return (v / 1e3).toFixed(1) + 'K';
  return v % 1 === 0 ? v.toLocaleString() : v.toFixed(2);
}

/**
 * Select the best chart type given column names, inferred types, row count, and optional hint.
 * Decision tree (hint always wins):
 *   temporal + numeric → Line
 *   2-col cat+num, ≤7 rows → Pie   (part-of-whole)
 *   2-col cat+num, ≤14 rows → Column (ranked comparison)
 *   2-col cat+num, >14 rows → Bar   (many categories)
 *   cat + num(s) → Column
 *   single row → KPI
 *   default → Column
 */
function _selectChartType(columns, types, rows, hint) {
  if (hint) return hint;
  const numCols  = types.filter(t => t === 'numeric').length;
  const catCols  = types.filter(t => t === 'categorical').length;
  const timeCols = types.filter(t => t === 'temporal').length;
  const n = rows.length;
  if (n === 1 && numCols >= 1)                              return 'KPI';
  if (timeCols >= 1 && numCols >= 1)                        return 'Line';
  if (columns.length === 2 && catCols === 1 && numCols === 1) {
    // When category labels are long, horizontal Bar handles them far better than Pie spider labels
    const catIdx = types.findIndex(t => t === 'categorical');
    const avgLen = n ? rows.reduce((s, r) => s + (r[catIdx] || '').length, 0) / n : 0;
    if (n <= 7) {
      if (avgLen > 12) return 'Bar'; // long names → horizontal bar, no spider-label overlap
      return 'Pie';
    }
    if (n <= 14) return 'Column';
    return 'Bar';
  }
  if (catCols >= 1 && numCols >= 1)                         return 'Column';
  return 'Column';
}

/** Stable chronological sort of a typed data array on its temporal column, in place.
 *  Unparseable values keep their relative order and go last (never silently dropped). */
function _sortChronological(data, timeCol) {
  const order = _slashOrder(data.map(d => d[timeCol]));
  const keyed = data.map((d, i) => ({ d, i, t: _parseDateValue(d[timeCol], order) }));
  keyed.sort((a, b) => {
    const an = Number.isNaN(a.t), bn = Number.isNaN(b.t);
    if (an && bn) return a.i - b.i;
    if (an) return 1;
    if (bn) return -1;
    return a.t - b.t || a.i - b.i;
  });
  keyed.forEach((k, i) => { data[i] = k.d; });
  return data;
}

/** THE measure among a result's numeric columns — the ONE rule every chart and KPI path
 *  asks. A numeric column is not automatically a measure: `month_number`, `customer_id`,
 *  `rank`, `year` are keys or ordinals that happen to be numbers, and plotting one draws a
 *  meaningless staircase (prod session 42eb166c turn 2: robberies per month charted
 *  `month_number` 1..12 as a straight line instead of `robbery_count`). A column is key-like
 *  when its NAME ends in a key/ordinal word or its VALUES are a consecutive integer run in
 *  row order. The first numeric column that is neither is the measure; when every numeric
 *  column looks key-like the LAST one wins (a SELECT lists its measure last); none → -1. */
const _KEYLIKE_NAME_RE = /(^|[_\s.-])(id|no|nr|num|number|seq|sequence|rank|ranking|index|idx|key|code|ordinal|pos|position|year|yr|month|mon|day|week|wk|quarter|qtr|hour|hr|minute|min)$/i;
function _isConsecutiveRun(values) {
  const n = values.map(_cleanCell).filter(v => v !== '').map(v => _cleanNumText(v));
  if (n.length < 3 || !n.every(v => /^[-+]?\d+$/.test(v))) return false;
  const k = n.map(Number);
  for (let i = 1; i < k.length; i++) if (k[i] !== k[i - 1] + 1) return false;
  return true;
}
function _measureIndex(columns, types, rows) {
  const nums = types.map((t, i) => t === 'numeric' ? i : -1).filter(i => i >= 0);
  if (!nums.length) return -1;
  const keyLike = i => _KEYLIKE_NAME_RE.test(String(columns[i] || '').trim()) || _isConsecutiveRun(rows.map(r => r[i]));
  const measure = nums.find(i => !keyLike(i));
  return measure !== undefined ? measure : nums[nums.length - 1];
}

/** Test seam — the pure typing/ordering functions, so a gate can execute exactly what
 *  the shipped renderer runs (no browser, no G2Plot). Not for callers. */
export const _vizInternals = {
  cleanCell: _cleanCell, detectColType: _detectColType, parseNum: _parseNum,
  parseDateValue: _parseDateValue, slashOrder: _slashOrder, sortChronological: _sortChronological,
  selectChartType: _selectChartType, extractVizHint: _extractVizHint, buildG2PlotSpec: _buildG2PlotSpec,
  measureIndex: _measureIndex,
};

/** Returns true when the UI is in light mode.
 *  ThemeManager sets data-theme on <body> (not <html>), so we read from body — matching canvas/renderer.js. */
function _isLightTheme() {
  return document.body.getAttribute('data-theme') === 'light';
}
/** Primary text colour for G2Plot labels/legend — adapts to current theme. */
function _chartTextColor(light = _isLightTheme()) {
  return light ? '#334155' : 'rgba(255,255,255,0.85)';
}
/** Secondary/axis text colour — slightly muted. */
function _chartAxisColor(light = _isLightTheme()) {
  return light ? '#64748b' : 'rgba(255,255,255,0.55)';
}
/**
 * Re-inject current-theme colours into a stored G2Plot options object.
 * Dashboard tile specs are serialised with colours baked in at pin time (possibly
 * in the opposite theme). This helper overwrites only the text/axis fill values
 * so the chart is always readable regardless of when it was first created.
 *
 * IMPORTANT: This must be called every time a chart tile is rendered (not cached),
 * because the theme can change between renders. The MutationObserver in _injectCSS
 * triggers _renderGrid() on theme change, which calls this function fresh.
 */
function _patchChartColorsForTheme(options, light = _isLightTheme()) {
  if (!options) return {};
  const o = JSON.parse(JSON.stringify(options)); // deep clone — never mutate the stored spec
  const textC = _chartTextColor(light);
  const axisC = _chartAxisColor(light);

  // Legend label text — ensure the path exists and always write the colour,
  // even if the legend config has no style object yet (G2Plot merges options).
  if (o.legend && o.legend !== false) {
    if (!o.legend.itemName) o.legend.itemName = {};
    if (!o.legend.itemName.style) o.legend.itemName.style = {};
    o.legend.itemName.style.fill = textC;
  }

  // Value labels on bars / columns / pie outer labels (never patch inner pie labels)
  if (o.label?.style?.fill !== undefined && o.label?.type !== 'inner') o.label.style.fill = textC;
  // Axis tick labels
  if (o.xAxis?.label?.style?.fill !== undefined) o.xAxis.label.style.fill = axisC;
  if (o.yAxis?.label?.style?.fill !== undefined) o.yAxis.label.style.fill = axisC;
  return o;
}

/** Build a G2Plot options wrapper {type, options} from parsed data */
function _buildG2PlotSpec(chartType, columns, types, rows) {
  const catCol  = columns.find((_, i) => types[i] === 'categorical') || columns[0];
  const _mi     = _measureIndex(columns, types, rows);
  const numCol  = _mi >= 0 ? columns[_mi] : columns[columns.length - 1];
  const timeCol = columns.find((_, i) => types[i] === 'temporal')    || columns[0];

  // Build typed data array
  const data = rows.map(r => {
    const obj = {};
    columns.forEach((col, i) => {
      obj[col] = (i < r.length)
        ? (types[i] === 'numeric' ? _parseNum(r[i] || '0') : (r[i] || ''))
        : (types[i] === 'numeric' ? 0 : '');
    });
    return obj;
  });

  // Sort descending by numeric value for categorical charts
  if (chartType !== 'Line') {
    data.sort((a, b) => _parseNum(b[numCol]) - _parseNum(a[numCol]));
  } else if (types[columns.indexOf(timeCol)] === 'temporal') {
    // A line reads left-to-right as time. The rows arrive in whatever order the query
    // produced — a `TOP 30 … ORDER BY date DESC`, or a text column sorted lexically —
    // and drawing them in that order zigzags the line. Sort chronologically here,
    // deterministically; the model never has to remember to ORDER BY ASC.
    _sortChronological(data, timeCol);
  }

  const labelStyle = { fontSize: 11, fill: _chartTextColor(), fontFamily: 'inherit' };
  const axisLabelStyle = { fill: _chartAxisColor(), fontSize: 11 };

  if (chartType === 'Pie') {
    const avgLabelLen = data.length
      ? data.reduce((s, d) => s + String(d[catCol]).length, 0) / data.length : 0;
    const longNames = avgLabelLen > 12;
    return {
      type: 'Pie',
      options: {
        data,
        angleField: numCol,
        colorField: catCol,
        color: _VIZ_COLORS,
        radius: 0.68,
        innerRadius: longNames ? 0.40 : 0.36,
        appendPadding: [4, 0, 12, 0],
        // Long names: inner % labels only, legend carries the names → no overlap
        // Short names: outer labels with name + %
        label: longNames ? {
          type: 'inner',
          offset: '-40%',
          content: '{percentage}',
          style: { fontSize: 11, fontWeight: 700, fill: '#fff', textAlign: 'center' },
          autoRotate: false,
        } : {
          type: 'outer',
          content: '{name}: {percentage}',
          style: labelStyle,
        },
        statistic: { title: false, content: false }, // suppress 'Total XXXXK' centre text
        legend: {
          position: 'bottom',
          flipPage: false,
          maxRow: 2,
          itemName: { style: { fill: _chartTextColor(), fontSize: 11 } },
        },
        tooltip: { formatter: d => ({ name: d[catCol], value: _fmtNum(d[numCol]) }) },
        interactions: [{ type: 'element-active' }],
        pieStyle: { lineWidth: 2, stroke: 'rgba(0,0,0,0.2)' },
      },
    };
  }

  if (chartType === 'Bar') {
    return {
      type: 'Bar',
      options: {
        data: [...data].reverse(), // lowest at top = natural reading order
        xField: numCol,
        yField: catCol,
        color: _VIZ_COLORS[0],
        barStyle: { radius: [0, 4, 4, 0] },
        label: {
          position: 'right',
          formatter: d => _fmtNum(d[numCol]),
          style: labelStyle,
        },
        xAxis: { label: { formatter: v => _fmtNum(v), style: axisLabelStyle } },
        // yAxis shows category names (strings) — no numeric formatter
        yAxis: { label: { style: axisLabelStyle } },
        tooltip: { formatter: d => ({ name: d[catCol], value: _fmtNum(d[numCol]) }) },
      },
    };
  }

  if (chartType === 'Line') {
    return {
      type: 'Line',
      options: {
        data,
        xField: timeCol,
        yField: numCol,
        color: _VIZ_COLORS[0],
        lineStyle: { lineWidth: 2.5 },
        point: { size: 4, style: { fill: _VIZ_COLORS[0], lineWidth: 2, stroke: '#fff' } },
        smooth: true,
        xAxis: { label: { style: axisLabelStyle } },
        yAxis: { label: { formatter: v => _fmtNum(v), style: axisLabelStyle } },
        tooltip: { formatter: d => ({ name: numCol, value: _fmtNum(d[numCol]) }) },
      },
    };
  }

  // Default: Column
  return {
    type: 'Column',
    options: {
      data,
      xField: catCol,
      yField: numCol,
      color: _VIZ_COLORS[0],
      columnStyle: { radius: [6, 6, 0, 0] },
      label: {
        position: 'top',
        formatter: d => _fmtNum(d[numCol]),
        style: labelStyle,
      },
      xAxis: { label: { autoRotate: true, autoEllipsis: true, style: axisLabelStyle } },
      yAxis: { label: { formatter: v => _fmtNum(v), style: axisLabelStyle } },
      tooltip: { formatter: d => ({ name: d[catCol], value: _fmtNum(d[numCol]) }) },
    },
  };
}

/** Build KPI card HTML for single-value or metric-list data */
function _buildKpiContent(columns, types, rows) {
  const items = [];
  if (rows.length === 1) {
    columns.forEach((col, i) => {
      if (types[i] === 'numeric') items.push({ label: col, value: _fmtNum(rows[0][i]) });
    });
  } else {
    const numIdx = _measureIndex(columns, types, rows);
    const idx = numIdx >= 0 ? numIdx : 1;
    rows.slice(0, 6).forEach(r => {
      items.push({ label: r[0] || '', value: _fmtNum(r[idx] || r[1] || '0') });
    });
  }
  return `<div class="dash-kpi-grid">${
    items.map(m => `
      <div class="dash-kpi-item">
        <div class="dash-kpi-value">${_esc(m.value)}</div>
        <div class="dash-kpi-label">${_esc(m.label)}</div>
      </div>`).join('')
  }</div>`;
}

/**
 * Build the inline visualization card HTML.
 * Chart area is empty — JS fills it after DOM insertion via _renderVizIntoCard().
 * view='tile_added': tile already pinned → "View in Dashboard" button only.
 * view='visualization': not yet pinned → "Add to Dashboard" + "Open Dashboard" buttons.
 */
function _buildVizCard(spec) {
  const isPinned = !!spec.dashboard_id;
  const title    = spec.tile_title || spec.name || 'Visualization';
  const badge    = isPinned
    ? `<div class="dash-inline-badge">Pinned</div>`
    : `<div class="dash-viz-badge-auto">auto viz</div>`;
  // Always show the selector footer — even when already pinned, so the user
  // can pin to additional dashboards or open any board directly.
  const footer = `<div class="dash-viz-dest-row">
      <select class="dash-viz-dest-select" data-action="select-dash" disabled>
        <option value="">Loading dashboards…</option>
      </select>
      <button class="dash-inline-btn" data-action="add-new-dash">${ICONS.plus} New</button>
      <button class="dash-inline-btn primary" data-action="add-to-dash" disabled>${ICONS.pin} Pin</button>
      <button class="dash-inline-btn icon-only" data-action="open-selected-dash" title="Open selected dashboard" disabled>${ICONS.grid}</button>
    </div>`;
  return `
    <div class="dash-viz-card">
      <div class="dash-viz-card-header">
        <div class="dash-inline-icon">${ICONS.chart}</div>
        <div class="dash-viz-title">${_esc(title)}</div>
        ${badge}
        <button type="button" class="dash-viz-open-btn" data-action="open-canvas" title="Open the dashboard panel" aria-label="Open the dashboard panel">${ICONS.grid}</button>
      </div>
      <div class="dash-viz-chart-area"></div>
      <div class="dash-viz-footer">${footer}</div>
    </div>`;
}

/**
 * Render the auto-viz chart into .dash-viz-chart-area inside cardContainer.
 * Stores the computed G2Plot spec on card._g2spec for the "Add to Dashboard" handler.
 */
/**
 * Re-derive a G2Plot spec from fresh HTML, locking to the stored chart type.
 * Used by _pollTileRefresh to preserve the tile's visualization style when
 * the session pipeline returns a raw HTML table instead of a component_render
 * event. Reuses existing parsing primitives — no duplication.
 *
 * @param {string} html            Fresh HTML containing a data table
 * @param {string} lockedChartType Chart type to preserve: 'Pie'|'Column'|'Bar'|'Line'
 * @returns {{type:string, options:object}|null}  G2Plot spec, or null if no table found
 */
function _rebuildSpecFromHtml(html, lockedChartType) {
  const parsed = _parseHtmlTable(html);
  if (!parsed) return null;
  const { columns, rows } = parsed;
  const types = columns.map((_, i) => _detectColType(rows.map(r => r[i] || '')));

  // Same multi-categorical column merge as _renderVizIntoCard
  const catIndices = types.map((t, i) => t === 'categorical' ? i : -1).filter(i => i >= 0);
  let chartColumns = columns, chartTypes = types, chartRows = rows;
  if (catIndices.length > 1) {
    const numIdx = _measureIndex(columns, types, rows);
    if (numIdx >= 0) {
      const combinedLabel = catIndices.map(i => columns[i]).join(' ');
      chartColumns = [combinedLabel, columns[numIdx]];
      chartTypes   = ['categorical', 'numeric'];
      chartRows    = rows.map(r => [catIndices.map(i => r[i] || '').join(' '), r[numIdx] || '0']);
    }
  }

  // Lock to the tile's stored chart type; only auto-select when nothing is stored
  const type = lockedChartType || _selectChartType(chartColumns, chartTypes, chartRows, '');
  if (!type || type === 'KPI') return null; // KPI tiles keep HTML as-is
  return _buildG2PlotSpec(type, chartColumns, chartTypes, chartRows);
}

function _renderVizIntoCard(cardContainer, spec) {
  const chartArea = cardContainer.querySelector('.dash-viz-chart-area');
  const card      = cardContainer.querySelector('.dash-viz-card');
  if (!chartArea || !card) return;

  // Resolution order: explicit chart_type arg → user's query text → tile title
  const hint   = _extractVizHint(spec.chart_type || '') || _extractVizHint(spec.query || '') || _extractVizHint(spec.tile_title || '');
  const parsed = _parseHtmlTable(spec.content || '');

  // force_table: show the HTML content as a styled table, no chart (used for
  // intercepted plain-text response tables so they don't double-chart when
  // the dashboard component also rendered a chart viz card below).
  if (spec.force_table) {
    chartArea.style.height = 'auto';
    chartArea.innerHTML = `<div style="overflow:auto;max-height:360px;padding:2px 4px 4px;font-size:0.82rem;color:var(--text-primary);">${spec.content || ''}</div>`;
    if (card) card._g2spec = { _isTable: true, _content: spec.content };
    return;
  }

  const _fallbackHtml = () => {
    if (spec.content) {
      chartArea.style.height = 'auto';
      chartArea.innerHTML = `<div style="padding:10px 12px;overflow:auto;max-height:300px;font-size:0.8rem;color:var(--text-primary);">${
        typeof marked !== 'undefined' ? marked.parse(spec.content) : spec.content
      }</div>`;
    } else if (spec.view === 'tile_added') {
      chartArea.style.height = 'auto';
      chartArea.innerHTML = `<div style="padding:16px 12px;text-align:center;font-size:0.8rem;color:${C.muted};">Tile pinned to dashboard. Open the dashboard to view and refresh it.</div>`;
    } else {
      chartArea.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:100%;font-size:0.75rem;color:${C.muted};">No data to visualize</div>`;
    }
  };

  if (!parsed) { card._g2spec = {}; _fallbackHtml(); return; }

  const { columns, rows } = parsed;
  const types     = columns.map((_, i) => _detectColType(rows.map(r => r[i] || '')));

  // When there are multiple categorical columns (e.g. First Name + Last Name + Revenue),
  // concatenate them into a single label column so the chart has clean category names.
  const catIndices = types.map((t, i) => t === 'categorical' ? i : -1).filter(i => i >= 0);
  let chartColumns = columns;
  let chartTypes   = types;
  let chartRows    = rows;
  if (catIndices.length > 1) {
    const numIdx = _measureIndex(columns, types, rows);
    const combinedLabel = catIndices.map(i => columns[i]).join(' ');
    // Build 2-column dataset: [combined-label, numeric]
    if (numIdx >= 0) {
      chartColumns = [combinedLabel, columns[numIdx]];
      chartTypes   = ['categorical', 'numeric'];
      chartRows    = rows.map(r => [
        catIndices.map(i => r[i] || '').join(' '),
        r[numIdx] || '0',
      ]);
    }
  }

  const chartType = _selectChartType(chartColumns, chartTypes, chartRows, hint);

  if (chartType === 'KPI') {
    chartArea.style.height = 'auto';
    chartArea.innerHTML = _buildKpiContent(columns, types, rows);
    card._g2spec = {};
    return;
  }

  const g2spec = _buildG2PlotSpec(chartType, columns, types, rows);
  card._g2spec = g2spec || {};

  if (!g2spec || typeof G2Plot === 'undefined' || !G2Plot[g2spec.type]) {
    _fallbackHtml(); return;
  }

  try {
    const plot = new G2Plot[g2spec.type](chartArea, {
      ...g2spec.options,
      // 'auto' lets G2Plot reserve space for axis labels (matches the dashboard
      // tile path); a fixed numeric padding clips long y-axis category labels on
      // the left because .dash-viz-chart-area has overflow:hidden.
      padding: 'auto',
      autoFit: true,
    });
    plot.render();
    chartArea._plot = plot;
  } catch (err) {
    console.error('[DashboardRenderer] Inline viz G2Plot error:', err, g2spec.type);
    _fallbackHtml();
  }
}

// ── Utilities ─────────────────────────────────────────────────────────────────
function _esc(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function _safeParse(str) {
  try { return typeof str === 'string' ? JSON.parse(str) : str; }
  catch { return {}; }
}

// Decode the HTML entities that survive inside a single-quoted data-spec
// attribute (&amp; &lt; &gt; &quot; &#39;) so the embedded JSON can be parsed.
function _decodeEntities(s) {
  if (!s) return '';
  return String(s)
    .replace(/&quot;/g, '"')
    .replace(/&#0?39;/g, "'")
    .replace(/&apos;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&');
}

// Expose globally for dashboardHandler.js (IIFE, non-module)
window.openDashboardPanel   = openDashboardPanel;
window.closeDashboardPanel  = closeDashboardPanel;
window.addTileFromComponent  = addTileFromComponent;
window.isDashboardPanelActive = isDashboardPanelActive;
