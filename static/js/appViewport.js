/**
 * appViewport — the app shell is the visible area, always.
 *
 * The app is one fixed box (main.css: body). On an iPad the keyboard does not resize the page:
 * WebKit only shrinks its visible area (visualViewport) — for the on-screen keyboard, and for
 * the strip it reserves for the collapsed shortcut bar of a hardware keyboard (~70 px, the ◞
 * glyph) — and then PANS that visible area to reveal a focused field the keyboard would cover,
 * and lets a finger drag it over the room it reserved. A shell sized to the page is cut by the
 * keyboard, is scrolled up by the browser, and shows the document behind it (a white band).
 *
 * So the shell is the visible area, every time it changes, with no threshold:
 *
 *   --app-h    while a field is focused the visible height (the space above whatever covers the
 *              bottom), else the whole window
 *   --app-top  where the visible area starts inside the window
 *
 * and a pan of the visible area is put back (window.scrollTo(0, 0)): with the composer inside
 * the shell, above the keyboard, there is nothing under it for the browser to reveal; a drag
 * into the reserved room is undone at the next frame. A pinch-zoom is the user's view: the
 * variables are cleared and the pan is theirs. Without a visualViewport nothing is set and
 * the shell is 100% of the window.
 *
 * History: a threshold (follow only an on-screen keyboard, ≥150 px) left the shortcut-bar strip
 * to the browser — which panned the page up by exactly that strip and showed white beneath.
 * A single put-back per event ran during the keyboard's closing animation, where WebKit
 * overrides it; Chrome on iPad then left the conversation moved up after the keyboard closed.
 * Every event now re-applies until the animation is over (settle), and a touch re-checks.
 * A field on a modal (fixed to the window, not the shell) keeps the browser's pan while the
 * keyboard would cover it — putting that pan back hid the field being typed into.
 * Chrome on iPad keeps reporting the reduced visible height after the keyboard is gone (even
 * across reloads): with no field focused the shell is now the whole window whatever it reports.
 * Chrome also keeps the field focused when the keyboard is hidden; that close lets go of the field
 * (as Safari does). A full-height shell under the ◞ strip while focused was tried and reverted:
 * Chrome panned the whole page up by the strip to reveal the field.
 *
 * Plain script, no exports, loaded once in <head> (it writes only to <html>).
 */
(function () {
    'use strict';
    const vv = typeof window !== 'undefined' && window.visualViewport;
    if (!vv) return;
    const html = document.documentElement;
    const root = html.style;

    function clear() { root.removeProperty('--app-h'); root.removeProperty('--app-top'); }

    // Larger than the ◞ strip (~55–80 px), smaller than any on-screen keyboard (≥ ~260 px).
    const STRIP_MAX_PX = 150;
    const HIDDEN_MS = 250;
    let keyboardUp = false, hiddenSince = 0;

    /** A field that brings up a keyboard has the focus. */
    function editableFocused() {
        const el = document.activeElement;
        if (!el || el === document.body) return false;
        if (el.isContentEditable) return true;
        const tag = el.tagName;
        if (tag === 'TEXTAREA') return !el.readOnly && !el.disabled;
        if (tag !== 'INPUT') return false;
        return !/^(checkbox|radio|range|color|file|button|submit|reset|image|hidden)$/i.test(el.type || '') && !el.readOnly && !el.disabled;
    }

    /** True while the focused field sits on a fixed overlay (not the shell) below the visible area:
     *  a fixed box does not move when the pan is put back, so putting it back would hide the field. */
    function overlayFieldNeedsPan() {
        const field = document.activeElement;
        if (!field || field === document.body || !field.getBoundingClientRect) return false;
        let el = field;
        while (el && el !== document.body && el !== html) {
            if (getComputedStyle(el).position === 'fixed') return field.getBoundingClientRect().bottom > vv.height + 1;
            el = el.parentElement;
        }
        return false;   // in the shell: the shell is the visible area, its scrollers reveal the field
    }

    let queued = 0;
    function apply() {
        queued = 0;
        if (vv.scale > 1.01) return clear();                         // pinch-zoomed: the user's view
        // A panned visible area (the browser revealing a focused field, a drag into the room it
        // reserved for the keyboard) is put back; the shell below is sized so nothing needs revealing.
        // Not while a field on an overlay needs it: a modal is fixed to the window, not to the shell,
        // so its lower fields are only above the keyboard while the browser keeps its pan — once the
        // keyboard closes the field fits and the pan is put back like any other.
        if ((vv.offsetTop > 0.5 || vv.offsetLeft > 0.5 || window.scrollY > 0.5 || window.scrollX > 0.5) && !overlayFieldNeedsPan()) {
            window.scrollTo(0, 0);
        }
        // While a field is focused the shell is the visible area (above the keyboard, or above the
        // strip iPadOS keeps for the ◞ button). With no field focused there is no keyboard, whatever
        // the browser still reports — Chrome on iPad keeps a reduced visible height after the keyboard
        // is gone, even across reloads in the tab: the shell is then the whole window (the layout
        // height, html.clientHeight, which the keyboard never changes). A shell kept full-height
        // under the strip while focused was panned up by Chrome to reveal the field — never that.
        const layoutH = html.clientHeight || 0;
        const offset = Math.max(0, Math.round(vv.offsetTop));
        const focused = editableFocused();
        const covered = layoutH ? layoutH - offset - vv.height : 0;
        // Hiding the on-screen keyboard is the end of typing: Safari lets go of the field, Chrome
        // keeps it focused under the ◞ strip. A real keyboard that closes while the field keeps the
        // focus lets go of it here, so the page returns to the whole window. A hardware keyboard
        // (only the strip, never a keyboard) is left alone.
        // The hidden state must last (HIDDEN_MS, seen by the settle series): a rotation with the
        // keyboard up passes through a frame that looks the same and must not close the keyboard.
        if (focused && covered > STRIP_MAX_PX) { keyboardUp = true; hiddenSince = 0; }
        else if (keyboardUp && focused) {
            const now = Date.now();
            if (!hiddenSince) hiddenSince = now;
            else if (now - hiddenSince >= HIDDEN_MS) { keyboardUp = false; hiddenSince = 0; document.activeElement.blur(); return settle(); }
        } else { keyboardUp = false; hiddenSince = 0; }
        const shellH = focused || !layoutH ? vv.height : layoutH - offset;
        const h = Math.round(shellH) + 'px';
        const top = offset + 'px';                                   // a pan that survives: the shell follows it
        if (root.getPropertyValue('--app-h') !== h) root.setProperty('--app-h', h);
        if (root.getPropertyValue('--app-top') !== top) root.setProperty('--app-top', top);
    }
    function schedule() { if (!queued) queued = requestAnimationFrame(apply); }

    // The keyboard animates for ~300–500 ms, and a put-back made during it is overridden: WebKit
    // restores its own scroll late, and in Chrome on iPad (a WKWebView) often not at all, leaving
    // the page moved up after the keyboard closed with no further event to correct it. So every
    // event re-applies again until the animation is surely over (each one restarts the series).
    const SETTLE_MS = [60, 180, 400, 750, 1200];
    let settling = [];
    function settle() {
        schedule();
        settling.forEach(clearTimeout);
        settling = SETTLE_MS.map(ms => setTimeout(schedule, ms));
    }

    vv.addEventListener('resize', settle);           // the keyboard or its bar opened/closed, a toolbar changed
    vv.addEventListener('scroll', settle);           // the browser or a finger panned the visible area
    window.addEventListener('orientationchange', settle);
    window.addEventListener('scroll', settle);       // the document itself moved (the keyboard's extra room)
    document.addEventListener('focusin', settle);    // a field took focus: the keyboard/bar is about to show
    document.addEventListener('focusout', settle);   // …or lost it: the keyboard is about to close
    // The keyboard's own hide key closes it with the field still focused (no focusout) — and a
    // touch after any missed close puts the page back at once.
    document.addEventListener('touchend', schedule, { passive: true });
    apply();
})();
