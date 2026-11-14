/**
 * constructorMenu.js
 *
 * The "New" button in each Intelligence tab header (Planner Repositories, Knowledge
 * Repositories, Knowledge Graphs): a menu of that tab's constructors, so creating
 * something never requires scrolling past every existing instance to the constructor
 * cards at the bottom of the list.
 *
 * ONE source: the menu is read from the constructor CARDS the tab already renders
 * (templateSystem.js → `[data-template-id]` in the container the button names), and
 * choosing a row CLICKS that card's own Deploy button. There is no second template
 * list, no second filter and no second deploy path to drift — a constructor that gets
 * a card gets a menu row, and both open exactly the same dialog.
 *
 * Markup contract (templates/index.html):
 *   <button data-ctor-source="<id of the constructor-card container>">New</button>
 * Card contract (templateSystem.js:createTemplateCard):
 *   [data-template-id] · h3 (name) · p (description) · svg (icon) · .template-deploy-btn
 *
 * Also pins each tab's header row (`.intel-section-head`: title · Active only · Import ·
 * New) to the top of the scrolling pane, so the button stays reachable mid-list.
 *
 * Self-contained IIFE — no build step, no dependencies.
 */
(function () {
  'use strict';

  const ICON_FALLBACK = '<path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4"/>';

  // ── CSS ─────────────────────────────────────────────────────────────────────

  function _injectCSS() {
    if (document.getElementById('ctor-menu-css')) return;
    const s = document.createElement('style');
    s.id = 'ctor-menu-css';
    s.textContent = `
/* The tab's header row stays at the top of the scrolling pane. A sticky element
   stops at the pane's CONTENT edge, which would leave the pane's top padding (p-6 =
   1.5rem) as a strip the cards show through — so the header is pulled up over that
   padding (negative margin + negative top) and pads itself by the same amount: its
   title rests exactly where it always was, and pinned it does not move at all. */
.intel-section-head {
  position: sticky;
  top: -1.5rem;
  z-index: 20;
  margin: -1.5rem -8px 0.75rem;
  padding: 1.5rem 8px 0.75rem;
  background: var(--bg-primary, #141619);
}
/* Cards slide under a soft edge instead of a hard line (invisible at rest). */
.intel-section-head::after {
  content: '';
  position: absolute;
  left: 0; right: 0; top: 100%;
  height: 12px;
  pointer-events: none;
  background: linear-gradient(var(--bg-primary, #141619), transparent);
}
.ctor-new-btn > svg { width: 16px; height: 16px; flex-shrink: 0; }
.ctor-new-btn > .ctor-new-caret { width: 11px; height: 11px; opacity: 0.8; transition: transform 0.15s ease; }
.ctor-new-btn[aria-expanded="true"] > .ctor-new-caret { transform: rotate(180deg); }

/* The menu is portalled to <body> (position:fixed) so no pane or glass panel clips it. */
.ctor-menu {
  position: fixed;
  z-index: 1000;
  box-sizing: border-box;
  width: 380px;
  max-width: calc(100vw - 24px);
  max-height: min(70vh, 560px);
  overflow: hidden auto;
  padding: 6px;
  background: var(--bg-overlay, rgba(20, 22, 25, 0.98));
  border: 1px solid var(--border-primary, rgba(148, 163, 184, 0.18));
  border-radius: 10px;
  box-shadow: var(--shadow-xl, 0 20px 40px rgba(0, 0, 0, 0.5));
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  animation: ctor-menu-in 0.16s cubic-bezier(0.16, 1, 0.3, 1);
}
@keyframes ctor-menu-in {
  from { opacity: 0; transform: translateY(-8px) scale(0.97); }
  to   { opacity: 1; transform: translateY(0) scale(1); }
}
@media (prefers-reduced-motion: reduce) { .ctor-menu { animation: none; } }
.ctor-menu-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  width: 100%;
  padding: 9px 10px;
  text-align: left;
  font: inherit;
  color: var(--text-primary, #f8fafc);
  background: none;
  border: 1px solid transparent;
  border-radius: 8px;
  cursor: pointer;
}
.ctor-menu-item:hover:not(:disabled),
.ctor-menu-item:focus-visible {
  background: var(--hover-bg-strong, rgba(148, 163, 184, 0.12));
  border-color: var(--border-secondary, rgba(148, 163, 184, 0.1));
  outline: none;
}
.ctor-menu-item:focus-visible { border-color: var(--teradata-orange, #F15F22); }
.ctor-menu-item:disabled { opacity: 0.5; cursor: not-allowed; }
.ctor-menu-icon {
  display: inline-flex;
  flex-shrink: 0;
  margin-top: 1px;
  color: var(--text-muted, #94a3b8);
}
.ctor-menu-icon svg { width: 18px; height: 18px; }
.ctor-menu-item:hover:not(:disabled) .ctor-menu-icon,
.ctor-menu-item:focus-visible .ctor-menu-icon { color: var(--teradata-orange, #F15F22); }
.ctor-menu-text { min-width: 0; flex: 1; }
.ctor-menu-name { display: block; font-size: 0.84rem; font-weight: 600; line-height: 1.3; }
.ctor-menu-desc {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  margin-top: 2px;
  font-size: 0.75rem;
  line-height: 1.35;
  color: var(--text-muted, #94a3b8);
}
.ctor-menu-soon {
  flex-shrink: 0;
  margin-top: 1px;
  padding: 1px 6px;
  font-size: 0.65rem;
  font-weight: 600;
  color: var(--text-muted, #94a3b8);
  border: 1px dashed var(--border-primary, rgba(148, 163, 184, 0.18));
  border-radius: 9999px;
}
.ctor-menu-empty { padding: 12px 10px; font-size: 0.8rem; color: var(--text-muted, #94a3b8); }
.ctor-menu-foot {
  display: block;
  width: 100%;
  margin-top: 4px;
  padding: 8px 10px 6px;
  text-align: left;
  font: inherit;
  font-size: 0.74rem;
  color: var(--text-muted, #94a3b8);
  background: none;
  border: 0;
  border-top: 1px solid var(--border-secondary, rgba(148, 163, 184, 0.1));
  cursor: pointer;
}
.ctor-menu-foot:hover, .ctor-menu-foot:focus-visible { color: var(--text-primary, #f8fafc); outline: none; }
`;
    document.head.appendChild(s);
  }

  // ── Read the constructors from their cards ───────────────────────────────────

  function _constructors(source) {
    const container = document.getElementById(source);
    if (!container) return [];
    return Array.from(container.querySelectorAll('[data-template-id]')).map(card => {
      const deploy = card.querySelector('.template-deploy-btn');
      const name   = card.querySelector('h3');
      const desc   = card.querySelector('p');
      const icon   = card.querySelector('svg');
      return {
        id:   card.getAttribute('data-template-id'),
        name: (name && name.textContent.trim()) || card.getAttribute('data-template-id'),
        desc: (desc && desc.textContent.trim()) || '',
        icon: (icon && icon.innerHTML) || ICON_FALLBACK,
        // No Deploy button, or a disabled one ("Coming soon"): listed, not runnable.
        deploy: deploy && !deploy.disabled ? deploy : null,
      };
    });
  }

  // ── The menu ─────────────────────────────────────────────────────────────────

  let _open = null;   // { menu, btn }

  function _close(returnFocus) {
    const o = _open;
    if (!o) return;
    _open = null;
    o.menu.remove();
    o.btn.setAttribute('aria-expanded', 'false');
    if (returnFocus) o.btn.focus();
  }

  function _items(menu) {
    return Array.from(menu.querySelectorAll('button')).filter(b => !b.disabled);
  }

  function _show(btn) {
    const source = btn.getAttribute('data-ctor-source');
    const list   = _constructors(source);

    const menu = document.createElement('div');
    menu.className = 'ctor-menu';
    menu.setAttribute('role', 'menu');
    menu.setAttribute('aria-label', btn.getAttribute('aria-label') || 'New');

    if (!list.length) {
      const empty = document.createElement('div');
      empty.className = 'ctor-menu-empty';
      empty.textContent = 'No constructors are available here yet.';
      menu.appendChild(empty);
    }
    list.forEach(c => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'ctor-menu-item';
      row.setAttribute('role', 'menuitem');
      row.setAttribute('data-ctor-id', c.id);
      row.disabled = !c.deploy;

      const icon = document.createElement('span');
      icon.className = 'ctor-menu-icon';
      // The card's own icon markup (already in the page) — no second icon table.
      icon.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true">' + c.icon + '</svg>';
      const text = document.createElement('span');
      text.className = 'ctor-menu-text';
      const name = document.createElement('span');
      name.className = 'ctor-menu-name';
      name.textContent = c.name;
      text.appendChild(name);
      if (c.desc) {
        const desc = document.createElement('span');
        desc.className = 'ctor-menu-desc';
        desc.textContent = c.desc;
        text.appendChild(desc);
      }
      row.appendChild(icon);
      row.appendChild(text);
      if (!c.deploy) {
        const soon = document.createElement('span');
        soon.className = 'ctor-menu-soon';
        soon.textContent = 'Coming soon';
        row.appendChild(soon);
      }
      // The card's own Deploy button IS the act — same checks, same dialog.
      row.addEventListener('click', () => { if (!c.deploy) return; _close(false); c.deploy.click(); });
      menu.appendChild(row);
    });

    // The cards remain the catalogue: full descriptions and each constructor's defaults.
    const container = document.getElementById(source);
    if (container && list.length) {
      const foot = document.createElement('button');
      foot.type = 'button';
      foot.className = 'ctor-menu-foot';
      foot.setAttribute('role', 'menuitem');
      foot.textContent = 'Constructor details and defaults are at the end of this list';
      foot.addEventListener('click', () => {
        _close(false);
        if (container.scrollIntoView) container.scrollIntoView({ behavior: 'smooth', block: 'center' });
      });
      menu.appendChild(foot);
    }

    document.body.appendChild(menu);
    _place(menu, btn);
    btn.setAttribute('aria-expanded', 'true');
    _open = { menu, btn };
    const first = _items(menu)[0];
    if (first) first.focus();
  }

  // Under the button, right edges aligned, kept inside the viewport.
  // Both boxes are read at their LAYOUT size about their centre: the button is still
  // easing back from its press scale when the click lands, and the menu is mid entry
  // animation — a bounding rect taken now is a few pixels off for either.
  function _place(menu, btn) {
    const r  = btn.getBoundingClientRect();
    const vw = window.innerWidth || 1280;
    const w  = menu.offsetWidth || Math.min(380, vw - 24);
    const right  = (r.left + r.right) / 2 + (btn.offsetWidth  || r.width)  / 2;
    const bottom = (r.top + r.bottom) / 2 + (btn.offsetHeight || r.height) / 2;
    const left = Math.max(12, Math.min(right - w, vw - w - 12));
    menu.style.top  = Math.round(bottom + 6) + 'px';
    menu.style.left = Math.round(left) + 'px';
  }

  // ── Events (delegated — the buttons live in static markup) ───────────────────

  function _onClick(e) {
    const t = e.target;
    const btn = t && t.closest ? t.closest('[data-ctor-source]') : null;
    if (btn) {
      const wasOpenHere = _open && _open.btn === btn;
      _close(false);
      if (!wasOpenHere) _show(btn);
      return;
    }
    if (_open && !(t && t.closest && t.closest('.ctor-menu'))) _close(false);
  }

  function _onKeyDown(e) {
    if (!_open) return;
    if (e.key === 'Escape') { e.preventDefault(); _close(true); return; }
    if (e.key === 'Tab') { _close(false); return; }
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp' && e.key !== 'Home' && e.key !== 'End') return;
    const items = _items(_open.menu);
    if (!items.length) return;
    e.preventDefault();
    const i = items.indexOf(document.activeElement);
    const j = e.key === 'Home' ? 0
      : e.key === 'End' ? items.length - 1
      : e.key === 'ArrowDown' ? (i + 1) % items.length
      : (i <= 0 ? items.length - 1 : i - 1);
    items[j].focus();
  }

  // The menu is anchored to a button in a scrolling pane — if the page moves under
  // it, it would point at nothing. Scrolling INSIDE the menu is its own business.
  function _onScroll(e) {
    if (!_open) return;
    const t = e.target;
    if (t && t.closest && t.closest('.ctor-menu')) return;
    _close(false);
  }

  function _init() {
    _injectCSS();
    document.addEventListener('click', _onClick);
    document.addEventListener('keydown', _onKeyDown);
    document.addEventListener('scroll', _onScroll, true);
    if (window.addEventListener) window.addEventListener('resize', () => _close(false));
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _init);
  } else {
    _init();
  }

  window.ConstructorMenu = { close: () => _close(false), constructors: _constructors };
})();
