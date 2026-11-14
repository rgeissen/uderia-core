/**
 * PROP-006 — the administrator's COMBINING RULES (Administration → Trust → Enforcement).
 *
 * A deployment rule names data-source ENDPOINTS ("never combine what comes from the HR warehouse
 * with what comes from the CRM system") and holds for every user who reaches those systems,
 * whatever they named them. An agent may add rules of its own; it can never loosen these.
 * Endpoints are derived by code from users' configurations (GET /v1/admin/source-endpoints) —
 * never typed, never a credential.
 *
 * Plain script (like adminManager.js, which mounts it and saves through the Trust panel's one
 * Save): `window.CombineRulesAdmin = {mount, rules, snapshot, mounted}`. State is the truth; the
 * endpoint list loads asynchronously and a rule naming an endpoint no user reaches today is kept
 * and says so. Theme variables only; monochrome icons.
 */
(function () {
    'use strict';
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const ICON_LOCK = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="1.5"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>';
    const ICON_X = '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
    const ICON_PLUS = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>';

    let S = null;

    function norm(rules) {
        return (Array.isArray(rules) ? rules : []).map(r => ({
            endpoints: [...new Set(((r && (r.endpoints || r.sources)) || []).map(String).filter(Boolean))].sort(),
            reason: String((r && r.reason) || ''),
        })).filter(r => r.endpoints.length >= 2);
    }

    /** How an endpoint reads to an administrator: the names users gave it and how many reach it. */
    function epInfo(ep) {
        const e = ((S && S.endpoints) || []).find(x => x.endpoint === ep);
        if (!e) return { label: ep, sub: S && S.loaded ? 'no user reaches this system today — the rule is kept' : '', absent: !!(S && S.loaded) };
        const names = e.names || [];
        const label = names.length ? names.slice(0, 2).join(', ') + (names.length > 2 ? ` +${names.length - 2}` : '') : ep;
        return { label, sub: `${e.endpoint} · ${e.users} user${e.users === 1 ? '' : 's'}`, absent: false, kind: e.kind };
    }

    function chip(ep) {
        const i = epInfo(ep);
        return `<span class="cra-chip${i.absent ? ' is-absent' : ''}" title="${esc(i.sub || ep)}"><span class="cra-chip-n">${esc(i.label)}</span>${i.sub ? `<span class="cra-chip-e">${esc(i.sub)}</span>` : ''}</span>`;
    }

    function rulesHtml() {
        if (!S.rules.length) return '<div class="cra-line">No rules yet — combining may join results from any two sources an agent reads.</div>';
        return S.rules.map((r, i) => `
          <div class="cra-rule" data-cra-rule="${i}">
            <span class="cra-lead">${ICON_LOCK} Never combine</span>
            ${r.endpoints.map(chip).join('<span class="cra-with">with</span>')}
            <button type="button" class="cra-act cra-icon" data-cra-act="remove" data-cra-index="${i}" aria-label="Remove this rule">${ICON_X}</button>
            ${r.reason ? `<div class="cra-reason">${esc(r.reason)}</div>` : ''}
          </div>`).join('');
    }

    function listHtml() {
        if (!S.loaded) return '<div class="cra-line">Loading the systems your users reach…</div>';
        if (S.loadError) return '<div class="cra-line">The systems could not be listed — existing rules are kept.</div>';
        const f = String(S.filter || '').toLowerCase();
        const list = (S.endpoints || []).filter(e => !f || `${e.endpoint} ${(e.names || []).join(' ')}`.toLowerCase().includes(f));
        if (!S.endpoints.length) return '<div class="cra-line">No user has an MCP server or database connection yet.</div>';
        if (!list.length) return '<div class="cra-line">No system matches.</div>';
        const sel = new Set(S.builder);
        return list.map(e => `<button type="button" class="cra-pick" data-cra-act="toggle" data-cra-ep="${esc(e.endpoint)}" aria-pressed="${sel.has(e.endpoint) ? 'true' : 'false'}">
            <span><span class="cra-chip-n">${esc((e.names || []).join(', ') || e.endpoint)}</span><br><span class="cra-chip-e">${esc(e.endpoint)}</span></span>
            <span class="cra-pill">${esc(e.kind === 'mcp' ? 'MCP server' : 'database')} · ${esc(e.users)} user${e.users === 1 ? '' : 's'}</span></button>`).join('');
    }

    function builderHtml() {
        if (!S.builderOpen) {
            return `<button type="button" class="cra-act" data-cra-act="open">${ICON_PLUS} Add a rule</button>`;
        }
        const n = S.builder.length;
        return `<div class="cra-builder" role="group" aria-label="New combining rule">
            <div class="cra-line">Pick two or more systems whose results must never meet in one answer.</div>
            <input type="search" class="cra-input" data-cra-act="filter" placeholder="Search systems or their names" aria-label="Search systems" value="${esc(S.filter || '')}">
            <div class="cra-list">${listHtml()}</div>
            <input type="text" class="cra-input" data-cra-act="reason" maxlength="200" placeholder="Why — shown to users when a combine is refused" value="${esc(S.reason || '')}">
            <div class="cra-row-acts">
              <button type="button" class="cra-act" data-cra-act="save" ${n >= 2 ? '' : 'disabled'}>Add rule${n ? ` (${n} selected)` : ''}</button>
              <button type="button" class="cra-act" data-cra-act="cancel">Cancel</button>
            </div></div>`;
    }

    function render() {
        if (!S) return;
        S.host.innerHTML = `
          <div class="cra">
            <div class="cra-h"><span class="cra-t">Combining rules</span>
              <span class="cra-sub">Hold for every user who reaches these systems, whatever they named them. Agents may add their own rules — they can never loosen these.</span></div>
            <div class="cra-rules">${rulesHtml()}</div>
            <div class="cra-foot">${builderHtml()}</div>
          </div>`;
    }

    function onClick(e) {
        const b = e.target && e.target.closest && e.target.closest('[data-cra-act]');
        if (!b || !S) return;
        const act = b.dataset.craAct;
        if (act === 'open') { S.builderOpen = true; S.builder = []; S.filter = ''; S.reason = ''; }
        else if (act === 'cancel') { S.builderOpen = false; }
        else if (act === 'toggle') { const ep = b.dataset.craEp; S.builder = S.builder.includes(ep) ? S.builder.filter(x => x !== ep) : [...S.builder, ep]; }
        else if (act === 'save') {
            const eps = [...new Set(S.builder)].sort();
            if (eps.length < 2) return;
            if (!S.rules.some(r => r.endpoints.join('|') === eps.join('|'))) S.rules.push({ endpoints: eps, reason: String(S.reason || '').trim().slice(0, 200) });
            S.builderOpen = false;
        }
        else if (act === 'remove') { S.rules.splice(parseInt(b.dataset.craIndex, 10), 1); }
        else return;
        render();
    }

    function onInput(e) {
        const t = e.target; if (!t || !S) return;
        const act = t.dataset && t.dataset.craAct;
        if (act === 'filter') { S.filter = t.value; const l = S.host.querySelector('.cra-list'); if (l) l.innerHTML = listHtml(); }
        else if (act === 'reason') { S.reason = t.value; }
    }

    function injectCss() {
        if (document.getElementById('cra-css')) return;
        const s = document.createElement('style');
        s.id = 'cra-css';
        s.textContent = `
.cra { margin-top:16px; padding-top:16px; border-top:1px solid var(--border-secondary); }
.cra-h { display:flex; flex-direction:column; gap:2px; margin-bottom:10px; }
.cra-t { color:var(--text-primary); font-weight:600; font-size:0.9rem; }
.cra-sub, .cra-line, .cra-reason { color:var(--text-muted); font-size:0.75rem; }
.cra-reason { width:100%; }
.cra-rule { display:flex; flex-wrap:wrap; align-items:center; gap:6px; padding:8px 10px; border:1px solid var(--border-secondary); border-radius:10px; color:var(--text-primary); font-size:0.82rem; background:var(--card-bg); }
.cra-rule + .cra-rule { margin-top:6px; }
.cra-lead { display:inline-flex; align-items:center; gap:5px; }
.cra-with { color:var(--text-muted); font-size:0.75rem; }
.cra-chip { display:inline-flex; flex-direction:column; padding:3px 9px; border-radius:8px; border:1px solid var(--border-secondary); }
.cra-chip.is-absent { border-style:dashed; }
.cra-chip-n { color:var(--text-primary); font-size:0.78rem; font-weight:600; }
.cra-chip-e { color:var(--text-muted); font-size:0.68rem; font-family:ui-monospace, SFMono-Regular, Menlo, monospace; word-break:break-all; }
.cra-act { font-size:0.75rem; color:var(--text-muted); background:transparent; border:1px solid var(--border-secondary); border-radius:6px; padding:3px 9px; cursor:pointer; display:inline-flex; align-items:center; gap:4px; }
.cra-act:hover, .cra-act:focus-visible { color:var(--text-primary); border-color:var(--teradata-orange); outline:none; }
.cra-act[disabled] { opacity:.5; cursor:not-allowed; }
.cra-icon { width:24px; height:24px; justify-content:center; padding:0; margin-left:auto; }
.cra-foot { margin-top:10px; }
.cra-builder { border:1px solid var(--border-secondary); border-radius:10px; padding:10px; background:var(--bg-primary); display:flex; flex-direction:column; gap:8px; }
.cra-input { width:100%; background:var(--card-bg); color:var(--text-primary); border:1px solid var(--border-secondary); border-radius:6px; padding:5px 8px; font-size:0.8rem; }
.cra-list { display:flex; flex-direction:column; gap:4px; max-height:260px; overflow:auto; }
.cra-pick { display:grid; grid-template-columns:1fr auto; gap:8px; align-items:center; text-align:left; background:transparent; border:1px solid var(--border-secondary); border-radius:8px; padding:6px 8px; cursor:pointer; color:var(--text-primary); }
.cra-pick[aria-pressed="true"] { border-color:var(--teradata-orange); }
.cra-pick:focus-visible { outline:none; border-color:var(--teradata-orange); }
.cra-pill { font-size:0.68rem; padding:1px 8px; border-radius:999px; border:1px solid var(--border-secondary); color:var(--text-muted); white-space:nowrap; }
.cra-row-acts { display:flex; gap:6px; }
`;
        document.head.appendChild(s);
    }

    /** Mount on a host with the stored rules; `fetchEndpoints` is injectable (the gate). */
    function mount(host, storedRules, fetchEndpoints) {
        if (!host) return null;
        injectCss();
        S = { host, rules: norm(storedRules), endpoints: [], loaded: false, loadError: false,
              builderOpen: false, builder: [], filter: '', reason: '' };
        if (host.dataset.craWired !== '1') {
            host.dataset.craWired = '1';
            host.addEventListener('click', onClick);
            host.addEventListener('input', onInput);
        }
        render();
        const f = fetchEndpoints || (async () => {
            const tok = localStorage.getItem('tda_auth_token');
            const r = await fetch('/api/v1/admin/source-endpoints', { headers: tok ? { 'Authorization': `Bearer ${tok}` } : {} });
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            return (await r.json()).endpoints || [];
        });
        const mine = S;
        Promise.resolve().then(f).then(list => {
            if (S !== mine) return;
            S.endpoints = Array.isArray(list) ? list : []; S.loaded = true; render();
        }).catch(() => { if (S === mine) { S.loaded = true; S.loadError = true; render(); } });
        return S;
    }

    window.CombineRulesAdmin = {
        mount,
        mounted: () => !!S,
        /** The rules to save — exactly the app-config shape. */
        rules: () => (S ? S.rules.map(r => ({ endpoints: [...r.endpoints], reason: r.reason })) : null),
        /** By-value snapshot for the Trust panel's unsaved-changes marker. */
        snapshot: () => JSON.stringify(S ? S.rules : null),
        _state: () => S,
    };
})();
