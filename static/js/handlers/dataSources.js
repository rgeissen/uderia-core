/**
 * PROP-006 — the profile editor's DATA SOURCES section: every source an agent reads, in one place.
 *
 *   Tools        the agent's MCP server (the `#profile-field-mcp-server` select — the same
 *                element, moved here; its id, population and save are unchanged) and, phase 2,
 *                any ADDITIONAL servers (`additionalMcpServers: [{id, alias, tools, prompts}]`):
 *                added and removed here, each with a short name; its tools and prompts are
 *                picked per server under Resources (`#profile-modal-additional-mcp`). A primary
 *                server's tools keep their names; an additional server's read `<alias>__<name>`.
 *   Databases    the Database Navigator's connections: the default catalog source
 *                (`componentConfig.database_navigator.sourceId`) and the databases the agent may
 *                query itself (`…query = {enabled, sources}`) — storage keys unchanged
 *   Combining    shown only when the agent reads two or more sources: whether this deployment
 *                combines results, the administrator's rules (locked — they cannot be loosened
 *                here) and the agent's own rules (`combineIsolation`, which only ADD restrictions)
 *
 * Rules this module keeps:
 *   - STATE is the truth; controls edit state, render draws state. A save before the async data
 *     arrived carries the STORED values forward — an empty UI is not an empty choice.
 *   - A stored id this instance does not have stays visible and says so (it is refused at run
 *     time, never silently dropped).
 *   - Theme variables only (no Tailwind colour class, no bare colour); monochrome icons.
 *   - The pure parts are exported so the gate executes the shipped code.
 */

const QUERY_TIP = 'A single SELECT per call, on a read-only connection, with the same protections as an MCP SQL tool: authority, loop guard, turn budget, preflight and the result firewall. Recorded in the agent\'s signature.';

export function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

const ICON = {
    mcp: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><rect x="3" y="4" width="18" height="6" rx="1.5"/><rect x="3" y="14" width="18" height="6" rx="1.5"/><path d="M7 7h.01M7 17h.01"/></svg>',
    db: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><ellipse cx="12" cy="5.5" rx="7" ry="2.5"/><path d="M5 5.5v13c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5v-13M5 12c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5"/></svg>',
    lock: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="1.5"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>',
    x: '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    plus: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
    split: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M8 4v16M16 4v16M3 12h2M19 12h2"/></svg>',
};

// ── pure model ──────────────────────────────────────────────────────────────────────────

/** Normalise the stored query block ({enabled, sources}) — absent means off. */
export function normQuery(q) {
    return { enabled: !!(q && q.enabled === true), sources: Array.isArray(q && q.sources) ? q.sources.map(String) : [] };
}

/** Normalise stored agent rules — kept as stored; the server validated them. */
export function normRules(r) {
    return (Array.isArray(r) ? r : []).filter(x => x && Array.isArray(x.sources))
        .map(x => ({ sources: x.sources.map(String), ...(x.reason ? { reason: String(x.reason) } : {}) }));
}

/**
 * Every source the agent reads, as rows. `st`: {usesMcp, mcpServerId, navOn, query, sourceId,
 * view}. `view` is the /v1/data-sources payload (or null while loading).
 */
export function effectiveSources(st) {
    const view = st.view || { mcp_servers: [], connections: [] };
    const rows = [];
    if (st.usesMcp && st.mcpServerId) {
        const sv = (view.mcp_servers || []).find(x => String(x.id) === String(st.mcpServerId));
        rows.push({ source_id: `mcp:${st.mcpServerId}`, kind: 'mcp', role: 'tools',
            name: sv ? sv.name : String(st.mcpServerId), endpoint: sv ? sv.endpoint : null,
            missing: !!st.view && !sv });
    }
    if (st.usesMcp && st.mcpServerId) {
        for (const a of (st.additional || [])) {
            const sv = (view.mcp_servers || []).find(x => String(x.id) === String(a.id));
            rows.push({ source_id: `mcp:${a.id}`, kind: 'mcp', role: 'tools', alias: a.alias,
                name: sv ? sv.name : String(a.id), endpoint: sv ? sv.endpoint : null,
                missing: !!st.view && !sv });
        }
    }
    if (st.navOn && st.query && st.query.enabled) {
        for (const sid of st.query.sources) {
            const c = (view.connections || []).find(x => x.source_id === sid);
            rows.push({ source_id: sid, kind: 'database', role: 'queries',
                name: c ? c.name : sid, endpoint: c ? c.endpoint : null, driver: c ? c.driver : '',
                isDefault: st.sourceId === sid, missing: !!st.view && !c });
        }
    }
    return rows;
}

/** How many distinct sources the agent reads — the ONE number that decides whether the
 *  Combining card is shown (a single-source agent never sees it). The navigator's default
 *  connection counts as one source when queries are on with none chosen. */
export function sourceCount(st) {
    const n = effectiveSources(st).length;
    const implicit = (st.navOn && st.query && st.query.enabled && !st.query.sources.length) ? 1 : 0;
    return n + implicit;
}

/** The one-line summary at the top of the section, in words. */
export function summaryText(st) {
    const n = sourceCount(st);
    if (!n) return 'This agent reads no data source yet.';
    const parts = [`Reads ${n} source${n === 1 ? '' : 's'}`];
    if (n >= 2) {
        const c = (st.view && st.view.combine) || null;
        parts.push(c ? (c.available ? 'may combine results' : 'combining is off in this deployment') : 'combining state loading');
        const lim = rulesView(st).applying;
        if (lim) parts.push(`${lim} rule${lim === 1 ? '' : 's'} limit${lim === 1 ? 's' : ''} combining`);
    }
    return parts.join(' · ');
}

/** The Combining card's model: shown only with two or more sources (or a stored agent rule, so
 *  a rule is never hidden). Admin rules that bind two of the agent's sources are listed first,
 *  locked. An agent rule naming a source the agent no longer reads is kept and marked. */
export function rulesView(st) {
    const rows = effectiveSources(st);
    const ids = new Set(rows.map(r => r.source_id));
    const nameOf = (sid) => {
        const r = rows.find(x => x.source_id === sid);
        if (r) return r.name;
        const v = st.view || {};
        const any = [...(v.mcp_servers || []), ...(v.connections || [])].find(x => x.source_id === sid);
        return any ? any.name : sid;
    };
    const admin = ((st.view && st.view.admin_rules) || []).map(r => {
        const yours = r.yours || {};
        const bound = Object.values(yours).flat().filter(sid => ids.has(sid));
        const groups = (r.endpoints || []).map(ep => ({ endpoint: ep, sources: (yours[ep] || []).filter(sid => ids.has(sid)).map(nameOf) }));
        return { reason: r.reason || '', groups, applies: groups.filter(g => g.sources.length).length >= 2, _bound: bound.length };
    }).filter(r => r.applies);
    const agent = (st.rules || []).map((r, i) => ({
        index: i, reason: r.reason || '',
        members: r.sources.map(sid => ({ source_id: sid, name: nameOf(sid), absent: !ids.has(sid) })),
    }));
    const n = sourceCount(st);
    return {
        shown: n >= 2 || agent.length > 0,
        admin, agent,
        applying: admin.length + agent.filter(a => a.members.filter(m => !m.absent).length >= 2).length,
        canAdd: rows.length >= 2,
        addBlocked: rows.length >= 2 ? '' : 'A rule names two of this agent\'s sources — add another source first.',
    };
}

// ── additional MCP servers (phase 2) ────────────────────────────────────────────────────

/** The short-name rule — mirrors core/tool_identity.py ALIAS_RE (lower-case letters and digits,
 *  single underscores between them, a letter first, at most 12 characters). */
export const ALIAS_RE = /^[a-z](?:[a-z0-9]|_(?=[a-z0-9]))*$/;
export const ALIAS_MAX = 12;
export const MAX_ADDITIONAL = 8;
export const SEP = '__';

export function validAlias(a) { return typeof a === 'string' && a.length <= ALIAS_MAX && ALIAS_RE.test(a); }

/** Why a short name is not usable, in words — '' when it is. */
export function aliasProblem(alias, others) {
    if (!alias) return 'Give this server a short name.';
    if (alias.length > ALIAS_MAX) return `At most ${ALIAS_MAX} characters.`;
    if (!ALIAS_RE.test(alias)) return 'Lower-case letters and digits, a letter first; single underscores between them.';
    if ((others || []).includes(alias)) return 'Another server already uses this short name.';
    return '';
}

/** Words that never tell one MCP server from another. */
const GENERIC_WORDS = new Set(['mcp', 'server', 'servers', 'srv', 'the']);

/** A short name suggested from a server's name: its DISTINCTIVE words (words the first server's
 *  name also carries, and generic words like "MCP", are dropped — "Teradata MCP - dbc" beside
 *  "Teradata MCP - sales" suggests `dbc`), slugged, a letter first, at most 12 characters,
 *  unique among `taken` (a digit is appended when needed). Always returns a valid alias. */
export function suggestAlias(name, taken, primaryName) {
    const used = new Set(taken || []);
    const words = (x) => String(x || '').toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
    const common = new Set(words(primaryName));
    const all = words(name);
    const distinct = all.filter(w => !common.has(w) && !GENERIC_WORDS.has(w));
    const pick = distinct.length ? distinct : all.filter(w => !GENERIC_WORDS.has(w));
    let base = (pick.length ? pick : all).join('_').replace(/^[0-9_]+/, '');
    if (!base) base = 'srv';
    base = base.slice(0, ALIAS_MAX).replace(/_+$/, '');
    if (!validAlias(base)) base = 'srv';
    if (!used.has(base)) return base;
    for (let i = 2; i < 100; i++) {
        const suf = String(i);
        const cand = base.slice(0, ALIAS_MAX - suf.length).replace(/_+$/, '') + suf;
        if (validAlias(cand) && !used.has(cand)) return cand;
    }
    return 'srv' + Date.now().toString().slice(-6);
}

/** The stored list, normalised (entries kept in order; the server validated them). */
export function normAdditional(raw) {
    return (Array.isArray(raw) ? raw : []).filter(e => e && e.id)
        .map(e => ({ id: String(e.id), alias: String(e.alias || ''),
            tools: Array.isArray(e.tools) ? e.tools.map(String) : ['*'],
            prompts: Array.isArray(e.prompts) ? e.prompts.map(String) : ['*'] }));
}

/** The servers that can be added: every MCP server of the user, each saying why it cannot be
 *  added when it cannot (the primary, already added, the limit) — never hidden. */
export function addableServers(st) {
    const servers = (st.view && st.view.mcp_servers) || [];
    const added = new Set((st.additional || []).map(a => a.id));
    const full = (st.additional || []).length >= MAX_ADDITIONAL;
    return servers.map(sv => {
        const id = String(sv.id);
        const why = id === String(st.mcpServerId) ? 'primary' : added.has(id) ? 'added' : full ? `at most ${MAX_ADDITIONAL}` : '';
        return { id, name: sv.name, endpoint: sv.endpoint, why };
    });
}

/** "N of M tools" for a server's selection, in words; the stored selection before the
 *  server's catalogue loaded. */
export function selectionText(sel, names, noun) {
    const all = sel.includes('*');
    if (!names) return all ? `all ${noun}` : `${sel.length} ${noun}`;
    const n = all ? names.length : names.filter(x => sel.includes(x)).length;
    return `${n} of ${names.length} ${noun}`;
}

/** Toggle one name in a selection against the server's full list: all chosen collapses to
 *  ['*'] (the primary's rule), otherwise a literal list in catalogue order. */
export function toggleSelection(sel, name, on, names) {
    const cur = new Set(sel.includes('*') ? names : sel);
    if (on) cur.add(name); else cur.delete(name);
    const out = names.filter(x => cur.has(x));
    return out.length === names.length && names.length ? ['*'] : out;
}

/** Every short-name problem, keyed by server id — a save with any is refused before it is sent. */
export function additionalErrors(st) {
    const out = {};
    const list = (st && st.additional) || [];
    list.forEach((a, i) => {
        const others = list.filter((_, j) => j !== i).map(x => x.alias);
        const p = aliasProblem(a.alias, others);
        if (p) out[a.id] = p;
    });
    return out;
}

// ── collect (the save) ──────────────────────────────────────────────────────────────────

/** The query block to save — same contract as before the move: off with nothing chosen writes
 *  no key (baseline); before the section rendered, the stored block is carried forward. */
export function collectQuery(st, storedQuery) {
    if (!st || !st.rendered) return storedQuery || null;
    const q = st.query || { enabled: false, sources: [] };
    if (!q.enabled && !q.sources.length) return null;
    return { enabled: !!q.enabled, sources: [...q.sources] };
}

/** The navigator's pinned catalog source — empty = auto (no key). Carried forward if not rendered. */
export function collectSourceId(st, storedSourceId) {
    if (!st || !st.rendered) return storedSourceId || '';
    return st.sourceId || '';
}

/** The agent's own rules — carried forward until rendered; `undefined` = do not send the key. */
export function collectRules(st, storedRules) {
    if (!st || !st.rendered) return undefined;
    const now = normRules(st.rules);
    const before = normRules(storedRules);
    if (!now.length && !before.length) return undefined;       // nothing ever set: no key (baseline)
    return now;                                                  // [] clears a stored set
}

/** `additionalMcpServers` to save — `undefined` = do not send the key (before the section
 *  rendered, or nothing ever set: a single-server agent stays byte-identical). `[]` removes a
 *  stored list; so does an agent that no longer calls MCP tools. */
export function collectAdditional(st, storedAdditional, { usesMcp = true } = {}) {
    const before = normAdditional(storedAdditional);
    if (!st || !st.rendered) return undefined;
    const now = usesMcp ? (st.additional || []).map(a => ({ id: a.id, alias: a.alias, tools: [...a.tools], prompts: [...a.prompts] })) : [];
    if (!now.length && !before.length) return undefined;
    return now;
}

// ── render ──────────────────────────────────────────────────────────────────────────────

export function injectCss(doc = document) {
    if (doc.getElementById('ds-css')) return;
    const s = doc.createElement('style');
    s.id = 'ds-css';
    s.textContent = `
/* the editor's global \`#profile-modal label\` rule hard-codes a dark-theme colour; the
   section's labels follow the theme, so they are scoped by id to win on specificity */
#profile-modal #profile-data-sources-body label { color:var(--text-muted); padding:0; }
.ds-summary { display:flex; align-items:center; gap:8px; padding:10px 14px; border:1px solid var(--border-secondary); border-radius:10px; color:var(--text-primary); background:var(--card-bg); font-size:0.85rem; }
.ds-summary-dot { width:8px; height:8px; border-radius:999px; background:var(--teradata-orange); flex:0 0 auto; }
.ds-panel { border:1px solid var(--border-secondary); border-radius:12px; background:var(--card-bg); padding:14px 16px; }
.ds-panel + .ds-panel { margin-top:12px; }
.ds-panel-h { display:flex; align-items:baseline; justify-content:space-between; gap:12px; margin-bottom:10px; }
.ds-panel-t { color:var(--text-primary); font-weight:600; font-size:0.9rem; }
.ds-panel-sub { color:var(--text-muted); font-size:0.75rem; }
.ds-row { display:grid; grid-template-columns:28px 1fr auto; gap:10px; align-items:center; padding:9px 10px; border:1px solid var(--border-secondary); border-radius:10px; }
.ds-row + .ds-row { margin-top:6px; }
.ds-row.is-missing { border-style:dashed; }
.ds-glyph { width:28px; height:28px; display:inline-flex; align-items:center; justify-content:center; border-radius:8px; border:1px solid var(--border-secondary); color:var(--text-muted); }
.ds-name { color:var(--text-primary); font-size:0.85rem; font-weight:600; }
.ds-ep { color:var(--text-muted); font-size:0.72rem; font-family:ui-monospace, SFMono-Regular, Menlo, monospace; word-break:break-all; }
.ds-meta { display:flex; flex-wrap:wrap; gap:6px; align-items:center; justify-content:flex-end; }
.ds-pill { font-size:0.68rem; padding:1px 8px; border-radius:999px; border:1px solid var(--border-secondary); color:var(--text-muted); white-space:nowrap; }
.ds-pill.is-default { color:var(--text-primary); border-color:var(--teradata-orange); }
.ds-pill.is-warn { color:var(--text-primary); border-style:dashed; }
.ds-act { font-size:0.72rem; color:var(--text-muted); background:transparent; border:1px solid var(--border-secondary); border-radius:6px; padding:2px 8px; cursor:pointer; display:inline-flex; align-items:center; gap:4px; }
.ds-act:hover, .ds-act:focus-visible { color:var(--text-primary); border-color:var(--teradata-orange); outline:none; }
.ds-act[disabled] { opacity:.5; cursor:not-allowed; }
.ds-icon-btn { width:24px; height:24px; justify-content:center; padding:0; }
.ds-line { color:var(--text-muted); font-size:0.78rem; margin-top:8px; }
.ds-line b { color:var(--text-primary); font-weight:600; }
.ds-field { display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:10px; }
.ds-field label { color:var(--text-muted); font-size:0.75rem; }
.ds-select { background:var(--bg-primary); color:var(--text-primary); border:1px solid var(--border-secondary); border-radius:8px; padding:4px 8px; font-size:0.8rem; }
.ds-toggle-row { display:flex; align-items:center; gap:10px; margin-bottom:10px; color:var(--text-primary); font-size:0.82rem; }
.ds-tip { color:var(--text-muted); font-size:0.72rem; cursor:help; }
.ds-picker { margin-top:8px; border:1px solid var(--border-secondary); border-radius:10px; padding:8px; background:var(--bg-primary); }
.ds-picker input[type=search] { width:100%; background:var(--card-bg); color:var(--text-primary); border:1px solid var(--border-secondary); border-radius:6px; padding:5px 8px; font-size:0.8rem; margin-bottom:6px; }
.ds-pick { display:grid; grid-template-columns:1fr auto; gap:8px; align-items:center; width:100%; text-align:left; background:transparent; border:1px solid transparent; border-radius:8px; padding:6px 8px; cursor:pointer; color:var(--text-primary); }
.ds-pick:hover, .ds-pick:focus-visible { border-color:var(--teradata-orange); outline:none; }
.ds-pick[disabled] { cursor:not-allowed; color:var(--text-muted); }
.ds-rule { display:flex; flex-wrap:wrap; align-items:center; gap:6px; padding:8px 10px; border:1px solid var(--border-secondary); border-radius:10px; color:var(--text-primary); font-size:0.82rem; }
.ds-rule + .ds-rule { margin-top:6px; }
.ds-rule.is-admin { border-style:dashed; }
.ds-rule-by { margin-left:auto; color:var(--text-muted); font-size:0.7rem; display:inline-flex; align-items:center; gap:4px; }
.ds-chip { display:inline-flex; align-items:center; gap:4px; padding:1px 8px; border-radius:999px; border:1px solid var(--border-secondary); font-size:0.75rem; color:var(--text-primary); }
.ds-chip.is-absent { color:var(--text-muted); border-style:dashed; }
.ds-chip-pick { cursor:pointer; background:transparent; }
.ds-chip-pick[aria-pressed="true"] { border-color:var(--teradata-orange); }
.ds-reason { color:var(--text-muted); font-size:0.72rem; width:100%; }
.ds-input { flex:1; min-width:160px; background:var(--card-bg); color:var(--text-primary); border:1px solid var(--border-secondary); border-radius:6px; padding:4px 8px; font-size:0.78rem; }
.ds-state { display:inline-flex; align-items:center; gap:6px; font-size:0.78rem; color:var(--text-primary); }
.ds-state.is-off { color:var(--text-muted); }
.ds-note { margin-top:12px; padding:8px 12px; border-left:3px solid var(--teradata-orange); color:var(--text-muted); font-size:0.78rem; }
.ds-test-ok { color:var(--text-primary); }
.ds-alias-row { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-top:6px; }
.ds-alias-row .ds-input { flex:0 0 auto; width:9.5rem; min-width:0; font-family:ui-monospace, SFMono-Regular, Menlo, monospace; }
.ds-input[aria-invalid="true"] { border-color:var(--teradata-orange); }
.ds-alias-help { color:var(--text-muted); font-size:0.72rem; }
.ds-alias-help b { color:var(--text-primary); font-weight:600; font-family:ui-monospace, SFMono-Regular, Menlo, monospace; }
.ds-alias-problem { color:var(--text-primary); font-size:0.72rem; margin-top:4px; padding-left:8px; border-left:2px solid var(--teradata-orange); }
.ds-add-row { margin-top:8px; display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
#profile-modal #profile-modal-additional-mcp label, #profile-modal #profile-modal-primary-mcp-label label { color:var(--text-primary); padding:0; }
.ds-res-group { margin-top:16px; padding-top:14px; border-top:1px solid var(--border-secondary); }
.ds-res-h { display:grid; grid-template-columns:28px 1fr auto; gap:10px; align-items:center; margin-bottom:10px; }
.ds-res-cols { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
.ds-res-cols.is-one { grid-template-columns:1fr; }
.ds-res-sub { display:flex; align-items:center; gap:8px; margin-bottom:6px; color:var(--text-muted); font-size:0.72rem; text-transform:uppercase; letter-spacing:.04em; }
.ds-res-sub .ds-res-count { text-transform:none; letter-spacing:0; }
.ds-res-sub .ds-act { margin-left:auto; text-transform:none; letter-spacing:0; }
.ds-res-sub .ds-act + .ds-act { margin-left:0; }
.ds-res-list { max-height:12rem; overflow-y:auto; padding:8px 12px; border:1px solid var(--border-secondary); border-radius:8px; background:var(--bg-primary); display:flex; flex-direction:column; gap:4px; }
.ds-res-item { gap:8px; font-size:0.82rem; color:var(--text-primary); width:100%; }
.ds-res-primary { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:10px; color:var(--text-muted); font-size:0.78rem; }
.ds-res-primary .ds-name { font-size:0.82rem; }
`;
    doc.head.appendChild(s);
}

export function summaryHtml(st) {
    return `<div class="ds-summary" role="status"><span class="ds-summary-dot" aria-hidden="true"></span><span>${esc(summaryText(st))}</span></div>`;
}

function srcRowHtml(r, extra) {
    const pills = [];
    pills.push(`<span class="ds-pill">${r.role === 'tools' ? 'tools' : 'queries'}${r.driver ? ' · ' + esc(r.driver) : ''}</span>`);
    if (r.isDefault) pills.push('<span class="ds-pill is-default">default</span>');
    if (r.missing) pills.push('<span class="ds-pill is-warn">not on this instance — refused at run time</span>');
    return `
      <div class="ds-row${r.missing ? ' is-missing' : ''}" data-ds-source="${esc(r.source_id)}">
        <span class="ds-glyph">${r.kind === 'mcp' ? ICON.mcp : ICON.db}</span>
        <div><div class="ds-name">${esc(r.name)}</div>${r.endpoint ? `<div class="ds-ep">${esc(r.endpoint)}</div>` : ''}${extra && extra.body ? extra.body : ''}</div>
        <div class="ds-meta">${pills.join('')}${extra && extra.actions ? extra.actions : ''}</div>
      </div>`;
}

export function databasesHtml(st) {
    if (st.navOn === false) {
        return `<div class="ds-line">The Database Navigator is off for this agent, so it reads no database directly.
                <button type="button" class="ds-act" data-ds-act="open-capabilities">Turn it on under Capabilities</button></div>`;
    }
    const view = st.view;
    const conns = (view && view.connections) || [];
    const opts = ['<option value="">Auto — your default connection, then your first</option>']
        .concat(conns.filter(c => c.queryable).map(c => `<option value="${esc(c.source_id)}"${st.sourceId === c.source_id ? ' selected' : ''}>${esc(c.name)}</option>`));
    if (st.sourceId && !conns.some(c => c.source_id === st.sourceId)) {
        opts.push(`<option value="${esc(st.sourceId)}" selected>${esc(st.sourceId)} (not found)</option>`);
    }
    const q = st.query || { enabled: false, sources: [] };
    let html = `
      <div class="ds-field"><label for="ds-default-source">Default database (catalog)</label>
        <select id="ds-default-source" class="ds-select" data-ds-act="default-source">${opts.join('')}</select></div>
      <div class="ds-toggle-row">
        <label class="ind-toggle ind-toggle--sm"><input type="checkbox" data-ds-act="toggle-query" ${q.enabled ? 'checked' : ''}><span class="ind-track"></span></label>
        <span>Let the agent run its own read-only queries</span>
        <span class="ds-tip u-tip" data-tip="${esc(QUERY_TIP)}">What is checked?</span>
      </div>`;
    if (!q.enabled) {
        html += `<div class="ds-line">Queries are off — the agent browses the catalog but runs no SQL of its own.</div>`;
        return html;
    }
    const rows = effectiveSources(st).filter(r => r.kind === 'database');
    if (!rows.length) {
        html += srcRowHtml({ source_id: '', kind: 'database', role: 'queries', name: 'The default database', endpoint: 'resolved when the agent runs — pick databases below to fix the list' });
    }
    html += rows.map(r => srcRowHtml(r, {
        actions: `${!r.missing && !r.isDefault ? `<button type="button" class="ds-act" data-ds-act="make-default" data-ds-id="${esc(r.source_id)}">Make default</button>` : ''}`
            + `${!r.missing ? `<button type="button" class="ds-act" data-ds-act="test" data-ds-id="${esc(r.source_id)}">Test</button>` : ''}`
            + `<button type="button" class="ds-act ds-icon-btn" data-ds-act="remove" data-ds-id="${esc(r.source_id)}" aria-label="Remove ${esc(r.name)}">${ICON.x}</button>`,
        body: st.tests && st.tests[r.source_id] ? `<div class="ds-line${st.tests[r.source_id].ok ? ' ds-test-ok' : ''}">${esc(st.tests[r.source_id].text)}</div>` : '',
    })).join('');
    html += `<div style="margin-top:8px;"><button type="button" class="ds-act" data-ds-act="open-picker" aria-expanded="${st.pickerOpen ? 'true' : 'false'}">${ICON.plus} Add a database</button></div>`;
    if (st.pickerOpen) html += pickerHtml(st);
    return html;
}

/** The add-a-database picker: every connection, searchable; one already added, or one the
 *  agent cannot query (not a direct driver), is shown and says why — never hidden. */
export function pickerListHtml(st) {
    const conns = ((st.view && st.view.connections) || []);
    const added = new Set((st.query && st.query.sources) || []);
    const f = String(st.pickerFilter || '').toLowerCase();
    const list = conns.filter(c => !f || `${c.name} ${c.driver} ${c.endpoint || ''}`.toLowerCase().includes(f));
    if (!st.view) return '<div class="ds-line">Loading your connections…</div>';
    if (!conns.length) return '<div class="ds-line">No database connections yet. <button type="button" class="ds-act" data-ds-act="manage-connections">Add one under Connectors</button></div>';
    if (!list.length) return '<div class="ds-line">No connection matches.</div>';
    return list.map(c => {
        const why = added.has(c.source_id) ? 'added' : (!c.queryable ? 'cannot be queried directly' : '');
        return `<button type="button" class="ds-pick" data-ds-act="add" data-ds-id="${esc(c.source_id)}" ${why ? 'disabled' : ''}>
            <span><span class="ds-name">${esc(c.name)}</span><br><span class="ds-ep">${esc(c.endpoint || c.driver)}</span></span>
            <span class="ds-pill">${esc(why || c.driver)}</span></button>`;
    }).join('');
}

export function pickerHtml(st) {
    return `<div class="ds-picker" role="dialog" aria-label="Add a database">
        <input type="search" data-ds-act="picker-filter" placeholder="Search connections" value="${esc(st.pickerFilter || '')}" aria-label="Search connections">
        <div class="ds-pick-list">${pickerListHtml(st)}</div></div>`;
}

export function combiningHtml(st) {
    const rv = rulesView(st);
    if (!rv.shown) return '';
    const c = (st.view && st.view.combine) || null;
    const state = !c ? '<span class="ds-state is-off">checking this deployment…</span>'
        : c.available
            ? `<span class="ds-state">${ICON.split} On — the agent may join rows from these sources in one SELECT, run by code, never by the model.</span>`
            : `<span class="ds-state is-off">${ICON.split} Off in this deployment — ${esc(c.reason || 'switched off')}. Rules below still apply once it is turned on.</span>`;
    const incomplete = c && c.available
        ? `<div class="ds-line">A result that is only a first page (truncated or cut at its limit) is ${c.incomplete_inputs === 'disclose' ? '<b>used and disclosed</b> in the answer' : '<b>refused</b> as an input'} — set by your administrator.</div>` : '';
    const chip = (m) => `<span class="ds-chip${m.absent ? ' is-absent' : ''}">${esc(m.name)}${m.absent ? ' (not among this agent\'s sources)' : ''}</span>`;
    const adminRows = rv.admin.map(r => `
        <div class="ds-rule is-admin">Never combine ${r.groups.map(g => g.sources.length ? g.sources.map(n => `<span class="ds-chip">${esc(n)}</span>`).join(' ') : `<span class="ds-chip is-absent">${esc(g.endpoint)}</span>`).join(' <span>with</span> ')}
            <span class="ds-rule-by">${ICON.lock} set by your administrator — cannot be loosened here</span>
            ${r.reason ? `<div class="ds-reason">${esc(r.reason)}</div>` : ''}</div>`).join('');
    const agentRows = rv.agent.map(r => `
        <div class="ds-rule">Never combine ${r.members.map(chip).join(' <span>with</span> ')}
            <span class="ds-rule-by">this agent's rule
              <button type="button" class="ds-act ds-icon-btn" data-ds-act="remove-rule" data-ds-index="${r.index}" aria-label="Remove this rule">${ICON.x}</button></span>
            ${r.reason ? `<div class="ds-reason">${esc(r.reason)}</div>` : ''}</div>`).join('');
    let builder = '';
    if (st.builderOpen) {
        const rows = effectiveSources(st);
        const sel = new Set(st.builder || []);
        builder = `<div class="ds-rule" role="group" aria-label="New rule">Never combine
            ${rows.map(x => `<button type="button" class="ds-chip ds-chip-pick" data-ds-act="builder-toggle" data-ds-id="${esc(x.source_id)}" aria-pressed="${sel.has(x.source_id) ? 'true' : 'false'}">${esc(x.name)}</button>`).join(' ')}
            <input type="text" class="ds-input" data-ds-act="builder-reason" maxlength="200" placeholder="Why (optional) — shown when a combine is refused" value="${esc(st.builderReason || '')}">
            <button type="button" class="ds-act" data-ds-act="builder-save" ${sel.size >= 2 ? '' : 'disabled'}>Add rule</button>
            <button type="button" class="ds-act" data-ds-act="builder-cancel">Cancel</button>
            ${sel.size < 2 ? '<div class="ds-reason">Pick two or more sources.</div>' : ''}</div>`;
    }
    return `
      <div class="ds-panel" id="ds-combining">
        <div class="ds-panel-h"><span class="ds-panel-t">Combining results</span><span class="ds-panel-sub">decided by code before anything runs</span></div>
        ${state}${incomplete}
        <div style="margin-top:10px;">${adminRows}${agentRows}${builder}</div>
        ${!st.builderOpen ? `<div style="margin-top:8px;"><button type="button" class="ds-act" data-ds-act="builder-open" ${rv.canAdd ? '' : 'disabled'} ${rv.canAdd ? '' : `title="${esc(rv.addBlocked)}"`}>${ICON.plus} Add a rule</button>
            <span class="ds-panel-sub">Your rules only add restrictions; the administrator's always apply.</span></div>` : ''}
      </div>`;
}

export function mcpRowMetaHtml(st) {
    if (!st.usesMcp) return '<div class="ds-line">This agent class does not call MCP tools.</div>';
    const r = effectiveSources(st).find(x => x.kind === 'mcp');
    if (!r) return '';
    const cls = st.classified;
    return `${r.endpoint ? `<div class="ds-ep">${esc(r.endpoint)}</div>` : ''}
        <div class="ds-meta" style="justify-content:flex-start;margin-top:6px;">
          <span class="ds-pill">tools</span>
          ${r.missing ? '<span class="ds-pill is-warn">not on this instance</span>' : ''}
          ${cls === true ? '<span class="ds-pill">tools classified</span>' : cls === false ? '<span class="ds-pill is-warn">not classified yet — run the profile test</span>' : ''}
        </div>`;
}

// ── additional MCP servers: the Data sources rows ───────────────────────────────────────

function _resFor(st, id) { return (st.resources && st.resources[id]) || null; }

export function aliasHelpHtml(alias) {
    return validAlias(alias)
        ? `its tools read <b>${esc(alias)}${SEP}</b><b>name</b>`
        : 'a short name tells its tools apart from the other servers\'';
}

export function additionalRowHtml(st, a, problem) {
    const sv = ((st.view && st.view.mcp_servers) || []).find(x => String(x.id) === a.id);
    const missing = !!st.view && !sv;
    const res = _resFor(st, a.id);
    const ready = res && res.state === 'ready';
    const pills = [`<span class="ds-pill">${esc(selectionText(a.tools, ready ? res.tools : null, 'tools'))}</span>`];
    if (st.usesPrompts) pills.push(`<span class="ds-pill">${esc(selectionText(a.prompts, ready ? res.prompts : null, 'prompts'))}</span>`);
    if (missing) pills.push('<span class="ds-pill is-warn">not on this instance — refused at run time</span>');
    if (res && res.state === 'error') pills.push('<span class="ds-pill is-warn">tools could not be listed</span>');
    const inputId = `ds-alias-${esc(a.id)}`;
    return `
      <div class="ds-row${missing ? ' is-missing' : ''}" data-ds-source="mcp:${esc(a.id)}">
        <span class="ds-glyph">${ICON.mcp}</span>
        <div>
          <div class="ds-name">${esc(sv ? sv.name : a.id)}</div>${sv && sv.endpoint ? `<div class="ds-ep">${esc(sv.endpoint)}</div>` : ''}
          <div class="ds-alias-row">
            <label for="${inputId}">Short name</label>
            <input type="text" id="${inputId}" class="ds-input" data-ds-act="alias" data-ds-id="${esc(a.id)}" value="${esc(a.alias)}" maxlength="${ALIAS_MAX}" spellcheck="false" autocomplete="off" aria-invalid="${problem ? 'true' : 'false'}">
            <span class="ds-alias-help" data-ds-id="${esc(a.id)}" aria-live="polite">${aliasHelpHtml(a.alias)}</span>
          </div>
          ${problem ? `<div class="ds-alias-problem" role="alert">${esc(problem)}</div>` : ''}
        </div>
        <div class="ds-meta">${pills.join('')}
          <button type="button" class="ds-act" data-ds-act="pick-resources" data-ds-id="${esc(a.id)}">Pick tools</button>
          <button type="button" class="ds-act ds-icon-btn" data-ds-act="remove-mcp" data-ds-id="${esc(a.id)}" aria-label="Remove ${esc(sv ? sv.name : a.id)}">${ICON.x}</button>
        </div>
      </div>`;
}

/** The Tools card's additional servers + the add control. A single-server agent sees only the
 *  add control, and only when the user has another MCP server to add. */
export function additionalHtml(st) {
    if (!st.usesMcp || !st.mcpServerId) return '';
    const errs = additionalErrors(st);
    const rows = (st.additional || []).map(a => additionalRowHtml(st, a, errs[a.id] || '')).join('');
    const addable = addableServers(st);
    const canAdd = addable.some(x => !x.why);
    if (!rows && !canAdd && !st.addMcpOpen) return '';
    let html = rows;
    if (st.promotedNote) html += `<div class="ds-line">${esc(st.promotedNote)}</div>`;
    html += `<div class="ds-add-row"><button type="button" class="ds-act" data-ds-act="open-add-mcp" aria-expanded="${st.addMcpOpen ? 'true' : 'false'}" ${canAdd ? '' : 'disabled'}>${ICON.plus} Add another MCP server</button>
        ${rows ? '<span class="ds-panel-sub">The first server\'s tools keep their names; each additional server\'s read by its short name.</span>' : ''}</div>`;
    if (st.addMcpOpen) {
        html += `<div class="ds-picker" role="dialog" aria-label="Add an MCP server">${addable.map(x => `
            <button type="button" class="ds-pick" data-ds-act="add-mcp" data-ds-id="${esc(x.id)}" ${x.why ? 'disabled' : ''}>
              <span><span class="ds-name">${esc(x.name)}</span>${x.endpoint ? `<br><span class="ds-ep">${esc(x.endpoint)}</span>` : ''}</span>
              <span class="ds-pill">${esc(x.why || 'add')}</span></button>`).join('')}</div>`;
    }
    return html;
}

// ── additional MCP servers: the Resources groups ────────────────────────────────────────

function _resColHtml(a, res, kind) {
    const sel = a[kind];
    const names = res && res.state === 'ready' ? res[kind] : null;
    const title = kind === 'tools' ? 'Tools' : 'Prompts';
    let list;
    if (!res || res.state === 'loading') list = '<div class="ds-line" style="margin:0;">Loading…</div>';
    else if (res.state === 'error') list = `<div class="ds-line" style="margin:0;">Could not be listed — ${esc(res.error || 'the server did not answer')}. Your stored choice is kept.</div>`;
    else if (!names.length) list = `<div class="ds-line" style="margin:0;">No ${kind} on this server.</div>`;
    else list = names.map(n => `
        <label class="ind-toggle ind-toggle--sm ds-res-item"><input type="checkbox" data-ds-act="res-pick" data-ds-id="${esc(a.id)}" data-ds-kind="${kind}" value="${esc(n)}" ${sel.includes('*') || sel.includes(n) ? 'checked' : ''}><span class="ind-track"></span><span>${esc(n)}</span></label>`).join('');
    const bulk = names && names.length
        ? `<button type="button" class="ds-act" data-ds-act="res-all" data-ds-id="${esc(a.id)}" data-ds-kind="${kind}">All</button><button type="button" class="ds-act" data-ds-act="res-none" data-ds-id="${esc(a.id)}" data-ds-kind="${kind}">None</button>` : '';
    return `<div><div class="ds-res-sub">${title} <span class="ds-res-count">${esc(selectionText(sel, names, kind))}</span>${bulk}</div><div class="ds-res-list">${list}</div></div>`;
}

export function additionalResourcesHtml(st) {
    if (!st.usesMcp || !st.mcpServerId || !(st.additional || []).length) return '';
    return st.additional.map(a => {
        const sv = ((st.view && st.view.mcp_servers) || []).find(x => String(x.id) === a.id);
        const res = _resFor(st, a.id);
        const name = sv ? sv.name : a.id;
        return `
      <section class="ds-res-group" id="ds-res-${esc(a.id)}" aria-label="Tools and prompts of ${esc(name)}">
        <div class="ds-res-h"><span class="ds-glyph">${ICON.mcp}</span>
          <div><div class="ds-name">${esc(name)}</div><div class="ds-alias-help">${aliasHelpHtml(a.alias)}</div></div>
          <span class="ds-pill">${esc(a.alias || 'no short name')}</span></div>
        <div class="ds-res-cols${st.usesPrompts ? '' : ' is-one'}">${_resColHtml(a, res, 'tools')}${st.usesPrompts ? _resColHtml(a, res, 'prompts') : ''}</div>
      </section>`;
    }).join('');
}

/** The line above the primary's own lists — only when there is more than one server, so a
 *  single-server agent's Resources card is unchanged. */
export function primaryResourcesLabelHtml(st) {
    if (!st.usesMcp || !st.mcpServerId || !(st.additional || []).length) return '';
    const sv = ((st.view && st.view.mcp_servers) || []).find(x => String(x.id) === String(st.mcpServerId));
    return `<div class="ds-res-primary"><span class="ds-glyph">${ICON.mcp}</span><span class="ds-name">${esc(sv ? sv.name : st.mcpServerId)}</span><span>its tools keep their names</span></div>`;
}

// ── the controller (one per modal open) ─────────────────────────────────────────────────

let _st = null;

export function current() { return _st; }

/** The state for THIS profile's open editor, or null — a save never reads the state of an
 *  earlier modal open (another profile, or a new-profile form). */
export function stateFor(profile) {
    if (!_st) return null;
    const a = _st.profile, b = profile;
    if (a === b) return _st;
    return (a && b && a.id && a.id === b.id) ? _st : null;
}

/**
 * Open the section for a profile. `ctx`: {modal, profile, fetchView?, fetchTrust?, testConnection?}.
 * The fetchers are injectable (the gate drives the controller without a network).
 */
export function init(ctx) {
    const { modal, profile } = ctx;
    const host = modal && modal.querySelector('#profile-data-sources-body');
    if (!host) return null;
    injectCss(modal.ownerDocument || document);
    const nav = ((profile && profile.componentConfig) || {}).database_navigator || {};
    _st = {
        modal, host, profile,
        // state starts EQUAL to the stored values, so it is safe to save from the first moment;
        // edits made before the connections load are kept (never an empty-UI overwrite)
        view: null, rendered: true,
        usesMcp: true, mcpServerId: '',
        navOn: null,
        query: normQuery(nav.query), sourceId: nav.sourceId ? String(nav.sourceId) : '',
        rules: normRules(profile && profile.combineIsolation),
        classified: (profile && profile.classification_results) ? !!profile.classification_results.last_classified : null,
        tests: {}, pickerOpen: false, pickerFilter: '', builderOpen: false, builder: [], builderReason: '',
        trustNote: false,
        additional: normAdditional(profile && profile.additionalMcpServers),
        resources: {}, addMcpOpen: false, promotedNote: '', usesPrompts: true,
        resHost: modal.querySelector('#profile-modal-additional-mcp'),
        resLabelHost: modal.querySelector('#profile-modal-primary-mcp-label'),
        fetchView: ctx.fetchView || defaultFetchView, testConnection: ctx.testConnection || defaultTest,
        fetchResources: ctx.fetchResources || defaultFetchResources,
    };
    syncFromModal();
    wire();
    render();
    // an additional server's catalogue is fetched only when the agent has one — a
    // single-server agent pays no call
    for (const a of _st.additional) loadResources(a.id);
    Promise.resolve().then(() => _st && _st.fetchView()).then(v => {
        if (!_st || !v) return;
        _st.view = v; render();
    }).catch(() => { if (_st) { _st.viewError = true; render(); } });
    const ft = ctx.fetchTrust || defaultFetchTrust;
    if (profile && profile.id) {
        Promise.resolve().then(() => ft(profile.id)).then(t => {
            if (_st && t && t.state && t.state !== 'unsigned') { _st.trustNote = true; render(); }
        }).catch(() => {});
    }
    return _st;
}

/** Read the live controls that stay elsewhere: the moved MCP select (and whether the editor's
 *  surface contract shows it for this class) and the Navigator's on/off toggle. */
export function syncFromModal() {
    if (!_st) return;
    const m = _st.modal;
    const field = m.querySelector('#profile-field-mcp-server');
    const sel = m.querySelector('#profile-modal-mcp-server');
    _st.usesMcp = !!field && field.style.display !== 'none' && !field.classList.contains('hidden');
    _st.mcpServerId = sel ? String(sel.value || '') : '';
    const ps = m.querySelector('#profile-modal-prompts-section');
    _st.usesPrompts = !ps || (ps.style.display !== 'none' && !ps.classList.contains('hidden'));
    // the primary is now one of the additional servers: it cannot be both — it leaves the list
    const promoted = (_st.additional || []).find(a => a.id === _st.mcpServerId);
    if (promoted) {
        _st.additional = _st.additional.filter(a => a !== promoted);
        _st.promotedNote = `The first server is now the one that was “${promoted.alias}” — it was removed from the additional servers, and its tools keep their names.`;
    }
    const nt = m.querySelector('.comp-toggle[data-component-id="database_navigator"]');
    if (nt) _st.navOn = !!nt.checked && !nt.disabled;
    else if (_st.navOn === null) {
        const cfg = ((_st.profile && _st.profile.componentConfig) || {}).database_navigator;
        _st.navOn = cfg && typeof cfg.enabled === 'boolean' ? cfg.enabled : null;
    }
}

export function render() {
    if (!_st) return;
    const { host } = _st;
    const q = (sel) => host.querySelector(sel);
    const sum = q('#ds-summary'); if (sum) sum.innerHTML = summaryHtml(_st);
    const meta = q('#ds-mcp-meta'); if (meta) meta.innerHTML = _st.usesMcp ? mcpRowMetaHtml(_st) : '';
    const add = q('#ds-mcp-additional'); if (add) add.innerHTML = additionalHtml(_st);
    const sub = q('#ds-tools-sub');
    if (sub) sub.textContent = (_st.additional || []).length && _st.usesMcp
        ? `the MCP servers the agent calls — ${_st.additional.length + 1}` : 'the MCP server the agent calls';
    if (_st.resHost) _st.resHost.innerHTML = additionalResourcesHtml(_st);
    if (_st.resLabelHost) _st.resLabelHost.innerHTML = primaryResourcesLabelHtml(_st);
    // the editor's surface contract hides the MCP row for a class that calls no MCP tools —
    // the card then SAYS so rather than standing empty
    const absent = q('#ds-mcp-absent'); if (absent) absent.innerHTML = _st.usesMcp ? '' : mcpRowMetaHtml(_st);
    const dbs = q('#ds-databases');
    if (dbs) dbs.innerHTML = _st.navOn === null ? '<div class="ds-line">Loading the Database Navigator state…</div>' : databasesHtml(_st);
    const comb = q('#ds-combining-host'); if (comb) comb.innerHTML = combiningHtml(_st);
    const note = q('#ds-trust-note');
    if (note) note.innerHTML = _st.trustNote
        ? '<div class="ds-note">These sources are part of this agent\'s attested deployment. After saving a change, re-attest the deployment from the agent\'s trust badge.</div>' : '';
    if (_st.viewError && dbs) dbs.insertAdjacentHTML('afterbegin', '<div class="ds-line">Your connections could not be loaded — your stored choices are kept.</div>');
}

function wire() {
    const { host, modal } = _st;
    if (host.dataset.dsWired === '1') return;
    host.dataset.dsWired = '1';
    host.addEventListener('click', onClick);
    host.addEventListener('change', onChange);
    host.addEventListener('input', onInput);
    if (_st.resHost && _st.resHost.dataset.dsWired !== '1') {
        _st.resHost.dataset.dsWired = '1';
        _st.resHost.addEventListener('click', onClick);
        _st.resHost.addEventListener('change', onChange);
    }
    // the moved MCP select and the Navigator toggle (rendered async on the Capabilities section)
    modal.addEventListener('change', (e) => {
        const t = e.target;
        if (!_st || !t) return;
        if ((t.id || (t.getAttribute && t.getAttribute('id'))) === 'profile-modal-mcp-server' || (t.matches && t.matches('.comp-toggle[data-component-id="database_navigator"]'))) {
            syncFromModal(); render();
        }
    });
}

async function onClick(e) {
    const b = e.target && e.target.closest && e.target.closest('[data-ds-act]');
    if (!b || !_st) return;
    const act = b.dataset.dsAct, id = b.dataset.dsId;
    const st = _st;
    if (act === 'open-picker') { st.pickerOpen = !st.pickerOpen; st.pickerFilter = ''; }
    else if (act === 'add' && id && !(b.disabled || (b.hasAttribute && b.hasAttribute('disabled')))) { if (!st.query.sources.includes(id)) st.query.sources.push(id); st.pickerOpen = false; }
    else if (act === 'remove' && id) { st.query.sources = st.query.sources.filter(x => x !== id); if (st.sourceId === id) st.sourceId = ''; }
    else if (act === 'make-default' && id) { st.sourceId = id; }
    else if (act === 'test' && id) {
        st.tests[id] = { ok: false, text: 'Testing…' }; render();
        const res = await st.testConnection(id).catch(err => ({ ok: false, text: String(err && err.message || err) }));
        if (_st !== st) return;
        st.tests[id] = res;
    }
    else if (act === 'remove-rule') { st.rules.splice(parseInt(b.dataset.dsIndex, 10), 1); }
    else if (act === 'builder-open') { st.builderOpen = true; st.builder = []; st.builderReason = ''; }
    else if (act === 'builder-cancel') { st.builderOpen = false; }
    else if (act === 'builder-toggle' && id) { st.builder = st.builder.includes(id) ? st.builder.filter(x => x !== id) : [...st.builder, id]; }
    else if (act === 'builder-save') {
        const srcs = [...new Set(st.builder)].sort();
        if (srcs.length >= 2 && !st.rules.some(r => [...r.sources].sort().join('|') === srcs.join('|'))) {
            st.rules.push({ sources: srcs, ...(st.builderReason.trim() ? { reason: st.builderReason.trim().slice(0, 200) } : {}) });
        }
        st.builderOpen = false;
    }
    else if (act === 'open-capabilities') {
        const nav = st.modal.querySelector('.profile-nav-item[data-profile-section="capabilities"]');
        if (nav) nav.click();
        return;
    }
    else if (act === 'open-add-mcp') { st.addMcpOpen = !st.addMcpOpen; st.promotedNote = ''; }
    else if (act === 'add-mcp' && id && !(b.disabled || (b.hasAttribute && b.hasAttribute('disabled')))) {
        if (!st.additional.some(a => a.id === id) && id !== String(st.mcpServerId)) {
            const sv = ((st.view && st.view.mcp_servers) || []).find(x => String(x.id) === id);
            const prim = ((st.view && st.view.mcp_servers) || []).find(x => String(x.id) === String(st.mcpServerId));
            const alias = suggestAlias(sv ? sv.name : id, st.additional.map(a => a.alias), prim ? prim.name : '');
            st.additional.push({ id, alias, tools: ['*'], prompts: ['*'] });
            loadResources(id);
        }
        st.addMcpOpen = false;
    }
    else if (act === 'remove-mcp' && id) { st.additional = st.additional.filter(a => a.id !== id); delete st.resources[id]; }
    else if (act === 'pick-resources' && id) { revealResources(id); return; }
    else if ((act === 'res-all' || act === 'res-none') && id) {
        const a = st.additional.find(x => x.id === id), res = _resFor(st, id), kind = b.dataset.dsKind;
        if (!a || !res || res.state !== 'ready' || (kind !== 'tools' && kind !== 'prompts')) return;
        a[kind] = act === 'res-all' ? (res[kind].length ? ['*'] : []) : [];
    }
    else if (act === 'manage-connections') { if (typeof window !== 'undefined' && window.openPlatformConnector) window.openPlatformConnector('uderia-database'); return; }
    else return;
    render();
}

function onChange(e) {
    const t = e.target; if (!t || !_st) return;
    const act = t.dataset && t.dataset.dsAct;
    if (act === 'toggle-query') _st.query.enabled = !!t.checked;
    else if (act === 'default-source') _st.sourceId = String(t.value || '');
    else if (act === 'alias') { const a = _st.additional.find(x => x.id === t.dataset.dsId); if (!a) return; a.alias = String(t.value || '').trim(); }
    else if (act === 'res-pick') {
        const a = _st.additional.find(x => x.id === t.dataset.dsId), res = _resFor(_st, t.dataset.dsId), kind = t.dataset.dsKind;
        if (!a || !res || res.state !== 'ready' || (kind !== 'tools' && kind !== 'prompts')) return;
        a[kind] = toggleSelection(a[kind], t.value, !!t.checked, res[kind]);
    }
    else return;
    render();
}

function onInput(e) {
    const t = e.target; if (!t || !_st) return;
    const act = t.dataset && t.dataset.dsAct;
    if (act === 'picker-filter') {
        _st.pickerFilter = t.value;
        const list = _st.host.querySelector('.ds-pick-list');       // the list only — focus stays in the search box
        if (list) list.innerHTML = pickerListHtml(_st);
    } else if (act === 'builder-reason') { _st.builderReason = t.value; }
    else if (act === 'alias') {
        // live, without re-rendering the row (the caret stays): the help line and every row's
        // problem are refreshed; the full render runs on change
        const a = _st.additional.find(x => x.id === t.dataset.dsId); if (!a) return;
        a.alias = String(t.value || '').trim();
        const errs = additionalErrors(_st);
        for (const h of _st.host.querySelectorAll('.ds-alias-help')) {
            if (h.dataset && h.dataset.dsId === a.id) h.innerHTML = aliasHelpHtml(a.alias);
        }
        for (const inp of _st.host.querySelectorAll('input[data-ds-act="alias"]')) {
            inp.setAttribute('aria-invalid', errs[inp.dataset.dsId] ? 'true' : 'false');
        }
    }
}

/** Load one additional server's tools and prompts (injectable fetcher). */
export function loadResources(id) {
    const st = _st; if (!st) return Promise.resolve();
    st.resources[id] = { state: 'loading', tools: [], prompts: [] };
    return Promise.resolve().then(() => st.fetchResources(id)).then(r => {
        if (_st !== st || !st.resources[id]) return;
        st.resources[id] = { state: 'ready', tools: (r && r.tools) || [], prompts: (r && r.prompts) || [] };
        render();
    }).catch(err => {
        if (_st !== st || !st.resources[id]) return;
        st.resources[id] = { state: 'error', tools: [], prompts: [], error: String((err && err.message) || err) };
        render();
    });
}

/** "Pick tools": open the Resources section, unfold the MCP card, bring the server's group in view. */
function revealResources(id) {
    const m = _st.modal;
    const nav = m.querySelector('.profile-nav-item[data-profile-section="resources"]');
    if (nav) nav.click();
    const cardEl = m.querySelector('#profile-content-mcp-resources');
    const card = cardEl && cardEl.querySelector('details');
    if (card) card.open = true;
    const grp = _st.resHost ? Array.from(_st.resHost.querySelectorAll('.ds-res-group')).find(g => g.id === `ds-res-${id}`) : null;
    if (grp && grp.scrollIntoView) grp.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ── default fetchers (browser) ──────────────────────────────────────────────────────────

function _auth() { const t = localStorage.getItem('tda_auth_token'); return t ? { 'Authorization': `Bearer ${t}` } : {}; }

async function defaultFetchView() {
    const r = await fetch('/api/v1/data-sources', { headers: _auth() });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
}
async function defaultFetchTrust(id) {
    const r = await fetch(`/api/v1/trust/agent/${encodeURIComponent(id)}`, { headers: _auth() });
    return r.ok ? r.json() : null;
}
async function defaultFetchResources() {
    throw new Error('the server\'s tools could not be listed here');
}
async function defaultTest(sourceId) {
    const id = String(sourceId).replace(/^conn:/, '');
    const r = await fetch(`/api/v1/database-connections/${encodeURIComponent(id)}/test`, { method: 'POST', headers: _auth() });
    const d = await r.json().catch(() => ({}));
    const ok = r.ok && (d.status === 'success' || d.success === true);
    return { ok, text: ok ? (d.message || 'Connected.') : `Could not connect — ${d.message || `HTTP ${r.status}`}` };
}
