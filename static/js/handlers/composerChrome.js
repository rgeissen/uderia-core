/**
 * The composer's three states (Oct 2026) — before · during · after.
 *
 *   setRunning(active, steering)  — the hairline + the ring around Stop, the steering tag and
 *                                   the shortcut line; called by UI.setExecutionState, the one
 *                                   place the running state is decided.
 *   showReceipt(sid, info)        — the turn's receipt row on top of the card once the answer
 *                                   settles: the answer's own verdict (read from its trust chip,
 *                                   never re-derived), the duration the turn reported, and the
 *                                   tokens and cost the Live Status counters hold — one source
 *                                   each. Hidden at the next keystroke, a new turn or a switch.
 *   refresh()                     — repaint the receipt's figures (post-turn costs arrive late).
 *   clear()                       — a session switch: the receipt belongs to the session it was
 *                                   drawn for, never to the one now on screen.
 *
 * Plain script; every entry point is fail-soft — the composer must work without it.
 */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const KBD = (k) => `<kbd>${k}</kbd>`;
    const HINT_IDLE = `${KBD('Enter')} send · ${KBD('Shift')}+${KBD('Enter')} new line · hold ${KBD('Alt')} for turn summaries`;
    const HINT_STEER = `${KBD('Enter')} sends a steer · Stop keeps what has arrived`;
    const HINT_RUN = 'Working · Stop keeps what has arrived';
    const STATES = ['pass', 'warn', 'danger', 'na'];

    const ICON_CHECK = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7"/></svg>';
    const ICON_SHIELD = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6l8-3z"/></svg>';
    const ICON_X = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';

    let _running = false;
    let _steering = false;
    let _receipt = null;   // { sid, turnId, durationMs, partial, bubble }

    function form() { return $('chat-form'); }

    // ── during ───────────────────────────────────────────────────────────────────
    function setRunning(active, steering) {
        _running = !!active;
        _steering = !!(active && steering);
        const f = form();
        if (!f) return;
        f.classList.toggle('is-running', _running);
        f.classList.toggle('is-steering', _steering);
        if (_running) { f.setAttribute('aria-busy', 'true'); hideReceipt(); }
        else f.removeAttribute('aria-busy');
        syncEmpty();   // the app clears the box from code at submit — no input event fires
        renderHint();
    }
    function renderHint() {
        const h = $('composer-hint');
        if (!h) return;
        h.innerHTML = _running ? (_steering ? HINT_STEER : HINT_RUN) : HINT_IDLE;
    }
    function syncEmpty() {
        const f = form(), i = $('user-input');
        if (f && i) f.classList.toggle('is-empty', !String(i.value || '').trim());
    }

    // ── after ────────────────────────────────────────────────────────────────────
    function fmtDuration(ms) {
        const n = Number(ms);
        if (!Number.isFinite(n) || n <= 0) return null;
        const s = n / 1000;
        if (s < 60) return `${s.toFixed(1)}s`;
        const m = Math.floor(s / 60);
        return `${m}m ${Math.round(s - m * 60)}s`;
    }
    function fmtTokens(n) {
        if (!Number.isFinite(n) || n <= 0) return null;
        if (n < 1000) return String(n);
        if (n < 1e6) return `${(n / 1000).toFixed(n < 100000 ? 1 : 0)}k`;
        return `${(n / 1e6).toFixed(1)}M`;
    }
    function readNumber(id) {
        const el = $(id);
        if (!el) return 0;
        const n = parseInt(String(el.textContent || '').replace(/[^\d]/g, ''), 10);
        return Number.isFinite(n) ? n : 0;
    }
    /** The answer's own trust chip — the one place the verdict is rendered. */
    function trustChip() {
        if (!_receipt) return null;
        let b = _receipt.bubble;
        if (!b || !b.isConnected) {
            const log = $('chat-log');
            const chips = log ? log.querySelectorAll('.la-trust') : [];
            return chips.length ? chips[chips.length - 1] : null;
        }
        return b.querySelector('.la-trust');
    }
    function readVerdict() {
        const chip = trustChip();
        if (!chip) return null;
        const w = chip.querySelector('.la-tw');
        const word = w ? String(w.textContent || '').trim() : '';
        if (!word) return null;
        const state = STATES.find((s) => chip.classList.contains('la-' + s)) || 'na';
        return { word, state };
    }
    function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

    function paintReceipt() {
        const r = $('composer-receipt');
        if (!r || !_receipt) return;
        const parts = [];
        const v = readVerdict();
        if (v) parts.push(`<span class="cr-verdict cr-${v.state}">${v.state === 'pass' ? ICON_CHECK : ICON_SHIELD}${esc(v.word)}</span>`);
        const figs = [];
        const d = fmtDuration(_receipt.durationMs);
        if (d) figs.push(`<span class="cr-fig">${_receipt.partial ? 'stopped after' : 'answered in'} <b>${d}</b></span>`);
        const tok = fmtTokens(readNumber('turn-input-tokens') + readNumber('turn-output-tokens'));
        if (tok) figs.push(`<span class="cr-fig"><b>${tok}</b> tokens</span>`);
        const acc = window.sessionCostAccumulator;
        const cost = acc && Number(acc.turn);
        if (_receipt.performed) figs.push(`<span class="cr-fig"><b>${_receipt.performed}</b> ${_receipt.performed === 1 ? 'action' : 'actions'} performed</span>`);
        if (_receipt.parked) figs.push(`<span class="cr-fig"><b>${_receipt.parked}</b> sent for review</span>`);
        if (Number.isFinite(cost) && cost > 0) figs.push(`<span class="cr-fig"><b>$${cost < 0.01 ? cost.toFixed(4) : cost.toFixed(3)}</b></span>`);
        if (!parts.length && !figs.length) { hideReceipt(); return; }
        if (parts.length && figs.length) parts.push('<span class="cr-sep" aria-hidden="true"></span>');
        parts.push(...figs);
        parts.push('<span class="cr-grow"></span>');
        if (trustChip()) parts.push('<button type="button" class="cr-open">Trust record</button>');
        parts.push(`<button type="button" class="cr-dismiss" aria-label="Dismiss the receipt">${ICON_X}</button>`);
        r.innerHTML = parts.join('');
        r.setAttribute('aria-label', 'Last turn receipt');
        r.hidden = false;
    }
    function showReceipt(sid, info) {
        const i = info || {};
        _receipt = { sid, turnId: i.turnId, durationMs: i.durationMs, partial: !!i.partial, bubble: i.bubble || null };
        // the trust chip is painted by the Living Answer's settle in the same tick — read it after
        setTimeout(() => { if (_receipt && _receipt.sid === sid) paintReceipt(); }, 0);
    }
    function hideReceipt() {
        const r = $('composer-receipt');
        if (r) { r.hidden = true; r.innerHTML = ''; }
        _receipt = null;
    }
    function refresh() {
        const r = $('composer-receipt');
        if (_receipt && r && !r.hidden) paintReceipt();
    }

    function onReceiptClick(e) {
        const open = e.target.closest('.cr-open');
        const dismiss = e.target.closest('.cr-dismiss');
        if (dismiss) { hideReceipt(); const i = $('user-input'); if (i) i.focus(); return; }
        if (open) {
            const chip = trustChip();
            if (!chip) return;
            try { chip.scrollIntoView({ block: 'center', behavior: 'smooth' }); } catch (_) { /* no-op */ }
            if (chip.getAttribute('aria-expanded') !== 'true') chip.click();
            chip.focus({ preventScroll: true });
        }
    }

    function init() {
        const i = $('user-input');
        if (i) {
            i.addEventListener('input', () => {
                syncEmpty();
                if (String(i.value || '').trim() && _receipt) hideReceipt();
            });
        }
        const r = $('composer-receipt');
        if (r) r.addEventListener('click', onReceiptClick);
        // the verdict can change after the settle (the seal, a late score): keep the row in step
        UderiaBus.on('uderia:answer-verdict', (ev) => {
            const sid = ev && ev.detail && ev.detail.sid;
            if (_receipt && (!sid || sid === _receipt.sid)) refresh();
        });
        // a confirmed act lands in the receipt window (PROP-008 §5a: "1 action performed")
        UderiaBus.on('uderia:action-performed', (ev) => {
            const d = (ev && ev.detail) || {};
            const sid = d.session_id || window.__currentSessionId;
            if (!_receipt || (sid && _receipt.sid && sid !== _receipt.sid)) {
                _receipt = { sid, turnId: d.turn_id, durationMs: null, partial: false, bubble: null };
            }
            const out = (d.record || {}).outcome;
            if (out === 'performed') _receipt.performed = (_receipt.performed || 0) + 1;
            else if (out === 'parked') _receipt.parked = (_receipt.parked || 0) + 1;
            paintReceipt();
        });
        syncEmpty();
        renderHint();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();

    window.Composer = { setRunning, showReceipt, hideReceipt, refresh, clear: hideReceipt, syncEmpty };
})();
