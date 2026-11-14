/**
 * attachmentSources.js — S9 Grounded Multimodal, the consumer face (a plain script, like
 * turnLineage.js). What the platform READ from an attachment is shown wherever the attachment
 * is: the composer chip ("read · OCR · 3 rows" the moment the upload returns), the user
 * bubble's chip after the turn ("used as source"), the answer capsule's Sources row (each
 * image with a crop of the cited region), and the figure-lineage dialog (a figure quoted from
 * an attachment table opens on the file, the region and the row). ONE vocabulary:
 *   verified   — read by the platform (OCR / PDF tables / CSV / text); the gate holds the answer to it
 *   unverified — transcribed by the model; recorded, never evidence
 *   unread     — nothing could read it; the model saw it natively only
 * Absence-tolerant: no stamp ⇒ nothing is added, the chips render exactly as before.
 * Image bytes come from the session-scoped content endpoint (auth header → object URL).
 */
(function () {
    'use strict';
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const _blobs = new Map();   // `${sid}:${fileId}` → Promise<objectURL|null>

    function usable(x) { return !!x && ((Array.isArray(x.tables) && x.tables.length > 0) || Number(x.text_chars || 0) > 0 || (typeof x.text === 'string' && x.text.trim().length > 0)); }
    function state(x) { return !usable(x) ? 'unread' : (x.verified ? 'verified' : 'unverified'); }
    function methodWord(x) {
        const m = String((x && x.method) || 'none');
        return { ocr: 'OCR', pdf_table: 'PDF tables', pdf_text: 'PDF text', text: 'text', csv: 'CSV', docx: 'document text', vision_transcription: 'model transcription', none: 'not readable' }[m] || m.replace(/_/g, ' ');
    }
    function whatWord(x) {
        const t = Array.isArray(x && x.tables) ? x.tables : [];
        if (t.length) {
            const rows = t.reduce((a, tt) => a + Number(tt.row_count || 0), 0);
            return `${t.length} table${t.length === 1 ? '' : 's'} · ${rows} row${rows === 1 ? '' : 's'}`;
        }
        const n = Number((x && x.text_chars) || 0) || String((x && x.text) || '').length;
        return n ? `${n.toLocaleString('en-US')} chars` : '';
    }
    /** The one badge every surface wears; `used` adds the turn-time fact. */
    function badgeHtml(x, used) {
        const st = state(x);
        const label = st === 'verified' ? `${used ? 'used as source · ' : 'read · '}${methodWord(x)}${whatWord(x) ? ' · ' + whatWord(x) : ''}`
            : (st === 'unverified' ? `${used ? 'used · ' : ''}transcribed · unverified` : 'not readable');
        const tip = st === 'verified'
            ? `Read by the platform (${methodWord(x)}${x.engine && x.engine !== 'model' ? ' · ' + x.engine : ''}) — verified.\nThe grounding check holds the answer to it; a figure quoted from it traces to the file${Array.isArray(x.tables) && x.tables.length ? ' and its region' : ''}.`
            : (st === 'unverified' ? 'Transcribed by the model — unverified.\nRecorded so the answer can name where it came from, but never evidence for the answer.'
                : `${(x && x.note) || 'No extractor could read this file here'}.\nThe model saw it natively; nothing in it is vouched for.`);
        return `<span class="as-badge as-${st} u-tip" data-tip="${esc(tip)}" data-state="${st}">${esc(label)}</span>`;
    }
    function isImage(x, att) {
        return (x && x.kind === 'image') || (att && (att.is_image || /^image\//.test(att.content_type || '')));
    }
    function imageUrl(sid, fileId) {
        const key = `${sid}:${fileId}`;
        if (_blobs.has(key)) return _blobs.get(key);
        const tok = localStorage.getItem('tda_auth_token');
        // a data: URL, not an object URL — the page's CSP allows img-src 'self' data: and
        // deliberately not blob:, and widening the policy for a thumbnail is the wrong trade
        const p = fetch(`/api/v1/chat/upload/${encodeURIComponent(fileId)}/content?session_id=${encodeURIComponent(sid)}`,
            { headers: tok ? { 'Authorization': 'Bearer ' + tok } : {} })
            .then(r => (r.ok ? r.blob() : null)).then(b => (b ? blobToDataUrl(b) : null)).catch(() => null);
        _blobs.set(key, p);
        return p;
    }
    function blobToDataUrl(blob) {
        return new Promise((resolve) => {
            try { const fr = new FileReader(); fr.onload = () => resolve(String(fr.result || '')); fr.onerror = () => resolve(null); fr.readAsDataURL(blob); }
            catch (_) { resolve(null); }
        });
    }
    /** Fill every `<img data-as-thumb>` under `root` from the content endpoint (or a local data URL). */
    function hydrate(root, sid) {
        if (!root || !root.querySelectorAll) return;
        root.querySelectorAll('img[data-as-thumb]').forEach(img => {
            if (img.dataset.asDone) return;
            img.dataset.asDone = '1';
            if (img.dataset.asLocal !== undefined) return;   // the composer sets its own src
            if (!sid || !img.dataset.fileId) return;
            imageUrl(sid, img.dataset.fileId).then(url => { if (url) img.src = url; else img.remove(); });
        });
        root.querySelectorAll('canvas[data-as-crop]').forEach(cv => { if (!cv.dataset.asDone) { cv.dataset.asDone = '1'; drawCrop(cv, sid); } });
    }
    /** `local` = the caller owns the bytes (the composer's own file) — hydrate must not fetch. */
    function thumbHtml(fileId, alt, local) {
        return `<img data-as-thumb data-file-id="${esc(fileId)}"${local ? ' data-as-local="1"' : ''} class="as-thumb" alt="${esc(alt)}" width="28" height="20">`;
    }
    /** Alt text FROM the extraction — a screen reader hears what the platform read. */
    function altText(x, filename) {
        if (!usable(x)) return `${filename}: image, not readable by the platform`;
        const t = Array.isArray(x.tables) ? x.tables : [];
        if (t.length) {
            const cols = (t[0].columns || []).slice(0, 6).join(', ');
            return `${filename}: table of ${cols}${t[0].columns && t[0].columns.length > 6 ? ' …' : ''}, ${whatWord(x)} (${x.verified ? 'read by the platform' : 'transcribed by the model'})`;
        }
        return `${filename}: ${String(x.text || '').slice(0, 120) || whatWord(x)} (${x.verified ? 'read by the platform' : 'transcribed by the model'})`;
    }

    /** Decorate the attachment chips inside a user bubble from a turn's stamp (reload + live). */
    function decorate(bubble, stamp, sid) {
        if (!bubble || !stamp || !Array.isArray(stamp.sources)) return 0;
        let n = 0;
        stamp.sources.forEach(x => {
            const chip = bubble.querySelector(`.attachment-chip[data-file-id="${CSS.escape(String(x.file_id))}"]`);
            if (!chip || chip.dataset.asUsed) return;
            chip.dataset.asUsed = state(x);
            const old = chip.querySelector('.as-badge, .as-kind');
            if (old) old.remove();
            chip.insertAdjacentHTML('beforeend', badgeHtml(x, true));
            if (isImage(x) && !chip.querySelector('img[data-as-thumb]')) {
                const icon = chip.querySelector('svg.chip-icon');
                if (icon) icon.outerHTML = thumbHtml(x.file_id, altText(x, x.filename));
            }
            n += 1;
        });
        hydrate(bubble, sid);
        return n;
    }
    /** Live: the Live Status card reported the turn's sources → mark the last user bubble's chips. */
    function markUsed(sid, details) {
        try {
            // `state` is a module import, never on window — the viewed session is published as
            // window.__currentSessionId (state.js); a dial-less caller passes no sid (accepted)
            const cur = window.__currentSessionId || (window.state && window.state.currentSessionId) || null;
            if (cur && sid && sid !== cur) return 0;
            const log = document.getElementById('chat-log');
            if (!log) return 0;
            const bubbles = [...log.querySelectorAll('[data-file-id]')].map(el => el.closest('.message-bubble, .chat-message, [data-role]') || el.parentElement);
            const last = bubbles.length ? bubbles[bubbles.length - 1] : null;
            return last ? decorate(last, details, sid) : 0;
        } catch (_) { return 0; }
    }

    /** The region crop: the image drawn into a small canvas, the cited region outlined. */
    function drawCrop(cv, sid) {
        const fileId = cv.dataset.fileId;
        const region = (cv.dataset.region || '').split(',').map(Number);
        const W = Number(cv.dataset.w || 0), H = Number(cv.dataset.h || 0);
        if (!fileId || !sid) return;
        imageUrl(sid, fileId).then(url => {
            if (!url) { cv.remove(); return; }
            const im = new Image();
            im.onload = () => {
                try {
                    const iw = im.naturalWidth || W, ih = im.naturalHeight || H;
                    const ok = region.length === 4 && region.every(Number.isFinite);
                    const pad = 8;
                    const x0 = ok ? Math.max(0, region[0] - pad) : 0, y0 = ok ? Math.max(0, region[1] - pad) : 0;
                    const x1 = ok ? Math.min(iw, region[2] + pad) : iw, y1 = ok ? Math.min(ih, region[3] + pad) : ih;
                    const cw = Math.max(1, x1 - x0), ch = Math.max(1, y1 - y0);
                    const scale = Math.min(cv.width / cw, cv.height / ch, 1);
                    const dw = Math.round(cw * scale), dh = Math.round(ch * scale);
                    cv.width = dw; cv.height = dh;
                    const ctx = cv.getContext('2d');
                    ctx.drawImage(im, x0, y0, cw, ch, 0, 0, dw, dh);
                    if (ok) {
                        ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--teradata-orange').trim() || '#F15F22';
                        ctx.lineWidth = 2;
                        ctx.strokeRect((region[0] - x0) * scale + 1, (region[1] - y0) * scale + 1, (region[2] - region[0]) * scale - 2, (region[3] - region[1]) * scale - 2);
                    }
                } catch (_) { /* a crop that cannot draw leaves the chip */ }
            };
            im.src = url;
        });
    }
    function cropHtml(x, table, size) {
        if (!x || x.kind !== 'image' || !table) return '';
        const reg = Array.isArray(table.region) && table.region.length === 4 ? table.region.join(',') : '';
        const w = size || 220;
        return `<canvas data-as-crop data-file-id="${esc(x.file_id)}" data-region="${esc(reg)}" data-w="${esc(x.width || 0)}" data-h="${esc(x.height || 0)}" width="${w}" height="${Math.round(w * 0.6)}" class="as-crop" role="img" aria-label="${esc(`${x.filename}: the cited region${reg ? '' : ' (whole image)'}`)}"></canvas>`;
    }

    /** The capsule's Sources row: one chip per attachment source, an image with its region crop. */
    function sourcesRowHtml(td, sid) {
        const a = td && td.attachment_sources;
        const srcs = a && Array.isArray(a.sources) ? a.sources : [];
        if (!srcs.length) return '';
        return `<div class="as-sources">` + srcs.map(x => {
            const st = state(x);
            const t = Array.isArray(x.tables) ? x.tables : [];
            const stmt = Array.isArray(x.statement_ns) && x.statement_ns.length ? ` · result set ${x.statement_ns.join(', ')}` : '';
            const region = t.length && t[0].region ? ` · region ${t[0].region.map(v => Math.round(v)).join(',')}${t[0].page ? ' on page ' + t[0].page : ''}` : '';
            const tip = st === 'verified' ? `Read by the platform (${methodWord(x)}${x.engine && x.engine !== 'model' ? ' · ' + x.engine : ''}) — verified${region}${stmt}.\nThe grounding check held the answer to it.`
                : (st === 'unverified' ? 'Transcribed by the model — unverified. Never evidence.' : `${x.note || 'Not readable'} — the model saw it natively.`);
            return `<span class="as-source as-${st} u-tip" data-tip="${esc(tip)}" data-file-id="${esc(x.file_id)}">
                ${isImage(x) ? (t.length && x.verified ? cropHtml(x, t[0], 160) : thumbHtml(x.file_id, altText(x, x.filename))) : ''}
                <span class="as-source-name">${esc(x.filename)}</span>
                ${badgeHtml(x, st !== 'unread')}
            </span>`;
        }).join('') + `</div>`;
    }

    /** The lineage dialog: a statement registered from an attachment → the file, the region, the row. */
    function statementHtml(st, sid) {
        const s = st && st.source;
        if (!s) return '';
        const x = { file_id: s.file_id, filename: s.filename, kind: s.width || s.height ? 'image' : (String(st.statement || '').match(/\.(png|jpe?g|gif|webp)#/i) ? 'image' : 'other'), method: s.method, engine: s.engine, verified: true, width: s.width, height: s.height, tables: [{ region: s.region, page: s.page, row_count: st.row_count, columns: st.columns }] };
        const where = `${esc(s.filename)} — ${esc(methodWord(x))}${s.engine ? ' (' + esc(s.engine) + ')' : ''}, verified${s.page ? `, page ${esc(s.page)}` : ''}`;
        return `<div class="as-lineage">${x.kind === 'image' ? cropHtml(x, x.tables[0], 260) : ''}<p class="fl-lead">Quoted from the attachment ${where}${s.region ? ' — the cited region is outlined' : ''}.</p></div>`;
    }

    window.AttachmentSources = { blobToDataUrl, badgeHtml, thumbHtml, altText, decorate, markUsed, hydrate, sourcesRowHtml, statementHtml, cropHtml, state, methodWord, whatWord, _internals: { usable, imageUrl, _blobs } };
})();
