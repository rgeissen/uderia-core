/**
 * Answer Capsule — consumer-facing "Why trust this answer?" affordance.
 *
 * Every trust surface in the platform is operator-facing (Live Status, roster,
 * admin cards); the person who most needs transparency is the one READING the
 * answer. This script injects a quiet toggle under each assistant message that
 * expands into a compact provenance capsule: the turn's Trust Journey glyphs,
 * the declared scope, the governed-vs-free-formed data lane, what the guards
 * caught (labels only — never values), the provenance seal — and an
 * "Export receipt" action that downloads the turn's verifiable record (the
 * Ed25519-signed provenance chain + trust stamps) as a portable JSON artifact.
 *
 * Pure rendering over persisted turn data (same source as the reload trace).
 * Plain script (MutationObserver pattern, like the dashboard pin button):
 * no module imports, guarded everywhere, removable without trace.
 */
(function () {
    'use strict';

    const CACHE = new Map(); // "sid:turn" -> turn details promise

    function fetchTurn(sid, turn) {
        const key = `${sid}:${turn}`;
        if (!CACHE.has(key)) {
            const token = localStorage.getItem('tda_auth_token');
            CACHE.set(key, fetch(`/api/session/${sid}/turn/${turn}/details`,
                { headers: token ? { 'Authorization': `Bearer ${token}` } : {} })
                .then((r) => (r.ok ? r.json() : null)).catch(() => null));
        }
        return CACHE.get(key);
    }

    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

    /** Does this capsule render the dial? One rule for the ring AND for the readings below
     *  it: where the dial renders, its stations carry the verdicts, so the capsule does not
     *  say them a second time. */
    function dialRenders(td) {
        return !!(window.AssuranceDial && window.AssuranceDial.enabled() && td && td.assurance);
    }

    function miniJourney(td) {
        // R-2 — the capsule's mini-ring and the Live Status strip were two renderings of
        // the same facts. The dial is the one instrument; the sections it renders carry
        // the linear glyphs' content one gesture deeper (ledger I12/I13), never dropped.
        if (dialRenders(td)) {
            return '<div class="ac-dial-host" data-ac-dial></div>';
        }
        if (!window.TrustJourney || !window.TrustJourney.deriveStages) return '';
        try {
            const stages = window.TrustJourney.deriveStages(td);
            const icon = window.TrustJourney.iconFor || (() => '');
            // The mini-circle glyph — this turn's journey in radial form, the
            // same universal trust mark as the roster; click → the Trust Circle.
            // R-5 — station semantics for the ring even beside the legacy strip: the
            // mini ring is an 8-station vocabulary everywhere else, and ten stage
            // states silently changed its segment count. Stages stay the fallback
            // for a payload without the assurance block.
            const _AD = window.AssuranceDial;
            const ringStates = (td && td.assurance && _AD && _AD.stations && _AD.ORDER)
                ? (() => { const st = _AD.stations(td.assurance);
                           return _AD.ORDER.map((k) => st[k].state); })()
                : stages.map((s) => s.state);
            const ring = (window.TrustCircle && window.TrustCircle.glyph)
                ? `<span class="tc-mini-host u-tip" role="button" tabindex="0"
                       data-tip="This turn's trust journey as the circle.\nClick to open the Trust Circle."
                       onclick="window.agentPerformance && window.agentPerformance.openCircle && window.agentPerformance.openCircle(${td && td.profile_tag ? `'${esc(td.profile_tag)}'` : ''})"
                       style="margin-right:2px;">${window.TrustCircle.glyph(ringStates, { size: 22, label: 'Open the Trust Circle' })}</span>`
                : '';
            // Same per-stage icons as the Live Status Trust Journey strip, so the
            // consumer capsule and the operator strip read identically.
            return `<div class="flex items-center flex-wrap mb-2" style="gap:6px;">` + ring + stages.map((s, i) => {
                const edge = i === 0 ? ' tj-tip-left' : (i === stages.length - 1 ? ' tj-tip-right' : '');
                return `<span class="tj-stage tj-${s.state}${edge}" data-stage="${esc(s.key)}" style="cursor:default;" data-tip="${esc(s.label + '\n' + s.tip)}">${icon(s.icon)}</span>`;
            }).join('') + `</div>`;
        } catch (_) { return ''; }
    }

    function factRow(label, value, cls) {
        if (!value) return '';
        return `<div class="flex items-start gap-2 py-0.5">
            <span class="text-[10px] uppercase tracking-wide flex-shrink-0" style="color: var(--text-muted); width: 86px;">${esc(label)}</span>
            <span class="text-[11px] ${cls || ''}" style="color: var(--text-primary);">${value}</span></div>`;
    }

    // HITL review state for this turn — read from the review queue (owner's own
    // content). Append-only release: the delivered history is never rewritten;
    // an approved original is surfaced HERE, clearly labelled as a human release.
    let _reviewsPromise = null;
    function fetchReviews(fresh) {
        if (fresh || !_reviewsPromise) {
            const token = localStorage.getItem('tda_auth_token');
            _reviewsPromise = fetch('/api/v1/assurance/reviews?status=all&include_content=true',
                { headers: token ? { 'Authorization': `Bearer ${token}` } : {} })
                .then((r) => (r.ok ? r.json() : null)).catch(() => null);
        }
        return _reviewsPromise;
    }

    // (Re)render the HITL review section for one loaded capsule body — used on
    // first load, on re-open (fresh fetch, so a decision made elsewhere shows),
    // and on the live review-decision event.
    function refreshReviewSection(body, sid, turn, fresh) {
        return fetchReviews(fresh).then((rv) => {
            const items = ((rv && rv.items) || []).filter((it) => it.session_id === sid
                && String(it.turn_id) === String(turn));
            let div = body.querySelector('.ac-review');
            if (!items.length) { if (div) div.remove(); return; }
            if (!div) {
                div = document.createElement('div');
                div.className = 'ac-review';
                body.appendChild(div);
            }
            div.innerHTML = items.map(reviewHtml).join('');
        }).catch(() => { /* capsule stands without review state */ });
    }

    function reviewHtml(item) {
        if (!item) return '';
        if (item.status === 'pending') {
            return `<div class="mt-2 pt-2" style="border-top: 1px dashed var(--border-secondary);">
                <span class="trust-chip tc-warn">awaiting human review</span>
                <span class="text-[10px]" style="color: var(--text-muted);"> — the original answer is parked in the Assurance Inbox (Agents view) for your disposition.</span></div>`;
        }
        if (item.status === 'retained') {
            // Plain-enforce recovery ledger: recoverable, dispositionable, but NOT owed
            // work — the wording must never imply a human review is outstanding, and it
            // must never fall through to "withholding upheld" (a decision nobody made).
            return `<div class="mt-2 pt-2" style="border-top: 1px dashed var(--border-secondary);">
                <div class="mb-1"><span class="trust-chip u-tip" data-tip="${esc('The gate withheld this answer under Enforce. The original was retained\nas a recoverable entry — no action is owed. You may read it here, and\napprove or uphold it from the Assurance Inbox if you wish.')}">withheld — original retained</span>
                <span class="text-[10px]" style="color: var(--text-muted);"> recoverable · no action owed</span></div>
                <details><summary class="text-[10px] cursor-pointer" style="color: var(--text-muted);">Read the retained original</summary>
                    <div class="text-[11px] mt-1 p-2 rounded whitespace-pre-wrap" style="color: var(--text-primary); background: var(--bg-overlay, rgba(127,127,127,0.06)); border: 1px solid var(--border-secondary); max-height: 200px; overflow-y: auto;">${esc(item.original_text || '(content unavailable)')}</div>
                </details></div>`;
        }
        if (item.status === 'approved') {
            const released = !!item.released_to_context;
            const chip = released ? 'released into the conversation' : 'released after human review (record only)';
            const tip = released
                ? 'The owner explicitly released this original INTO the conversation:\nsubsequent turns can see and discuss it (clearly labelled as a human decision).\nForward-only — this sealed turn itself was never rewritten.'
                : 'The owner has seen the withheld original and accepts it.\nRecord only — the conversation itself is unchanged;\nthe original is readable here.';
            return `<div class="mt-2 pt-2" style="border-top: 1px dashed var(--border-secondary);">
                <div class="mb-1"><span class="trust-chip tc-pass u-tip" data-tip="${esc(tip)}">${chip}</span>
                <span class="text-[10px]" style="color: var(--text-muted);"> ${item.decided_at ? new Date(item.decided_at).toLocaleString() : ''}${item.note ? ' · ' + esc(item.note) : ''}</span></div>
                <details><summary class="text-[10px] cursor-pointer" style="color: var(--text-muted);">Read the released original</summary>
                    <div class="text-[11px] mt-1 p-2 rounded whitespace-pre-wrap" style="color: var(--text-primary); background: var(--bg-overlay, rgba(127,127,127,0.06)); border: 1px solid var(--border-secondary); max-height: 200px; overflow-y: auto;">${esc(item.original_text || '(content unavailable)')}</div>
                </details></div>`;
        }
        return `<div class="mt-2 pt-2" style="border-top: 1px dashed var(--border-secondary);">
            <span class="trust-chip">withholding upheld by the owner</span>${item.note ? `<span class="text-[10px]" style="color: var(--text-muted);"> — ${esc(item.note)}</span>` : ''}</div>`;
    }

    // The sealed chain, drawn AS a chain — one seal per step (hash stub + type),
    // linked in order, with an inline verify action. Verification failure renders
    // an honest broken-chain state, never a silent pass.
    // S7 — the drafts: the first draft with its finding highlighted, each revision beside it.
    // A tab list (role=tablist / tab / tabpanel, arrow keys), shown ONLY when a revision
    // happened (mark the exception).
    function draftsHtml(td) {
        const o = td.outcome;
        if (!o || !Number(o.revisions) || !Array.isArray(o.drafts) || !o.drafts.length) return '';
        const mark = (text, findings) => {
            let h = esc(text || '');
            const spans = [];
            (findings || []).forEach(f => (f.spans || []).forEach(s => { if (String(s).trim()) spans.push(String(s)); }));
            spans.sort((a, b) => b.length - a.length).forEach(s => {
                const e = esc(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                h = h.replace(new RegExp(e, 'g'), `<mark class="ac-draft-mark">${esc(s)}</mark>`);
            });
            return h;
        };
        const tabs = o.drafts.map((d, i) => {
            const label = d.which === 'first' ? 'First draft' : `Revision ${String(d.which).replace('revision_', '')}`;
            const n = (d.findings || []).length;
            return `<button type="button" role="tab" id="ac-draft-tab-${i}" aria-selected="${i === 0 ? 'true' : 'false'}" aria-controls="ac-draft-panel-${i}" tabindex="${i === 0 ? '0' : '-1'}" class="ac-draft-tab" data-i="${i}">${label}${n ? ` · ${n} finding${n === 1 ? '' : 's'}` : (d.which === 'first' ? '' : ' · clean')}</button>`;
        }).join('');
        const panels = o.drafts.map((d, i) => `<div role="tabpanel" id="ac-draft-panel-${i}" aria-labelledby="ac-draft-tab-${i}" class="ac-draft-panel${i === 0 ? '' : ' hidden'}" tabindex="0">${d.empty ? '<em>the revision returned nothing — the first draft was delivered</em>' : mark(d.text, d.findings)}</div>`).join('');
        const verdict = o.cleared ? `<span class="trust-chip tc-pass">revised — the revision passed the gate</span>` : `<span class="trust-chip tc-warn">revised — not cleared, the annotated first draft was delivered</span>`;
        return `<details class="ac-drafts mt-2">
            <summary class="text-[11px] cursor-pointer" style="color: var(--text-muted);">Drafts · ${verdict}</summary>
            <div role="tablist" aria-label="Answer drafts" class="ac-draft-tabs">${tabs}</div>
            ${panels}
        </details>`;
    }

    function wireDrafts(body) {
        const list = body.querySelector('.ac-draft-tabs');
        if (!list) return;
        const tabs = [...list.querySelectorAll('[role=tab]')];
        const select = (i) => {
            tabs.forEach((t, k) => { t.setAttribute('aria-selected', String(k === i)); t.tabIndex = k === i ? 0 : -1; });
            body.querySelectorAll('.ac-draft-panel').forEach((p, k) => p.classList.toggle('hidden', k !== i));
            tabs[i].focus();
        };
        tabs.forEach((t, i) => {
            t.addEventListener('click', () => select(i));
            t.addEventListener('keydown', (e) => {
                if (e.key === 'ArrowRight') { e.preventDefault(); select((i + 1) % tabs.length); }
                if (e.key === 'ArrowLeft') { e.preventDefault(); select((i - 1 + tabs.length) % tabs.length); }
                if (e.key === 'Home') { e.preventDefault(); select(0); }
                if (e.key === 'End') { e.preventDefault(); select(tabs.length - 1); }
            });
        });
    }

    function chainStripHtml(td, sid) {
        const chain = td.provenance_chain || [];
        if (!chain.length) return '';
        const lock = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>';
        const seals = chain.map((s, i) => {
            const tip = `${s.step_type || 'step'}\n${s.content_summary || ''}\nchain ${String(s.chain_hash || '').slice(0, 12)}…${s.signature ? '\nEd25519-signed' : ''}`;
            return `<span class="pc-step u-tip" data-tip="${esc(tip)}">${lock}<span class="pc-type">${esc(s.step_type || 'step')}</span><code class="pc-hash">${esc(String(s.chain_hash || '').slice(0, 8))}</code></span>`
                + (i < chain.length - 1 ? '<span class="pc-link"></span>' : '');
        }).join('');
        return `<details class="mt-2 pt-2" style="border-top: 1px dashed var(--border-secondary);">
            <summary class="text-[10px] cursor-pointer" style="color: var(--text-muted);">View the sealed chain — ${chain.length} step(s), Ed25519-signed, tamper-evident</summary>
            <div class="pc-strip mt-1">${seals}</div>
            <div class="flex items-center gap-2 mt-1.5">
                <button type="button" class="pc-verify text-[11px] px-2.5 py-1 rounded" data-session="${esc(sid)}"
                    style="color: var(--text-muted); border: 1px solid var(--border-secondary); background: transparent;">Verify chain</button>
                <span class="pc-verify-out text-[10px]" style="color: var(--text-muted);"></span>
            </div></details>`;
    }

    async function verifyChain(btn, body) {
        const out = body.querySelector('.pc-verify-out');
        const strip = body.querySelector('.pc-strip');
        if (out) out.textContent = 'Verifying against the deployment provenance key…';
        try {
            const token = localStorage.getItem('tda_auth_token');
            const r = await fetch(`/api/v1/sessions/${encodeURIComponent(btn.dataset.session)}/provenance/verify`,
                { method: 'POST', headers: token ? { 'Authorization': `Bearer ${token}` } : {} });
            const d = r.ok ? await r.json() : null;
            const v = (d && d.verification) || {};
            if (v.valid === true) {
                if (out) out.innerHTML = `<span class="trust-chip tc-pass">chain verified — ${v.turns_verified} turn(s), Ed25519</span>`;
            } else if (v.valid === false) {
                if (strip) strip.classList.add('pc-broken');
                const err = (v.errors && v.errors[0]) || 'integrity check failed';
                if (out) out.innerHTML = `<span class="trust-chip tc-danger">verification FAILED — ${esc(err)}</span>`;
            } else {
                if (out) out.innerHTML = `<span class="trust-chip">nothing to verify (${(v.warnings && v.warnings[0]) || 'no sealed turns'})</span>`;
            }
        } catch (e) {
            if (out) out.textContent = 'Verification unavailable.';
        }
    }

    // The persisted gate stamps are read by THREE surfaces (the assurance dial, the
    // journey strip, this capsule) and must say the same thing. A stamp with NO verdict
    // is an ABSENCE (`{ran:false, action:"na"}` = nothing in this answer to ground;
    // `action:"error"` = the gate failed), never a conviction: the capsule once had only
    // two branches (pass · annotate) and let an abstained gate fall into
    // "withheld — confirmed drift" — red under a dial reading all quiet (prod 0c09571e).
    // Mirrors assurance_points._read_grounding / _guard_reading; keep the words in step.
    function groundingChip(g) {
        if (!g) return '';
        if (!('decision' in g)) {
            if (g.action === 'error') return `<span class="trust-chip tc-unknown">did not run — the gate failed (not a pass)</span>`;
            if (g.action === 'na') return `<span class="trust-chip tc-na">nothing in this answer to ground</span>`;
            return `<span class="trust-chip tc-na">did not run — ${esc(g.reason || 'not enabled')}</span>`;
        }
        if (g.decision === 'pass') return `<span class="trust-chip tc-pass">in scope</span>`;
        if (g.decision === 'annotate') return `<span class="trust-chip tc-warn">annotated — reached outside scope</span>`;
        if (g.decision === 'block') return `<span class="trust-chip tc-danger">withheld — confirmed drift</span>`;
        return `<span class="trust-chip tc-na">${esc(String(g.decision))}</span>`;
    }
    function guardChip(gu) {
        if (!gu) return '';
        if (gu.ran === false) return `<span class="trust-chip tc-na">did not run — ${esc(gu.reason || 'not enabled for this agent')}</span>`;
        const cats = (gu.categories || []).map(esc).join(', ');
        if (gu.action === 'error') return `<span class="trust-chip tc-unknown">the guard failed — not recorded as a pass</span>`;
        if (gu.action === 'block') return `<span class="trust-chip tc-danger">blocked (${cats || 'violation'})</span>`;
        if (gu.action === 'redact') return `<span class="trust-chip tc-warn">personal data masked: ${cats || 'pii'}</span>`;
        if (gu.action === 'annotate') return `<span class="trust-chip tc-warn">caught: ${cats || 'findings'} — annotated</span>`;
        if (cats) return `<span class="trust-chip tc-warn">caught: ${cats}</span>`;
        return `<span class="trust-chip tc-pass">screened — clean</span>`;
    }

    function capsuleHtml(td, sid, turn) {
        const g = td.grounding || null;
        const gu = td.guardrail || null;
        const ob = td.obda || null;
        const pm = td.provenance_meta || null;
        const scope = g && (g.scope_databases || []).length
            ? g.scope_databases.map(esc).join(', ') : '';
        const groundingLine = groundingChip(g);
        const guardLine = guardChip(gu);
        // Requests the contract REFUSED are governance working, not data moving —
        // named beside the lane, never folded into an "operations ran" count
        // (an M4 reject once counted as a third "governed data operation").
        const rejNote = ob && ob.rejected_count
            ? ` · ${ob.rejected_count} request${ob.rejected_count === 1 ? '' : 's'} rejected by the contract`
            : '';
        const laneLine = ob
            ? (ob.lane === 'governed'
                ? `<span class="trust-chip tc-pass">governed — every figure from a governed metric${rejNote}</span>`
                : ob.lane === 'confined'
                ? `<span class="trust-chip tc-pass">confined — raw SQL denied by the data surface (${ob.denied_count || 0} attempt${(ob.denied_count || 0) === 1 ? '' : 's'}, nothing executed)</span>`
                : `<span class="trust-chip tc-warn">${ob.governed_count || 0} governed · ${ob.freeform_count || 0} free-formed (advisory)${rejNote}</span>`)
            : '';
        // O2 — the consumption receipts: one chip per signed data product this answer
        // consumed, with the sealed facts (request/sql hash, rows, trust at consumption)
        // one hover away. Reload-safe: reads the same stamped `obda.operations` the
        // EPC step was sealed from, so chip and seal cannot disagree.
        let productsLine = '';
        {
            const rc = ((ob && ob.operations) || []).filter((o) => o && o.product);
            if (rc.length) {
                productsLine = rc.slice(0, 4).map((o) => {
                    const trust = o.trust_state === 'trusted' ? 'tc-pass'
                        : (o.trust_state ? 'tc-warn' : '');
                    const rows = (o.row_count == null) ? '' : ` · ${o.row_count} row${o.row_count === 1 ? '' : 's'}`;
                    const tip = `Signed data product — a governed consumption contract.\n`
                        + `Request hash: ${String(o.request_hash || '').slice(0, 16)}…\n`
                        + `Compiled SQL hash: ${String(o.sql_hash || '').slice(0, 16)}…\n`
                        + `Trust at consumption: ${o.trust_state || 'unknown'}.\n`
                        + `This receipt is sealed into the turn's Ed25519 provenance chain\n`
                        + `(step type obda.consumption) — export the receipt to verify offline.`;
                    return `<span class="trust-chip u-tip ${trust}" data-tip="${esc(tip)}">${esc(o.product)}${o.version != null ? ' v' + esc(String(o.version)) : ''}${o.trust_state ? ' · ' + esc(o.trust_state) : ''}${rows}</span>`;
                }).join(' ');
            }
        }
        // OBDA-ACT — the signed actions this answer invoked, each in the same words as its Live
        // Status card (`act_words`, folded server-side with the confirmations a person gave
        // afterwards — a confirmed act reads as how it ended, once).
        let actionsLine = '';
        {
            const acts = Array.isArray(td.action_acts) ? td.action_acts : [];
            if (acts.length) {
                actionsLine = acts.slice(0, 4).map((a) => {
                    const cls = { performed: 'tc-pass', prepared: '', parked: 'tc-warn', refused: 'tc-warn', observed: '' }[a.outcome] || '';
                    const tip = `Signed action — a write contract on the ontology.\n`
                        + (a.outcome === 'performed'
                            ? `Performed${a.confirmed_by ? ' after ' + a.confirmed_by + ' confirmed it' : ''}${a.approved_by ? ' after the owner approved it' : ''}.\n`
                              + `The receipt is signed (Ed25519) — export it from the answer row to verify offline.`
                            : a.outcome === 'refused' ? `Refused — nothing was written.\n${a.refusal || a.refused_check || ''}`
                            : a.outcome === 'parked' ? 'Parked for the owner\'s review in the Assurance Inbox — nothing written yet.'
                            : a.outcome === 'prepared' ? 'Prepared — awaiting a person\'s confirmation; nothing written yet.'
                            : 'Observe posture — recorded what would change; nothing changed.');
                    return `<span class="trust-chip u-tip ${cls}" data-tip="${esc(tip)}">${esc(a.words || a.action || 'action')}</span>`;
                }).join(' ') + (acts.length > 4 ? ` <span class="text-[10px]" style="color: var(--text-muted);">+${acts.length - 4} more</span>` : '');
            }
        }
        const sealLine = pm && pm.sealed
            ? `<span class="trust-chip tc-pass">sealed · Ed25519-signed chain · tip ${esc(String(pm.chain_tip_hash || '').slice(0, 12))}…</span>`
            : `<span class="trust-chip" style="opacity:.6;">no sealed record</span>`;
        // Wave 4 (U3/K3): source provenance — ONE pill per REPOSITORY, because its integrity
        // signature belongs to the repository (a SOURCE guarantee, visually distinct from the
        // product seal); its documents listed once each beneath it, with their freshness.
        // Grouped by repository id and document id: the chunk's `source` is a DOCUMENT label
        // (its title on one chunk, its file name on the next), and grouping by it rendered
        // one repository and six documents as four peer "sources", each repeating the
        // repository's signature state.
        const kev = td.knowledge_retrieval_event || {};
        const kchunks = Array.isArray(kev.chunks) ? kev.chunks : [];
        let sourcesLine = '';
        let nRepos = 0, nDocs = 0;
        if (kchunks.length) {
            const repos = new Map();
            for (const c of kchunks) {
                const rk = c.collection_id != null ? `id:${c.collection_id}` : `name:${c.collection_name || ''}`;
                if (!repos.has(rk)) repos.set(rk, { c, docs: new Map() });
                const r = repos.get(rk);
                const dk = c.document_id || c.source || '';
                if (!dk) continue;
                const label = String(c.source || '');
                const isFile = /\.[a-z0-9]{1,5}$/i.test(label);
                const d = r.docs.get(dk) || { title: '', file: '', c };
                if (isFile && !d.file) d.file = label; else if (!isFile && !d.title) d.title = label;
                r.docs.set(dk, d);
            }
            nRepos = repos.size;
            repos.forEach((r) => { nDocs += r.docs.size; });
            const TRUST = { signed: ['tc-pass', 'integrity-signed'], changed: ['tc-warn', 'signature changed'],
                            unsigned: ['', 'unsigned'] };
            sourcesLine = [...repos.values()].slice(0, 4).map(({ c, docs }) => {
                const [trust, trustTxt] = TRUST[c.source_trust] || ['', ''];
                const sens = c.sensitivity ? ` · ${c.sensitivity}` : '';
                const name = c.collection_name || 'knowledge repository';
                const tip = `Corpus integrity signature: ${trustTxt || 'unknown'} — a SOURCE guarantee\n(this document set, this embedding model), never a consumption guarantee.`
                          + `${sens ? '\nSensitivity: ' + c.sensitivity + '.' : ''}\n${docs.size} document${docs.size === 1 ? '' : 's'} retrieved from it.`;
                const pill = `<span class="trust-chip u-tip ${trust}" data-tip="${esc(tip)}">${esc(name)}${trustTxt ? ' · ' + trustTxt : ''}${sens}</span>`;
                const list = [...docs.values()];
                const shown = list.slice(0, 6).map((d) => {
                    const fresh = d.c.sync_off ? 'sync off'
                        : (d.c.verified_days_ago != null ? `verified ${d.c.verified_days_ago}d ago` : '');
                    const lbl = d.title || d.file;
                    const dtip = [d.title, d.file].filter(Boolean).join('\n') + (fresh ? `\nFreshness: ${fresh} (CDC).` : '');
                    return `<span class="ac-doc u-tip" data-tip="${esc(dtip)}">${esc(lbl)}${fresh ? `<span class="ac-doc-fresh"> · ${esc(fresh)}</span>` : ''}</span>`;
                }).join('');
                const more = list.length > 6 ? `<span class="ac-doc ac-doc-more">+${list.length - 6} more</span>` : '';
                return `<div class="ac-src">${pill}<div class="ac-docs">${shown}${more}</div></div>`;
            }).join('');
        }
        // S9: the attachments the platform read for this turn — each named, its method and
        // verification, an image as a crop of the cited region (drawn after mount).
        let attachmentsLine = '';
        try {
            if (window.AttachmentSources && td.attachment_sources) attachmentsLine = window.AttachmentSources.sourcesRowHtml(td, sid);
        } catch (_) { attachmentsLine = ''; }
        // Where the dial renders, it is the one instrument for VERDICTS: the grounding
        // gate, the guards and the seal are its stations' members, in the same words. The
        // capsule below it lists what the answer was MADE FROM — each item something to
        // open or verify — and never restates a reading (Rule 3.0). Without the dial (the
        // legacy strip) the readings stay, since nothing else on the capsule carries them.
        const dial = dialRenders(td);
        const drew = `${factRow('Scope', scope ? `<span class="text-[11px]">${scope}</span>` : '')}
            ${factRow('Sources', sourcesLine)}
            ${factRow('Attachments', attachmentsLine)}
            ${factRow('Data lane', laneLine)}
            ${factRow('Data products', productsLine)}`;
        const did = factRow('Actions', actionsLine);
        const evidence = window.EvidenceView ? factRow('Evidence', '<div data-ac-evidence></div>') : '';
        const exportRow = `<div class="flex items-center gap-2 mt-2">
                <button type="button" class="ac-export text-[11px] px-2.5 py-1 rounded" data-session="${esc(sid)}" data-turn="${esc(turn)}"
                    style="color: var(--text-muted); border: 1px solid var(--border-secondary); background: transparent;">${window.EvidenceView ? 'Export evidence' : 'Export receipt'}</button>
                <span class="text-[10px]" style="color: var(--text-muted);">${window.EvidenceView
                    ? 'one file anyone can verify offline: the answer, its trust checks, its sealed chain, and every signature it was made with'
                    : 'a portable, independently verifiable record of how this answer was produced'}</span>
            </div>`;
        // Without the dial (the legacy strip) nothing else carries the readings, so they stay,
        // in their original order among the rows.
        if (!dial) {
            return `${miniJourney(td)}
            ${factRow('Grounding', groundingLine)}
            ${drew}
            ${did}
            ${factRow('Guardrail', guardLine)}
            ${factRow('Provenance', sealLine)}
            ${evidence}
            ${draftsHtml(td)}
            ${chainStripHtml(td, sid)}
            ${exportRow}`;
        }
        // FOLDED, like the sealed chain inside it: the dial above is the reading, and what
        // the answer was made from is reference a reader opens when they want it. The fold
        // line names what is inside, so a closed fold still says what it holds.
        //
        // STRUCTURED by what each item is to the reader: what the answer DREW ON (its inputs),
        // what it DID (writes, only when there were any), and what there is TO VERIFY (the
        // evidence, the kept drafts, the sealed chain, the export). One flat list mixed
        // inputs with proofs, and needed a sentence to say what it was.
        const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
        const prods = ((ob && ob.operations) || []).filter((o) => o && o.product).length;
        const nActs = Array.isArray(td.action_acts) ? td.action_acts.length : 0;
        const nChain = (td.provenance_chain || []).length;
        const inside = [
            nRepos ? plural(nRepos, 'repository', 'repositories') : '',
            nDocs ? plural(nDocs, 'document', 'documents') : '',
            attachmentsLine ? 'attachments' : '',
            prods ? plural(prods, 'data product', 'data products') : '',
            nActs ? plural(nActs, 'action', 'actions') : '',
            window.EvidenceView ? 'evidence' : '',
            nChain ? 'sealed chain' : '',
        ].filter(Boolean).join(' · ');
        const grp = (h, tip, body) => (body && body.trim())
            ? `<div class="ac-made-grp"><div class="ac-made-h u-tip" data-tip="${esc(tip)}">${h}</div>${body}</div>` : '';
        return `${miniJourney(td)}
            <details class="ac-made"><summary class="ac-made-sum"><span class="ad-key-lead">Made from</span>${
                inside ? `<span class="ac-made-n">${esc(inside)}</span>` : ''}</summary>
            ${grp('Drew on', 'The inputs this answer was built from: its data scope, the knowledge it retrieved, the files it read, the data it consumed.', drew)}
            ${grp('Did', 'What this answer changed: each signed action, and how it ended.', did)}
            ${grp('To verify', 'What lets someone check this answer without trusting this instance: the signed parts it was made with, the drafts it kept, the sealed chain, and one file to verify offline.',
                  evidence + draftsHtml(td) + chainStripHtml(td, sid) + exportRow)}
            </details>`;
    }

    async function exportReceipt(sid, turn) {
        const td = await fetchTurn(sid, turn);
        if (!td) return;
        let evalScore = null;
        try {
            const token = localStorage.getItem('tda_auth_token');
            const r = await fetch(`/api/v1/evals/turn-score?session_id=${encodeURIComponent(sid)}&turn=${parseInt(turn, 10)}`,
                { headers: token ? { 'Authorization': `Bearer ${token}` } : {} });
            if (r.ok) { const d = await r.json(); if (d.scored) evalScore = d; }
        } catch (_) { /* eval optional */ }
        // PROP-009: with the Ontology Suite present the receipt IS the evidence bundle — one
        // export, verifiable offline (scripts/verify_evidence.py or the verifier page): the
        // answer and its trust stamps, the sealed chain, the bill of materials, and every
        // attestation as it was signed when the answer was given. The evaluation is appended to
        // the bundle TEXT, never re-serialized, so every re-derived hash stays exact.
        if (window.EvidenceView && window.EvidenceView.exportBundleText) {
            let text = await window.EvidenceView.exportBundleText({
                turns: [{ session_id: sid, turn: parseInt(turn, 10) }], include_receipt: true, preset: 'answer' });
            if (text) {
                text = window.EvidenceView.appendTopLevel(text, 'evaluation', evalScore);
                window.EvidenceView.downloadText(text, `uderia_answer_evidence_${String(sid).slice(0, 8)}_turn${turn}.json`,
                                                 { what: 'answer evidence', sessionId: sid, turnId: turn });
                return;
            }
        }
        const evidence = null;
        const receipt = {
            receipt_version: 1,
            exported_at: new Date().toISOString(),
            session_id: sid, turn: parseInt(turn, 10),
            user_query: td.user_query || null,
            answer: td.final_summary || td.final_summary_text || null,
            trust: {
                grounding: td.grounding || null,
                guardrail_outbound: td.guardrail || null,
                guardrail_inbound: td.guardrail_inbound || null,
                governed_data: td.obda || null,
                honesty: td.honesty || null,
            },
            evaluation: evalScore,
            evidence: evidence,
            provenance_meta: td.provenance_meta || null,
            provenance_chain: td.provenance_chain || null,
            note: 'Each provenance step carries an Ed25519 signature over its chain hash; '
                + 'the chain is verifiable offline with the deployment provenance public key.',
        };
        const blob = new Blob([JSON.stringify(receipt, null, 2)], { type: 'application/json' });
        window.FilesKeep.deliver(blob, `uderia_answer_receipt_${String(sid).slice(0, 8)}_turn${turn}.json`,
                                 { what: 'answer receipt', sessionId: sid, turnId: turn });
    }

    function resolveIds(bubble) {
        // Prefer an in-bubble anchor carrying both ids; otherwise turn-id from the
        // bubble + the viewed session (window.__currentSessionId is stamped by
        // state.js whenever the session switches — all bubbles in the chat log
        // belong to it).
        const both = bubble.querySelector('[data-turn-id][data-session-id]');
        if (both) return { sid: both.dataset.sessionId, turn: both.dataset.turnId };
        const t = bubble.querySelector('[data-turn-id]');
        if (!t) return null;
        const anchor = bubble.closest('[data-session-id]');
        const sid = (anchor && anchor.dataset.sessionId) || window.__currentSessionId || null;
        return sid ? { sid, turn: t.dataset.turnId } : null;
    }

    function injectToggle(bubble) {
        if (bubble.dataset.acDone) return;
        // Assistant messages only: the assistant turn badge marks them; a user
        // bubble carries .user-avatar and is skipped.
        if (bubble.querySelector('.user-avatar')) return;
        if (!bubble.querySelector('.assistant-badge') && !bubble.querySelector('[data-turn-id]')) return;
        const ids = resolveIds(bubble);
        if (!ids || !ids.sid || !ids.turn) return;
        bubble.dataset.acDone = '1';
        const holder = document.createElement('div');
        holder.className = 'answer-capsule-holder mt-1';
        holder.dataset.acSid = ids.sid;
        holder.dataset.acTurn = ids.turn;
        holder.innerHTML = `<button type="button" class="ac-toggle text-[11px] inline-flex items-center gap-1" aria-expanded="false"
                style="color: var(--text-muted); background: none; border: none; cursor: pointer; padding: 2px 0;">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
                Why trust this answer?</button>
            <div class="ac-body hidden rounded-lg px-3 py-2 mt-1" style="background: var(--card-bg); border: 1px solid var(--border-primary);"></div>`;
        const container = bubble.querySelector('.message-content')?.parentElement || bubble;
        const btn = holder.querySelector('.ac-toggle');
        const body = holder.querySelector('.ac-body');
        // Share the answer's action row with the vote pill when ui.js provided one:
        // the toggle moves into the row's left slot, and the holder (now carrying only
        // the expandable body) stays full-width beneath it, so opening the capsule is
        // not squeezed into half the bubble. No row (older render path) ⇒ unchanged.
        const slot = container.querySelector('.msg-actions-row .msg-actions-slot');
        if (slot) {
            slot.appendChild(btn);
            holder.classList.remove('mt-1');
        }
        container.appendChild(holder);
        btn.addEventListener('click', async () => {
            const open = btn.getAttribute('aria-expanded') === 'true';
            btn.setAttribute('aria-expanded', String(!open));
            body.classList.toggle('hidden', open);
            if (!open && body.dataset.loaded) {
                // Re-opening an already-loaded capsule: refresh the review state
                // fresh, so a decision made in the inbox meanwhile shows here.
                refreshReviewSection(body, ids.sid, ids.turn, true);
                // PROP-009: "now" moves while the capsule is closed — re-read it fresh.
                const evHost = body.querySelector('[data-ac-evidence]');
                if (evHost && window.EvidenceView) window.EvidenceView.mount(evHost, ids.sid, ids.turn, { fresh: true });
            }
            if (!open && !body.dataset.loaded) {
                body.innerHTML = `<div class="text-[11px]" style="color: var(--text-muted);">Loading trust record…</div>`;
                const td = await fetchTurn(ids.sid, ids.turn);
                if (!td) {
                    body.innerHTML = `<div class="text-[11px]" style="color: var(--text-muted);">No trust record available for this answer.</div>`;
                    return;
                }
                body.dataset.loaded = '1';
                body.innerHTML = capsuleHtml(td, ids.sid, ids.turn);
                try { if (window.AttachmentSources) window.AttachmentSources.hydrate(body, ids.sid); } catch (_) { /* crops are best-effort */ }
                // R-2 — fill the dial host. `capsuleHtml` returns a string, so the
                // instrument (which wires its own listeners) is rendered after the
                // markup lands, into the placeholder miniJourney left for it.
                const dialHost = body.querySelector('[data-ac-dial]');
                if (dialHost && window.AssuranceDial && td.assurance) {
                    window.AssuranceDial.render(dialHost, td.assurance, { size: 116 });
                }
                // Eval stage back-fill — the SAME turn-score fetch the Live Status strip
                // runs, so the capsule's mini-journey and the strip can never disagree
                // about the evaluation glyph (deriveStages seeds it 'na' by design).
                if (window.TrustJourney && window.TrustJourney.updateEvalStage) {
                    window.TrustJourney.updateEvalStage(body, ids.sid, ids.turn);
                }
                // PROP-009: the answer's evidence — every signed part, when answered · now.
                // Absent (the Ontology Suite removed) ⇒ no host was rendered ⇒ nothing here.
                const evHost = body.querySelector('[data-ac-evidence]');
                if (evHost && window.EvidenceView) window.EvidenceView.mount(evHost, ids.sid, ids.turn);
                const ex = body.querySelector('.ac-export');
                if (ex) ex.addEventListener('click', () => exportReceipt(ex.dataset.session, ex.dataset.turn));
                wireDrafts(body);   // S7: the drafts tab list
                const pv = body.querySelector('.pc-verify');
                if (pv) pv.addEventListener('click', () => verifyChain(pv, body));
                // HITL review state for this turn (pending / released / upheld).
                refreshReviewSection(body, ids.sid, ids.turn, false);
            }
        });
    }

    function scan(rootEl) {
        (rootEl.querySelectorAll ? rootEl.querySelectorAll('.message-bubble') : []).forEach(injectToggle);
        if (rootEl.classList && rootEl.classList.contains('message-bubble')) injectToggle(rootEl);
    }

    function start() {
        const log = document.getElementById('chat-log');
        if (!log) { setTimeout(start, 1500); return; }
        scan(log);
        const obs = new MutationObserver((muts) => {
            for (const m of muts) {
                m.addedNodes.forEach((n) => { if (n.nodeType === 1) scan(n); });
            }
        });
        obs.observe(log, { childList: true, subtree: true });
    }

    // Live refresh: a review decision made in the Assurance Inbox updates any
    // open capsule for that turn in place (event dispatched by notifications.js).
    // OBDA-ACT — a person confirmed an act, or the owner decided a parked one: the turn's acts
    // moved, so the capsule re-reads that turn the next time it is opened.
    const _forgetTurn = (sid, turn) => {
        if (!sid || turn == null) return;
        CACHE.delete(`${sid}:${turn}`);
        document.querySelectorAll('.answer-capsule-holder').forEach((holder) => {
            if (holder.dataset.acSid !== String(sid) || String(holder.dataset.acTurn) !== String(turn)) return;
            const body = holder.querySelector('.ac-body');
            if (body) delete body.dataset.loaded;
        });
    };
    UderiaBus.on('uderia:action-performed', (ev) => {
        const d = (ev && ev.detail) || {};
        _forgetTurn(d.session_id, d.turn_id);
    });
    UderiaBus.on('uderia:review-decision', (ev) => {
        const d0 = (ev && ev.detail) || {};
        if (d0.source === 'action') _forgetTurn(d0.session_id, d0.turn_id);
    });
    UderiaBus.on('uderia:review-decision', (ev) => {
        const rdn = (ev && ev.detail) || {};
        document.querySelectorAll('.answer-capsule-holder').forEach((holder) => {
            const body = holder.querySelector('.ac-body');
            if (!body || !body.dataset.loaded) return;
            if (rdn.session_id && holder.dataset.acSid !== rdn.session_id) return;
            if (rdn.turn_id != null && String(holder.dataset.acTurn) !== String(rdn.turn_id)) return;
            refreshReviewSection(body, holder.dataset.acSid, holder.dataset.acTurn, true);
        });
    });

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();
