/**
 * Turn budget bar — the Visible Budget (S5).
 *
 * A slim, honest budget readout in the Live Status header: one segment per declared unit
 * (steps · tokens · seconds), each a real progressbar (role="progressbar", aria-valuetext in
 * WORDS — "2 of 4 steps used"), filled live from the `turn_budget` notice the backend pushes
 * at every tool boundary and LLM call, and restored on a plan reload from the persisted
 * `budget` stamp. It never renders without an armed budget: a bar that is visible always
 * means this turn is spending against a declared limit.
 *
 * Colour = attention, never a verdict: quiet until 80% (amber, "nearly used"), red only once
 * a unit is EXHAUSTED — and the label then says how the turn ended (gracefully / killed), a
 * word, since colour alone carries nothing. Theme tokens only; no motion (a width change is
 * not animated, so `prefers-reduced-motion` has nothing to reduce).
 *
 * Absence-tolerant: no host element on the page ⇒ nothing here ever runs.
 */
(function () {
    'use strict';
    const HOST_ID = 'turn-budget-bar';
    const NEAR = 0.75;   // = turn_budget.NEAR_SHARE: the bar says "nearly used" exactly when the model is told so
    let _state = null;       // last known budget state {units, exhausted, graceful, killed, session_id, finish?}
    let _forSession = null;
    let _timer = null;       // ticks the seconds unit while a turn runs

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }
    function host() { return document.getElementById(HOST_ID); }

    function _fmt(unit, n) {
        n = Number(n) || 0;
        if (unit === 'tokens') return n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(Math.round(n));
        if (unit === 'seconds') return `${Math.round(n)}s`;
        return String(Math.round(n));
    }

    // The words a screen reader hears — and the tooltip shows: never a bare percentage.
    function _words(u) {
        const used = _fmt(u.unit, u.used), total = _fmt(u.unit, u.total);
        const noun = u.unit === 'seconds' ? '' : ` ${u.unit}`;
        if (u.share >= 1) return `${u.unit} budget used up: ${used} of ${total}${noun}`;
        if (u.share >= NEAR) return `${u.unit} nearly used: ${used} of ${total}${noun}`;
        return `${used} of ${total}${noun} used`;
    }

    function _level(u, st) {
        if (u.share >= 1 || (st && st.exhausted === u.unit)) return 'exhausted';
        if (u.share >= NEAR) return 'near';
        return 'ok';
    }

    function _ending(st) {
        if (!st || !st.exhausted) return '';
        if (st.killed) return `budget exhausted (${esc(st.exhausted)}) — the turn was stopped by a hard limit`;
        return `budget exhausted (${esc(st.exhausted)}) — finished gracefully`;
    }

    function render() {
        const h = host();
        if (!h) return;
        const st = _state;
        if (!st || !Array.isArray(st.units) || !st.units.length) {
            if (!h.hidden) { h.hidden = true; h.innerHTML = ''; }
            if (_timer) { clearInterval(_timer); _timer = null; }
            return;
        }
        // seconds keep ticking on the viewer's clock while the turn runs (no finish yet)
        const now = Date.now() / 1000;
        const units = st.units.map(u => {
            if (u.unit === 'seconds' && !st.finish && st.started_at) {
                const used = Math.max(Number(u.used) || 0, now - Number(st.started_at));
                return Object.assign({}, u, { used, share: u.total ? Math.min(1, used / u.total) : 0 });
            }
            return u;
        });
        const segs = units.map(u => {
            const lvl = _level(u, st);
            const pct = Math.round(Math.min(1, Math.max(0, Number(u.share) || 0)) * 100);
            const words = _words(u);
            return `<div class="tbb-seg tbb-${lvl}" role="progressbar" aria-label="${esc(u.unit)} budget"
                        aria-valuemin="0" aria-valuemax="${esc(_fmt(u.unit, u.total))}" aria-valuenow="${esc(_fmt(u.unit, u.used))}"
                        aria-valuetext="${esc(words)}" data-tip="${esc(words)}" tabindex="0">
                        <span class="tbb-label">${esc(u.unit)}</span>
                        <span class="tbb-track" aria-hidden="true"><span class="tbb-fill" style="width:${pct}%"></span></span>
                        <span class="tbb-num" aria-hidden="true">${esc(_fmt(u.unit, u.used))}<span class="tbb-of">/${esc(_fmt(u.unit, u.total))}</span></span>
                    </div>`;
        }).join('');
        const ending = _ending(st);
        h.innerHTML = `<div class="tbb-row"><span class="tbb-title">Turn budget</span>${segs}</div>` +
            (ending ? `<div class="tbb-ending tbb-${st.killed ? 'killed' : 'graceful'}" role="status">${ending}</div>` : '');
        h.hidden = false;
        const running = !st.finish && units.some(u => u.unit === 'seconds');
        if (running && !_timer) _timer = setInterval(render, 1000);
        if (!running && _timer) { clearInterval(_timer); _timer = null; }
    }

    /** A live `turn_budget` notice (state + session_id) or a reload stamp. */
    function update(st, sessionId) {
        if (!st || typeof st !== 'object') return;
        const sid = sessionId || st.session_id || null;
        // session-guarded: the bar belongs to the viewed session only
        const viewed = (window.state && window.state.currentSessionId) || null;
        if (sid && viewed && sid !== viewed) return;
        _state = Object.assign({}, st);
        _forSession = sid;
        render();
    }

    /** A new turn starts (or a session is switched): forget the previous reading. */
    function reset() {
        _state = null; _forSession = null; render();
    }

    window.BudgetBar = { update, reset, render, current: () => _state, forSession: () => _forSession };
})();
