/**
 * Planner Health — the planner repository's twin of Corpus Health.
 *
 * A champion case's SQL is REPLAYED verbatim on a retrieval hit, so the signing gate
 * refuses a planner whose cases carry destructive, mutating or stacked SQL, or a prompt
 * injection in the question. Before this panel the gate could say "stacked-statement" but
 * not WHICH case, and the only way to curate a case was a curl against the delete route.
 * This panel reads the SAME findings and the SAME verdict the gate computes
 * (`GET /v1/rag/collections/<id>/scan-manifest` → `planner_scan_manifest`), names each
 * flagged case with the text that tripped the scanner, and offers the two acts a planner
 * has: **Remove** (the case is wrong or fraudulent — it goes, from both stores) and
 * **Acknowledge** with a reason (the scanner is wrong — e.g. a semicolon inside a string
 * literal; the judgement is stamped on the case and freezes into the earned signature).
 * There is deliberately no Quarantine: a champion case has no "kept but excluded" state
 * worth having.
 *
 * Pure rendering (`renderPlannerHealth`) is separated from I/O so the gate
 * `test/test_planner_health.mjs` can read the markup without a DOM or a server. The
 * panel follows a write made elsewhere (REST, another tab) through the trust-event
 * notice the two routes broadcast (`planner_case_removed` · `planner_finding_ack`).
 */

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const chip = (level, label) => `<span class="trust-chip tc-${level}">${esc(label)}</span>`;
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
const shortQ = (q, n = 90) => { const t = String(q || '').replace(/\s+/g, ' ').trim(); return t.length <= n ? t : `${t.slice(0, n - 1).trim()}…`; };

const BTN = 'text-[10px] px-2 py-0.5 rounded u-tip';
const _btn = (act, caseId, label, tip, color) =>
    `<button type="button" class="${BTN}" data-ph-act="${act}" data-case-id="${esc(caseId)}" data-tip="${esc(tip)}"
        style="color: ${color}; border: 1px solid ${color}; background: transparent;">${esc(label)}</button>`;

/** One flagged case: the question, its labels, its state, the excerpt, the acts. */
function _findingRow(f, m) {
    const open = f.blocking.length && !f.acknowledged;
    const state = open ? chip('danger', 'unresolved')
        : (f.acknowledged ? chip('pass', 'acknowledged') : chip('warn', 'noted'));
    const labels = [...f.blocking, ...f.caveats].join(', ');
    const acts = [
        _btn('show', f.case_id, 'Show', 'Select this case in the table below — its plan and trace are what the agent replays.', 'var(--text-muted, #94a3b8)'),
        ...(open ? [_btn('ack', f.case_id, 'Acknowledge',
            'A recorded human judgement: the finding is benign (e.g. a semicolon inside a string\nliteral). Requires a reason; it is stamped on the case, freezes into the earned signature\nand unblocks signing.', 'var(--trust-pass, #34d399)')] : []),
        _btn('remove', f.case_id, 'Remove',
            'Delete this champion case from the repository — the vector store and the on-disk\nmirror the signing gate reads. The act is recorded; it cannot be undone.', 'var(--trust-danger, #f87171)'),
    ].join(' ');
    const ack = f.acknowledged && f.ack ? `<div class="mt-0.5" style="font-size: 11px; color: var(--text-muted);">acknowledged${f.ack.at ? ` ${esc(String(f.ack.at).slice(0, 10))}` : ''}: ${esc(f.ack.reason || '')}</div>` : '';
    return `<div class="py-1.5 ph-finding" data-case-id="${esc(f.case_id)}" style="border-top: 1px solid var(--border-secondary);">
        <div class="flex items-center gap-2 flex-wrap">
            <code class="u-tip" data-tip="${esc(f.case_id)}" style="color: var(--text-primary); font-size: 12px;">“${esc(shortQ(f.user_query))}”</code>
            <span style="font-size: 11px;">${esc(labels)}</span> ${state}
            ${f.is_most_efficient ? chip('warn', 'champion') : ''}
            <span style="margin-left: auto; display: inline-flex; gap: 6px;">${acts}</span>
        </div>
        ${f.excerpt ? `<pre class="mt-1 rounded p-2" style="font-size: 11px; white-space: pre-wrap; word-break: break-word; max-height: 7.5em; overflow: auto; margin: 0; background: var(--bg-overlay, rgba(127,127,127,0.06)); color: var(--text-muted); border: 1px solid var(--border-secondary);"><span style="color: var(--text-muted); opacity: .7;">${esc(f.argument || 'text')} · </span>${esc(f.excerpt)}</pre>` : ''}
        ${ack}
    </div>`;
}

/** The panel's markup from a manifest — pure, so the gate can read it. */
export function renderPlannerHealth(m, collectionId) {
    const findings = Array.isArray(m.findings) ? m.findings : [];
    const open = findings.filter((f) => f.blocking.length && !f.acknowledged);
    const acked = findings.filter((f) => f.blocking.length && f.acknowledged);
    const noted = findings.filter((f) => !f.blocking.length);
    const rows = [];
    if (!m.cases) {
        rows.push(`<div class="flex items-center gap-2 flex-wrap">${chip('warn', 'no champion cases')}
            <span style="font-size: 11px;">An empty planner cannot be signed — it learns a case from each successful turn, or is seeded from a pack.</span></div>`);
        return rows.join('');
    }
    rows.push(`<div class="flex items-center gap-2 flex-wrap mb-2" data-ph-summary>
        ${chip(open.length ? 'danger' : 'pass', `${plural(m.cases, 'case')} scanned`)}
        ${open.length ? chip('danger', `${plural(open.length, 'case')} block sign-off`) : chip('pass', 'no open findings')}
        ${acked.length ? chip('pass', `${acked.length} acknowledged`) : ''}
        ${noted.length ? chip('warn', `${noted.length} noted`) : ''}
        ${m.verdict && m.verdict.band ? `<span style="margin-left: auto; font-size: 11px; color: var(--text-muted);">gate: <span style="color: var(--text-primary);">${esc(m.verdict.band)}</span>${m.verdict.note ? ` — ${esc(m.verdict.note)}` : ''}</span>` : ''}
    </div>`);
    if (findings.length) {
        rows.push(`<div class="mb-2" style="font-size: 11px; opacity: .85;">Only
            <span style="color: var(--trust-danger, #f87171);">blocking findings</span> — destructive, mutating or
            stacked SQL, a prompt injection in the question — gate a signature; a SQL comment is noted and never
            blocks. A champion case is replayed verbatim on a hit, so remove a case that is wrong, and acknowledge
            one the scanner misreads.</div>`);
    }
    if (m.pack_owned) {
        rows.push(`<div class="mb-2" style="font-size: 11px; color: var(--text-muted);">This repository is pack-owned: a seed removed or acknowledged here is restored by the next pack update — fix it in the pack as well.</div>`);
    }
    if (open.length) {
        rows.push(`<div class="mt-1 mb-0.5" style="font-size: 10.5px; text-transform: uppercase; letter-spacing: .04em; color: var(--text-muted);">Blocking sign-off — champion cases the gate refuses</div>`);
        rows.push(...open.map((f) => _findingRow(f, m)));
    }
    if (acked.length) {
        rows.push(`<div class="mt-2 mb-0.5" style="font-size: 10.5px; text-transform: uppercase; letter-spacing: .04em; color: var(--text-muted);">Acknowledged — the owner's recorded judgement</div>`);
        rows.push(...acked.map((f) => _findingRow(f, m)));
    }
    if (noted.length) {
        rows.push(`<div class="mt-2 mb-0.5" style="font-size: 10.5px; text-transform: uppercase; letter-spacing: .04em; color: var(--text-muted);">Noted — does not block</div>`);
        rows.push(...noted.map((f) => _findingRow(f, m)));
    }
    return rows.join('');
}

// ── I/O ──────────────────────────────────────────────────────────────────────

const TOKEN = () => localStorage.getItem('tda_auth_token');
const _hdr = () => ({ Authorization: `Bearer ${TOKEN()}`, 'Content-Type': 'application/json' });
let _lastManifest = null;
let _wiredEl = null;

function _el() { return document.getElementById('planner-health-live'); }

/** Fetch the manifest and render it into #planner-health-live. */
export async function fetchPlannerHealth(collectionId) {
    const el = _el();
    if (!el) return null;
    _wire(el, collectionId);
    try {
        const res = await fetch(`/api/v1/rag/collections/${collectionId}/scan-manifest`, { headers: _hdr() });
        if (res.status === 403) { el.textContent = 'Planner health is the owner’s — a subscribed repository is curated by its publisher.'; return null; }
        if (!res.ok) { el.textContent = 'Planner health unavailable.'; return null; }
        const m = await res.json();
        _lastManifest = m;
        el.innerHTML = renderPlannerHealth(m, collectionId);
        return m;
    } catch (e) {
        el.textContent = 'Planner health unavailable.';
        return null;
    }
}

function _findingOf(caseId) {
    return ((_lastManifest && _lastManifest.findings) || []).find((f) => f.case_id === caseId) || null;
}

function _refreshRows() {
    try { if (window.ragRefreshInspectedRows) window.ragRefreshInspectedRows(); } catch (_) { /* the rows view is optional */ }
}

/** Remove ONE case — confirmed in the themed banner, never a native dialog. */
export function removePlannerCase(collectionId, caseId) {
    const f = _findingOf(caseId);
    const q = f ? shortQ(f.user_query, 120) : caseId;
    const go = async () => {
        try {
            const res = await fetch(`/api/v1/rag/collections/${collectionId}/rows/${encodeURIComponent(caseId)}`,
                { method: 'DELETE', headers: _hdr() });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.message || data.error || res.status);
            if (window.showAppBanner) window.showAppBanner(`Champion case removed — “${q}”. The repository’s signature reads changed until signed again.`, 'success', 6000);
        } catch (e) {
            if (window.showAppBanner) window.showAppBanner(`Remove failed: ${e.message}`, 'error', 6000);
        }
        await fetchPlannerHealth(collectionId);
        _refreshRows();
    };
    if (window.showConfirmBanner) {
        window.showConfirmBanner(`Remove the champion case “${q}” from this planner? It is deleted from the vector store and the on-disk mirror, and the act is recorded. This cannot be undone.`, go,
            { confirmText: 'Remove case', title: 'Remove this champion case' });
    } else {
        go();
    }
}

/** Acknowledge ONE case's finding with a reason — the themed prompt, never a native one. */
export async function acknowledgePlannerFinding(collectionId, caseId) {
    const f = _findingOf(caseId);
    const ask = window.showPromptModal;
    if (!ask) {
        if (window.showAppBanner) window.showAppBanner('Cannot collect an acknowledgement reason — the banner system did not load.', 'error', 6000);
        return;
    }
    const reason = await ask({
        title: 'Acknowledge this finding',
        context: [
            ...(f ? [{ k: 'Case', v: shortQ(f.user_query, 140) }] : []),
            ...(f && f.blocking.length ? [{ k: 'Finding', v: f.blocking.join(', ') }] : []),
        ],
        body: 'A recorded human judgement that this finding is benign. It unblocks sign-off for this '
            + 'repository and is stamped on the case, so anyone verifying the signature later reads your '
            + 'reason alongside the finding. A later, different finding on the same case opens it again.',
        label: 'Why is it benign?',
        placeholder: 'e.g. the semicolon sits inside a string literal — the statement is one read-only SELECT',
        hint: 'Say what the text actually is and why replaying it is safe. If the case is wrong or '
            + 'unsafe, remove it instead.',
        confirmText: 'Acknowledge',
        required: true,
    });
    if (!reason || !reason.trim()) return;
    try {
        const res = await fetch(`/api/v1/rag/collections/${collectionId}/rows/${encodeURIComponent(caseId)}/acknowledge-finding`,
            { method: 'POST', headers: _hdr(), body: JSON.stringify({ reason: reason.trim() }) });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.message || data.error || res.status);
        if (window.showAppBanner) window.showAppBanner('Finding acknowledged — the reason is stamped on the case and freezes into the repository’s signature.', 'success', 6000);
    } catch (e) {
        if (window.showAppBanner) window.showAppBanner(`Acknowledge failed: ${e.message}`, 'error', 6000);
    }
    await fetchPlannerHealth(collectionId);
    _refreshRows();
}

let _busOff = null;
let _followT = null;

/** One delegated click handler + one bus subscription per mounted container. */
function _wire(el, collectionId) {
    if (_wiredEl !== el) {
        el.addEventListener('click', (ev) => {
            const b = ev.target.closest('[data-ph-act]');
            if (!b) return;
            const cid = el.dataset.collectionId;
            const caseId = b.dataset.caseId;
            if (b.dataset.phAct === 'remove') removePlannerCase(cid, caseId);
            else if (b.dataset.phAct === 'ack') acknowledgePlannerFinding(cid, caseId);
            else if (b.dataset.phAct === 'show') { try { if (window.ragSelectCase) window.ragSelectCase(caseId); } catch (_) { /* optional */ } }
        });
        _wiredEl = el;
    }
    el.dataset.collectionId = String(collectionId);
    // Follow a write made elsewhere (REST, another tab): the two routes broadcast a
    // trust-event notice naming the collection; one subscriber, debounced.
    if (!_busOff && window.UderiaBus) {
        try {
            _busOff = window.UderiaBus.on('uderia:trust-event', (e) => {
                const d = (e && e.detail) || {};
                const t = String(d.event_type || '');
                if (t !== 'planner_case_removed' && t !== 'planner_finding_ack') return;
                const live = _el();
                if (!live || String(d.collection_id) !== String(live.dataset.collectionId)) return;
                clearTimeout(_followT);
                _followT = setTimeout(() => { fetchPlannerHealth(live.dataset.collectionId); _refreshRows(); }, 300);
            });
        } catch (_) { /* a page without the bus simply does not follow */ }
    }
}

if (typeof window !== 'undefined') {
    window.PlannerHealth = Object.freeze({ fetchPlannerHealth, renderPlannerHealth, removePlannerCase, acknowledgePlannerFinding });
}
