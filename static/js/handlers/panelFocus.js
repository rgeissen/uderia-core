/**
 * panelFocus.js
 *
 * Focus-follows-interaction for the workspace surfaces.
 *
 * The chat column and the right-side split panels (canvas, kg, scheduler, flow,
 * dashboard, memory, database navigator — any `.sp-panel` in the row) are co-equal, loosely-coupled surfaces — none owns another. There
 * is therefore no STRUCTURAL hierarchy. Instead we give the surface the user is
 * actually working in a transient ATTENTION emphasis: a crisp accent ring + lift
 * on the focused panel, with a gentle recede on the others' chrome. Whoever you
 * touch becomes momentarily loud, then yields it. (VS Code editor groups / Figma
 * panes model.)
 *
 * Pure emphasis — no width changes, no filters over the live flow canvas / charts.
 * The focused panel's ring uses its own --sp-accent (chat falls back to brand
 * orange), so this shares the exact visual language as the resize-handle accent.
 *
 * Self-contained IIFE — no build step, no dependencies.
 */
(function () {
  'use strict';

  const CONTAINER_ID = 'chat-canvas-split';
  const CHAT_PANEL_ID = 'conversation-panel'; // the chat column (built by splitPanelResizer)
  // A panel is STRUCTURAL: any `.sp-panel` that is a direct child of the split row —
  // the selector polish.css frames and rings (`#chat-canvas-split > .sp-panel`), and
  // the one splitPanelResizer.js resizes. No id list: the Database Navigator was
  // added to the resizer's list and not to a twin here, so it never took the ring.
  const PANEL_SEL = '#' + CONTAINER_ID + ' > .sp-panel';
  function _allPanels() {
    const c = document.getElementById(CONTAINER_ID);
    return c ? Array.from(c.querySelectorAll(':scope > .sp-panel')) : [];
  }

  const FOCUSED_CLASS = 'sp-focused';
  const HAS_FOCUS_CLASS = 'has-focused-panel';

  let _current = null;

  function _panelFromTarget(target) {
    if (!target || !target.closest) return null;
    // Never treat a resize handle drag as a panel focus.
    if (target.closest('.split-resize-handle')) return null;
    const el = target.closest(PANEL_SEL);
    // Only accept panels that are actually visible.
    if (!el) return null;
    if (el.id !== CHAT_PANEL_ID && (el.style.display === 'none' || el.style.display === '')) {
      return null;
    }
    return el;
  }

  function _setFocused(el) {
    if (!el || el === _current) return;
    _allPanels().forEach(p => p.classList.toggle(FOCUSED_CLASS, p === el));
    const container = document.getElementById(CONTAINER_ID);
    if (container) container.classList.add(HAS_FOCUS_CLASS);
    _current = el;
  }

  function _onInteraction(e) {
    const panel = _panelFromTarget(e.target);
    if (panel) _setFocused(panel);
  }

  function _init() {
    const container = document.getElementById(CONTAINER_ID);
    if (!container) return;
    // pointerdown catches clicks anywhere in a panel; focusin catches keyboard
    // tabbing into a panel's controls.
    container.addEventListener('pointerdown', _onInteraction, true);
    container.addEventListener('focusin', _onInteraction);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _init);
  } else {
    _init();
  }

  window.PanelFocus = {
    focus: (id) => { const el = document.getElementById(id); if (el) _setFocused(el); },
    clear: () => {
      _allPanels().forEach(p => p.classList.remove(FOCUSED_CLASS));
      document.getElementById(CONTAINER_ID)?.classList.remove(HAS_FOCUS_CLASS);
      _current = null;
    },
  };
})();
