/**
 * KG instance lifecycle UI — update-from-source (diff → confirm) + version switcher.
 *
 * These are capabilities of an *instantiated* KG (not the constructor):
 *  - openKgUpdateDiff: re-sync a declarative KG against its source. Shows a
 *    layer-aware diff; on confirm, applies the update (which snapshots the prior
 *    graph as a switchable version).
 *  - openKgVersions: list saved versions and switch (restore) between them.
 *
 * Theme-aware (CSS variables); monochrome SVG icons; auth via tda_auth_token.
 */

import { showAppBanner } from '../../bannerSystem.js';

const TOKEN = () => localStorage.getItem('tda_auth_token');
const CONTENT_ID = 'kg-uv-content';
let _overlay = null;

function _esc(s) {
    return String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function _ensureOverlay() {
    if (_overlay) return;
    _overlay = document.createElement('div');
    _overlay.id = 'kg-uv-overlay';
    _overlay.className = 'fixed inset-0 z-[9999] flex items-center justify-center bg-black/60 backdrop-blur-sm hidden opacity-0 transition-opacity duration-200';
    _overlay.innerHTML = `
      <div id="${CONTENT_ID}" class="rounded-2xl shadow-2xl w-full max-w-lg max-h-[85vh] overflow-y-auto p-6 transform scale-95 opacity-0 transition-all duration-200"
           style="background:var(--card-bg,#111827);border:1px solid var(--border-primary,#374151);"></div>`;
    document.body.appendChild(_overlay);
    _overlay.addEventListener('click', (e) => { if (e.target === _overlay) _hide(); });
}

// Set modal content + wire EVERY close affordance (header X *and* any Cancel/Close
// button). The earlier code wired only querySelector('.kg-uv-close') — the first match
// (the header X) — so the Cancel button never closed the modal.
function _setContent(html) {
    _ensureOverlay();
    _overlay.querySelector(`#${CONTENT_ID}`).innerHTML = html;
    _overlay.querySelectorAll('.kg-uv-close').forEach(b => b.addEventListener('click', _hide));
}

function _show(html) {
    _setContent(html);
    _overlay.classList.remove('hidden');
    requestAnimationFrame(() => {
        _overlay.classList.remove('opacity-0');
        const c = _overlay.querySelector(`#${CONTENT_ID}`);
        c.classList.remove('scale-95', 'opacity-0');
        c.classList.add('scale-100', 'opacity-100');
    });
}

function _hide() {
    if (!_overlay) return;
    const c = _overlay.querySelector(`#${CONTENT_ID}`);
    c.classList.add('scale-95', 'opacity-0');
    _overlay.classList.add('opacity-0');
    setTimeout(() => _overlay.classList.add('hidden'), 200);
}

function _header(title, subtitle) {
    return `
      <div class="flex items-center justify-between mb-4">
        <div>
          <h3 class="text-lg font-bold" style="color:var(--text-primary,#fff);">${_esc(title)}</h3>
          ${subtitle ? `<p class="text-xs" style="color:var(--text-muted,#9ca3af);">${_esc(subtitle)}</p>` : ''}
        </div>
        <button class="kg-uv-close p-1" style="color:var(--text-muted,#9ca3af);">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
        </button>
      </div>`;
}

function _spinner(text) {
    return `<div class="flex items-center gap-2 text-sm py-6 justify-center" style="color:var(--text-muted,#9ca3af);">
        <svg class="w-4 h-4 animate-spin" style="color:var(--teradata-orange,#a855f7);" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path></svg>
        ${_esc(text)}</div>`;
}

// ── Update-from-source ───────────────────────────────────────────────────────

const _DIVISIONS = ['operations', 'business', 'corporate'];

// Read the user's current scope selection from the rendered toggles into an override dict.
function _readScopeOverride() {
    const o = {};
    _overlay.querySelectorAll('.kg-uv-opt').forEach(cb => {
        if (cb.dataset.opt !== 'physical') o[cb.dataset.opt] = cb.checked;
    });
    const phys = _overlay.querySelector('.kg-uv-opt[data-opt="physical"]');
    o.layers = (phys && phys.checked) ? ['logical', 'physical'] : ['logical'];
    const divs = [..._overlay.querySelectorAll('.kg-uv-div')].filter(c => c.checked).map(c => c.dataset.div);
    o.divisions = (divs.length === _DIVISIONS.length || divs.length === 0) ? null : divs;
    return o;
}

// Scope segment: installed options as toggles. Select to extend, deselect to reduce.
function _scopeSegment(opts) {
    const layers = Array.isArray(opts.layers) ? opts.layers : ['logical'];
    const physical = layers.includes('physical');
    const divs = opts.divisions; // null/[] = all
    const row = (id, label, checked, desc) => `
        <label class="flex items-center justify-between gap-3 py-1.5 cursor-pointer">
          <span class="min-w-0"><span class="text-sm" style="color:var(--text-primary,#e5e7eb);">${label}</span>
            ${desc ? `<span class="text-xs block" style="color:var(--text-muted,#9ca3af);">${desc}</span>` : ''}</span>
          <input type="checkbox" class="kg-uv-opt" data-opt="${id}" ${checked ? 'checked' : ''}
            style="width:16px;height:16px;flex-shrink:0;accent-color:var(--teradata-orange,#F15F22);cursor:pointer;">
        </label>`;
    const divChecked = d => (divs == null || !Array.isArray(divs) || divs.length === 0) ? true : divs.includes(d);
    const divRow = d => `
        <label class="flex items-center gap-1.5 cursor-pointer text-xs" style="color:var(--text-primary,#e5e7eb);">
          <input type="checkbox" class="kg-uv-div" data-div="${d}" ${divChecked(d) ? 'checked' : ''}
            style="width:14px;height:14px;accent-color:var(--teradata-orange,#F15F22);cursor:pointer;">
          <span style="text-transform:capitalize;">${d}</span></label>`;
    return `
      <div class="rounded-lg p-3 mb-3" style="background:var(--bg-overlay,rgba(30,30,40,0.4));border:1px solid var(--border-secondary,rgba(255,255,255,0.06));">
        <div class="text-xs font-semibold mb-1" style="color:var(--text-muted,#9ca3af);letter-spacing:0.04em;">SCOPE — select to extend, deselect to reduce</div>
        ${row('include_columns', 'Columns', !!opts.include_columns, 'table → column nodes + foreign-key edges')}
        ${row('include_metrics', 'Metrics', !!opts.include_metrics, 'metric nodes + measures edges')}
        ${row('include_glossary', 'Glossary', !!opts.include_glossary, 'business-glossary concept nodes')}
        ${row('physical', 'Physical layer (PDM)', physical, 'catalog / schema / physical-table DDL layer')}
        ${row('validate_with_source', 'Validate against the source data', !!opts.validate_with_source, 'sampled date shapes + ranges, declared-key uniqueness, blank ratios, row counts (one scan per table)')}
        <div class="mt-2 pt-2" style="border-top:1px solid var(--border-secondary,rgba(255,255,255,0.06));">
          <div class="text-xs mb-1" style="color:var(--text-muted,#9ca3af);">Divisions</div>
          <div class="flex gap-4 flex-wrap">${_DIVISIONS.map(divRow).join('')}</div>
        </div>
      </div>`;
}

// ── Workload tuning (PROP-008 C1) — the pure model, exported for the gate ──
// The offered act is the act that is OWED: pending → Approve / Reject (a rebuild proposal
// is advice — Reject only); approved → Land; landed / rejected / failed / expired → none.
const TUNING_PURPOSE = { statistics: 'Statistics', join_index: 'Covering join index', partition: 'Partition' };
const TUNING_STATE = {
    pending: ['worth a look', 'warn'], approved: ['approved — ready to land', 'warn'],
    landed: ['landed', 'pass'], failed: ['the database refused it', 'danger'],
    rejected: ['rejected', 'na'], expired: ['expired', 'na'],
};
// What the review found that is NOT a proposal — a view (tuned on its base tables), a table
// the agents read that the graph never showed. Bounded: the first four, the rest counted. Pure.
export function tuningFindingsHtml(findings) {
  const fs = (findings || []).filter(f => f && f.text);
  if (!fs.length) return '';
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const shown = fs.slice(0, 4).map(f => `<li>${esc(f.text)}</li>`).join('');
  const more = fs.length > 4 ? `<li>+${fs.length - 4} more</li>` : '';
  return `<ul class="kg-uv-tn-findings" style="margin:6px 0 0 16px;list-style:disc;color:var(--text-muted,#9ca3af);">${shown}${more}</ul>`;
}

export function tuningActs(p) {
    if (!p) return [];
    if (p.status === 'pending') return p.landing === 'rebuild' ? ['reject'] : ['approve', 'reject'];
    if (p.status === 'approved') return ['land'];
    return [];
}
export function tuningWords(p) {
    const pr = p.proposal || {};
    const what = `${TUNING_PURPOSE[p.purpose] || p.purpose} on <code>${_esc(p.table)}</code>`
        + ((pr.columns || []).length ? ` (${pr.columns.map(_esc).join(', ')})` : '');
    const rc = p.receipt || {};
    let tail = '';
    if (p.status === 'landed') {
        const b = rc.measured_before, a = rc.measured_after;
        const n = rc.measured_statements;
        tail = (b != null && a != null)
            ? `estimated ${b}s → ${a}s over ${n} workload statement${n === 1 ? '' : 's'}`
            : 'not measured (no workload statement could be explained)';
        // An estimate made without statistics is a guess: when the landing removed guesses,
        // the later figure is the first well-founded one — never read a rise as a regression.
        // Whether any workload statement now RUNS differently — a cost estimate can move with
        // nothing changed; the plan's shape says whether the landing did anything.
        const pc = rc.plans_changed;
        if (b != null && a != null && pc != null)
            tail += pc === 0 ? ' — no plan changed: the workload runs as before'
                : ` — ${pc} of ${n} plan${n === 1 ? '' : 's'} changed`;
        const ub = rc.unconfident_before, ua = rc.unconfident_after;
        if (b != null && a != null && ub != null && ua != null && ub > ua)
            tail += ` — before, ${ub} of ${n} plan${n === 1 ? '' : 's'} rested on a low- or no-confidence estimate (a guess); `
                + (ua === 0 ? 'the later figure is the first well-founded one' : `${ua} still are`);
        tail += ` · approved by ${_esc(rc.approved_by || '?')}` + ((rc.attestation || {}).signed ? ' · signed receipt' : ' · receipt not signed');
    } else if (p.status === 'failed' || p.status === 'expired' || p.status === 'rejected') {
        tail = _esc(p.note || '');
    } else if (p.landing === 'rebuild') {
        tail = 'needs the table rebuilt — the platform never rebuilds a table; this is advice';
    }
    return { what, evidence: _esc(pr.evidence || ''), state: TUNING_STATE[p.status] || [p.status, 'na'], tail };
}

export async function openKgUpdateDiff(kgId, kgName, sourceLabel, onDone) {
    const title = `Update “${kgName}”`;
    const sub = `Source: ${sourceLabel || 'declarative model'}`;
    let installed = {};   // the options stored on the KG (the baseline)
    let override = null;  // the user's adjusted scope (null until they touch a toggle)
    let recheckTimer = null;

    async function check(ov) {
        const r = await fetch('/api/v1/knowledge-graph/check-update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${TOKEN()}` },
            body: JSON.stringify(ov ? { kg_id: kgId, options_override: ov } : { kg_id: kgId }),
        });
        const res = await r.json();
        if (!r.ok && res.status !== 'not_resyncable') throw new Error(res.message || `HTTP ${r.status}`);
        return res;
    }

    function closeBtn(label) {
        return `<button class="kg-uv-close card-btn card-btn--primary w-full mt-2">${label || 'Close'}</button>`;
    }

    // Render the scope segment + diff body. Re-rendered (no animation) on each re-check.
    function paint(res, firstShow) {
        const scopeOpts = override || installed;
        const scopeChanged = override != null;
        let body;
        if (res.is_empty && !scopeChanged) {
            body = `<div class="flex items-center gap-2 text-sm py-3" style="color:#4ade80;">
                 <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                 Already up to date — no changes in the source.</div>`;
        } else {
            const c = res.diff.counts;
            const layerRows = Object.entries(res.diff.layer_summary || {}).map(([layer, s]) =>
                `<div class="flex justify-between text-xs py-0.5"><span style="color:var(--text-muted,#9ca3af);text-transform:capitalize;">${_esc(layer)}</span>
                  <span style="color:var(--text-primary,#d1d5db);">+${s.added} · −${s.removed} · ~${s.changed}</span></div>`).join('');
            const sample = (arr, sign, color) => (arr || []).slice(0, 6).map(e =>
                `<div class="text-xs font-mono" style="color:${color};">${sign} ${_esc(e.name || `${e.source_name}→${e.target_name}`)}</div>`).join('');
            // "What's changing" — the changed-property breakdown makes a big `changed`
            // count legible (usually metamodel ENRICHMENT, e.g. references / definitions
            // gained, not corruption).
            const ps = res.diff.changed_prop_summary || {};
            const psEntries = Object.entries(ps).slice(0, 6);
            const propRow = psEntries.length
                ? `<div class="mt-2 pt-2" style="border-top:1px solid var(--border-secondary,rgba(255,255,255,0.06));">
                     <div class="text-xs mb-1" style="color:var(--text-muted,#9ca3af);">Properties changing on existing nodes:</div>
                     <div class="flex flex-wrap gap-1">${psEntries.map(([k, n]) =>
                        `<span class="text-xs px-2 py-0.5 rounded" style="background:rgba(251,191,36,0.12);color:#fbbf24;">${_esc(k)} ×${n.toLocaleString()}</span>`).join('')}</div></div>`
                : '';
            body = `<div class="rounded-lg p-3 mb-3" style="background:var(--bg-overlay,rgba(30,30,40,0.4));border:1px solid var(--border-secondary,rgba(255,255,255,0.06));">
               <div class="grid grid-cols-3 gap-2 text-center text-xs mb-2">
                 <div><div class="text-lg font-bold" style="color:#4ade80;">+${c.entities_added}</div><div style="color:var(--text-muted,#9ca3af);">added</div></div>
                 <div><div class="text-lg font-bold" style="color:#f87171;">−${c.entities_removed}</div><div style="color:var(--text-muted,#9ca3af);">removed</div></div>
                 <div><div class="text-lg font-bold" style="color:#fbbf24;">~${c.entities_changed}</div><div style="color:var(--text-muted,#9ca3af);">changed</div></div>
               </div>
               <div class="text-xs text-center" style="color:var(--text-muted,#9ca3af);">relationships: +${c.relationships_added} · −${c.relationships_removed}</div>
               ${layerRows ? `<div class="mt-2 pt-2" style="border-top:1px solid var(--border-secondary,rgba(255,255,255,0.06));">${layerRows}</div>` : ''}
               ${propRow}
             </div>
             <div class="max-h-32 overflow-y-auto mb-3 space-y-0.5">
               ${sample(res.diff.entities_added, '+', '#4ade80')}
               ${sample(res.diff.entities_removed, '−', '#f87171')}
             </div>`;
        }
        const noChange = res.is_empty && !scopeChanged;
        const html = _header(title, sub) +
            _scopeSegment(scopeOpts) + _demoDataSegment(res.descriptor) + _metricViewsSegment(res.descriptor) + _physicalSegment(res.descriptor) + _tuningSegment() + body +
            `<p class="text-xs mb-3" style="color:var(--text-muted,#9ca3af);">The current graph will be saved as a version before applying, so you can switch back.</p>
             <div class="flex gap-2">
               <button class="kg-uv-close card-btn card-btn--secondary flex-1">Cancel</button>
               <button id="kg-uv-apply" class="card-btn card-btn--primary flex-1" ${noChange ? 'disabled style="opacity:0.5;cursor:default;"' : ''}>Apply update</button>
             </div>`;
        if (firstShow) _show(html); else _setContent(html);
        _wireScope();
        _wireApply();
        _wireDemoData(res.descriptor);
        _wireMetricViews(res.descriptor);
        _wirePhysical(res.descriptor);
        _wireTuning();
    }

    // ── Workload tuning: the recorded workload of the agents grounded on this graph, reviewed
    // against its deployed tables; proposals are decided by a person and only then landed. ──
    function _tuningSegment() {
        return `<div id="kg-uv-tn" class="rounded-lg p-3 mb-3" style="display:none;background:var(--bg-overlay,rgba(30,30,40,0.4));border:1px solid var(--border-secondary,rgba(255,255,255,0.06));">
          <div class="text-xs font-semibold mb-1" style="color:var(--text-primary,#e5e7eb);">Workload tuning</div>
          <div class="text-xs mb-2" style="color:var(--text-muted,#9ca3af);">Review what the agents grounded on this graph actually asked of its tables — EXPLAIN plans and the dictionary, read only. Each proposal names its evidence; nothing is applied until you approve it and land it, and a landing is measured before and after.</div>
          <div class="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs" style="color:var(--text-muted,#9ca3af);">
            <label class="flex items-center gap-1.5">Connection <select id="kg-uv-tn-target" class="rounded p-1 text-xs outline-none cursor-pointer"
                  style="background:var(--bg-overlay,rgba(30,30,40,0.8));border:1px solid var(--border-secondary,rgba(241,95,34,0.25));color:var(--text-primary,#fff);"><option value="">loading…</option></select></label>
            <button id="kg-uv-tn-run" class="card-btn card-btn--secondary" style="padding:2px 10px;">Review workload</button>
          </div>
          <div id="kg-uv-tn-out" class="text-xs mt-2" style="color:var(--text-muted,#9ca3af);"></div>
          <div id="kg-uv-tn-list" class="mt-2 space-y-1.5"></div>
        </div>`;
    }

    function _tuningRow(p) {
        const w = tuningWords(p);
        const [word, tone] = w.state;
        const color = { pass: 'var(--trust-pass,#4ade80)', warn: 'var(--trust-warn,#fbbf24)', danger: 'var(--trust-danger,#f87171)' }[tone] || 'var(--text-muted,#9ca3af)';
        const btn = (act, label, primary) => `<button class="kg-uv-tn-act card-btn ${primary ? 'card-btn--primary' : 'card-btn--secondary'}" data-act="${act}" data-id="${_esc(p.id)}" style="padding:1px 8px;">${label}</button>`;
        const labels = { approve: ['Approve', true], reject: ['Reject', false], land: ['Land', true] };
        const acts = tuningActs(p).map(a => btn(a, labels[a][0], labels[a][1])).join(' ');
        const ddl = ((p.proposal || {}).ddl || []).map(_esc).join('\n');
        return `<div class="rounded p-2" data-tuning-id="${_esc(p.id)}" style="border:1px solid var(--border-secondary,rgba(255,255,255,0.06));">
            <div class="flex items-center gap-2 text-xs"><span style="color:var(--text-primary,#e5e7eb);">${w.what}</span>
              <span class="ml-auto px-1.5 rounded" style="border:1px solid ${color};color:${color};">${_esc(word)}</span></div>
            ${w.evidence ? `<div class="text-xs mt-1" style="color:var(--text-muted,#9ca3af);">${w.evidence}</div>` : ''}
            ${w.tail ? `<div class="text-xs mt-1" style="color:var(--text-muted,#9ca3af);">${w.tail}</div>` : ''}
            <div class="flex items-center gap-2 mt-1">${acts}
              ${ddl ? `<details class="text-xs ml-auto"><summary class="cursor-pointer" style="color:var(--text-muted,#9ca3af);">Statements</summary><pre class="whitespace-pre-wrap mt-1" style="color:var(--text-primary,#d1d5db);">${ddl}</pre></details>` : ''}</div>
          </div>`;
    }

    function _paintTuning(rows) {
        const list = _overlay.querySelector('#kg-uv-tn-list');
        if (!list) return;
        list.innerHTML = (rows || []).length ? rows.map(_tuningRow).join('')
            : '<div class="text-xs" style="color:var(--text-muted,#9ca3af);">No proposals yet — review the workload to derive them.</div>';
    }

    async function _wireTuning() {
        const box = _overlay.querySelector('#kg-uv-tn');
        const sel = _overlay.querySelector('#kg-uv-tn-target');
        const run = _overlay.querySelector('#kg-uv-tn-run');
        if (!box || !sel || !run) return;
        const H = { 'Content-Type': 'application/json', Authorization: `Bearer ${TOKEN()}` };
        let rows = [];
        try {
            const r = await fetch(`/api/v1/knowledge-graph/tuning/proposals?kg_id=${encodeURIComponent(kgId)}`, { headers: H });
            if (!r.ok) return;                       // OBDA-MODEL · OBDA-ACT off on this deployment — the segment stays hidden
            rows = (await r.json()).proposals || [];
        } catch (_) { return; }
        box.style.display = '';
        await _fillConnections(sel);
        if (!sel.value && sel.options.length === 2) sel.selectedIndex = 1;   // the only connection
        _paintTuning(rows);
        const out = _overlay.querySelector('#kg-uv-tn-out');
        run.addEventListener('click', async () => {
            if (!sel.value) { showAppBanner('Select the connection the graph is deployed on.', 'error'); return; }
            run.disabled = true; out.textContent = 'Reading the recorded workload, EXPLAIN plans and the dictionary…';
            try {
                const r = await fetch('/api/v1/knowledge-graph/tuning/review', { method: 'POST', headers: H,
                    body: JSON.stringify({ kg_id: kgId, connection_id: sel.value }) });
                const res = await r.json();
                if (!r.ok || res.status !== 'success') throw new Error(res.message || `HTTP ${r.status}`);
                const wl = (res.review || {}).workload || {}, ex = (res.review || {}).explain || {};
                const pending = (res.proposals || []).filter(p => p.status === 'pending').length;
                out.innerHTML = `${wl.statements ?? 0} recorded statement${wl.statements === 1 ? '' : 's'} by ${(wl.agents || []).map(t => '@' + _esc(t)).join(', ') || 'this graph’s agents'}`
                    + ` · ${ex.explained ?? 0} explained · <strong>${pending} proposal${pending === 1 ? '' : 's'} awaiting you</strong>`
                    + (((res.review || {}).unavailable || []).length ? ` · could not read: ${res.review.unavailable.map(_esc).join('; ')}` : '') + '.'
                    + tuningFindingsHtml((res.review || {}).findings);
                rows = res.proposals || [];
                _paintTuning(rows);
            } catch (err) {
                out.innerHTML = `<span style="color:var(--trust-danger,#f87171);">Failed: ${_esc(err.message)}</span>`;
            } finally { run.disabled = false; }
        });
        _overlay.querySelector('#kg-uv-tn-list').addEventListener('click', async (e) => {
            const b = e.target.closest('.kg-uv-tn-act');
            if (!b) return;
            const act = b.dataset.act, id = b.dataset.id;
            b.disabled = true; const prev = b.textContent;
            b.textContent = act === 'land' ? 'Landing…' : '…';
            try {
                const r = await fetch(`/api/v1/knowledge-graph/tuning/proposals/${encodeURIComponent(id)}/${act}`, {
                    method: 'POST', headers: H, body: JSON.stringify(act === 'land' && sel.value ? { connection_id: sel.value } : {}) });
                const res = await r.json();
                const p = res.proposal;
                if (p) rows = rows.map(x => x.id === id ? p : x);
                _paintTuning(rows);
                if (!r.ok && !p) throw new Error(res.message || `HTTP ${r.status}`);
                if (act === 'land') showAppBanner(p && p.status === 'landed' ? 'Tuning landed and measured.' : 'The database refused the tuning statement.', p && p.status === 'landed' ? 'success' : 'warning');
            } catch (err) {
                showAppBanner(`Could not ${act}: ${err.message}`, 'error');
                b.disabled = false; b.textContent = prev;
            }
        });
    }

    // ── Demo data (industry-model graphs only): regenerate the deployed rows in place ──
    // Data only — tables, keys and the physical layer stay as deployed; seconds, not a
    // 30-minute re-deploy.  The connection defaults to the one recorded on the graph.
    function _demoDataSegment(desc) {
        if (!desc || desc.strategy !== 'industry_model') return '';
        const d = desc.deploy || {};
        const sel = (id, opts, cur) => `<select id="${id}" class="rounded p-1 text-xs outline-none cursor-pointer"
              style="background:var(--bg-overlay,rgba(30,30,40,0.8));border:1px solid var(--border-secondary,rgba(241,95,34,0.25));color:var(--text-primary,#fff);">
              ${opts.map(([v, l]) => `<option value="${v}"${v === cur ? ' selected' : ''}>${l}</option>`).join('')}</select>`;
        const num = (id, val, w) => `<input id="${id}" type="number" min="1" value="${val}" class="${w} rounded p-1 text-xs outline-none"
              style="background:var(--bg-overlay,rgba(30,30,40,0.8));border:1px solid var(--border-secondary,rgba(241,95,34,0.25));color:var(--text-primary,#fff);"/>`;
        return `<div class="rounded-lg p-3 mb-3" style="background:var(--bg-overlay,rgba(30,30,40,0.4));border:1px solid var(--border-secondary,rgba(255,255,255,0.06));">
          <div class="text-xs font-semibold mb-1" style="color:var(--text-primary,#e5e7eb);">Demo data</div>
          <div class="text-xs mb-2" style="color:var(--text-muted,#9ca3af);">Regenerate the deployed rows in place — tables, keys and the graph's physical layer stay as they are.</div>
          <div class="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs" style="color:var(--text-muted,#9ca3af);">
            <label class="flex items-center gap-1.5">Profile ${sel('kg-uv-dd-profile', [['semantic', 'semantic (realistic)'], ['basic', 'basic (uniform)']], d.data_profile || 'semantic')}</label>
            <label class="flex items-center gap-1.5">Base rows ${num('kg-uv-dd-rows', d.rows_per_table || 20, 'w-20')}</label>
            <label class="flex items-center gap-1.5">Max rows ${num('kg-uv-dd-max', d.max_rows_per_model || 200000, 'w-24')}</label>
            <label class="flex items-center gap-1.5">Connection <select id="kg-uv-dd-target" data-current="${_esc(d.deployment_target_id || '')}" class="rounded p-1 text-xs outline-none cursor-pointer"
                  style="background:var(--bg-overlay,rgba(30,30,40,0.8));border:1px solid var(--border-secondary,rgba(241,95,34,0.25));color:var(--text-primary,#fff);"><option value="">loading…</option></select></label>
            <button id="kg-uv-dd-run" class="card-btn card-btn--secondary" style="padding:2px 10px;">Regenerate demo data</button>
          </div>
          <div id="kg-uv-dd-out" class="text-xs mt-2" style="color:var(--text-muted,#9ca3af);"></div>
        </div>`;
    }

    // ── physical model: re-deploy in place (metric views, rename the database) ──
    // ── Metric views (deployed industry-model graphs only): re-land the views, nothing else ──
    // A metric view is derived (compiled from the model's expression), so a compiler fix
    // reaches the deployed model here in seconds: each view is REPLACE VIEWed in place —
    // atomic, a definition Teradata still refuses leaves the existing view standing — and the
    // graph's physical layer is patched to what exists. Tables, rows, statistics untouched.
    function _metricViewsSegment(desc) {
        if (!desc || desc.strategy !== 'industry_model' || !desc.deploy) return '';
        const d = desc.deploy || {};
        const last = d.metric_views || null;
        const cb = (id, label, on, tip) => `<label class="flex items-center gap-1.5 cursor-pointer" title="${_esc(tip || '')}"><input id="${id}" type="checkbox"${on ? ' checked' : ''}/> ${label}</label>`;
        const lastLine = last ? `Last landed ${_esc(String(last.at || '').replace('T', ' ').slice(0, 16))} — ${last.created ?? 0} created/replaced` +
            ((last.refused || []).length ? `, <span style="color:#fbbf24;">${last.refused.length} still refused</span>` : '') + '.' : '';
        return `<div class="rounded-lg p-3 mb-3" style="background:var(--bg-overlay,rgba(30,30,40,0.4));border:1px solid var(--border-secondary,rgba(255,255,255,0.06));">
          <div class="text-xs font-semibold mb-1" style="color:var(--text-primary,#e5e7eb);">Metric views</div>
          <div class="text-xs mb-2" style="color:var(--text-muted,#9ca3af);">Re-land the model's metric views only — recompiled with the current compiler and replaced in place; tables, rows and statistics are untouched (seconds, not a redeploy). A view the database still refuses is named, never silently dropped.</div>
          <div class="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs" style="color:var(--text-muted,#9ca3af);">
            ${cb('kg-uv-mv-missing', 'Only the missing ones', true, 'Create only the views absent from the database; unchecked replaces every metric view with the current compiler output')}
            <label class="flex items-center gap-1.5">Connection <select id="kg-uv-mv-target" data-current="${_esc(d.deployment_target_id || '')}" class="rounded p-1 text-xs outline-none cursor-pointer"
                  style="background:var(--bg-overlay,rgba(30,30,40,0.8));border:1px solid var(--border-secondary,rgba(241,95,34,0.25));color:var(--text-primary,#fff);"><option value="">loading…</option></select></label>
            <button id="kg-uv-mv-check" class="card-btn card-btn--secondary" style="padding:2px 10px;" title="Dry run — lists the views the database is missing; changes nothing">Check</button>
            <button id="kg-uv-mv-run" class="card-btn card-btn--secondary" style="padding:2px 10px;">Land metric views</button>
          </div>
          <div id="kg-uv-mv-out" class="text-xs mt-2" style="color:var(--text-muted,#9ca3af);">${lastLine}</div>
        </div>`;
    }

    async function _wireMetricViews(desc) {
        const sel = _overlay.querySelector('#kg-uv-mv-target');
        const check = _overlay.querySelector('#kg-uv-mv-check');
        const run = _overlay.querySelector('#kg-uv-mv-run');
        if (!sel || !check || !run) return;
        await _fillConnections(sel);
        const call = async (dryRun) => {
            const out = _overlay.querySelector('#kg-uv-mv-out');
            const target = sel.value;
            if (!target) { showAppBanner('Select the connection the model is deployed on.', 'error'); return; }
            const missingOnly = !!_overlay.querySelector('#kg-uv-mv-missing')?.checked;
            check.disabled = run.disabled = true;
            out.textContent = dryRun ? 'Checking which metric views the database holds…' : 'Landing metric views…';
            try {
                const r = await fetch('/api/v1/knowledge-graph/redeploy-metric-views', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${TOKEN()}` },
                    body: JSON.stringify({ kg_id: kgId, deployment_target_id: target, missing_only: missingOnly, dry_run: dryRun }),
                });
                const res = await r.json();
                if (!r.ok || res.status !== 'success') throw new Error(res.message || `HTTP ${r.status}`);
                const mv = res.metric_views || {};
                const missing = mv.missing_before || [];
                const names = (arr) => arr.map(v => `<code>${_esc(String(v).split('.').pop())}</code>`).join(', ');
                if (dryRun) {
                    out.innerHTML = `${mv.views_in_model ?? 0} metric views in the model · <strong>${missing.length} missing</strong> on the database`
                        + (missing.length ? `: ${names(missing)}` : '') + ` · would run ${(mv.planned || []).length}.`;
                    return;
                }
                const refused = mv.refused || [];
                out.innerHTML = `<span style="color:#4ade80;">Done</span> — ${(mv.created || []).length} created/replaced`
                    + (missing.length ? ` (${missing.filter(v => (mv.created || []).includes(v)).length} of ${missing.length} missing landed)` : '')
                    + (refused.length ? `; <span style="color:#fbbf24;">${refused.length} still refused</span>: ` +
                       refused.map(x => `<code>${_esc(String(x.view).split('.').pop())}</code> — ${_esc(x.reason || '')}`).join('; ') : '')
                    + ((mv.ghosts_removed || []).length ? `; ${mv.ghosts_removed.length} stale artifact(s) removed from the graph` : '') + '.';
                showAppBanner(`Metric views landed for “${kgName}”.`, refused.length ? 'warning' : 'success');
            } catch (err) {
                out.innerHTML = `<span style="color:#f87171;">Failed: ${_esc(err.message)}</span>`;
            } finally {
                check.disabled = run.disabled = false;
            }
        };
        check.addEventListener('click', () => call(true));
        run.addEventListener('click', () => call(false));
    }

    function _physicalSegment(desc) {
        if (!desc || desc.strategy !== 'industry_model' || !desc.deploy) return '';
        const d = desc.deploy || {};
        const root = d.root_database || (desc.options || {}).db_name || '';
        const inp = (id, val, w, ph) => `<input id="${id}" type="text" value="${_esc(val)}" placeholder="${_esc(ph || '')}" class="${w} rounded p-1 text-xs outline-none"
              style="background:var(--bg-overlay,rgba(30,30,40,0.8));border:1px solid var(--border-secondary,rgba(241,95,34,0.25));color:var(--text-primary,#fff);"/>`;
        const cb = (id, label, on, tip) => `<label class="flex items-center gap-1.5 cursor-pointer" title="${_esc(tip || '')}"><input id="${id}" type="checkbox"${on ? ' checked' : ''}/> ${label}</label>`;
        return `<div class="rounded-lg p-3 mb-3" style="background:var(--bg-overlay,rgba(30,30,40,0.4));border:1px solid var(--border-secondary,rgba(255,255,255,0.06));">
          <div class="text-xs font-semibold mb-1" style="color:var(--text-primary,#e5e7eb);">Physical model</div>
          <div class="text-xs mb-2" style="color:var(--text-muted,#9ca3af);">Re-deploy the tables, keys, demo data and statistics in place (same graph) — turn metric views on, or move the model to a differently named database; a renamed model's previous database is dropped only if everything in it is the model's own.</div>
          <div class="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs" style="color:var(--text-muted,#9ca3af);">
            <label class="flex items-center gap-1.5">Database ${inp('kg-uv-ph-root', root, 'w-40', root)}</label>
            ${cb('kg-uv-ph-views', 'Metric views', !!d.deploy_metric_views, 'Emit one Teradata view per metric of the model (the metric semantic layer)')}
            ${cb('kg-uv-ph-drop', 'Drop previous database on rename', true, 'Refused (and named) if that database holds any object the model does not own')}
            <label class="flex items-center gap-1.5">Connection <select id="kg-uv-ph-target" data-current="${_esc(d.deployment_target_id || '')}" class="rounded p-1 text-xs outline-none cursor-pointer"
                  style="background:var(--bg-overlay,rgba(30,30,40,0.8));border:1px solid var(--border-secondary,rgba(241,95,34,0.25));color:var(--text-primary,#fff);"><option value="">loading…</option></select></label>
            <button id="kg-uv-ph-run" class="card-btn card-btn--secondary" style="padding:2px 10px;">Redeploy physical model</button>
          </div>
          <div id="kg-uv-ph-out" class="text-xs mt-2" style="color:var(--text-muted,#9ca3af);"></div>
        </div>`;
    }

    async function _fillConnections(sel) {
        try {
            const r = await fetch('/api/v1/database-connections', { headers: { Authorization: `Bearer ${TOKEN()}` } });
            const data = await r.json();
            const conns = (data.connections || []).filter(c => (c.driver || '').toLowerCase() === 'teradata');
            const cur = sel.dataset.current || '';
            sel.innerHTML = (conns.length ? '<option value="">Select a connection…</option>' : '<option value="">No Teradata connections</option>') +
                conns.map(c => `<option value="${c.id}"${c.id === cur ? ' selected' : ''}>${_esc(c.name || c.id)}${c.host ? ' · ' + _esc(c.host) : ''}</option>`).join('');
        } catch (err) {
            sel.innerHTML = `<option value="">Could not load connections</option>`;
        }
    }

    async function _wirePhysical(desc) {
        const sel = _overlay.querySelector('#kg-uv-ph-target');
        const btn = _overlay.querySelector('#kg-uv-ph-run');
        if (!sel || !btn) return;
        await _fillConnections(sel);
        btn.addEventListener('click', async () => {
            const out = _overlay.querySelector('#kg-uv-ph-out');
            const target = sel.value;
            if (!target) { showAppBanner('Select the connection to deploy to.', 'error'); return; }
            const root = (_overlay.querySelector('#kg-uv-ph-root')?.value || '').trim();
            const prev = ((desc.deploy || {}).root_database || (desc.options || {}).db_name || '');
            const renaming = root && prev && root.toLowerCase() !== prev.toLowerCase();
            btn.disabled = true; btn.textContent = 'Redeploying…';
            out.textContent = renaming
                ? `Rebuilding the model in ${root}, loading demo data, then dropping ${prev} — 10-20 minutes for a large model.`
                : 'Re-creating the tables and loading demo data in place — 10-20 minutes for a large model.';
            try {
                const r = await fetch('/api/v1/knowledge-graph/redeploy-physical', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${TOKEN()}` },
                    body: JSON.stringify({
                        kg_id: kgId, deployment_target_id: target,
                        root_database: root || null,
                        deploy_metric_views: !!_overlay.querySelector('#kg-uv-ph-views')?.checked,
                        drop_previous_root: !!_overlay.querySelector('#kg-uv-ph-drop')?.checked,
                        kg_name: renaming ? root : null,
                    }),
                });
                const res = await r.json();
                if (!r.ok || res.status !== 'success') throw new Error(res.message || `HTTP ${r.status}`);
                const rd = res.redeploy || {};
                const views = (rd.convert_summary || {}).views;
                const dropNote = rd.previous_root_database && rd.previous_root_database !== rd.root_database
                    ? (rd.previous_root_dropped ? ` · ${_esc(rd.previous_root_database)} dropped`
                       : ` · <span style="color:#fbbf24;">${_esc(rd.previous_root_database)} kept: ${_esc(rd.previous_root_drop_error || '')}</span>`)
                    : '';
                out.innerHTML = `<span style="color:#4ade80;">Done</span> — ${(rd.convert_summary || {}).tables ?? ''} tables${views != null ? `, ${views} metric views` : ''} in ${_esc(rd.root_database || '')}; ${(rd.load?.total_rows || 0).toLocaleString()} rows${dropNote}.`;
                showAppBanner(`Physical model redeployed for “${renaming ? root : kgName}”.`, 'success');
                if (renaming) setTimeout(() => UderiaBus.announce('knowledge_graph', 'created'), 300);
            } catch (err) {
                out.innerHTML = `<span style="color:#f87171;">Failed: ${_esc(err.message)}</span>`;
            } finally {
                btn.disabled = false; btn.textContent = 'Redeploy physical model';
            }
        });
    }

    async function _wireDemoData(desc) {
        const sel = _overlay.querySelector('#kg-uv-dd-target');
        const btn = _overlay.querySelector('#kg-uv-dd-run');
        if (!sel || !btn) return;
        await _fillConnections(sel);
        btn.addEventListener('click', async () => {
            const out = _overlay.querySelector('#kg-uv-dd-out');
            const target = sel.value;
            if (!target) { showAppBanner('Select the connection the model was deployed to.', 'error'); return; }
            btn.disabled = true; btn.textContent = 'Regenerating…';
            out.textContent = 'Emptying the tables and loading fresh rows — a minute or two for a large model.';
            try {
                const r = await fetch('/api/v1/knowledge-graph/reload-demo-data', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${TOKEN()}` },
                    body: JSON.stringify({
                        kg_id: kgId, deployment_target_id: target,
                        data_profile: _overlay.querySelector('#kg-uv-dd-profile')?.value || 'semantic',
                        rows_per_table: parseInt(_overlay.querySelector('#kg-uv-dd-rows')?.value, 10) || 20,
                        max_rows_per_model: parseInt(_overlay.querySelector('#kg-uv-dd-max')?.value, 10) || 200000,
                    }),
                });
                const res = await r.json();
                if (!r.ok || res.status !== 'success') throw new Error(res.message || `HTTP ${r.status}`);
                const rl = res.reload || {};
                const roles = Object.entries(rl.data_profile?.tables_by_role || {}).map(([k, n]) => `${n} ${k}`).join(', ');
                const errs = (rl.load?.errors || []).length + (rl.clear?.errors || []).length;
                out.innerHTML = `<span style="color:${errs ? '#fbbf24' : '#4ade80'};">${rl.status === 'success' ? 'Done' : 'Partial'}</span> — ${(rl.load?.total_rows || 0).toLocaleString()} rows into ${_esc(rl.root_database || '')} (${rl.tables} tables${roles ? '; ' + roles : ''})${errs ? ` · ${errs} table(s) reported an error` : ''}.`;
                showAppBanner(`Demo data regenerated for “${kgName}”: ${(rl.load?.total_rows || 0).toLocaleString()} rows.`, errs ? 'warning' : 'success');
            } catch (err) {
                out.innerHTML = `<span style="color:#f87171;">Failed: ${_esc(err.message)}</span>`;
            } finally {
                btn.disabled = false; btn.textContent = 'Regenerate demo data';
            }
        });
    }

    function _wireScope() {
        _overlay.querySelectorAll('.kg-uv-opt, .kg-uv-div').forEach(cb => cb.addEventListener('change', () => {
            override = _readScopeOverride();
            // Mark the diff area as recomputing without losing the toggles.
            const ap = _overlay.querySelector('#kg-uv-apply');
            if (ap) { ap.disabled = true; ap.textContent = 'Recomputing…'; }
            if (recheckTimer) clearTimeout(recheckTimer);
            recheckTimer = setTimeout(async () => {
                try {
                    const res = await check(override);
                    if (res.status === 'not_resyncable') { showAppBanner(res.message, 'error'); return; }
                    paint(res, false);
                } catch (err) {
                    showAppBanner(`Re-check failed: ${err.message}`, 'error');
                }
            }, 280);
        }));
    }

    function _wireApply() {
        const btn = _overlay.querySelector('#kg-uv-apply');
        if (!btn || btn.disabled) return;
        btn.addEventListener('click', async () => {
            _setContent(_header(title) + _spinner('Applying update…'));
            try {
                const r = await fetch('/api/v1/knowledge-graph/apply-update', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${TOKEN()}` },
                    body: JSON.stringify(override ? { kg_id: kgId, options_override: override } : { kg_id: kgId }),
                });
                const out = await r.json();
                if (!r.ok || out.status !== 'success') throw new Error(out.message || `HTTP ${r.status}`);
                showAppBanner(`“${kgName}” updated: ${out.entities_added} nodes, ${out.relationships_added} relationships.`, 'success');
                _hide();
                onDone?.();
            } catch (err) {
                showAppBanner(`Update failed: ${err.message}`, 'error');
            }
        });
    }

    // Initial check (no override) — establishes the installed scope baseline.
    _show(_header(title, sub) + _spinner('Checking source for changes…'));
    let res;
    try {
        res = await check(null);
    } catch (err) {
        _show(_header(title) + `<p class="text-sm py-4" style="color:#f87171;">Check failed: ${_esc(err.message)}</p>` + closeBtn());
        return;
    }
    if (res.status === 'not_resyncable') {
        _show(_header(title) + `<p class="text-sm py-4" style="color:var(--text-muted,#9ca3af);">${_esc(res.message)}</p>` + closeBtn());
        return;
    }
    installed = res.options || (res.descriptor && res.descriptor.options) || {};
    paint(res, true);
}

// ── Version switcher ─────────────────────────────────────────────────────────

export async function openKgVersions(kgId, profileId, kgName, onDone) {
    _show(_header(`Versions — “${kgName}”`) + _spinner('Loading version history…'));
    let versions = [];
    try {
        const r = await fetch(`/api/v1/knowledge-graph/versions?kg_id=${encodeURIComponent(kgId)}`, {
            headers: { Authorization: `Bearer ${TOKEN()}` },
        });
        const data = await r.json();
        if (!r.ok || data.status !== 'success') throw new Error(data.message || `HTTP ${r.status}`);
        versions = data.versions || [];
    } catch (err) {
        _show(_header(`Versions — “${kgName}”`) + `<p class="text-sm py-4" style="color:#f87171;">${_esc(err.message)}</p>
          <button class="kg-uv-close card-btn card-btn--primary w-full mt-2">Close</button>`);
        return;
    }

    if (!versions.length) {
        _show(_header(`Versions — “${kgName}”`) + `<p class="text-sm py-4" style="color:var(--text-muted,#9ca3af);">No saved versions yet. Versions are created on update and on manual snapshot.</p>
          <button class="kg-uv-close card-btn card-btn--primary w-full mt-2">Close</button>`);
        return;
    }

    // newest first
    const rows = [...versions].reverse().map(v => `
        <div class="flex items-center justify-between py-2 px-2 rounded-lg" style="border-bottom:1px solid var(--border-secondary,rgba(255,255,255,0.06));">
          <div class="min-w-0">
            <div class="text-sm truncate" style="color:var(--text-primary,#e5e7eb);">v${v.version_num} · ${_esc(v.label || 'snapshot')}</div>
            <div class="text-xs" style="color:var(--text-muted,#9ca3af);">${v.entity_count} nodes · ${v.rel_count} edges · ${_esc(v.created_at || '')}</div>
          </div>
          <button class="kg-uv-restore card-btn card-btn--info" data-vid="${_esc(v.version_id)}" data-vnum="${v.version_num}">Switch to</button>
        </div>`).join('');

    _show(_header(`Versions — “${kgName}”`, `${versions.length} saved version(s)`) +
        `<div class="max-h-80 overflow-y-auto">${rows}</div>
         <button class="kg-uv-close card-btn card-btn--secondary w-full mt-3">Close</button>`);

    _overlay.querySelectorAll('.kg-uv-restore').forEach(btn => {
        btn.addEventListener('click', async () => {
            const vnum = btn.dataset.vnum;
            btn.disabled = true; btn.textContent = 'Switching…';
            try {
                const r = await fetch(`/api/v1/knowledge-graph/versions/${encodeURIComponent(btn.dataset.vid)}/restore`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${TOKEN()}` },
                    body: JSON.stringify({ kg_id: kgId, profile_id: profileId }),
                });
                const out = await r.json();
                if (!r.ok || out.status !== 'success') throw new Error(out.message || `HTTP ${r.status}`);
                showAppBanner(`Switched “${kgName}” to v${vnum} (${out.entity_count} nodes).`, 'success');
                _hide();
                onDone?.();
            } catch (err) {
                showAppBanner(`Switch failed: ${err.message}`, 'error');
                btn.disabled = false; btn.textContent = 'Switch to';
            }
        });
    });
}
