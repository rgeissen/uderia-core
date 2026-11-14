/**
 * viewportDiag — an on-device readout for a support report. No-op unless the page is opened
 * with ?scrolldiag=1; then a corner box shows what the browser's viewport is doing: the page's
 * scroll, the visible area (visualViewport) against the window and the app, and the most each
 * has moved. Built for the iPad case, where Safari's toolbars and keyboard cannot be emulated.
 *
 * Plain script, no exports, loaded once from index.html. Never takes a tap.
 */
(function () {
    'use strict';
    if (typeof document === 'undefined' || !/[?&]scrolldiag=1\b/.test(location.search)) return;

    function start() {
        const box = document.createElement('div');
        box.style.cssText = 'position:fixed;right:8px;bottom:8px;z-index:2147483647;pointer-events:none;font:11px/1.35 ui-monospace,monospace;'
            + 'white-space:pre;padding:6px 8px;border-radius:6px;background:rgba(0,0,0,.78);color:#fff;';
        document.body.appendChild(box);
        let maxPan = 0, maxScroll = 0;
        (function tick() {
            const vv = window.visualViewport || { offsetTop: 0, height: innerHeight, scale: 1 };
            const app = document.body.getBoundingClientRect();
            maxPan = Math.max(maxPan, vv.offsetTop);
            maxScroll = Math.max(maxScroll, window.scrollY, document.body.scrollTop);
            box.textContent = `page scrollY ${Math.round(window.scrollY)}   body ${document.body.scrollTop}\n`
                + `visible area: top ${Math.round(vv.offsetTop)}  h ${Math.round(vv.height)}  zoom ${vv.scale.toFixed(2)}\n`
                + `window h ${innerHeight}  page h ${document.documentElement.clientHeight}\n`
                + `app: top ${Math.round(app.top)}  h ${Math.round(app.height)}  position ${getComputedStyle(document.body).position}`
                + `  (--app-h ${document.documentElement.style.getPropertyValue('--app-h') || 'unset'})\n`
                + `covered below the visible area: ${Math.round(document.documentElement.clientHeight - vv.offsetTop - vv.height)} px\n`
                + `most moved: page ${Math.round(maxScroll)}  visible area ${Math.round(maxPan)}\n`
                + `focus: ${(a => a && a !== document.body ? a.tagName.toLowerCase() + (a.id ? '#' + a.id : '') : 'none')(document.activeElement)}`
                + `  touch points ${navigator.maxTouchPoints || 0}`;
            requestAnimationFrame(tick);
        })();
    }
    if (document.body) start(); else document.addEventListener('DOMContentLoaded', start);
})();
