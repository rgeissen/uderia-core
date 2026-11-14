/**
 * Library access — the anchor-grant dialog (PROP-005 §6).
 *
 * ONE dialog, two entry points: the profile editor's Skills row ("what does THIS
 * agent get?") and the Skill Library component's Libraries tab ("who gets THIS
 * library, and how much?"). The dialog owns the editing; the caller owns the
 * persistence (`onSave(anchors)`), so neither entry point re-implements the
 * semantics — and they cannot drift.
 *
 * The grant is a governance boundary enforced fail-closed at the tool layer, so
 * the dialog is written around the DECISION, not the data:
 *   · it opens with the boundary stated in plain language, not a count;
 *   · it groups by the library's own structure, because the real decision is
 *     "the design half, not the Teradata half" — not 77 individual checkboxes;
 *   · it shows the consequence of the pending change before you commit it;
 *   · nothing is written until you save, and the primary action is disabled
 *     while there is nothing to save.
 *
 * Semantics preserved exactly (gate-asserted in test_progressive_skills.py):
 *   · ALL  -> no `anchors` key. An exhaustive list would freeze the grant and
 *     silently exclude sections a later skill update adds.
 *   · DENY-ALL -> `[]`, distinguishable from absence: a real choice.
 *   · GLOBS are preserved verbatim, never expanded — they grant forward.
 */

const SG_ICONS = {
    book: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="sg-i"><path stroke-linecap="round" stroke-linejoin="round" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"/></svg>',
    x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="sg-i"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>',
    chev: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" class="sg-i sg-chev"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7"/></svg>',
    search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="sg-i"><path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-4.35-4.35M17 11a6 6 0 11-12 0 6 6 0 0112 0z"/></svg>',
    globe: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="sg-i"><circle cx="12" cy="12" r="9"/><path stroke-linecap="round" d="M3 12h18M12 3c2.5 2.7 2.5 15.3 0 18M12 3c-2.5 2.7-2.5 15.3 0 18"/></svg>',
};

function _sgCss() {
    if (document.getElementById('sg-css')) return;
    const st = document.createElement('style');
    st.id = 'sg-css';
    st.textContent = `
.sg-overlay{position:fixed;inset:0;background:rgba(2,6,23,.66);backdrop-filter:blur(3px);z-index:1200;display:flex;align-items:center;justify-content:center;padding:1.5rem}
.sg-card{width:min(720px,96vw);max-height:88vh;display:flex;flex-direction:column;border-radius:14px;
  background:var(--card-bg,#0f172a);border:1px solid var(--border-primary,#334155);box-shadow:0 24px 60px rgba(0,0,0,.5)}
.sg-i{width:1rem;height:1rem;flex-shrink:0}
.sg-hd{display:flex;align-items:center;gap:.6rem;padding:1rem 1.15rem;border-bottom:1px solid var(--border-secondary,rgba(148,163,184,.16))}
.sg-hd .sg-ic{color:#38bdf8}
.sg-title{font-size:.95rem;font-weight:600;color:var(--text-primary,#e5e7eb);flex:1;min-width:0}
.sg-title small{display:block;font-weight:400;font-size:.75rem;color:var(--text-muted,#94a3b8);margin-top:.1rem}
.sg-x{background:none;border:0;color:var(--text-muted,#94a3b8);cursor:pointer;padding:.25rem;border-radius:6px}
.sg-x:hover{background:var(--hover-bg,rgba(148,163,184,.12));color:var(--text-primary,#e5e7eb)}
.sg-body{padding:1rem 1.15rem;overflow-y:auto;flex:1;min-height:0}
.sg-hero{display:flex;gap:.7rem;align-items:flex-start;border:1px solid;border-radius:10px;padding:.7rem .85rem;margin-bottom:.9rem}
.sg-hero .h-t{font-size:.85rem;font-weight:600}
.sg-hero .h-d{font-size:.78rem;color:var(--text-primary,#e5e7eb);margin-top:.15rem;line-height:1.45}
.sg-modes{display:grid;grid-template-columns:1fr 1fr;gap:.6rem;margin-bottom:.9rem}
.sg-mode{text-align:left;border-radius:10px;padding:.65rem .75rem;cursor:pointer;
  background:var(--input-bg,rgba(15,23,42,.6));border:1px solid var(--border-primary,#334155);color:var(--text-primary,#e5e7eb)}
.sg-mode:hover{border-color:#38bdf8}
.sg-mode[aria-pressed="true"]{border-color:#38bdf8;background:rgba(14,165,233,.10)}
.sg-mode .m-t{font-size:.82rem;font-weight:600;display:flex;align-items:center;gap:.4rem}
.sg-mode .m-d{font-size:.72rem;color:var(--text-muted,#94a3b8);margin-top:.2rem;line-height:1.4}
.sg-mode:focus-visible,.sg-x:focus-visible,.sg-grp-h:focus-visible,.sg-btn:focus-visible{outline:2px solid #38bdf8;outline-offset:2px}
.sg-tools{display:flex;gap:.5rem;align-items:center;margin-bottom:.6rem}
.sg-search{flex:1;display:flex;align-items:center;gap:.45rem;border-radius:8px;padding:.4rem .6rem;
  background:var(--input-bg,rgba(15,23,42,.6));border:1px solid var(--border-primary,#334155);color:var(--text-muted,#94a3b8)}
.sg-search input{flex:1;background:none;border:0;outline:none;color:var(--text-primary,#e5e7eb);font-size:.8rem}
.sg-mini{font-size:.72rem;padding:.3rem .55rem;border-radius:6px;cursor:pointer;
  background:var(--input-bg,rgba(15,23,42,.6));border:1px solid var(--border-primary,#334155);color:var(--text-muted,#94a3b8)}
.sg-mini:hover{color:var(--text-primary,#e5e7eb);border-color:#64748b}
.sg-globs{display:flex;flex-wrap:wrap;gap:.35rem;margin-bottom:.7rem}
.sg-glob{display:inline-flex;align-items:center;gap:.3rem;font-family:ui-monospace,monospace;font-size:.7rem;padding:.2rem .45rem;border-radius:6px;
  background:rgba(168,85,247,.12);color:#c084fc;border:1px solid rgba(168,85,247,.28)}
.sg-glob button{background:none;border:0;color:inherit;cursor:pointer;font-size:.85rem;line-height:1}
.sg-grp{border:1px solid var(--border-secondary,rgba(148,163,184,.16));border-radius:9px;margin-bottom:.45rem;overflow:hidden}
.sg-grp-h{width:100%;display:flex;align-items:center;gap:.55rem;padding:.5rem .65rem;cursor:pointer;
  background:var(--hover-bg,rgba(148,163,184,.05));border:0;color:var(--text-primary,#e5e7eb);text-align:left}
.sg-grp-h:hover{background:var(--hover-bg-strong,rgba(148,163,184,.12))}
.sg-grp-h .g-n{font-family:ui-monospace,monospace;font-size:.78rem;flex:1;min-width:0}
.sg-grp-h .g-c{font-size:.7rem;color:var(--text-muted,#94a3b8)}
.sg-grp-h .sg-chev{transition:transform .15s}
.sg-grp[data-open="1"] .sg-chev{transform:rotate(90deg)}
.sg-grp-b{display:none;padding:.3rem .65rem .55rem 2.1rem}
.sg-grp[data-open="1"] .sg-grp-b{display:block}
.sg-row{display:flex;align-items:flex-start;gap:.5rem;padding:.18rem 0;font-size:.76rem;color:var(--text-primary,#e5e7eb);cursor:pointer}
.sg-row .r-a{font-family:ui-monospace,monospace}
.sg-row .r-t{color:var(--text-muted,#94a3b8)}
.sg-row.sg-byglob .r-a{color:#c084fc}
.sg-ft{display:flex;align-items:center;gap:.75rem;padding:.8rem 1.15rem;border-top:1px solid var(--border-secondary,rgba(148,163,184,.16))}
.sg-conseq{flex:1;min-width:0;font-size:.75rem;color:var(--text-muted,#94a3b8);line-height:1.4}
.sg-btn{font-size:.8rem;font-weight:600;padding:.45rem .9rem;border-radius:8px;cursor:pointer;
  background:var(--input-bg,rgba(15,23,42,.6));border:1px solid var(--border-primary,#334155);color:var(--text-primary,#e5e7eb)}
.sg-btn.prim{background:#0ea5e9;border-color:#0ea5e9;color:#04202f}
.sg-btn.prim:disabled{opacity:.45;cursor:not-allowed}
.sg-btn:not(.prim):hover{border-color:#64748b}
@media (prefers-reduced-motion:reduce){.sg-grp-h .sg-chev{transition:none}}
`;
    document.head.appendChild(st);
}

const _sgEsc = (s) => String(s ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/** A glob grants forward — match it, never expand it into today's anchors. */
function _sgGlobRe(g) {
    return new RegExp('^' + g.replace(/[.+?^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*') + '$');
}

/** Group anchors by their first path segment — the library's own structure, which
 *  is the unit real decisions are made in ("the design half", "not Teradata"). */
function _sgGroup(anchors) {
    const groups = new Map();
    for (const a of anchors) {
        const g = a.anchor.includes('/') ? a.anchor.split('/')[0] : 'general';
        if (!groups.has(g)) groups.set(g, []);
        groups.get(g).push(a);
    }
    return [...groups.entries()].sort((x, y) => y[1].length - x[1].length);
}

/**
 * @param {object} o
 * @param {string} o.skillId · o.skillName · {number} o.totalSections
 * @param {string} o.profileTag — whose grant this is (display only)
 * @param {string[]|null} o.anchors — current grant; null/absent = all
 * @param {function(string[]|null): Promise<void>|void} o.onSave — receives the new
 *        grant: null = all sections (no key), [] = deny all, else the list.
 */
export async function openSkillGrantDialog(o) {
    _sgCss();
    const total = o.totalSections || 0;
    let grant = Array.isArray(o.anchors) ? [...o.anchors] : null;   // null = all
    const initial = JSON.stringify(grant);
    let index = null;      // [{anchor,title,summary}] — lazily fetched
    let query = '';

    const overlay = document.createElement('div');
    overlay.className = 'sg-overlay';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', `Library access for ${o.skillName || o.skillId}`);
    overlay.innerHTML = `<div class="sg-card" id="sg-card"></div>`;
    document.body.appendChild(overlay);

    const close = () => { overlay.remove(); document.removeEventListener('keydown', onKey); };
    function onKey(e) { if (e.key === 'Escape') close(); }
    document.addEventListener('keydown', onKey);
    overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
    const card = overlay.querySelector('#sg-card');

    const globs = () => (grant || []).filter(a => a.includes('*'));
    const exact = () => new Set((grant || []).filter(a => !a.includes('*')));
    const isGranted = (anchor) => grant === null
        || exact().has(anchor)
        || globs().some(g => _sgGlobRe(g).test(anchor));
    const grantedCount = () => grant === null ? total
        : (index ? index.filter(a => isGranted(a.anchor)).length : exact().size);

    function setExact(anchor, on) {
        const g = globs();
        const ex = exact();
        if (on) ex.add(anchor); else ex.delete(anchor);
        grant = [...g, ...[...ex].sort()];
        render();
    }

    function render() {
        const restricted = grant !== null;
        const n = grantedCount();
        const denied = total - n;
        const dirty = JSON.stringify(grant) !== initial;

        // The hero states the BOUNDARY in plain language — the count is the detail.
        let heroColor, heroTitle, heroDesc;
        if (!restricted) {
            heroColor = '#38bdf8';
            heroTitle = 'Full library access';
            heroDesc = `This agent may fetch any of the ${total} sections — including sections a future update to this skill adds.`;
        } else if (n === 0) {
            heroColor = '#f87171';
            heroTitle = 'No library access';
            heroDesc = 'Every fetch is refused. The agent keeps the skill’s always-resident root, but can open none of its sections.';
        } else {
            heroColor = '#fbbf24';
            const groupsDenied = index
                ? _sgGroup(index).filter(([, list]) => list.every(a => !isGranted(a.anchor))).map(([g]) => g)
                : [];
            heroTitle = `Restricted to ${n} of ${total} sections`;
            heroDesc = `${denied} section${denied === 1 ? '' : 's'} ${denied === 1 ? 'is' : 'are'} refused at the tool layer`
                + (groupsDenied.length ? ` — including everything under ${groupsDenied.map(g => `<span class="r-a">${_sgEsc(g)}/</span>`).join(', ')}` : '')
                + '. The refusal names what the agent may read instead, so it can self-correct.';
        }

        const globChips = globs().length ? `
            <div class="sg-globs">${globs().map(g => {
                const m = index ? index.filter(a => _sgGlobRe(g).test(a.anchor)).length : null;
                return `<span class="sg-glob" title="Pattern grant — matches current and future sections">
                    ${SG_ICONS.globe}${_sgEsc(g)}${m !== null ? ` · ${m}` : ''}
                    <button type="button" data-rmglob="${_sgEsc(g)}" aria-label="Remove pattern ${_sgEsc(g)}">&times;</button></span>`;
            }).join('')}</div>` : '';

        let listHtml = '';
        if (restricted) {
            if (!index) {
                listHtml = `<p class="sg-conseq">Loading sections…</p>`;
            } else {
                const q = query.trim().toLowerCase();
                const groups = _sgGroup(index).map(([g, list]) => [g, q
                    ? list.filter(a => a.anchor.toLowerCase().includes(q) || (a.title || '').toLowerCase().includes(q))
                    : list]).filter(([, list]) => list.length);
                listHtml = groups.length ? groups.map(([g, list]) => {
                    const on = list.filter(a => isGranted(a.anchor)).length;
                    const state = on === 0 ? 'none' : (on === list.length ? 'all' : 'some');
                    const open = q ? '1' : (state === 'some' ? '1' : '0');
                    return `
                    <div class="sg-grp" data-open="${open}" data-group="${_sgEsc(g)}">
                        <button type="button" class="sg-grp-h" data-toggle-grp="${_sgEsc(g)}"
                                aria-expanded="${open === '1'}">
                            ${SG_ICONS.chev}
                            <input type="checkbox" data-grp-all="${_sgEsc(g)}" ${state === 'all' ? 'checked' : ''}
                                   aria-label="Grant all of ${g === 'general' ? 'the top level' : _sgEsc(g)}"
                                   onclick="event.stopPropagation()">
                            <span class="g-n">${g === 'general' ? 'top level' : _sgEsc(g) + '/'}</span>
                            <span class="g-c">${on} of ${list.length}</span>
                        </button>
                        <div class="sg-grp-b">
                            ${list.map(a => {
                                const byGlob = grant !== null && !exact().has(a.anchor) && isGranted(a.anchor);
                                return `<label class="sg-row ${byGlob ? 'sg-byglob' : ''}"${byGlob ? ' title="Granted by a pattern above"' : ''}>
                                    <input type="checkbox" data-anchor="${_sgEsc(a.anchor)}" ${isGranted(a.anchor) ? 'checked' : ''} ${byGlob ? 'disabled' : ''}>
                                    <span><span class="r-a">${_sgEsc(a.anchor)}</span>${a.title && a.title !== a.anchor ? ` <span class="r-t">— ${_sgEsc(a.title)}</span>` : ''}</span>
                                </label>`;
                            }).join('')}
                        </div>
                    </div>`;
                }).join('') : `<p class="sg-conseq">No section matches “${_sgEsc(query)}”.</p>`;
            }
        }

        card.innerHTML = `
            <div class="sg-hd">
                <span class="sg-ic">${SG_ICONS.book}</span>
                <span class="sg-title">Library access
                    <small>${_sgEsc(o.skillName || o.skillId)}${o.profileTag ? ` → @${_sgEsc(o.profileTag)}` : ''}</small></span>
                <button class="sg-x" id="sg-x" aria-label="Close">${SG_ICONS.x}</button>
            </div>
            <div class="sg-body">
                <div class="sg-hero" style="color:${heroColor};background:color-mix(in srgb,${heroColor} 9%,transparent);border-color:color-mix(in srgb,${heroColor} 38%,transparent)">
                    <span>${SG_ICONS.book}</span>
                    <div><div class="h-t">${heroTitle}</div><div class="h-d">${heroDesc}</div></div>
                </div>
                <div class="sg-modes">
                    <button type="button" class="sg-mode" data-mode="all" aria-pressed="${!restricted}">
                        <span class="m-t">All sections</span>
                        <span class="m-d">Recommended. Includes sections added by future updates.</span>
                    </button>
                    <button type="button" class="sg-mode" data-mode="some" aria-pressed="${restricted}">
                        <span class="m-t">Selected sections only</span>
                        <span class="m-d">Draw a boundary. Anything outside it is refused.</span>
                    </button>
                </div>
                ${restricted ? `
                <div class="sg-tools">
                    <label class="sg-search">${SG_ICONS.search}
                        <input type="text" id="sg-q" placeholder="Filter ${total} sections…" value="${_sgEsc(query)}" aria-label="Filter sections">
                    </label>
                    <button type="button" class="sg-mini" data-bulk="all">Select all shown</button>
                    <button type="button" class="sg-mini" data-bulk="none">Clear shown</button>
                </div>` : ''}
                ${globChips}
                ${listHtml}
            </div>
            <div class="sg-ft">
                <span class="sg-conseq">${dirty
                    ? `Pending: <strong style="color:var(--text-primary,#e5e7eb)">${n} of ${total}</strong> granted. Not saved yet.`
                    : `Currently <strong style="color:var(--text-primary,#e5e7eb)">${restricted ? `${n} of ${total}` : `all ${total}`}</strong> granted.`}</span>
                <button type="button" class="sg-btn" id="sg-cancel">Cancel</button>
                <button type="button" class="sg-btn prim" id="sg-save" ${dirty ? '' : 'disabled'}>Save library access</button>
            </div>`;

        wire();
    }

    function wire() {
        card.querySelector('#sg-x').onclick = close;
        card.querySelector('#sg-cancel').onclick = close;
        card.querySelector('#sg-save').onclick = async () => {
            const btn = card.querySelector('#sg-save');
            btn.disabled = true; btn.textContent = 'Saving…';
            try { await o.onSave(grant); close(); }
            catch (e) {
                btn.disabled = false; btn.textContent = 'Save library access';
                if (window.showAppBanner) window.showAppBanner(`Could not save library access: ${e}`, 'error');
            }
        };
        card.querySelectorAll('[data-mode]').forEach(b => b.onclick = async () => {
            if (b.dataset.mode === 'all') { grant = null; render(); return; }
            if (grant === null) grant = [];      // restricting starts from an explicit nothing
            await ensureIndex();
            render();
        });
        const q = card.querySelector('#sg-q');
        if (q) {
            q.oninput = () => { query = q.value; render();
                const nq = card.querySelector('#sg-q'); if (nq) { nq.focus(); nq.setSelectionRange(nq.value.length, nq.value.length); } };
        }
        card.querySelectorAll('[data-toggle-grp]').forEach(h => h.onclick = () => {
            const g = h.closest('.sg-grp');
            g.dataset.open = g.dataset.open === '1' ? '0' : '1';
            h.setAttribute('aria-expanded', g.dataset.open === '1');
        });
        card.querySelectorAll('[data-grp-all]').forEach(cb => cb.onchange = () => {
            const g = cb.dataset.grpAll;
            const list = index.filter(a => (a.anchor.includes('/') ? a.anchor.split('/')[0] : 'general') === g);
            const ex = exact();
            for (const a of list) { if (cb.checked) ex.add(a.anchor); else ex.delete(a.anchor); }
            grant = [...globs(), ...[...ex].sort()];
            render();
        });
        card.querySelectorAll('[data-anchor]').forEach(cb => cb.onchange = () => setExact(cb.dataset.anchor, cb.checked));
        card.querySelectorAll('[data-rmglob]').forEach(b => b.onclick = () => {
            grant = (grant || []).filter(a => a !== b.dataset.rmglob); render();
        });
        card.querySelectorAll('[data-bulk]').forEach(b => b.onclick = () => {
            const qq = query.trim().toLowerCase();
            const shown = (index || []).filter(a => !qq || a.anchor.toLowerCase().includes(qq) || (a.title || '').toLowerCase().includes(qq));
            const ex = exact();
            for (const a of shown) { if (b.dataset.bulk === 'all') ex.add(a.anchor); else ex.delete(a.anchor); }
            grant = [...globs(), ...[...ex].sort()];
            render();
        });
    }

    async function ensureIndex() {
        if (index) return;
        try {
            const res = await fetch(`/api/v1/skills/${encodeURIComponent(o.skillId)}/library-index`,
                { headers: { 'Authorization': `Bearer ${localStorage.getItem('tda_auth_token')}` } });
            index = res.ok ? ((await res.json()).anchors || []) : [];
        } catch { index = []; }
    }

    render();
    if (grant !== null) { await ensureIndex(); render(); }
}

if (typeof window !== 'undefined') window.openSkillGrantDialog = openSkillGrantDialog;
