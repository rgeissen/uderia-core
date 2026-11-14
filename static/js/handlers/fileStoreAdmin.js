/**
 * fileStoreAdmin — Administration → Components → Files: Stores · Policy · Capacity (PROP-010 §4.5).
 *
 * Registers three tabs on the Files component's admin page through the one extension point
 * `window.ComponentAdminTabs` (platformConnectorAdminHandler.js), beside Overview and Users —
 * the component's global switch and per-user overrides stay where every component has them.
 *
 *   Stores    where files are stored: cards with health and usage, Add store (backend →
 *             connection → encryption & capacity → test → save; save only after a passing
 *             test), set default, edit, delete (refused while it holds files), move files with
 *             a dry-run count first
 *   Policy    who may keep, which stores each tier's agents may use, the download policy, the
 *             quota per user; encryption (locked on in hardened mode), retention, size, warning
 *   Capacity  what is provisioned, held and promised — overcommit said in words — per store,
 *             per tier, per-user overrides, the largest consumers
 *
 * Every write saves at once and says so (aria-live); destructive acts confirm through
 * window.showConfirmBanner. Theme variables only; monochrome SVG.
 */
(function () {
    'use strict';
    if (typeof window === 'undefined') return;

    const tok = () => localStorage.getItem('tda_auth_token');
    const hdrs = () => ({ 'Content-Type': 'application/json', ...(tok() ? { Authorization: `Bearer ${tok()}` } : {}) });
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const api = async (path, opts = {}) => {
        const r = await fetch(`/api/v1${path}`, { ...opts, headers: { ...hdrs(), ...(opts.headers || {}) } });
        let body = {};
        try { body = await r.json(); } catch (_) { body = {}; }
        return { ok: r.ok, status: r.status, body };
    };
    const banner = (m, t = 'success') => { if (typeof window.showAppBanner === 'function') window.showAppBanner(m, t); };
    const confirmIt = (m, fn) => { if (typeof window.showConfirmBanner === 'function') window.showConfirmBanner(m, fn); else fn(); };
    const GLYPH = {
        local: 'M4 6h16v5H4zM4 13h16v5H4zM8 8.5h.01M8 15.5h.01',
        s3: 'M7 18a4 4 0 0 1-.6-8A5.5 5.5 0 0 1 17 8.5a4 4 0 0 1 .5 9.5z',
        azure_blob: 'M7 18a4 4 0 0 1-.6-8A5.5 5.5 0 0 1 17 8.5a4 4 0 0 1 .5 9.5z',
        gcs: 'M7 18a4 4 0 0 1-.6-8A5.5 5.5 0 0 1 17 8.5a4 4 0 0 1 .5 9.5z',
        lock: 'M6 11h12v9H6zM8.5 11V8a3.5 3.5 0 0 1 7 0v3',
    };
    const svg = (d, n = 16) => `<svg width="${n}" height="${n}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${d}"/></svg>`;

    const CARD = 'background:var(--card-bg);border:1px solid var(--border-primary);border-radius:12px;padding:16px';
    const INPUT = 'height:32px;border:1px solid var(--border-primary);border-radius:8px;background:var(--input-bg,var(--bg-primary));color:var(--text-primary);padding:0 10px;font:inherit;font-size:13px';
    const MUTED = 'color:var(--text-muted)';
    const H = 'margin:0;font-size:15px;font-weight:600;color:var(--text-primary)';

    function bar(frac, warn) {
        const f = Math.max(0, Math.min(1, frac || 0));
        const col = f >= 1 ? 'var(--status-error,#ef4444)' : (warn ? 'var(--status-warning,#d97706)' : 'var(--teradata-orange)');
        return `<span style="display:block;flex:1;height:6px;border-radius:999px;background:var(--hover-bg-strong);overflow:hidden" role="img" aria-label="${Math.round(f * 100)}% used"><span style="display:block;height:100%;width:${(f * 100).toFixed(1)}%;background:${col}"></span></span>`;
    }

    function live(host, msg) {
        const el = host.querySelector('[data-live]');
        if (el) { el.textContent = msg; clearTimeout(el._t); el._t = setTimeout(() => { el.textContent = ''; }, 4000); }
    }

    // ── Stores ───────────────────────────────────────────────────────────────
    async function renderStores(host) {
        host.style.textAlign = 'left';   // the component page's loading placeholder centres its text
        host.innerHTML = `<p style="${MUTED};font-size:13px">Loading stores…</p>`;
        const [st, be, cap, pol] = await Promise.all([api('/file-stores/configurations'), api('/file-stores/backends'),
                                                      api('/file-stores/capacity'), api('/file-stores/settings')]);
        if (pol.ok) window.__filesPolicy = pol.body.settings;   // the Add-store form asks: is encryption required?
        if (!st.ok) { host.innerHTML = `<p style="${MUTED}">${esc(st.body.message || 'Stores could not be loaded.')}</p>`; return; }
        const stores = st.body.stores || [];
        const backends = (be.body.backends || []);
        const capById = new Map(((cap.body.capacity || {}).stores || []).map(s => [s.id, s]));
        host.innerHTML = `
          <div style="display:flex;flex-direction:column;gap:16px">
            <div style="display:flex;align-items:flex-end;gap:12px;flex-wrap:wrap">
              <div style="flex:1;min-width:260px"><h3 style="${H}">Where files are stored</h3>
                <p style="margin:4px 0 0;${MUTED};font-size:13px;line-height:1.5">Kept files and every chat attachment. Each agent writes to the store it is bound to (or the default); a file stays in the store it was written to.</p></div>
              <span data-live aria-live="polite" style="${MUTED};font-size:12px"></span>
              <button type="button" data-add class="card-btn card-btn--primary">Add store</button>
            </div>
            <div data-form></div>
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px">
              ${stores.map(s => storeCard(s, capById.get(s.id))).join('')}
            </div>
            ${moveSection(stores)}
          </div>`;
        host.querySelector('[data-add]').addEventListener('click', () => addForm(host, backends, null));
        host.querySelectorAll('[data-test]').forEach(b => b.addEventListener('click', async () => {
            b.disabled = true; b.textContent = 'Testing…';
            const r = await api(`/file-stores/configurations/${encodeURIComponent(b.dataset.test)}/test`, { method: 'POST' });
            live(host, r.body.success ? `Test passed — ${r.body.message}` : `Test failed — ${r.body.message || r.body.error || 'no answer'}`);
            renderStores(host);
        }));
        host.querySelectorAll('[data-default]').forEach(b => b.addEventListener('click', async () => {
            const r = await api(`/file-stores/configurations/${encodeURIComponent(b.dataset.default)}/default`, { method: 'POST' });
            if (r.ok) { banner('Default store changed. New files from agents without a store of their own go there.'); renderStores(host); }
            else banner(r.body.message || 'The default could not be changed.', 'error');
        }));
        host.querySelectorAll('[data-edit]').forEach(b => b.addEventListener('click', () => addForm(host, backends, stores.find(s => s.id === b.dataset.edit))));
        host.querySelectorAll('[data-del]').forEach(b => b.addEventListener('click', () => {
            const s = stores.find(x => x.id === b.dataset.del);
            confirmIt(`Delete the store ${s.name}? Agents bound to it will use the default store.`, async () => {
                const r = await api(`/file-stores/configurations/${encodeURIComponent(s.id)}`, { method: 'DELETE' });
                if (r.ok) { banner(`${s.name} deleted.`); renderStores(host); }
                else banner(r.body.message || 'The store could not be deleted.', 'warning');
            });
        }));
        wireMove(host);
    }

    function storeCard(s, cap) {
        const health = s.builtin ? { col: 'var(--status-success,#10b981)', text: 'Always available' }
            : s.last_test_ok === true ? { col: 'var(--status-success,#10b981)', text: `Healthy · tested ${when(s.last_test_at)}` }
                : s.last_test_ok === false ? { col: 'var(--status-error,#ef4444)', text: `Test failed · ${s.last_test_message || ''}` }
                    : { col: 'var(--status-warning,#d97706)', text: 'Not tested yet' };
        const prov = cap && cap.provisioned_bytes;
        const capLine = cap ? (prov
            ? `<div style="display:flex;align-items:center;gap:10px;font-size:12px">${bar(cap.fraction, cap.state === 'warn')}<span style="${MUTED};white-space:nowrap">${esc(cap.held_label)} of ${esc(cap.provisioned_label)}</span></div>`
            : `<div style="font-size:12px;${MUTED}">${esc(cap.held_label)} held · no capacity set${cap.disk_free_label ? ` · ${esc(cap.disk_free_label)} free on disk` : ''}</div>`) : '';
        return `<article style="${CARD};display:flex;flex-direction:column;gap:10px">
            <div style="display:flex;align-items:center;gap:10px">
              <span style="width:34px;height:34px;border-radius:8px;background:var(--bg-primary);border:1px solid var(--border-secondary);display:inline-flex;align-items:center;justify-content:center;color:var(--text-secondary)">${svg(GLYPH[s.backend_type] || GLYPH.local, 17)}</span>
              <span style="flex:1;min-width:0;display:flex;flex-direction:column"><strong style="font-weight:600;color:var(--text-primary)">${esc(s.name)}</strong>
                <span style="font-size:12px;${MUTED}">${esc(s.backend_label)}${s.location ? ` · ${esc(s.location)}` : ''}</span></span>
              ${s.is_default ? '<span style="font-size:11px;padding:1px 8px;border-radius:999px;border:1px solid var(--teradata-orange);color:var(--text-primary)">Default</span>' : ''}
            </div>
            <div style="display:flex;gap:6px;flex-wrap:wrap;font-size:11px">
              <span style="display:inline-flex;align-items:center;gap:5px;padding:2px 8px;border:1px solid var(--border-primary);border-radius:999px;color:var(--text-secondary)">${svg(GLYPH.lock, 11)}${s.encrypt ? 'Encrypted' : 'Not encrypted'}</span>
              <span style="padding:2px 8px;border:1px solid var(--border-primary);border-radius:999px;color:var(--text-secondary)">${s.agents} agent${s.agents === 1 ? '' : 's'} bound</span>
            </div>
            <div style="display:flex;align-items:baseline;gap:6px"><span style="font-size:20px;font-weight:600;font-variant-numeric:tabular-nums;color:var(--text-primary)">${s.files}</span><span style="${MUTED};font-size:13px">files · ${esc(s.bytes_label)}</span></div>
            ${capLine}
            <div style="display:flex;align-items:center;gap:8px;font-size:12px">
              <span style="width:8px;height:8px;border-radius:999px;background:${health.col}" aria-hidden="true"></span>
              <span style="flex:1;min-width:0;color:var(--text-secondary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap" class="u-tip" data-tip="${esc(health.text)}">${esc(health.text)}</span>
            </div>
            <div style="display:flex;gap:6px;flex-wrap:wrap">
              ${s.builtin ? '' : `<button type="button" data-test="${esc(s.id)}" class="card-btn card-btn--neutral card-btn--sm">Test</button>`}
              ${s.is_default ? '' : `<button type="button" data-default="${esc(s.id)}" class="card-btn card-btn--neutral card-btn--sm">Make default</button>`}
              ${s.builtin ? '' : `<button type="button" data-edit="${esc(s.id)}" class="card-btn card-btn--neutral card-btn--sm">Edit</button>
                 <button type="button" data-del="${esc(s.id)}" class="card-btn card-btn--danger card-btn--sm">Delete</button>`}
            </div></article>`;
    }

    function when(iso) {
        if (!iso) return '';
        const t = new Date(iso.endsWith('Z') ? iso : iso + 'Z').getTime();
        const d = (Date.now() - t) / 1000;
        if (d < 120) return 'just now';
        if (d < 3600) return `${Math.floor(d / 60)} min ago`;
        if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
        return new Date(t).toLocaleDateString();
    }

    function addForm(host, backends, existing) {
        const box = host.querySelector('[data-form]');
        const st = { step: existing ? 2 : 1, backend: existing ? existing.backend_type : 's3', tested: false, msg: '',
                     name: existing ? existing.name : '', cfg: Object.assign({}, existing ? existing.config : {}),
                     encrypt: existing ? existing.encrypt_setting !== false : true, capacity: existing && existing.capacity_mb ? existing.capacity_mb : '' };
        const steps = ['Backend', 'Connection', 'Encryption and capacity', 'Test'];
        const draw = () => {
            // the connection fields are the backend's own, from the ONE catalog (filestore/catalog.py)
            const spec = backends.find(b => b.id === st.backend) || { fields: [] };
            const field = (f) => f.multiline
                ? `<label style="display:flex;flex-direction:column;gap:4px;font-size:12px;${MUTED};grid-column:1/-1">${esc(f.label)}
                    <textarea data-k="${esc(f.key)}" rows="4" placeholder="${esc(f.placeholder || '')}" style="${INPUT};height:auto;padding:8px 10px;font-family:monospace;font-size:12px" autocomplete="off" spellcheck="false">${esc(st.cfg[f.key] || '')}</textarea></label>`
                : `<label style="display:flex;flex-direction:column;gap:4px;font-size:12px;${MUTED}">${esc(f.label)}
                    <input data-k="${esc(f.key)}" type="${f.secret ? 'password' : 'text'}" value="${esc(st.cfg[f.key] || '')}" placeholder="${esc(f.placeholder || '')}" style="${INPUT}" autocomplete="off"></label>`;
            const conn = (spec.fields || []).map(field).join('');
            box.innerHTML = `<section style="${CARD};border-color:var(--teradata-orange);display:flex;flex-direction:column;gap:14px" aria-label="${existing ? 'Edit store' : 'Add a store'}">
              <div style="display:flex;align-items:center;gap:10px"><h3 style="${H};flex:1">${existing ? `Edit ${esc(existing.name)}` : 'Add a store'}</h3>
                <button type="button" data-close aria-label="Close" class="card-btn card-btn--neutral card-btn--sm">Cancel</button></div>
              <ol style="list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px">${steps.map((l, i) => {
                    const n = i + 1, on = n === st.step, done = n < st.step;
                    return `<li style="padding:8px 10px;border-radius:8px;font-size:12px;border:1px solid ${on ? 'var(--teradata-orange)' : 'var(--border-secondary)'};color:${on || done ? 'var(--text-primary)' : 'var(--text-muted)'}"><strong>${n}</strong> ${l}</li>`;
                }).join('')}</ol>
              ${st.step === 1 ? `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px">${backends.map(b => `
                  <button type="button" data-b="${esc(b.id)}" ${b.available ? '' : 'disabled'} aria-pressed="${st.backend === b.id}" style="display:flex;flex-direction:column;gap:4px;text-align:left;padding:12px;border-radius:10px;border:1px solid ${st.backend === b.id ? 'var(--teradata-orange)' : 'var(--border-primary)'};background:transparent;color:var(--text-primary);cursor:${b.available ? 'pointer' : 'not-allowed'};opacity:${b.available ? 1 : 0.55}">
                    <strong>${esc(b.label)}</strong><span style="font-size:12px;${MUTED}">${esc(b.note || b.hint)}</span></button>`).join('')}</div>` : ''}
              ${st.step === 2 ? `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px">
                  <label style="display:flex;flex-direction:column;gap:4px;font-size:12px;${MUTED}">Name<input data-name value="${esc(st.name)}" placeholder="EU archive" style="${INPUT}"></label>${conn}</div>` : ''}
              ${st.step === 3 ? `<div style="display:flex;flex-direction:column;gap:12px">
                  <label style="display:flex;align-items:flex-start;gap:10px;padding:12px;border-radius:8px;background:var(--bg-primary);border:1px solid var(--border-secondary)">
                    <input type="checkbox" data-encrypt ${st.encrypt ? 'checked' : ''} ${window.__filesPolicy && window.__filesPolicy.require_encryption ? 'disabled' : ''} style="margin-top:3px">
                    <span style="display:flex;flex-direction:column;gap:4px"><strong style="color:var(--text-primary)">Encrypt every file</strong>
                      <span style="font-size:12px;${MUTED};line-height:1.5">${window.__filesPolicy && window.__filesPolicy.require_encryption
                        ? `Required on this deployment${window.__filesPolicy.require_encryption_locked ? ' (hardened mode)' : ' — change it in the Policy tab'}.` : 'Recommended.'} Each file gets its own key; the store sees only ciphertext and opaque names. The provider's own encryption can be on as well.</span></span></label>
                  <label style="display:flex;flex-direction:column;gap:4px;font-size:12px;${MUTED};max-width:320px">Capacity for Files in this store (MB, empty = no limit)
                    <input data-capacity type="number" min="1" value="${esc(st.capacity)}" placeholder="e.g. 512000 for 500 GB" style="${INPUT}"></label>
                  <span style="font-size:12px;${MUTED}">Writes are refused once the store holds this much; admins see a warning before.</span></div>` : ''}
              ${st.step === 4 ? `<p style="margin:0;padding:10px 12px;border-radius:8px;background:${st.tested ? 'rgba(16,185,129,0.10)' : 'var(--hover-bg)'};font-size:13px;color:var(--text-primary);line-height:1.5" aria-live="polite">${esc(st.msg || 'Save is offered after a passing test. The test writes, reads back and deletes one small object.')}</p>` : ''}
              <div style="display:flex;gap:8px;justify-content:flex-end">
                ${st.step > 1 && !(existing && st.step === 2) ? '<button type="button" data-back class="card-btn card-btn--neutral">Back</button>' : ''}
                ${st.step < 4 ? `<button type="button" data-next class="card-btn card-btn--primary">Continue</button>` : ''}
                ${st.step === 4 && !st.tested ? `<button type="button" data-run class="card-btn card-btn--primary">Test connection</button>` : ''}
                ${st.step === 4 && st.tested ? `<button type="button" data-save class="card-btn card-btn--primary">${existing ? 'Save changes' : 'Save store'}</button>` : ''}
              </div></section>`;
            box.querySelector('[data-close]').addEventListener('click', () => { box.innerHTML = ''; });
            box.querySelectorAll('[data-b]').forEach(b => b.addEventListener('click', () => { st.backend = b.dataset.b; draw(); }));
            const collect = () => {
                box.querySelectorAll('[data-k]').forEach(i => { st.cfg[i.dataset.k] = i.value.trim(); });
                const n = box.querySelector('[data-name]'); if (n) st.name = n.value.trim();
                const e = box.querySelector('[data-encrypt]'); if (e) st.encrypt = e.checked;
                const c = box.querySelector('[data-capacity]'); if (c) st.capacity = c.value.trim();
            };
            box.querySelector('[data-back]')?.addEventListener('click', () => { collect(); st.step -= 1; st.tested = false; draw(); });
            box.querySelector('[data-next]')?.addEventListener('click', () => {
                collect();
                if (st.step === 2 && !st.name) { live(host, 'A store needs a name.'); return; }
                if (st.step === 2) {
                    const spec = backends.find(b => b.id === st.backend) || { fields: [] };
                    const missing = (spec.fields || []).find(f => f.required && !st.cfg[f.key]);
                    if (missing) { live(host, `${spec.label} needs ${missing.label.split(' (')[0].toLowerCase()}.`); return; }
                    const alt = spec.one_of || [];
                    if (alt.length && !alt.some(k => st.cfg[k])) {
                        live(host, `${spec.label} needs ${alt.map(k => (spec.fields.find(f => f.key === k) || { label: k }).label.split(' (')[0].toLowerCase()).join(' or ')}.`); return;
                    }
                }
                st.step += 1; draw();
            });
            box.querySelector('[data-run]')?.addEventListener('click', async () => {
                const id = existing ? existing.id : '_draft';
                const body = { backend_type: st.backend, config: st.cfg, existing_id: existing ? existing.id : null };
                // an edited store tests the settings in the form when they changed, else the saved ones
                const r = await api(`/file-stores/configurations/${existing && !formChanged() ? encodeURIComponent(id) : '_draft'}/test`, { method: 'POST', body: JSON.stringify(body) });
                st.tested = !!r.body.success;
                st.msg = r.body.success ? `Test passed: ${r.body.message}` : `Test failed: ${r.body.message || 'no answer'} — fix the connection and test again.`;
                draw();
            });
            const formChanged = () => existing && JSON.stringify(existing.config) !== JSON.stringify(st.cfg);
            box.querySelector('[data-save]')?.addEventListener('click', async () => {
                const body = { name: st.name, backend_type: st.backend, config: st.cfg, encrypt: st.encrypt, capacity_mb: st.capacity || '' };
                const r = existing
                    ? await api(`/file-stores/configurations/${encodeURIComponent(existing.id)}`, { method: 'PUT', body: JSON.stringify(body) })
                    : await api('/file-stores/configurations', { method: 'POST', body: JSON.stringify(body) });
                if (!r.ok) { st.msg = r.body.message || 'The store could not be saved.'; st.tested = false; draw(); return; }
                banner(existing ? `${st.name} saved.` : `${st.name} added. Bind agents to it in the agent editor, or make it the default.`);
                box.innerHTML = '';
                renderStores(host);
            });
            if (window.enhanceSelects) window.enhanceSelects(box);
        };
        draw();
        box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    function moveSection(stores) {
        if (stores.length < 2) return '';
        const opts = (sel) => stores.map(s => `<option value="${esc(s.id)}" ${s.id === sel ? 'selected' : ''}>${esc(s.name)}</option>`).join('');
        return `<section style="${CARD};display:flex;flex-direction:column;gap:12px" aria-label="Move files">
            <h3 style="${H}">Move files between stores</h3>
            <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:13px;color:var(--text-secondary)">
              From <select data-from style="${INPUT}">${opts(stores[0].id)}</select> to <select data-to style="${INPUT}">${opts(stores[1].id)}</select>
              <button type="button" data-plan class="card-btn card-btn--neutral card-btn--sm">Count what would move</button></div>
            <div data-plan-out style="font-size:13px;color:var(--text-secondary)"></div></section>`;
    }

    function wireMove(host) {
        const planBtn = host.querySelector('[data-plan]');
        if (!planBtn) return;
        const out = host.querySelector('[data-plan-out]');
        planBtn.addEventListener('click', async () => {
            const from = host.querySelector('[data-from]').value, to = host.querySelector('[data-to]').value;
            if (from === to) { out.textContent = 'Choose two different stores.'; return; }
            const r = await api('/file-stores/migrate', { method: 'POST', body: JSON.stringify({ from, to, dry_run: true }) });
            if (!r.ok) { out.textContent = r.body.message || 'Could not count.'; return; }
            const p = r.body.plan;
            const name = (id) => host.querySelector(`[data-from] option[value="${CSS.escape(id)}"]`)?.textContent || id;
            if (!p.files) { out.textContent = `${name(from)} holds no files.`; return; }
            out.innerHTML = `${p.files} file${p.files === 1 ? '' : 's'}, ${esc(p.bytes_label)}, would move from <strong>${esc(name(from))}</strong> to <strong>${esc(name(to))}</strong>. Nothing moves until you run it; the move is audited.
              <button type="button" data-run-move class="card-btn card-btn--primary" style="margin-left:10px">Move ${p.files} file${p.files === 1 ? '' : 's'}</button>`;
            out.querySelector('[data-run-move]').addEventListener('click', () => confirmIt(`Move ${p.files} files now? Each is read back before the original is removed.`, async () => {
                const m = await api('/file-stores/migrate', { method: 'POST', body: JSON.stringify({ from, to, dry_run: false }) });
                if (m.ok) { banner(`Moved ${m.body.moved} file${m.body.moved === 1 ? '' : 's'}${m.body.failed ? `, ${m.body.failed} could not be moved` : ''}.`, m.body.failed ? 'warning' : 'success'); renderStores(host); }
                else banner(m.body.message || 'The move did not run.', 'error');
            }));
        });
        if (window.enhanceSelects) window.enhanceSelects(host);
    }

    // ── Policy ───────────────────────────────────────────────────────────────
    const POLICIES = [['allowed', 'Allowed'], ['allowed_except_data_derived', 'Except files from a data source'], ['disabled', 'Off']];

    async function renderPolicy(host) {
        host.style.textAlign = 'left';   // the component page's loading placeholder centres its text
        host.innerHTML = `<p style="${MUTED};font-size:13px">Loading policy…</p>`;
        const [pr, st] = await Promise.all([api('/file-stores/settings'), api('/file-stores/configurations')]);
        if (!pr.ok) { host.innerHTML = `<p style="${MUTED}">${esc(pr.body.message || 'The policy could not be loaded.')}</p>`; return; }
        const p = pr.body.settings;
        window.__filesPolicy = p;
        const stores = st.body.stores || [];
        const tierRow = (t) => {
            const c = p.tiers[t];
            const name = t[0].toUpperCase() + t.slice(1);
            if (c.bypass) {
                return `<tr><td style="padding:12px 8px;font-weight:500;color:var(--text-primary)">${name}</td>
                  <td colspan="3" style="padding:12px 8px;${MUTED};font-size:12px">Admins may always keep and download, and use every store.</td>
                  <td style="padding:8px"><input data-quota="${t}" type="number" min="0" value="${c.quota_mb}" style="${INPUT};width:110px" aria-label="Quota per admin in MB"> <span style="${MUTED};font-size:12px">MB</span></td></tr>`;
            }
            return `<tr><td style="padding:12px 8px;font-weight:500;color:var(--text-primary)">${name}</td>
              <td style="padding:8px"><label class="ind-toggle ind-toggle--primary"><input type="checkbox" data-enabled="${t}" ${c.files_enabled ? 'checked' : ''} aria-label="${name}s may keep files"><span class="ind-track"></span></label></td>
              <td style="padding:8px"><div style="display:flex;gap:6px;flex-wrap:wrap">${stores.map(s => `<label style="display:inline-flex;align-items:center;gap:5px;font-size:12px;color:var(--text-secondary)">
                    <input type="checkbox" data-store="${t}" value="${esc(s.id)}" ${(c.allowed_stores || []).includes(s.id) ? 'checked' : ''}>${esc(s.name)}</label>`).join('')}</div></td>
              <td style="padding:8px"><div role="group" aria-label="Downloads for ${name}s" style="display:flex;gap:6px;flex-wrap:wrap">${POLICIES.map(([k, l]) =>
                    `<button type="button" data-pol="${t}" data-v="${k}" aria-pressed="${c.download_policy === k}" class="ind-tab ind-tab--pill${c.download_policy === k ? ' active' : ''}" style="padding:.3rem .65rem;font-size:.75rem">${l}</button>`).join('')}</div></td>
              <td style="padding:8px"><input data-quota="${t}" type="number" min="0" value="${c.quota_mb}" style="${INPUT};width:110px" aria-label="Quota per ${t} in MB"> <span style="${MUTED};font-size:12px">MB</span></td></tr>`;
        };
        host.innerHTML = `<div style="display:flex;flex-direction:column;gap:16px">
            <section style="${CARD};display:flex;flex-direction:column;gap:12px">
              <div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap"><h3 style="${H};flex:1">Who may keep and download files</h3><span data-live aria-live="polite" style="${MUTED};font-size:12px"></span></div>
              <div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:13px;min-width:760px">
                <thead><tr style="text-align:left;${MUTED};font-size:12px">
                  <th style="padding:6px 8px;font-weight:500">Tier</th><th style="padding:6px 8px;font-weight:500">Keep files</th>
                  <th style="padding:6px 8px;font-weight:500">Stores its agents may use</th><th style="padding:6px 8px;font-weight:500">Downloads</th>
                  <th style="padding:6px 8px;font-weight:500">Space per user (0 = no limit)</th></tr></thead>
                <tbody>${['user', 'developer', 'admin'].map(tierRow).join('')}</tbody></table></div>
              <p style="margin:0;font-size:12px;${MUTED};line-height:1.5">Attachments always work, so chatting is never blocked by Files: an attachment is stored, encrypted and deleted with its conversation whatever these switches say. "Except files from a data source" refuses downloads of files kept from answers that read a data source; people can still use them in a conversation. Every download is recorded in the audit log.</p>
            </section>
            <section style="${CARD};display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px">
              <label style="display:flex;align-items:flex-start;gap:10px">
                <label class="ind-toggle ind-toggle--primary"><input type="checkbox" data-g="require_encryption" ${p.require_encryption ? 'checked' : ''} ${p.require_encryption_locked ? 'disabled' : ''} aria-label="Require encryption"><span class="ind-track"></span></label>
                <span style="display:flex;flex-direction:column;gap:3px"><strong style="color:var(--text-primary);font-size:13px">Require encryption</strong>
                  <span style="font-size:12px;${MUTED}">${p.require_encryption_locked ? 'Locked on: hardened mode.' : 'Every store encrypts, whatever its own setting.'}</span></span></label>
              ${num('retention_days', 'Recently deleted keeps files', 'days (0 = delete at once)', p.retention_days)}
              ${num('max_file_mb', 'Largest file', 'MB', p.max_file_mb)}
              ${num('capacity_warn_percent', 'Warn when a store or quota reaches', '%', p.capacity_warn_percent)}
              ${num('platform_local_capacity_mb', 'Capacity of Platform local', 'MB (0 = the disk)', p.platform_local_capacity_mb)}
            </section></div>`;
        const save = async (patch, what) => {
            const r = await api('/file-stores/settings', { method: 'PUT', body: JSON.stringify(patch) });
            if (r.ok) { window.__filesPolicy = r.body.settings; live(host, `Saved — ${what}`); }
            else { live(host, r.body.message || 'Not saved'); renderPolicy(host); }
        };
        host.querySelectorAll('[data-enabled]').forEach(i => i.addEventListener('change', () => save({ tiers: { [i.dataset.enabled]: { files_enabled: i.checked } } }, `${i.dataset.enabled}s ${i.checked ? 'may' : 'may not'} keep files`)));
        host.querySelectorAll('[data-pol]').forEach(b => b.addEventListener('click', () => {
            host.querySelectorAll(`[data-pol="${b.dataset.pol}"]`).forEach(x => { const on = x === b; x.setAttribute('aria-pressed', on); x.classList.toggle('active', on); });
            save({ tiers: { [b.dataset.pol]: { download_policy: b.dataset.v } } }, `${b.dataset.pol} downloads: ${b.textContent.toLowerCase()}`);
        }));
        host.querySelectorAll('[data-store]').forEach(i => i.addEventListener('change', () => {
            const t = i.dataset.store;
            const ids = [...host.querySelectorAll(`[data-store="${t}"]:checked`)].map(x => x.value);
            save({ tiers: { [t]: { allowed_stores: ids } } }, `stores for ${t}s`);
        }));
        host.querySelectorAll('[data-quota]').forEach(i => i.addEventListener('change', () => save({ tiers: { [i.dataset.quota]: { quota_mb: i.value || 0 } } }, `space per ${i.dataset.quota}`)));
        host.querySelectorAll('[data-g]').forEach(i => i.addEventListener('change', () => {
            const v = i.type === 'checkbox' ? i.checked : i.value;
            save({ [i.dataset.g]: v }, i.closest('label')?.querySelector('strong')?.textContent || i.dataset.g);
        }));
    }

    function num(key, label, unit, value) {
        return `<label style="display:flex;flex-direction:column;gap:6px"><strong style="color:var(--text-primary);font-size:13px">${label}</strong>
          <span style="display:flex;align-items:center;gap:8px"><input data-g="${key}" type="number" min="0" value="${esc(value)}" style="${INPUT};width:110px" aria-label="${esc(label)}"><span style="font-size:12px;${MUTED}">${unit}</span></span></label>`;
    }

    // ── Keys — who wraps each file's data key ────────────────────────────────
    // The platform key, or an external key manager (filestore/kms.py). New files take the
    // active choice; files kept before keep the key that wrapped them until re-wrapped.
    async function renderKeys(host) {
        host.style.textAlign = 'left';   // the component page's loading placeholder centres its text
        host.innerHTML = `<p style="${MUTED};font-size:13px">Loading key encryption…</p>`;
        const r = await api('/file-stores/keys');
        if (!r.ok) { host.innerHTML = `<p style="${MUTED}">${esc(r.body.message || 'Key encryption could not be loaded.')}</p>`; return; }
        const v = r.body;
        const byId = Object.fromEntries((v.providers || []).map(p => [p.id, p]));
        const st = { sel: v.active, cfg: Object.assign({}, (byId[v.active] || {}).config || {}), tested: false, msg: '' };
        const active = byId[v.active] || { label: v.active };
        const activeKey = v.active_key;   // which key the active provider uses — the server's rule (kms.PROVIDERS key_field)
        const usage = v.usage || [];
        const total = usage.reduce((n, u) => n + u.files, 0);
        const stale = usage.filter(u => !(u.provider === v.active && (u.key || null) === (activeKey || null))).reduce((n, u) => n + u.files, 0);
        const draw = () => {
            const p = byId[st.sel] || { fields: [] };
            const field = (f) => f.multiline
                ? `<label style="display:flex;flex-direction:column;gap:4px;font-size:12px;${MUTED};grid-column:1/-1">${esc(f.label)}
                    <textarea data-k="${esc(f.key)}" rows="4" placeholder="${esc(f.placeholder || '')}" style="${INPUT};height:auto;padding:8px 10px;font-family:monospace;font-size:12px" autocomplete="off" spellcheck="false">${esc(st.cfg[f.key] || '')}</textarea></label>`
                : `<label style="display:flex;flex-direction:column;gap:4px;font-size:12px;${MUTED}">${esc(f.label)}
                    <input data-k="${esc(f.key)}" type="${f.secret ? 'password' : 'text'}" value="${esc(st.cfg[f.key] || '')}" placeholder="${esc(f.placeholder || '')}" style="${INPUT}" autocomplete="off"></label>`;
            host.innerHTML = `<div style="display:flex;flex-direction:column;gap:16px">
              <section style="${CARD};display:flex;flex-direction:column;gap:10px">
                <div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap"><h3 style="${H};flex:1">Who holds the key to every file</h3><span data-live aria-live="polite" style="${MUTED};font-size:12px"></span></div>
                <p style="margin:0;font-size:13px;color:var(--text-primary);line-height:1.6;display:flex;align-items:center;gap:6px;flex-wrap:wrap">${svg(GLYPH.lock, 13)}<span>New files are wrapped by <strong>${esc(active.label)}</strong>${activeKey ? ` · <code style="font-size:12px">${esc(activeKey)}</code>` : ''}.</span></p>
                <p style="margin:0;font-size:12px;${MUTED};line-height:1.5">Every file is encrypted with its own data key; this decides what wraps that key. With an external key manager, reading a file needs the key manager to agree, and revoking the key there makes the files it wrapped unreadable here. Content fingerprints and sealed records (attachment lists, store settings, these settings) stay under the platform key.</p>
              </section>
              <section style="${CARD};display:flex;flex-direction:column;gap:12px">
                <h3 style="${H}">Wrap new files' keys with</h3>
                <div role="group" aria-label="Key encryption" style="display:flex;gap:6px;flex-wrap:wrap">${(v.providers || []).map(x =>
                    `<button type="button" data-p="${esc(x.id)}" aria-pressed="${st.sel === x.id}" ${x.available ? '' : 'disabled'} class="ind-tab ind-tab--pill${st.sel === x.id ? ' active' : ''}" style="padding:.3rem .7rem;font-size:.78rem" data-tip="${esc(x.note || x.hint)}">${esc(x.label)}</button>`).join('')}</div>
                <p style="margin:0;font-size:12px;${MUTED}">${esc(p.note || p.hint || '')}</p>
                ${(p.fields || []).length ? `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px">${p.fields.map(field).join('')}</div>` : ''}
                ${st.msg ? `<p style="margin:0;padding:10px 12px;border-radius:8px;background:${st.tested ? 'rgba(16,185,129,0.10)' : 'var(--hover-bg)'};font-size:13px;color:var(--text-primary)" aria-live="polite">${esc(st.msg)}</p>` : ''}
                <div style="display:flex;gap:8px;justify-content:flex-end">
                  <button type="button" data-test class="card-btn card-btn--neutral">Test</button>
                  <button type="button" data-use class="card-btn card-btn--primary" ${st.tested ? '' : 'disabled'} data-tip="${st.tested ? '' : 'A passing test comes first'}">Use for new files</button>
                </div>
              </section>
              <section style="${CARD};display:flex;flex-direction:column;gap:10px">
                <h3 style="${H}">Keys in use</h3>
                ${usage.length ? `<table style="width:100%;border-collapse:collapse;font-size:13px"><thead><tr style="text-align:left;${MUTED};font-size:12px">
                    <th style="padding:6px 8px;font-weight:500">Wrapped by</th><th style="padding:6px 8px;font-weight:500">Key</th><th style="padding:6px 8px;font-weight:500;text-align:right">Files</th></tr></thead>
                  <tbody>${usage.map(u => `<tr><td style="padding:6px 8px;color:var(--text-primary)">${esc(u.label)}</td><td style="padding:6px 8px;${MUTED};font-family:monospace;font-size:12px;overflow-wrap:anywhere">${esc(u.key || '—')}</td><td style="padding:6px 8px;text-align:right;color:var(--text-primary)">${u.files.toLocaleString()}</td></tr>`).join('')}</tbody></table>`
                  : `<p style="margin:0;font-size:13px;${MUTED}">No encrypted files yet.</p>`}
                ${stale ? `<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap"><span style="flex:1;font-size:12px;${MUTED}">${stale.toLocaleString()} of ${total.toLocaleString()} file${total === 1 ? '' : 's'} are wrapped by another key than the one new files use. Re-wrapping changes only the wrapped keys — no file is rewritten.</span>
                    <button type="button" data-rewrap class="card-btn card-btn--neutral">Re-wrap ${stale.toLocaleString()} under ${esc(active.label)}</button></div>` : ''}
              </section></div>`;
            const collect = () => host.querySelectorAll('[data-k]').forEach(i => { st.cfg[i.dataset.k] = i.value.trim(); });
            host.querySelectorAll('[data-k]').forEach(i => i.addEventListener('input', () => { if (st.tested) { st.tested = false; st.msg = 'Changed — test again.'; collect(); draw(); } }));
            host.querySelectorAll('[data-p]').forEach(b => b.addEventListener('click', () => {
                st.sel = b.dataset.p; st.cfg = Object.assign({}, (byId[st.sel] || {}).config || {}); st.tested = false; st.msg = ''; draw();
            }));
            host.querySelector('[data-test]').addEventListener('click', async (e) => {
                collect(); e.currentTarget.disabled = true; e.currentTarget.textContent = 'Testing…';
                const t = await api('/file-stores/keys/test', { method: 'POST', body: JSON.stringify({ provider: st.sel, config: st.cfg }) });
                st.tested = !!(t.body && t.body.success);
                st.msg = st.tested ? `Test passed: ${t.body.message}` : `Test failed: ${(t.body && t.body.message) || 'no answer'}`;
                draw();
            });
            host.querySelector('[data-use]').addEventListener('click', () => {
                collect();
                const label = (byId[st.sel] || {}).label || st.sel;
                confirmIt(`New files' keys will be wrapped by ${label}. Files kept before keep their key until you re-wrap them. Continue?`, async () => {
                    const s = await api('/file-stores/keys', { method: 'PUT', body: JSON.stringify({ provider: st.sel, config: st.cfg }) });
                    if (!s.ok) { st.tested = false; st.msg = s.body.message || 'Not saved'; draw(); return; }
                    banner(`New files' keys are wrapped by ${label}`);
                    renderKeys(host);
                });
            });
            host.querySelector('[data-rewrap]')?.addEventListener('click', async () => {
                const plan = await api('/file-stores/keys/rewrap', { method: 'POST', body: JSON.stringify({ dry_run: true }) });
                const n = (plan.body && plan.body.files) || 0;
                confirmIt(`Re-wrap ${n.toLocaleString()} file key${n === 1 ? '' : 's'} under ${active.label}? Each key is unwrapped by the key that holds it and wrapped by the new one.`, async () => {
                    const d = await api('/file-stores/keys/rewrap', { method: 'POST', body: JSON.stringify({ dry_run: false }) });
                    const failed = (d.body.failed || []).length;
                    banner(`Re-wrapped ${d.body.rewrapped || 0} file key(s)${failed ? ` — ${failed} could not be unwrapped` : ''}`, failed ? 'warning' : 'success');
                    renderKeys(host);
                });
            });
        };
        draw();
    }

    // ── Capacity ─────────────────────────────────────────────────────────────
    async function renderCapacity(host) {
        host.style.textAlign = 'left';   // the component page's loading placeholder centres its text
        host.innerHTML = `<p style="${MUTED};font-size:13px">Measuring…</p>`;
        const [cr, ur] = await Promise.all([api('/file-stores/capacity'), api('/admin/users?status=active&limit=500')]);
        if (!cr.ok) { host.innerHTML = `<p style="${MUTED}">${esc(cr.body.message || 'Capacity could not be read.')}</p>`; return; }
        const c = cr.body.capacity;
        const users = (ur.body.users || []).filter(u => u.is_active !== false);
        const over = c.overcommit;
        const headline = c.provisioned_bytes
            ? (over != null && over > 1
                ? `Consumers are promised ${esc(c.promised_label)} — ${Math.round(over * 100)}% of the ${esc(c.provisioned_label)} provisioned. That is fine while they use less (${esc(c.used_label)} today); raise capacity or lower quotas before use catches up.`
                : `${esc(c.used_label)} used of ${esc(c.provisioned_label)} provisioned; quotas promise ${esc(c.promised_label)}${c.unlimited_users ? `, plus ${c.unlimited_users} user${c.unlimited_users === 1 ? '' : 's'} without a limit` : ''}.`)
            : `${esc(c.used_label)} used. No store has a capacity set${c.has_unprovisioned_store ? ' — set one per store in Stores to see what is promised against what exists' : ''}; quotas promise ${esc(c.promised_label)}.`;
        host.innerHTML = `<div style="display:flex;flex-direction:column;gap:16px">
          <section style="${CARD};display:flex;flex-direction:column;gap:6px">
            <span style="font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;${MUTED}">Space</span>
            <p style="margin:0;font-size:15px;line-height:1.5;color:var(--text-primary)">${headline}</p>
            <span data-live aria-live="polite" style="${MUTED};font-size:12px"></span></section>
          <section style="${CARD};display:flex;flex-direction:column;gap:12px"><h3 style="${H}">Per store</h3>
            ${c.stores.map(s => `<div style="display:grid;grid-template-columns:minmax(140px,200px) minmax(0,1fr) minmax(140px,auto);gap:12px;align-items:center;font-size:13px">
                <span style="color:var(--text-primary);font-weight:500">${esc(s.name)}</span>
                ${s.provisioned_bytes ? bar(s.fraction, s.state === 'warn') : `<span style="${MUTED};font-size:12px">No capacity set${s.disk_free_label ? ` · ${esc(s.disk_free_label)} free of ${esc(s.disk_total_label)} on disk` : ' · object store, no disk limit'}</span>`}
                <span style="${MUTED};font-size:12px;text-align:right">${esc(s.held_label)} in ${s.files} file${s.files === 1 ? '' : 's'}${s.provisioned_bytes ? ` of ${esc(s.provisioned_label)}` : ''}${s.over_disk ? ' · more than the disk has free' : ''}</span></div>`).join('')}
          </section>
          <section style="${CARD};display:flex;flex-direction:column;gap:10px"><h3 style="${H}">Promised to consumers</h3>
            <table style="width:100%;border-collapse:collapse;font-size:13px"><thead><tr style="text-align:left;${MUTED};font-size:12px">
              <th style="padding:6px 8px;font-weight:500">Tier</th><th style="padding:6px 8px;font-weight:500">Users</th><th style="padding:6px 8px;font-weight:500">Space each</th>
              <th style="padding:6px 8px;font-weight:500">Overrides</th><th style="padding:6px 8px;font-weight:500">Promised</th></tr></thead>
              <tbody>${c.tiers.map(t => `<tr><td style="padding:8px;color:var(--text-primary)">${t.tier[0].toUpperCase() + t.tier.slice(1)}</td><td style="padding:8px">${t.users}</td>
                <td style="padding:8px">${esc(t.quota_label)}</td><td style="padding:8px">${t.overrides}</td><td style="padding:8px">${esc(t.promised_label)}</td></tr>`).join('')}</tbody></table>
            <p style="margin:0;font-size:12px;${MUTED}">Change the space each tier gets in the Policy tab.</p></section>
          <section style="${CARD};display:flex;flex-direction:column;gap:10px"><h3 style="${H}">Per-user space</h3>
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
              <select data-ou style="${INPUT};min-width:200px"><option value="">Choose a user…</option>${users.map(u => `<option value="${esc(u.id)}">${esc(u.username)}</option>`).join('')}</select>
              <input data-om type="number" min="0" placeholder="MB (0 = no limit)" style="${INPUT};width:150px" aria-label="Space for this user in MB">
              <button type="button" data-oadd class="card-btn card-btn--neutral card-btn--sm">Set</button></div>
            ${c.overrides.length ? `<div style="display:flex;flex-direction:column;gap:6px">${c.overrides.map(o => `<div style="display:flex;align-items:center;gap:10px;font-size:13px">
                <span style="flex:1;color:var(--text-primary)">${esc(o.username || o.user_uuid)}</span><span style="${MUTED}">${o.quota_mb ? `${o.quota_mb.toLocaleString()} MB` : 'No limit'}</span>
                <button type="button" data-odel="${esc(o.user_uuid)}" class="card-btn card-btn--neutral card-btn--sm">Use the tier's</button></div>`).join('')}</div>`
                : `<p style="margin:0;font-size:12px;${MUTED}">Nobody has space of their own — every user gets their tier's.</p>`}</section>
          <section style="${CARD};display:flex;flex-direction:column;gap:8px"><h3 style="${H}">Largest consumers</h3>
            ${c.top_users.length ? c.top_users.map(t => `<div style="display:flex;align-items:center;gap:10px;font-size:13px">
                <span style="flex:1;color:var(--text-primary)">${esc(t.username || t.user_uuid)}</span><span style="${MUTED}">${t.files} file${t.files === 1 ? '' : 's'}</span>
                <span style="min-width:80px;text-align:right;color:var(--text-primary);font-variant-numeric:tabular-nums">${esc(t.label)}</span></div>`).join('')
                : `<p style="margin:0;font-size:12px;${MUTED}">No files yet.</p>`}</section></div>`;
        host.querySelector('[data-oadd]').addEventListener('click', async () => {
            const uid = host.querySelector('[data-ou]').value, mb = host.querySelector('[data-om]').value;
            if (!uid || mb === '') { live(host, 'Choose a user and a size.'); return; }
            const r = await api(`/file-stores/user-quotas/${encodeURIComponent(uid)}`, { method: 'PUT', body: JSON.stringify({ quota_mb: Number(mb) }) });
            if (r.ok) renderCapacity(host); else live(host, r.body.message || 'Not saved');
        });
        host.querySelectorAll('[data-odel]').forEach(b => b.addEventListener('click', async () => {
            const r = await api(`/file-stores/user-quotas/${encodeURIComponent(b.dataset.odel)}`, { method: 'DELETE' });
            if (r.ok) renderCapacity(host); else live(host, r.body.message || 'Not saved');
        }));
        if (window.enhanceSelects) window.enhanceSelects(host);
    }

    // ── the agent editor's File store field (not admin-only) ─────────────────
    // The stores this user's tier may bind an agent to, the consequence said in words, and —
    // for a Coordinate agent — a warning when one of its experts keeps its files elsewhere.
    async function populateProfileStore(profile, profiles) {
        const sel = document.getElementById('profile-modal-file-store');
        const note = document.getElementById('profile-modal-file-store-note');
        if (!sel) return;
        const current = (profile && profile.fileStoreConfigId) || '';
        const r = await api('/file-stores/available');
        const stores = r.ok ? (r.body.stores || []) : [];
        const def = stores.find(x => x.id === (r.body.default_store_id || '')) || stores.find(x => x.is_default);
        sel.innerHTML = `<option value="">Default${def ? ` — ${esc(def.name)}` : ''}</option>`
            + stores.map(x => `<option value="${esc(x.id)}">${esc(x.name)}${x.encrypt ? '' : ' (not encrypted)'}</option>`).join('')
            + (current && !stores.some(x => x.id === current) ? `<option value="${esc(current)}">A store you can no longer use — the default applies</option>` : '');
        sel.value = current;
        const say = () => {
            if (!note) return;
            const chosen = stores.find(x => x.id === sel.value) || def;
            let html = chosen ? `New files from this agent are stored in <strong>${esc(chosen.name)}</strong>${chosen.encrypt ? ', encrypted' : ''}. Files already kept stay where they are.` : '';
            if (profile && profile.profile_type === 'genie') {
                const mine = sel.value || (def && def.id) || '';
                const slaves = ((profile.genieConfig || {}).slaveProfiles || []).map(id => profiles.find(p => p.id === id)).filter(Boolean);
                const differ = slaves.filter(p => (p.fileStoreConfigId || (def && def.id) || '') !== mine);
                if (differ.length) {
                    const names = differ.map(p => `@${esc(p.tag || p.name)}`).join(', ');
                    html += `<br><span style="color:var(--text-primary)">${names} ${differ.length === 1 ? 'keeps its' : 'keep their'} files in another store. Files kept from this agent's answers go to its own store, even when an expert produced the data.</span>`;
                }
            }
            note.innerHTML = html;
        };
        sel.onchange = say;
        say();
        if (window.enhanceSelects) window.enhanceSelects(sel.parentElement);
    }
    window.FileStorePicker = { populate: populateProfileStore };

    window.ComponentAdminTabs = window.ComponentAdminTabs || {};
    window.ComponentAdminTabs.files = [
        { id: 'stores', label: 'Stores', render: renderStores },
        { id: 'policy', label: 'Policy', render: renderPolicy },
        { id: 'keys', label: 'Keys', render: renderKeys },
        { id: 'capacity', label: 'Capacity', render: renderCapacity },
    ];
})();
