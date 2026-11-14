/**
 * Dashboard Handler — JS
 *
 * Closes and restores the dashboard split panel on a session switch (called from
 * sessionManagement.js).
 *
 * "Pin to dashboard" is NOT injected here any more: it is the footer act of an answer
 * row — a data row's table and every component whose manifest declares
 * `answer_surface.pin` (chart, knowledge graph, flow, EXPLAIN plan) — drawn by the
 * server and revealed/handled in ui.js (_wirePinActs). The floating hover button this
 * module used to inject over every card is gone: it covered the card's own controls
 * and could not be found without hovering.
 *
 * Follows the same IIFE pattern as splitPanelResizer.js — no build step.
 */

(function () {
  'use strict';

  // ── Close panel on session switch ─────────────────────────────────────────
  /**
   * Called by sessionManagement.js before switching sessions.
   * Closes the dashboard panel and saves state to localStorage.
   */
  window.closeDashboardSplitPanelForSession = function (sessionId) {
    const panel = document.getElementById('dashboard-split-panel');
    if (!panel || !panel.classList.contains('dash-split--open')) return;

    // Save which dashboard was open for this session
    if (window._currentDashboardId) {
      try {
        localStorage.setItem(
          `dashboard_state_${sessionId}`,
          JSON.stringify({ dashboard_id: window._currentDashboardId })
        );
      } catch (_) {}
    }

    if (typeof window.closeDashboardPanel === 'function') {
      window.closeDashboardPanel();
    } else {
      panel.classList.remove('dash-split--open');
      setTimeout(() => { panel.style.display = 'none'; }, 350);
    }
  };

  /**
   * Restore dashboard state when switching back to a session.
   */
  window.restoreDashboardStateForSession = function (sessionId) {
    try {
      const saved = localStorage.getItem(`dashboard_state_${sessionId}`);
      if (!saved) return;
      const { dashboard_id } = JSON.parse(saved);
      if (!dashboard_id) return;

      import('/api/v1/components/dashboard/renderer')
        .then(({ openDashboardPanel }) => {
          openDashboardPanel({ dashboard_id });
        })
        .catch(() => {});
    } catch (_) {}
  };

})();
