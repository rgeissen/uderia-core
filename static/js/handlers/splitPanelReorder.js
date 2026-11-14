/**
 * splitPanelReorder.js
 *
 * Drag a workspace panel by its header to change the SEQUENCE of the row.
 *
 * The conversation column and every component split panel (canvas, kg, scheduler,
 * dashboard, flow, memory, database navigator — any `#chat-canvas-split > .sp-panel`)
 * are co-equal peers, so any of them may sit anywhere in the row.
 *
 * The sequence is carried by the CSS `order` property, never by moving DOM nodes:
 * re-inserting a panel would reset the chat's scroll position, reload a canvas
 * preview iframe and re-mount live canvases. With `order` the DOM is untouched, so
 * nothing inside a panel can notice it moved. The default sequence sets NO `order`
 * at all (the row is byte-identical to a deployment without this module).
 *
 *   pointer:  press anywhere on a panel header, drag, release on the accent bar
 *             that marks the landing slot. Escape cancels. A press that does not
 *             travel is still an ordinary click on whatever was pressed.
 *   keyboard: Alt+Shift+Left / Right while focus is on a control in the header.
 *
 * Which panels are visible, and in which visual order, is asked of the resizer
 * (`SplitPanelResizer.visiblePanels()`) — one rule for both modules. The sequence
 * is a per-viewer convenience kept in localStorage; a hidden panel keeps its slot
 * and appears there when it is opened.
 *
 * Self-contained IIFE — no build step, no dependencies. Load AFTER splitPanelResizer.js.
 */
(function () {
  'use strict';

  const SPLIT_ID    = 'chat-canvas-split';
  const STORAGE_KEY = 'uderia_split_order_v1';
  const THRESHOLD   = 6;   // px the pointer must travel before a press becomes a drag
  // The whole header is the handle — its title and buttons included, as a browser
  // tab is both clickable and draggable: a press that does not travel stays the
  // control's click, one that travels is a drag (and its click is swallowed). Only a
  // text-entry field is exempt — there a press-and-move selects text.
  const TEXT_ENTRY = 'input, textarea, select, [contenteditable="true"]';

  // ── CSS ─────────────────────────────────────────────────────────────────────

  function _injectCSS() {
    if (document.getElementById('split-reorder-css')) return;
    const s = document.createElement('style');
    s.id = 'split-reorder-css';
    s.textContent = `
/* A header is a drag handle only while there is a neighbour to trade places with
   (.cc-solo = the conversation is the only visible panel). touch-action:none lets a
   finger drag the header instead of panning the row. */
#chat-canvas-split:not(.cc-solo) > .sp-panel > .sp-header {
  cursor: grab;
  touch-action: none;
}
body.sp-reordering, body.sp-reordering * {
  cursor: grabbing !important;
  user-select: none !important;
  -webkit-user-select: none !important;
}
.sp-panel.sp-reorder-source { opacity: 0.6; }
/* The chip that follows the pointer: the panel's name on its own accent. */
.sp-reorder-ghost {
  position: fixed;
  z-index: 10000;
  pointer-events: none;
  transform: translate(12px, 14px);
  max-width: 260px;
  padding: 6px 10px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.78rem;
  font-weight: 600;
  color: var(--text-primary, #f8fafc);
  background: var(--card-bg, #1f2126);
  border: 1px solid var(--border-primary, rgba(148, 163, 184, 0.18));
  border-left: 3px solid var(--sp-accent, var(--teradata-orange, #F15F22));
  border-radius: var(--radius-md, 8px);
  box-shadow: var(--shadow-lg, 0 10px 20px -5px rgba(0, 0, 0, 0.3));
}
/* The landing slot — the same accent bar the resize handle lights while dragging. */
.sp-reorder-indicator {
  position: fixed;
  z-index: 9999;
  pointer-events: none;
  width: 3px;
  transform: translateX(-1.5px);
  border-radius: 2px;
  background: var(--teradata-orange, #F15F22);
  box-shadow: 0 0 10px var(--teradata-orange-glow, rgba(241, 95, 34, 0.4));
}
`;
    document.head.appendChild(s);
  }

  // ── The sequence ─────────────────────────────────────────────────────────────

  function _split() { return document.getElementById(SPLIT_ID); }

  // Every panel of the row, in DOM order — the default sequence.
  function _domPanels() {
    const split = _split();
    return split ? Array.from(split.querySelectorAll(':scope > .sp-panel')) : [];
  }

  function _loadSaved() {
    try {
      const v = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
      return Array.isArray(v) ? v.filter(x => typeof x === 'string') : [];
    } catch (_) { return []; }
  }

  // The saved sequence, reconciled with the panels that exist now: an id that is
  // gone is dropped, a panel the saved sequence never knew is appended in DOM order.
  function _sequence() {
    const dom = _domPanels().map(p => p.id).filter(Boolean);
    const seq = [];
    _loadSaved().forEach(id => { if (dom.includes(id) && !seq.includes(id)) seq.push(id); });
    dom.forEach(id => { if (!seq.includes(id)) seq.push(id); });
    return seq;
  }

  // Paint a sequence onto the row and remember it. The default sequence paints
  // nothing and stores nothing.
  function _apply(seq) {
    const panels = _domPanels();
    const dom = panels.map(p => p.id);
    const isDefault = seq.length === dom.length && seq.every((id, i) => id === dom[i]);
    panels.forEach(p => { p.style.order = isDefault ? '' : String(seq.indexOf(p.id) + 1); });
    try {
      if (isDefault) localStorage.removeItem(STORAGE_KEY);
      else localStorage.setItem(STORAGE_KEY, JSON.stringify(seq));
    } catch (_) {}
    // The resize handles sit between visually adjacent panels — rebuild them.
    if (window.SplitPanelResizer) window.SplitPanelResizer.update();
  }

  // The visible panels in visual order — the resizer's rule, not a second one.
  function _visible() {
    const r = window.SplitPanelResizer;
    return r && r.visiblePanels ? r.visiblePanels() : [];
  }

  // Landing slot among the OTHER visible panels: the number of them whose
  // midpoint lies left of the pointer.
  function _dropSlot(others, x) {
    let k = 0;
    others.forEach(o => { if (x > (o.left + o.right) / 2) k++; });
    return k;
  }

  // Move `id` so it lands at slot `k` among the visible `others` (ids in visual
  // order). The two ends of the row are the ends of the WHOLE sequence — a panel
  // dragged to the far right stays rightmost when a hidden panel is opened later.
  // In between, hidden panels keep their place relative to the visible neighbours.
  function _moved(seq, id, others, k) {
    if (!others.length) return seq.slice();
    const out = seq.filter(x => x !== id);
    if (k <= 0) out.unshift(id);
    else if (k >= others.length) out.push(id);
    else out.splice(out.indexOf(others[k]), 0, id);
    return out;
  }

  function _panelName(panel) {
    const el = panel.querySelector('.sp-header .sp-switcher-name, .sp-header .sp-title');
    return (el && el.textContent.trim()) || 'Panel';
  }

  // ── Announcements (screen readers) ───────────────────────────────────────────

  function _announce(msg) {
    let live = document.getElementById('split-reorder-live');
    if (!live) {
      live = document.createElement('div');
      live.id = 'split-reorder-live';
      live.className = 'sr-only';
      live.setAttribute('aria-live', 'polite');
      document.body.appendChild(live);
    }
    live.textContent = msg;
  }

  function _announceMove(panel) {
    const vis = _visible();
    _announce(_panelName(panel) + ' moved to position ' + (vis.indexOf(panel) + 1) + ' of ' + vis.length);
  }

  // ── Which panel does this event want to move? ────────────────────────────────

  function _draggablePanel(target) {
    if (!target || !target.closest) return null;
    const header = target.closest('.sp-header');
    const panel = header && header.parentNode;
    if (!panel || !panel.classList || !panel.classList.contains('sp-panel')) return null;
    if (panel.parentNode !== _split()) return null;
    const vis = _visible();
    if (vis.length < 2 || !vis.includes(panel)) return null;
    // A fullscreen panel covers the row — there is no row to reorder behind it.
    if (vis.some(p => getComputedStyle(p).position === 'fixed')) return null;
    return panel;
  }

  // ── Pointer drag ─────────────────────────────────────────────────────────────

  let _drag = null;   // { panel, startX, startY, active, ghost, bar, slot }

  function _onPointerDown(e) {
    if (_drag || e.button || e.isPrimary === false) return;   // primary button / first touch only
    if (e.target.closest && e.target.closest(TEXT_ENTRY)) return;
    const panel = _draggablePanel(e.target);
    if (!panel) return;
    _drag = { panel, startX: e.clientX, startY: e.clientY, active: false, ghost: null, bar: null, slot: -1 };
    document.addEventListener('pointermove', _onPointerMove);
    document.addEventListener('pointerup', _onPointerUp);
    document.addEventListener('pointercancel', _cancel);
    document.addEventListener('keydown', _onDragKey, true);
  }

  function _begin() {
    const d = _drag;
    d.active = true;
    document.body.classList.add('sp-reordering');
    d.panel.classList.add('sp-reorder-source');
    const sel = window.getSelection && window.getSelection();
    if (sel && sel.removeAllRanges) sel.removeAllRanges();

    d.ghost = document.createElement('div');
    d.ghost.className = 'sp-reorder-ghost';
    d.ghost.textContent = _panelName(d.panel);
    const accent = getComputedStyle(d.panel).getPropertyValue('--sp-accent');
    if (accent && accent.trim()) d.ghost.style.setProperty('--sp-accent', accent.trim());
    document.body.appendChild(d.ghost);

    d.bar = document.createElement('div');
    d.bar.className = 'sp-reorder-indicator';
    d.bar.style.display = 'none';
    document.body.appendChild(d.bar);
  }

  function _onPointerMove(e) {
    const d = _drag;
    if (!d) return;
    if (!d.active) {
      if (Math.abs(e.clientX - d.startX) < THRESHOLD && Math.abs(e.clientY - d.startY) < THRESHOLD) return;
      _begin();
    }
    d.ghost.style.left = e.clientX + 'px';
    d.ghost.style.top  = e.clientY + 'px';

    const vis    = _visible();
    const others = vis.filter(p => p !== d.panel).map(p => p.getBoundingClientRect());
    const k      = _dropSlot(others, e.clientX);
    // Landing where it already sits is not a move — no bar, nothing to drop on.
    if (!others.length || k === vis.indexOf(d.panel)) {
      d.slot = -1;
      d.bar.style.display = 'none';
      return;
    }
    d.slot = k;
    const x = k === 0 ? others[0].left
      : k === others.length ? others[k - 1].right
      : (others[k - 1].right + others[k].left) / 2;
    const row = _split().getBoundingClientRect();
    d.bar.style.display = '';
    d.bar.style.left    = x + 'px';
    d.bar.style.top     = row.top + 'px';
    d.bar.style.height  = row.height + 'px';
  }

  function _onPointerUp() {
    const d = _drag;
    if (!d) return;
    const commit = d.active && d.slot >= 0;
    const others = commit ? _visible().filter(p => p !== d.panel).map(p => p.id) : [];
    _teardown();
    if (!d.active) return;          // a plain click on the header — not ours
    _swallowNextClick();            // the release of a drag must not also click
    if (commit) {
      _apply(_moved(_sequence(), d.panel.id, others, d.slot));
      _announceMove(d.panel);
    }
  }

  function _cancel() { if (_drag) _teardown(); }

  function _onDragKey(e) {
    if (e.key !== 'Escape' || !_drag || !_drag.active) return;
    e.preventDefault();
    e.stopPropagation();
    _swallowNextClick();
    _teardown();
  }

  function _teardown() {
    const d = _drag;
    _drag = null;
    document.removeEventListener('pointermove', _onPointerMove);
    document.removeEventListener('pointerup', _onPointerUp);
    document.removeEventListener('pointercancel', _cancel);
    document.removeEventListener('keydown', _onDragKey, true);
    if (!d) return;
    document.body.classList.remove('sp-reordering');
    d.panel.classList.remove('sp-reorder-source');
    if (d.ghost) d.ghost.remove();
    if (d.bar) d.bar.remove();
  }

  // A drag that started on a clickable part of the header (a switcher, the
  // click-to-rename session title) ends with a click event on it — eat exactly that one.
  function _swallowNextClick() {
    const eat = (ev) => { ev.stopPropagation(); ev.preventDefault(); };
    document.addEventListener('click', eat, true);
    setTimeout(() => document.removeEventListener('click', eat, true), 0);
  }

  // ── Keyboard ─────────────────────────────────────────────────────────────────

  function _move(panel, dir) {
    const vis = _visible();
    const i = vis.indexOf(panel);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= vis.length) return false;
    const others = vis.filter(p => p !== panel).map(p => p.id);
    _apply(_moved(_sequence(), panel.id, others, j));
    _announceMove(panel);
    return true;
  }

  function _onKeyDown(e) {
    if (!e.altKey || !e.shiftKey || (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight')) return;
    const t = e.target;
    // In a text field Alt+Shift+Arrow extends the selection by a word — leave it.
    if (t && t.closest && t.closest(TEXT_ENTRY)) return;
    const panel = _draggablePanel(t);
    if (!panel) return;
    e.preventDefault();
    _move(panel, e.key === 'ArrowLeft' ? -1 : 1);
  }

  // ── Initialise ───────────────────────────────────────────────────────────────

  function _init() {
    const split = _split();
    if (!split) return;
    _injectCSS();
    _apply(_sequence());
    split.addEventListener('pointerdown', _onPointerDown);
    split.addEventListener('keydown', _onKeyDown);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _init);
  } else {
    _init();
  }

  window.SplitPanelOrder = {
    get:   _sequence,
    set:   (seq) => {
      const known = _domPanels().map(p => p.id);
      const head  = (Array.isArray(seq) ? seq : []).filter((id, i, a) => known.includes(id) && a.indexOf(id) === i);
      _apply(head.concat(known.filter(id => !head.includes(id))));
    },
    move:  (id, dir) => { const el = document.getElementById(id); return el ? _move(el, dir < 0 ? -1 : 1) : false; },
    reset: () => _apply(_domPanels().map(p => p.id)),
  };
})();
