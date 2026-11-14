/**
 * Trust Journey strip — the per-turn assurance pipeline, rendered.
 *
 * One compact strip showing the stages every turn passes through (the manual's
 * §2.0 pipeline): inbound guard → authority → scope → tool enforcement →
 * outbound guard → grounding gate → honesty → governed data → evaluation →
 * provenance seal. Each stage is a glyph in one of four states (shape + fill,
 * colorblind-safe — see static/css/trust.css):
 *   pass   (solid)   — the layer ran and found nothing
 *   warn   (half)    — the layer ran and ACTED (annotated / masked / flagged)
 *   danger (outline) — the layer blocked / withheld
 *   na     (dashed)  — not applicable / not enabled / not recorded on this turn
 *
 * LEGACY FALLBACK ONLY (PROP-002): the per-turn surface is the Assurance dial
 * (8 stations, six states, the dot-mark grammar — assuranceDial.js), which removes
 * this strip whenever the turn carries an `assurance` block. The strip renders
 * only for pre-dial turns whose payload predates the registry, and its TEN
 * stages are a retired dialect — do not extend it; extend the registry
 * (agent/assurance_points.py) and the dial instead.
 *
 * Pure rendering over data the platform already persists on the turn
 * (grounding / guardrail / obda / honesty stamps, provenance_meta) — it never
 * alters anything and degrades honestly when a stamp is absent.
 *
 * Plain script (same pattern as qualityDimensions.js): exposes
 * window.TrustJourney and is invoked, guarded, from ui.js's
 * injectTrajectoryEvalOverlay so it renders on BOTH the live path
 * (turn_score_complete) and the reload path (handleReloadPlanClick finally).
 */
(function () {
    'use strict';

    const S = (p) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${p}</svg>`;
    const ICONS = {
        inbound: S('<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M12 8v4"/><path d="M12 16h.01"/>'),
        authority: S('<circle cx="12" cy="8" r="4"/><path d="M4 21v-1a7 7 0 0 1 14 0v1"/>'),
        scope: S('<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2"/>'),
        tools: S('<path d="M14.7 6.3a5 5 0 0 0-6.9 6.2L3 17.3V21h3.7l4.8-4.8a5 5 0 0 0 6.2-6.9l-3.2 3.2-2.8-.7-.7-2.8 3.7-3.7z"/>'),
        outbound: S('<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/>'),
        grounding: S('<path d="M12 3v18"/><path d="M5 8l7-5 7 5"/><path d="M5 8v4a7 7 0 0 0 14 0V8"/>'),
        honesty: S('<path d="M12 3v18"/><path d="M7 6h10"/><path d="M5 6l-2 6a3 3 0 0 0 6 0L7 6"/><path d="M17 6l-2 6a3 3 0 0 0 6 0l-2-6"/>'),
        obda: S('<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7"/>'),
        evaluation: S('<path d="M3 12h4l3-8 4 16 3-8h4"/>'),
        seal: S('<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>'),
    };

    // guardrail verdict action → strip state + note
    function guardState(g, kind) {
        if (!g || g.mode === 'off') return { state: 'na', tip: `${kind} guard: not enabled or not recorded` };
        // A stamp that RAN=false is an absence, not a clean screen (mirrors _guard_reading).
        if (g.ran === false) return { state: 'na', tip: `${kind} guard: did not run — ${g.reason || 'not enabled for this agent'}` };
        if (g.action === 'error') return { state: 'unknown', tip: `${kind} guard: failed — not recorded as a pass` };
        const cats = (g.categories || []).join(', ');
        switch (g.action) {
            // an OBSERVE-mode catch: found something, changed nothing (mirrors _guard_reading)
            case 'pass': return cats
                ? { state: 'warn', tip: `${kind} guard: caught ${cats} — recorded, not altered (observe mode)` }
                : { state: 'pass', tip: `${kind} guard: screened — clean` };
            case 'annotate': return { state: 'warn', tip: `${kind} guard: caught ${cats || 'findings'} — annotated` };
            case 'redact': return { state: 'warn', tip: `${kind} guard: personal data masked (${cats || 'pii'})` };
            case 'block': return { state: 'danger', tip: `${kind} guard: blocked (${cats || 'violation'})` };
            default: return { state: 'pass', tip: `${kind} guard: screened` };
        }
    }

    function deriveStages(td) {
        td = td || {};
        const stages = [];

        stages.push({ key: 'inbound', label: 'Inbound guard', icon: 'inbound',
                      scrollText: 'Guardrail (input)', ...guardState(td.guardrail_inbound, 'Inbound') });

        const origin = td.origin || '';
        const autonomous = ['flow', 'genie_parent', 'genie_child'].includes(origin) || td.source === 'scheduler';
        stages.push({ key: 'authority', label: 'Authority', icon: 'authority',
                      state: autonomous ? 'pass' : 'na',
                      tip: autonomous
                          ? `Authority: autonomous run — acting as the agent principal (${origin || 'scheduler'}), subset of owner`
                          : 'Authority: interactive turn — acting as the user' });

        const gr = td.grounding || null;
        const scopeDbs = (gr && gr.scope_databases) || [];
        const hasScope = scopeDbs.length > 0 || !!td.kg_enrichment_event;
        stages.push({ key: 'scope', label: 'Trusted scope', icon: 'scope',
                      state: hasScope ? 'pass' : 'na',
                      tip: hasScope
                          ? `Trusted scope stamped${scopeDbs.length ? `: ${scopeDbs.slice(0, 4).join(', ')}${scopeDbs.length > 4 ? '…' : ''}` : ''}`
                          : 'Trusted scope: no knowledge model bound to this turn' });

        // Tool enforcement — evidence of tool calls under the enforcement chokepoint.
        let toolCount = 0;
        if (Array.isArray(td.execution_trace)) toolCount = td.execution_trace.length;
        else if (Array.isArray(td.turn_action_history)) toolCount = td.turn_action_history.length;
        else if (Array.isArray(td.conversation_agent_events)) {
            toolCount = td.conversation_agent_events.filter(e => e && (e.type === 'tool_start' || e.tool_name)).length;
        }
        let denied = false;
        try { denied = JSON.stringify(td.execution_trace || td.conversation_agent_events || '').includes('agent_denied'); } catch (_) { /* noop */ }
        stages.push({ key: 'tools', label: 'Tool enforcement', icon: 'tools',
                      state: denied ? 'warn' : (toolCount > 0 ? 'pass' : 'na'),
                      tip: denied ? 'Tool enforcement: at least one call was DENIED by Agent Authority'
                          : (toolCount > 0 ? `Tool enforcement: ${toolCount} call(s) executed under the enforcement chokepoint`
                                           : 'Tool enforcement: no tool calls this turn') });

        stages.push({ key: 'outbound', label: 'Outbound guard', icon: 'outbound',
                      scrollText: 'Guardrail', ...guardState(td.guardrail, 'Outbound') });

        if (gr) {
            // The finding the gate acted on, in the registry's words (assurance_points
            // `_grounding_finding`): family and count, never a fixed phrase.
            const finding = (() => {
                const parts = [];
                const spans = gr.unsupported_claims || [], m = gr.claims_checked;
                if (spans.length) parts.push(m ? `${spans.length} of ${m} statements unmatched` : `${spans.length} unmatched statement${spans.length === 1 ? '' : 's'}`);
                const nums = gr.ungrounded_numbers || [];
                if (nums.length) parts.push(`${nums.length} figure${nums.length === 1 ? '' : 's'} unmatched`);
                const srcs = [...new Set([...(gr.out_of_scope_dbs || []), ...(gr.answer_oos_refs || [])])].sort();
                if (srcs.length) parts.push('out of scope: ' + srcs.slice(0, 3).join(', ') + (srcs.length > 3 ? ' …' : ''));
                return parts.join(' · ') || (gr.decision === 'block' ? 'drift confirmed' : 'flagged');
            })();
            const map = { pass: ['pass', 'Grounding gate: answer stays inside declared scope'],
                          annotate: ['warn', `Grounding gate: ${finding} — annotated`],
                          block: ['danger', `Grounding gate: withheld — ${finding}`] };
            // No verdict on the stamp = the gate ABSTAINED (nothing to ground) or failed —
            // an absence, never a pass (it once fell back to 'pass', a vacuous clean).
            const [st, tip] = map[gr.decision]
                || (!('decision' in gr)
                    ? (gr.action === 'error' ? ['unknown', 'Grounding gate: failed — not recorded as a pass']
                        : gr.action === 'na' ? ['na', 'Grounding gate: nothing in this answer to ground']
                        : ['na', `Grounding gate: did not run — ${gr.reason || 'not enabled'}`])
                    : ['na', `Grounding gate: ${gr.decision}`]);
            const oos = (gr.out_of_scope_dbs || []).length;
            stages.push({ key: 'grounding', label: 'Grounding gate', icon: 'grounding',
                          scrollText: 'Grounding Gate', state: st,
                          tip: tip + (oos ? `\nout-of-scope: ${gr.out_of_scope_dbs.slice(0, 3).join(', ')}` : '') });
        } else {
            stages.push({ key: 'grounding', label: 'Grounding gate', icon: 'grounding',
                          state: 'na', tip: 'Grounding gate: not enabled or no scope to check' });
        }

        if (td.honesty) {
            const h = td.honesty;
            stages.push({ key: 'honesty', label: 'Honesty', icon: 'honesty', scrollText: 'Honesty',
                          state: h.decision === 'block' ? 'danger' : 'warn',
                          tip: h.decision === 'block'
                              ? 'Honesty check: confident non-existence claim on failed queries — answer rewritten'
                              : `Honesty check: flagged — ${h.reason || 'assertion not supported by successful queries'}` });
        }

        if (td.obda) {
            const o = td.obda;
            const st = o.lane === 'governed' ? 'pass' : 'warn';
            stages.push({ key: 'obda', label: 'Governed data', icon: 'obda',
                          scrollText: 'Governed Data Operations', state: st,
                          tip: o.lane === 'governed'
                              ? `Governed data: all ${o.governed_count || 0} data operation(s) via governed metrics`
                              : `Governed data: ${o.freeform_count || 0} free-formed / ${o.governed_count || 0} governed (advisory lane shown honestly)` });
        }

        stages.push({ key: 'evaluation', label: 'Evaluation', icon: 'evaluation',
                      state: 'na', tip: 'Evaluation: checking for a recorded score…' });

        const pm = td.provenance_meta || null;
        stages.push({ key: 'seal', label: 'Provenance seal', icon: 'seal',
                      state: pm && pm.sealed ? 'pass' : 'na',
                      tip: pm && pm.sealed
                          ? `Provenance: turn sealed into the signed chain\ntip ${String(pm.chain_tip_hash || '').slice(0, 12)}… · ${pm.step_count || '?'} step(s)`
                          : 'Provenance: no sealed chain recorded on this turn' });
        return stages;
    }

    function stageButton(st, idx, total) {
        const edge = idx === 0 ? ' tj-tip-left' : (idx === total - 1 ? ' tj-tip-right' : '');
        const scroll = st.scrollText ? ` data-scroll="${st.scrollText.replace(/"/g, '&quot;')}"` : '';
        return `<button type="button" class="tj-stage tj-${st.state}${edge}" data-stage="${st.key}"` +
               ` data-tip="${(st.label + '\n' + st.tip).replace(/"/g, '&quot;')}" aria-label="${st.label}: ${st.state}"${scroll}>` +
               `${ICONS[st.icon] || ''}</button>`;
    }

    function buildStrip(stages) {
        // Rank order aligned with the grammar's ATTN_RANK (PROP-002): `na` sits
        // BELOW `pass` on every surface — the strip was the one place they tied.
        // Connectors take worst-of their neighbours; na/pass both map to the
        // unclassed connector, so the visual is unchanged, but the ordering no
        // longer contradicts the shared rank table.
        const rank = { na: 0, pass: 1, warn: 2, danger: 3 };
        const cls = { 0: '', 1: '', 2: 'tj-warn', 3: 'tj-danger' };
        let html = '';
        stages.forEach((st, i) => {
            if (i > 0) {
                const worse = Math.max(rank[stages[i - 1].state] || 0, rank[st.state] || 0);
                html += `<div class="tj-link ${cls[worse]}"></div>`;
            }
            html += stageButton(st, i, stages.length);
        });
        const acted = stages.filter(s => s.state === 'warn' || s.state === 'danger').length;
        const sub = acted ? `${acted} layer(s) acted this turn` : 'all layers quiet';
        return `<div class="tj-head">` +
               `<svg xmlns="http://www.w3.org/2000/svg" style="width:14px;height:14px;color:var(--teradata-orange);" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>` +
               `<span class="tj-title">Trust Journey</span><span class="tj-sub">${sub}</span>` +
               `<button type="button" class="tj-filter u-tip u-tip-right" aria-pressed="false" data-tip="Show only the trust events in this trace\n(grounding, guardrail, honesty, governed data, quality)"` +
               ` style="margin-left:auto;font-size:.62rem;padding:1px 8px;border-radius:9999px;border:1px solid var(--border-secondary);background:transparent;color:var(--text-muted);cursor:pointer;">Trust only</button>` +
               `</div><div class="tj-track">${html}</div>`;
    }

    // ── "Trust only" filter — isolate the assurance events from execution noise ──
    const _TRUST_STEP_MARKERS = ['Grounding Gate', 'Guardrail', 'Honesty',
                                 'Governed Data Operations', 'Agent Quality', 'Human Review'];
    let _filterObserver = null;

    function _isTrustStep(el) {
        const t = el.textContent || '';
        return _TRUST_STEP_MARKERS.some((m) => t.includes(m));
    }

    function _applyTrustFilter(host, on) {
        // Hide non-trust steps + whole execution-phase containers; the strip and
        // the Quality overlay always stay. Display state is restored on toggle-off.
        host.querySelectorAll('.status-step').forEach((el) => {
            el.style.display = (on && !_isTrustStep(el)) ? 'none' : '';
        });
        host.querySelectorAll('.status-phase-container').forEach((el) => {
            el.style.display = (on && !_isTrustStep(el)) ? 'none' : '';
        });
    }

    function wireFilter(strip) {
        const btn = strip.querySelector('.tj-filter');
        const host = document.getElementById('status-window-content');
        if (!btn || !host) return;
        btn.addEventListener('click', () => {
            const on = btn.getAttribute('aria-pressed') !== 'true';
            btn.setAttribute('aria-pressed', String(on));
            btn.style.color = on ? 'var(--teradata-orange)' : 'var(--text-muted)';
            btn.style.borderColor = on ? 'var(--teradata-orange)' : 'var(--border-secondary)';
            _applyTrustFilter(host, on);
            // Keep filtering steps that stream in while the filter is active.
            if (_filterObserver) { _filterObserver.disconnect(); _filterObserver = null; }
            if (on) {
                _filterObserver = new MutationObserver(() => _applyTrustFilter(host, true));
                _filterObserver.observe(host, { childList: true, subtree: false });
            }
        });
    }

    async function fetchDetails(sessionId, turnId) {
        try {
            const token = localStorage.getItem('tda_auth_token');
            const r = await fetch(`/api/session/${sessionId}/turn/${turnId}/details`,
                { headers: token ? { 'Authorization': `Bearer ${token}` } : {} });
            if (!r.ok) return null;
            return await r.json();
        } catch (_) { return null; }
    }

    function setStage(strip, key, state, tip, label) {
        const el = strip.querySelector(`.tj-stage[data-stage="${key}"]`);
        if (!el) return;
        el.classList.remove('tj-pass', 'tj-warn', 'tj-danger', 'tj-na');
        el.classList.add(`tj-${state}`);
        if (tip) el.setAttribute('data-tip', `${label || key}\n${tip}`);
        _refreshSub(strip);
    }

    // Keep the "N layer(s) acted" subtitle honest after async stage updates
    // (the eval stage fills in after a turn-score fetch).
    function _refreshSub(strip) {
        const sub = strip.querySelector('.tj-sub');
        if (!sub) return;
        const acted = strip.querySelectorAll('.tj-stage.tj-warn, .tj-stage.tj-danger').length;
        sub.textContent = acted ? `${acted} layer(s) acted this turn` : 'all layers quiet';
    }

    async function updateEvalStage(strip, sessionId, turnId) {
        try {
            const token = localStorage.getItem('tda_auth_token');
            const r = await fetch(`/api/v1/evals/turn-score?session_id=${encodeURIComponent(sessionId)}&turn=${parseInt(turnId, 10)}`,
                { headers: token ? { 'Authorization': `Bearer ${token}` } : {} });
            if (!r.ok) throw new Error('no score');
            const d = await r.json();
            if (!d.scored) {
                setStage(strip, 'evaluation', 'na', 'Evaluation: no recorded score for this turn', 'Evaluation');
                return;
            }
            const verdicts = d.verdicts || [];
            const fails = verdicts.filter(v => v && v.passed === false).length;
            const judged = d.judged || d.has_judges;
            const st = fails ? 'warn' : 'pass';
            setStage(strip, 'evaluation', st,
                `Evaluation: scored${judged ? ' + judged' : ''} — ${verdicts.length} check${verdicts.length === 1 ? '' : 's'}, ${fails} failing (see Quality card)`,
                'Evaluation');
        } catch (_) {
            setStage(strip, 'evaluation', 'na', 'Evaluation: no recorded score for this turn', 'Evaluation');
        }
    }

    function wireScroll(strip) {
        strip.addEventListener('click', (ev) => {
            const btn = ev.target.closest('.tj-stage[data-scroll]');
            if (!btn) return;
            const text = btn.getAttribute('data-scroll');
            const host = document.getElementById('status-window-content');
            if (!host || !text) return;
            const steps = host.querySelectorAll('.status-step');
            for (const step of steps) {
                if ((step.textContent || '').includes(text)) {
                    step.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    return;
                }
            }
        });
    }

    /**
     * Inject the strip at the top of the Live Status content for one turn.
     * Idempotent per render (guards on #trust-journey-strip). `turnData` may be
     * partial (live path) — if the trust stamps are absent the full turn is
     * fetched. Never throws; failure leaves the trace untouched.
     */
    async function inject(sessionId, turnId, turnData) {
        try {
            const host = document.getElementById('status-window-content');
            if (!host || sessionId == null || turnId == null) return;
            if (host.querySelector('#trust-journey-strip')) return;
            let td = turnData;
            const hasStamps = td && (td.grounding || td.guardrail || td.provenance_meta ||
                                     td.guardrail_inbound || td.obda || td.honesty);
            if (!hasStamps) {
                const fetched = await fetchDetails(sessionId, turnId);
                if (fetched) td = fetched;
            }
            if (!td) return;
            if (td.profile_type === 'flow' || td.flow_run_id) return;
            if (host.querySelector('#trust-journey-strip')) return; // re-check post-await

            const strip = document.createElement('div');
            strip.id = 'trust-journey-strip';
            strip.setAttribute('data-session-id', String(sessionId));
            strip.setAttribute('data-turn-id', String(turnId));
            const stages = deriveStages(td);
            strip.innerHTML = buildStrip(stages);
            host.insertBefore(strip, host.firstChild);
            wireScroll(strip);
            wireFilter(strip);
            updateEvalStage(strip, sessionId, turnId);
            // R-5 — the ambient chat-header mark speaks STATION semantics even on the
            // legacy path. The mini ring is an 8-station vocabulary everywhere else
            // (roster, tree, capsule); feeding it the TEN stage states meant the flag
            // silently changed the glyph's segment count and meaning — the same fact
            // in two languages, decided by a rendering preference. Stages remain the
            // fallback for a payload without the assurance block (pre-R-2 server).
            const glyphHost = document.getElementById('chat-trust-glyph-host');
            if (glyphHost && window.TrustCircle && window.TrustCircle.glyphInto) {
                glyphHost.style.display = 'inline-flex';
                // The EXECUTED profile tag (turn records stamp the @TAG override,
                // not the session default) — the click focuses this agent.
                glyphHost.dataset.tag = td.profile_tag || '';
                const AD = window.AssuranceDial;
                const states = (td.assurance && AD && AD.stations && AD.ORDER)
                    ? (() => { const st = AD.stations(td.assurance);
                               return AD.ORDER.map((k) => st[k].state); })()
                    : stages.map((s) => s.state);
                window.TrustCircle.glyphInto(glyphHost, states,
                    { size: 20, label: 'Latest assurance reading — open the Trust Circle' });
            }
        } catch (e) {
            console.warn('[trust-journey] inject failed (trace unaffected):', e);
        }
    }

    // Exposed so other trust surfaces (e.g. the answer capsule's mini-journey)
    // render the SAME per-stage icons as the Live Status strip — one source.
    function iconFor(key) { return ICONS[key] || ''; }

    window.TrustJourney = { inject, deriveStages, iconFor, updateEvalStage };
})();
