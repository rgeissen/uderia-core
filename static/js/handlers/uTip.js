/**
 * uTip — the viewport-clamped tooltip layer for the [data-tip] family
 * (.u-tip, .trust-mark, .tj-stage).
 *
 * The pure-CSS ::after tooltip is absolutely positioned INSIDE its host, so it is
 * clipped by any overflow ancestor (the Live Status panel scrolls) and by the
 * viewport's right edge — a tooltip that can be cut is not a tooltip. This layer
 * renders ONE fixed-position singleton on <body>, clamps it into the viewport
 * horizontally, and flips it above the target when there is no room below.
 *
 * The markup contract is unchanged: hosts keep `class="u-tip" data-tip="…"` (the
 * `.u-tip-left/right` alignment hints become no-ops — clamping supersedes them).
 * The CSS renderer is suppressed via `html.utip-js` so one hover can never grow
 * two tooltips; if this script is absent the CSS tooltip still works (absence-
 * tolerant, like every enhancer).
 *
 * Plain script, no exports, loaded once from index.html.
 */
(function () {
    'use strict';
    if (typeof document === 'undefined') return;   // gates execute consumers under Node

    const SEL = '.u-tip[data-tip], .trust-mark[data-tip], .tj-stage[data-tip]';
    const MARGIN = 8;      // minimum distance from every viewport edge
    const GAP = 7;         // distance from the target (mirrors the CSS tooltip)
    const DWELL_MS = 200;  // the same hover dwell the CSS tooltip used

    let tipEl = null, showTimer = null, current = null;

    function el() {
        if (tipEl && tipEl.isConnected) return tipEl;
        tipEl = document.createElement('div');
        tipEl.id = 'u-tip-layer';
        tipEl.setAttribute('role', 'tooltip');
        // Mirrors the .u-tip[data-tip]::after look (trust.css) — opaque, pre-line,
        // theme vars — so the move to the body layer is invisible except for the
        // clamping. pointer-events none: the tooltip must never steal the hover.
        // Above every modal: the Assurance Guide's backdrop sits at 100040 and its own
        // tooltip at 100050, so a layer at 99999 painted BEHIND the Guide — every lead's
        // definition was invisible there. A tooltip outranks whatever hosts it.
        tipEl.style.cssText = 'position:fixed;z-index:100060;max-width:272px;width:max-content;'
            + 'white-space:pre-line;text-align:left;font-size:0.73rem;line-height:1.5;'
            + 'color:var(--text-primary);background-color:var(--tip-bg,#23262c);'
            + 'border:1px solid var(--border-primary);border-radius:0.5rem;'
            + 'padding:0.5rem 0.65rem;box-shadow:0 8px 24px rgba(0,0,0,0.42);'
            + 'pointer-events:none;display:none;isolation:isolate;';
        document.body.appendChild(tipEl);
        return tipEl;
    }

    function show(target) {
        const text = target.getAttribute('data-tip');
        if (!text) return;
        const t = el();
        // Two tiers, so a tooltip that carries BOTH a consequence and its boundary reads
        // as one statement and one footnote rather than a wall of equal sentences. The
        // note is opt-in (`data-tip-note`); a plain tip renders exactly as before.
        const note = target.getAttribute('data-tip-note');
        t.textContent = '';                 // textContent throughout — never markup
        const lead = document.createElement('div');
        lead.textContent = text;
        t.appendChild(lead);
        if (note) {
            const n = document.createElement('div');
            n.className = 'u-tip-note';
            n.textContent = note;
            t.appendChild(n);
        }
        t.style.display = 'block';
        t.style.left = '0px'; t.style.top = '0px';   // reset before measuring
        const r = target.getBoundingClientRect();
        const tr = t.getBoundingClientRect();
        const vw = window.innerWidth, vh = window.innerHeight;
        // Centered on the target, CLAMPED into the viewport — the clamp that the
        // CSS tooltip structurally could not do.
        let x = r.left + r.width / 2 - tr.width / 2;
        x = Math.max(MARGIN, Math.min(x, vw - tr.width - MARGIN));
        // Below by default; ABOVE when the host asks for it (`u-tip-above`), because a
        // control with its own affordance underneath — the vote pill sits right on top
        // of "Why trust this answer?" — would otherwise cover the thing it sits above.
        // Either preference flips to the other side when there is no room for it.
        const wantAbove = target.classList && target.classList.contains('u-tip-above');
        let y = wantAbove ? r.top - tr.height - GAP : r.bottom + GAP;
        if (wantAbove) {
            if (y < MARGIN) y = r.bottom + GAP;
        } else if (y + tr.height > vh - MARGIN) {
            y = r.top - tr.height - GAP;
        }
        if (y < MARGIN) y = MARGIN;
        t.style.left = x.toFixed(0) + 'px';
        t.style.top = y.toFixed(0) + 'px';
        current = target;
    }

    function hide() {
        clearTimeout(showTimer); showTimer = null; current = null;
        if (tipEl) tipEl.style.display = 'none';
    }

    document.addEventListener('mouseover', (e) => {
        const target = e.target && e.target.closest && e.target.closest(SEL);
        if (!target || target === current) return;
        clearTimeout(showTimer);
        showTimer = setTimeout(() => show(target), DWELL_MS);
    }, true);
    document.addEventListener('mouseout', (e) => {
        const target = e.target && e.target.closest && e.target.closest(SEL);
        if (!target) return;
        if (e.relatedTarget && target.contains(e.relatedTarget)) return;
        hide();
    }, true);
    // Keyboard parity: focus shows immediately (a keyboard user has no dwell).
    document.addEventListener('focusin', (e) => {
        const target = e.target && e.target.closest && e.target.closest(SEL);
        if (target) show(target);
    }, true);
    document.addEventListener('focusout', hide, true);
    // A fixed-position tooltip does not follow a scrolling target — hide instead of drift.
    document.addEventListener('scroll', hide, true);
    document.addEventListener('click', hide, true);

    // Supersede the CSS renderer (trust.css keys off this class) — one tooltip, ever.
    document.documentElement.classList.add('utip-js');
})();
