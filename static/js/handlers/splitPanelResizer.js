/**
 * splitPanelResizer.js
 *
 * Horizontal resize handles between open split panels.
 *
 * All split panels (canvas, kg, scheduler, flow) are flex-row siblings.
 * When 2+ are visible this module injects a drag handle between each consecutive
 * pair so the user can adjust widths.  The chat / conversation panel (flex:1) fills
 * the remaining space automatically and is never given a width — wherever it sits
 * in the row (splitPanelReorder.js lets the user change the sequence).
 *
 * Self-contained IIFE — no build step, no dependencies.
 */
(function () {
  'use strict';

  // The conversation column (#conversation-panel — built at init by
  // _relocateConversationHeader) is always visible and is a panel like the others,
  // so a drag handle is injected between it and its neighbours (not just between
  // split panels themselves). It is leftmost by default; the VISUAL sequence is the
  // panels' flex `order` (set by splitPanelReorder.js), DOM position breaking ties.
  const CHAT_PANEL_ID = 'conversation-panel';
  const CHAT_CONTAINER_ID = 'chat-container'; // the scroll body inside the panel
  // A panel is STRUCTURAL: any `.sp-panel` that is a direct child of the split row
  // (the same selector polish.css frames — `#chat-canvas-split > .sp-panel`). No id
  // list: the Database Navigator was added to a hand-maintained list here and NOT to
  // panelFocus.js's twin, so it resized but never took the focus ring. A panel that
  // exists in the DOM is a panel here, and in panelFocus.js, by the same rule.
  const SPLIT_ID = 'chat-canvas-split';
  function _allPanels() {
    const split = document.getElementById(SPLIT_ID);
    return split ? Array.from(split.querySelectorAll(':scope > .sp-panel')) : [];
  }

  const HANDLE_CLASS = 'split-resize-handle';
  const MIN_PX       = 220;   // minimum panel width while dragging
  const CONV_MIN_PX  = 300;   // conversation panel floor (matches #conversation-panel min-width)
  const DEFAULT_PX   = 360;   // initial width when a second panel opens beside another
  const STORAGE_KEY  = 'uderia_split_widths_v3';

  let _isDragging = false;   // suppress MutationObserver rebuilds during a drag

  // ── CSS ─────────────────────────────────────────────────────────────────────

  function _injectCSS() {
    if (document.getElementById('split-resizer-css')) return;
    const s = document.createElement('style');
    s.id = 'split-resizer-css';
    s.textContent = `
/* ── Resize handle — "invisible until intent" ────────────────────────────────
   At rest the boundary is silent (the panels' own borders carry the seam).
   The hit target stays generous; a grip reveals on hover; the line lights up in
   the brand accent with a soft glow only while actively dragging. ──────────── */
.split-resize-handle {
  width: 10px;
  flex-shrink: 0;
  cursor: col-resize;
  background: transparent;
  position: relative;
  z-index: 20;
  user-select: none;
  -webkit-user-select: none;
}
/* Expanded invisible hit zone — easy to grab without pixel-hunting */
.split-resize-handle::before {
  content: '';
  position: absolute;
  top: 0; bottom: 0;
  left: -7px; right: -7px;
}
/* The visual element: a centred grip that grows out of nothing on hover,
   and becomes an accent-lit bar while dragging. */
.split-resize-handle::after {
  content: '';
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 3px;
  height: 36px;
  border-radius: var(--radius-full, 9999px);
  background: var(--text-muted, #64748b);
  opacity: 0;
  transition: opacity var(--duration-fast, 0.2s) var(--ease-out-expo, ease),
              height   var(--duration-fast, 0.2s) var(--ease-out-expo, ease),
              background var(--duration-fast, 0.2s) ease,
              box-shadow var(--duration-fast, 0.2s) ease;
  pointer-events: none;
}
.split-resize-handle:hover::after {
  opacity: 0.65;
}
.split-resize-handle.dragging::after {
  opacity: 1;
  height: 100%;
  width: 2px;
  border-radius: 1px;
  background: var(--teradata-orange, #F15F22);
  box-shadow: 0 0 10px var(--teradata-orange-glow, rgba(241,95,34,0.4));
}
/* Prevent text selection across the whole page during a drag */
body.split-dragging * {
  user-select: none !important;
  -webkit-user-select: none !important;
}
`;
    document.head.appendChild(s);
  }

  // ── Persistence ──────────────────────────────────────────────────────────────

  function _load() {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); } catch { return {}; }
  }
  function _save(id, px) {
    try {
      const w = _load();
      w[id] = Math.round(px);
      localStorage.setItem(STORAGE_KEY, JSON.stringify(w));
    } catch (_) {}
  }

  // ── Visible panel list ────────────────────────────────────────────────────────

  function _visiblePanels() {
    const els = _allPanels()
      .filter(el => {
        // chat-container has no inline display style; use computed style instead
        if (el.id === CHAT_PANEL_ID) return getComputedStyle(el).display !== 'none';
        // split panels are hidden via inline style="display:none"
        return el.style.display !== '' && el.style.display !== 'none';
      });
    // Order by VISUAL position — a handle must join ADJACENT panels. The row is a
    // flex row, so that is the `order` property first (unset = 0 for every panel =
    // the default sequence) and DOM position second, exactly as the browser lays out.
    els.sort((a, b) =>
      (_order(a) - _order(b)) ||
      ((a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) ? -1 : 1));
    return els;
  }

  function _order(el) { return parseInt(el.style.order, 10) || 0; }

  // ── Handle DOM management ─────────────────────────────────────────────────────

  function _removeHandles() {
    if (_isDragging) return;  // never tear down handles during an active drag
    document.querySelectorAll('.' + HANDLE_CLASS).forEach(h => h.remove());
  }

  function _updateHandles() {
    if (_isDragging) return;  // never touch handles during an active drag
    _removeHandles();
    const panels = _visiblePanels();
    // Solo state: when the conversation is the ONLY visible panel there's no
    // neighbour to frame against, so its base border + focus ring are dropped
    // (CSS: #chat-canvas-split.cc-solo > #conversation-panel).
    const split = document.getElementById('chat-canvas-split');
    if (split) split.classList.toggle('cc-solo',
      panels.length === 1 && panels[0] && panels[0].id === CHAT_PANEL_ID);
    if (panels.length < 2) return;

    for (let i = 1; i < panels.length; i++) {
      const handle = _makeHandle(panels[i - 1], panels[i]);
      // Same `order` as the panel to its right and ahead of it in the DOM, so the
      // handle is laid out immediately before that panel in any sequence.
      handle.style.order = panels[i].style.order || '';
      panels[i].parentNode.insertBefore(handle, panels[i]);
    }
  }

  function _makeHandle(leftPanel, rightPanel) {
    const handle = document.createElement('div');
    handle.className = HANDLE_CLASS;
    handle.setAttribute('role', 'separator');
    handle.title = 'Drag to resize';

    handle.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      _isDragging = true;

      // Snapshot widths BEFORE calling _toPx so we read offsetWidth while
      // flex layout is still intact.  chat-container has no inline width.
      const startLeft  = leftPanel.offsetWidth;
      const startRight = rightPanel.offsetWidth;
      const startX     = e.clientX;

      // Freeze split panels to explicit px; never touch the conversation's flex —
      // it is the row's filler on whichever side of the handle it sits.
      const leftIsChat  = leftPanel.id === CHAT_PANEL_ID;
      const rightIsChat = rightPanel.id === CHAT_PANEL_ID;
      if (!leftIsChat)  _toPx(leftPanel);
      if (!rightIsChat) _toPx(rightPanel);

      handle.classList.add('dragging');
      document.body.classList.add('split-dragging');

      // Disable CSS width transitions while dragging for instant feedback
      leftPanel.style.transition  = 'none';
      rightPanel.style.transition = 'none';

      function onMove(mv) {
        const dx = mv.clientX - startX;
        if (leftIsChat) {
          // Conversation is the flex filler — only the right panel takes an explicit
          // width. Clamp so the conversation never shrinks below its own floor; the
          // divider then stops cleanly instead of pushing the chat off-screen.
          const maxRight = startRight + Math.max(0, startLeft - CONV_MIN_PX);
          const newRight = Math.min(maxRight, Math.max(MIN_PX, startRight - dx));
          rightPanel.style.width = newRight + 'px';
        } else if (rightIsChat) {
          // The mirror case: the conversation sits to the RIGHT of the handle.
          const maxLeft = startLeft + Math.max(0, startRight - CONV_MIN_PX);
          const newLeft = Math.min(maxLeft, Math.max(MIN_PX, startLeft + dx));
          leftPanel.style.width = newLeft + 'px';
        } else {
          // Two fixed panels: conserve their COMBINED width so the divider just moves
          // between the two neighbours and no other panel shifts. Stop at either min.
          const total   = startLeft + startRight;
          const newLeft = Math.max(MIN_PX, Math.min(total - MIN_PX, startLeft + dx));
          leftPanel.style.width  = newLeft + 'px';
          rightPanel.style.width = (total - newLeft) + 'px';
        }
      }

      function onUp() {
        _isDragging = false;
        handle.classList.remove('dragging');
        document.body.classList.remove('split-dragging');
        document.removeEventListener('pointermove', onMove);
        document.removeEventListener('pointerup',   onUp);
        leftPanel.style.transition  = '';
        rightPanel.style.transition = '';
        if (!leftIsChat)  _save(leftPanel.id,  parseFloat(leftPanel.style.width));
        if (!rightIsChat) _save(rightPanel.id, parseFloat(rightPanel.style.width));
        // Rebuild handles now that drag is done (catches any deferred updates)
        _updateHandles();
      }

      // Attach to document — not handle — so they survive if the handle is
      // re-inserted by a MutationObserver callback during the drag.
      document.addEventListener('pointermove', onMove);
      document.addEventListener('pointerup',   onUp);
    });

    return handle;
  }

  // Convert a panel's current width (possibly %) to explicit px and remove constraints
  // that would interfere with arbitrary resizing.
  function _toPx(panel) {
    if (!panel.style.width || panel.style.width.endsWith('%')) {
      panel.style.width = panel.offsetWidth + 'px';
    }
    panel.style.flex     = 'none';
    panel.style.maxWidth = 'none';
    panel.style.minWidth = MIN_PX + 'px';
  }

  // ── Visibility tracking (detect newly opened panels) ─────────────────────────

  let _prevVisibleIds = new Set();
  // The handles depend on ONE thing: the visible panels' sequence (and their flex
  // `order`, which each handle copies). A style/class write that leaves that sequence
  // as it was — a hover class, a focus ring, a toolbar height, a class re-set to the
  // same value — must not rebuild them. Rebuilding on every attribute record removed
  // and re-inserted the handles into the row, which the sub-header scanner saw as a
  // childList change and answered with a class write, which this observer saw… — a
  // two-observer loop that ran as fast as the main thread allowed for as long as a
  // panel with a sub-header was open, restyling the whole row (a 500-node graph is
  // ~5,000 SVG elements) on every turn. Typing in the composer then waited on it.
  let _prevSequenceKey = '';
  const _sequenceKey = (panels) => panels.map(p => `${p.id}:${_order(p)}`).join('>');

  function _onAttributeChange() {
    const panels    = _visiblePanels();
    const key = _sequenceKey(panels);
    if (key === _prevSequenceKey) return;       // nothing a handle depends on changed
    _prevSequenceKey = key;
    const currentIds = new Set(panels.map(p => p.id));

    // Newly opened = visible now but not before; never treat chat-container as newly opened
    const newlyOpened = panels.filter(p => !_prevVisibleIds.has(p.id) && p.id !== CHAT_PANEL_ID);

    if (newlyOpened.length > 0 && panels.length >= 2) {
      const saved = _load();

      // Give newly opened split panels an explicit initial width if they don't have one
      newlyOpened.forEach(panel => {
        if (!panel.style.width || panel.style.width.endsWith('%')) {
          const savedPx = saved[panel.id];
          // A panel may declare its own opening width (`data-sp-default-width` on the aside —
          // Files opens wide enough for its list and detail side by side), clamped so the
          // conversation keeps its floor. A width the user dragged to always wins.
          const declared = parseInt((panel.dataset && panel.dataset.spDefaultWidth)
              || (typeof panel.getAttribute === 'function' && panel.getAttribute('data-sp-default-width')) || '', 10);
          const host = panel.parentElement;
          const rowW = host && typeof host.getBoundingClientRect === 'function' ? host.getBoundingClientRect().width : 0;
          const fit = declared && rowW ? Math.max(MIN_PX, Math.min(declared, rowW - CONV_MIN_PX - 24)) : DEFAULT_PX;
          panel.style.width    = (savedPx && savedPx >= MIN_PX ? savedPx : fit) + 'px';
          panel.style.flex     = 'none';
          panel.style.maxWidth = 'none';
          panel.style.minWidth = MIN_PX + 'px';
        }
      });

      // Freeze existing open split panels to their current rendered width so the
      // chat area doesn't jump when a neighbour panel appears.
      // Never freeze chat-container — it must stay flex:1.
      panels
        .filter(p => !newlyOpened.includes(p) && p.id !== CHAT_PANEL_ID)
        .forEach(panel => {
          if (!panel.style.width || panel.style.width.endsWith('%')) {
            _toPx(panel);
          }
        });
    }

    _prevVisibleIds = currentIds;
    _updateHandles();
  }

  // ── Restore saved widths on page load ────────────────────────────────────────

  function _restoreWidths() {
    const saved = _load();
    Object.entries(saved).forEach(([id, px]) => {
      const el = document.getElementById(id);
      if (el && px >= MIN_PX && el.style.display !== 'none') {
        el.style.width    = px + 'px';
        el.style.flex     = 'none';
        el.style.maxWidth = 'none';
        el.style.minWidth = MIN_PX + 'px';
      }
    });
  }

  // ── Conversation column as a peer panel ──────────────────────────────────────
  // Make the conversation a true peer of the component panels: wrap the chat in a
  // flex-column #conversation-panel and move the session title bar (#conversation-
  // header) to the top of it, so its header lines up on the same row as the
  // component panel headers. Reparenting preserves all event listeners; idempotent.

  function _relocateConversationHeader() {
    const split  = document.getElementById('chat-canvas-split');
    const chat   = document.getElementById(CHAT_CONTAINER_ID);
    const header = document.getElementById('conversation-header');
    if (!split || !chat) return;
    if (document.getElementById('conversation-panel')) return; // already done

    const panel = document.createElement('div');
    panel.id = 'conversation-panel';
    panel.className = 'conversation-panel sp-panel';
    split.insertBefore(panel, chat);     // wrapper takes the chat's place (leftmost)
    if (header) panel.appendChild(header); // header on top (becomes the panel's sp-header)
    panel.appendChild(chat);             // chat scroll-body below
  }

  // ── Initialise ───────────────────────────────────────────────────────────────

  function _init() {
    _relocateConversationHeader();
    _injectCSS();
    _restoreWidths();
    _updateHandles();

    // Watch each panel's style + class attributes for visibility changes
    const observer = new MutationObserver(_onAttributeChange);
    _allPanels().forEach(el => observer.observe(el, { attributes: true, attributeFilter: ['style', 'class'] }));
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _init);
  } else {
    _init();
  }

  // Public API — call manually if needed after programmatic panel changes
  // `visiblePanels` is THE answer to "which panels are showing, left to right" —
  // splitPanelReorder.js asks it rather than restating the visibility rule.
  window.SplitPanelResizer = { update: _updateHandles, visiblePanels: _visiblePanels };
})();
