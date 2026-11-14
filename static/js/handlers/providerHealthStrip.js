/**
 * Provider health strip — the honest breaker UI (S4, the Resilience Envelope).
 *
 * Shows a slim strip in the chat header ONLY while an LLM provider's circuit breaker is
 * open or half-open ("Anthropic is degraded — turns route to the fallback · recovers in 42s"),
 * with a live countdown; disappears on recovery. It never renders for a healthy estate, so a
 * strip that is visible always means something: a breaker is protecting turns right now.
 *
 * Sources: the live `provider_health` notice (a transition, pushed to the turn's owner) and a
 * re-read of GET /api/v1/llm/provider-health on open (so a reload never shows a stale strip).
 * Accessibility: `role="status"` + `aria-live="polite"` (a state change is announced once,
 * never interrupts), no motion — the countdown is text, and `prefers-reduced-motion` is
 * respected by having nothing to reduce. Theme tokens only; monochrome glyph.
 *
 * Absence-tolerant: no host element on the page ⇒ nothing here ever runs.
 */
(function () {
    'use strict';
    const HOST_ID = 'provider-health-strip';
    let _snaps = {};          // provider → snapshot
    let _timer = null;
    let _lastTold = '';

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }
    function host() { return document.getElementById(HOST_ID); }

    function _fmt(secs) {
        const s = Math.max(0, Math.round(Number(secs) || 0));
        if (s >= 90) return `${Math.ceil(s / 60)} min`;
        return `${s}s`;
    }

    // The reading per degraded provider — words from the breaker's own states.
    function _line(snap, now) {
        const p = esc(snap.provider || '?');
        if (snap.state === 'half_open') {
            return `<strong>${p}</strong> is recovering — its next call is a probe; turns route to the profile's signed fallback until that probe succeeds`;
        }
        const left = snap.recovers_in != null && snap.at != null
            ? Math.max(0, snap.recovers_in - (now - snap.at) / 1000)
            : snap.recovers_in;
        const why = snap.last_reason ? ` (${esc(snap.last_reason)})` : '';
        return `<strong>${p}</strong> is degraded${why} — turns route to the profile's signed fallback · probes again in ${_fmt(left)}`;
    }

    function render() {
        const h = host();
        if (!h) return;
        const now = Date.now();
        const degraded = Object.values(_snaps).filter(s => s && s.degraded);
        if (!degraded.length) {
            if (!h.hidden) { h.hidden = true; h.innerHTML = ''; }
            if (_timer) { clearInterval(_timer); _timer = null; }
            _lastTold = '';
            return;
        }
        const lines = degraded.map(s => `<span class="phs-line">${_line(s, now)}</span>`).join('');
        // announce a CHANGE once (the countdown is not a change worth announcing) — the
        // politeness is set BEFORE the mutation, or a screen reader sees the old value
        const told = degraded.map(s => `${s.provider}:${s.state}`).join('|');
        h.setAttribute('aria-live', told !== _lastTold ? 'polite' : 'off');
        _lastTold = told;
        h.innerHTML = `<span class="phs-glyph" aria-hidden="true">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
            </span><span class="phs-body">${lines}</span>`;
        h.hidden = false;
        if (!_timer) _timer = setInterval(render, 1000);
    }

    function update(snap) {
        if (!snap || !snap.provider) return;
        _snaps[snap.provider] = Object.assign({}, snap, { at: Date.now() });
        render();
    }

    async function refresh() {
        try {
            const tok = localStorage.getItem('tda_auth_token');
            const r = await fetch('/api/v1/llm/provider-health', { headers: tok ? { 'Authorization': 'Bearer ' + tok } : {} });
            if (!r.ok) return;
            const data = await r.json();
            const list = Array.isArray(data.providers) ? data.providers : [];
            _snaps = {};
            list.forEach(s => { if (s && s.provider) _snaps[s.provider] = Object.assign({}, s, { at: Date.now() }); });
            render();
        } catch (_) { /* a health read must never break the page */ }
    }

    window.ProviderHealthStrip = { update, refresh, render, _state: () => _snaps };
    if (host()) {
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', refresh);
        else refresh();
    }
})();
