/*
 * Idle session lock.
 *
 * Signs the user out after TDA_SESSION_IDLE_MINUTES of no keyboard, pointer,
 * touch or scroll input (injected by the server as
 * window.UDERIA_SESSION_IDLE_MINUTES; 0 or absent = off). Activity is shared
 * across the user's open tabs through localStorage, so working in one tab keeps
 * the others signed in. A warning appears one minute before the lock with a
 * "Stay signed in" control.
 *
 * Background requests (polling, notification streams) do NOT count as activity:
 * that is why this lock lives in the browser. The server applies the same limit
 * to token use as a backstop for a tab that was closed (auth/security.py).
 */
(function () {
    'use strict';

    const minutes = Number(window.UDERIA_SESSION_IDLE_MINUTES || 0);
    if (!Number.isFinite(minutes) || minutes <= 0) return;

    const LIMIT_MS = minutes * 60 * 1000;
    const WARN_MS = Math.min(60 * 1000, Math.floor(LIMIT_MS / 2));
    const KEY = 'uderia_last_activity';
    const TICK_MS = 5000;
    let last = Date.now();
    let banner = null;
    let locking = false;

    function readShared() {
        try { return Number(localStorage.getItem(KEY)) || 0; } catch (e) { return 0; }
    }
    function writeShared(t) {
        try { localStorage.setItem(KEY, String(t)); } catch (e) { /* storage blocked: per-tab only */ }
    }
    function signedIn() {
        try { return !!localStorage.getItem('tda_auth_token'); } catch (e) { return false; }
    }

    let lastWrite = 0;
    function markActive() {
        if (locking) return;
        last = Date.now();
        if (last - lastWrite > 2000) { writeShared(last); lastWrite = last; }
        if (banner) hideBanner();
    }

    ['keydown', 'pointerdown', 'wheel', 'touchstart', 'scroll'].forEach(ev =>
        window.addEventListener(ev, markActive, { capture: true, passive: true }));
    let lastMove = 0;
    window.addEventListener('pointermove', () => {
        const now = Date.now();
        if (now - lastMove > 1000) { lastMove = now; markActive(); }
    }, { capture: true, passive: true });
    window.addEventListener('storage', e => {
        if (e.key === KEY && banner && Number(e.newValue) > last) { last = Number(e.newValue); hideBanner(); }
    });

    function showBanner(remainingMs) {
        if (!banner) {
            banner = document.createElement('div');
            banner.id = 'idle-lock-banner';
            banner.setAttribute('role', 'alertdialog');
            banner.setAttribute('aria-live', 'assertive');
            banner.setAttribute('aria-labelledby', 'idle-lock-text');
            banner.style.cssText = [
                'position:fixed', 'left:50%', 'bottom:calc(24px + env(safe-area-inset-bottom, 0px))',
                'transform:translateX(-50%)', 'z-index:100000', 'display:flex', 'gap:12px',
                'align-items:center', 'flex-wrap:wrap', 'max-width:calc(100vw - 32px)',
                'padding:12px 16px', 'border-radius:8px',
                'background:var(--card-bg, #1e293b)', 'color:var(--text-primary, #e2e8f0)',
                'border:1px solid var(--border-primary, #334155)',
                'box-shadow:0 8px 24px rgba(0,0,0,.25)', 'font-size:14px'
            ].join(';');
            banner.innerHTML =
                '<span id="idle-lock-text"></span>' +
                '<button type="button" id="idle-lock-stay" style="padding:6px 12px;border-radius:6px;' +
                'border:1px solid var(--teradata-orange, #F15F22);background:transparent;' +
                'color:var(--text-primary, #e2e8f0);cursor:pointer;font:inherit">Stay signed in</button>';
            document.body.appendChild(banner);
            banner.querySelector('#idle-lock-stay').addEventListener('click', markActive);
            banner.querySelector('#idle-lock-stay').focus({ preventScroll: true });
        }
        const s = Math.max(0, Math.ceil(remainingMs / 1000));
        banner.querySelector('#idle-lock-text').textContent =
            `You will be signed out in ${s} second${s === 1 ? '' : 's'} because the session has been idle.`;
    }
    function hideBanner() {
        if (banner) { banner.remove(); banner = null; }
    }

    async function lock() {
        locking = true;
        hideBanner();
        try {
            if (window.authClient && typeof window.authClient.logout === 'function') {
                await window.authClient.logout();
            } else {
                localStorage.removeItem('tda_auth_token');
            }
        } catch (e) { /* signed out locally regardless */ }
        window.location.href = '/login?reason=idle';
    }

    // The server keeps its own idle clock on AUTHENTICATED REQUESTS (auth_tokens.last_used_at),
    // the browser keeps this one on INPUT. A user who reads, scrolls and types for half an hour
    // without a request kept this clock alive while the server's ran out — the token was
    // revoked under an active tab (prod, 2026-10-20). So while the user is active, the lock
    // touches the server at most every HEARTBEAT_MS, and the two clocks read the same.
    const HEARTBEAT_MS = Math.max(60 * 1000, Math.min(5 * 60 * 1000, Math.floor(LIMIT_MS / 3)));
    let lastBeat = Date.now();
    function heartbeat() {
        lastBeat = Date.now();
        let tok = null;
        try { tok = localStorage.getItem('tda_auth_token'); } catch (e) { return; }
        if (!tok) return;
        fetch('/api/v1/auth/me', { headers: { 'Authorization': 'Bearer ' + tok }, cache: 'no-store' }).catch(() => {});
    }

    writeShared(last);
    setInterval(() => {
        if (locking || !signedIn()) return;
        const latest = Math.max(last, readShared());
        if (latest > last) last = latest;
        const idle = Date.now() - latest;
        if (idle < HEARTBEAT_MS && Date.now() - lastBeat >= HEARTBEAT_MS) heartbeat();
        if (idle >= LIMIT_MS) { lock(); return; }
        if (idle >= LIMIT_MS - WARN_MS) showBanner(LIMIT_MS - idle);
        else if (banner) hideBanner();
    }, TICK_MS);
})();
