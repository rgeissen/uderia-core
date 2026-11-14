/**
 * subHeaderAutohide.js
 *
 * Auto-hiding secondary toolbar ("sub-header") for component split panels.
 *
 * Behaviour (agreed design):
 *   - Collapsed by default — the secondary toolbar slides away so the canvas is clean.
 *   - Hovering the panel header (or the revealed toolbar) slides it open.
 *   - A pin button in the header locks it open; pinned state persists per panel.
 *
 * Applies to any .sp-panel that contains a [data-sp-subheader] element (KG, flow,
 * canvas). Dashboard / scheduler have no secondary toolbar, so they're exempt
 * automatically (no marker → no pin, no collapse).
 *
 * The collapse is driven entirely by classes on the .sp-panel, so it is immune to
 * the component re-rendering its body (plain-DOM or React). The pin button is
 * injected into the STATIC .sp-header-actions (defined in index.html), so it
 * survives renders too. Self-contained IIFE — no build step, no dependencies.
 */
(function () {
  'use strict';

  const PIN_KEY      = 'uderia_subheader_pinned_v1';
  const CLOSE_DELAY  = 300;   // ms grace before collapsing after the pointer leaves
  const ROOT_ID      = 'chat-canvas-split';

  const ICON_PIN =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round" stroke-linejoin="round"><path d="M9 4h6l-1 7 3 3v2H7v-2l3-3-1-7z"/>' +
    '<line x1="12" y1="16" x2="12" y2="21"/></svg>';

  const _closeTimers = new WeakMap();

  function _loadPins() {
    try { return JSON.parse(localStorage.getItem(PIN_KEY) || '{}'); } catch { return {}; }
  }
  function _savePin(id, pinned) {
    try {
      const p = _loadPins();
      if (pinned) p[id] = 1; else delete p[id];
      localStorage.setItem(PIN_KEY, JSON.stringify(p));
    } catch (_) {}
  }

  // Measure the toolbar's true height so the slide animates to the right size
  // (scrollHeight is reported even while max-height:0 clips it).
  function _measure(panel) {
    const sub = panel.querySelector('[data-sp-subheader]');
    if (sub) panel.style.setProperty('--sp-subheader-h', (sub.scrollHeight || 240) + 'px');
  }

  function _open(panel) {
    clearTimeout(_closeTimers.get(panel));
    _measure(panel);
    panel.classList.add('sp-subheader-open');
  }
  function _scheduleClose(panel) {
    clearTimeout(_closeTimers.get(panel));
    _closeTimers.set(panel, setTimeout(() => panel.classList.remove('sp-subheader-open'), CLOSE_DELAY));
  }

  // Wire a panel: mark it, restore pinned state, inject the pin button (once).
  function _wire(panel) {
    if (!panel.id) return;
    const hasSub = !!panel.querySelector('[data-sp-subheader]');
    if (!hasSub) return;                       // dashboard / scheduler: nothing to do
    // Idempotent wiring must not WRITE when there is nothing to change: adding a class
    // the panel already wears still re-sets the class attribute, and the resizer's
    // attribute observer answers every record (it rebuilt its handles, a childList
    // change this module's own observer then queued a scan for — a loop at full speed).
    if (!panel.classList.contains('sp-has-subheader')) panel.classList.add('sp-has-subheader');

    const actions = panel.querySelector('.sp-header .sp-header-actions');
    if (actions && !actions.querySelector('.sp-subheader-pin')) {
      const btn = document.createElement('button');
      btn.className = 'sp-action-btn sp-subheader-pin';
      btn.type = 'button';
      btn.innerHTML = ICON_PIN;
      const sync = () => {
        const pinned = panel.classList.contains('sp-subheader-pinned');
        btn.title = pinned ? 'Unpin toolbar' : 'Pin toolbar open';
        btn.setAttribute('aria-pressed', pinned ? 'true' : 'false');
      };
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const pinned = panel.classList.toggle('sp-subheader-pinned');
        if (pinned) _measure(panel);
        _savePin(panel.id, pinned);
        sync();
      });
      actions.insertBefore(btn, actions.firstChild);
      // Restore persisted pinned state.
      if (_loadPins()[panel.id]) { panel.classList.add('sp-subheader-pinned'); _measure(panel); }
      sync();
    }
  }

  // Hover via delegation on a stable root, so it survives body re-renders.
  function _inHeaderZone(node) {
    return node && node.closest && node.closest('.sp-header, [data-sp-subheader]');
  }
  function _onOver(e) {
    const panel = e.target.closest && e.target.closest('.sp-panel.sp-has-subheader');
    if (panel && _inHeaderZone(e.target)) _open(panel);
  }
  function _onOut(e) {
    const panel = e.target.closest && e.target.closest('.sp-panel.sp-has-subheader');
    if (panel && !_inHeaderZone(e.relatedTarget)) _scheduleClose(panel);
  }

  let _scanQueued = false;
  function _scan() {
    _scanQueued = false;
    document.querySelectorAll('.sp-panel').forEach(_wire);
  }
  function _queueScan() {
    if (_scanQueued) return;
    _scanQueued = true;
    requestAnimationFrame(_scan);
  }

  function _init() {
    const root = document.getElementById(ROOT_ID) || document.body;
    root.addEventListener('mouseover', _onOver);
    root.addEventListener('mouseout',  _onOut);
    _scan();
    // Re-wire when renderers create their sub-toolbars (idempotent + debounced).
    new MutationObserver(_queueScan).observe(root, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _init);
  else _init();
})();
