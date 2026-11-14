/**
 * The Platform component's roles as people read them — ONE renderer for the three places a
 * role is decided or read: the component's Roles tab (Components → Platform), the agent
 * editor's role picker, and the playbook skill's card (Components → Skill Library →
 * Libraries). All of it comes from GET /api/v1/platform/roles, which the server computes by
 * the very rules TDA_Platform enforces — a view here cannot disagree with what an agent may do.
 *
 * Contract: docs/Architecture/PLATFORM_OPERATIONS_ARCHITECTURE.md §21. Examples and boundaries
 * are written per role (components/builtin/platform/roles/<id>.json) and shown here first.
 */

const _cache = {};

/** The roles (counts; with `detail`, each role's operations, playbooks and agents, and each
 *  playbook's roles). One request per page per shape; `invalidateRoles()` drops it. */
export function fetchRoles(detail = false) {
    const key = detail ? 'detail' : 'counts';
    if (!_cache[key]) {
        const tok = localStorage.getItem('tda_auth_token');
        _cache[key] = fetch(`/api/v1/platform/roles${detail ? '?detail=1' : ''}`, {
            headers: tok ? { Authorization: `Bearer ${tok}` } : {},
        }).then(r => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
          .catch(e => { delete _cache[key]; throw e; });
    }
    return _cache[key];
}

export function invalidateRoles() {
    Object.keys(_cache).forEach(k => delete _cache[k]);
}

export function esc(t) {
    return String(t ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

const _n = (n, one, many) => `${n} ${n === 1 ? one : (many || one + 's')}`;

/** One line: what a role reaches and teaches, and who holds it. A planned role says it grants
 *  nothing yet and what it waits for — never a count that reads as a grant. */
export function countsLine(role) {
    const c = role.counts || {};
    if (role.status !== 'active') {
        const needs = (role.needs || []).length ? ` — waits for ${role.needs.join(', ')}` : '';
        return `planned: grants nothing yet${needs}`;
    }
    const parts = [`reaches ${_n(c.callable ?? 0, 'operation')}`];
    if (c.writes) parts.push(`may submit ${_n(c.writes, 'change')}`);
    if (role.asks) parts.push('asks other agents');
    parts.push(`teaches ${_n(c.playbooks ?? 0, 'playbook')}`);
    if (c.agents !== undefined) parts.push(`held by ${_n(c.agents, 'agent')}`);
    return parts.join(' · ');
}

const _summaryStyle = 'cursor:pointer;color:var(--text-primary);font-size:0.75rem;padding:2px 0;';
const _rowStyle = 'font-size:0.72rem;padding:2px 0 2px 12px;color:var(--text-muted);';
const _opStyle = 'font-family:ui-monospace,monospace;color:var(--text-primary);';

function _fold(title, count, body) {
    if (!count) return '';
    return `<details class="prv-fold" style="margin-top:4px;">
        <summary style="${_summaryStyle}">${esc(title)} · ${count}</summary>${body}</details>`;
}

/** The folds under a role (needs the `detail` shape): the playbooks it teaches, the changes it
 *  can submit (with the ones a person always approves marked), what it reads, what it lists
 *  but cannot call and why, and the agents that hold it. */
export function reachHTML(role) {
    const ops = role.operations || [];
    const writes = ops.filter(o => o.callable && o.effect === 'write');
    const reads = ops.filter(o => o.callable && o.effect === 'read');
    const not = ops.filter(o => !o.callable);
    const pbs = role.playbooks || [];
    const agents = role.agents || [];
    const op = o => `<div style="${_rowStyle}"><span style="${_opStyle}">${esc(o.op)}</span> — ${esc(o.summary)}${
        o.always_reviewed ? ` <span style="color:var(--teradata-orange,#F15F22);">· always approved by you (${esc(o.always_reviewed)})</span>` : ''}${
        o.why_not ? `<div style="padding-left:12px;">${esc(o.why_not)}</div>` : ''}</div>`;
    const examples = role.examples || [];
    const notOffered = role.not_offered || [];
    const covers = n => {
        const byOp = {};   // the arguments an operation leaves out, under it once
        (n.args || []).forEach(a => { const [o, arg] = String(a).split(':'); (byOp[o] = byOp[o] || []).push(arg); });
        return [...(n.ops || []).map(o => `<span style="${_opStyle}">${esc(o)}</span>`),
                ...Object.entries(byOp).map(([o, args]) => `<span style="${_opStyle}">${esc(o)}</span> · ${esc(args.join(', '))}`)].join(', ');
    };
    return [
        _fold('Examples', examples.length, examples.map(e =>
            `<div style="${_rowStyle}"><span style="color:var(--text-primary);">“${esc(e.ask)}”</span><div>${esc(e.outcome)}</div></div>`).join('')),
        _fold('What it does not offer — and why', notOffered.length, notOffered.map(n =>
            `<div style="${_rowStyle}"><span style="color:var(--text-primary);">${esc(n.what)}</span><div>${esc(n.why)}</div>${
                covers(n) ? `<div style="opacity:0.8;">${covers(n)}</div>` : ''}</div>`).join('')),
        _fold('Playbooks it teaches', pbs.length, pbs.map(p =>
            `<div style="${_rowStyle}"><span style="color:var(--text-primary);">${esc(p.title || p.anchor)}</span> <span style="${_opStyle}opacity:0.7;">${esc(p.anchor)}</span>${p.summary ? `<div>${esc(p.summary)}</div>` : ''}</div>`).join('')),
        _fold('Changes it can submit', writes.length, writes.map(op).join('')),
        _fold('What it reads', reads.length, reads.map(op).join('')),
        _fold('Listed, not callable', not.length, not.map(op).join('')),
        agents.length
            ? `<div style="font-size:0.72rem;margin-top:4px;color:var(--text-muted);">Held by ${agents.map(a => `<span style="${_opStyle}">@${esc(a.tag)}</span>`).join(', ')}</div>`
            : '',
    ].join('');
}

/** The manifest's declaration in one sentence — where an agent's roles are kept, the default,
 *  and the skill whose playbooks the roles slice. */
export function declarationLine(data) {
    const d = data.declaration || {};
    const byId = Object.fromEntries((data.roles || []).map(r => [r.id, r.title]));
    const def = (d.default || data.default || []).map(id => byId[id] || id).join(', ');
    return `Chosen per agent in the agent editor (stored as ${esc(d.grant || 'componentConfig.platform.roles')}). `
         + `An agent that chooses none holds ${esc(def || 'no role')}. Playbooks come from the ${esc(d.playbooks || '')} skill. `
         + `A role only narrows what the Platform component offers, carries no user tier, and what it grants is part of the agent's signature.`;
}
