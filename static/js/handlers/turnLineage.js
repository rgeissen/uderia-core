/**
 * Turn Lineage — S8 "Regenerate as an Experiment" (plain script, `window.TurnLineage`).
 *
 * Three surfaces, one read (the session's served `lineage`):
 *   · the EXPERIMENT card under an adopted answer — every sibling answer to the same question,
 *     side by side, each with its verdict strip (the Scorecard's bands, never re-derived), its
 *     agent, its cost, one Adopt per column; the non-adopted ones collapsed to a stub;
 *   · Regenerate ▾ on an answer (same agent, or another activated one) and Edit on a question —
 *     the acts, submitted through the ONE stream path (`window.__submitLineage`, owned by
 *     eventHandlers) so Live Status renders the run like any turn;
 *   · the BRANCH switch on an edited question — "1 other branch", append-only.
 *
 * Nothing here decides which answer is better: it shows the verdicts and lets the reader adopt.
 * Every act re-reads the session from the server (the one source of lineage) rather than
 * patching bubbles by hand. Absent lineage ⇒ nothing mounts ⇒ byte-identical chat.
 */
(function () {
    'use strict';
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const hdrs = () => ({ 'Authorization': `Bearer ${localStorage.getItem('tda_auth_token')}`, 'Content-Type': 'application/json' });
    const api = async (path, opts = {}) => {
        const r = await fetch('/api/v1' + path, { headers: hdrs(), ...opts });
        let j = null; try { j = await r.json(); } catch (_) { /* no body */ }
        if (!r.ok) throw new Error((j && (j.error || j.message)) || `HTTP ${r.status}`);
        return j;
    };
    const ICON = {
        flask: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 3h6M10 3v6L4 20a1 1 0 0 0 .9 1.5h14.2A1 1 0 0 0 20 20l-6-11V3"/></svg>',
        redo: '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 3v6h-6"/></svg>',
        edit: '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>',
        branch: '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 3v12"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/></svg>',
        check: '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>',
    };
    const reduceMotion = () => window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // ── the ONE verdict-mark builder: the Agents lens's, never a second grammar ────────────
    function mark(band) {
        const ap = window.agentPerformance;
        if (ap && ap.mark) { if (ap.ensureCss) ap.ensureCss(); return ap.mark(band); }
        return `<span class="tl-sq-fallback" data-band="${esc(band)}">${esc(band === 'healthy' ? '●' : band === 'review' ? '◐' : band === 'danger' ? '○' : '·')}</span>`;
    }
    const BAND_WORD = { healthy: 'quiet', review: 'worth a look', danger: 'needs you now' };

    /** The strip + its words from the Scorecard's persisted verdicts — nothing re-derived. */
    function stripHtml(score) {
        if (!score || !score.scored) return { html: `<span class="tl-strip tl-strip-pending" aria-label="not scored yet">not scored yet</span>`, word: 'not scored yet', level: 'na' };
        const vs = (score.verdicts || []).filter((v) => v && v.dimension);
        const order = { danger: 0, review: 1, healthy: 2 };
        const banded = vs.map((v) => ({ d: v.dimension, b: v.band || (v.passed === false ? 'danger' : v.passed === true ? 'healthy' : 'na') }));
        banded.sort((a, b) => (order[a.b] ?? 3) - (order[b.b] ?? 3));
        const counts = { danger: 0, review: 0, healthy: 0 };
        for (const x of banded) if (counts[x.b] != null) counts[x.b] += 1;
        const level = counts.danger ? 'danger' : counts.review ? 'review' : 'healthy';
        const word = counts.danger ? `${counts.danger} needs you now` : counts.review ? `${counts.review} worth a look` : `all ${counts.healthy} checks quiet`;
        const marks = banded.map((x) => `<span class="tl-mark u-tip" data-tip="${esc(x.d.replace(/_/g, ' '))} · ${esc(BAND_WORD[x.b] || 'not applicable')}">${mark(x.b)}</span>`).join('');
        return { html: `<span class="tl-strip" role="img" aria-label="verdict strip: ${esc(word)}">${marks}</span>`, word, level };
    }

    const scoreCache = new Map();  // `${sid}:${turn}` → score payload
    async function fetchScore(sid, turn, force) {
        const k = `${sid}:${turn}`;
        if (!force && scoreCache.has(k)) return scoreCache.get(k);
        try {
            const j = await api(`/evals/turn-score?session_id=${encodeURIComponent(sid)}&turn=${encodeURIComponent(turn)}`);
            scoreCache.set(k, j); return j;
        } catch (_) { return null; }
    }

    const tagChip = (tag, type) => {
        if (!tag) return '';
        if (window.renderProfileTag) return window.renderProfileTag(tag, type || 'llm_only', 'profile-tag--sm');
        return `<span class="tl-tag">@${esc(tag)}</span>`;
    };
    const profileTypeOf = (tag) => (window.configState?.profiles || []).find((p) => p.tag === tag)?.profile_type || null;
    const fmtCost = (c) => (c == null ? '' : `$${Number(c).toFixed(4)}`);
    const fmtMs = (ms) => (ms == null ? '' : ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`);

    // The server is the one source of lineage: every act re-reads the session through the
    // same load the sidebar uses (sessionManagement publishes it), never a hand-patched bubble.
    async function reloadSession(sid) {
        try {
            if (typeof window.__reloadSession === 'function') await window.__reloadSession(sid);
            else console.warn('[lineage] no session reloader published');
        } catch (e) { console.warn('[lineage] reload failed', e); }
    }

    // ── the experiment card ────────────────────────────────────────────────────────────────
    /**
     * @param bubble  the ADOPTED answer's bubble (already rendered by the reload loop)
     * @param root    the turn carrying the question
     * @param set     lineage.siblings[root] — {adopted, turns:[{turn, adopted, profile_tag, model, provider, in_view}]}
     * @param ctx     {sessionId, history, workflowHistory}
     */
    function mountExperiment(bubble, root, set, ctx) {
        if (!bubble || !set || !Array.isArray(set.turns) || set.turns.length < 2) return;
        if (bubble.querySelector('.tl-experiment')) return;
        const sid = ctx.sessionId;
        const recs = new Map((ctx.workflowHistory || []).map((t) => [t.turn, t]));
        const msgs = new Map((ctx.history || []).filter((m) => m.role === 'assistant' && Number.isInteger(m.turn_number)).map((m) => [m.turn_number, m]));
        const members = set.turns.filter((t) => t.in_view !== false);
        const adopted = set.adopted;
        const sec = document.createElement('section');
        sec.className = 'tl-experiment mt-2';
        sec.setAttribute('aria-label', `Answer experiment for turn ${root}: ${members.length} answers to the same question`);
        sec.dataset.root = String(root);
        const rows = members.map((t, i) => {
            const rec = recs.get(t.turn) || {};
            const isA = t.turn === adopted;
            const metaText = `${fmtCost(rec.turn_cost)}${rec.duration_ms ? ` · ${fmtMs(rec.duration_ms)}` : ''}${rec.turn_input_tokens ? ` · ${Number(rec.turn_input_tokens).toLocaleString()} in` : ''}`;
            return `<div class="tl-row${isA ? ' is-adopted' : ''}" role="listitem" tabindex="${i === 0 ? '0' : '-1'}" data-turn="${t.turn}" aria-label="answer ${t.turn}${isA ? ', adopted' : ''}">
                <span class="tl-cell tl-who">${tagChip(t.profile_tag, profileTypeOf(t.profile_tag))}<span class="tl-model u-tip" data-tip="${esc(t.provider || 'provider not recorded')}">${esc(t.model || '')}</span></span>
                <span class="tl-cell tl-score" data-score-host="${t.turn}"><span class="tl-strip tl-strip-pending">scoring…</span></span>
                <span class="tl-cell tl-meta u-tip" data-tip="${esc(metaText)}">${esc(metaText)}</span>
                <span class="tl-cell tl-acts">${isA
                    ? `<span class="tl-adopted-badge u-tip" data-tip="the conversation continues from this answer">${ICON.check} adopted</span>`
                    : `<button type="button" class="tl-btn tl-adopt" data-turn="${t.turn}" aria-label="Adopt answer ${t.turn}">Adopt</button>`}
                    <button type="button" class="tl-btn tl-show" data-turn="${t.turn}" aria-expanded="false" aria-label="${isA ? 'Compare with another answer' : `Show answer ${t.turn}`}">${isA ? 'Compare' : 'Show'}</button>
                </span>
            </div>`;
        }).join('');
        sec.innerHTML = `<header class="tl-head">
                <span class="tl-title">${ICON.flask} Experiment · ${members.length} answers to this question</span>
                <span class="tl-sub">adopted: answer ${esc(adopted)} — compare by their verdicts, then adopt the one the conversation should continue from</span>
            </header>
            <div class="tl-rows" role="list" aria-label="answers">${rows}</div>
            <div class="tl-columns hidden" aria-live="polite"></div>`;
        const container = bubble.querySelector('.message-content')?.parentElement || bubble;
        container.appendChild(sec);

        // the strips (async, each from the Scorecard's own record)
        const fill = async (force) => {
            for (const t of members) {
                const host = sec.querySelector(`[data-score-host="${t.turn}"]`);
                if (!host) continue;
                const s = await fetchScore(sid, t.turn, force);
                const st = stripHtml(s);
                host.innerHTML = `${st.html}<span class="tl-word tl-${st.level}">${esc(st.word)}</span>`;
            }
        };
        fill(false);
        sec._refill = () => fill(true);

        // columns: adopted | the shown one (two-column card, shared strip semantics)
        const columns = sec.querySelector('.tl-columns');
        const colHtml = (turn) => {
            const m = msgs.get(turn); const t = members.find((x) => x.turn === turn) || {};
            const host = sec.querySelector(`[data-score-host="${turn}"]`);
            return `<div class="tl-col${turn === adopted ? ' is-adopted' : ''}" data-turn="${turn}">
                <div class="tl-col-head">${tagChip(t.profile_tag, profileTypeOf(t.profile_tag))}<span class="tl-col-n">answer ${turn}${turn === adopted ? ' · adopted' : ''}</span>${host ? host.innerHTML : ''}</div>
                <div class="tl-col-body">${m ? m.content : '<em>the text of this answer is not in the record</em>'}</div>
                ${turn === adopted ? '' : `<button type="button" class="tl-btn tl-adopt tl-adopt-col" data-turn="${turn}">Adopt this answer</button>`}
            </div>`;
        };
        let shown = null;
        const toggleShow = (turn) => {
            const btn = sec.querySelector(`.tl-show[data-turn="${turn}"]`);
            if (shown === turn) { shown = null; columns.classList.add('hidden'); columns.innerHTML = ''; sec.querySelectorAll('.tl-show').forEach((b) => b.setAttribute('aria-expanded', 'false')); return; }
            shown = turn;
            const other = turn === adopted ? members.find((x) => x.turn !== adopted)?.turn : turn;
            columns.innerHTML = `${colHtml(adopted)}${other != null ? colHtml(other) : ''}`;
            columns.classList.remove('hidden');
            if (!reduceMotion()) columns.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 160 });
            sec.querySelectorAll('.tl-show').forEach((b) => b.setAttribute('aria-expanded', String(b === btn)));
        };
        sec.addEventListener('click', async (e) => {
            const show = e.target.closest('.tl-show'); if (show) { toggleShow(Number(show.dataset.turn)); return; }
            const adopt = e.target.closest('.tl-adopt'); if (adopt) { await adoptTurn(sid, Number(adopt.dataset.turn), adopt); }
        });
        // keyboard: arrows move between answers (the rows are the columns' keyboard form)
        sec.addEventListener('keydown', (e) => {
            const rowsEl = [...sec.querySelectorAll('.tl-row')];
            const i = rowsEl.indexOf(document.activeElement);
            if (i < 0) return;
            let j = null;
            if (e.key === 'ArrowRight' || e.key === 'ArrowDown') j = (i + 1) % rowsEl.length;
            else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') j = (i - 1 + rowsEl.length) % rowsEl.length;
            else if (e.key === 'Home') j = 0; else if (e.key === 'End') j = rowsEl.length - 1;
            else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleShow(Number(rowsEl[i].dataset.turn)); return; }
            if (j == null) return;
            e.preventDefault();
            rowsEl.forEach((r, k) => r.setAttribute('tabindex', k === j ? '0' : '-1'));
            rowsEl[j].focus();
        });
    }

    async function adoptTurn(sid, turn, btn) {
        if (btn) { btn.disabled = true; btn.textContent = 'Adopting…'; }
        try {
            await api(`/sessions/${encodeURIComponent(sid)}/turns/${turn}/adopt`, { method: 'POST', body: '{}' });
            if (window.showAppBanner) window.showAppBanner(`Answer ${turn} adopted — the conversation continues from it.`, 'success', 3000);
            await reloadSession(sid);
        } catch (e) {
            if (btn) { btn.disabled = false; btn.textContent = 'Adopt'; }
            if (window.showAppBanner) window.showAppBanner(`Could not adopt: ${e.message}`, 'error', 5000);
        }
    }

    // ── the branch switch on a question ───────────────────────────────────────────────────
    function mountBranchSwitch(userBubble, turn, lineage, sid) {
        const br = lineage && lineage.branches; if (!br || !Array.isArray(br.heads) || !br.heads.length) return;
        if (userBubble.querySelector('.tl-branch')) return;
        const active = Number(br.active || 0);
        // (a) this question is a branch HEAD (an edited message): offer the version it replaced
        const head = br.heads.find((h) => h.turn === turn);
        // (b) this question was EDITED into other branch(es) that are not active: offer them
        const edits = br.heads.filter((h) => h.of === turn && h.turn !== active);
        if (!head && !edits.length) return;
        const el = document.createElement('div');
        el.className = 'tl-branch';
        const parts = [];
        if (head) parts.push(`<span class="tl-branch-note">${ICON.branch} edited question · this branch</span><button type="button" class="tl-btn tl-switch" data-head="${Number(head.parent || 0)}" aria-label="Switch to the version before the edit">Show the original</button>`);
        for (const h of edits) parts.push(`<span class="tl-branch-note">${ICON.branch} ${edits.length === 1 ? '1 other branch' : `${edits.length} other branches`}</span><button type="button" class="tl-btn tl-switch" data-head="${h.turn}" aria-label="Switch to the edited version">Show the edited version</button>`);
        el.innerHTML = parts.join('');
        const container = userBubble.querySelector('.message-content')?.parentElement || userBubble;
        container.appendChild(el);
        el.addEventListener('click', async (e) => {
            const b = e.target.closest('.tl-switch'); if (!b) return;
            b.disabled = true;
            try { await api(`/sessions/${encodeURIComponent(sid)}/branches/${Number(b.dataset.head)}/activate`, { method: 'POST', body: '{}' }); await reloadSession(sid); }
            catch (err) { b.disabled = false; if (window.showAppBanner) window.showAppBanner(`Could not switch branch: ${err.message}`, 'error', 5000); }
        });
    }

    // ── the acts on every bubble: Regenerate ▾ on an answer, Edit on a question ───────────
    let _enabled = null;   // the deployment's switch, learned from the first lineage read
    function setEnabled(v) { _enabled = !!v; }
    function submit(detail) {
        if (typeof window.__submitLineage === 'function') return window.__submitLineage(detail);
        if (window.showAppBanner) window.showAppBanner('The conversation module is not ready to regenerate yet.', 'warning', 3000);
    }
    let _activated = null;
    async function activatedProfiles() {
        if (_activated) return _activated;
        try {
            const j = await api('/profiles/active_for_consumption');
            const ids = new Set(j.profile_ids || []);
            const all = window.configState?.profiles || [];
            _activated = all.filter((p) => ids.has(p.id));
        } catch (_) { _activated = (window.configState?.profiles || []).slice(0, 12); }
        return _activated;
    }
    function turnOfAssistant(bubble) {
        const own = bubble.dataset.lineageTurn; if (own) return Number(own);
        const b = bubble.querySelector('.assistant-badge[data-turn-id], [data-turn-id]'); return b ? Number(b.dataset.turnId) : null;
    }
    function rootOfUser(bubble) {
        const own = bubble.dataset.lineageTurn; if (own) return Number(own);
        const b = bubble.querySelector('[data-turn-id]'); return b ? Number(b.dataset.turnId) : null;
    }
    function injectActs(bubble) {
        if (_enabled !== true || bubble.dataset.tlDone) return;
        const isUser = !!bubble.querySelector('.user-avatar');
        if (isUser) {
            const turn = rootOfUser(bubble); if (!turn) return;
            if (bubble.querySelector('.primer-tag') || /primer/i.test(bubble.className)) return;
            bubble.dataset.tlDone = '1';
            const wrap = document.createElement('div'); wrap.className = 'tl-user-acts';
            wrap.innerHTML = `<button type="button" class="tl-act tl-edit" aria-label="Edit this question and resend it as a branch">${ICON.edit} Edit</button>`;
            (bubble.querySelector('.message-content')?.parentElement || bubble).appendChild(wrap);
            wrap.querySelector('.tl-edit').addEventListener('click', () => openEditor(bubble, turn));
            return;
        }
        const turn = turnOfAssistant(bubble); if (!turn) return;
        bubble.dataset.tlDone = '1';
        const wrap = document.createElement('details'); wrap.className = 'tl-menu';
        wrap.innerHTML = `<summary class="tl-act" aria-label="Regenerate this answer, same or another agent">${ICON.redo} Regenerate <span aria-hidden="true">▾</span></summary><div class="tl-menu-list" role="menu"></div>`;
        const container = bubble.querySelector('.message-content')?.parentElement || bubble;
        const slot = container.querySelector('.msg-actions-row .msg-actions-slot');
        (slot || container).appendChild(wrap);
        wrap.addEventListener('toggle', async () => {
            if (!wrap.open) return;
            const list = wrap.querySelector('.tl-menu-list');
            list.innerHTML = `<button type="button" class="tl-menu-item" role="menuitem" data-pid="">Same agent</button><div class="tl-menu-sep" role="separator"></div><div class="tl-menu-hint">…or another agent</div>`;
            const ps = await activatedProfiles();
            for (const p of ps) {
                const b = document.createElement('button'); b.type = 'button'; b.className = 'tl-menu-item'; b.setAttribute('role', 'menuitem'); b.dataset.pid = p.id;
                b.innerHTML = `${tagChip(p.tag, p.profile_type)}<span class="tl-menu-name">${esc(p.name || '')}</span>`;
                list.appendChild(b);
            }
            list.querySelector('.tl-menu-item')?.focus();
        });
        wrap.addEventListener('click', (e) => {
            const it = e.target.closest('.tl-menu-item'); if (!it) return;
            wrap.open = false;
            submit({ regenerate_of: turn, profile_override_id: it.dataset.pid || null });
        });
        wrap.addEventListener('keydown', (e) => { if (e.key === 'Escape') { wrap.open = false; wrap.querySelector('summary').focus(); } });
    }
    function openEditor(bubble, turn) {
        const content = bubble.querySelector('.message-content'); if (!content || bubble.querySelector('.tl-editor')) return;
        const original = bubble.dataset.lineageQuestion || content.innerText.trim();
        const ed = document.createElement('form'); ed.className = 'tl-editor';
        ed.innerHTML = `<label class="tl-editor-label" for="tl-edit-${turn}">Edit and resend — the conversation branches here; the original stays in the record</label>
            <textarea id="tl-edit-${turn}" class="tl-editor-text" rows="3"></textarea>
            <div class="tl-editor-acts"><button type="submit" class="tl-btn tl-primary">Resend as a branch</button><button type="button" class="tl-btn tl-cancel">Cancel</button></div>`;
        ed.querySelector('textarea').value = original;
        content.after(ed); ed.querySelector('textarea').focus();
        const close = () => { ed.remove(); try { const b = bubble.querySelector('.tl-edit'); if (b) b.focus(); } catch (_) { /* no-op */ } };
        ed.querySelector('.tl-cancel').addEventListener('click', close);
        ed.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
        ed.addEventListener('submit', (e) => {
            e.preventDefault();
            const text = ed.querySelector('textarea').value.trim();
            if (!text || text === original.trim()) { ed.remove(); return; }
            ed.remove();
            submit({ edit_of: turn, prompt: text });
        });
    }
    // `r` on a focused answer regenerates it (same agent)
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'r' || e.metaKey || e.ctrlKey || e.altKey) return;
        const t = e.target; if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
        const bubble = t && t.closest ? t.closest('.message-bubble') : null;
        if (!bubble || bubble.querySelector('.user-avatar') || _enabled !== true) return;
        const turn = turnOfAssistant(bubble); if (!turn) return;
        e.preventDefault(); submit({ regenerate_of: turn, profile_override_id: null });
    });

    function scan(rootEl) {
        (rootEl.querySelectorAll ? rootEl.querySelectorAll('.message-bubble') : []).forEach(injectActs);
        if (rootEl.classList && rootEl.classList.contains('message-bubble')) injectActs(rootEl);
    }
    function start() {
        const log = document.getElementById('chat-log');
        if (!log) { setTimeout(start, 1500); return; }
        scan(log);
        new MutationObserver((muts) => { for (const m of muts) for (const n of m.addedNodes) if (n.nodeType === 1) scan(n); }).observe(log, { childList: true, subtree: true });
    }
    // a fresh score for a sibling refreshes its strip in place
    UderiaBus.on('uderia:turn-scored', (e) => {
        const d = e.detail || {}; scoreCache.delete(`${d.session_id}:${d.turn}`);
        document.querySelectorAll('.tl-experiment').forEach((sec) => { if (sec._refill) sec._refill(); });
    });
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();

    window.TurnLineage = { mountExperiment, mountBranchSwitch, setEnabled, stripHtml, adoptTurn, _internals: { fetchScore, activatedProfiles } };
})();
