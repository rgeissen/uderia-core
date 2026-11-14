/**
 * Figure lineage — the click on a receipted/quoted figure in the answer (S3, Compute with
 * Lineage). The marks are written server-side (`compute_lineage.mark_html`): a receipted
 * figure carries `data-receipt` (index into the turn's `compute.receipts`), a quoted one
 * `data-statement` + `data-row`, both `data-turn-id`. This script does ONE thing: opens a
 * real dialog showing the receipt and the rows it was computed over (or the quoted row),
 * read from the persisted turn (`/api/session/<sid>/turn/<n>/details` → `compute`).
 *
 * Accessibility: `role="dialog"` + `aria-modal`, labelled by its heading, focus trapped,
 * Escape/backdrop close, focus returned to the figure. The hover lineage itself is the
 * platform tooltip (`u-tip`) the mark already carries. Theme tokens only; no emoji.
 *
 * Absence-tolerant: no marks on the page ⇒ nothing here ever runs.
 */
(function () {
    'use strict';
    const SEL = 'mark.figure-receipted, mark.figure-quoted';
    let _open = null;          // {root, restore}
    let _cache = new Map();    // `${sid}:${turn}` → compute stamp

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }
    function fmt(v) {
        if (v == null) return '—';
        // the answer's figures are formatted en-US server-side ("90,512.28"); the dialog must read
        // the SAME number, not a locale variant of it ("90.512,28")
        if (typeof v === 'number') return Number.isInteger(v) ? v.toLocaleString('en-US') : v.toLocaleString('en-US', { maximumFractionDigits: 6 });
        return String(v);
    }
    function sessionOf(mark) {
        const bubble = mark.closest('.message-bubble, .assistant-message, [data-session-id]');
        const el = bubble && bubble.querySelector('[data-session-id]');
        return (el && el.dataset.sessionId) || (bubble && bubble.dataset && bubble.dataset.sessionId)
            || window.__currentSessionId || (window.state && window.state.currentSessionId) || null;
    }
    async function loadCompute(sid, turn) {
        const key = `${sid}:${turn}`;
        if (_cache.has(key)) return _cache.get(key);
        const tok = localStorage.getItem('tda_auth_token');
        const r = await fetch(`/api/session/${encodeURIComponent(sid)}/turn/${parseInt(turn, 10)}/details`,
            { headers: tok ? { 'Authorization': 'Bearer ' + tok } : {} });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        const c = (data && data.compute) || null;
        _cache.set(key, c);
        return c;
    }

    function table(rows, highlightRow) {
        const list = Array.isArray(rows) ? rows.filter(r => r && typeof r === 'object') : [];
        if (!list.length) return '<p class="fl-empty">No rows were carried on this receipt.</p>';
        const cols = [];
        list.forEach(r => Object.keys(r).forEach(k => { if (!cols.includes(k)) cols.push(k); }));
        const head = cols.map(c => `<th>${esc(c)}</th>`).join('');
        const body = list.map((r, i) => `<tr${(highlightRow === i + 1) ? ' class="fl-hit"' : ''}>${cols.map(c => `<td>${esc(fmt(r[c]))}</td>`).join('')}</tr>`).join('');
        return `<div class="fl-table-wrap"><table class="fl-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
    }

    // PROP-006 — where a statement's rows came from. Rendered ONLY when the turn read more
    // than one source or combined (`compute.multi_source`, decided by the server): a
    // single-source figure reads exactly as it always did.
    function when(ts) {
        if (!ts) return '';
        const d = new Date(ts);
        return isNaN(d) ? String(ts) : d.toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'medium' });
    }
    function sourceLabel(st) {
        if (!st) return '';
        if (st.source_id === 'combine') return 'combined by code from the turn\'s results';
        return st.source_name || st.source_id || '';
    }
    function sourceLine(st, compute) {
        if (!st || !compute || !compute.multi_source) return '';
        const src = sourceLabel(st);
        if (!src && !st.ts) return '';
        return `<p class="fl-source"><span class="fl-source-k">Source</span> ${esc(src || 'not recorded')}${st.ts ? ` · <span class="fl-source-k">read</span> ${esc(when(st.ts))}` : ''}</p>`;
    }
    // The second hop: a combined statement's inputs, each with its own source and time — the
    // way from a combined figure back to the databases it came from.
    function inputsHtml(st, compute) {
        const ins = (st && Array.isArray(st.inputs)) ? st.inputs : [];
        if (!ins.length) return '';
        const all = (compute && compute.statements) || [];
        const rows = ins.map(n => {
            const x = all.find(s => s && s.n === n);
            if (!x) return `<li><span class="fl-in-n">statement ${esc(n)}</span> <span class="fl-empty">not on this turn's record</span></li>`;
            return `<li><span class="fl-in-n">statement ${esc(n)}</span> ${esc(sourceLabel(x) || 'source not recorded')}`
                + `${x.ts ? ` · ${esc(when(x.ts))}` : ''} · ${esc(fmt(x.row_count))} rows`
                + `${x.statement ? `<code class="fl-in-sql">${esc(x.statement)}</code>` : ''}</li>`;
        }).join('');
        return `<div class="fl-inputs"><div class="fl-inputs-h">Combined from</div><ul>${rows}</ul></div>`;
    }

    function receiptRows(rc) {
        if (!rc) return '';
        if (rc.op === 'sql') {
            const norm = (rc.normalizations || []).map(x => `${x.left} = ${x.right} (${x.applied})`).join('; ');
            const pairs = [
                ['Operation', 'COMBINE — one SELECT across the turn\'s results, run by code'],
                ['Statements', (rc.statements || []).map(n => `statement ${n}`).join(', ') || '—'],
                ['Rows read', fmt(rc.row_count)],
                ['Rows produced', fmt(rc.result_rows) + (rc.truncated ? ' (first rows only)' : '')],
                norm ? ['Keys matched after', norm] : null,
                rc.result_statement ? ['Result', `statement ${rc.result_statement}`] : null,
                rc.receipt_hash ? ['Receipt', rc.receipt_hash] : null,
            ].filter(Boolean);
            return `<dl class="fl-receipt">${pairs.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>`
                + (rc.sql ? `<pre class="fl-sql"><code>${esc(rc.sql)}</code></pre>` : '');
        }
        const pairs = [
            ['Operation', (rc.op || '').toUpperCase() + (rc.column ? `(${rc.column})` : '')],
            rc.by ? ['Grouped by', rc.by] : null,
            rc.where ? ['Filter', rc.where] : null,
            rc.on ? ['Joined on', rc.on] : null,
            ['Rows used', rc.row_count_used != null ? `${fmt(rc.row_count_used)} of ${fmt(rc.row_count)}` : fmt(rc.row_count)],
            ['Statement', (rc.statements || []).join(', ') || '—'],
            rc.value != null ? ['Value', fmt(rc.value)] : null,
            rc.receipt_hash ? ['Receipt', rc.receipt_hash] : null,
        ].filter(Boolean);
        return `<dl class="fl-receipt">${pairs.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>`;
    }

    // a combine receipt: its inputs as the second hop (the receipt's statements ARE the inputs)
    function combineHop(rc, compute) {
        if (!rc || rc.op !== 'sql') return '';
        return inputsHtml({ inputs: rc.statements || [] }, compute);
    }

    function close() {
        if (!_open) return;
        const { root, restore } = _open;
        _open = null;
        document.removeEventListener('keydown', onKey, true);
        root.remove();
        try { restore && restore.focus(); } catch (_) { /* gone */ }
    }
    function onKey(e) {
        if (!_open) return;
        if (e.key === 'Escape') { e.preventDefault(); close(); return; }
        if (e.key === 'Tab') {
            const f = _open.root.querySelectorAll('button, [href], input, [tabindex]:not([tabindex="-1"])');
            if (!f.length) return;
            const first = f[0], last = f[f.length - 1];
            if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
            else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        }
    }

    function open(mark, title, bodyHtml) {
        close();
        const root = document.createElement('div');
        root.className = 'fl-backdrop';
        root.innerHTML = `
          <div class="fl-dialog" role="dialog" aria-modal="true" aria-labelledby="fl-title">
            <div class="fl-head">
              <h3 id="fl-title" class="fl-title">${esc(title)}</h3>
              <button type="button" class="fl-close" aria-label="Close">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>
              </button>
            </div>
            <div class="fl-body">${bodyHtml}</div>
          </div>`;
        root.addEventListener('click', (e) => { if (e.target === root) close(); });
        root.querySelector('.fl-close').addEventListener('click', close);
        document.body.appendChild(root);
        _open = { root, restore: mark };
        document.addEventListener('keydown', onKey, true);
        root.querySelector('.fl-close').focus();
    }

    // The compute card's own "View the rows" action: the card was rendered mid-turn (no
    // turn id yet), so the turn is read from the message it sits in — a receipted mark of
    // the same answer, else the user bubble the answer follows (its avatar carries the
    // turn) — and the receipt is found by its hash on the persisted stamp.
    function turnOfCard(card) {
        const bubble = card.closest('.message-bubble');
        const mark = bubble && bubble.querySelector('mark.figure-receipted[data-turn-id], mark.figure-quoted[data-turn-id]');
        if (mark && mark.dataset.turnId) return mark.dataset.turnId;
        let prev = bubble && bubble.previousElementSibling;
        while (prev) { const av = prev.querySelector && prev.querySelector('[data-turn-id]'); if (av && av.dataset.turnId) return av.dataset.turnId; prev = prev.previousElementSibling; }
        return null;
    }
    async function onCardRows(btn) {
        const card = btn.closest('.ans-ev, .compute-card') || btn.closest('.message-content');
        const sid = sessionOf(btn), turn = turnOfCard(card), hash = btn.dataset.receiptHash || (card && card.dataset.receiptHash);
        const lead = (card && card.querySelector('.ans-meta, .compute-card-m')) ? card.querySelector('.ans-meta, .compute-card-m').textContent.trim() : 'Compute receipt';
        if (!sid || !turn) { open(btn, 'Compute receipt', `<p class="fl-empty">${esc(lead)}</p><p class="fl-empty">The rows are on the turn\'s record once it is persisted.</p>`); return; }
        open(btn, 'Compute receipt', '<p class="fl-empty">Loading the rows…</p>');
        let compute = null;
        try { compute = await loadCompute(sid, turn); } catch (_) { compute = null; }
        if (!_open) return;
        const body = _open.root.querySelector('.fl-body');
        const rc = compute && (compute.receipts || []).find(r => r && hash && r.receipt_hash === hash);
        body.innerHTML = rc
            ? `<p class="fl-lead">Computed by code, not by the model.</p>${receiptRows(rc)}${combineHop(rc, compute)}${table(rc.sample_rows)}`
            : `<p class="fl-empty">${compute ? 'No receipt with this hash is on the persisted turn.' : 'The turn\'s compute record is not available.'}</p>`;
    }

    async function onClick(e) {
        const cardBtn = e.target && e.target.closest && e.target.closest('.compute-open-btn[data-act="rows"]');
        if (cardBtn) { e.preventDefault(); onCardRows(cardBtn); return; }
        const mark = e.target && e.target.closest && e.target.closest(SEL);
        if (!mark) return;
        e.preventDefault();
        const sid = sessionOf(mark);
        const turn = mark.dataset.turnId;
        const figure = mark.textContent.trim();
        if (!sid || !turn) {
            open(mark, `Figure ${figure}`, `<p class="fl-empty">${esc(mark.dataset.tip || 'Lineage recorded on the turn.')}</p>`);
            return;
        }
        open(mark, `Figure ${figure}`, '<p class="fl-empty">Loading the rows…</p>');
        let compute = null;
        try { compute = await loadCompute(sid, turn); } catch (err) { compute = null; }
        if (!_open) return;
        const body = _open.root.querySelector('.fl-body');
        if (!compute) { body.innerHTML = '<p class="fl-empty">The turn\'s compute record is not available.</p>'; return; }
        if (mark.classList.contains('figure-receipted')) {
            const rc = (compute.receipts || [])[parseInt(mark.dataset.receipt, 10)];
            body.innerHTML = rc
                ? `<p class="fl-lead">Computed by code, not by the model: ${esc(mark.dataset.tip || '').replace(/^Computed:\s*/, '').replace(/\s*— click.*$/, '')}</p>${receiptRows(rc)}${combineHop(rc, compute)}${table(rc.sample_rows)}`
                : '<p class="fl-empty">No receipt matches this figure on the persisted turn.</p>';
        } else {
            const stN = parseInt(mark.dataset.statement, 10), row = parseInt(mark.dataset.row, 10);
            const st = (compute.statements || []).find(s => s && s.n === stN);
            const rc = (compute.receipts || []).find(r => Array.isArray(r.statements) && r.statements.includes(stN) && Array.isArray(r.sample_rows));
            // S9: a statement registered FROM an attachment carries its source (file, method,
            // region) and its own sample rows — the figure traces to the image region.
            const fromAttachment = st && st.source && window.AttachmentSources;
            if (fromAttachment) {
                body.innerHTML = window.AttachmentSources.statementHtml(st, sid)
                    + `<p class="fl-lead">Row ${row} of statement ${stN} — <code>${esc(st.statement)}</code></p>`
                    + (Array.isArray(st.sample_rows) && st.sample_rows.length ? table(st.sample_rows, row) : (rc ? table(rc.sample_rows, row) : `<p class="fl-empty">The attachment table holds ${fmt(st.row_count)} row(s).</p>`));
                try { window.AttachmentSources.hydrate(body, sid); } catch (_) { /* the crop is best-effort */ }
                return;
            }
            const rows = (st && Array.isArray(st.sample_rows) && st.sample_rows.length) ? st.sample_rows : (rc ? rc.sample_rows : null);
            // a literal the agent wrote INTO the statement (a filter, a bound): the lineage
            // is the statement itself, and its rows are what that literal selected
            // a figure DERIVED from a column (its sum or average over the complete rows): the
            // lineage is the column, and its rows are what was summed
            const stmt = st ? ` — <code>${esc(st.statement)}</code>` : '';
            const lead = mark.dataset.derived
                ? `Computed from the data: the ${esc(mark.dataset.derived)} of ${esc(mark.dataset.column || 'a column')} over ${st ? fmt(st.row_count) : 'the'} rows of statement ${stN}${stmt}`
                : mark.dataset.literal
                ? `Used as ${mark.dataset.literal === 'bound' ? 'the bound of' : 'a filter in'} statement ${stN}${stmt}`
                : `Quoted from the data: row ${row} of statement ${stN}${stmt}`;
            body.innerHTML = `<p class="fl-lead">${lead}</p>`
                + sourceLine(st, compute) + inputsHtml(st, compute)
                + (rows ? table(rows, row) : `<p class="fl-empty">The statement returned ${st ? fmt(st.row_count) : '?'} row(s); its rows were not carried on this turn's record.</p>`);
        }
    }

    document.addEventListener('click', onClick);
    // keyboard: Enter / Space on a focused figure opens its rows (the mark is role=button)
    document.addEventListener('keydown', (e) => {
        if ((e.key === 'Enter' || e.key === ' ') && e.target && e.target.matches && e.target.matches(SEL)) {
            e.preventDefault(); onClick({ target: e.target, preventDefault() {} });
        }
    });
    window.FigureLineage = { close, _sessionOf: sessionOf, _sourceLine: sourceLine, _inputsHtml: inputsHtml, _receiptRows: receiptRows };
})();
