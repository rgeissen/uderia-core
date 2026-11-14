/**
 * toolbarOverflow.js — Priority+ overflow for plain-DOM component toolbars.
 *
 * The flow toolbar has its own React implementation (ToolbarOverflow inside the
 * flow renderer). This is the framework-agnostic DOM equivalent for toolbars
 * built with document.createElement — e.g. the knowledge-graph toolbar (and,
 * later, the shared .sp-header).
 *
 * Behaviour: when the toolbar runs out of width, the lowest-priority items
 * collapse (right-to-left) into a "⋯ More" popover; they flow back when space
 * returns. Real DOM nodes are MOVED (their event listeners stay intact). Pinned
 * items never overflow. Measurement is by child-width summation (no
 * overflow:hidden), so inline dropdowns inside the toolbar are never clipped.
 *
 * Usage:
 *   window.PanelToolbarOverflow.attach(toolbarEl, {
 *     pinned: '.kg-toolbar-search',   // selector: never overflow
 *     ignore: '.kg-toolbar-spacer',   // selector: skip (flex spacer)
 *     gap: 8,
 *   });
 *
 * Self-contained IIFE — no build step, no dependencies.
 */
(function () {
  'use strict';

  const MORE_ICON =
    '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' +
    '<circle cx="5" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="19" cy="12" r="1.7"/></svg>';

  function attach(toolbar, opts) {
    opts = opts || {};
    if (!toolbar || toolbar._pcOverflowAttached) return null;
    toolbar._pcOverflowAttached = true;

    const pinnedSel = opts.pinned || '';
    const ignoreSel = opts.ignore || '';
    const gap = typeof opts.gap === 'number' ? opts.gap : 8;

    toolbar.style.flexWrap = 'nowrap';
    toolbar.style.minWidth = '0';

    // ── "⋯ More" button ──────────────────────────────────────────────────────
    const moreBtn = document.createElement('button');
    moreBtn.type = 'button';
    moreBtn.className = 'pc-btn pc-btn--icon pc-overflow-btn';
    moreBtn.title = 'More toolbar actions';
    moreBtn.setAttribute('aria-haspopup', 'menu');
    moreBtn.setAttribute('aria-expanded', 'false');
    moreBtn.innerHTML = MORE_ICON;
    moreBtn.style.display = 'none';
    toolbar.appendChild(moreBtn);

    // ── Overflow menu (portal to body, position:fixed) ───────────────────────
    const menu = document.createElement('div');
    menu.className = 'pc-menu pc-overflow-menu';
    menu.style.position = 'fixed';
    menu.style.display = 'none';
    // Inherit the panel accent so menu highlights match the toolbar.
    const panel = toolbar.closest('.sp-panel');
    if (panel) menu.style.setProperty('--sp-accent', getComputedStyle(panel).getPropertyValue('--sp-accent'));
    document.body.appendChild(menu);

    let menuOpen = false;
    function positionMenu() {
      const r = moreBtn.getBoundingClientRect();
      menu.style.top = (r.bottom + 6) + 'px';
      menu.style.right = Math.max(8, window.innerWidth - r.right) + 'px';
      menu.style.left = 'auto';
    }
    function openMenu() { menuOpen = true; moreBtn.setAttribute('aria-expanded', 'true'); positionMenu(); menu.style.display = 'flex'; }
    function closeMenu() { menuOpen = false; moreBtn.setAttribute('aria-expanded', 'false'); menu.style.display = 'none'; }
    moreBtn.addEventListener('click', (e) => { e.stopPropagation(); menuOpen ? closeMenu() : openMenu(); });
    document.addEventListener('mousedown', (e) => {
      if (menuOpen && !menu.contains(e.target) && !moreBtn.contains(e.target)) closeMenu();
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && menuOpen) closeMenu(); });

    // Ordered snapshot of the flexible items (everything that may overflow).
    const original = Array.from(toolbar.children).filter(c =>
      c !== moreBtn &&
      !(pinnedSel && c.matches && c.matches(pinnedSel)) &&
      !(ignoreSel && c.matches && c.matches(ignoreSel))
    );
    const stashed = []; // items currently in the menu, in original (left→right) order

    function usedWidth() {
      const kids = Array.from(toolbar.children).filter(c =>
        c.offsetParent !== null && !(ignoreSel && c.matches && c.matches(ignoreSel)));
      let w = 0;
      kids.forEach(c => { w += c.offsetWidth; });
      return w + gap * Math.max(0, kids.length - 1);
    }

    function measure() {
      if (!toolbar.clientWidth) return; // not laid out yet (panel hidden)
      const avail = toolbar.clientWidth;

      // Grow back: try restoring stashed items (leftmost-collapsed first).
      while (stashed.length) {
        const item = stashed[0];
        toolbar.insertBefore(item, moreBtn);
        item.classList.remove('pc-overflow-item');
        if (usedWidth() > avail) {            // didn't fit — put it back and stop
          menu.insertBefore(item, menu.firstChild);
          item.classList.add('pc-overflow-item');
          break;
        }
        stashed.shift();
      }

      // Shrink: move the right-most in-bar flexible item into the menu until it fits.
      let guard = 0;
      while (usedWidth() > avail && guard++ < 60) {
        const inBar = original.filter(it => it.parentNode === toolbar);
        if (!inBar.length) break;
        const item = inBar[inBar.length - 1];
        item.classList.add('pc-overflow-item');
        menu.insertBefore(item, menu.firstChild);
        stashed.unshift(item);
      }

      moreBtn.style.display = stashed.length ? 'inline-flex' : 'none';
      if (!stashed.length) closeMenu();
    }

    let raf = 0;
    const ro = (typeof ResizeObserver !== 'undefined')
      ? new ResizeObserver(() => { cancelAnimationFrame(raf); raf = requestAnimationFrame(measure); })
      : null;
    if (ro) ro.observe(toolbar);
    requestAnimationFrame(measure);

    const api = {
      measure,
      destroy() { if (ro) ro.disconnect(); cancelAnimationFrame(raf); menu.remove(); toolbar._pcOverflowAttached = false; },
    };
    toolbar._pcOverflow = api;
    return api;
  }

  window.PanelToolbarOverflow = { attach };
})();
