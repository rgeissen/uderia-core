/**
 * The Living Answer — S1.1, reshaped (plain script, `window.LivingAnswer`).
 *
 * ONE bubble, ONE work line, ONE receipt, ONE verdict — live and after a reload alike.
 *
 *   BORN     at task_start: the work line reads what the agent is doing, in plain words, with the
 *            seconds and tokens so far; a hairline under it fills as the turn moves through its
 *            phases (context · retrieve · reason · draft · check · seal).
 *   WORKING  every curated step lands as a ROW of the receipt — a timeline in consumer words
 *            (Read the knowledge graph · 12 nodes · Queried Sales · 32 rows · 0.2s); a running
 *            row breathes, then SNAPS to its fact.
 *   FOLD     at the first token the rows converge into the work line, whose counters count up
 *            (Writing · 1 query · 12 nodes, then Done · … once settled); click reopens the receipt.
 *   WRITING  the answer streams beneath the line; only the new characters rise in.
 *   SETTLE   the line and the receipt move onto the authoritative message; the checks the
 *            platform ran on the draft land as the receipt's last rows (Checked against its
 *            sources · in scope); the ONE verdict chip lands on the action row beside "Why trust
 *            this answer?" — Verified · N findings · Not checked — never a bare "clean", and
 *            NEVER "verified" when no check ran (a turn nobody checked is not a clean turn).
 *   SEALED   the chip's tooltip says the turn is sealed and scored.
 *
 * RELOAD PARITY: `restore(bubble, turn)` rebuilds the SAME chrome from the persisted turn by
 * replaying its recorded events through the SAME reducer the live stream uses — a turn looks
 * one way whether you watched it or opened it later.
 *
 * Rules that hold: one clock (motion keys off event arrival, never timers); the data moves for
 * every event even off-screen — only painting is on-screen; the verdict is derived ONCE
 * (`verdictOf`) and rendered ONCE; consumer words at depth 0–2, operator words stay in Live
 * Status (depth 3, one click from any row).
 */
(function () {
    'use strict';
    if (window.LivingAnswer) return;

    const PHASES = ['context', 'retrieve', 'reason', 'draft', 'screen', 'seal'];
    const PHASE_STATUS = {
        context: 'Assembling the context…', retrieve: 'Retrieving…', reason: 'Reasoning…',
        draft: 'Drafting the answer…', screen: 'Checking the answer…', seal: 'Sealing the turn…',
    };
    const GATE_LABEL = { grounding: 'its sources', guardrail: 'personal data and harm', honesty: 'its claims', lineage: 'its figures', revision: 'a revision' };
    const _live = new Map();     // sessionId → turn state (data; the DOM is rebuilt from it)
    let _cur = null;

    function optedOut() { try { return localStorage.getItem('tda_living_answer') === '0'; } catch (_) { return false; } }
    function enabled() { return !optedOut() && !!window.__livingAnswerEnabled; }
    function setEnabled(on) { try { localStorage.setItem('tda_living_answer', on ? '1' : '0'); } catch (_) { /* no storage */ } }
    function cur() { return window.__currentSessionId || (window.state && window.state.currentSessionId) || null; }
    function onScreen(sid) { const c = cur(); return !c || c === sid; }
    function reduced() { try { return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches); } catch (_) { return false; } }
    function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
    function num(v) { const n = Number(v); return Number.isFinite(n) ? n : 0; }
    function fmtDur(ms) { const s = Math.max(0, num(ms)) / 1000; if (s < 60) return s.toFixed(1) + 's'; const m = Math.floor(s / 60); return `${m}m ${Math.round(s - m * 60)}s`; }
    function fmtTok(n) { n = Math.round(num(n)); return n >= 1000 ? (n / 1000).toFixed(1) + 'k tokens' : n + ' tokens'; }

    // ── the turn's data ─────────────────────────────────────────────────────────────────
    function fresh(sid) {
        return {
            sid, cls: null, tag: null, phase: 'context', t0: Date.now(), t1: null, tokens: 0, chips: [], folded: false,
            counts: { tools: 0, queries: 0, chunks: 0, entities: 0, experts: 0, steers: 0, corrections: 0, recoveries: 0, revisions: 0, widened: 0, askedDenied: 0 },
            findings: 0, gates: {}, settled: false, sealed: false, stopped: false, ended: false, partial: false, restored: false, turnId: null,
            el: null, clock: null, statusText: PHASE_STATUS.context, pendingTools: [], receipts: 0,
        };
    }
    function elapsed(st) { return (st.t1 != null ? st.t1 : Date.now()) - st.t0; }

    // ── arming: the stream's task_start says the turn streams ─────────────────────────────
    function onTaskStart(sid, payload) {
        if (!sid || !payload || !payload.stream) return;
        window.__livingAnswerEnabled = true;
        if (optedOut()) return;
        const st = fresh(sid);
        _live.set(sid, st);
        if (onScreen(sid)) mount(st);
        panelTurn('live:' + sid);   // the panel is about to show this running turn (its number is not known yet)
    }
    // ── which turn the Live Status panel shows: a row's key is an ordinal WITHIN its turn, so it
    // may only be matched against the panel that shows that turn; a reload declares the turn it
    // renders (eventHandlers), a live turn declares itself here and takes its number at settle ──
    function panelHost() { return document.getElementById('status-window-content'); }
    function panelTurn(v) { const h = panelHost(); if (!h) return; if (v === undefined) return h.dataset.laTurn || ''; h.dataset.laTurn = v == null ? '' : String(v); }
    /**
     * Load THIS turn's trace into the panel the way the reader does: the turn's avatar. The
     * delegated handler answers `.clickable-avatar[data-turn-id]`, which the app puts on the
     * USER avatar of the turn (the assistant's own avatar is a long-press replay), so the
     * avatar is found by the turn's number, else on the bubbles above this one.
     */
    function reloadOwnTurn(st) {
        const log = document.getElementById('chat-log');
        let av = (st.turnId != null && log) ? log.querySelector(`.clickable-avatar[data-turn-id="${String(st.turnId).replace(/"/g, '')}"]`) : null;
        for (let p = st.el ? st.el.previousElementSibling : null; !av && p; p = p.previousElementSibling) av = p.querySelector('.clickable-avatar[data-turn-id]');
        if (!av && st.el) av = st.el.querySelector('.clickable-avatar[data-turn-id]');
        if (av) { av.click(); return true; }
        return false;
    }
    /** the stamp is session-scoped: a stale number left by another session can never match */
    function turnStamp(sid, turnId) { return `${sid}:${turnId}`; }
    function panelShowsTurn(st) {
        const v = panelTurn();
        if (!v) return false;
        if (v === 'live:' + st.sid) return !st.ended;
        return st.turnId != null && v === turnStamp(st.sid, st.turnId);
    }
    function active(sid) { const st = _live.get(sid); return !!(st && !st.ended && !st.settled); }

    // ── the follow lock: submitting a question IS the intent to watch the answer form ──
    // The bubble grows in three moments the reader cannot scroll along with (the chips
    // of the working phase, the fold, the settle), then the text streams. ONE lock, armed
    // at birth, released only by the reader's own upward scroll (growth never fires a
    // scroll event, so it never releases the lock), re-engaged when they return to the
    // bottom. The streamer (ui.js) consults it through `following()` — before, it read
    // "is the reader at the bottom?" at the first token, which the chips had already
    // made false, so a long working phase left the answer streaming below the fold.
    const _follow = { on: false, sc: null, wired: null };
    function scroller() { return document.getElementById('chat-container'); }
    function atBottom(sc) { return sc ? (sc.scrollHeight - sc.scrollTop - sc.clientHeight) < 48 : true; }
    function armFollow() {
        const sc = scroller(); if (!sc) return;
        _follow.on = true; _follow.sc = sc;
        if (_follow.wired !== sc) {
            _follow.wired = sc;
            sc.addEventListener('scroll', () => { _follow.on = atBottom(sc); }, { passive: true });
        }
        try { sc.scrollTop = sc.scrollHeight; } catch (_) { /* no-op */ }
    }
    function follow() { const sc = _follow.sc || scroller(); if (_follow.on && sc) { try { sc.scrollTop = sc.scrollHeight; } catch (_) { /* no-op */ } } }
    function following() { return !!_follow.on; }

    // ── DOM: the chrome (one line + the receipt), built the same way live and on reload ──
    const CHEV = '<svg width="12" height="12" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 8l5 5 5-5"/></svg>';
    const SHIELD = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>';
    function chromeHtml() {
        return '<div class="la-line la-live" role="status" aria-live="off">'
            + '<span class="la-dot" aria-hidden="true"></span><span class="la-lead"></span><span class="la-counts"></span>'
            + '<span class="la-clock"></span><span class="la-chev" aria-hidden="true">' + CHEV + '</span>'
            + '<span class="la-track" aria-hidden="true"><span class="la-fill"></span></span></div>'
            + '<div class="la-proc" hidden><ol class="la-steps" role="list"></ol></div>';
    }
    function mount(st) {
        const log = document.getElementById('chat-log');
        if (!log) return null;
        try { log.querySelectorAll('[data-living]').forEach(n => { if (n !== st.el) n.remove(); }); } catch (_) { /* no-op */ }
        if (st.el && st.el.isConnected) return st.el;
        if (window.hideWelcomeScreen) { try { window.hideWelcomeScreen(); } catch (_) { /* no-op */ } }
        const w = document.createElement('div');
        w.className = 'message-bubble group flex items-start gap-4 answer-provisional la-bubble';
        w.setAttribute('aria-busy', 'true');
        w.setAttribute('data-living', st.sid);
        w.innerHTML =
            '<div class="flex-shrink-0 w-10 h-10 rounded-full flex items-center justify-center text-white font-bold shadow-lg assistant-avatar-icon" style="background:var(--teradata-orange,#F15F22)">A</div>'
            + '<div class="p-4 rounded-xl shadow-lg max-w-3xl glass-panel relative flex-1 min-w-0 la-box" style="background:var(--card-bg, rgba(20,22,25,0.6))">'
            + '<p class="font-bold mb-2 text-sm" style="color:var(--teradata-orange,#F15F22)">Assistant</p>'
            + '<span class="sr-only">The answer is being written.</span>'
            + chromeHtml()
            + '<div class="message-content la-content"></div>'
            + '</div>';
        log.appendChild(w);
        st.el = w;
        st.chips.forEach(c => paintChip(st, c, true));
        paintLine(st, true);
        placeFill(st);
        wireLine(st);
        startClock(st);
        _cur = st.sid;
        armFollow();
        return w;
    }
    function q(st, sel) { return st.el ? st.el.querySelector(sel) : null; }

    /** ui.js asks for the host to stream text INTO (so no second bubble is ever created). */
    function contentHost(sid) {
        const st = _live.get(sid);
        if (!st || st.ended || st.settled || !enabled()) return null;
        const w = mount(st);
        if (!w) return null;
        return { wrapper: w, content: w.querySelector('.la-content') };
    }

    // ── the phase hairline + the status words ───────────────────────────────────────────
    function placeFill(st) {
        const f = q(st, '.la-fill'); if (!f) return;
        const i = PHASES.indexOf(st.phase);
        f.style.width = st.settled ? '100%' : `${Math.round(((i + 1) / PHASES.length) * 100)}%`;
        f.classList.toggle('la-stalled', !!st.stopped);
    }
    function setPhase(st, p, statusText) {
        const i = PHASES.indexOf(p), j = PHASES.indexOf(st.phase);
        if (i > j) st.phase = p;
        if (statusText) setStatus(st, statusText);
        else if (i > j) setStatus(st, PHASE_STATUS[p]);
        placeFill(st);
    }
    function setStatus(st, text) {
        st.statusText = text;
        if (!st.folded) { const s = q(st, '.la-lead'); if (s) s.textContent = text; }
    }

    // ── the clock: one t0, frozen once at settle ───────────────────────────────────────
    function clockText(st) { return `${fmtDur(elapsed(st))}${st.tokens ? ' · ' + fmtTok(st.tokens) : ''}`; }
    function startClock(st) {
        stopClock(st);
        const tick = () => { const c = q(st, '.la-clock'); if (c) { c.textContent = clockText(st); c.setAttribute('data-tip', st.tokens ? `${Math.round(st.tokens).toLocaleString()} tokens this turn` : 'elapsed'); } };
        tick();
        st.tick = tick;
        st.clock = setInterval(tick, 200);
    }
    function stopClock(st) { if (st.clock) { clearInterval(st.clock); st.clock = null; } }

    // ── the receipt: curated steps as rows of a timeline ───────────────────────────────
    function chip(st, id, phase, text, fact, opts) {
        let c = st.chips.find(x => x.id === id);
        if (c) { c.text = text || c.text; if (fact != null) c.fact = fact; if (opts && opts.running != null) c.running = opts.running; if (opts && opts.state) c.state = opts.state; carry(st, c); paintChip(st, c, false); return c; }
        c = { id, phase, text, fact: fact || '', running: !!(opts && opts.running), kind: (opts && opts.kind) || '', state: (opts && opts.state) || '', t0: Date.now(), dur: null };
        st.chips.push(c);
        carry(st, c);
        paintChip(st, c, false);
        return c;
    }
    /**
     * The row's identity, written ONCE onto the event that produced it — the SSE frame and its
     * payload live, the persisted record on a reload — so the Live Status card rendered from
     * that same object can wear it (`data-la`). Non-enumerable: a payload is dumped as JSON on
     * some cards, and the key is a link, never a fact of the turn.
     */
    function mark(o, key) {
        if (!o || typeof o !== 'object') return;
        try { Object.defineProperty(o, '__la', { value: key, enumerable: false, writable: true, configurable: true }); } catch (_) { /* a frozen record carries no key */ }
    }
    function carry(st, c) { (st._carry || []).forEach(o => mark(o, c.id)); }
    function snap(st, id, fact, opts) {
        const c = st.chips.find(x => x.id === id); if (!c) return null;
        carry(st, c);
        c.running = false; c.fact = fact;
        c.dur = (opts && opts.dur != null) ? num(opts.dur) : (st.restored ? null : Date.now() - c.t0);
        if (opts && opts.kind) c.kind = opts.kind;
        if (opts && opts.tip) c.tip = String(opts.tip);
        paintChip(st, c, false, true);
        return c;
    }
    function paintChip(st, c, instant, snapped) {
        const ul = q(st, '.la-steps'); if (!ul) return;
        let li = ul.querySelector(`[data-chip="${c.id}"]`);
        if (!li) {
            li = document.createElement('li');
            li.className = 'la-chip'; li.setAttribute('data-chip', c.id); li.setAttribute('role', 'button'); li.tabIndex = 0;
            li.innerHTML = '<span class="la-mark" aria-hidden="true"></span><span class="la-t"></span><span class="la-f"></span><span class="la-dur"></span>';
            const open = () => openCard(st, c);
            li.addEventListener('click', open);
            li.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
            ul.appendChild(li);
            if (!instant && !reduced()) requestAnimationFrame(() => requestAnimationFrame(() => li.classList.add('la-in')));
            else li.classList.add('la-in');
        }
        li.classList.toggle('la-run', !!c.running);
        li.classList.toggle('la-snap', !!snapped && !instant);
        li.classList.remove('la-err', 'la-steer', 'la-fix', 'la-gate', 'la-pass', 'la-warn', 'la-danger');
        if (c.kind) li.classList.add('la-' + c.kind);
        if (c.state) li.classList.add('la-' + c.state);
        li.querySelector('.la-t').textContent = c.text;
        li.querySelector('.la-f').textContent = c.fact || (c.running ? 'running' : '');
        li.querySelector('.la-dur').textContent = (c.dur != null && c.dur >= 50) ? fmtDur(c.dur) : '';
        li.setAttribute('aria-label', `${c.text}${c.fact ? ' — ' + c.fact : ''}${c.tip ? ' (' + c.tip + ')' : ''}. ${isQuietCheck(c) ? 'Opens its record — the Live Status card, or the trust record when the panel is quiet on it.' : 'Opens its Live Status card.'}`);
        // the full fact when the row shows a bounded one (a refusal's whole reason), via the platform tooltip
        if (c.tip) { li.classList.add('u-tip'); li.setAttribute('data-tip', c.tip); } else { li.classList.remove('u-tip'); li.removeAttribute('data-tip'); }
        if (!st.folded) { const proc = q(st, '.la-proc'); if (proc) { proc.hidden = false; proc.classList.add('la-open'); } }
        if (onScreen(st.sid)) follow();
    }
    /** a check the platform ran on the draft: a row of the receipt + the fact the verdict reads */
    function gate(st, key, state, text, fact) {
        st.gates[key] = state;
        chip(st, 'gate:' + key, 'screen', text, fact, { kind: 'gate', state });
        placeChecksKey(st);
    }
    // ── depth 3: every row opens ITS Live Status card — live by the card's id, and on both paths
    // by the card's own words (the tool it ran, the gate it is), so a reloaded turn lands on the
    // same card the live turn did; a collapsed panel is opened first ──────────────────────────
    const HINT = { ctx: 'Context Window', kg: 'Knowledge Graph', rag: 'Champion|Strateg|RAG', know: 'Knowledge Retrieval|Knowledge', rerank: 'Rerank', plan: 'Meta-Plan|Strategic|Planning', pass: 'Synthesis|Pass',
        'gate:grounding': 'Grounding Gate', 'gate:guardrail': 'Guardrail', 'gate:honesty': 'Honesty', 'gate:lineage': 'Number Lineage|Lineage', 'gate:revision': 'Revision', tsw: 'Tool Surface', tsd: 'Tool Surface' };
    function escRe(x) { return String(x || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
    function hintFor(c) {
        if (c.tool) return escRe(c.tool);
        if (/^exp:/.test(c.id)) return escRe(c.id.slice(4));
        if (/^fix/.test(c.id)) return 'Correction';
        if (/^steer/.test(c.id)) return 'Steer';
        if (/^rec/.test(c.id)) return 'Recovery|Fallback|Retried|Continued';
        return HINT[c.id] || escRe(c.text);
    }
    function ordinalOf(st, c) { const h = hintFor(c); let k = 0; for (const x of st.chips) { if (x === c) break; if (hintFor(x) === h) k += 1; } return k; }
    /** the card(s) that carry this row's key — the last one is the step's final state (a tool's result, not its intent) */
    function cardByKey(c) {
        const host = document.getElementById('status-window-content'); if (!host) return null;
        let cards; try { cards = host.querySelectorAll(`.status-step[data-la="${String(c.id).replace(/"/g, '')}"]`); } catch (_) { return null; }
        return cards.length ? cards[cards.length - 1] : null;
    }
    /**
     * The words fallback, for a card rendered without a key (an older render path): matched on
     * the card's TITLE only — a card's body names things it is not about (the engine's start card
     * lists every available tool, the context card every module), and matching there is exactly
     * how a row used to land on the wrong event.
     */
    function findCard(st, c) {
        const host = document.getElementById('status-window-content'); if (!host) return null;
        let re; try { re = new RegExp(hintFor(c), 'i'); } catch (_) { return null; }
        const titleOf = el => { const h = el.querySelector('h4, .status-step-title'); return (h ? h.textContent : (el.firstElementChild ? el.firstElementChild.textContent : el.textContent)) || ''; };
        const cards = [...host.querySelectorAll('.status-step')].filter(el => re.test(titleOf(el)));
        return cards[ordinalOf(st, c)] || cards[0] || null;
    }
    function locateCard(st, c) { return cardByKey(c) || findCard(st, c); }
    /** a clean check has no card by design (the panel is quiet on a check that found nothing) — its record is the trust record */
    function openTrust(st) {
        const b = st.el ? st.el.querySelector('.la-trust') : null; if (!b) return false;
        if (b.getAttribute('aria-expanded') !== 'true') b.click();
        setTimeout(() => { try { const cap = st.el.querySelector('.ac-capsule, .answer-capsule, [data-ac-sid]'); (cap || b).scrollIntoView({ block: 'nearest', behavior: reduced() ? 'auto' : 'smooth' }); } catch (_) { /* no-op */ } }, 60);
        return true;
    }
    function isQuietCheck(c) { return /^gate:/.test(c.id) && c.state === 'pass'; }
    function ensurePanel() {
        try { const cb = document.getElementById('toggle-status-checkbox'); if (cb && !cb.checked) { cb.checked = true; cb.dispatchEvent(new Event('change')); } } catch (_) { /* no-op */ }
    }
    /**
     * Land on a card WHEREVER the panel keeps it. On an Optimize turn a card lives inside a
     * collapsed phase container (`details.status-phase-container`, nested for sub-phases),
     * and a filter pill may have hidden its category — a link that scrolls to a hidden card
     * lands nowhere. So: every collapsed ancestor container is opened, a category the reader
     * filtered out is switched back on THROUGH ITS PILL (the chip stays in step with what is
     * shown — never a silent class removal), then the card is scrolled to and highlighted.
     */
    function revealCard(card) {
        const host = document.getElementById('status-window-content');
        let unhid = false;
        for (let el = card; el && el !== host && el !== document.body; el = el.parentElement) {
            if (el.tagName === 'DETAILS' && el !== card && !el.open) el.open = true;
            if (el.classList && el.classList.contains('event-filtered-hidden')) {
                const cat = el.dataset && el.dataset.filterCategory;
                const chip = cat && document.querySelector(`.event-filter-chip[data-filter="${cat}"]`);
                if (chip) { chip.click(); unhid = true; }          // the pill re-enables the category and re-applies the filter
                else el.classList.remove('event-filtered-hidden');  // no pill to honour — show the card anyway
            }
        }
        const land = () => {
            card.scrollIntoView({ block: 'center', behavior: reduced() ? 'auto' : 'smooth' });
            card.classList.add('la-card-hot'); setTimeout(() => card.classList.remove('la-card-hot'), 1400);
        };
        // a container just opened (or a filter just re-applied) needs a frame to lay out
        if (unhid || typeof requestAnimationFrame !== 'function') setTimeout(land, 60); else requestAnimationFrame(land);
    }
    function openCard(st, c) {
        try {
            ensurePanel();
            const direct = panelShowsTurn(st) ? locateCard(st, c) : null;   // never another turn's card of the same ordinal
            if (direct) return revealCard(direct);
            // a check that found nothing has no card, live or reloaded — the panel is quiet on it by
            // design — so its record is the trust record: never a reload that lands nowhere
            if (isQuietCheck(c) && panelShowsTurn(st) && openTrust(st)) return;
            // the panel shows another turn (or nothing yet): load THIS turn's trace — the avatar's
            // own reload — then land on the card once it is rendered
            reloadOwnTurn(st);
            let tries = 0;
            const poll = () => {
                const k = panelShowsTurn(st) ? locateCard(st, c) : null; if (k) return revealCard(k);
                if (++tries < 16) return setTimeout(poll, 250);
                if (/^gate:/.test(c.id) && openTrust(st)) return;
                const host = document.getElementById('status-window-content'); if (host) host.scrollIntoView({ block: 'nearest' });
            };
            setTimeout(poll, 250);
        } catch (_) { /* a link must never break the bubble */ }
    }

    // ── the work line: status words while working, the counters once folded ────────────
    function countsHtml(st) {
        const c = st.counts, parts = [];
        const n = (v, one, many) => `<span class="la-n" data-n="${v}">${v}</span><span class="la-u"> ${v === 1 ? one : many}</span>`;
        if (c.tools) parts.push(c.queries === c.tools ? n(c.tools, 'query', 'queries') : n(c.tools, 'tool call', 'tool calls'));
        if (c.chunks) parts.push(n(c.chunks, 'document', 'documents'));
        if (c.entities) parts.push(n(c.entities, 'node', 'nodes'));   // graph nodes read (kgVocabulary: an entity is a business concept)
        if (c.experts) parts.push(n(c.experts, 'expert', 'experts'));
        if (c.steers) parts.push(n(c.steers, 'steer', 'steers'));
        if (c.corrections) parts.push(n(c.corrections, 'correction', 'corrections'));
        if (c.recoveries) parts.push(n(c.recoveries, 'recovery', 'recoveries'));
        if (c.revisions) parts.push(n(c.revisions, 'revision', 'revisions'));
        return parts.map(p => ' <span class="la-sep">·</span> ' + p).join('');
    }
    function paintLine(st, instant) {
        const line = q(st, '.la-line'); if (!line) return;
        const lead = line.querySelector('.la-lead'), counts = line.querySelector('.la-counts'), clock = line.querySelector('.la-clock');
        line.classList.toggle('la-live', !st.folded && !st.settled);
        line.classList.toggle('la-folded', !!st.folded);
        line.classList.toggle('la-settled', !!st.settled);
        line.classList.toggle('la-stopped', !!st.partial);
        if (st.folded || st.settled) {
            // ONE state word, honest at each moment: the fold lands at the FIRST TOKEN, when the
            // work is done but the answer is still being written — "Done" there would be false
            lead.textContent = st.partial ? 'Stopped' : (st.settled ? 'Done' : 'Writing');
            counts.innerHTML = countsHtml(st);
            line.setAttribute('role', 'button'); line.setAttribute('tabindex', '0');
            if (!line.hasAttribute('aria-expanded')) line.setAttribute('aria-expanded', 'false');
            line.removeAttribute('aria-live');
            line.setAttribute('aria-label', `${lead.textContent} ${fmtDur(elapsed(st))}. ${counts.textContent}. Opens the steps.`);
            if (!instant && !reduced()) {
                counts.querySelectorAll('.la-n[data-n]').forEach(el => {
                    const target = Number(el.dataset.n); let i = 0;
                    const step = () => { i += 1; el.textContent = String(i); if (i < target) setTimeout(step, 70); };
                    el.textContent = '0'; setTimeout(step, 120);
                });
            }
        } else {
            lead.textContent = st.statusText; counts.innerHTML = '';
        }
        if (clock) { clock.textContent = clockText(st); clock.classList.add('u-tip'); clock.setAttribute('data-tip', st.tokens ? `${Math.round(st.tokens).toLocaleString()} tokens this turn` : 'elapsed'); }
        placeFill(st);
    }
    function wireLine(st) {
        const line = q(st, '.la-line'), proc = q(st, '.la-proc');
        if (!line || !proc || line.dataset.wired) return;
        line.dataset.wired = '1';
        const toggle = () => {
            if (!st.folded && !st.settled) return;   // while working the receipt is already open
            const open = proc.hidden; proc.hidden = !open; proc.classList.toggle('la-open', open); line.classList.toggle('la-open', open); line.setAttribute('aria-expanded', String(open));
        };
        line.addEventListener('click', toggle);
        line.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } });
    }
    // the fold: rows converge into the line
    function fold(st) {
        if (st.folded) return;
        st.folded = true;
        const proc = q(st, '.la-proc'), ul = q(st, '.la-steps'), line = q(st, '.la-line');
        if (ul) [...ul.children].forEach((li, i) => setTimeout(() => li.classList.add('la-fold'), reduced() ? 0 : 24 * i));
        setTimeout(() => {
            if (proc) { proc.classList.remove('la-open'); proc.hidden = true; }
            if (ul) [...ul.children].forEach(li => li.classList.remove('la-fold'));
            if (onScreen(st.sid)) follow();
        }, reduced() ? 0 : 220);
        if (line) line.setAttribute('aria-expanded', 'false');
        paintLine(st, false);
    }

    // ── the ONE verdict ─────────────────────────────────────────────────────────────────
    /** {word, state, tip} — derived once from what the checks did; never "verified" with no check run. */
    function verdictOf(st) {
        const ran = GATE_ORDER.filter(k => k in st.gates).concat(Object.keys(st.gates).filter(k => !GATE_ORDER.includes(k)));   // one order, both paths
        const n = st.findings;
        if (n > 0) {
            const state = ran.some(k => st.gates[k] === 'danger') ? 'danger' : 'warn';
            return { word: `${n} finding${n === 1 ? '' : 's'}`, state, tip: `The checks found something on this answer — open the trust record to see what.` };
        }
        if (ran.length) {
            const rev = st.counts.revisions ? ` The draft was revised ${st.counts.revisions === 1 ? 'once' : 'twice'} before delivery.` : '';
            return { word: 'Verified', state: 'pass', tip: `Checked ${ran.map(k => GATE_LABEL[k] || k).join(' · ')} — nothing to report.${rev}` };
        }
        if (st.partial) return { word: 'Not checked', state: 'na', tip: 'Stopped before the checks ran on this answer.' };
        return { word: 'Not checked', state: 'na', tip: 'No check ran on this answer — this agent has no gate armed.' };
    }
    function paintTrust(st, instant) {
        const box = st.el; if (!box) return;
        const slot = box.querySelector('.msg-actions-slot'); if (!slot) return;
        let b = slot.querySelector('.la-trust');
        const v = verdictOf(st);
        if (!b) {
            b = document.createElement('button'); b.type = 'button';
            b.innerHTML = SHIELD + '<span class="la-tw"></span><span class="la-tchev" aria-hidden="true">' + CHEV + '</span>';
            // ONE trust control: the chip opens the record; the capsule's own toggle (where the
            // listener and the aria-expanded state live) is folded behind it, never a second button.
            // The capsule mounts its toggle by observer AFTER the settle, so the fold runs now
            // AND when the toggle arrives (one observer per slot, disconnected once it has folded).
            const sync = () => { const t = slot.querySelector('.ac-toggle'); const open = t && t.getAttribute('aria-expanded') === 'true'; b.classList.toggle('la-open', !!open); b.setAttribute('aria-expanded', String(!!open)); };
            const foldToggle = () => {
                const t = slot.querySelector('.ac-toggle'); if (!t || t.dataset.laFolded) return false;
                t.dataset.laFolded = '1'; t.classList.add('la-folded-toggle'); t.setAttribute('tabindex', '-1'); t.setAttribute('aria-hidden', 'true');
                t.addEventListener('click', () => setTimeout(sync, 0));
                return true;
            };
            b.addEventListener('click', () => { const tt = slot.querySelector('.ac-toggle'); if (tt) { tt.click(); setTimeout(sync, 0); } });
            if (!foldToggle()) {
                try {
                    const mo = new MutationObserver(() => { if (foldToggle()) mo.disconnect(); });
                    mo.observe(slot, { childList: true, subtree: true });
                    setTimeout(() => mo.disconnect(), 30000);
                } catch (_) { /* no observer: the toggle stays a plain second button */ }
            }
            slot.insertBefore(b, slot.firstElementChild || null);
            if (!instant && !reduced()) requestAnimationFrame(() => b.classList.add('la-pop'));
            b.setAttribute('aria-expanded', 'false');
        }
        b.className = `la-trust la-${v.state} u-tip u-tip-above` + (b.classList.contains('la-open') ? ' la-open' : '');
        b.querySelector('.la-tw').textContent = v.word;
        b.setAttribute('data-tip', v.tip + (st.sealed ? ' Sealed in the provenance chain.' : '') + ' Opens the trust record.');
        b.setAttribute('aria-label', `${v.word}. Opens why you can trust this answer.`);
        // the composer's receipt reads this chip — tell it the verdict moved (the seal, a late score)
        try { UderiaBus.emit('uderia:answer-verdict', { sid: st.sid, turnId: st.turnId }); } catch (_) { /* no-op */ }
    }

    // ── the curated vocabulary: a tool name → consumer words ────────────────────────────
    function toolWords(name, args) {
        const n = String(name || '');
        if (/^TDA_FinalReport$/i.test(n)) return null;                          // drafting, not a step
        if (/^TDA_(SystemLog|ContextReport|CurrentDate|DateRange)$/i.test(n)) return null;
        if (/^TDA_KnowledgeGraph$/i.test(n)) return 'Consulted the knowledge graph';
        if (/^TDA_Compute$/i.test(n)) return 'Computed the figures';
        if (/^TDA_Charting$/i.test(n)) return 'Drew a chart';
        if (/^TDA_Dashboard$/i.test(n)) return 'Updated a dashboard';
        if (/^TDA_Canvas$/i.test(n)) return 'Wrote to the canvas';
        if (/^TDA_Memory$/i.test(n)) return 'Updated agent memory';
        if (/^TDA_DatabaseNavigator$/i.test(n)) return 'Browsed the database';
        if (/^TDA_FindTools$/i.test(n)) return 'Looked for a tool';
        if (/^TDA_Action$/i.test(n)) {
            const verb = String((args && args.action) || '').toLowerCase();
            if (verb === 'list_actions') return 'Listed the actions it may perform';
            // interactive: perform/undo PREPARE — nothing changes until the person confirms
            return verb === 'undo' ? 'Prepared an undo · awaiting your confirmation'
                                   : 'Prepared an action · awaiting your confirmation';
        }
        if (/^TDA_SystemOrchestration$/i.test(n)) return 'Ran the query across the range';
        if (/^TDA_/i.test(n)) return 'Used ' + n.replace(/^TDA_/, '');
        const sql = args && (args.sql || args.query);
        if (/readQuery|writeQuery|query/i.test(n)) {
            const tabs = sqlTables(sql);
            if (!tabs.length) return 'Ran a query';
            const shown = tabs.slice(0, 3).join(', ') + (tabs.length > 3 ? ` +${tabs.length - 3} more` : '');
            return `Queried ${shown}`;
        }
        if (/databaseList/i.test(n)) return 'Listed the databases';
        if (/tableList/i.test(n)) return `Listed the tables${args && args.database_name ? ' in ' + args.database_name : ''}`;
        if (/columnDescription|tableDDL|describe/i.test(n)) return `Read the schema${args && (args.table_name || args.object_name) ? ' of ' + (args.table_name || args.object_name) : ''}`;
        if (/tablePreview/i.test(n)) return 'Previewed the data';
        const human = n.replace(/^[a-z]+_/, '').replace(/([a-z])([A-Z])/g, '$1 $2').replace(/_/g, ' ').toLowerCase();
        return human ? human.charAt(0).toUpperCase() + human.slice(1) : 'Used a tool';
    }
    /**
     * Every table a statement reads — each FROM and each JOIN, in the order the statement names
     * them, once each; a CTE's own name and a derived table are not tables. A join of three
     * entities is three entities on the row, not the first one.
     */
    function sqlTables(sql) {
        const s = String(sql || ''); if (!s) return [];
        const ctes = new Set(); const cteRe = /(?:\bwith\b|,)\s*([\w]+)\s+as\s*\(/gi; let m;
        while ((m = cteRe.exec(s))) ctes.add(m[1].toLowerCase());
        const out = []; const seen = new Set();
        const re = /\b(?:from|join)\s+((?:"[^"]+"|[\w]+)(?:\s*\.\s*(?:"[^"]+"|[\w]+)){0,2})/gi;
        while ((m = re.exec(s))) {
            if (inFunctionArgs(s, m.index)) continue;   // EXTRACT(YEAR FROM col), SUBSTRING(x FROM n): not a table
            const name = m[1].replace(/"/g, '').replace(/\s+/g, '');
            if (!name || /^(select|lateral|values|unnest)$/i.test(name) || ctes.has(name.toLowerCase())) continue;
            const k = name.toLowerCase(); if (seen.has(k)) continue; seen.add(k); out.push(name);
        }
        return out;
    }
    /** is this position inside a function's argument list (the nearest unclosed `(` belongs to `name(` and no SELECT opened since)? */
    function inFunctionArgs(s, at) {
        let depth = 0;
        for (let i = at - 1; i >= 0; i--) {
            const ch = s[i];
            if (ch === ')') depth += 1;
            else if (ch === '(') {
                if (depth > 0) { depth -= 1; continue; }
                const before = s.slice(Math.max(0, i - 40), i), inside = s.slice(i + 1, at);
                return /[\w]\s*$/.test(before) && !/\bselect\b/i.test(inside);
            }
        }
        return false;
    }
    /** the open call a completion closes: the oldest one of the SAME tool (the completion names it), else the oldest open call */
    function takePending(st, name) {
        const p = st.pendingTools || [];
        let i = name ? p.findIndex(c => String(c.tool || '').toLowerCase() === String(name).toLowerCase()) : -1;
        if (i < 0) i = p.length ? 0 : -1;
        return i < 0 ? null : p.splice(i, 1)[0];
    }
    function isQuery(name) { return /readQuery|writeQuery|query/i.test(String(name || '')); }
    function rowsFact(d) {
        if (!d) return null;
        const n = d.result_rows ?? d.row_count ?? (d.metadata && d.metadata.row_count) ?? (Array.isArray(d.results) ? d.results.length : null) ?? (Array.isArray(d.data) ? d.data.length : null);
        if (n == null) return null;
        return `${Number(n).toLocaleString()} row${Number(n) === 1 ? '' : 's'}`;
    }

    // ── the ONE reducer: every live frame, and every recorded event of a reloaded turn ───
    function reduce(st, type, ev) {
        // the facts ride `details` (an object) — or `payload` when `details` is the step's prose
        const d = (ev && (typeof ev.details === 'object' && ev.details ? ev.details : (ev.payload && typeof ev.payload === 'object' ? ev.payload : ev))) || {};
        // the objects that carry this event — a row produced from it marks every one (see `mark`)
        st._carry = [ev, d, ev && ev.src].filter(o => o && typeof o === 'object');
        switch (type) {
        case 'execution_start': {
            st.cls = d.profile_type || st.cls; st.tag = d.profile_tag || st.tag;
            if (st.tag && !st.restored) setStatus(st, `@${st.tag} · ${st.statusText}`);
            break;
        }
        case 'context_window_snapshot': {
            const mods = d.modules ? (Array.isArray(d.modules) ? d.modules.length : Object.keys(d.modules).length) : (Array.isArray(d.contributions) ? d.contributions.length : 0);
            setPhase(st, 'context');
            chip(st, 'ctx', 'context', 'Assembled the context', mods ? `${mods} modules` : 'ready');
            break;
        }
        case 'kg_enrichment': {
            const n = num(d.entity_count) || (Array.isArray(d.entity_names) ? d.entity_names.length : 0) || num(d.entities)
                || (Array.isArray(d.enrichments) ? d.enrichments.reduce((a, e) => a + num(e && e.entity_count), 0) : 0);   // per graph, as the event carries it
            setPhase(st, 'retrieve');
            st.counts.entities = n || st.counts.entities;
            chip(st, 'kg', 'retrieve', 'Read the knowledge graph', n ? `${n} node${n === 1 ? '' : 's'}` : 'in scope');
            break;
        }
        case 'rag_retrieval': {
            // The planner emits this event ONLY when champion cases matched, and its payload is the
            // first case (`case_id` + `full_case_data`) plus `count`. An older frame carries no count:
            // its existence is the fact, so an absent count reads as one match — never as none.
            const explicit = d.count != null ? num(d.count) : null;
            const n = explicit != null ? explicit : ((Array.isArray(d.cases) && d.cases.length) || ((d.case_id || d.full_case_data) ? 1 : 0));
            setPhase(st, 'retrieve');
            chip(st, 'rag', 'retrieve', 'Recalled proven strategies', n ? `${n} case${n === 1 ? '' : 's'}` : 'none matched');
            break;
        }
        case 'knowledge_retrieval_start': {
            setPhase(st, 'retrieve', 'Retrieving knowledge…');
            chip(st, 'know', 'retrieve', 'Searched the knowledge repositories', null, { running: true });
            break;
        }
        case 'knowledge_retrieval_complete':
        case 'knowledge_retrieval': {
            const n = num(d.document_count) || (Array.isArray(d.documents) ? d.documents.length : 0);
            setPhase(st, 'retrieve');
            st.counts.chunks = n;
            if (!st.chips.find(x => x.id === 'know')) chip(st, 'know', 'retrieve', 'Searched the knowledge repositories', null, { running: true });
            snap(st, 'know', n ? `${n} document${n === 1 ? '' : 's'}`
                : (d.answered_from_attachments ? 'nothing relevant · answering from the attachment' : 'nothing relevant'),
                { dur: d.duration_ms, kind: n ? '' : 'warn' });
            break;
        }
        case 'knowledge_reranking_start': chip(st, 'rerank', 'retrieve', 'Reranked the evidence', null, { running: true }); break;
        case 'knowledge_reranking_complete': snap(st, 'rerank', 'done'); break;
        case 'plan_generated': {
            const n = num(d.phase_count) || (Array.isArray(d.plan) ? d.plan.length : 0) || (Array.isArray(d.phases) ? d.phases.length : 0);
            setPhase(st, 'reason', 'Planning…');
            chip(st, 'plan', 'reason', 'Planned the work', n ? `${n} phase${n === 1 ? '' : 's'}` : 'ready');
            break;
        }
        case 'phase_start': {
            if (d.goal) setStatus(st, `Phase ${d.phase_num || ''}${d.total_phases ? '/' + d.total_phases : ''}: ${String(d.goal).slice(0, 72)}`);
            break;
        }
        case 'tool_intent': case 'conversation_tool_invoked': {
            const name = d.tool_name || (d.details && d.details.tool_name);
            const words = toolWords(name, d.arguments || d.args || (d.details && d.details.arguments));
            if (!words) { if (/^TDA_FinalReport$/i.test(name || '')) setPhase(st, 'draft'); break; }
            setPhase(st, 'reason');
            setStatus(st, words + '…');
            const id = 'tool' + (st.counts.tools + 1);
            st.counts.tools += 1; if (isQuery(name)) st.counts.queries += 1;
            const tc = chip(st, id, 'reason', words, null, { running: true });
            tc.tool = name;
            st.pendingTools.push(tc);   // a model may fire several calls in one round: pair each completion with the OLDEST open call of that tool
            break;
        }
        case 'tool_result': case 'conversation_tool_completed': {
            const c = takePending(st, d.tool_name || (d.details && d.details.tool_name)); if (!c) break;
            const rows = /^TDA_/i.test(c.tool || '') ? (d.result_rows != null ? rowsFact(d) : null) : rowsFact(d);   // a component call has no rows to count
            const failed = (d.success === false) || (ev && ev.type === 'error') || (d.status === 'error');
            // a component that REFUSED (recovery-shaped — the model corrects itself on the next call)
            // is not "done": the row says why, in the component's own words, with the failed mark
            // the row carries the reason's first clause (bounded, so the title is never pushed
            // off the line); the full reason is on the row's label, one hover away
            const reason = typeof d.refused === 'string' ? d.refused.replace(/\s+/g, ' ').trim() : '';
            const refused = reason ? `refused — ${reason.split(/ — |: /)[0].slice(0, 48)}` : '';
            snap(st, c.id, refused || (failed ? 'failed' : (rows || 'done')), { kind: (failed || refused) ? 'err' : null, dur: d.duration_ms, tip: reason || undefined });
            break;
        }
        case 'tool_error': {
            const c = takePending(st, d.tool_name || (d.details && d.details.tool_name)); if (c) snap(st, c.id, 'failed', { kind: 'err' });
            break;
        }
        case 'conversation_tool_rewrite': {
            st.counts.corrections += 1;
            chip(st, 'fix' + st.counts.corrections, 'reason', 'Corrected the statement', 'no LLM round spent', { kind: 'fix' });
            break;
        }
        case 'genie_slave_invoked': {
            st.counts.experts += 1;
            setPhase(st, 'reason', `Asking @${d.profile_tag || 'expert'}…`);
            chip(st, 'exp:' + (d.profile_tag || st.counts.experts), 'reason', `Asked @${d.profile_tag || 'expert'}`, null, { running: true });
            break;
        }
        case 'genie_slave_completed': {
            snap(st, 'exp:' + (d.profile_tag || ''), d.success === false ? 'failed' : 'answered', { kind: d.success === false ? 'err' : null, dur: d.duration_ms });
            break;
        }
        case 'genie_synthesis_start': setPhase(st, 'draft', 'Synthesizing the experts’ answers…'); break;
        case 'genie_synthesis_skipped': chip(st, 'pass', 'draft', 'Passed the expert’s answer through', 'no synthesis needed'); setPhase(st, 'draft'); break;
        case 'llm_execution': setPhase(st, 'draft'); break;
        case 'status_indicator_update': {
            if (d.target === 'llm' && d.state === 'busy' && PHASES.indexOf(st.phase) < PHASES.indexOf('reason')) setPhase(st, 'reason');
            break;
        }
        case 'llm_execution_complete': case 'conversation_llm_complete': case 'genie_synthesis_complete': case 'conversation_agent_complete':
            if (!st.folded) fold(st);
            setPhase(st, 'screen');
            break;
        case 'grounding_gate': {
            const dec = String(d.decision || '');
            if (!dec) break;   // the gate had nothing to check: not a check that ran
            const gs = dec === 'block' ? 'danger' : (dec === 'annotate' ? 'warn' : 'pass');
            if (gs !== 'pass') st.findings += 1;
            setPhase(st, 'screen');
            gate(st, 'grounding', gs, 'Checked against its sources', dec === 'block' ? 'withheld — outside its sources' : (dec === 'annotate' ? 'a claim the sources do not support was marked' : 'in scope'));
            break;
        }
        case 'guardrail': {
            const act = String(d.action || '');
            const gs = act === 'block' ? 'danger' : ((act === 'redact' || act === 'annotate') ? 'warn' : 'pass');   // the trust grammar: masked = worth a look, withheld = found something
            if (d.acted || gs !== 'pass') st.findings += 1;
            setPhase(st, 'screen');
            gate(st, 'guardrail', gs, 'Screened for personal data and harm', act === 'redact' ? 'personal data masked' : (act === 'block' ? 'withheld' : (act === 'annotate' ? 'a finding noted' : 'nothing found')));
            break;
        }
        case 'honesty': {
            const acted = !!d.acted;
            if (acted) st.findings += 1;
            gate(st, 'honesty', acted ? 'warn' : 'pass', 'Checked the claims', acted ? `an over-claim was ${d.action === 'rewrite' ? 'rewritten' : 'disclosed'}` : 'claims only what it can show');
            break;
        }
        case 'compute_lineage': {
            st.receipts = num(d.receipted) || (Array.isArray(d.receipted) ? d.receipted.length : 0) || st.receipts;
            const un = num(d.unreceipted) || (Array.isArray(d.unreceipted) ? d.unreceipted.length : 0);
            if (un && !st.gates.lineage) st.findings += 1;   // a figure with no receipt is a finding, once
            gate(st, 'lineage', un ? 'warn' : 'pass', 'Traced the figures', un ? `${un} figure${un === 1 ? '' : 's'} without a receipt` : 'every figure traces to its rows');
            break;
        }
        case 'obda': break;
        case 'steer_consumed': {
            st.counts.steers += 1;
            chip(st, 'steer' + st.counts.steers, st.phase, 'Considered your steer', d.at ? `at ${d.at}` : 'at its next step', { kind: 'steer' });
            break;
        }
        case 'llm_recovery': {
            st.counts.recoveries += 1;
            const kind = String(d.kind || 'retry');
            chip(st, 'rec' + st.counts.recoveries, st.phase, kind === 'fallback' ? `Served by the fallback ${d.to || ''}`.trim() : (kind === 'continuation' ? 'Continued a cut-off reply' : 'Retried the model'), d.attempt ? `attempt ${d.attempt}` : '', { kind: 'fix' });
            break;
        }
        case 'outcome_revision': {
            st.counts.revisions = Math.max(st.counts.revisions, num(d.revision) || st.counts.revisions + 1);
            const ok = !!d.cleared;
            if (ok && st.findings > 0) st.findings -= 1;
            gate(st, 'revision', ok ? 'pass' : 'warn', ok ? 'Revised the draft' : 'Tried a revision', ok ? 'the finding cleared' : 'the first draft stands');
            break;
        }
        case 'tool_surface': {
            const c = d.counts || {}; const asked = Array.isArray(d.asked_denied) ? d.asked_denied : [];
            if (num(c.widened) > st.counts.widened) { st.counts.widened = num(c.widened); chip(st, 'tsw', 'reason', `Found ${st.counts.widened} more tool${st.counts.widened === 1 ? '' : 's'}`, 'widened its own toolkit'); }
            if (asked.length > st.counts.askedDenied) { st.counts.askedDenied = asked.length; chip(st, 'tsd', 'reason', 'Asked for a tool it may not use', asked[asked.length - 1] || '', { kind: 'warn' }); }
            break;
        }
        case 'token_update': {
            const t = num(d.turn_input) + num(d.turn_output);
            if (t > st.tokens) { st.tokens = t; if (st.tick) st.tick(); }
            break;
        }
        case 'turn_score_complete': seal(st, 'scored'); break;
        default: break;
        }
    }

    // ── the stream: every event passes here BEFORE its own dispatch ──────────────────────
    function onEvent(sid, eventName, ev) {
        const st = _live.get(sid);
        if (!st || st.ended || st.settled) return;
        if ((!st.el || !st.el.isConnected) && onScreen(sid)) mount(st);
        const type = (ev && ev.type) || eventName;
        try { reduce(st, type, ev); } catch (_) { /* a live instrument must never break the stream */ }
    }
    /** the notification channel (steer_consumed, llm_recovery, turn_score_complete ride it) */
    function onNotification(type, payload) {
        const p = payload || {};
        const sid = p.session_id || (p.details && p.details.session_id) || cur();
        const st = sid && _live.get(sid);
        if (!st) return;
        if (type === 'turn_score_complete') { seal(st, 'scored'); return; }
        onEvent(sid, type, { type, details: p.details || p });
    }

    // ── writing: the first token folds the block; text renders with a stable block model ─
    function firstToken(sid) {
        const st = _live.get(sid); if (!st) return;
        if (!st.folded) fold(st);
        setPhase(st, 'draft', 'Writing…');
    }
    /** Hold back an unclosed code fence and a table whose header row is not closed yet. */
    function stableText(text) {
        let t = String(text || '');
        const fences = (t.match(/```/g) || []).length;
        if (fences % 2 === 1) t = t.slice(0, t.lastIndexOf('```'));
        // trailing table lines without a separator row yet → hold them
        const lines = t.split('\n'); let i = lines.length - 1, tail = [];
        while (i >= 0 && /^\s*\|/.test(lines[i])) { tail.unshift(lines[i]); i -= 1; }
        if (tail.length && !tail.some(l => /^\s*\|?\s*:?-{2,}/.test(l))) t = lines.slice(0, i + 1).join('\n');
        return t;
    }
    /** Render markdown into `content`; only the NEW characters rise in. The whole block is
     *  re-parsed per frame (markdown is not incremental), so each batch is REMEMBERED by its
     *  character range and re-wrapped with a negative animation delay until its rise is over —
     *  a batch that arrived 100 ms ago resumes 100 ms into its animation instead of snapping. */
    const RISE_MS = 240;
    function renderText(content, text, prebuilt) {
        const before = content.textContent || '';
        let html = typeof prebuilt === 'string' ? prebuilt : null;   // the final's own renderer (server), when it sent one
        if (html == null) {
            const shown = stableText(text);
            try { html = (typeof marked !== 'undefined') ? marked.parse(shown) : esc(shown); } catch (_) { html = esc(shown); }
        }
        content.innerHTML = html;
        try {
            const after = content.textContent || '';
            const now = Date.now();
            let batches = content._laBatches || [];
            const grew = after.length > before.length && after.startsWith(before.slice(0, Math.min(before.length, 64)));
            if (grew) batches.push({ start: before.length, end: after.length, t: now }); else batches = [];
            batches = batches.filter(b => now - b.t < RISE_MS);
            content._laBatches = batches;
            if (!reduced()) for (let k = batches.length - 1; k >= 0; k--) wrapRange(content, batches[k].start, batches[k].end, now - batches[k].t);
            content.querySelectorAll('table:not([data-la])').forEach(tb => { tb.setAttribute('data-la', '1'); tb.querySelectorAll('tbody tr').forEach((tr, k) => { tr.classList.add('la-row'); tr.style.animationDelay = (k * 40) + 'ms'; }); });
            content.querySelectorAll('pre:not([data-la])').forEach(p => { p.setAttribute('data-la', '1'); p.classList.add('la-block'); });
        } catch (_) { /* decoration only */ }
        return true;
    }
    /** Wrap the plain-text range [start, end) of `root` in .la-new spans (a table's or a code
     *  block's text counts toward the offsets but is never wrapped — those land as blocks). */
    function wrapRange(root, start, end, ageMs) {
        const nodes = []; let off = 0;
        const collect = (node, skip) => { for (const c of node.childNodes) {
            if (c.nodeType === 3) { nodes.push({ n: c, at: off, skip }); off += c.nodeValue.length; }
            else if (c.nodeType === 1) collect(c, skip || /^(PRE|CODE|TABLE)$/.test(c.tagName));
        } };
        collect(root, false);
        for (let k = nodes.length - 1; k >= 0; k--) {
            const { n, at, skip } = nodes[k]; const len = n.nodeValue.length;
            const a = Math.max(start, at), b = Math.min(end, at + len);
            if (b <= a || skip) continue;
            let target = n;
            if (b < at + len) target.splitText(b - at);
            if (a > at) target = target.splitText(a - at);
            const span = document.createElement('span'); span.className = 'la-new';
            if (ageMs > 0) span.style.animationDelay = `-${Math.min(ageMs, RISE_MS)}ms`;
            target.parentNode.insertBefore(span, target); span.appendChild(target);
        }
    }

    // ── the text is done (final arrived / stopped / errored) ───────────────────────────
    function textDone(sid, outcome) {
        const st = _live.get(sid); if (!st) return false;
        if (!outcome) { abort(sid); return false; }     // no final answer to settle into
        st.partial = outcome === 'partial';
        return true;
    }
    function abort(sid) {
        const st = _live.get(sid); if (!st) return;
        stopClock(st);
        if (st.el) { try { st.el.remove(); } catch (_) { if (st.el.parentNode) st.el.parentNode.removeChild(st.el); } }
        st.el = null; st.ended = true;
        _live.delete(sid);
    }

    // ── settle: the chrome moves onto the authoritative message; the verdict lands once ───
    function settle(sid, finalWrapper, opts) {   // opts: { partial, turnId, checks, durationMs }
        const st = _live.get(sid); if (!st || st.settled) return false;
        const o = opts || {};
        st.settled = true; st.partial = !!o.partial; st.turnId = o.turnId != null ? o.turnId : st.turnId;
        if (st.turnId != null && panelTurn() === 'live:' + sid) panelTurn(turnStamp(sid, st.turnId));
        st.t1 = Date.now(); stopClock(st);
        st.chips.forEach(c => { if (c.running) { c.running = false; c.fact = c.fact || (st.partial ? 'stopped' : 'done'); paintChip(st, c, true); } });
        st.pendingTools = [];
        const box = finalWrapper && finalWrapper.querySelector('.message-content') ? finalWrapper.querySelector('.message-content').parentNode : null;
        const contentEl = box ? box.querySelector('.message-content') : null;
        if (box && contentEl && st.el) {
            const line = q(st, '.la-line'), proc = q(st, '.la-proc');
            box.classList.add('la-box', 'la-settled');
            if (line) box.insertBefore(line, contentEl);
            if (proc) box.insertBefore(proc, contentEl);
            st.el.remove();
            st.el = box.closest('.message-bubble') || finalWrapper;
            if (!st.folded) { st.folded = true; if (proc) { proc.hidden = true; proc.classList.remove('la-open'); } }
            wireLine(st);
            if (o.checks) reduceStamps(st, o.checks);
            if (o.durationMs != null && num(o.durationMs) > 0) st.t1 = st.t0 + num(o.durationMs);   // the turn's own clock, as the reload will read it
            orderGateRows(st);
            setPhase(st, 'seal');
            if (st.partial) {
                st.stopped = true;
                const cut = document.createElement('div'); cut.className = 'la-cut'; cut.setAttribute('role', 'note'); cut.setAttribute('aria-label', 'stopped here');
                contentEl.appendChild(cut);
            }
            paintLine(st, true);
            paintTrust(st, false);
            landMarks(contentEl, st);
            // landed in place: the caller holds the reader's line still (ui.landInPlace);
            // following to the bottom here would move exactly what they were reading
            if (!o.inPlace && onScreen(sid)) follow();
        }
        return true;
    }
    /** The verification the reader can watch: each mark on the final text animates in, staggered. */
    function landMarks(contentEl, st) {
        try {
            let k = 0; const stag = reduced() ? 0 : 90;
            contentEl.querySelectorAll('mark.claim-unsupported').forEach(m => { m.classList.add('la-draw'); m.style.animationDelay = (120 + stag * k++) + 'ms'; });
            contentEl.querySelectorAll('mark.figure-receipted, mark.figure-quoted').forEach(m => { m.classList.add('la-rcpt'); m.style.animationDelay = (120 + stag * k++) + 'ms'; });
            const tw = []; const collect = (node) => { for (const c of node.childNodes) { if (c.nodeType === 3) tw.push(c); else if (c.nodeType === 1 && !/^(PRE|CODE)$/.test(c.tagName)) collect(c); } };
            collect(contentEl);
            tw.forEach(tn => {
                const re = /\[REDACTED:[^\]]+\]/g; if (!re.test(tn.nodeValue)) return;
                const frag = document.createDocumentFragment(); let last = 0; re.lastIndex = 0; let m;
                while ((m = re.exec(tn.nodeValue))) {
                    if (m.index > last) frag.appendChild(document.createTextNode(tn.nodeValue.slice(last, m.index)));
                    const s = document.createElement('span'); s.className = 'la-redacted'; s.textContent = m[0]; s.style.animationDelay = (120 + stag * k++) + 'ms';
                    frag.appendChild(s); last = m.index + m[0].length;
                }
                if (last < tn.nodeValue.length) frag.appendChild(document.createTextNode(tn.nodeValue.slice(last)));
                tn.parentNode.replaceChild(frag, tn);
            });
        } catch (_) { /* decoration only */ }
    }

    // ── the seal ───────────────────────────────────────────────────────────────────────
    function seal(st, why) {
        if (!st || st.sealed) return;
        st.sealed = true;
        const dot = q(st, '.la-dot'); if (dot) dot.classList.add('la-done');
        if (st.settled) paintTrust(st, true);
        const v = verdictOf(st);
        announce(v.state === 'na' ? 'Verification: no check ran on this answer.' : `Verification complete: ${v.word}.`);
    }
    function announce(text) {
        try {
            let live = document.getElementById('answer-stream-status');
            if (!live) { live = document.createElement('div'); live.setAttribute('id', 'answer-stream-status'); live.className = 'sr-only'; live.setAttribute('role', 'status'); live.setAttribute('aria-live', 'polite'); document.body.appendChild(live); }
            live.textContent = ''; setTimeout(() => { live.textContent = text; }, 30);
        } catch (_) { /* no-op */ }
    }

    // ── the stream's end / a stop ──────────────────────────────────────────────────────
    function stopping(sid) {
        const st = _live.get(sid); if (!st || st.settled) return;
        st.stopped = true; placeFill(st); setStatus(st, 'Stopping…');
    }
    function ended(sid) {
        const st = _live.get(sid); if (!st) return;
        stopClock(st);
        if (st.settled) { if (!st.sealed) seal(st, 'persisted'); st.ended = true; _live.delete(sid); return; }
        abort(sid);
    }

    /** the check rows in ONE order, whatever order they arrived in — live (event order) or reloaded (stamp order) */
    const GATE_ORDER = ['grounding', 'guardrail', 'honesty', 'lineage', 'revision'];
    function orderGateRows(st) {
        const ul = q(st, '.la-steps');
        const gates = GATE_ORDER.map(k => st.chips.find(c => c.id === 'gate:' + k)).filter(Boolean);
        if (!gates.length) return;
        st.chips = st.chips.filter(c => !/^gate:/.test(c.id)).concat(gates);
        if (ul) gates.forEach(c => { const li = ul.querySelector(`[data-chip="${c.id}"]`); if (li) { li.remove(); ul.appendChild(li); } });
        placeChecksKey(st);
    }
    /**
     * THE KEY for the receipt's two colours. A white mark is a step of the agent's WORK (it
     * happened); a green mark is a CHECK the platform ran on the draft that held (trust — the
     * same green as every trust surface). Two colours with no key are a guess (the trust
     * contract's own rule), so the check rows sit under one labelled hairline: the colour
     * change reads as "here the platform starts verifying", never as random.
     */
    function placeChecksKey(st) {
        const ul = q(st, '.la-steps'); if (!ul) return;
        let key = ul.querySelector('.la-key');
        const first = ul.querySelector('.la-chip.la-gate');
        if (!first) { if (key) key.remove(); return; }
        if (!key) { key = document.createElement('li'); key.className = 'la-key'; key.setAttribute('role', 'presentation'); key.textContent = 'Checks on the draft'; }
        if (key.nextElementSibling !== first) ul.insertBefore(key, first);
    }
    /**
     * The checks that RAN, from their stamps — the persisted turn's, or the `checks` summary the
     * final_answer frame carries (a clean guardrail is quiet on the live stream by design, so the
     * settle reads the stamps exactly as a reload does). Idempotent per check: a check the live
     * stream already reported (it acted) is not counted twice.
     */
    function reduceStamps(st, src) {
        const t = src || {};
        const g = t.grounding; if (!st.gates.grounding && g && g.decision) reduce(st, 'grounding_gate', { details: { decision: g.decision }, src: g });
        const gu = t.guardrail; if (!st.gates.guardrail && gu && gu.ran) reduce(st, 'guardrail', { details: { action: gu.action, acted: gu.acted }, src: gu });
        const h = t.honesty; if (!st.gates.honesty && h && h.ran) reduce(st, 'honesty', { details: { acted: h.acted, action: h.action }, src: h });
        const cl = t.compute_lineage;
        if (!st.gates.lineage && cl && cl.ran && (num(cl.figures) || num(cl.receipts))) reduce(st, 'compute_lineage', { details: { receipted: cl.receipts, unreceipted: Array.isArray(cl.unreceipted) ? cl.unreceipted.length : cl.unreceipted }, src: cl });
    }

    // ── RELOAD PARITY: the same chrome from the persisted turn, through the same reducer ──
    function replayTurn(st, turn) {
        const feed = (list) => (Array.isArray(list) ? list : []).forEach(e => { if (e && e.type) { try { reduce(st, e.type, e); } catch (_) { /* one bad record never hides the rest */ } } });
        const cw = turn.context_window_snapshot_event;
        if (cw) reduce(st, 'context_window_snapshot', { details: { modules: cw.modules, contributions: cw.contributions }, src: cw });
        const kg = turn.kg_enrichment_event;
        if (kg && (kg.total_entities || (Array.isArray(kg.enrichments) && kg.enrichments.length))) reduce(st, 'kg_enrichment', { details: kg });
        const kr = turn.knowledge_retrieval_event;
        if (kr && kr.retrieved !== false && kr.document_count != null) reduce(st, 'knowledge_retrieval', { details: { document_count: kr.document_count, duration_ms: kr.duration_ms }, src: kr });
        // a persisted turn keeps the champion it was guided by (`case_id`) and how many matched — the same row as live
        if (turn.case_id) reduce(st, 'rag_retrieval', { details: { case_id: turn.case_id, count: turn.rag_case_count }, src: turn });
        (Array.isArray(turn.execution_trace) ? turn.execution_trace : []).forEach(e => {
            const a = (e && e.action) || {}; const tn = a.tool_name; const args = a.arguments || {};
            if (tn === 'TDA_SystemLog') {
                // the trace renders the plan card from this entry's `arguments.details` — that is its carrier
                if (args.message === 'Strategic Meta-Plan Generated' && Array.isArray(args.details)) reduce(st, 'plan_generated', { details: { phases: args.details }, src: args.details });
                return;
            }
            if (!tn || tn === 'TDA_FinalReport') return;
            // the trace renders the intent card from `action` and the result card from `result`
            reduce(st, 'tool_intent', { details: { tool_name: tn, arguments: args }, src: a });
            const r = (e && e.result) || {};
            reduce(st, 'tool_result', { details: { status: r.status, row_count: r.metadata && r.metadata.row_count, results: r.results }, src: r });
        });
        feed(turn.conversation_agent_events);
        feed(turn.genie_events);
        (Array.isArray(turn.steers) ? turn.steers : []).forEach(s => reduce(st, 'steer_consumed', { details: { at: s && s.at }, src: s }));
        const res = turn.resilience;
        if (res && res.served_by) reduce(st, 'llm_recovery', { details: { kind: 'fallback', to: res.served_by }, src: res });
        reduceStamps(st, turn);
        const oc = turn.outcome; if (oc && (oc.revision || oc.revisions)) reduce(st, 'outcome_revision', { details: { revision: num(oc.revision || oc.revisions), cleared: oc.outcome === 'cleared' || !!oc.cleared }, src: oc });
        st.chips.forEach(c => { if (c.running) { c.running = false; c.fact = c.fact || 'done'; } });
    }
    /**
     * Write the receipt's keys onto a persisted turn's records WITHOUT rendering anything — the
     * Live Status reload renders its cards from these same objects, so a card rendered from a
     * marked record wears the key the receipt's row was given for it (through the ONE reducer,
     * so the two cannot disagree). Idempotent; never throws.
     */
    function stampTurn(turn) {
        try {
            if (!turn || typeof turn !== 'object') return false;
            const st = fresh('__stamp'); st.restored = true;
            replayTurn(st, turn);
            return true;
        } catch (_) { return false; }
    }
    /**
     * Rebuild the settled chrome on a message rendered from history. `turn` is the persisted
     * workflow_history entry of that message's turn. Idempotent; never throws.
     */
    function restore(bubble, turn, opts) {
        try {
            if (!bubble || !turn || optedOut()) return false;
            if (bubble.querySelector('.la-line')) return true;
            const contentEl = bubble.querySelector('.message-content'); if (!contentEl) return false;
            const box = contentEl.parentNode;
            const st = fresh((opts && opts.sessionId) || turn.session_id || cur());
            st.restored = true; st.settled = true; st.folded = true;
            st.turnId = turn.turn != null ? turn.turn : null;
            st.cls = turn.profile_type || null; st.tag = turn.profile_tag || null;
            st.partial = !!turn.is_partial;
            st.t1 = st.t0 + num(turn.duration_ms);
            st.tokens = num(turn.turn_input_tokens) + num(turn.turn_output_tokens);
            st.el = bubble;
            box.classList.add('la-box', 'la-settled');
            const tmp = document.createElement('div'); tmp.innerHTML = chromeHtml();
            const nodes = [...tmp.children];
            nodes.forEach(n => box.insertBefore(n, contentEl));
            replayTurn(st, turn);
            orderGateRows(st);
            st.chips.forEach(c => paintChip(st, c, true));
            st.phase = 'seal';
            st.sealed = !!((Array.isArray(turn.provenance_chain) && turn.provenance_chain.length) || (turn.integrity && turn.integrity.ran));
            paintLine(st, true);
            wireLine(st);
            paintTrust(st, true);
            return true;
        } catch (_) { return false; }
    }

    window.LivingAnswer = { enabled, setEnabled, onTaskStart, active, contentHost, onEvent, onNotification, firstToken, renderText, stableText, textDone, settle, stopping, ended, restore, stampTurn, panelTurn, turnStamp, verdictOf, following,
        _phases: PHASES, _toolWords: toolWords, _live, _fresh: fresh, _replay: replayTurn, _sqlTables: sqlTables };
})();
