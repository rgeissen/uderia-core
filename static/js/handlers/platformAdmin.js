/**
 * platformAdmin — Administration → Components → Platform: Agent asking.
 *
 * Registers one tab on the Platform component's admin page through the one extension point
 * `window.ComponentAdminTabs` (platformConnectorAdminHandler.js), beside Overview and Users.
 *
 *   Agent asking   whether an agent may put a question to another of its user's agents
 *                  (TDA_Platform's `ask`), and the deployment's limits: how long a chain of
 *                  asks may be, how many asks one turn may make, how long one ask may take.
 *                  Off by default. Each agent's owner still lists which agents it may ask, and
 *                  an asked agent must be activated for REST use (core/platform_asking.py).
 *
 * Every change saves at once and says so (aria-live). Theme variables only; no emoji.
 */
(function () {
    'use strict';
    if (typeof window === 'undefined') return;

    const tok = () => localStorage.getItem('tda_auth_token');
    const hdrs = () => ({ 'Content-Type': 'application/json', ...(tok() ? { Authorization: `Bearer ${tok()}` } : {}) });
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const api = async (method, body) => {
        const r = await fetch('/api/v1/platform/asking', { method, headers: hdrs(), ...(body ? { body: JSON.stringify(body) } : {}) });
        let data = {};
        try { data = await r.json(); } catch (_) { data = {}; }
        return { ok: r.ok, data };
    };

    const CARD = 'background:var(--card-bg);border:1px solid var(--border-primary);border-radius:12px;padding:16px';
    const INPUT = 'height:32px;border:1px solid var(--border-primary);border-radius:8px;background:var(--input-bg,var(--bg-primary));color:var(--text-primary);padding:0 10px;font:inherit;font-size:13px;width:90px';
    const MUTED = 'color:var(--text-muted)';
    const H = 'margin:0;font-size:15px;font-weight:600;color:var(--text-primary)';
    // name → [label, unit and range, min, max]
    const LIMITS = [
        ['max_depth', 'Chain of asks', 'an asked agent may ask further up to this depth (1 = it may not ask on)', 1, 3],
        ['max_per_turn', 'Asks per turn', 'how many agents one turn may ask', 1, 10],
        ['timeout_s', 'Time per ask', 'seconds; an answer that takes longer stays in the asked agent\'s conversation', 30, 900],
    ];

    function live(host, msg) {
        const el = host.querySelector('[data-live]');
        if (el) { el.textContent = msg; clearTimeout(el._t); el._t = setTimeout(() => { el.textContent = ''; }, 4000); }
    }

    async function renderAsking(host) {
        host.style.textAlign = 'left';
        host.innerHTML = `<p style="${MUTED};font-size:13px">Loading…</p>`;
        const { ok, data } = await api('GET');
        if (!ok) { host.innerHTML = `<p style="${MUTED}">${esc(data.message || data.error || 'The settings could not be loaded.')}</p>`; return; }
        const s = data.settings || {};
        host.innerHTML = `<section style="${CARD};display:flex;flex-direction:column;gap:14px;max-width:760px">
            <div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap">
              <h3 style="${H};flex:1">Agents asking other agents</h3>
              <span data-live aria-live="polite" style="${MUTED};font-size:12px"></span>
            </div>
            <p style="${MUTED};font-size:13px;margin:0">An agent with the Platform component may put a question to another of its user's agents and use the answer.
              Its owner lists which agents it may ask; an asked agent must be activated for REST use, answers in a conversation of its own
              and works under its own authority. Every ask spends the asked agent's tokens.</p>
            <label style="display:flex;align-items:center;gap:10px;font-size:13px;color:var(--text-primary)">
              <span class="ind-toggle ind-toggle--primary"><input type="checkbox" data-ask-enabled ${s.enabled ? 'checked' : ''} aria-label="Agents may ask other agents"><span class="ind-track"></span></span>
              <span>Agents may ask other agents</span>
            </label>
            <div style="display:flex;flex-direction:column;gap:10px" data-limits>
              ${LIMITS.map(([k, label, hint, lo, hi]) => `<label style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:13px">
                  <span style="color:var(--text-primary);min-width:120px">${esc(label)}</span>
                  <input type="number" data-limit="${k}" min="${lo}" max="${hi}" value="${esc(s[k])}" style="${INPUT}" aria-label="${esc(label)}">
                  <span style="${MUTED};font-size:12px;flex:1;min-width:200px">${esc(hint)} (${lo}–${hi})</span>
                </label>`).join('')}
            </div>
          </section>`;
        const sync = () => host.querySelectorAll('[data-limit]').forEach(i => { i.disabled = !host.querySelector('[data-ask-enabled]').checked; });
        sync();
        const save = async (body, said) => {
            const r = await api('PUT', body);
            if (!r.ok) { live(host, r.data.message || 'Not saved.'); return; }
            const out = r.data.settings || {};
            host.querySelectorAll('[data-limit]').forEach(i => { i.value = out[i.dataset.limit]; });
            live(host, said);
        };
        host.querySelector('[data-ask-enabled]').addEventListener('change', (e) => {
            sync();
            save({ enabled: e.target.checked }, e.target.checked ? 'Asking is on.' : 'Asking is off.');
        });
        host.querySelectorAll('[data-limit]').forEach(i => i.addEventListener('change', () => {
            save({ [i.dataset.limit]: Number(i.value) }, 'Saved.');
        }));
    }

    window.ComponentAdminTabs = window.ComponentAdminTabs || {};
    window.ComponentAdminTabs.platform = [
        { id: 'asking', label: 'Agent asking', render: renderAsking },
    ];
})();
