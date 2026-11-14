/**
 * filesKeep — "Keep" in the conversation (PROP-010 §4.7.2, surfaces 1 and 3).
 *
 *   · a footer act on every answer table and chart row, beside "Pin to dashboard"
 *     (answer grammar `.ans-tf-r .ans-act`), and "Keep answer" in the answer's action row;
 *   · revealed only when the Files component is enabled for the agent that ANSWERED
 *     (ComponentGate, the same gate as Pin);
 *   · the button reads "Kept · Open" when the server already holds that part of that turn —
 *     derived from GET /v1/files/kept, never from a click-time flag, so it is still true after a
 *     reload, in another tab, and after a REST keep (`UderiaBus.onComponent('files')` re-reads it);
 *   · keeping the same content twice offers the existing file first ("Already kept … · Open ·
 *     Keep another copy") — the server matches the content fingerprint.
 *
 * The table and chart CSV are built by the SERVER from the persisted answer; only a chart's PNG
 * comes from this browser (charts render client-side). Plain script; `window.FilesKeep`.
 */
(function () {
    'use strict';
    if (typeof document === 'undefined') return;

    const tok = () => localStorage.getItem('tda_auth_token');
    const hdrs = () => ({ 'Content-Type': 'application/json', ...(tok() ? { Authorization: `Bearer ${tok()}` } : {}) });
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const KEPT = new Map();   // "sid:turn" -> Promise<Map(anchor -> kept record)>

    function openFiles(opts) {
        return import('/api/v1/components/files/renderer')
            .then(m => m.openFilesPanel(opts || {}))
            .catch(err => console.error('[Files] the Files panel could not be opened', err));
    }

    function origin(el) {
        if (typeof window.__answerOrigin === 'function') {
            try { return window.__answerOrigin(el); } catch (_) { /* fall through */ }
        }
        const bubble = el.closest('.message-bubble');
        return {
            sessionId: el.closest('[data-session-id]')?.dataset?.sessionId || window.__currentSessionId || '',
            turnId: bubble?.querySelector('[data-turn-id]')?.dataset?.turnId || '',
            profileId: window.__currentProfileId || '',
        };
    }

    function gate(profileId) {
        return window.ComponentGate ? window.ComponentGate.isEnabled('files', profileId) : Promise.resolve(false);
    }

    function keptFor(sid, turn) {
        const k = `${sid}:${turn}`;
        if (!KEPT.has(k)) {
            KEPT.set(k, fetch(`/api/v1/files/kept?session_id=${encodeURIComponent(sid)}&turn_id=${encodeURIComponent(turn)}`, { headers: hdrs() })
                .then(r => (r.ok ? r.json() : { kept: [] }))
                .then(d => {
                    const m = new Map();
                    (d.kept || []).forEach(x => { if (x.anchor) m.set(x.anchor, x); });
                    return m;
                })
                .catch(() => new Map()));
        }
        return KEPT.get(k);
    }

    // ── the parts of an answer that can be kept ──────────────────────────────
    function tableOf(footer) {
        let el = footer.previousElementSibling;
        while (el && !el.classList.contains('ans-tw')) el = el.previousElementSibling;
        return el ? el.querySelector('table') : null;
    }

    function chartOf(footer) {
        const row = footer.closest('.ans-ev');
        if (!row) return null;
        return row.querySelector('[data-component-id="chart"], .chart-render-target');
    }

    function partOf(footer, content) {
        const table = tableOf(footer);
        if (table) {
            const all = Array.from(content.querySelectorAll('table'));
            const headers = Array.from(table.querySelectorAll('thead th')).map(th => th.textContent.trim());
            return { kind: 'table', anchor: `table:${Math.max(0, all.indexOf(table))}`, headers };
        }
        const chart = chartOf(footer);
        if (chart) {
            const all = Array.from(content.querySelectorAll('[data-component-id="chart"], .chart-render-target'));
            return { kind: 'chart', anchor: `chart:${Math.max(0, all.indexOf(chart))}`, el: chart };
        }
        return null;
    }

    function chartPng(el) {
        try {
            const canvas = el && el.querySelector('canvas');
            return canvas ? canvas.toDataURL('image/png') : null;
        } catch (_) { return null; }   // a tainted canvas: the CSV is still kept
    }

    // ── button states ────────────────────────────────────────────────────────
    function setKept(btn, rec) {
        btn.dataset.state = 'kept';
        btn.dataset.fileId = rec.id || '';
        btn.textContent = btn.dataset.whole ? 'Answer kept · Open' : 'Kept · Open';
        btn.setAttribute('aria-label', `Kept as ${rec.filename || 'a file'} — open it in Files`);
        btn.classList.add('fk-kept');
    }

    function setIdle(btn) {
        btn.dataset.state = 'idle';
        delete btn.dataset.fileId;
        btn.textContent = btn.dataset.whole ? 'Keep answer' : 'Keep';
        btn.setAttribute('aria-label', btn.dataset.whole ? 'Keep this answer in Files' : 'Keep this in Files');
        btn.classList.remove('fk-kept');
    }

    function say(btn, text) {
        let live = btn.parentElement && btn.parentElement.querySelector('.fk-live');
        if (!live && btn.parentElement) {
            live = document.createElement('span');
            live.className = 'fk-live';
            live.setAttribute('aria-live', 'polite');
            live.style.cssText = 'position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)';
            btn.parentElement.appendChild(live);
        }
        if (live) live.textContent = text;
    }

    function offerDuplicate(btn, existing, retry) {
        btn.parentElement?.querySelector('.fk-dup')?.remove();
        const wrap = document.createElement('span');
        wrap.className = 'fk-dup';
        wrap.style.cssText = 'display:inline-flex;align-items:center;gap:8px;font-size:inherit;color:var(--text-secondary);margin-right:8px';
        wrap.innerHTML = `<span>Already kept as ${esc(existing.filename)}</span>
            <button type="button" class="ans-act fk-dup-open">Open</button>
            <button type="button" class="ans-act fk-dup-copy">Keep another copy</button>`;
        btn.parentElement?.insertBefore(wrap, btn);
        wrap.querySelector('.fk-dup-open').addEventListener('click', () => { wrap.remove(); openFiles({ focus: existing.id }); });
        wrap.querySelector('.fk-dup-copy').addEventListener('click', () => { wrap.remove(); retry(true); });
        say(btn, `This was already kept as ${existing.filename}.`);
    }

    async function keep(btn, part, allowDuplicate) {
        if (btn.dataset.state === 'kept') { openFiles({ focus: btn.dataset.fileId }); return; }
        if (btn.dataset.busy) return;
        const o = origin(btn);
        if (!o.sessionId || !o.turnId) { say(btn, 'This answer is not saved yet — try again in a moment.'); return; }
        const body = { session_id: o.sessionId, turn_id: parseInt(o.turnId, 10), kind: part.kind, anchor: part.anchor,
                       headers: part.headers, allow_duplicate: !!allowDuplicate };
        if (part.kind === 'chart') { const png = chartPng(part.el); if (png) body.data_base64 = png; }
        btn.dataset.busy = '1';
        btn.textContent = 'Keeping…';
        try {
            const r = await fetch('/api/v1/files/keep', { method: 'POST', headers: hdrs(), body: JSON.stringify(body) });
            const d = await r.json().catch(() => ({}));
            if (r.status === 409 && d.code === 'duplicate' && d.file) {
                setIdle(btn);
                offerDuplicate(btn, d.file, (again) => keep(btn, part, again));
                return;
            }
            if (!r.ok) {
                setIdle(btn);
                say(btn, d.message || 'This could not be kept.');
                if (typeof window.showAppBanner === 'function') window.showAppBanner(d.message || 'This could not be kept.', 'warning');
                return;
            }
            const first = (d.files || [])[0] || {};
            setKept(btn, first);
            KEPT.delete(`${o.sessionId}:${o.turnId}`);
            const n = (d.files || []).length;
            say(btn, n > 1 ? `Kept ${n} files: ${d.files.map(f => f.filename).join(' and ')}.` : `Kept as ${first.filename}.`);
        } catch (_) {
            setIdle(btn);
            say(btn, 'This could not be kept.');
        } finally {
            delete btn.dataset.busy;
        }
    }

    // ── wiring ───────────────────────────────────────────────────────────────
    function makeBtn(whole) {
        const b = document.createElement('button');
        b.type = 'button';
        b.hidden = true;
        if (whole) {
            b.dataset.whole = '1';
            b.className = 'fk-keep-answer text-[11px]';
            b.style.cssText = 'color:var(--text-muted);background:none;border:none;cursor:pointer;padding:2px 0;margin-left:12px';
        } else {
            b.className = 'ans-act fk-keep';
        }
        setIdle(b);
        return b;
    }

    async function syncStates(bubble) {
        const o = origin(bubble.querySelector('.message-content') || bubble);
        if (!o.sessionId || !o.turnId) return;
        const kept = await keptFor(o.sessionId, o.turnId);
        bubble.querySelectorAll('.fk-keep, .fk-keep-answer').forEach(btn => {
            if (btn.dataset.busy) return;
            const rec = kept.get(`${o.turnId}:${btn.dataset.anchor}`);
            if (rec) setKept(btn, rec); else if (btn.dataset.state === 'kept') setIdle(btn);
        });
    }

    function wire(bubble) {
        if (!bubble) return;
        if (bubble.querySelector('.attachment-chip[data-file-id]')) wireLogChips(bubble);
        if (bubble.querySelector('.user-avatar')) return;
        const content = bubble.querySelector('.message-content');
        if (!content) return;
        const added = [];
        content.querySelectorAll('.ans-tf-r').forEach(footerR => {
            if (footerR.querySelector('.fk-keep')) return;
            const footer = footerR.closest('.ans-tf');
            const part = footer ? partOf(footer, content) : null;
            if (!part) return;
            const btn = makeBtn(false);
            btn.dataset.anchor = part.anchor;
            btn.addEventListener('click', () => keep(btn, partOf(footer, content) || part, false));
            footerR.appendChild(btn);
            added.push(btn);
        });
        const slot = bubble.querySelector('.msg-actions-row .msg-actions-slot');
        if (slot && !slot.querySelector('.fk-keep-answer')) {
            const btn = makeBtn(true);
            btn.dataset.anchor = 'answer';
            btn.addEventListener('click', () => keep(btn, { kind: 'answer', anchor: 'answer' }, false));
            slot.appendChild(btn);
            added.push(btn);
        }
        if (!added.length && !bubble.querySelector('.fk-keep, .fk-keep-answer')) return;
        const o = origin(content);
        gate(o.profileId).then(ok => {
            bubble.querySelectorAll('.fk-keep, .fk-keep-answer').forEach(b => { b.hidden = !ok; });
            if (ok) syncStates(bubble);
        });
    }

    const pending = new Set();
    let raf = 0;
    function schedule(bubble) {
        if (!bubble) return;
        pending.add(bubble);
        if (raf) return;
        raf = requestAnimationFrame(() => {
            raf = 0;
            const list = [...pending];
            pending.clear();
            list.forEach(wire);
        });
    }

    function css() {
        if (document.getElementById('files-keep-css')) return;
        const st = document.createElement('style');
        st.id = 'files-keep-css';
        // a hidden act (Pin when the dashboard is off) must not leave its separator behind
        st.textContent = '.ans-tf-r .ans-act[hidden] + .ans-act::before{content:none}'
            + '.ans-tf-r .ans-act[hidden]{display:none !important}'
            + '.fk-keep-answer:hover,.fk-keep-answer:focus-visible{color:var(--text-primary) !important;text-decoration:underline}'
            + '.fk-kept{color:var(--text-primary)}'
            // the Save-to-Files offer that follows an export
            // top centre, below the navigation bar — where the platform's floating toast sits; never over the composer
            + '#files-export-offer{position:fixed;left:50%;top:64px;transform:translate(-50%,-8px);opacity:0;z-index:80;'
            + 'display:flex;align-items:center;gap:10px;max-width:min(560px,calc(100vw - 32px));padding:10px 10px 10px 14px;'
            + 'background:var(--bg-overlay,var(--card-bg));color:var(--text-primary);border:1px solid var(--border-primary);'
            + 'border-radius:10px;box-shadow:0 12px 32px rgba(0,0,0,.28);font-size:13px;transition:opacity .18s,transform .18s}'
            + '#files-export-offer.is-on{opacity:1;transform:translate(-50%,0)}'
            + '#files-export-offer .fko-t{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}'
            + '#files-export-offer .fko-t b{font-weight:600}'
            + '#files-export-offer .fko-i{color:var(--text-muted);flex-shrink:0;display:flex}'
            + '@media (prefers-reduced-motion: reduce){#files-export-offer{transition:none}}';
        document.head.appendChild(st);
    }

    // ── Save to Files — every export (PROP-010 phase 3) ──────────────────────
    // deliver(blob, filename, meta) is THE way an export leaves a page: it downloads exactly as
    // the export always did, records the download (a browser-built export cannot be governed —
    // its data is already here — so it is at least named in the audit trail), and, where Files
    // is available to this user, offers "Save to Files" for a few seconds. meta: {what,
    // sessionId, turnId, profileId, componentId, instanceId, source, saveUrl, serverBuilt}.
    // A server-built export passes `saveUrl` (its own route): saving asks the SERVER for the
    // bytes with ?to=files, so the kept file is the server's, not this page's copy.
    let _avail = null, _availAt = 0;
    function filesAvailable() {
        if (_avail && Date.now() - _availAt < 60000) return _avail;
        _availAt = Date.now();
        _avail = fetch('/api/v1/files?limit=1', { headers: hdrs() })
            .then(r => (r.ok ? r.json() : null)).then(d => !!(d && d.available)).catch(() => false);
        return _avail;
    }

    function download(blob, filename) {
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    }

    function deliver(blob, filename, meta = {}) {
        download(blob, filename);
        if (!meta.serverBuilt) {
            fetch('/api/v1/files/exports/recorded', { method: 'POST', headers: hdrs(),
                body: JSON.stringify({ what: meta.what || 'export', filename, size: blob.size || 0, session_id: meta.sessionId || null }) })
                .catch(() => {});
        }
        filesAvailable().then(ok => { if (ok) offer(blob, filename, meta); });
    }

    async function saveExport(blob, filename, meta = {}, allowDuplicate = false) {
        let r;
        if (meta.saveUrl) {
            const u = new URL(meta.saveUrl, location.origin);
            u.searchParams.set('to', 'files');
            if (allowDuplicate) u.searchParams.set('allow_duplicate', '1');
            r = await fetch(u.pathname + u.search, { headers: { Authorization: `Bearer ${tok()}` } });
        } else {
            const fd = new FormData();
            fd.append('file', blob, filename);
            fd.append('filename', filename);
            for (const [k, v] of [['component_id', meta.componentId], ['instance_id', meta.instanceId], ['session_id', meta.sessionId],
                                  ['turn_id', meta.turnId], ['profile_id', meta.profileId]]) {
                if (v != null && v !== '') fd.append(k, String(v));
            }
            if (meta.source) fd.append('source', JSON.stringify(meta.source));
            if (allowDuplicate) fd.append('allow_duplicate', '1');
            r = await fetch('/api/v1/files/save', { method: 'POST', headers: { Authorization: `Bearer ${tok()}` }, body: fd });
        }
        const d = await r.json().catch(() => ({}));
        return { ok: r.ok, status: r.status, file: d.file, message: d.message, code: d.code };
    }

    // ONE toast for every save outcome: the offer after a download, "Saved to Files · Open"
    // after any save (a dashboard snapshot, an export), "Already in Files" for a duplicate.
    let _offerTimer = null;
    function toast() {
        css();
        document.getElementById('files-export-offer')?.remove();
        clearTimeout(_offerTimer);
        const el = document.createElement('div');
        el.id = 'files-export-offer';
        el.setAttribute('role', 'status');
        el.setAttribute('aria-live', 'polite');
        const ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"/></svg>';
        const X = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
        const t = {
            el,
            hide: () => { el.classList.remove('is-on'); setTimeout(() => el.remove(), 200); },
            arm: (ms) => { clearTimeout(_offerTimer); _offerTimer = setTimeout(t.hide, ms); },
            paint: (text, acts) => {
                el.innerHTML = `<span class="fko-i">${ICON}</span><span class="fko-t">${text}</span>${acts}`
                    + `<button type="button" data-x class="card-btn card-btn--neutral card-btn--sm" aria-label="Dismiss">${X}</button>`;
                el.querySelector('[data-x]').addEventListener('click', t.hide);
            },
            saved: (file, already, again) => {
                t.paint(`${already ? 'Already in Files' : 'Saved to Files'} · <b>${esc(file.filename)}</b>`,
                        `<button type="button" data-open class="card-btn card-btn--neutral card-btn--sm">Open</button>`
                        + (already && again ? `<button type="button" data-again class="card-btn card-btn--neutral card-btn--sm">Save another copy</button>` : ''));
                el.querySelector('[data-open]').addEventListener('click', () => { t.hide(); openFiles({ focus: file.id }); });
                if (already && again) el.querySelector('[data-again]').addEventListener('click', again);
                t.arm(8000);
            },
        };
        el.addEventListener('mouseenter', () => clearTimeout(_offerTimer));
        el.addEventListener('mouseleave', () => t.arm(4000));
        el.addEventListener('focusin', () => clearTimeout(_offerTimer));
        document.body.appendChild(el);
        requestAnimationFrame(() => el.classList.add('is-on'));
        return t;
    }

    // "Saved to Files · <name> · Open" — for any save made elsewhere (a dashboard snapshot).
    function announceSaved(file) {
        if (!file) return;
        toast().saved(file, false);
    }

    function offer(blob, filename, meta) {
        const t = toast();
        const save = async (again) => {
            const btn = t.el.querySelector('[data-save], [data-again]');
            if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
            const r = await saveExport(blob, filename, meta, !!again);
            if (r.ok && r.file) t.saved(r.file, false);
            else if (r.status === 409 && r.code === 'duplicate' && r.file) t.saved(r.file, true, () => save(true));
            else { t.paint(esc(r.message || 'Files could not keep this export.'), ''); t.arm(7000); }
        };
        t.paint(`Downloaded <b>${esc(filename)}</b>`,
                `<button type="button" data-save class="card-btn card-btn--primary card-btn--sm">Save to Files</button>`);
        t.el.querySelector('[data-save]').addEventListener('click', () => save(false));
        t.arm(9000);
    }

    // A flow's File node names its file after a run (Open in Files).
    UderiaBus.on('uderia:flow-open-file', (e) => {
        const id = e && e.detail && e.detail.file_id;
        if (id) openFiles({ focus: id });
    });

    function start() {
        css();
        const log = document.getElementById('chat-log');
        if (!log) { setTimeout(start, 1500); return; }
        log.querySelectorAll('.message-bubble').forEach(schedule);
        new MutationObserver((muts) => {
            for (const m of muts) {
                m.addedNodes.forEach(n => {
                    if (n.nodeType !== 1) return;
                    // what this module itself adds to a chip is not new content to wire
                    if (n.matches && n.matches('.fk-chip-keep, .fk-chip-state')) return;
                    if (n.classList && n.classList.contains('message-bubble')) schedule(n);
                    else if (n.closest) schedule(n.closest('.message-bubble'));
                    if (n.querySelectorAll) n.querySelectorAll('.message-bubble').forEach(schedule);
                });
            }
        }).observe(log, { childList: true, subtree: true });
    }

    // Live: a file kept, deleted or restored ANYWHERE (another tab, a REST script) re-reads the
    // kept state of the answers on screen.
    UderiaBus.onComponent('files', (e) => {
        KEPT.clear();
        // a turn reading a file changes no chip; a keep, delete, restore or purge does
        const chipsChanged = !e || !e.detail || e.detail.action !== 'used';
        if (chipsChanged) ATT.clear();
        document.querySelectorAll('#chat-log .message-bubble').forEach(b => {
            if (chipsChanged && b.querySelector('.attachment-chip[data-file-id]')) wireLogChips(b);
            if (b.querySelector('.fk-keep:not([hidden]), .fk-keep-answer:not([hidden])')) syncStates(b);
        });
    });

    async function keepAttachment(fileId) {
        const r = await fetch(`/api/v1/files/${encodeURIComponent(fileId)}/keep`, { method: 'POST', headers: hdrs() });
        const d = await r.json().catch(() => ({}));
        return { ok: r.ok, message: d.message };
    }

    // ── chat-log attachment chips: Keep / Kept / Deleted ────────────────────
    // The chip states what its attachment is NOW, from the conversation's own manifest
    // (GET /v1/files/attachments): an upload or a file attached from Files that is live offers
    // Keep (or opens as Kept); one deleted from Files says so and offers Restore while Recently
    // deleted still holds it; one whose content is gone says that. A turn never reads a deleted
    // attachment (session_files.readable_manifest), so the chip is the only place it shows.
    const ATT = new Map();   // session id -> Promise<{chip id -> state}>

    function attachmentStates(sid) {
        if (!ATT.has(sid)) {
            ATT.set(sid, fetch(`/api/v1/files/attachments?session_id=${encodeURIComponent(sid)}`, { headers: hdrs() })
                .then(r => (r.ok ? r.json() : { manifest: false, attachments: {} }))
                .then(d => ({ readable: d.manifest !== false, byId: d.attachments || {} }))
                .catch(() => ({ readable: false, byId: {} })));
        }
        return ATT.get(sid);
    }

    function untilWords(iso) {
        if (!iso) return 'until an admin removes it';
        const t = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z');
        return `until ${t.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}`;   // the UI's language: "23 Nov"
    }

    function markDeleted(chip, st, sid) {
        chip.classList.add('fk-chip-deleted');
        chip.querySelectorAll(':scope > *:not(.fk-chip-state)').forEach(el => { el.style.opacity = '.55'; });
        const name = chip.querySelector('.truncate');
        if (name) name.style.textDecoration = 'line-through';
        const tag = document.createElement('span');
        tag.className = 'fk-chip-state u-tip';
        tag.style.cssText = 'margin-left:4px;font-size:11px;color:var(--text-muted)';
        tag.textContent = 'Deleted';
        tag.dataset.tip = st.state === 'deleted'
            ? `Deleted from Files — restorable ${untilWords(st.purge_after)}. The answer stays; no turn reads this file.`
            : 'Deleted from Files — its content is gone. The answer stays.';
        chip.appendChild(tag);
        if (st.state !== 'deleted' || !st.file_id) return;
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'fk-chip-keep card-btn card-btn--neutral card-btn--sm';
        b.style.cssText = 'margin-left:4px;padding:0 .45rem';
        b.textContent = 'Restore';
        b.setAttribute('aria-label', 'Restore this file from Recently deleted');
        b.addEventListener('click', async () => {
            b.disabled = true;
            const r = await fetch(`/api/v1/files/${encodeURIComponent(st.file_id)}/restore`, { method: 'POST', headers: hdrs() });
            const d = await r.json().catch(() => ({}));
            if (!r.ok) {
                b.disabled = false;
                if (typeof window.showAppBanner === 'function') window.showAppBanner(d.message || 'The file could not be restored.', 'warning');
                return;
            }
            ATT.delete(sid);   // the live notice re-wires every chip; this covers a missed one
            const bubble = chip.closest('.message-bubble');
            if (bubble) wireLogChips(bubble);
        });
        chip.appendChild(b);
    }

    async function wireLogChips(bubble) {
        const chips = bubble.querySelectorAll('.attachment-chip[data-file-id]');
        if (!chips.length || !bubble.closest('#chat-log')) return;
        const sid = bubble.closest('[data-session-id]')?.dataset?.sessionId || window.__currentSessionId;
        if (!sid) return;
        const ok = await gate(window.__currentProfileId || '');
        if (!ok) return;
        const states = await attachmentStates(sid);
        chips.forEach(chip => {
            // absent from a list that was read = removed by a purge; an unreadable list says nothing
            const st = states.byId[chip.dataset.fileId] || (states.readable ? { state: 'gone' } : null);
            // A chip already showing this state is left untouched. Rebuilding it anyway re-inserted
            // its button, the chat-log observer answered that insert by wiring the bubble again,
            // which rebuilt it again — a loop on every animation frame for as long as the
            // conversation was open; an iPad spent its main thread on it and no control answered.
            const sig = !st ? '' : st.state !== 'live' ? `${st.state}|${st.file_id || ''}|${st.purge_after || ''}`
                : `live|${st.file_id || ''}|${st.lifetime === 'kept' ? 'kept' : 'keep'}`;
            if (chip.dataset.fkSig === sig) return;
            chip.dataset.fkSig = sig;
            chip.querySelectorAll('.fk-chip-keep, .fk-chip-state').forEach(el => el.remove());
            chip.classList.remove('fk-chip-deleted');
            chip.querySelectorAll(':scope > *').forEach(el => { el.style.opacity = ''; });
            const nm = chip.querySelector('.truncate');
            if (nm) nm.style.textDecoration = '';
            if (!st) return;
            if (st.state !== 'live') { markDeleted(chip, st, sid); return; }
            if (!st.file_id) return;   // an old upload that never became a file: the chip as before
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'fk-chip-keep card-btn card-btn--neutral card-btn--sm u-tip';
            b.style.cssText = 'margin-left:4px;padding:0 .45rem';
            const kept = st.lifetime === 'kept';
            b.textContent = kept ? 'Kept' : 'Keep';
            b.dataset.tip = kept ? 'Kept in Files — it stays when the conversation is deleted' : 'Deleted with the conversation unless you keep it';
            b.setAttribute('aria-label', kept ? 'Open this file in Files' : 'Keep this file in Files');
            b.addEventListener('click', async () => {
                if (b.textContent === 'Kept') { openFiles({ focus: st.file_id }); return; }
                const r = await keepAttachment(st.file_id);
                if (r.ok) { b.textContent = 'Kept'; b.dataset.tip = 'Kept in Files — it stays when the conversation is deleted'; ATT.delete(sid); }
                else if (typeof window.showAppBanner === 'function') window.showAppBanner(r.message || 'The file could not be kept.', 'warning');
            });
            chip.appendChild(b);
        });
    }

    // ── "From Files…" — the picker ───────────────────────────────────────────
    // ONE picker for every place that chooses a kept file. With no options it attaches the
    // chosen files to the conversation (the composer's From Files…); with `choose` it resolves
    // to the chosen files instead (a flow's File input node). `accept(f)` decides what may be
    // chosen and says why not.
    function pick(opts = {}) {
        const choose = typeof opts.choose === 'function' ? opts.choose : null;
        const single = !!opts.single;
        const accept = opts.accept || (f => ({ ok: !!f.attachable, note: f.attach_note || 'A turn cannot read this type' }));
        const goWord = opts.actionLabel || 'Attach';
        document.getElementById('files-pick-modal')?.remove();
        const wrap = document.createElement('div');
        wrap.id = 'files-pick-modal';
        wrap.style.cssText = 'position:fixed;inset:0;z-index:70;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.45);padding:16px';
        wrap.innerHTML = `
          <div role="dialog" aria-modal="true" aria-labelledby="fpk-title" style="width:min(560px,100%);max-height:min(640px,90vh);display:flex;flex-direction:column;background:var(--bg-overlay,var(--card-bg));color:var(--text-primary);border:1px solid var(--border-primary);border-radius:12px;box-shadow:0 20px 50px rgba(0,0,0,.35);overflow:hidden">
            <div style="display:flex;align-items:center;gap:10px;padding:14px 16px;border-bottom:1px solid var(--border-primary)">
              <h2 id="fpk-title" style="margin:0;font-size:16px;font-weight:600;flex:1">${esc(opts.title || 'Attach from Files')}</h2>
              <button type="button" data-x aria-label="Close" class="card-btn card-btn--neutral card-btn--sm"></button>
            </div>
            <div style="padding:12px 16px;display:flex;flex-direction:column;gap:10px">
              <label style="display:flex;align-items:center;gap:8px;height:34px;padding:0 10px;border:1px solid var(--border-primary);border-radius:8px;background:var(--input-bg,var(--bg-primary))">
                <span style="position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)">Search files</span>
                <input type="search" data-q placeholder="Search names, conversations, questions" style="flex:1;border:0;background:transparent;color:var(--text-primary);font:inherit;outline:none">
              </label>
              <div data-chips style="display:flex;gap:6px;flex-wrap:wrap"></div>
            </div>
            <div data-list role="listbox" aria-multiselectable="${single ? 'false' : 'true'}" style="flex:1;overflow:auto;padding:0 8px"></div>
            <div style="display:flex;align-items:center;gap:10px;padding:12px 16px;border-top:1px solid var(--border-primary)">
              <span style="flex:1;font-size:12px;color:var(--text-muted)">${esc(opts.note || 'Attached by reference, not copied. Only types a turn can read can be chosen.')}</span>
              <button type="button" data-cancel class="card-btn card-btn--neutral">Cancel</button>
              <button type="button" data-go disabled class="card-btn card-btn--primary">${esc(goWord)}</button>
            </div>
          </div>`;
        // the close glyph is drawn, not an emoji
        wrap.querySelector('[data-x]').innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
        document.body.appendChild(wrap);
        const chosen = new Set();
        const picked = new Map();     // id → the file as listed when chosen (survives a new search)
        let kind = 'all', q = '', files = [];
        let settled = false;
        const close = (picked) => {
            wrap.remove(); document.removeEventListener('keydown', onKey, true);
            if (choose) { if (!settled) { settled = true; choose(picked || null); } }
            else DOM_focus();
        };
        const DOM_focus = () => document.getElementById('user-input')?.focus();
        const onKey = (e) => { if (e.key === 'Escape') { e.preventDefault(); close(); } };
        document.addEventListener('keydown', onKey, true);
        wrap.addEventListener('mousedown', (e) => { if (e.target === wrap) close(); });
        wrap.querySelector('[data-x]').addEventListener('click', () => close());
        wrap.querySelector('[data-cancel]').addEventListener('click', () => close());
        const go = wrap.querySelector('[data-go]');
        const paintGo = () => { go.disabled = !chosen.size; go.textContent = chosen.size && !single ? `${goWord} ${chosen.size}` : goWord; };
        const KINDS = opts.kinds || [['all', 'All'], ['table', 'Tables'], ['document', 'Documents'], ['image', 'Images'], ['upload', 'Uploads']];
        const render = (facets) => {
            wrap.querySelector('[data-chips]').innerHTML = KINDS.map(([k, l]) =>
                `<button type="button" data-k="${k}" aria-pressed="${kind === k}" class="ind-tab ind-tab--pill${kind === k ? ' active' : ''}" style="padding:.3rem .65rem;font-size:.75rem">${l} <span style="color:var(--text-muted)">${facets && facets[k] != null ? facets[k] : ''}</span></button>`).join('');
            wrap.querySelectorAll('[data-k]').forEach(b => b.addEventListener('click', () => { kind = b.dataset.k; load(); }));
            const list = wrap.querySelector('[data-list]');
            list.innerHTML = files.length ? files.map(f => { const a = accept(f); return `
              <label style="display:flex;align-items:center;gap:10px;padding:9px 10px;border-radius:8px;cursor:${a.ok ? 'pointer' : 'not-allowed'};opacity:${a.ok ? 1 : .6}">
                <input type="${single ? 'radio' : 'checkbox'}" name="fpk-choice" data-id="${esc(f.id)}" ${a.ok ? '' : 'disabled'} ${chosen.has(f.id) ? 'checked' : ''} style="width:15px;height:15px">
                <span style="flex:1;min-width:0;display:flex;flex-direction:column;gap:2px">
                  <span style="font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(f.filename)}</span>
                  <span style="font-size:12px;color:var(--text-muted)">${a.ok ? `${esc(f.type_label)} · ${esc(f.size_label)}${f.session_title ? ` · from “${esc(f.session_title)}”` : ''}` : esc(a.note || '')}</span>
                </span></label>`; }).join('')
                : `<p style="padding:24px;text-align:center;color:var(--text-muted);font-size:13px">${q ? `No files match “${esc(q)}”.` : 'No kept files yet.'}</p>`;
            list.querySelectorAll('input[data-id]').forEach(cb => cb.addEventListener('change', () => {
                if (single) { chosen.clear(); picked.clear(); }
                if (cb.checked) { chosen.add(cb.dataset.id); picked.set(cb.dataset.id, files.find(f => f.id === cb.dataset.id)); }
                else { chosen.delete(cb.dataset.id); picked.delete(cb.dataset.id); }
                paintGo();
            }));
        };
        let seq = 0;
        const load = async () => {
            const my = ++seq;
            const p = new URLSearchParams({ kind, limit: '200' });
            if (q) p.set('q', q);
            const r = await fetch(`/api/v1/files?${p}`, { headers: hdrs() });
            const d = r.ok ? await r.json() : { files: [], facets: {} };
            if (my !== seq) return;
            files = d.files || [];
            render(d.facets || {});
        };
        let t = null;
        const qi = wrap.querySelector('[data-q]');
        qi.addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => { q = qi.value.trim(); load(); }, 160); });
        go.addEventListener('click', async () => {
            if (choose) { close([...picked.values()].filter(Boolean)); return; }
            const sid = window.__currentSessionId;
            if (!sid) return;
            go.disabled = true;
            for (const id of chosen) {
                const r = await fetch(`/api/v1/files/${encodeURIComponent(id)}/attach`, { method: 'POST', headers: hdrs(), body: JSON.stringify({ session_id: sid }) });
                const d = await r.json().catch(() => ({}));
                if (r.ok && d.file && typeof window.__addPendingAttachment === 'function') window.__addPendingAttachment(d.file);
            }
            close();
        });
        paintGo();
        load();
        setTimeout(() => qi.focus(), 30);
    }

    window.FilesKeep = { open: openFiles, keepAttachment, pick, deliver, saveExport, filesAvailable, announceSaved,
                         refresh: () => UderiaBus.announce('files', 'changed') };

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();
