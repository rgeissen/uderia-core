/**
 * steerReattach.js — S10 Steer & Reattach, the client (a plain script, like turnLineage.js).
 *
 * STEER: while a turn runs the composer stays live; a sent steer is queued on the server
 * (`POST /v1/sessions/<id>/steer`), shown as a small italic bubble pinned under the running
 * answer, and — when the agent reads it with its next step — the `steer_consumed` step arrives
 * (notification channel), the pin says so and moves into the trace.
 *
 * REATTACH: the stream's frames are cursor-stamped (`id:`); this module keeps the cursor per
 * session. A stream that breaks before its final answer is resumed from the cursor
 * (`GET /v1/tasks/<id>/stream?since=`) and the reader sees `reconnected — caught up`; a session
 * opened while its turn still runs finds the task (`GET /v1/sessions/<id>/active-task`) and
 * backfills from cursor 0. The turn itself never stopped — only the view had.
 *
 * Absence-tolerant: with the switch off no task_start carries `reattach`, nothing here arms.
 */
(function () {
    'use strict';
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const _live = new Map();      // sessionId → { taskId, seq, delivered }
    const _pins = new Map();      // steerId → element
    const PLACEHOLDER_STEER = 'Steer the agent — considered at its next step';
    const MAX_RETRIES = 6;

    function enabled() { return _live.size > 0 || !!window.__steerReattachEnabled; }
    function tok() { return localStorage.getItem('tda_auth_token'); }
    function headers() { const t = tok(); return t ? { 'Authorization': 'Bearer ' + t } : {}; }
    function cur() { return window.__currentSessionId || (window.state && window.state.currentSessionId) || null; }

    /** The stream's task_start told us this turn is reattachable. */
    function onTaskStart(sid, payload) {
        if (!sid || !payload || !payload.task_id || !payload.reattach) return;
        window.__steerReattachEnabled = true;
        _live.set(sid, { taskId: payload.task_id, seq: 0, delivered: false });
        armComposer(sid);
    }
    /** Every cursor-stamped frame moves the session's cursor forward. */
    function cursor(sid, seq) {
        const rec = _live.get(sid);
        if (rec && Number.isInteger(seq) && seq > rec.seq) rec.seq = seq;
    }
    function delivered(sid) { const rec = _live.get(sid); if (rec) rec.delivered = true; }
    /** The tail's end said the turn is over (an error, a cancel — no final answer will come). */
    function markEnded(sid) { const rec = _live.get(sid); if (rec) rec.ended = true; }
    function ended(sid) { _live.delete(sid); disarmComposer(); }
    function liveTask(sid) { const rec = _live.get(sid); return rec ? { ...rec } : null; }

    // ── the composer in steer mode ─────────────────────────────────────────────────────────
    function armComposer(sid) {
        const inp = document.getElementById('user-input');
        const c = cur();
        if (!inp || (c && c !== sid)) return;
        inp.disabled = false;
        if (!inp.dataset.steerPlaceholder) inp.dataset.steerPlaceholder = inp.placeholder || '';
        inp.placeholder = PLACEHOLDER_STEER;
        inp.setAttribute('aria-description', 'A turn is running. What you send now steers the agent and is considered at its next step.');
    }
    function disarmComposer() {
        const inp = document.getElementById('user-input');
        if (!inp) return;
        if (inp.dataset.steerPlaceholder !== undefined) { inp.placeholder = inp.dataset.steerPlaceholder; delete inp.dataset.steerPlaceholder; }
        inp.removeAttribute('aria-description');
    }
    /** True when a sent message should be a STEER (a turn is running, reattachable). */
    function shouldSteer(sid) { return !!(sid && _live.has(sid)); }

    function liveRegion() {
        let r = document.getElementById('steer-live');
        if (!r) {
            r = document.createElement('div'); r.setAttribute('id', 'steer-live'); r.setAttribute('aria-live', 'polite'); r.setAttribute('role', 'status');
            r.className = 'sr-only'; document.body.appendChild(r);
        }
        return r;
    }
    function announce(text) { try { liveRegion().textContent = text; } catch (_) { /* no-op */ } }

    /** Send a steer for the running turn; pins it under the running answer. */
    async function sendSteer(sid, text) {
        const t = String(text || '').trim();
        if (!sid || !t) return null;
        const r = await fetch(`/api/v1/sessions/${encodeURIComponent(sid)}/steer`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...headers() }, body: JSON.stringify({ text: t }) });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
        const st = data.steer || {};
        pin(sid, st.id, t);
        announce('Steer sent — considered at the agent\'s next step.');
        return st;
    }
    function pin(sid, id, text) {
        const c = cur();
        if (c && c !== sid) return null;   // another session on screen — never its log; an unknown one is not "another"
        const log = document.getElementById('chat-log');
        if (!log) return null;
        const el = document.createElement('div');
        el.className = 'steer-pin'; el.dataset.steerId = id || ''; el.setAttribute('role', 'note');
        el.innerHTML = `<span class="steer-pin-glyph" aria-hidden="true"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h13M11 6l6 6-6 6"/></svg></span><em class="steer-pin-text">${esc(text)}</em><span class="steer-pin-state">sent · considered at the next step</span>`;
        log.appendChild(el);
        if (id) _pins.set(id, el);
        try { el.scrollIntoView({ block: 'nearest' }); } catch (_) { /* no-op */ }
        return el;
    }
    /** The agent read the steer(s): the pin says so and moves into the trace. */
    function consumed(payload) {
        const p = payload || {};
        if (p.session_id && cur() && p.session_id !== cur()) return 0;
        const at = p.at === 'turn_start' ? 'at the start of this turn' : `with the result of ${p.at || 'the next step'}`;
        let n = 0;
        (p.steers || []).forEach(s => {
            let el = _pins.get(s.id);
            if (!el && s.id) {
                // the session UI cache restores the log from innerHTML — the element the map
                // held is gone, the pin is still on screen: find it by its steer id
                try { const log = document.getElementById('chat-log'); el = log ? [...log.querySelectorAll('.steer-pin')].find(p => p.dataset && p.dataset.steerId === String(s.id)) || null : null; } catch (_) { el = null; }
            }
            if (!el) return;
            el.classList.add('is-considered');
            const st = el.querySelector('.steer-pin-state');
            if (st) st.textContent = `considered ${at}`;
            n += 1;
            setTimeout(() => { try { el.classList.add('is-gone'); setTimeout(() => el.remove(), 400); } catch (_) { /* no-op */ } }, 4000);
            _pins.delete(s.id);
        });
        if (n) announce(`Steer considered ${at}.`);
        return n;
    }

    // ── reattach ─────────────────────────────────────────────────────────────────────────
    function note(text, fade) {
        const log = document.getElementById('chat-log');
        if (!log) return;
        const el = document.createElement('div'); el.className = 'reattach-note'; el.setAttribute('role', 'status');
        el.textContent = text; log.appendChild(el);
        if (fade) setTimeout(() => { try { el.classList.add('is-gone'); setTimeout(() => el.remove(), 600); } catch (_) { /* no-op */ } }, 2500);
        return el;
    }
    /** Resume a running turn's stream from a cursor; `process` is the app's stream reader. */
    async function reattach(sid, taskId, since, process, opts) {
        const o = opts || {};
        let attempt = 0, delay = 500, delivered = null, noted = false;
        while (attempt < MAX_RETRIES) {
            attempt += 1;
            try {
                const r = await fetch(`/api/v1/tasks/${encodeURIComponent(taskId)}/stream?since=${encodeURIComponent(since)}`, { headers: headers() });
                if (r.status === 404) { return { status: 'gone' }; }
                if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);
                if (!_live.has(sid)) _live.set(sid, { taskId, seq: since, delivered: false });
                if (!o.silent && !noted) { note('reconnected — caught up', true); noted = true; }   // once, not per attempt
                delivered = await process(r.body, sid);
                const rec = _live.get(sid);
                if (rec && rec.delivered) return { status: 'delivered', turn: delivered };
                // the tail's end said the turn is OVER without a final answer (an error, a
                // cancel): there is nothing more to fetch — never a retry storm on a dead turn
                if (rec && rec.ended) return { status: 'ended' };
                // the tail ended without a final answer (the turn is still running, the server
                // closed the tail, or a gap was declared) — try again from the new cursor
                since = rec ? rec.seq : since;
                if (o.gap) return { status: 'gap' };
                if (rec && rec.gap) return { status: 'gap' };
            } catch (e) {
                if (!o.silent) console.warn('[reattach] attempt', attempt, 'failed', e && e.message);
            }
            await new Promise(res => setTimeout(res, delay)); delay = Math.min(delay * 2, 8000);
        }
        return { status: 'failed' };
    }
    /** A session opened mid-turn: find its running task (null when none / switch off). */
    async function activeTask(sid) {
        try {
            const r = await fetch(`/api/v1/sessions/${encodeURIComponent(sid)}/active-task`, { headers: headers() });
            if (!r.ok) return null;
            const d = await r.json();
            return d && d.task_id ? { taskId: d.task_id, lastSeq: d.last_seq || 0 } : null;
        } catch (_) { return null; }
    }
    function markGap(sid) { const rec = _live.get(sid); if (rec) rec.gap = true; }

    window.SteerReattach = { onTaskStart, cursor, delivered, markEnded, ended, liveTask, shouldSteer, armComposer, disarmComposer, sendSteer, consumed, reattach, activeTask, markGap, note, PLACEHOLDER_STEER, _internals: { _live, _pins } };
})();
