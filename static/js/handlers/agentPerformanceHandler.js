/*
 * Agent Performance (the "Agents" topic) — WS3 W3.5 UX
 *
 * Agent-CENTRIC, not run-centric: the market buries "is THIS agent good?" under a
 * firehose of traces. Uderia leads with an agent health roster (profiles are
 * first-class, IFOC-classed) → drill into per-agent quality, regression, failure
 * clusters, experiments, and SME calibration.
 *
 * Matches the Execution Performance command-center design language (gradient hero,
 * rounded-xl metric/section cards, var(--card-bg)/var(--border-primary)). Fully
 * theme-aware (CSS variables), monochrome inline SVG icons, no emoji.
 * Exposes window.agentPerformance.load().
 */
(function () {
    'use strict';

    // Platform-wide 4-class health taxonomy (single source of truth — mirrors
    // agentKpiCharts.healthColor so roster, counters and charts never drift):
    //   Healthy ≥0.80 green · Review 0.50–0.79 amber · Danger <0.50 red · Inactive (no data) grey.
    // The band floors — ONE source, admin-configurable (`TRUST_BEHAVIOR.healthy_floor` /
    // `at_risk_floor`, served as `dashboard.quality_bands`). They were hardcoded here, again
    // in `_execBand`, and a third time in agentKpiCharts — four definitions of two numbers,
    // so a rate could read amber on the roster and green on the chart beside it. Defaults
    // are the historical values, so an untouched deployment is byte-identical.
    let HEALTHY = 0.8;   // green floor
    let REVIEW = 0.5;    // amber floor (below this = danger/red)
    /** Adopt the configured floors from the evals-dashboard payload. Ignores anything
     *  malformed or inverted — a bad value must not repaint the estate. */
    function _applyQualityBands(dash) {
        const b = dash && dash.quality_bands;
        if (!b) return;
        const h = Number(b.healthy), w = Number(b.warn);
        if (!(h > 0 && h <= 1) || !(w > 0 && w <= 1) || w >= h) return;
        HEALTHY = h; REVIEW = w;
        if (window.agentKpiCharts && typeof window.agentKpiCharts.setBands === 'function') {
            window.agentKpiCharts.setBands(h, w);   // the charts colour by the same floors
        }
    }
    // ONE window for every reading on this page: the KPI row, the roster and the four
    // cards all read the last N scored turns. Two endpoints with two defaults (500 /
    // 1000) once put "496 scored" and "991 turns" on the same screen.
    const ONLINE_EVAL_WINDOW = 500;
    const HEALTH = {
        // TC-90: routed through the trust tokens (theme-aware) — the raw dark-theme hexes
        // rendered a SECOND green/amber/red vocabulary next to the ring in light mode.
        healthy:     { label: 'Healthy',  color: 'var(--trust-pass, #10b981)' },
        review:      { label: 'Review',   color: 'var(--trust-warn, #f59e0b)' },  // "Evaluation Recommended"
        danger:      { label: 'Danger',   color: 'var(--trust-danger, #ef4444)' },
        unevaluated: { label: 'Inactive', color: 'var(--text-muted)' },
    };
    const healthClassFor = (pr) => pr == null ? 'unevaluated'
        : pr >= HEALTHY ? 'healthy' : pr >= REVIEW ? 'review' : 'danger';

    // IFOC methodology mapping (mandatory — never show raw internal type strings).
    // The IFOC palette has ONE source — `profileColors.js`, published as
    // `window.ProfileColors` for exactly this kind of IIFE handler. Its own header
    // records that the map "historically drifted across ~5 handlers"; this file used to
    // be one of them. Read it, never restate it.
    //
    // Two labels are this lens's own and are not colours: a flow-agent is a distinct
    // agent TYPE rather than an IFOC class, and `conversation_with_tools` is spelled out
    // here because the roster names the engine (the palette correctly folds it to
    // Ideate's colour through TYPE_ALIASES).
    const _LOCAL_LABEL = { conversation_with_tools: 'Ideate + Tools', flow: 'Flow' };
    const FLOW_COLOR = '#f59e0b';  // hex-palette: flow AGENT-TYPE identity (not a trust state; see FLOW_COLOR)
    const ifoc = (t) => {
        const PC = (typeof window !== 'undefined' && window.ProfileColors) || null;
        if (t === 'flow') return { label: 'Flow', color: FLOW_COLOR };
        const color = PC ? PC.getProfileColor(t) : 'var(--teradata-orange)';
        const label = _LOCAL_LABEL[t] || (PC ? PC.getProfileLabel(t) : (t || 'Agent'));
        return { label, color };
    };

    const fmtPct = (v) => (v == null ? '—' : (v * 100).toFixed(0) + '%');
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    const token = () => localStorage.getItem('tda_auth_token');

    // Consume-once prefetch: load() starts the requests of its SECOND stage (annotations,
    // inbox) beside the first, because only their rendering — not their fetching — needs
    // the first stage's data. A plain GET of a prefetched path takes the in-flight promise
    // exactly once; load() clears what is left when it ends, so a later refresh always
    // fetches fresh. Nothing is cached.
    const _prefetched = new Map();
    function _prefetch(paths) {
        for (const p of paths) {
            const pr = _apiFetch(p, {});
            pr.catch(() => {});   // an unconsumed failure must not surface as unhandled
            _prefetched.set(p, pr);
        }
    }

    function api(path, opts = {}) {
        if (!opts.method && !opts.body && _prefetched.has(path)) {
            const pr = _prefetched.get(path);
            _prefetched.delete(path);
            return pr;
        }
        return _apiFetch(path, opts);
    }

    async function _apiFetch(path, opts = {}) {
        const res = await fetch('/api/v1' + path, {
            ...opts,
            headers: { 'Authorization': 'Bearer ' + token(), 'Content-Type': 'application/json', ...(opts.headers || {}) },
        });
        if (!res.ok) {
            // TC-11: surface the server's reason, not a bare status. A refused release
            // (`release_not_permitted_for_source`), a validation error, a 403 — the owner
            // must learn WHY, which the callers (decideReview/runBacktest/runLiveProbe/
            // quarantineDocFromInbox) already promise. Parse the JSON `error`/`message`;
            // fall back to the status only when the body is not JSON.
            let msg = 'HTTP ' + res.status;
            try {
                const body = await res.json();
                msg = body.error || body.message || msg;
            } catch (_) { /* non-JSON body → keep the status */ }
            const err = new Error(msg);
            err.status = res.status;
            throw err;
        }
        return res.json();
    }

    // ── render primitives (command-center language) ───────────────────────────
    function sectionCard(title, iconPath, bodyHtml, actionsHtml) {
        return `<div class="perf-panel-card">
            <div class="flex items-center justify-between mb-3">
                <h3 class="font-semibold flex items-center gap-2" style="color: var(--text-primary);">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" style="color: var(--teradata-orange);" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="${iconPath}" /></svg>
                    ${esc(title)}
                </h3>
                <div class="flex items-center gap-2">${actionsHtml || ''}</div>
            </div>
            ${bodyHtml}
        </div>`;
    }

    // `key` is the internal scorer key; the row shows the logical label and is a
    // [data-dim] element (hover → styled tooltip, click → Assurance Guide).
    // Colour follows the platform's 3-band health taxonomy via healthClassFor
    // (Healthy ≥0.80 green · Review 0.50–0.79 amber · Danger <0.50 red) — the same rule
    // the compare heatmap and roster dots use. The legacy `threshold` arg is ignored
    // (it collapsed the amber band by painting everything below the healthy line red).
    // A dimension's pass rate is only as trustworthy as its denominator. Deterministic
    // dims are decided every turn; judge dims are decided only on sampled turns — so a
    // judge dim can show a stark 0%/100% off a SINGLE verdict. `n` (decided turns) and
    // `total` (scored turns) let us annotate the sample and de-emphasise low-n bars so a
    // 1-of-1 result never reads like a systemic 13/13 failure.
    const LOW_SAMPLE = 2;   // ≤ this many decided turns → the rate is tentative
    // Compact inline pass/fail sparkline (last N) — situates a dimension's rate
    // against its OWN recent trend, so "80%" reads differently if it's trending
    // up vs down. Rendered next to the dimension bar.
    function microSpark(series) {
        if (!series || series.length < 2) return '';
        const s = series.slice(-14), n = s.length, bw = 3, gap = 1, h = 12;
        const bars = s.map((v, i) =>
            `<rect x="${i * (bw + gap)}" y="${v ? 1 : h * 0.5}" width="${bw}" height="${v ? h - 2 : h * 0.45}" rx="0.5" fill="${v ? 'var(--trust-pass, #34d399)' : 'var(--trust-danger, #f87171)'}" opacity="${v ? 0.8 : 0.9}"></rect>`).join('');
        const w = n * (bw + gap);
        return `<span class="u-tip u-tip-left inline-flex flex-shrink-0" data-tip="last ${_plural(n, 'scored turn')} for this dimension (oldest→newest)"><svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" style="vertical-align:middle;">${bars}</svg></span>`;
    }

    function bar(key, value, threshold, n, total, series) {
        const pct = value == null ? 0 : Math.max(0, Math.min(1, value)) * 100;
        const color = value == null ? 'var(--border-secondary)'
            : HEALTH[healthClassFor(value)].color;
        const partial = (n != null && total != null && n < total);
        const tentative = (n != null && n <= LOW_SAMPLE);
        // Sample annotation. Confidence is NOT attention: colour is reserved for the rate's
        // band (Rule 4.1), so a thin sample is marked by SHAPE — a dashed badge — never by
        // borrowing amber, which would put a green-banded dimension under a warning tint.
        // Mirrors the platform's existing "no/weak grade" vocabulary (`qd-status` uses
        // border-style, not colour, for not-set / abstained / not-applicable).
        const sampleTitle = n == null ? '' :
            (tentative
                ? `Based on only ${n} judged turn${n === 1 ? '' : 's'} of ${total} scored — a single verdict drives this rate, so the value is shown as a fraction and the bar is dimmed.`
                : partial ? `Decided on ${n} of ${total} scored turns.` : `Decided on all ${n} turns.`);
        // At n<=2 the FRACTION is the denominator, so a separate n= badge would restate it
        // (Rule 3.0). Between that and the full window, the grey badge carries it.
        const sample = (!partial || tentative) ? '' :
            `<span class="text-[9px] px-1 rounded flex-shrink-0 u-tip u-tip-left" data-tip="${esc(sampleTitle)}" style="color:var(--text-muted); background:var(--hover-bg-strong, rgba(127,127,127,0.12));">n=${n}</span>`;
        const fillOpacity = tentative ? 0.45 : 1;
        // Rule 3.5, taken to its conclusion: a PERCENTAGE from one or two samples is false
        // precision — 0% and 100% are the only values n=1 can produce, and both read as
        // confident rates. `0/1` cannot mislead, and the visual break from the neighbouring
        // percentages is itself the signal that this row is a different kind of thing.
        const valueText = (tentative && n != null && value != null)
            ? `${Math.round(value * n)}/${n}` : fmtPct(value);
        return `<div class="flex items-center gap-2 mb-1.5">
            <div class="text-xs w-40 flex-shrink-0 flex items-center gap-1 min-w-0" style="color: var(--text-muted);" ${dimAttrs(key)}><span class="truncate">${esc(dimLabel(key))}</span>${sample}</div>
            <div class="flex-1 h-2 rounded-full overflow-hidden" style="background: var(--hover-bg-strong, rgba(127,127,127,0.15));"><div style="width:${pct}%; height:100%; background:${color}; opacity:${fillOpacity};"></div></div>
            ${microSpark(series)}
            <div class="text-xs w-10 text-right u-tip u-tip-left"${tentative ? ` data-tip="${esc(sampleTitle)}"` : ''} style="color:${tentative ? 'var(--text-muted)' : 'var(--text-primary)'};">${valueText}</div>
        </div>`;
    }

    /** The INTERVENTION reading — the mechanism axis, beside the quality one.
     *
     *  A quality rate and an intervention count answer different questions and are close
     *  to independent in practice: on one real 18-turn agent every intervention landed on
     *  a turn whose measurements were otherwise clean, and every measurement failure on a
     *  turn no control touched (contract Rule 3bb). Showing only the rate left "how often
     *  did a control have to act on this agent's answers?" unanswerable from any surface,
     *  while the words "pass rate" and "failed" invited exactly that reading.
     *
     *  Reports only what actually happened — an agent nothing acted on says so plainly
     *  rather than rendering a decorative zero. `errored` is included because a guard
     *  that COULD NOT RUN is not a guard that passed; the per-turn instrument has always
     *  said so and the aggregate had nowhere to put it.
     */
    function interventionNote(o) {
        const g = (o && o.gate) || {};
        const n = o && o.turns;
        if (!n) return '';
        const acted = (g.acted || 0) + (g.refused || 0);
        const bits = [];
        bits.push(acted
            ? `<strong style="color:var(--text-primary)">${acted}</strong> of ${n} answer${n === 1 ? '' : 's'} altered or withheld`
            : `no answer altered or withheld in ${n} turn${n === 1 ? '' : 's'}`);
        if (g.parked) bits.push(`${g.parked} parked for review`);
        if (g.escalated) bits.push(`${g.escalated} escalated to a judge`);
        // Surfaced last and named plainly: this is not an intervention, it is a gap in
        // the record — the honest reading of an errored guard is `unknown`, not `pass`.
        const err = g.errored
            ? `<span style="color:var(--trust-warn)"> · ${g.errored} turn${g.errored === 1 ? '' : 's'} where a guard did not run</span>`
            : '';
        return `<div class="text-xs mt-1" style="color: var(--text-muted);">${bits.join(' · ')}${err}</div>`;
    }

    // The user's own ratings of this agent's answers — a PREFERENCE axis, published
    // beside the quality reading and never inside it (Rule 3bb: intervention, quality
    // and preference are three separate axes). Renders NOTHING at zero votes: an
    // honest absence, not a decorative zero.
    //
    // The disagreement count is the reason this line exists — a turn the scorers
    // passed that the user rejected (or the reverse) is the only place a human says
    // the quality bands are wrong for this deployment.
    function voteNote(o) {
        const up = (o && o.upvotes) || 0;
        const down = (o && o.downvotes) || 0;
        if (!up && !down) return '';
        const bits = [];
        if (up) bits.push(`${up} up`);
        if (down) bits.push(`${down} down`);
        const dis = (o && o.vote_disagreements) || 0;
        const disPart = dis
            ? `<span class="u-tip" data-tip="Turns where the user's vote and the scored verdict point in opposite directions —&#10;the strongest calibration signal for the quality bands (Config → Trust → Quality bands).&#10;A vote never changes a verdict; it is recorded beside it."> · ${dis} disagree${dis === 1 ? 's' : ''} with the scorecard</span>`
            : '';
        return `<div class="text-xs mt-1" style="color: var(--text-muted);">`
            + `<span class="u-tip" data-tip="The user's own rating of these answers. A preference, not a measurement —&#10;it is never folded into the pass rate above.">user feedback — ${bits.join(' · ')}</span>${disPart}</div>`;
    }

    // pass/fail micro-bars (binary series, oldest→newest) — ONE bar per turn.
    //
    // Rule 9.4 is already satisfied here without help: a pass is full height and a fail
    // is half height, bottom-aligned, so the state is legible with no hue at all. The
    // height is NOT magnitude — there are exactly two.
    //
    // The fills were raw hex (`#10b981` / `#ef4444`) while the LEGEND three blocks below
    // uses `var(--trust-pass)` / `var(--trust-danger)`. In the light theme those tokens
    // resolve to #059669 / #dc2626, so the key and the marks it is a key for rendered in
    // different colours — the same legend-drift class as the acted-dot wording. Both now
    // read the one vocabulary.
    function sparkBars(series, h = 22) {
        if (!series || !series.length) return `<div class="text-xs" style="color: var(--text-muted);">no live data</div>`;
        const n = series.length, w = Math.max(60, n * 4);
        const bw = w / n;
        const bars = series.map((v, i) =>
            `<rect x="${(i * bw).toFixed(1)}" y="${v ? 2 : h * 0.45}" width="${(bw - 1).toFixed(1)}" height="${v ? h - 4 : h * 0.5}" rx="1" fill="${v ? 'var(--trust-pass, #10b981)' : 'var(--trust-danger, #ef4444)'}" opacity="${v ? 0.85 : 0.9}"></rect>`).join('');
        return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="max-width:100%;">${bars}</svg>`;
    }

    function pill(text, color) {
        // TC-90: color-mix instead of hex-suffix alpha (`${color}1a`), so a var(--trust-*)
        // colour tints correctly in both themes.
        return `<span class="text-xs px-2 py-0.5 rounded-full inline-flex items-center gap-1" style="color:${color}; background:color-mix(in srgb, ${color} 10%, transparent); border:1px solid color-mix(in srgb, ${color} 20%, transparent);"><span style="width:6px;height:6px;border-radius:50%;background:${color};display:inline-block;"></span>${esc(text)}</span>`;
    }

    function $(id) { return document.getElementById(id); }
    function root() { return $('agent-performance-root'); }
    function setKpi(id, v) { const el = $(id); if (el) el.textContent = v; }

    // ── scope (My vs System) ──────────────────────────────────────────────────
    let _scope = 'my';
    // System-scope user focus (admin): null = all users. The roster key for a
    // non-self agent is "<tag>::<uuid>" (tags are only unique per user); own
    // agents keep the plain tag so every existing code path stays identical.
    let _scopeUser = null;
    let _usersMap = {};      // uuid → username (from the dashboard response)
    let _selfUuid = null;

    function splitKey(key) {
        const i = String(key || '').indexOf('::');
        return i < 0 ? { tag: key, user: null }
                     : { tag: String(key).slice(0, i), user: String(key).slice(i + 2) || null };
    }

    function usernameFor(uuid) {
        return _usersMap[uuid] || (uuid ? uuid.slice(0, 8) + '…' : '');
    }

    function _updateUserFilterButton() {
        const btn = $('agent-user-filter');
        if (!btn) return;
        btn.classList.toggle('hidden', _scope !== 'system');
        const label = $('agent-user-filter-label');
        if (label) label.textContent = _scopeUser ? usernameFor(_scopeUser) : 'All users';
        btn.classList.toggle('active', !!_scopeUser);
    }
    let _isAdmin = false;
    let _adminChecked = false;

    async function checkAdmin() {
        if (_adminChecked) return;
        _adminChecked = true;
        try {
            const data = await api('/auth/me/features');
            _isAdmin = (data.features || []).includes('view_all_sessions');
            if (_isAdmin) { const t = $('agent-tab-system'); if (t) t.classList.remove('hidden'); }
        } catch (e) { _isAdmin = false; }
    }

    function setScopeIndicator(dashboard) {
        const el = $('agent-scope-indicator');
        if (!el) return;
        if (_scope === 'system') {
            const uc = dashboard.user_count || 0;
            el.textContent = uc ? `System-wide agent performance · ${uc} user${uc === 1 ? '' : 's'}` : 'System-wide agent performance (all users)';
        } else {
            el.textContent = 'Your agents’ trust, quality & reliability';
        }
    }

    function switchScope(view) {
        if (view === 'system' && !_isAdmin) return;
        if (view === _scope) return;
        _scope = view;
        if (view !== 'system') _scopeUser = null;   // the focus only exists in system scope
        const my = $('agent-tab-my'), sys = $('agent-tab-system');
        if (my) my.classList.toggle('active', view === 'my');
        if (sys) sys.classList.toggle('active', view === 'system');
        _updateUserFilterButton();
        load();
    }

    // ── Topic sections — tab navigation in the LOWER pane (Administration idiom).
    // The Overview (KPI tiles + charts) is NOT a section: it lives permanently in
    // the upper command-center pane, above the perfPanelResizer divider.
    // All sections stay MOUNTED; tabs toggle visibility only. Load-bearing: the
    // Assurance Inbox refreshes in place on live uderia:trust-event DOM events
    // even while its tab is inactive, and cross-section links keep working.
    const SECTION_KEYS = ['circle', 'posture', 'roster', 'inbox', 'experiments', 'calibration', 'evidence'];
    let _inboxBadgeCount = 0;   // pending reviews + unacknowledged stored incidents
    // Derived observations are recomputed from the sealed trace, so they carry no
    // disposition and are deliberately NOT in the badge count (a badge that can
    // never reach zero gets ignored). They must still be VISIBLE: without this the
    // REVIEWED station reported "every layer quiet" while a danger-severity
    // sentinel sat in the inbox.
    let _inboxDerivedCount = 0;
    let _inboxDerivedDanger = 0;
    let _inboxDerivedTop = '';  // highest-severity derived summary, for the station panel
    // Per-agent attribution of the same items, so a FOCUSED station reports that agent's
    // work rather than the estate's. Without it REVIEWED paired a per-agent configuration
    // ("none park") with an estate-wide execution ("3 open") in one value — two different
    // scopes in one pair, which is exactly the mixing this grammar exists to prevent.
    // Shape: { '<tag>': { open, derived, derivedDanger, top, stations } }.
    // `stations` maps station key → count of that agent's derived observations at
    // that station: it drives the ring's acted dot (a mechanism acted in the
    // window — PROP-002). `_inboxUnattributed` is the count of items carrying no
    // profile tag: they belong to the estate and to no agent, and must be NAMED
    // when focused rather than silently dropped.
    let _inboxByTag = {};
    let _inboxUnattributed = 0;

    /** Which STATION an inbox event belongs to — the ONE mapping (PROP-002), used
     *  by the per-agent bucket tally AND the live pulse routing. The partition:
     *  grounding + honesty → GROUNDED; guardrail / firewall / quarantine /
     *  injection → GUARDED; review → REVIEWED; breaker / behavior → TRUSTED.
     *  Mirrors assurance_points.station_of_event (backend) — pinned in agreement
     *  by test_assurance_points, like the STATION_DIMS mirror. */
    function _stationOfEvent(eventType) {
        const t = String(eventType || '').toLowerCase();
        // PROP-002b — a station-shortfall observation names its station in its key
        // (`station_shortfall_<station>::<tag>` — the ::tag suffix scopes the ack).
        if (t.startsWith('station_shortfall_')) {
            const st = t.slice('station_shortfall_'.length).split('::')[0];
            return ['signed', 'grounded', 'guarded', 'bounded', 'scored',
                    'trusted', 'reviewed', 'proven'].includes(st) ? st : 'scored';
        }
        if (/^planner_(case|finding)/.test(t)) return 'signed';   // Planner Health: a removed/acknowledged case moves an ingredient's signature
        if (/review/.test(t)) return 'reviewed';
        if (/breaker|trust_behavior/.test(t)) return 'trusted';
        if (/ground|honesty|judge_condemnation/.test(t)) return 'grounded';
        if (/surface|policy/.test(t)) return 'bounded';   // E5: a data-surface breach is an AUTHORITY event; the OBDA policy's observations too
        return 'guarded';   // quarantine / defuse / ingest / injection / sentinel / exfil
    }

    // PROP-002b — the per-station reporting levels, served on the inbox payload
    // (operator-assigned from the ONE severity construct; danger always surfaces).
    let _stationReporting = {};
    // The inbox's severity VIEW filter (operator gesture, session-scoped): null =
    // everything; else one construct level. Presentation only — never a query.
    let _inboxSevFilter = null;
    // …and the agent VIEW filter (a tag, or null = all agents). Same contract.
    let _inboxAgentFilter = null;
    // The review ids currently SHOWN under the active filters — the exact set a
    // bulk disposition acts on (never a server-side "all": what you see is what
    // you approve).
    let _shownReviewIds = [];

    /** The IFOC class colour for an agent tag — resolved from the loaded profiles
     *  through the ONE `IFOC` map above (never a second colour source). Unknown
     *  tag / profiles not yet loaded → the neutral accent. */
    function _agentIfocColor(tag) {
        try {
            const t = String(tag || '').replace(/^@/, '');
            const p = (_profilesRaw || []).find((x) => x && x.tag === t);
            // The CLASS colour, per the rule `profileColors.js` states: "every place
            // that presents a profile by name/tag should colour it by its profile_type
            // using these helpers". A profile's own `color` field is seeded from it and
            // agrees today; preferring the field would make this surface the one place
            // that can disagree with the declared palette.
            return ifoc(p && p.profile_type).color;
        } catch (_) { return 'var(--teradata-orange)'; }
    }

    /** An @TAG, rendered by the ONE builder that renders every @TAG in Uderia.
     *
     *  `ui.js:renderProfileTag` owns the chip: its markup, its three CSS variables and
     *  its hover colour are one design. This lens printed the tag as bare coloured text,
     *  which is not the platform's tag — and the first fix for that hand-rolled a second
     *  copy of the chip here, which is the drift this codebase keeps paying for (~10
     *  copies already exist in ui.js alone).
     *
     *  Absent builder ⇒ the plain tag, degraded and honest. NOT a local styled copy:
     *  a fallback that reproduces the design is the duplication wearing a safety net.
     */
    function _agentTagChip(tag) {
        const t = String(tag || '').replace(/^@/, '');
        if (!t) return '';
        const p = (_profilesRaw || []).find((x) => x && x.tag === t);
        const build = (typeof window !== 'undefined' && window.renderProfileTag) || null;
        return build ? build(esc(t), p && p.profile_type)
                     : `<span>@${esc(t)}</span>`;
    }

    function _sevSurfaces(sev, station) {
        const TG = (typeof globalThis !== 'undefined' && globalThis.TrustGrammar) || null;
        if (TG && TG.severitySurfaces) return TG.severitySurfaces(sev, station, _stationReporting);
        return sev !== 'info';   // fallback: today's de facto (warning+)
    }
    let _rosterAttention = 0;   // agents below the green floor (review + danger)

    function showSection(key, opts) {
        if (!SECTION_KEYS.includes(key)) key = 'circle';
        // PROP-009 — the Evidence tab is rendered by the Ontology Suite's evidenceView.js;
        // without it there is no tab and no section to show.
        if (key === 'evidence' && !window.EvidenceView) key = 'circle';
        SECTION_KEYS.forEach((k) => {
            const el = $('agent-section-' + k);
            if (el) el.classList.toggle('hidden', k !== key);
        });
        document.querySelectorAll('#agent-section-tabs .agent-section-tab').forEach((b) =>
            b.classList.toggle('active', b.dataset.section === key));
        const scroller = document.getElementById('agent-section-' + key);
        const sc = scroller && scroller.closest('.overflow-y-auto');
        if (sc) sc.scrollTop = 0;
        if (key === 'evidence' && window.EvidenceView && scroller) {
            window.EvidenceView.mountEstate(scroller, { filter: (opts && opts.filter) || undefined });
        }
    }

    // Triage counts stay visible on the tabs even when the section is inactive.
    function updateTabBadges() {
        const set = (id, n) => {
            const el = $(id);
            if (!el) return;
            el.textContent = n > 0 ? String(n) : '';
            el.classList.toggle('hidden', !(n > 0));
        };
        set('agent-tabbadge-inbox', _inboxBadgeCount);
        set('agent-tabbadge-roster', _rosterAttention);
    }

    // ── data model ─────────────────────────────────────────────────────────────
    let _model = { agents: [], dashboard: {}, runs: [] };
    // The trust-trip lens (docs/roadmap/TRUST_TRIP_RESET.md): 'total' = the whole
    // record (the odometer, the default), 'trip' = each agent since its own last
    // reset (the current leg). Persisted — safe ONLY because every trip-scoped
    // reading discloses its own boundary (W3: the leg chip + "since …" windows),
    // so a returning user can never mistake which lens they are on.
    // Declared OUTSIDE the grammar-gate extraction span (the gate stubs it).
    let _circleLens = 'total';
    try {
        if (typeof localStorage !== 'undefined'
            && localStorage.getItem('tda_trust_lens') === 'trip') _circleLens = 'trip';
    } catch (_) { /* noop */ }
    // The roster's per-agent leg filter (trust trip): roster keys whose expanded
    // detail reads "this leg". Deliberately independent of the ring's `_circleLens`
    // — each surface carries its OWN disclosure chips (contract §10.2), so each
    // owns its lens. Session-scoped (not persisted): a roster filter is a working
    // gesture, not a standing preference.
    const _rosterLegOn = new Set();
    let _rosterFilter = 'all';   // 'all' | IFOC family label
    let _rosterSearch = '';
    let _expandedTag = null;     // tag of the agent whose detail is expanded inline in the roster
    let _trustBehavior = null;   // /evals/agents/trust-behavior payload (null = feature off / unavailable)
    let _profilesRaw = [];       // full profile dicts (posture matrix reads config blocks)
    let _trustAgentsFull = null; // /trust/agents payload list (integrity per agent; null = suite absent)
    let _actActivity = null;     // /actions/activity by_agent (OBDA-ACT; null = off, older backend, or system scope)

    // IFOC family (normalizes conversation_with_tools → Ideate) for the filter pills.
    const FAMILY = {
        llm_only: 'Ideate', conversation_with_tools: 'Ideate',
        rag_focused: 'Focus', tool_enabled: 'Optimize', genie: 'Coordinate',
        flow: 'Flows',
    };
    const FAMILY_ORDER = ['Ideate', 'Focus', 'Optimize', 'Coordinate', 'Flows', 'Other'];
    // DERIVED from the single source, not restated: this is the same IFOC palette as
    // `ifoc()`, keyed by the family LABEL the roster filter groups by. Written out, it
    // was a second copy in this one file — and the keying by label is exactly why it
    // survived a search for the copy keyed by type.
    const FAMILY_COLOR = (() => {
        const out = { Flows: FLOW_COLOR, Other: 'var(--teradata-orange)' };
        for (const t of Object.keys(FAMILY)) {
            const fam = FAMILY[t];
            if (out[fam]) continue;
            const c = ifoc(t).color;
            if (c) out[fam] = c;
        }
        return out;
    })();
    const family = (t) => FAMILY[t] || 'Other';

    function filteredAgents() {
        const q = _rosterSearch.trim().toLowerCase();
        return _model.agents.filter((a) => {
            if (_rosterFilter !== 'all' && family(a.type) !== _rosterFilter) return false;
            if (q && !((a.tag || '').toLowerCase().includes(q) || (a.name || '').toLowerCase().includes(q)
                       || (a.username || '').toLowerCase().includes(q))) return false;
            return true;
        });
    }

    function classifyAgent(a) {
        if (a.online && a.online.turns) return healthClassFor(a.online.pass_rate);
        if (a.offlinePassK != null) return healthClassFor(a.offlinePassK);
        return 'unevaluated';
    }

    function buildModel(profiles, dashboard, runs, scope, flows) {
        const byAgent = dashboard.by_agent || {};
        const byFlow = dashboard.by_flow || {};
        // latest offline pass^k per profile-class (runs are newest-first)
        const offlineByClass = {};
        for (const r of runs) {
            const pc = (r.summary && r.summary.per_profile_class) || {};
            for (const [cls, pk] of Object.entries(pc)) {
                if (!(cls in offlineByClass)) offlineByClass[cls] = pk;
            }
        }
        const byTag = {};
        const idToTag = {};
        profiles.forEach((p) => { byTag[p.tag] = p; if (p.id) idToTag[p.id] = p.tag; });
        const selfUuid = dashboard.self_uuid || null;
        const usersMap = dashboard.users || {};
        const passK = (type) => (type && type in offlineByClass) ? offlineByClass[type] : null;
        let agents;
        if (scope === 'system') {
            // System scope: by_agent cells are keyed "<tag>::<uuid>" — profile
            // tags are only unique PER USER, so each user's same-tag agent gets
            // its OWN card (with a user pill) instead of silently merging.
            // Own agents keep the plain tag as their roster key so every
            // existing detail/compare/runs path behaves exactly as in my-scope.
            const seen = new Set();
            agents = profiles.map((p) => {
                seen.add(p.tag + '::' + (selfUuid || ''));
                const online = byAgent[p.tag + '::' + (selfUuid || '')] || byAgent[p.tag + '::'] || null;
                const type = p.profile_type || (online && online.profile_class) || null;
                return { id: p.id, tag: p.tag, key: p.tag, name: p.name || p.tag, type,
                         color: p.color || ifoc(type).color, online,
                         user_uuid: selfUuid, username: null, isSelf: true,
                         offlinePassK: passK(type) };
            });
            Object.entries(byAgent).forEach(([key, cell]) => {
                const tag = cell.tag || String(key).split('::')[0];
                const uuid = cell.user_uuid || null;
                if (uuid && uuid === selfUuid) return;          // own — covered above
                if (!uuid && byTag[tag]) return;                // legacy no-uuid span of an own tag
                const k = tag + '::' + (uuid || '');
                if (seen.has(k)) return;
                seen.add(k);
                const type = cell.profile_class || null;
                agents.push({ id: null, tag, key: uuid ? k : tag, name: tag, type,
                              color: ifoc(type).color, online: cell,
                              user_uuid: uuid, username: usersMap[uuid] || null, isSelf: false,
                              offlinePassK: passK(type) });
            });
        } else {
            agents = profiles.map((p) => {
                const online = byAgent[p.tag] || null;
                const type = p.profile_type || (online && online.profile_class) || null;
                return { id: p.id, tag: p.tag, key: p.tag, name: p.name || p.tag, type,
                         color: p.color || ifoc(type).color, online, isSelf: true,
                         offlinePassK: passK(type) };
            });
        }
        // Flow-agents: a flow is its own agent (may have 0 or many profiles), so it
        // gets its own card keyed on flow identity, never folded into a profile.
        (flows || []).forEach((f) => {
            const fid = f.flow_id || f.id;
            if (!fid) return;
            const online = byFlow[fid] || null;
            // A flow composes 0..N profiles (flow-level default + per-node overrides);
            // resolve each ref to its tag for the card's "uses @X · @Y" line.
            const def = f.definition || {};
            const refs = [];
            const dp = (def.settings || {}).defaultProfileId;
            if (dp) refs.push(dp);
            (def.nodes || []).forEach((n) => { const pid = (n.data || {}).profile_id; if (pid) refs.push(pid); });
            const uses = [];
            refs.forEach((r) => {
                const tag = (idToTag[r] || String(r).replace(/^@/, '')).trim();
                if (tag && !uses.includes(tag)) uses.push(tag);
            });
            agents.push({
                id: fid, tag: f.name || fid, key: f.name || fid, name: f.name || fid,
                type: 'flow', kind: 'flow', color: FLOW_COLOR,   // single-sourced flow identity
                user_uuid: (online && online.user_uuid) || null,
                username: online && online.user_uuid ? (usersMap[online.user_uuid] || null) : null,
                isSelf: !online || !online.user_uuid || online.user_uuid === selfUuid,
                online, offlinePassK: null, usesProfiles: uses,
            });
        });

        agents.forEach((a) => { a.status = classifyAgent(a); });
        // worst health surfaces up: danger → review → healthy → inactive, then name
        const rank = { danger: 0, review: 1, healthy: 2, unevaluated: 3 };
        agents.sort((x, y) => (rank[x.status] - rank[y.status]) || x.tag.localeCompare(y.tag));
        return { agents, dashboard, runs };
    }

    // ── KPIs ────────────────────────────────────────────────────────────────────
    function renderKpis(m) {
        const evaluated = m.agents.filter((a) => a.status !== 'unevaluated').length;
        setKpi('agent-kpi-evaluated', evaluated);
        setKpi('agent-kpi-healthy', m.agents.filter((a) => a.status === 'healthy').length);
        // "Attention" counter now = review + danger (everything below the green floor)
        setKpi('agent-kpi-attention',
            m.agents.filter((a) => a.status === 'review' || a.status === 'danger').length);
        // Live Turns Scored · Gating Failures read the SAME population and predicate as
        // the Quality Trend card beneath them (`agent_kpi_dashboard.overall`: measured,
        // non-flow turns in the one window; failed = `turn_failed`). They used to read the
        // roster endpoint's raw span count under a different window and a frozen scalar,
        // so "496 scored · 80 failures" sat above "991 turns · 286 failed".
        const ov = (m.kpi && m.kpi.overall) || null;
        setKpi('agent-kpi-online', ov ? (ov.turns || 0) : (m.dashboard.total_spans || 0));
        setKpi('agent-kpi-failures', ov ? (ov.fail_turns || 0) : (m.dashboard.spans_with_failure || 0));
        const status = $('agent-online-status');
        if (status) {
            const on = m.dashboard.enabled;
            status.textContent = on ? 'Live scoring: on' : 'Live scoring: off';
            status.style.color = on ? 'var(--trust-pass)' : 'var(--text-muted)';
            status.style.borderColor = on ? 'color-mix(in srgb, var(--trust-pass) 33%, transparent)' : 'var(--border-secondary)';
            status.classList.add('u-tip', 'u-tip-right');
            status.setAttribute('data-tip', on ? 'Online evaluation is tail-sampling live turns.'
                : 'Enable APP_CONFIG.ONLINE_EVAL_ENABLED to score live turns.');
        }
    }

    // ── Behavioral Trust mark: seal (integrity) wrapped by a ring (conduct) ─────
    // Two visual channels on one glyph: the RING fill = the recency-weighted
    // behavioral score (color = composite state); the CENTER seal = Trust-Membrane
    // integrity (signed / changed / unsigned / unknown). Integrity can only cap,
    // never promote — mirroring eval/trust_behavior.compose().
    const TRUST_STATE_COLOR = {
        trusted: 'var(--trust-pass, #34d399)', proven: 'var(--trust-pass, #34d399)',
        watch: 'var(--trust-warn, #fbbf24)', at_risk: 'var(--trust-danger, #f87171)',
        provisional: 'var(--text-muted, #94a3b8)',
    };
    const TRUST_STATE_LABEL = {
        trusted: 'Trusted', proven: 'Proven', watch: 'Watch',
        at_risk: 'At risk', provisional: 'Provisional',
    };
    function trustMark(entry) {
        if (!entry) return '';
        const comp = entry.composite || {};
        const beh = entry.behavior || {};
        const integ = (entry.integrity || {}).state;
        const state = comp.state || 'provisional';
        const color = TRUST_STATE_COLOR[state] || TRUST_STATE_COLOR.provisional;
        const score = typeof beh.score === 'number' ? Math.max(0, Math.min(1, beh.score)) : 0;
        const R = 8, C = 2 * Math.PI * R;
        const dash = `${(score * C).toFixed(2)} ${C.toFixed(2)}`;
        // TC-46: `deployment_unattested` is publisher-signed (integrity intact) — only THIS
        // instance's deployment is unattested. It must not collapse into the never-signed seal;
        // a publisher-signed agent rendering identically to an unsigned one is a lie of shape.
        const sealColor = integ === 'trusted' ? 'var(--trust-seal-trusted, #34d399)'
            : integ === 'changed' ? 'var(--trust-seal-changed, #f59e0b)'
            : integ === 'deployment_unattested' ? 'var(--trust-seal-unattested, #38bdf8)'
            : 'var(--trust-seal-unsigned, #94a3b8)';
        // Seal glyph: signed = solid check; changed = alert; deployment_unattested = dashed
        // check (signed upstream, not attested here); unsigned/unknown = hollow dot.
        const seal = integ === 'trusted'
            ? `<path d="M7.2 10.2l1.9 1.9 3.4-3.6" stroke="${sealColor}" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/>`
            : integ === 'changed'
                ? `<path d="M10 6.4v4.2M10 13.4h.01" stroke="${sealColor}" stroke-width="1.8" fill="none" stroke-linecap="round"/>`
                : integ === 'deployment_unattested'
                    ? `<path d="M7.2 10.2l1.9 1.9 3.4-3.6" stroke="${sealColor}" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="2.2 1.8"/>`
                    : `<circle cx="10" cy="10" r="2.2" stroke="${sealColor}" stroke-width="1.4" fill="none"/>`;
        // The conduct score is a BLEND (pass rate recency-weighted, plus extra weight
        // on the trust-critical dimensions), so beside the card's raw pass rate it can
        // legitimately read 20 points apart — an unexplained 70% next to a 49% headline
        // was read as a bug. Name the components whenever the payload carries them, so
        // the figure explains its own distance from the card.
        const blend = (beh.score != null && beh.weighted_pass_rate != null
                       && beh.trust_dim_rate != null)
            ? `= overall ${(beh.weighted_pass_rate * 100).toFixed(0)}% (recency-weighted) `
              + `blended with trust-critical checks ${(beh.trust_dim_rate * 100).toFixed(0)}%`
            : '';
        const tipLines = [
            `Behavioral trust: ${TRUST_STATE_LABEL[state] || state}`,
            beh.score != null ? `conduct score ${(beh.score * 100).toFixed(0)}% over ${_plural(beh.turns, 'turn')}${beh.stale ? ' (stale evidence)' : ''}` : `no evaluated turns yet`,
            blend,
            `integrity: ${_integLabel(integ)}`,
            comp.reason ? `(${comp.reason})` : '',
        ].filter(Boolean).join('\n');
        const ringStyle = state === 'provisional' ? `stroke-dasharray:2 3;` : `stroke-dasharray:${dash};`;
        return `<span class="trust-mark" data-tip="${esc(tipLines)}" aria-label="Behavioral trust: ${esc(TRUST_STATE_LABEL[state] || state)}">
            <svg viewBox="0 0 20 20" width="20" height="20">
                <circle cx="10" cy="10" r="${R}" fill="none" stroke="var(--border-primary)" stroke-width="2"/>
                <circle cx="10" cy="10" r="${R}" fill="none" stroke="${color}" stroke-width="2"
                    style="${ringStyle}" stroke-linecap="round" transform="rotate(-90 10 10)"/>
                ${seal}
            </svg></span>`;
    }

    // ── roster ──────────────────────────────────────────────────────────────────
    function agentCard(a, expanded) {
        const cls = ifoc(a.type);
        const hc = HEALTH[a.status] || HEALTH.unevaluated;
        const statusPill = pill(hc.label, hc.color);
        const headline = a.online && a.online.turns ? a.online.pass_rate
            : a.offlinePassK;
        const headlineLabel = a.online && a.online.turns ? 'live pass rate'
            : (a.offlinePassK != null ? 'offline pass^k' : '');
        const big = headline == null
            ? `<div class="text-sm" style="color: var(--text-muted);">No evaluation yet</div>`
            : `<div class="flex items-baseline gap-2"><span class="text-3xl font-bold" style="color: var(--text-primary);">${fmtPct(headline)}</span><span class="text-xs" style="color: var(--text-muted);">${headlineLabel}</span></div>`;
        const spark = a.online && a.online.series && a.online.series.length
            ? `<div class="mt-2">${sparkBars(a.online.series)}</div>` : '';
        // The mechanism axis, on the card too — a reader should not have to expand an
        // agent to learn whether anything ever acted on its answers.
        const interv = a.online ? interventionNote(a.online) : '';
        const isFlow = a.kind === 'flow';
        const liveLabel = isFlow ? 'flow run' : 'live turn';
        // When the agent ran as a genie coordinator's expert for some turns, show
        // the split so a profile's "as expert" activity is honest, not conflated
        // with direct user invocations.
        const expertN = (a.online && a.online.expert_turns) || 0;
        const expertNote = expertN ? ` · ${expertN} as expert` : '';
        const sub = a.online && a.online.turns
            ? `<div class="text-xs mt-1" style="color: var(--text-muted);">${a.online.turns} ${liveLabel}${a.online.turns === 1 ? '' : 's'}${expertNote}${a.online.failures ? ` · ${a.online.failures} failed` : ''}</div>`
            : (a.status === 'unevaluated'
                ? `<div class="text-xs mt-1" style="color: var(--text-muted);">${isFlow ? 'Run the flow or enable live scoring' : 'Run an eval or enable live scoring'}</div>` : '');
        const title = isFlow
            ? `<span class="text-sm font-semibold" style="color: var(--text-primary);">${esc(a.name)}</span>`
            : `<span class="text-sm font-semibold" style="color: var(--text-primary);">@${esc(a.tag)}</span>`;
        let usesLine = '';
        if (isFlow) {
            const u = a.usesProfiles || [];
            usesLine = u.length
                ? `<div class="text-xs mt-1 truncate" style="color: var(--text-muted);">uses ${u.slice(0, 4).map((t) => '@' + esc(t)).join(' · ')}${u.length > 4 ? ` +${u.length - 4}` : ''}</div>`
                : `<div class="text-xs mt-1" style="color: var(--text-muted);">no fixed profile</div>`;
        }
        const chevron = `<svg class="h-4 w-4 transition-transform" style="color: var(--text-muted); transform: rotate(${expanded ? 180 : 0}deg);" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" /></svg>`;
        const k = a.key || a.tag;
        const selBox = _compareMode
            ? `<span class="ap-sel"><svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="#fff" stroke-width="3" style="opacity:${_compareSet.has(k) ? 1 : 0};transition:opacity .15s"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg></span>` : '';
        const cmpCls = _compareMode ? ` cmp-on${_compareSet.has(k) ? ' cmp-sel' : ''}` : '';
        // Owner attribution — rendered only in the cross-user (system) view.
        const userPill = (!a.isSelf && (a.username || a.user_uuid))
            ? `<span class="text-xs px-2 py-0.5 rounded-full u-tip" data-tip="This agent belongs to another user (admin system view)." style="color: var(--text-muted); background: var(--hover-bg-strong, rgba(127,127,127,.10)); border: 1px solid var(--border-secondary);">
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="display:inline-block;vertical-align:-1px;margin-right:3px;"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>${esc(a.username || (a.user_uuid || '').slice(0, 8) + '…')}</span>`
            : '';
        return `<button class="agent-card${cmpCls} text-left rounded-xl p-4 border transition-all w-full" data-agent-tag="${esc(k)}" aria-expanded="${expanded ? 'true' : 'false'}"
            style="position:relative; background-color: var(--card-bg); border-color: ${expanded ? cls.color : 'var(--border-primary)'}; border-left: 3px solid ${cls.color};${expanded ? ' box-shadow: 0 0 0 1px ' + cls.color + '55;' : ''}"
            onmouseover="this.style.borderColor='${cls.color}80'; this.style.borderLeftColor='${cls.color}';"
            onmouseout="this.style.borderColor='${expanded ? cls.color : 'var(--border-primary)'}'; this.style.borderLeftColor='${cls.color}';">
            ${selBox}
            <div class="flex items-center justify-between mb-2">
                <div class="flex items-center gap-2">
                    ${title}
                    <span class="text-xs px-2 py-0.5 rounded-full" style="color:${cls.color}; background:${cls.color}1a; border:1px solid ${cls.color}33;">${esc(cls.label)}</span>
                    ${userPill}
                </div>
                <div class="flex items-center gap-2">${(!a.kind && a.isSelf !== false) ? _stationRing(a.tag, 20) : ''}${(!a.kind && a.isSelf !== false && _trustBehavior && _trustBehavior.agents) ? trustMark(_tbAgent(a.key, a.tag)) : ''}${statusPill}${chevron}</div>
            </div>
            ${big}${spark}${sub}${interv}${usesLine}
        </button>`;
    }

    const ROSTER_ICON = 'M17 20h5v-2a4 4 0 00-3-3.87M9 20H4v-2a4 4 0 013-3.87m6-2.13a4 4 0 10-4-4 4 4 0 004 4z';

    function filterPills() {
        const present = [];
        FAMILY_ORDER.forEach((f) => { if (_model.agents.some((a) => family(a.type) === f)) present.push(f); });
        const pill = (label, value) => {
            const active = _rosterFilter === value;
            const c = value === 'all' ? 'var(--teradata-orange)' : (FAMILY_COLOR[value] || 'var(--teradata-orange)');
            const style = active
                ? `color:${c}; background:${c === 'var(--teradata-orange)' ? 'rgba(241,95,34,0.16)' : c + '26'}; border:1px solid ${c === 'var(--teradata-orange)' ? 'rgba(241,95,34,0.5)' : c + '80'};`
                : `color: var(--text-muted); background: transparent; border:1px solid var(--border-secondary);`;
            return `<button class="agent-filter-pill text-xs px-3 py-1 rounded-full transition-all" data-roster-filter="${value}" style="${style}">${label}</button>`;
        };
        return `<div class="flex flex-wrap items-center gap-1.5 mb-3">${pill('All', 'all')}${present.map((f) => pill(f, f)).join('')}</div>`;
    }

    function rosterGrid() {
        const list = filteredAgents();
        if (!list.length) {
            return `<div class="text-sm py-8 text-center" style="color: var(--text-muted);">No agents match this filter.</div>`;
        }
        // Clicking a card expands its detail INLINE as a full-width row right after it
        // (grid-column 1/-1) — never buried at the bottom of the roster.
        const cells = list.map((a) => {
            const k = a.key || a.tag;
            let html = agentCard(a, _expandedTag === k);
            if (_expandedTag && k === _expandedTag) {
                html += `<div class="agent-detail-inline" style="grid-column: 1 / -1;">${agentDetailHtml(a)}</div>`;
            }
            return html;
        }).join('');
        return `<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3" style="max-height: 60vh; overflow-y: auto; padding-right: 4px;">${cells}</div>`;
    }

    function compareToolbar() {
        return `<div class="flex items-center justify-between mb-3 flex-wrap gap-2">
            <div class="flex-1">${filterPills()}</div>
            <button data-agent-action="toggle-compare" class="text-xs px-3 py-1 rounded-full transition-all u-tip u-tip-right" data-tip="Select agents to compare side by side"
                style="${_compareMode ? 'color:var(--teradata-orange);background:rgba(241,95,34,.12);border:1px solid rgba(241,95,34,.5);' : 'color:var(--text-muted);background:transparent;border:1px solid var(--border-secondary);'}">
                ${_compareMode
                    ? '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="display:inline-block;vertical-align:-1px"><path d="M20 6L9 17l-5-5"/></svg> Done selecting'
                    : '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:inline-block;vertical-align:-1px"><path d="M17 4l3 3-3 3M20 7H8M7 20l-3-3 3-3M4 17h12"/></svg> Compare agents'}</button>
        </div>${_compareMode ? `<div class="text-[11px] mb-2 ap-fade" style="color:var(--text-muted)">Select 2+ agents to compare their scores side by side.</div>` : ''}`;
    }
    function rosterBody() { return compareToolbar() + `<div id="agent-roster-grid">${rosterGrid()}</div>`; }

    function renderRoster(m) {
        if (!m.agents.length) {
            return sectionCard('Agent Roster', ROSTER_ICON,
                `<div class="text-sm py-6 text-center" style="color: var(--text-muted);">No profiles found. Create a profile in Anatomy → Agent Profiles to evaluate an agent.</div>`);
        }
        const search = `<input id="agent-roster-search" type="text" placeholder="Search agents…" value="${esc(_rosterSearch)}"
            class="text-xs rounded px-2 py-1 focus:border-teradata-orange focus:outline-none" style="background: var(--input-bg); color: var(--text-primary); border: 1px solid var(--border-primary); width: 180px;">
            <span id="agent-roster-count" class="text-xs" style="color: var(--text-muted);">${m.agents.length}</span>`;
        return sectionCard('Agent Roster', ROSTER_ICON, `<div id="agent-roster-body">${rosterBody()}</div>`, search);
    }

    function applyRosterFilter() {
        const body = $('agent-roster-body');
        if (body) body.innerHTML = rosterBody();
        const count = $('agent-roster-count');
        if (count) {
            const n = filteredAgents().length, total = _model.agents.length;
            count.textContent = (_rosterFilter === 'all' && !_rosterSearch.trim()) ? `${total}` : `${n} / ${total}`;
        }
    }

    /** Fill an expanded detail's ASYNC sections — the Runs Ledger (every score per
     *  run), the timeline, and Trust History. The detail's static html renders their
     *  containers EMPTY; whoever (re)renders that html must call this, or the
     *  per-turn execution rows silently vanish. That is exactly the defect this
     *  centralizes away: `load()` re-renders the roster (a reload can land while an
     *  agent is expanded — a race with the first expand, or the trip-reset flow),
     *  but only the card click ran the fillers — so the ledger showed nothing until
     *  the user closed and reopened the agent. ONE filler, called from every
     *  render path, so the sections can never be orphaned again. */
    function _fillExpandedDetail(tag) {
        const a = _model.agents.find((x) => (x.key || x.tag) === tag);
        if (!a || a.kind === 'flow') return;
        if (window.agentKpiCharts) renderAgentTimeline(tag);
        // Trust history is the requester's own audit surface — skip for other
        // users' agents (their sign-offs/incidents are not this user's records).
        if (a.isSelf !== false) renderTrustHistory(tag);
        // Runs Ledger — every score per run (+ master/slave trace for coordinators).
        loadRunsLedger(tag);
    }

    /** Toggle the leg lens for one expanded agent, then re-render the WHOLE detail
     *  (the html carries the bars + work mix; the async sections re-fill from their
     *  caches) — one gesture, every section on the same window, each with its own
     *  disclosure chip. */
    function _toggleRosterLeg(tag) {
        if (_rosterLegOn.has(tag)) _rosterLegOn.delete(tag); else _rosterLegOn.add(tag);
        applyRosterFilter();
        _fillExpandedDetail(tag);
    }

    // Click a roster card → expand its detail inline (toggle). Re-renders the roster
    // body so the full-width detail row injects right after the clicked card.
    function toggleAgentDetail(tag) {
        // `tag` is the roster KEY (plain tag for own agents, "tag::uuid" for
        // another user's agent in the admin system view).
        _expandedTag = (_expandedTag === tag) ? null : tag;
        applyRosterFilter();
        if (!_expandedTag) return;
        _fillExpandedDetail(tag);
        const panel = root() && root().querySelector('.agent-detail-inline');
        if (panel) panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    // ── agent detail ────────────────────────────────────────────────────────────
    // Task-class palette (aligns with the Live Status / IFOC accents): the work-mix
    // transparency for the Agent-performance lens — consistent across all classes + flows.
    // hex-palette: work-mix task CATEGORIES (series identity, not trust state — `#10b981`
    // here is "reasoning", not "pass"). A fixed categorical palette, theme-invariant by design.
    const TASK_COLORS = { planning: '#a855f7', retrieval: '#3b82f6', tool: '#f59e0b',   // hex-palette
                          reasoning: '#10b981', coordination: '#F15F22', correction: '#ef4444' };  // hex-palette
    const TASK_ORDER = ['planning', 'retrieval', 'tool', 'reasoning', 'coordination', 'correction'];
    function taskMix(comp, unit) {
        const entries = TASK_ORDER.filter((c) => comp && comp[c]).map((c) => [c, comp[c]]);
        if (!entries.length) return `<div class="text-xs" style="color: var(--text-muted);">No task data yet.</div>`;
        const total = entries.reduce((s, [, n]) => s + n, 0);
        const seg = entries.map(([c, n]) =>
            `<div class="u-tip" data-tip="${n} ${c}" style="width:${(n / total * 100).toFixed(1)}%; background:${TASK_COLORS[c]}; height:100%;"></div>`).join('');
        const legend = entries.map(([c, n]) =>
            `<span class="inline-flex items-center gap-1 text-[11px]" style="color: var(--text-muted);"><span style="width:8px;height:8px;border-radius:2px;background:${TASK_COLORS[c]};display:inline-block;"></span>${n} ${c}</span>`).join(' ');
        return `<div class="flex items-baseline gap-2 mb-2"><span class="text-2xl font-bold" style="color: var(--text-primary);">${total.toFixed(1)}</span><span class="text-xs" style="color: var(--text-muted);">avg tasks / ${unit === 'runs' ? 'run' : 'turn'}</span></div>
            <div class="flex rounded overflow-hidden mb-2" style="height:10px; border:1px solid var(--border-secondary);">${seg}</div>
            <div class="flex flex-wrap gap-x-3 gap-y-1">${legend}</div>`;
    }

    // ── the measurement PROCESS MODEL, applied to the roster's dimension surfaces ──
    // ONE grouping for the detail bars, the runs-ledger verdict chips, the per-run band
    // strip and the compare heatmap: dimensions group by the STAGE OF THE TURN they
    // measure, in the order the turn ran (eval/stages.py, served through the assurance
    // vocabulary — this file declares no taxonomy of its own).
    //
    // It replaced grouping by the STATION that owned a dimension. Ownership ended with
    // the lane cut: nothing owns a measurement now, so station names here kept implying
    // a relationship the code no longer has. A process model also has a canonical ORDER
    // by construction — which is what the band strip's columns and the heatmap's column
    // order actually need, and what the retired scorer tiers never had.
    const _QD = () => (typeof window !== 'undefined' && window.QualityDimensions) || null;
    /** The stage a dimension measures, or '' when the served model has not landed or
     *  does not place it. '' is never a bucket — see `_stageGroupsOf`. */
    function _dimStageOf(d) {
        const q = _QD();
        try { return (q && q.stageOf && q.stageOf(d)) || ''; } catch (_) { return ''; }
    }
    /** Ordered stage groups over a key set — only stages with a measured member render.
     *  This is a breakdown of MEASUREMENTS, not a status surface: the ring above the
     *  detail carries every station's state, and an empty stage here would read as "this
     *  ran and measured nothing" rather than "this agent does not do that".
     *
     *  Before the served model lands, everything returns as ONE unlabelled group — a
     *  flat list, never a confident wrong grouping. */
    function _stageGroupsOf(keys) {
        const q = _QD();
        const all = (keys || []).slice();
        if (!q || !q.groupByStage || !q.stagesReady || !q.stagesReady()) {
            return all.length ? [{ st: '', label: '', keys: all }] : [];
        }
        const groups = q.groupByStage(all).map((g) => ({ st: g.stage, label: g.label.toUpperCase(), keys: g.keys }));
        const placed = new Set(groups.flatMap((g) => g.keys));
        const loose = all.filter((k) => !placed.has(k));
        // Unplaced checks are SHOWN under no heading — a registry gap the backend gate
        // fails on must not vanish from the surface that would reveal it.
        if (loose.length) groups.push({ st: '', label: '', keys: loose });
        return groups;
    }
    /** Stage keys present in a grouping, IN TURN ORDER. Unplaced ('') sorts last so a
     *  registry gap never leads the row. */
    function _stageKeysInOrder(groups) {
        const q = _QD();
        const order = ((q && q.stageOrder && q.stageOrder()) || []).concat(['']);
        return order.filter((st) => groups[st] && groups[st].length);
    }
    /** A stage's user-facing word, upper-cased for a header. '' renders nothing. */
    function _stageLabelOf(st) {
        const q = _QD();
        if (!st) return '';
        return String((q && q.stageLabel && q.stageLabel(st)) || st).toUpperCase();
    }
    /** A stage's one-line description — the header's hover. The header ALSO routes to
     *  the moment's own Guide entry (`stage_<key>`), which is strictly more reach than
     *  the station route it replaced: the old one opened a guarantee that did not
     *  describe how these checks are grouped, the new one opens the moment that does. */
    function _stageDescOf(st) {
        const q = _QD();
        return (st && q && q.stageDesc && q.stageDesc(st)) || '';
    }
    /** A dimension's position in TURN ORDER — the sort key for the band strip's columns
     *  and the compare heatmap. An unplaced dimension sorts last rather than first, so a
     *  registry gap cannot silently lead the row. */
    const _stageIdxOf = (d) => {
        const q = _QD();
        const order = (q && q.stageOrder && q.stageOrder()) || [];
        const i = order.indexOf(_dimStageOf(d));
        return i === -1 ? order.length : i;
    };
    /** The stage header's band chip — how THIS GROUP OF CHECKS did, and nothing more.
     *
     *  ⚠ It is NOT a station reading and must never be read as one. It used to be
     *  exactly that: the same computation the ring's station ran, so the header could
     *  not disagree with the segment above it. A STAGE has no ring segment — the ring
     *  reports guarantees, this reports measurements — so a header sourced from a
     *  station's state would be the meaning loss this whole rework removes, arriving by
     *  the back door.
     *
     *  The computation (per-turn union first, worst-dim fallback, `_execBand`) is kept
     *  because it is the honest reading of "how did these checks do over the window".
     *  Only the claim changed. */
    function _stageHeadChip(o, keys) {
        const cls = { pass: 'tc-pass', warn: 'tc-warn', danger: 'tc-danger' };
        const clean = _turnCleanRate(o, keys);
        if (clean && clean.window) {
            const band = _execBand(clean.clean / clean.window);
            return `<span class="trust-chip ${cls[band] || ''}" style="font-size:9px;padding:1px 6px;">${clean.clean}/${clean.window} turns clean</span>`;
        }
        const w = _worstDim(o, keys);
        if (w) {
            const band = _execBand(w.rate);
            return `<span class="trust-chip ${cls[band] || ''}" style="font-size:9px;padding:1px 6px;">worst check ${Math.round(w.rate * 100)}%</span>`;
        }
        return '';
    }

    function agentDetailHtml(a) {
        const cls = ifoc(a.type);
        const isFlow = a.kind === 'flow';
        // The leg filter (trust trip, contract §10): when active, the detail reads
        // the trip cell — the SAME aggregator's output over the current leg, so
        // every renderer below works on it unchanged. Each scoped section carries
        // the leg chip as its disclosure (10.2); the roster CARD above stays on
        // the whole record (it has no chip — 10.4).
        const trip = !isFlow && a.online && a.online.trip;
        const legOn = !!(trip && _rosterLegOn.has(a.key || a.tag));
        const o = legOn ? trip : (a.online || {});
        const legChip = legOn
            ? `<span class="tc-leg-chip${trip.note ? ' u-tip' : ''}"${trip.note ? ` data-tip="${esc(trip.note)}"` : ''} style="margin-left:8px;">this leg · since ${esc(_legDate(trip.since) || 'recently')}${(trip.leg_n || 1) > 1 ? ` · leg ${trip.leg_n}` : ''}</span>`
            : '';
        const dims = o.dimensions || {};
        const dimCounts = o.dimension_counts || {};
        const dimSeries = o.dimension_series || {};
        // Grouped BY STATION (the partition) — worst-first within a group so the
        // failing checks surface at the top of their own station, never buried
        // alphabetically. The station label routes to its Guide entry (`data-dim`,
        // the one glossary channel); the chip beside it is the ring's own banding.
        const _worstFirst = (x, y) => ((dims[x] == null ? 2 : dims[x]) - (dims[y] == null ? 2 : dims[y]))
            || x.localeCompare(y);
        const dimBars = _stageGroupsOf(Object.keys(dims)).map((g) => `
            <div class="flex items-center gap-2 mt-3 mb-1.5">
                <span class="text-[10px] font-bold" data-dim="station_${g.st}" role="button" tabindex="0"
                    style="color: var(--text-muted); letter-spacing: .08em; cursor: pointer;
                           font-family: var(--font-mono, ui-monospace, monospace);">${g.label}</span>
                ${_stageHeadChip(o, g.keys)}
                <span class="flex-1" style="border-top:1px solid var(--border-secondary); opacity:.5;"></span>
            </div>
            ${g.keys.slice().sort(_worstFirst).map((k) => bar(k, dims[k], HEALTHY, dimCounts[k], o.turns, dimSeries[k])).join('')}`).join('')
            || `<div class="text-xs" style="color: var(--text-muted);">No live dimension data yet for this agent.</div>`;
        // Scope the Assurance Guide to this agent's dimensions + their aggregate health band,
        // so opening it shows exactly what's tracked for this agent and its live status.
        const _agBand = (v) => { if (v == null) return 'na'; const c = healthClassFor(v); return c === 'unevaluated' ? 'na' : c; };
        // Aggregate context: bands from the agent's live pass-rates. The admin fail lines are
        // merged in later (re-stamped in renderRunsLedger, once runs verdicts are loaded), so
        // the guide's "Admin thresholds" toggle works here too — global boundaries, no per-turn
        // measured value. Stored so the re-stamp can preserve the bands + label.
        _agDimCtx = { items: Object.keys(dims).map((k) => ({ key: k, band: _agBand(dims[k]) })),
                      label: `${isFlow ? a.name : '@' + a.tag} · live${legOn ? ' · this leg' : ''}`, cls: a.type };
        const dimCtxAttr = (QD.contextAttrs && _agDimCtx.items.length)
            ? QD.contextAttrs(_agDimCtx.items, _agDimCtx.label, _agDimCtx.cls) : '';
        const offline = a.offlinePassK != null
            ? `<div class="flex items-baseline gap-2"><span class="text-2xl font-bold" style="color: var(--text-primary);">${fmtPct(a.offlinePassK)}</span><span class="text-xs" style="color: var(--text-muted);">latest offline pass^k (${esc(cls.label)}${isFlow ? '' : ' class'})</span></div>`
            : `<div class="text-sm" style="color: var(--text-muted);">No offline run yet. <span style="color: var(--teradata-orange);">Run an eval</span> from Experiments below.</div>`;
        const unit = isFlow ? 'runs' : 'turns';
        const online = o.turns
            ? `<div class="flex items-baseline gap-2 mb-2"><span class="text-2xl font-bold" style="color: var(--text-primary);">${fmtPct(o.pass_rate)}</span><span class="text-xs" style="color: var(--text-muted);">live pass rate · ${o.turns} ${unit} · ${o.failures || 0} failed</span></div>${sparkBars(o.series, 28)}${interventionNote(o)}${voteNote(o)}`
            : `<div class="text-sm" style="color: var(--text-muted);">${legOn
                ? 'No scored ' + unit + ' since the trip reset — the leg is empty, not clean.'
                : `No live-scored ${unit}. ${a.online ? '' : 'Enable live scoring to track this agent from real traffic.'}`}</div>`;
        // TC-83: pass RAW — sectionCard escapes its title once; pre-escaping here rendered
        // a flow named "R&D" as "R&amp;D".
        return sectionCard(`${isFlow ? a.name : '@' + a.tag} — ${cls.label}`,
            'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z',
            `<div class="grid grid-cols-1 lg:grid-cols-2 gap-5">
                <div><div class="text-xs font-medium mb-2" style="color: var(--text-muted);">LIVE QUALITY${legChip}</div>${online}</div>
                <div><div class="text-xs font-medium mb-2" style="color: var(--text-muted);">OFFLINE QUALITY</div>${offline}</div>
            </div>
            <div class="mt-4"><div class="text-xs font-medium mb-2" style="color: var(--text-muted);">WORK MIX — AVG TASK COMPOSITION${legChip}</div>${taskMix(o.task_composition, unit)}</div>
            <div class="mt-4" id="agent-dim-ctx" ${dimCtxAttr}><div class="flex items-center justify-between mb-2"><div class="text-xs font-medium" style="color: var(--text-muted);">LIVE PASS RATE BY DIMENSION${legChip}</div>${guideBtn('What do these mean?')}</div>
                <div class="flex items-center flex-wrap gap-x-3 gap-y-1 mb-2 text-[10px]" style="color: var(--text-muted);">
                    <span class="inline-flex items-center gap-1"><span style="width:9px;height:9px;border-radius:2px;background:var(--trust-pass, #10b981);display:inline-block;"></span>≥80% Healthy</span>
                    <span class="inline-flex items-center gap-1"><span style="width:9px;height:9px;border-radius:2px;background:var(--trust-warn, #f59e0b);display:inline-block;"></span>50–79% Review</span>
                    <span class="inline-flex items-center gap-1"><span style="width:9px;height:9px;border-radius:2px;background:var(--trust-danger, #ef4444);display:inline-block;"></span>&lt;50% Danger</span>
                    <span class="inline-flex items-center gap-1 u-tip u-tip-right" data-tip="Judge dimensions are scored only on sampled turns, so their denominator is smaller than the deterministic ones.\nn = the number of decided turns behind the bar; at n\u22642 a single verdict drives the rate, so the badge turns amber AND the bar is dimmed to 45% \u2014 two views of one fact: the rate is real, the evidence is thin."><span class="text-[9px] px-1 rounded" style="color:var(--text-muted); background:var(--hover-bg-strong, rgba(127,127,127,0.12));">n=11</span> fewer turns than scored · <span class="text-[9px] px-1 rounded" style="color:var(--text-muted); background:var(--hover-bg-strong, rgba(127,127,127,0.12));">0/1</span> ≤2 verdicts — shown as a fraction, bar dimmed</span>
                    <span class="inline-flex items-center gap-1 u-tip u-tip-right" data-tip="·judge = a model opined; ·metric = a deterministic rule.\nDashes belong to the absence family — provenance rides the word."><span class="text-[9px] px-1.5 rounded" style="border:1px solid var(--border-secondary);">·judge</span> = model-judged</span>
                </div>${dimBars}</div>
            <div id="agent-runs-ledger" class="mt-5"></div>
            <div id="agent-timeline" class="mt-5"></div>
            <div id="agent-trust-history" class="mt-5"></div>`,
            `<button data-agent-action="close-detail" class="text-xs px-2 py-1 rounded" style="color: var(--text-muted); border: 1px solid var(--border-secondary);">Close ✕</button>`);
    }

    async function renderAgentTimeline(tag) {
        let host = $('agent-timeline');
        if (!host) return;
        host.innerHTML = `<div class="text-xs" style="color: var(--text-muted);">Loading timeline…</div>`;
        try {
            const sk = splitKey(tag);
            const d = await api(`/evals/agents/${encodeURIComponent(sk.tag)}/timeline?scope=${_scope}${sk.user ? '&user=' + encodeURIComponent(sk.user) : ''}`);
            // guard: the user may have collapsed/switched agents while this was in flight
            host = $('agent-timeline');
            const cur = host && _expandedTag === tag;
            if (cur) host.innerHTML = window.agentKpiCharts.timelineCard(d || {});
        } catch (e) {
            const h = $('agent-timeline');
            if (h) h.innerHTML = '';
        }
    }

    // ── trust history (the audit-facing changelog for one agent) ────────────────
    async function renderTrustHistory(tag) {
        let host = $('agent-trust-history');
        if (!host) return;
        // TC-76: owner-scoped endpoint, bare-tag lookup — never fetch for a foreign
        // (`tag::uuid`-keyed) agent, or the CALLER's own history renders under another
        // user's agent of the same tag. (The call site also gates on isSelf; this is the
        // belt for any future caller.)
        if (String(tag || '').includes('::')) return;
        try {
            const d = await api(`/assurance/trust-history/${encodeURIComponent(tag)}`);
            host = $('agent-trust-history');
            if (!host || _expandedTag !== tag) return;
            const events = d.timeline || [];
            if (!events.length) {
                host.innerHTML = `<div class="text-xs font-medium mb-2" style="color: var(--text-muted);">TRUST HISTORY</div>
                    <div class="text-xs" style="color: var(--text-muted);">No trust-relevant history recorded for this agent yet (sign-offs, incidents, and review decisions will appear here).</div>`;
                return;
            }
            const kindChip = (e) => {
                const map = { signed: ['tc-pass', 'signed'], incident: [e.severity === 'danger' ? 'tc-danger' : 'tc-warn', e.event_type || 'incident'],
                              review: ['tc-warn', 'awaiting review'], review_decision: [e.decision === 'approved' ? 'tc-pass' : '', e.decision || 'decided'] };
                const [cls, label] = map[e.kind] || ['', e.kind];
                return `<span class="trust-chip ${cls}">${esc(label)}</span>`;
            };
            const rows = events.map((e) => `<div class="flex items-start gap-2 py-1 border-b" style="border-color: var(--border-secondary);">
                ${kindChip(e)}
                <div class="flex-1 min-w-0">
                    <div class="text-xs" style="color: var(--text-primary);">${esc(e.summary || '')}</div>
                    <div class="text-[10px]" style="color: var(--text-muted);">${e.ts ? new Date(e.ts).toLocaleString() : ''}</div>
                </div></div>`).join('');
            host.innerHTML = `<div class="text-xs font-medium mb-2" style="color: var(--text-muted);">TRUST HISTORY
                <span class="normal-case font-normal">— how trust in this agent evolved (sign-offs · incidents · review decisions)</span></div>
                <div style="max-height: 200px; overflow-y: auto;">${rows}</div>`;
        } catch (e) {
            const h = $('agent-trust-history');
            if (h) h.innerHTML = '';
        }
    }

    // ── experiments (runs + diff) ────────────────────────────────────────────────
    function renderExperiments(m) {
        const runs = m.runs || [];
        let inner;
        if (!runs.length) {
            inner = `<div class="text-sm py-4" style="color: var(--text-muted);">No offline experiment runs yet. Curate a golden set and run:
                <code style="color: var(--text-primary);">python -m trusted_data_agent.eval.cli run --dataset core</code></div>`;
        } else {
            const rows = runs.map((r) => {
                const s = r.summary || {}; const pk = s.pass_k;
                return `<tr class="border-b cursor-pointer agent-run-row" data-run-id="${esc(r.id)}" style="border-color: var(--border-secondary);">
                    <td class="py-1.5 text-xs" style="color: var(--text-primary);">${esc(r.id)}</td>
                    <td class="py-1.5 text-xs" style="color: var(--text-muted);">${esc(r.dataset_id)}</td>
                    <td class="py-1.5 text-xs text-center" style="color: var(--text-muted);">${s.n_cases || '—'}</td>
                    <td class="py-1.5 text-xs text-right font-medium" style="color:${HEALTH[healthClassFor(pk)].color};">${fmtPct(pk)}</td>
                </tr>`;
            }).join('');
            const opts = `<option value="">—</option>` + runs.map((r) => `<option value="${esc(r.id)}">${esc(r.id)}</option>`).join('');
            const selStyle = 'background: var(--card-bg); color: var(--text-primary); border: 1px solid var(--border-secondary);';
            inner = `<table class="w-full mb-3"><thead><tr class="text-left">
                    <th class="text-xs font-medium pb-1" style="color: var(--text-muted);">RUN</th>
                    <th class="text-xs font-medium pb-1" style="color: var(--text-muted);">DATASET</th>
                    <th class="text-xs font-medium pb-1 text-center" style="color: var(--text-muted);">CASES</th>
                    <th class="text-xs font-medium pb-1 text-right" style="color: var(--text-muted);">pass^k</th>
                </tr></thead><tbody>${rows}</tbody></table>
                <div id="agent-run-detail" class="mb-3"></div>
                <div class="flex items-center gap-2 flex-wrap pt-2 border-t" style="border-color: var(--border-secondary);">
                    <span class="text-xs" style="color: var(--text-muted);">Diff</span>
                    <select id="agent-diff-base" class="text-xs rounded px-2 py-1" style="${selStyle}">${opts}</select>
                    <span class="text-xs" style="color: var(--text-muted);">vs</span>
                    <select id="agent-diff-cand" class="text-xs rounded px-2 py-1" style="${selStyle}">${opts}</select>
                    <button data-agent-action="run-diff" class="text-xs px-2 py-1 rounded" style="color: var(--text-primary); border: 1px solid var(--teradata-orange);">Compare</button>
                </div><div id="agent-diff-out" class="mt-2"></div>`;
        }
        return sectionCard('Experiments & Datasets', 'M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z', inner);
    }

    async function renderRunDetail(runId) {
        const host = $('agent-run-detail');
        if (!host) return;
        host.innerHTML = `<div class="text-xs" style="color: var(--text-muted);">Loading ${esc(runId)}…</div>`;
        try {
            const [run, cr] = await Promise.all([
                api('/evals/runs/' + encodeURIComponent(runId)),
                api('/evals/runs/' + encodeURIComponent(runId) + '/clusters'),
            ]);
            const s = (run.run || run).summary || run.summary || {};
            const sec = (title, map) => {
                const keys = Object.keys(map || {}).sort();
                if (!keys.length) return '';
                return `<div class="mb-2"><div class="text-xs font-medium mb-1" style="color: var(--text-muted);">${title}</div>${keys.map((k) => bar(k, map[k], HEALTHY)).join('')}</div>`;
            };
            const clusters = (cr.clusters || []).length
                ? (cr.clusters || []).map((c) => `<div class="flex items-center justify-between py-1 border-b" style="border-color: var(--border-secondary);"><span class="text-xs" style="color: var(--text-primary);">${esc(c.dimensions.map(dimLabel).join(' + '))}</span><span class="text-xs" style="color: var(--text-muted);">${c.count}×</span></div>`).join('')
                : `<div class="text-xs" style="color: var(--text-muted);">No failures — clean run.</div>`;
            host.innerHTML = `<div class="rounded-lg p-3" style="background: var(--bg-overlay, rgba(127,127,127,0.06)); border: 1px solid var(--border-secondary);">
                <div class="text-xs font-semibold mb-2" style="color: var(--text-primary);">${esc(runId)} — pass^${s.k || 1} ${fmtPct(s.pass_k)} · rep ${fmtPct(s.rep_pass_rate)}</div>
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    <div>${sec('BY PROFILE CLASS', s.per_profile_class)}${sec('BY COHORT', s.per_cohort_pass_k)}</div>
                    <div>${sec('BY DIMENSION', s.per_dimension)}</div>
                </div>
                <div class="mt-2"><div class="text-xs font-medium mb-1" style="color: var(--text-muted);">FAILURE CLUSTERS</div>${clusters}</div>
            </div>`;
        } catch (e) { host.innerHTML = `<div class="text-xs" style="color: var(--trust-danger);">Failed: ${esc(e.message)}</div>`; }
    }

    async function runDiff() {
        const base = ($('agent-diff-base') || {}).value, cand = ($('agent-diff-cand') || {}).value;
        const out = $('agent-diff-out');
        if (!base || !cand) { out.innerHTML = `<div class="text-xs" style="color: var(--trust-warn);">Pick two runs.</div>`; return; }
        try {
            const { diff } = await api(`/evals/diff?baseline=${encodeURIComponent(base)}&candidate=${encodeURIComponent(cand)}`);
            const pk = diff.pass_k || {};
            const d = (x) => x == null ? '' : pill((x > 0 ? '+' : '') + (x * 100).toFixed(1) + 'pp', x >= 0 ? 'var(--trust-pass)' : 'var(--trust-danger)');
            const list = (arr, label) => arr.length ? `<div class="text-xs font-medium mb-1 mt-2" style="color: var(--text-muted);">${label}</div>` + arr.map((r) => `<div class="flex items-center justify-between py-0.5"><span class="text-xs" style="color: var(--text-primary);" ${dimAttrs(r.dimension)}>${esc(dimLabel(r.dimension))}</span>${d(r.delta)}</div>`).join('') : '';
            out.innerHTML = `<div class="rounded-lg p-3" style="background: var(--bg-overlay, rgba(127,127,127,0.06)); border: 1px solid var(--border-secondary);">
                <div class="flex items-center gap-2 mb-1"><span class="text-xs" style="color: var(--text-muted);">pass^k</span><span class="text-xs" style="color: var(--text-primary);">${fmtPct(pk.baseline)} → ${fmtPct(pk.candidate)}</span>${d(pk.delta)}</div>
                ${list(diff.regressions || [], 'REGRESSIONS')}${list(diff.improvements || [], 'IMPROVEMENTS')}
            </div>`;
        } catch (e) { out.innerHTML = `<div class="text-xs" style="color: var(--trust-danger);">Diff failed: ${esc(e.message)}</div>`; }
    }

    // ── The Trust Circle — model builder + mount (renderer: trustCircle.js) ─────
    // Every station is a LIVE reading assembled from data this lens already
    // loads; the circle fetches nothing and never invents a composite score.
    let _circleAgent = null;   // roster key of the agent in focus (null = whole estate)
    let _pendingCircleTag = null;   // deep-link focus, resolved once the model is loaded

    function _cfgMode(cfg, dflt) {
        const m = (cfg && cfg.mode) || dflt || 'off';
        return ['observe', 'annotate', 'review', 'enforce'].includes(m) ? m : 'off';
    }

    // The station's display label from the ONE registry (W5) — never a hand-written
    // `scored → JUDGED` ternary (there were eight of them).
    function _stationLabel(key) {
        const TGs = (typeof globalThis !== 'undefined' && globalThis.TrustGrammar) || null;
        return (TGs && TGs.stationLabel) ? TGs.stationLabel(key) : String(key || '').toUpperCase();
    }


    // The server-resolved EFFECTIVE mode for one gate (agent-level inheritance already
    // applied, composites preserved, source named) — stamped on GET /v1/profiles as
    // `posture` (the authorityScope precedent). ALWAYS preferred over reading the raw
    // config block: a gate inheriting an agent-level `enforce` has no own mode, and a
    // raw read would show the ring "observe" while the runtime enforces — the
    // two-instruments-one-fact defect. `fallback` covers an older backend only.
    function _gateMode(p, gate, fallback) {
        const g = p && p.posture && p.posture[gate];
        if (g && g.mode) return _cfgMode(g, 'observe');
        return fallback();
    }

    // An ACTING gate withholds/redacts (enforce) or does so AND parks the original (review).
    // `review` ranks above `enforce` on human oversight; both must read as acting everywhere,
    // or the posture matrix and the ring disagree about the same stored mode.
    function _actingMode(m) { return m === 'enforce' || m === 'review'; }

    // TC-8: in admin system scope the evals dashboard spans EVERY user, while the ring's
    // configuration side (own profiles) and SIGNED (own trust) cover only the caller — so an
    // estate reading like `1/4 · 3/12` paired four of YOUR profiles against twelve agents
    // belonging to everyone (the "two scopes in one pair" defect, checklist item 8). The
    // ring's subject is YOUR estate: restrict per-user-keyed execution cells to the caller's
    // own and recompute the two aggregate figures from the surviving cells, so both
    // attributes describe one subject. My-scope payloads (plain-tag keys) pass through
    // untouched — byte-identical baseline.
    function _selfDash(d) {
        const by = (d && d.by_agent) || {};
        const keys = Object.keys(by);
        if (!keys.length || !keys.some((k) => String(k).includes('::'))) return d || {};
        const self = String((d && d.self_uuid) || '');
        const mine = {};
        keys.forEach((k) => {
            const c = by[k] || {};
            const uuid = String(c.user_uuid || String(k).split('::')[1] || '');
            if (uuid === self) mine[k] = c;
        });
        const cells = Object.values(mine);
        return { ...d, by_agent: mine,
                 total_spans: cells.reduce((a, c) => a + (c.turns || 0), 0),
                 spans_with_failure: cells.reduce((a, c) => a + (c.failures || 0), 0) };
    }

    // TC-35: resolve a trust-behavior cell for an agent across both key shapes — my-scope
    // keys cells by plain tag; system-scope keys `tag::uuid` (so two users' same-tag agents
    // never merge). `a.key` from the roster already carries the same shape.
    function _tbAgent(key, tag) {
        const m = _trustBehavior && _trustBehavior.agents;
        if (!m) return null;
        return (key && m[key]) || m[tag]
            || (_trustBehavior.self_uuid ? m[tag + '::' + _trustBehavior.self_uuid] : null)
            || null;
    }

    // ── posture → state / label — ONE map each (trustGrammar, mirrored from ──
    // core/posture_modes.py). A configured mode is a CHOICE, never a finding:
    // enforce/review → pass (nothing owed) · observe/annotate → `watching` (turned
    // on, records, stops nothing — absence family, never amber, never green) ·
    // off → na. The ring, the posture matrix and the anatomy used to hold three
    // copies and disagreed about `observe`; this is the one reader.
    const _TG = (typeof globalThis !== 'undefined' && globalThis.TrustGrammar) || null;
    const _POSTURE_STATE = (_TG && _TG.POSTURE_STATE)
        || { off: 'na', observe: 'watching', annotate: 'watching', enforce: 'pass', review: 'pass' };
    const _POSTURE_LABEL = (_TG && _TG.POSTURE_LABEL)
        || { off: 'off', observe: 'watch', annotate: 'watch + disclose',
             enforce: 'enforce', review: 'enforce + keep for review' };
    function _postureState(m) { return _POSTURE_STATE[m] || 'na'; }
    // The reader's word for a STORED mode: `annotate` / `review` are storage tokens
    // and never reach a reading surface bare (Rule 3.0, W4).
    function _postureLabel(m) { return _POSTURE_LABEL[m] || String(m || ''); }

    // ── posture aggregation — ONE declared policy for every config station ────
    // WORST-OF. An assurance rollup is governed by its weakest member: the
    // strongest agent does not remove another agent's exposure, and "at least one
    // agent is protected" is not a question anyone asks. `warn` is this grammar's
    // own "acted / partial" (see trustCircle's state words) — the honest state for
    // mixed coverage; the station VALUE carries the magnitude ("2/10"), which is a
    // job a single glyph cannot do.
    //
    // `danger` is reserved for a DETECTED discrepancy (e.g. changed after
    // sign-off) or a measured bad outcome — NEVER for a chosen posture. `observe`
    // is the documented default, so grading it red would ship every fresh install
    // in alarm and teach operators to ignore red.
    //
    // Applied by signed · grounded · guarded · bounded. For a single focused agent
    // worst-of and best-of are identical, so per-agent rings never move.
    //
    // `watching` (W1): a mix of protecting and watching members is a POSTURE fact —
    // the value carries the magnitude (`3/10`) — never a finding, so it reads
    // `watching`, not amber. `warn` is reached only by a member that IS a finding
    // (an inert configured protection, an unscoped exposed agent, an unsigned one
    // where signing is practised). Same policy as trustGrammar.postureAggregate.
    function _postureAggregate(states) {
        if (!states.length) return 'na';
        if (states.includes('danger')) return 'danger';
        if (states.includes('warn')) return 'warn';
        if (states.every((s) => s === 'pass')) return 'pass';
        if (states.every((s) => s === 'na')) return 'na';
        return 'watching';
    }

    // ── attention priority — what the glyph COLOUR means ──────────────────────
    // Colour does NOT encode a dimension; it encodes the consumer's actual
    // decision: "does this station need me, and how urgently?" Its semantic is
    // literally "hover here". That is why a composite of design + execution is
    // legitimate here where a composite *verdict* would not be: a colour ramp is
    // only honest over an ordinal scale, and while (design, execution) pairs have
    // no legitimate ordering, attention priority does.
    //
    // The contract that makes it honest: the drill-down MUST name which dimension
    // drove the colour. `_assemble` does that unconditionally, so it cannot be
    // forgotten at a call site.
    const _RANK = { na: 0, watching: 1, pass: 2, warn: 3, danger: 4 };

    function _attention(designState, execState) {
        // Attention first, posture second, absence last. A real attention reading
        // (pass / warn / danger) on either side decides, worst-of; with none, a
        // `watching` side IS the reading (turned on, stops nothing — never hidden
        // behind a dash, never dressed as amber); with neither, the station is not
        // on. `na` on one side neither masks a reading on the other nor invents one:
        // an armed gate with no turns yet is quiet.
        const ATT = { pass: 1, warn: 2, danger: 3 };
        const live = [designState, execState].filter((s) => s && ATT[s]);
        if (live.length) return live.reduce((worst, s) => (ATT[s] > ATT[worst] ? s : worst), 'pass');
        return (designState === 'watching' || execState === 'watching') ? 'watching' : 'na';
    }

    function _driver(designState, execState) {
        const a = _RANK[designState] || 0, b = _RANK[execState] || 0;
        return a === b ? null : (a > b ? 'design' : 'execution');
    }

    // Execution rates share the eval layer's own bands so one number means one
    // thing everywhere in the product.
    function _execBand(rate) {
        if (typeof rate !== 'number') return 'na';
        return rate >= HEALTHY ? 'pass' : rate >= REVIEW ? 'warn' : 'danger';
    }

    /** The worst dimension among `keys` — WITH its identity and its own sample size.
     *
     *  #62 — returning only `min(rate)` discarded whose rate it was, and the stations
     *  then projected it onto `online.turns`: a judge dimension sampled ONCE and failed
     *  rendered `0/18` — zero of eighteen turns claimed unclean when seventeen were
     *  never judged on that dimension at all, while the qualifier on the same panel
     *  (`_dimShortfall`, which has always used the per-dim count) said `0/1`. A figure
     *  must carry the denominator it was measured on.
     *
     *  Ties prefer the larger sample: for the same rate, more evidence decides.
     *  `n` is null when the payload has no count for the dim (older backend) — the
     *  caller falls back to `turns`, which reproduces the old reading exactly.
     */
    function _worstDim(online, keys) {
        const dims = (online && online.dimensions) || {};
        const counts = (online && online.dimension_counts) || {};
        let worst = null;
        for (const k of keys) {
            if (typeof dims[k] !== 'number') continue;
            const n = counts[k] > 0 ? counts[k] : null;
            if (!worst || dims[k] < worst.rate || (dims[k] === worst.rate && (n || 0) > (worst.n || 0))) {
                worst = { key: k, rate: dims[k], n };
            }
        }
        return worst;
    }

    // The SINGLE assembly point for every station. Two attributes in, one station
    // out — so the value token, the four hover slots, the attention colour and the
    // named driver can never diverge between stations.
    // The attention vocabulary is CANONICAL in trustGrammar.js (PROP-002); the
    // literal below is the absence-tolerant fallback, gate-pinned equal to it.
    const _ATTN_WORD = (typeof globalThis !== 'undefined' && globalThis.TrustGrammar && globalThis.TrustGrammar.ATTN_WORD)
        ? Object.assign({}, globalThis.TrustGrammar.ABSENCE_WORD || {}, globalThis.TrustGrammar.ATTN_WORD)
        : { pass: 'quiet', warn: 'worth a look', danger: 'needs you now', na: 'not turned on', watching: 'watching' };

    /** SIGNED's actions, derived from THIS agent's integrity state.
     *
     *  The guided flow (`TrustBadge.signAgentGuided`) already signs every blocking
     *  ingredient bottom-up and then the agent — it existed all along but had no caller
     *  outside its own popover, so the ring could report that sign-off was owed and offer
     *  no way to do it. Absent trust package ⇒ no buttons (absence-tolerance contract).
     */
    function _signActions(rec) {
        if (!rec || !rec.profile_id) return [];
        // `typeof window` — this function is extracted and executed under Node by the
        // grammar gate, where a bare `window` is a ReferenceError, not a falsy value.
        const TB = (typeof window !== 'undefined') && window.TrustBadge;
        if (!(TB && TB.signAgentGuided)) return [];
        const st = rec.state || 'unsigned';
        // Never a button that fails on click: an older trust package without the
        // deployment half offers no attest act, so say nothing rather than offer it.
        if (st === 'deployment_unattested' && !TB.attestDeployment) {
            return [{ label: 'Verify offline', action: 'verify-agent' }];
        }
        if (st === 'trusted') return [{ label: 'Verify offline', action: 'verify-agent' }];
        // The act that is OWED — which differs per state, and the two halves of the trust
        // envelope owe DIFFERENT acts. `deployment_unattested` means the artefact is
        // already publisher-signed and intact; what is missing is this instance's own
        // attestation of the resolved model, tool catalog and connector opt-ins. Offering
        // "Sign this agent" there put the panel's most prominent action on an act that
        // cannot move the state — worse than offering nothing, because it reads as the
        // fix. See core/trust/deployment.py for why the two halves are separate.
        // `changed` routes by its FACE (which half drifted): a deployment drift is
        // moved ONLY by re-attesting the deployment — offering "Re-sign" there put
        // the primary action on an act that cannot move the state (Rule 8.5), which
        // the operator experienced as "re-signing doesn't work". Both-halves drift
        // offers both acts, re-sign first (bottom-up: portable, then this instance).
        const face = _changedFace(rec);
        const out = (st === 'deployment_unattested')
            ? [{ label: 'Attest this deployment', action: 'attest-deployment', primary: true },
               { label: 'Verify offline', action: 'verify-agent' }]
            : (st === 'changed' && face === 'deployment')
            ? (TB.attestDeployment
                ? [{ label: 'Re-attest this deployment', action: 'attest-deployment', primary: true },
                   { label: 'Verify offline', action: 'verify-agent' }]
                : [{ label: 'Verify offline', action: 'verify-agent' }])
            : (st === 'changed')
            ? [{ label: 'Re-sign this agent', action: 'sign-agent', primary: true },
               ...(face === 'both' && TB.attestDeployment
                   ? [{ label: 'Re-attest this deployment', action: 'attest-deployment' }] : []),
               { label: 'Verify offline', action: 'verify-agent' }]
            : [{ label: 'Sign this agent', action: 'sign-agent', primary: true },
               // `unsigned` is the PORTABLE half's word; the deployment half can owe its
               // own act at the same time, and it is the one act a non-signer OWNER can
               // perform. A consumer whose pack agent read `unsigned` (no publisher
               // signature to reinstate yet) was offered only the act they were not
               // permitted to take; attesting the deployment now means the agent reads
               // `trusted` the moment the publisher's signature arrives (Rule 8.5 —
               // every act that is owed, offered).
               ...(_deploymentOwed(rec) && TB.attestDeployment
                   ? [{ label: 'Attest this deployment', action: 'attest-deployment' }] : [])];

        // A route to each BLOCKER. Signing refuses until the blocking ingredient is
        // signable, and the refusal says what to do ("quarantine them or acknowledge each
        // finding") — but offered no way to get there, so the ring reached the sign flow
        // and the sign flow reached a wall with instructions on it. This is the same defect
        // one layer down, so it gets the same answer: a route, not a reimplementation of
        // the ingredient's own surface. Only kinds we can actually open are offered
        // (Rule 4b.3 — never a button that fails on click), and only the first two, so a
        // multi-blocker agent does not become a wall of buttons.
        // #31 — skills and ontologies are routable too: a skill has a sign-off surface
        // (the TrustBadge popover) and an ontology has the Intelligence KG card; naming
        // a blocker with no way to reach it left the reader mid-sentence.
        const ROUTABLE = { knowledge: 1, planner: 1, skill: 1, ontology: 1 };
        (rec.blocking || []).filter((b) => b && ROUTABLE[b.kind]).slice(0, 2).forEach((b) => {
            out.push({ label: `Open ${b.label || `${b.kind} #${b.artifact_id}`}`,
                       action: `open-ingredient:${b.kind}:${b.artifact_id}`,
                       ingredientLabel: b.label || '' });
        });
        return out;
    }

    /** A clean-rate execution VALUE as a percentage. The fraction is NOT dropped — it
     *  moves into the detail (Rule 3.5: a figure carries its denominator; 49% over 2
     *  turns must never read like 49% over 1,000).
     *
     *  FORMAT RULE (operator-settled): per-TURN rates render as % — turns are a
     *  statistical population. Per-AGENT ratios render as x/y — agents are a small
     *  enumerable population acted on individually ("1/9" names an agent; "11% of
     *  agents" is a nonsense at n=9). Apply this before converting any value. */
    const _pctText = (x, y) => (y ? `${Math.round((x / y) * 100)}%` : '');

    function _assemble(key, design, execution, o) {
        const baseState = _attention(design.state, execution.state);
        // PROP-002b — the third attention input: OPEN inbox items attributed to
        // this station (already filtered by the station's reporting level at
        // their one accumulation site). They can only RAISE attention — an open
        // danger item on a healthy-rate station is exactly the "quiet segment
        // beside a danger item" contradiction the fold exists to close. The
        // measurement underneath is never rewritten; deciding/acking the item
        // releases the fold.
        const oi = (o.openItems && (o.openItems.state === 'danger' || o.openItems.state === 'warn')
                    && o.openItems.count > 0) ? o.openItems : null;
        const raised = !!(oi && (_RANK[oi.state] || 0) > (_RANK[baseState] || 0));
        const state = raised ? oi.state : baseState;
        const driver = raised ? 'open items' : _driver(design.state, execution.state);
        // When open items drove the colour, the leading route is the place they
        // are decided (Rule 4b.8); the station's own route survives beneath it
        // (Rule 4b.4 — reordering is honest, deleting is not).
        const baseCta = (driver === 'execution' && o.execCta) ? o.execCta : o.cta;
        const baseCta2 = (o.execCta && o.cta)
            ? ((driver === 'execution') ? o.cta : o.execCta) : undefined;
        const cta = raised ? { label: 'Open the Assurance Inbox', action: 'inbox' } : baseCta;
        const cta2 = raised ? baseCta : baseCta2;
        return {
            key, state, design, execution, driver,
            // Configuration always left, execution always right, `—` never
            // collapsed: a vanishing side is how a config value gets misread as a
            // runtime figure.
            value: `${design.text || '—'} · ${execution.text || '—'}`,
            headline: o.headline,
            // STRUCTURED rows, not pre-formatted strings. The renderer needs the label,
            // the value and the qualifier as separate fields or it cannot lay out a
            // hierarchy — which is how four rows of identical grey prose happened.
            //   k  label        v  the value (the scannable anchor)
            //   d  qualifier    t  'read' (an attribute) | 'reason' (the reasoning)
            // Actions are DERIVED FROM STATE, never hardcoded per station: an unsigned
            // station needs "sign", a changed one needs "re-sign" AND "verify", a quiet one
            // needs "verify". One fixed CTA per station is what sent SIGNED to a surface
            // that cannot sign. Rendered only when the station is pinned.
            actions: o.actions || [],
            // The owed dot (accent, actionability channel) — a station-level FACT the
            // ring renders radially outside the band. Only the assembler can know it:
            // the renderer owns no ladder, and inferring "owed" from the actions list
            // would make every posture-matrix CTA read as outstanding work.
            owed: !!o.owed,
            // Open inbox items attributed here (the count the verdict names when they drove the colour).
            open: oi ? oi.count : 0,
            // The acted dot (PROP-002 as amended: ONE state-coloured dot) — a mechanism ACTED on
            // ≥1 turn in this window (the dial's own mark at ring altitude). Derived
            // from the inbox's per-agent derived observations at the ONE place they
            // are computed (renderAssuranceInbox), never re-derived per station.
            acted: !!o.acted,
            facts: [
                { k: 'Configuration', t: 'read', v: design.text || '—',
                  d: design.detail || (design.text ? '' : 'nothing to configure for this station') },
                { k: 'Execution', t: 'read', v: execution.text || '—',
                  d: execution.detail || (execution.text ? '' : 'no execution data in this window') },
                // The attention word is the chip's job; here it earns its place only by
                // naming the DRIVER — which attribute pulled the colour.
                // #64 (solo sweep) — the driver renders in the panel's own vocabulary:
                // the attribute above it is labelled "Configuration", so the internal
                // token `design` must not leak into the slot.
                // Operator-settled form (PROP-002 follow-up): the driver sits in
                // BRACKETS after the attention word, and the explanation is set off
                // with an em dash — anchor and prose were running together as one
                // phrase ("needs you now · execution enforce and review can…").
                { k: 'Why', t: 'reason', v: driver
                    ? `${_ATTN_WORD[state]} (${({ design: 'configuration' })[driver] || driver})`
                    : _ATTN_WORD[state],
                  d: o.why ? `— ${o.why}` : o.why },
                { k: 'Evidence', t: 'reason', v: '', d: (() => {
                    const base = o.evidence || _NONE;
                    if (!oi) return base;
                    const line = `${_plural(oi.count, 'open inbox item')} attributed to this station — decide or acknowledge in the Assurance Inbox`;
                    return base === _NONE ? line : `${base} · ${line}`;
                })() },
            ],
            // The route must lead where the DRIVER can be investigated (#17). Rule 8.5
            // says the offered ACT must be the act that is owed; the same logic governs
            // the offered ROUTE. Computed above so open items can take the lead
            // (Rule 4b.8) without ever deleting a route (Rule 4b.4).
            cta,
            ...(cta2 ? { cta2 } : {}),
            // Optional per-state glyph (SIGNED only today). Absent ⇒ the renderer keeps
            // the station's own icon, so no station is obliged to supply one.
            ...(o.glyph ? { glyph: o.glyph } : {}),
            // How this station's two figures were DERIVED (the "?" explainer). A separate
            // zone, never a fifth slot — the four slots carry the reading, this carries the
            // method. Built from the SAME variables that produced the figures, so the
            // explanation cannot drift from the number it explains.
            ...(o.derivation ? { derivation: o.derivation } : {}),
        };
    }

    /** The contributing quality dimensions for a station, with their LIVE band — the rows
     *  the "?" explainer makes clickable and hands to the existing Assurance Guide.
     *
     *  Reads the same `agent.online` payload the station's figure came from, so a chip can
     *  never claim a dimension the figure did not actually consider. `applicable` is false
     *  for a dimension this agent's class never produces — shown, but honestly greyed
     *  (silently dropping it would overstate what the station watches). */
    function _dimRows(online, keys) {
        const dims = (online && online.dimensions) || {};
        const counts = (online && online.dimension_counts) || {};
        const total = (online && online.turns) || null;
        return (keys || []).map((k) => {
            const rate = dims[k];
            const measured = typeof rate === 'number';
            const n = counts[k] != null ? counts[k] : null;
            return {
                key: k,
                label: _dimLabel(k),
                applicable: measured,
                // Band mirrors the roster's health taxonomy so a chip agrees with the bar
                // the same dimension draws two sections up.
                band: measured ? (rate >= HEALTHY ? 'healthy' : rate >= REVIEW ? 'review' : 'danger') : 'na',
                score: measured ? rate : null,
                n,
                total,
                // Rule 3.5 — a figure carries its own denominator. A judge dimension is
                // SAMPLED (5% by default), so `0%` can mean "0 of 1 judged turn" while the
                // bar beside it means "0 of 18". Without the denominator those read
                // identically, and the tentative one reads as a catastrophe. The two
                // states (and the LOW_SAMPLE threshold) are decided HERE so the ring chip
                // and the roster bar cannot diverge.
                partial: !!(measured && n != null && total != null && n < total),
                tentative: !!(measured && n != null && n <= LOW_SAMPLE),
            };
        });
    }

    /** One derivation record. `dims` may be empty — a station with no quality-dimension
     *  input must SAY so (four of the eight have none), or the explainer would imply the
     *  ring is a view onto the Quality card, which it is not. */
    // W6 — a derivation row renders only when it carries a LIVE value (a figure, an
    // instance, a named agent); a constant paragraph identical for every agent is
    // doctrine (Rule 3.3) and belongs in the Assurance Guide's station entry, which
    // the explainer routes to. `graded` (W3) is a guarantee station's one line about
    // its measurements — counted at JUDGED, never a chip grid here.
    function _live(text) { return /\d|@\w|: [a-z]/.test(String(text || '')); }
    function _deriv(o) {
        return { window: o.window || '',
                 config: _live(o.config) ? o.config : '',
                 exec: _live(o.exec) ? o.exec : '',
                 dims: o.dims || [], graded: o.graded || null, note: o.note || '' };
    }
    /** How the checks ABOUT a guarantee graded this window — from the same rows the
     *  JUDGED grid draws, reduced to one line: measured · clean · not produced. */
    function _gradedAt(online, keys) {
        const rows = _dimRows(online, keys || []);
        const measured = rows.filter((r) => r.applicable);
        return { total: rows.length, of: measured.length,
                 clean: measured.filter((r) => r.band === 'healthy').length,
                 disputed: 0 };
    }

    // Per-agent integrity → station state. `deployment_unattested` is genuinely
    // PARTIAL (the portable half is signed; this deployment's model/tool-catalog/
    // connector attestation is outstanding), so it reads amber rather than the
    // dashed "not enabled" that would imply nothing is left to do.
    const _INTEGRITY_STATE = {
        trusted: 'pass',
        changed: 'danger',
        deployment_unattested: 'warn',
        unsigned: 'na',            // conditional — see _integrityState()
    };

    // `unsigned` is the one state whose honest reading depends on context, because the
    // four levels conflate two different kinds of absence — and the distinction is not
    // "on vs off" but IS ANYTHING OWED:
    //   * deliberate  — a protection the operator chose not to enable (nothing owed)
    //   * outstanding — an act that is required and undone (something owed)
    // Signing is opt-in by design (badge-not-gate; sharing stays open), so a deployment
    // may legitimately never sign anything. Mapping `unsigned` to amber unconditionally
    // would leave every fresh install permanently amber — the exact "badge that can
    // never go green gets ignored" failure this grammar avoids elsewhere.
    //
    // So "outstanding" is a property of the DEPLOYMENT's practice, not of the agent:
    // once anything has been attested, signing is in use and an unsigned agent is
    // genuinely owed. Before the first signature the practice simply isn't in use.
    // The signal is deliberately deployment-wide, never scoped to the focused agent —
    // otherwise a focused unsigned agent could never read as outstanding.
    //
    // An UNKNOWN wire string stays `na`: it may not be outstanding at all, and
    // inventing attention for a state we cannot interpret would be a guess.
    function _integrityState(state, signingInUse) {
        if (state === 'unsigned') return signingInUse ? 'warn' : 'na';
        return _INTEGRITY_STATE[state] || 'na';
    }

    //: The qualifier beneath the SIGNED value. Three slots on one panel must not say the
    //: same thing three times, so each has a distinct job:
    //:   value     — WHAT it is        ("attested")
    //:   headline  — SO WHAT           (the consequence for the answer)
    //:   qualifier — WHAT WAS RECORDED (the mechanism: which halves are vouched for)
    //: The first version of this map paraphrased the headline almost verbatim — a wire
    //: token traded for a duplicate sentence, the same "adds nothing" defect in different
    //: clothes. Naming the two halves is what is genuinely additive here, and it is
    //: exactly what changes when an operator signs, then attests.
    //: An unknown wire string falls back to the unsigned reading, never the raw token.
    const _SIGNED_MEANS = {
        trusted: 'both halves vouched for — the publisher signature, and this instance’s '
               + 'resolved model, tool catalog and connector opt-ins',
        changed: 'an ingredient, the posture or the declared capability moved since sign-off',
        deployment_unattested: 'the portable half is vouched for; the local half — model, '
               + 'tools, connectors — is not',
        unsigned: 'neither half is vouched for — no publisher signature, no local attestation',
    };

    /** `changed` has TWO faces, one per signable half — and each face owes a
     *  DIFFERENT act. Naming the wrong one is how "re-signing doesn't work"
     *  happens: a deployment drift (the model/tool catalog/connector opt-ins
     *  moved on THIS instance) cannot be moved by a re-sign, only by a
     *  re-attestation of the deployment half. Reads the record's breakdown
     *  (portable_state + deployment) served by /trust/agents; an older payload
     *  without it degrades to the generic reading. */
    function _changedFace(rec) {
        if (!rec || rec.state !== 'changed') return null;
        const depChanged = rec.deployment && rec.deployment.state === 'changed';
        const portableOk = (rec.portable_state || 'changed') === 'trusted';
        if (portableOk && depChanged) return 'deployment';
        if (!portableOk && depChanged) return 'both';
        if (!portableOk) return 'portable';
        return null;   // breakdown absent (older backend) — generic reading
    }

    /** Does the deployment half owe an act of its own? Only when the backend carries the
     *  breakdown and says it is required — an older payload, or an agent with nothing
     *  deployment-bound to attest, owes none. */
    function _deploymentOwed(rec) {
        const d = rec && rec.deployment;
        if (!d || d.required === false) return false;
        return d.state !== 'trusted';
    }

    /** The deployment drift, named — "model changed: X → Y" — capped for a slot. */
    function _driftList(rec) {
        const d = rec && rec.deployment && rec.deployment.drift;
        return (Array.isArray(d) && d.length) ? _briefList(d.map(String), 2) : '';
    }

    /** The Trust Membrane seal for an integrity state, or null for the station default.
     *
     *  Colour is committed to attention priority, so two states at the same attention
     *  level are the same colour by design — correct, but it left `unsigned` and
     *  `deployment_unattested` visually identical while being different acts. The seal
     *  is a SECOND channel: shape carries integrity, colour carries attention, neither
     *  encodes the other. Crucially it is BORROWED from `TrustBadge.META[state].icon` —
     *  the same mark shown on profile cards and marketplace listings — so this fixes a
     *  consistency defect rather than inventing a private vocabulary for the ring.
     *
     *  Absence-tolerance: the trust package is removable (the Ontology Suite), and this
     *  function is also executed under Node by the grammar gate where a bare `window`
     *  is a ReferenceError — both degrade to the station's own glyph.
     */
    function _integrityGlyph(state) {
        if (!state) return null;
        const TB = (typeof window !== 'undefined') && window.TrustBadge;
        const m = TB && TB.META && TB.META[state];
        return (m && m.icon) || null;
    }

    // Behavioral-trust composite → station state, declared once (the counterpart of
    // `_INTEGRITY_STATE` for SIGNED). `provisional` is `warn`, not `na`: it means
    // "not enough evidence yet", which is a real gap in the record rather than a
    // protection the operator chose to leave off — and `na` would let an estate of
    // entirely unproven agents read as "not turned on" while the feature is on.
    const _BEHAVIOR_STATE = {
        trusted: 'pass', proven: 'pass',
        watch: 'warn', provisional: 'warn',
        at_risk: 'danger',
    };

    // I1 — the gate's own record, published on the per-agent cell by
    // `eval/analysis.online_quality_by_agent` from the span attributes
    // `eval/gate_outcome.py` lifts off the turn. Absent when no gate recorded an
    // outcome (masters off, or nothing scored), which is why every read is guarded:
    // a missing block means NOT RECORDED, never "measured zero".
    const _gate = (online) => (online && online.gate) || null;

    /** What the gates DID, as a phrase for an execution qualifier — or '' when they
     *  did nothing worth reporting.
     *
     *  This is the half of the reading the scorers structurally cannot supply. An
     *  inbound refusal is never scored at all (a blocked turn is deliberately kept
     *  away from the judges), and an outbound redaction is scored AFTER the
     *  redaction — clean by construction. So a guard that worked all day read
     *  "11/11 clean · none in this window" (#50, #53). A cleared escalation is the
     *  same omission from the other side: the strongest evidence a gate is working,
     *  invisible because only the eval span was consulted (#36).
     *
     *  `acts` are things the agent's user would notice; `escalated/cleared` is work
     *  the gate did that changed nothing — reported separately so "quiet" can mean
     *  "checked and cleared" rather than "nothing happened". */
    function _gateWork(online, kinds) {
        const g = _gate(online);
        if (!g) return '';
        const parts = [];
        if (kinds.refused && g.refused) parts.push(`${g.refused} refused`);
        if (kinds.redacted && g.redacted) parts.push(`${g.redacted} redacted`);
        if (kinds.blocked && g.blocked) parts.push(`${g.blocked} withheld`);
        if (kinds.cleared && g.cleared) parts.push(`${g.cleared} checked and cleared`);
        // `parked` is deliberately NOT reported here. It is a gate-WIDE count, so a
        // station reporting it claims parks another lane performed — live proof:
        // GROUNDED read "1 parked for review" for a park the inbound GUARDRAIL did.
        // Each station reports its OWN acts; in review mode an act implies a park,
        // and the park's home station is REVIEWED.
        return parts.join(' · ');
    }

    /** The execution attribute of a GUARANTEE station, under the lane cut.
     *
     *  A station's two attributes are configuration (would it work?) and execution (did
     *  it?). Execution used to be answered with a MEASUREMENT rate — the pass rate over
     *  the dimensions the station owned — which is why a red GROUNDED meant either "a
     *  gate acted" or "a measurement is low" with nothing on screen saying which.
     *  Measurement now counts at JUDGED alone, so a guarantee station answers execution
     *  with the only evidence that is genuinely its own: WHAT ITS MECHANISMS DID.
     *
     *  Three deliberate properties, each one a rule this platform has already paid for:
     *
     *   • `state: 'na'` — acting is not passing. A gate that withheld three answers is
     *     WORKING, so calling it `pass` would be the vacuous clean Rule 3.7 bans ("the
     *     gate ran" is not "the guarantee held"). We do not aggregate per-station gate
     *     denials or errors, so there is no honest health reading to give — and `na`
     *     says exactly that rather than inventing one. (`eval.gate.errored` exists but
     *     is counted once per TURN across all three gates, so it cannot be attributed
     *     to a station; giving GROUNDED the outbound guard's error would be worse than
     *     saying nothing.)
     *   • `text: ''` → the value renders `—`. Rule 1.2: a missing side is shown, never
     *     collapsed, or a config value gets misread as a runtime figure.
     *   • the detail NAMES the acts and NAMES where the measurements went, with a route.
     *     A bare `—` where a band used to be is reach loss: every fact still exists but
     *     is further away, and an inventory passes while the surface gets worse.
     *
     *  @param gateWork  this station's OWN acts, from `_gateWork` (already filtered to
     *                   the kinds this station performs — never gate-wide counts).
     *  @param nKeys     how many checks measure this guarantee (the LABEL map's count).
     */
    function _execAtJudged(gateWork, nKeys) {
        const moved = nKeys
            ? `${_plural(nKeys, 'check')} measure this guarantee — counted at JUDGED`
            : '';
        return {
            state: 'na',
            text: '',
            detail: [gateWork, moved].filter(Boolean).join(' · '),
        };
    }

    /** The `note` every guarantee station's derivation carries — declared ONCE.
     *
     *  Four copies of this paragraph is how the previous rule came to be stated four
     *  slightly different ways, and the version that mattered drifted. It answers the
     *  question the dimension rows above it provoke: *if these 14 checks are about
     *  grounding, why isn't GROUNDED coloured by them?* */
    const _JUDGED_NOTE =
        'The checks about this guarantee are counted at JUDGED, with every other '
        + 'measurement, and do not colour this station: a mechanism acts during the turn, '
        + 'a measurement grades it afterwards.';

    /** The `exec` prose for a guarantee station under the lane cut — declared once,
     *  parameterised by the station's own acts. */
    function _execDeriv(gateWork, nKeys, actName) {
        // W6 — only the live reading survives: the gate's own acts. The constant
        // sentence about where the checks are counted is now the `graded` row (W3).
        return gateWork ? `Execution is this station's OWN acts in the window: ${gateWork}.` : '';
    }

    function _countModes(modes) {
        const c = {};
        modes.forEach((m) => { c[m] = (c[m] || 0) + 1; });
        return ['enforce', 'review', 'annotate', 'observe', 'off']
            .filter((m) => c[m]).map((m) => `${c[m]} ${_postureLabel(m)}`).join(' · ') || 'none configured';
    }

    // ── the station grammar — ONE structure for all eight ─────────────────────
    // The panel has four zones; each does exactly one job, and they never swap:
    //
    //   value     the READING — what this control says right now, in its own unit.
    //             NEVER the size of the scope. Three stations used to render
    //             `profiles.length` / `states.length`, which is always `1` when an
    //             agent is focused — a constant that reads like a measurement.
    //   headline  the CONSEQUENCE — what this state means for the agent's answers.
    //   facts     Reading · Why · Evidence, in that order, always all three.
    //             "Why" is the rule that produced the glyph colour, so amber vs
    //             green is explainable. A slot with nothing to report says so
    //             explicitly — an absent slot must never read as a clean one.
    //   cta       the ACTION — trustCircle renders it as the click hint.
    //
    // Doctrine stays out: product claims ("receipts export from every capsule")
    // belong in the story link, not in a live reading.
    const _NONE = 'none in this window';
    // Mirrors core/review_queue.RELEASABLE_SOURCES. Release injects the withheld
    // original into subsequent turns' context, which is coherent only when that
    // original is an ANSWER. An inbound item's original is the user's REQUEST —
    // for a parked prompt injection, the attacker's payload — so offering release
    // there would hand the model the very text the guard refused, labelled as
    // owner-approved. The endpoint refuses it; this keeps the button off the row
    // (Rule 4b.3 — never a button that fails on click).
    const _RELEASABLE_SOURCES = new Set(['grounding', 'guardrail_outbound']);
    const _KIND_LABEL = {
        knowledge: 'knowledge repository', planner: 'planner repository',
        ontology: 'ontology', skill: 'skill', extension: 'extension',
        agent_pack: 'agent pack', flow: 'flow', dashboard: 'dashboard', canvas: 'canvas',
    };
    const _kindLabel = (k) => _KIND_LABEL[k] || String(k || 'ingredient');

    // Evidence drawn from the per-dimension pass rates the online eval already
    // aggregates: "groundedness 3/4 passed". Empty string when there is no signal,
    // so callers can substitute an explicit honest-absence line.
    /** A scorer key's human label, from the platform's single source.
     *
     *  `qualityDimensions.js` already maps every scorer key to a logical label and is
     *  used by the Quality card and the roster — the ring was the one surface printing
     *  raw keys (`grounded_overlap`) at a user. That is both the wire-token leak fixed
     *  at SIGNED and a consistency defect: one dimension, two names, depending on which
     *  panel you are looking at.
     *
     *  Falls back to title-case so an unmapped key degrades to something readable rather
     *  than snake_case, and never throws if the module is absent (it is a plain script,
     *  loaded before this one, but this function also runs under Node in the gate).
     */
    function _dimLabel(k) {
        const QD = (typeof window !== 'undefined') && window.QualityDimensions;
        if (QD && typeof QD.label === 'function') { try { return QD.label(k); } catch (e) { /* fall through */ } }
        return String(k || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
    }

    /** English plural without a "(s)" placeholder leaking into a polished surface. */
    const _plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + 's')}`;

    /** Rule 3.4 — expand a rate anchor in words: `<n> of <N> <unit> <predicate>`.
     *
     *  Only JUDGED (wire key `scored`) ever did this, and it is the only station no one misread. The
     *  others each dropped something, and the two that dropped the UNIT are exactly
     *  the two that misled this product's own author — twice, in one sitting:
     *
     *    GROUNDED  `9/11` · "Grounding Overlap 9/11 passed"
     *              → repeats the anchor's digits, puts a DIMENSION where the unit
     *                belongs, never says "turns". Read as "1 turn HAD an overlap"
     *                — the exact inverse of the truth.
     *    GUARDED   `11/11` · "safety dimensions clean"
     *              → no expansion at all, and "dimensions" implies the 11 counts
     *                dimensions when it counts turns.
     *
     *  A ratio without its unit is not a shorter sentence, it is an ambiguous one —
     *  and ambiguity on a trust surface is indistinguishable from a lie. So the unit
     *  is mandatory here, and `_dimShortfall` becomes the QUALIFIER of the predicate
     *  rather than a substitute for the reading.
     */
    function _expandRate(n, total, unit, predicate, opts = {}) {
        if (!total) return '';
        const pct = opts.pct === false ? '' : ` (${Math.round((n / total) * 100)}%)`;
        return `${n} of ${_plural(total, unit)} ${predicate}${pct}`;
    }

    function _dimShortfall(online, keys) {
        const dims = (online && online.dimensions) || {};
        const counts = (online && online.dimension_counts) || {};
        return keys
            .filter((k) => typeof dims[k] === 'number' && dims[k] < 1)
            // TC-84: an older span may carry the rate but not the count — "Grounding
            // Overlap 0/0 passed" is nonsense; fall back to the rate alone.
            .map((k) => counts[k]
                ? `${_dimLabel(k)} ${Math.round(dims[k] * counts[k])}/${counts[k]} passed`
                : `${_dimLabel(k)} ${Math.round(dims[k] * 100)}% pass rate`)
            .join(' · ');
    }

    /** The INSTANCES behind a shortfall, for the Evidence slot.
     *
     *  Execution already says WHICH dimensions fell short; repeating that in Evidence
     *  filled two slots with one fact (Rule 3.0). What Evidence owes is the instances —
     *  and the per-turn series answers a question the aggregate cannot: was a turn
     *  caught by SEVERAL checks, or only one?
     *
     *  That distinction is the difference between a corroborated failure and a single
     *  check disagreeing with the others. On a documentation corpus a coarse lexical
     *  overlap check flags well-paraphrased answers that the semantic judge passes — so
     *  "3 turns fell short" and "1 turn fell short on 2 checks, 2 more on 1" call for
     *  very different responses, and only the second is actionable.
     *
     *  `dimension_series` is the trailing per-turn pass/fail emitted by
     *  `eval/analysis.online_quality_by_agent`. Absent (older spans) ⇒ '' , and the
     *  caller falls back rather than inventing a reading.
     */
    function _shortfallInstances(online, keys) {
        // #63 — the per-dim series append only when a dim was DECIDED, so series of
        // different lengths do NOT align by index: a judge sampled once has a 1-entry
        // series whose index 0 is a DIFFERENT turn than a full-window dim's index 0.
        // The producer now emits `turn_failed_dims` — one entry per scored turn — and
        // that is the only structure a per-turn corroboration reading may come from.
        const tf = (online && online.turn_failed_dims);
        let perTurn = null, n = 0;
        if (Array.isArray(tf) && tf.length) {
            n = tf.length;
            perTurn = tf.map((fails) => (Array.isArray(fails) ? fails : [])
                .filter((d) => keys.includes(d)));
        } else {
            // Older backend: fall back to the series — but ONLY dims decided on every
            // scored turn (series length == the maximum) are aligned by construction;
            // a shorter (sampled) series must be dropped, not zipped in at index 0.
            const series = (online && online.dimension_series) || {};
            const live = keys.filter((k) => Array.isArray(series[k]) && series[k].length);
            if (live.length < 1) return '';
            n = Math.max(...live.map((k) => series[k].length));
            const full = live.filter((k) => series[k].length === n);
            if (!full.length) return '';
            perTurn = Array.from({ length: n }, (_, i) => full.filter((k) => series[k][i] === 0));
        }
        let multi = 0, single = 0;
        const singleDrivers = {};
        for (const failed of perTurn) {
            if (failed.length > 1) multi++;
            else if (failed.length === 1) {
                single++;
                singleDrivers[failed[0]] = (singleDrivers[failed[0]] || 0) + 1;
            }
        }
        if (!multi && !single) return '';
        const parts = [];
        if (multi) parts.push(`${_plural(multi, 'turn')} fell short on more than one check`);
        if (single) {
            const only = Object.keys(singleDrivers);
            parts.push(`${single} on ${only.length === 1 ? `${_dimLabel(only[0])} only` : 'a single check only'}`);
        }
        return `of the last ${n} scored turns: ${parts.join(' · ')}`;
    }

    /** #62b — the honest full-window execution figure: of the last N scored turns,
     *  how many were clean on this station's checks? A turn is clean when no check
     *  that RAN on it failed — a sampled judge that abstained on a turn never counts
     *  against it, and a judge that ran and failed counts exactly once, on ITS turn.
     *  This is the per-turn UNION the per-dim aggregates cannot reconstruct (zipping
     *  them projected one dim's rate onto turns it never measured — the 0/18 defect).
     *  Returns null when the payload predates `turn_failed_dims` (older backend);
     *  callers fall back to the worst dim over its own sample size. */
    /** Rule 3.0c — turns whose ONLY failures among `keys` are DISPUTED: a coarse check
     *  flagged them and the judge that refines it cleared them. Such a turn is neither
     *  a failure (the better measure cleared it) nor clean (something flagged), so it
     *  leaves the RATE alone and FLOORS the station at "worth a look" instead. Server-
     *  computed (`turn_disputed_dims`, from the per-turn decided set); absent on an
     *  older backend, which simply yields 0 — the pre-rule reading, unchanged. */
    function _disputedOnlyTurns(online, keys) {
        const tf = (online && online.turn_failed_dims) || [];
        const td = (online && online.turn_disputed_dims) || [];
        if (!Array.isArray(td) || !td.length) return 0;
        let n = 0;
        tf.forEach((fails, i) => {
            const mine = (Array.isArray(fails) ? fails : []).filter((d) => keys.includes(d));
            if (!mine.length) return;
            const disp = Array.isArray(td[i]) ? td[i] : [];
            if (mine.every((d) => disp.includes(d))) n++;
        });
        return n;
    }

    function _turnCleanRate(online, keys) {
        const tf = (online && online.turn_failed_dims);
        if (!Array.isArray(tf) || !tf.length) return null;
        // #66 (solo sweep) — if NO check of this family ever ran in the window, a
        // "clean" union would be vacuously true ("N of N clean" measured by nothing).
        // The station then falls back to `—` / no execution reading, which is honest.
        const dims = (online && online.dimensions) || {};
        if (!keys.some((k) => typeof dims[k] === 'number')) return null;
        // Rule 3.0c — a turn whose family failures are ALL disputed does not count
        // against the rate (the judge that refines them cleared the answer); it is
        // reported separately and floors the station at warn, so nothing is silenced.
        const td = (online && online.turn_disputed_dims) || [];
        let bad = 0, disputedOnly = 0;
        tf.forEach((fails, i) => {
            const mine = (Array.isArray(fails) ? fails : []).filter((d) => keys.includes(d));
            if (!mine.length) return;
            const disp = Array.isArray(td[i]) ? td[i] : [];
            if (disp.length && mine.every((d) => disp.includes(d))) disputedOnly++;
            else bad++;
        });
        return { clean: tf.length - bad, window: tf.length, disputed: disputedOnly };
    }

    /** The execution band ONE agent's focused ring would show for `keys` — the same
     *  per-turn-union-first / worst-dim-fallback computation the focused station runs,
     *  extracted so the estate rollup below reuses it VERBATIM. Returns null when the
     *  agent has no measurement on these keys (honest absence — never drags a rollup). */
    function _agentExecBand(online, keys) {
        const clean = _turnCleanRate(online, keys);
        if (clean) {
            return clean.window
                ? { band: _execBand(clean.clean / clean.window), n: clean.window }
                : null;
        }
        const worst = _worstDim(online, keys);
        return worst ? { band: _execBand(worst.rate), n: worst.n } : null;
    }

    /** The execution cell the ACTIVE lens selects for one agent (trust trip, W4/W6).
     *
     *  `trip` is the SAME aggregator's output over the agent's current leg (attached
     *  server-side only for agents with a marker), so every reader of the total cell
     *  works on it unchanged. Under the trip lens an UNMARKED agent keeps its whole
     *  record — its trip reading IS its whole record — and an EMPTY leg (turns 0)
     *  stays selected: the ring must say "the leg is empty", never silently fall
     *  back to the odometer as if no reset had happened. */
    function _lensCell(online) {
        return (_circleLens === 'trip' && online && online.trip) ? online.trip : online;
    }

    /** A leg boundary as a short human date — inside the gate's extraction span
     *  because every trip disclosure renders through it. Falls back to the ISO
     *  date part rather than guessing. */
    function _legDate(iso) {
        if (!iso) return '';
        try {
            return new Date(iso).toLocaleDateString(undefined,
                { day: 'numeric', month: 'short' });
        } catch (_) { return String(iso).slice(0, 10); }
    }

    /** W7 — the previous leg's clean figure for a station's dimension set, from the
     *  `trip.prev` sub-cell (same shape as any cell, so the SAME readers apply).
     *  `keys = null` reads the whole-card union (JUDGED). Null when there is no
     *  previous leg or nothing was measured — the comparison then simply absents. */
    function _legPrevFigure(prev, keys) {
        if (!prev) return null;
        if (!keys) {
            return prev.turns ? { x: prev.turns - (prev.failures || 0), n: prev.turns } : null;
        }
        const c = _turnCleanRate(prev, keys);
        if (c) return c.window ? { x: c.clean, n: c.window } : null;
        const w = _worstDim(prev, keys);
        return (w && w.n) ? { x: Math.round(w.rate * w.n), n: w.n } : null;
    }

    // Estate-scope execution rollup: classify each scored agent by the SAME band its
    // own focused ring would show, then aggregate WORST-OF (Rule 5.1, applied to
    // execution). The proportional band this replaces (`_execBand(clean/total)`) let a
    // red agent hide behind a healthy average — 9 of 10 clean rendered green while the
    // tenth's own ring read danger, so "quiet" at estate level did not mean "no agent
    // needs you". The invariant now holds by construction: the estate station can
    // never read better than the worst colour a drill-down can reach, because both
    // altitudes run the same banding. The magnitude stays in the value (Rule 5.2);
    // agents with nothing measured on these keys are excluded, never counted clean.
    // `keys = null` bands each agent's whole-card rollup instead (JUDGED's union).
    // Lens-aware (W6): under the trip lens each agent is read since ITS OWN last
    // reset where one exists — `legged` counts them so the derivation can disclose
    // the mix of windows honestly.
    function _estateExecRollup(dash, keys) {
        const rows = [];
        let legged = 0;
        Object.entries((dash && dash.by_agent) || {}).forEach(([k, c]) => {
            if (!c) return;
            const cc = _lensCell(c);
            if (cc !== c) legged++;
            // A FUNCTION computes the key set per cell — what JUDGED counts depends on
            // which dimensions each agent's class actually produced, so a fixed list
            // cannot serve it. A plain array behaves exactly as before.
            const kk = (typeof keys === 'function') ? keys(cc) : keys;
            let band = null, n = null;
            if (kk) {
                const b = _agentExecBand(cc, kk);
                if (b) { band = b.band; n = b.n; }
            } else if (cc.turns) {
                band = _execBand((cc.turns - (cc.failures || 0)) / cc.turns);
                n = cc.turns;
            }
            if (!band || band === 'na') return;
            rows.push({ tag: (c && c.tag) || String(k).split('::')[0], band, n });
        });
        const short = rows.filter((r) => r.band !== 'pass');
        return {
            total: rows.length,
            quiet: rows.length - short.length,
            short,
            legged,
            state: rows.length ? _postureAggregate(rows.map((r) => r.band)) : 'na',
        };
    }

    /** The agents behind an estate shortfall, worst-first, for the Evidence slot —
     *  the instances that make a worst-of amber/red actionable rather than merely
     *  accurate. Only `danger` carries its attention word; amber is the list's default. */
    function _shortAgentNames(short) {
        return _briefList(
            short.slice()
                .sort((a, b) => ((b.band === 'danger') ? 1 : 0) - ((a.band === 'danger') ? 1 : 0))
                .map((r) => `@${r.tag}${r.band === 'danger' ? ' — needs you now' : ''}`), 3);
    }

    // Dedup + cap a list for a one-line Evidence slot ("a, b, c +2 more").
    function _briefList(items, cap = 4) {
        const uniq = [...new Set(items)];
        return uniq.length <= cap ? uniq.join(', ')
            : `${uniq.slice(0, cap).join(', ')} +${uniq.length - cap} more`;
    }

    // ── the LABEL map — which guarantee each measurement is ABOUT ──────────────
    // ⚠ NOT A COUNTING MAP. Every scored dimension is counted at JUDGED and nowhere else
    // (`assurance_points.MEASUREMENT_STATION`); a guarantee station reads only its
    // MECHANISMS. What survives here is the half worth keeping — the topic of a
    // measurement — and it drives every READING surface: the roster's dimension bars,
    // its band strip and ledger chips, the compare heatmap's column order, the
    // scorecard's groups and the Guide's station homes.
    //
    // Still the mirror of `assurance_points.station_dimensions()` (the registry derives
    // it from mechanism ownership; this is the JS render copy, gate-enforced EQUAL both
    // ways in test_assurance_points.py §12 so neither side can move alone). The pin is
    // if anything MORE valuable now: a label that disagrees across the two languages
    // would file the same check under two guarantees on two surfaces.
    const STATION_DIMS = {
        // `knowledge_freshness`, `retrieval_performed` and `source_consistency` were
        // dropped (symmetry map A10): they were labelled to `corpus_quarantine` by
        // PROXIMITY — "it sits at the retrieval seam" — not because that machinery
        // explains a poor score. A poor freshness points at CDC sync, a poor
        // retrieval-performed at the retrieval configuration. They are still COUNTED at
        // JUDGED like every measurement; only the false pointer is gone.
        grounded: ['answer_groundedness', 'citations', 'context_utilization',
                   'governed_data_ratio', 'grain_faithfulness', 'grounded_overlap',
                   'groundedness', 'honest_absence', 'numeric_fidelity',
                   'retrieval_relevance', 'scope_adherence', 'scope_faithfulness', 'citation_fidelity', 'identifier_provenance',
                   'kg_seed_utilization', 'enrichment_recall'],
        guarded:  ['content_safety', 'output_sanity', 'safety'],
        // `no_tool_loops` / `tool_success_rate` dropped (A18): `tool_enforcement`
        // enforces WHICH tools may run; those grade HOW WELL the ones that ran behaved.
        bounded:  ['action_safety', 'budget_adherence', 'must_not_call', 'surface_violation', 'tool_subset'],
        proven:   ['clean_termination', 'provenance_integrity'],
        // PROP-011: the needs-input mechanism (REVIEWED — a person is needed) owns the measure of a
        // turn that went past a code-detected ambiguity without asking.
        reviewed: ['guessed_past_ambiguity'],
        // NO `scored` key, deliberately — and the registry has none either, which is
        // what the key-set equality gate checks. JUDGED owns NOTHING by declaration: it
        // counted everything, exactly as its own definition says ("the checks no
        // other station owns"). It used to hold `governed_data_ratio`, because that
        // dimension's mechanism sat here by elimination rather than by argument; moving
        // `governed_data` to GROUNDED — where "did the answer rest on declared,
        // traceable data operations" belongs — took the dimension with it and made the
        // definition true without exception.
    };
    /** JUDGED's key set for one cell: EVERY dimension this agent's class measured.
     *
     *  Measurement is a lane, not a guarantee-attribute (see `STATION_DIMS`). The
     *  `_FOREIGN_DIMS` exclusion that stood here belonged to the guarantee-axis
     *  partition, where a grounding check was counted at GROUNDED and had to be kept out
     *  of JUDGED to avoid a double count. With one counting station the exclusion would
     *  LOSE checks instead of de-duplicating them: whatever it removed would be counted
     *  at no station at all.
     *
     *  Still per-cell, because what a class produces varies — the key set is what this
     *  agent actually measured, never the catalogue of what could be measured. */
    function _judgedKeys(online) {
        return Object.keys((online && online.dimensions) || {});
    }

    /** BOUNDED's act line — `acted 3 · refused 1 (close_lead: not granted)` — over the given
     *  agents' recent acts. Empty when no act was invoked (no line, never a vacuous zero). */
    function _actEvidence(tags) {
        if (!_actActivity) return '';
        const sum = { performed: 0, refused: 0, parked: 0, prepared: 0, observed: 0, refusals: [] };
        for (const t of tags || []) {
            const c = _actActivity[t];
            if (!c) continue;
            for (const k of ['performed', 'refused', 'parked', 'prepared', 'observed']) sum[k] += Number(c[k]) || 0;
            for (const r of c.refusals || []) if (sum.refusals.length < 2) sum.refusals.push(r);
        }
        const parts = [];
        if (sum.performed) parts.push(`acted ${sum.performed}`);
        if (sum.parked) parts.push(`parked ${sum.parked}`);
        if (sum.prepared) parts.push(`awaiting confirmation ${sum.prepared}`);
        if (sum.observed) parts.push(`observed ${sum.observed}`);
        if (sum.refused) parts.push(`refused ${sum.refused}`);
        if (!parts.length) return '';
        return `signed actions in recent sessions: ${parts.join(' · ')}`
            + (sum.refusals.length ? ` (${sum.refusals.join('; ')})` : '');
    }

    // ── the verdict (W2) — three short rows, built from the stations' own values ──
    // The ring's centre showed a tag and a class; the default panel showed doctrine; a
    // first cut of this builder produced one run-on sentence naming every station —
    // "overkill, ugly". The glance layer is now THREE rows, each derived only from the
    // stations' `design.text` / `execution.text` / `state` (Rule 3b.5):
    //   needs you — only findings (warn / danger), worst first; absent when there are none
    //   posture   — signing, the two gates merged into one clause, the scope
    //   record    — conduct, graded turns, sealed receipts
    // Platform names only (the grounding gate, the guardrails — never "drift gate");
    // the stored posture labels stay in the matrix, the row says what the posture DOES.
    // Rule 3.7: zero graded turns reads "no turns graded yet", never clean. The same
    // builder feeds the roster mini-ring tooltip (`text`), so the two cannot disagree.
    function _verdictLine(stationsArr, center) {
        const pl = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
        const by = Object.fromEntries((stationsArr || []).map((s) => [s.key, s]));
        const one = !(center && center.kind === 'estate');
        const frac = (t) => { const m = /(\d+)\/(\d+)/.exec(String(t || '')) || /(\d+) of (\d+)/.exec(String(t || '')); return m ? [+m[1], +m[2]] : null; };
        const isFinding = (s) => s && (s.state === 'warn' || s.state === 'danger');
        const now = [], posture = [], record = [];
        const push = (arr, s, text) => { if (s && text) arr.push({ key: s.key, state: s.state, text }); };
        const S = by.signed, G = by.grounded, U = by.guarded, B = by.bounded, J = by.scored, T = by.trusted, R = by.reviewed, P = by.proven;
        const graded = J && (() => {
            if (J.design.text === 'off') return 'scoring off';
            const f = frac(one ? J.execution.detail : J.execution.text);
            if (!f) return one ? 'no turns graded yet' : 'no agents graded yet';
            return one ? `${f[0]} of ${pl(f[1], 'turn')} graded clean` : `${f[0]} of ${pl(f[1], 'agent')} clean on their checks`;
        })();
        if (one) {
            // ── needs you: findings only, in ring order (sorted worst-first below)
            const sig = S && S.execution.text;
            const SIG = { attested: 'signed and attested', changed: 'signed, but changed since', unsigned: 'not signed',
                          'not attested here': 'signed, not attested on this deployment' };
            if (isFinding(S)) push(now, S, SIG[sig] || `signing: ${_ATTN_WORD[S.state]}`);
            const gateNow = (s, name) => {
                if (!isFinding(s)) return;
                if (s.driver === 'open items' && s.open) push(now, s, `${name}: ${pl(s.open, 'open inbox item')}`);
                else if (s.design.state === 'warn' && s.design.text === 'off') push(now, s, `${name} configured but inert`);
                else push(now, s, `${name}: ${_ATTN_WORD[s.state]}`);
            };
            gateNow(G, 'grounding gate'); gateNow(U, 'guardrails');
            if (isFinding(B)) push(now, B, 'unscoped — nothing limits what it may do');
            if (isFinding(J)) push(now, J, graded);
            if (isFinding(T) && T.execution.text) push(now, T, `conduct ${T.execution.text}`);
            if (isFinding(R)) { const m = /^(\d+) open/.exec(R.execution.text || ''); if (m && +m[1] > 0) push(now, R, `${pl(+m[1], 'item')} waiting for you`); }
            if (isFinding(P)) push(now, P, P.execution.text ? `${P.execution.text.replace(/ sealed$/, '')} receipts sealed` : 'nothing sealed');
            // ── posture: signing · the two gates as ONE clause · the scope
            if (S && !isFinding(S)) push(posture, S, SIG[sig] || 'not signed');
            const verb = (s) => (!s ? '' : s.design.state === 'pass' ? 'stops' : s.design.state === 'watching' ? 'watches' : s.design.state === 'na' ? 'off' : 'watches');
            const gv = verb(G), uv = verb(U);
            const tag = (s) => (s && /disclose/.test(s.design.text || '') ? 'disclose' : s && /review/.test(s.design.text || '') ? 'review' : '');
            let gates = '';
            if (gv && uv && gv === uv) {
                gates = gv === 'off' ? 'grounding gate and guardrails off'
                    : `${gv} drift and unsafe content${gv === 'watches' ? ', stops neither' : ''}`;
                const t = tag(G) === tag(U) ? tag(G) : '';
                if (t === 'disclose') gates += ', disclosing what it finds';
                if (t === 'review') gates += ', keeping originals for review';
            } else if (gv || uv) {
                const part = (v, what, name) => (v === 'off' ? `${name} off` : `${v} ${what}`);
                gates = [gv && part(gv, 'drift', 'grounding gate'), uv && part(uv, 'unsafe content', 'guardrails')].filter(Boolean).join(' · ');
            }
            if (gates) posture.push({ key: 'grounded', state: (G || U || {}).state, text: gates });
            if (B && B.design.text === 'scoped') push(posture, B, 'acts only within its scope');
            // ── record
            if (T && !isFinding(T) && T.execution.text) push(record, T, `conduct ${T.execution.text}`);
            if (J && !isFinding(J)) push(record, J, graded);
            if (P && !isFinding(P)) { const m = /^(\d+) sealed/.exec(P.execution.text || ''); if (m) push(record, P, `${pl(+m[1], 'receipt')} sealed`); }
        } else {
            const cnt = (s, phrase) => { const f = frac(s && s.design.text); return f ? `${f[0]} of ${f[1]} ${phrase}` : ''; };
            const stop = (s, what) => { const f = frac(s && s.design.text); return f ? `${f[0]} of ${f[1]} ${f[0] === 1 ? 'stops' : 'stop'} ${what}` : (s && s.design.text === 'off' ? `${what === 'drift' ? 'grounding gate' : 'guardrails'} off` : ''); };
            // SIGNED has no configuration dial — its count is the EXECUTION side.
            const sigCnt = (() => { const f = frac(S && S.execution.text); return f ? `${f[0]} of ${f[1]} signed` : ''; })();
            const items = [
                [S, sigCnt, 'posture'], [G, stop(G, 'drift'), 'posture'], [U, stop(U, 'unsafe content'), 'posture'],
                [B, cnt(B, 'scoped'), 'posture'], [J, graded, 'record'],
                [T, (() => { const f = frac(T && T.execution.text); return f ? `${f[0]} of ${f[1]} proven over time` : ''; })(), 'record'],
                [R, (() => { const m = /^(\d+) open/.exec((R && R.execution.text) || ''); return m && +m[1] > 0 ? `${pl(+m[1], 'item')} waiting for you` : ''; })(), 'now'],
                [P, (() => { const m = /^(\d+) sealed/.exec((P && P.execution.text) || ''); return m ? `${pl(+m[1], 'receipt')} sealed` : ''; })(), 'record'],
            ];
            for (const [s, text, home] of items) {
                if (!s || !text) continue;
                push(isFinding(s) ? now : (home === 'now' ? record : (home === 'posture' ? posture : record)), s, text);
            }
        }
        const rank = { danger: 0, warn: 1 };
        now.sort((x, y) => ((rank[x.state] ?? 2) - (rank[y.state] ?? 2)));
        const worst = now.some((c) => c.state === 'danger') ? 'danger' : (now.length ? 'warn' : null);
        const lines = [
            now.length ? { k: 'needs you', state: worst, text: now.map((c) => c.text).join(' · ') } : null,
            posture.length ? { k: 'posture', state: null, text: posture.map((c) => c.text).join(' · ') } : null,
            record.length ? { k: 'record', state: null, text: record.map((c) => c.text).join(' · ') } : null,
        ].filter(Boolean);
        const lead = center && center.label ? center.label : '';
        const clauses = now.concat(posture, record);
        return { lead, lines, clauses,
                 text: [lead, ...lines.map((l) => l.text)].filter(Boolean).join(' — ') };
    }

    function _circleStationsFor(agent) {
        const profiles = agent
            ? _profilesRaw.filter((p) => p.tag === agent.tag)
            : _profilesRaw;
        const dash = _selfDash(_model.dashboard || {});   // TC-8: one subject for both attributes
        const one = !!agent;            // focused on a single agent vs the whole estate
        const N = profiles.length;
        // PROP-002 — per-station ACTED flags for the acted dot (a mechanism acted
        // on ≥1 recent turn). Read from the inbox's per-agent derived observations,
        // attributed to stations at their ONE computation site (renderAssuranceInbox)
        // — never re-derived here. Focused-only: estate attribution would put one
        // agent's event on the whole ring. Absent data (gate harness, inbox not yet
        // loaded) ⇒ no dots — honest absence, never an inference.
        const rActed = (one && _inboxByTag[agent.tag] && _inboxByTag[agent.tag].stations) || null;
        // PROP-002b — OPEN inbox items per station (severity-filtered at their one
        // accumulation site): the colour fold's input. Focused-only, like the acted dots.
        const rOpenItems = (one && _inboxByTag[agent.tag] && _inboxByTag[agent.tag].openItems) || null;
        // The trust-trip lens (docs/roadmap/TRUST_TRIP_RESET.md). `oncell` is the
        // execution cell the ACTIVE lens selects for the focused agent — the trip
        // cell is the same aggregator's output (W4), so every reader below works on
        // it unchanged. `tripMeta` carries the leg boundary for the disclosures W3
        // makes mandatory: no trip-scoped reading may render without naming it.
        // Configuration and the canonical TRUSTED composite never re-window (W5).
        const oncell = one ? _lensCell(agent.online) : null;
        const tripMeta = (one && _circleLens === 'trip' && agent.online && agent.online.trip)
            ? agent.online.trip : null;
        const legSince = tripMeta ? _legDate(tripMeta.since) : '';

        // 1 · Signed — Trust-Membrane integrity (defensive: suite may be absent).
        const integ = _trustAgentsFull || [];
        const integFor = agent ? integ.filter((t) => t.tag === agent.tag) : integ;
        const nTrusted = integFor.filter((t) => t.state === 'trusted').length;
        const nChanged = integFor.filter((t) => t.state === 'changed').length;
        // Count each state EXPLICITLY. As a remainder this swallowed
        // `deployment_unattested`, so a signed-but-not-locally-attested agent was told
        // it "is not signed yet" — the opposite of what happened.
        const nUnsigned = integFor.filter((t) => t.state === 'unsigned').length;
        const nUnattested = integFor.filter((t) => t.state === 'deployment_unattested').length;
        // The blocking ingredients are already on the /trust/agents payload — no extra
        // fetch needed to name what is holding sign-off back.
        // Name the blocker, don't just identify it. `artifact_id` is a database id, so this
        // read "knowledge repository #2" at the exact moment it asked you to sign it — and a
        // knowledge signature is EARNED (bound to a scan report), so an integer is not
        // something you can judge. The backend now supplies `label`; the id stays as the
        // fallback for an ingredient whose source could not name it.
        const blockers = integFor.flatMap((t) => (t.blocking || []))
            .map((b) => (b.label
                ? `${_kindLabel(b.kind)} “${b.label}”`
                : `${_kindLabel(b.kind)} #${b.artifact_id}`));
        // Planner Health — a blocker that can name WHAT holds it back carries `finding`
        // (the backend scans the cases it already has in hand): "1 case not signable —
        // “…” (stacked-statement)". Without it the slot said the planner was not
        // signable and the operator replayed the scanner by hand to learn which case.
        const blockerFindings = integFor.flatMap((t) => (t.blocking || []))
            .filter((b) => b && b.finding)
            .map((b) => `${_kindLabel(b.kind)}${b.label ? ` “${b.label}”` : ''}: ${b.finding}`
                + (b.kind === 'planner' ? ' · remove the case or acknowledge its finding under Planner Health' : ''));
        // Deployment-wide, NOT the focused scope: does this deployment practise signing?
        const signingInUse = (_trustAgentsFull || []).some((t) => t.state === 'trusted'
            || t.state === 'changed' || t.state === 'deployment_unattested');
        const sState = _postureAggregate(
            integFor.map((t) => _integrityState(t.state, signingInUse)));
        // PROP-002 follow-up — the owed dot must be ACTIONABLE at estate scope too.
        // The estate panel named a count ("25 unsigned") with no route to any of the
        // agents owing the act: context-sensitive support means SURFACING them. The
        // owing agents, worst first (changed = a detected discrepancy outranks
        // outstanding work), each with the act that is owed named (Rule 8.5). Trust
        // records are the CALLER's own agents (the trust read is owner-scoped), so a
        // focus route on one is always performable (Rule 4b.3).
        const _OWED_RANK = { changed: 0, deployment_unattested: 1, unsigned: 2 };
        const _OWED_ACT = { changed: 'changed after sign-off',
                            deployment_unattested: 'attestation owed',
                            unsigned: 'sign-off owed' };
        const sOwing = one ? [] : integFor
            .filter((t) => t.state === 'changed' || t.state === 'deployment_unattested'
                || (t.state === 'unsigned' && signingInUse))
            .sort((a, b) => (_OWED_RANK[a.state] ?? 9) - (_OWED_RANK[b.state] ?? 9));
        // Signing has no configuration dial — it is an ACT, not a setting. So the
        // design side is honestly absent (`—`) rather than a fabricated attribute,
        // and attention comes from execution alone.
        const signed = _assemble('signed',
            { state: 'na', text: '', detail: 'signing is an act, not a setting — nothing to configure' },
            {
                state: sState,
                // Focused on ONE agent, `0/1` is the scope size wearing a ratio's
                // clothes: it reads the same for a never-signed agent and for one whose
                // only outstanding act is the local attestation. The state word is the
                // actual reading. Estate scope keeps the ratio, which there is real.
                text: !integFor.length ? ''
                    : one ? ({ trusted: 'attested', changed: 'changed',
                               deployment_unattested: 'not attested here' }[integFor[0].state] || 'unsigned')
                    : `${nTrusted}/${integFor.length}`,
                // The qualifier must ADD to the value, in words. Echoing the wire string
                // (`deployment_unattested`) put a machine token in a human slot and said
                // nothing the value had not already said — the same defect class as the
                // `0/1` it sat next to.
                // `changed` names its FACE (which half drifted) when the payload
                // carries the breakdown — "changed" alone sent the operator to
                // re-sign a portable half that was never broken.
                detail: !integFor.length ? 'no attestations recorded for this scope'
                    : one ? ((() => {
                        const face = _changedFace(integFor[0]);
                        if (face === 'deployment') {
                            return 'the portable signature is intact — THIS deployment moved after it was attested'
                                + (_driftList(integFor[0]) ? `: ${_driftList(integFor[0])}` : '');
                        }
                        if (face === 'both') {
                            return 'both halves drifted — the composition since sign-off, and this deployment since its attestation'
                                + (_driftList(integFor[0]) ? ` (${_driftList(integFor[0])})` : '');
                        }
                        return _SIGNED_MEANS[integFor[0].state] || _SIGNED_MEANS.unsigned;
                    })())
                    : `${nTrusted} of ${integFor.length} signed and intact` +
                      (nChanged ? `, ${nChanged} changed` : '')
                      + (nUnattested ? `, ${nUnattested} awaiting deployment attestation` : '')
                      + (nUnsigned ? `, ${nUnsigned} unsigned` : ''),
            },
            {
                headline: !one
                    ? `${nTrusted} of ${integFor.length} agents are signed and intact` +
                      (nChanged ? `; ${nChanged} changed after sign-off.` : '.')
                    : nChanged
                    ? (_changedFace(integFor[0]) === 'deployment'
                        ? 'This deployment moved after it was attested — what runs here no longer matches what was vouched for; the portable signature is intact.'
                        : 'Its make-up changed after sign-off — the signature no longer matches what runs.')
                    : nTrusted === integFor.length && integFor.length
                    ? 'Content-bound and offline-verifiable — any change to its make-up will show.'
                    // A baseline DOES exist here — claiming otherwise contradicted the Why
                    // slot on the same panel. What is missing is this deployment's own half.
                    : nUnattested
                    ? 'Signed and intact, but this deployment has not attested its own half — so the badge cannot go green here.'
                    : 'No baseline exists, so a change to what this agent is made of cannot be detected.',
                // Name the NATURE of the absence, not just its level: "worth a look" is
                // the right triage word but does not say that work is owed.
                why: nUnattested
                    ? 'the publisher signature is intact and every ingredient is signed — what is outstanding '
                      + 'is THIS deployment attesting its own resolved model, tool catalog and connector opt-ins, '
                      + 'which never travel with a pack'
                    // #61 — this branch was written for a FOCUSED agent and reused
                    // verbatim at estate scope, where it said "this one is not signed
                    // yet" of eight unsigned agents. Both attributes must describe the
                    // same subject (§7.8); so must the reasoning about them.
                    : (nUnsigned && signingInUse
                        ? 'sign-off is outstanding — this deployment signs agents and '
                          + (one ? 'this one is not signed yet. '
                                 : `${_plural(nUnsigned, 'agent')} here ${nUnsigned === 1 ? 'is' : 'are'} not signed yet. `)
                        : nUnsigned
                        ? 'nothing here has been attested yet, so signing is not in use — nothing is owed. '
                        : '')
                      + 'An agent is signable only once every ingredient it contains is signed',
                // #26/#27 — Evidence derives from the SAME regime as Why, so the two
                // slots can no longer contradict ("nothing is owed" beside "blocked
                // by …"). And an unsigned ingredient is not a WALL: the guided flow
                // signs it first, so the slot describes the order of operations. A
                // genuine block only exists at sign time (a validator refusing an
                // unscreened corpus), and that is reported by the act itself.
                evidence: [
                    // The deployment drift, NAMED — "no ingredient is holding sign-off
                    // back" was true and useless beside a `changed` badge: the drift
                    // was in this instance's half, and the record knows exactly what
                    // moved ("model changed: X → Y"). Name it, first.
                    ...(one && integFor[0] && ['deployment', 'both'].includes(_changedFace(integFor[0]))
                        ? [`deployment drift — ${_driftList(integFor[0]) || 'this instance’s resolved capability surface moved'}`
                           + ' · a re-attestation of this deployment is the act that clears it']
                        : []),
                    // Rule 5.4 at the station that OWES acts: name the agents, not
                    // just the count — an accurate number with no route to the fix
                    // is the amber operators learn to ignore.
                    ...(sOwing.length
                        ? [`owed by ${_briefList(sOwing.map((t) => `@${t.tag} (${_OWED_ACT[t.state]})`))}`]
                        : []),
                    blockers.length
                        ? (signingInUse
                            ? `sign-off will first sign ${_briefList(blockers)}`
                            : `nothing is owed — a first sign-off would begin with ${_briefList(blockers)}`)
                        : (integFor.length ? 'no ingredient is holding sign-off back' : ''),
                    ...blockerFindings.slice(0, 2),
                    // #32 — the caveat a signature came with, joined to the stations it
                    // explains. `status` is the validator's machine-readable verdict, so
                    // the cross-reference is deterministic, never prose-matching.
                    ...(one && integFor[0] ? [integFor[0].caveat && integFor[0].caveat.note
                        ? `signed with a caveat — ${integFor[0].caveat.note}`
                          + (integFor[0].caveat.status === 'observe_only'
                              ? ' (the reading behind GROUNDED and GUARDED)' : '')
                        : ''] : []),
                    ...((one && integFor[0] && Array.isArray(integFor[0].ingredient_caveats))
                        ? integFor[0].ingredient_caveats.slice(0, 2).map((c) => (c && c.note
                            ? `${_kindLabel(c.kind)}${c.label ? ` “${c.label}”` : ''} signed with a caveat — ${c.note}`
                            : ''))
                        : []),
                ].filter(Boolean).join(' · '),
                // Signing is only offered where it can actually be carried out: a single
                // focused agent, with the trust package present (TrustBadge lives in the
                // removable Ontology Suite, so its absence must degrade to no button).
                // Own agents only. In system scope a roster key is "tag::uuid", and tags are
                // unique only PER USER — so offering to sign while focused on someone else's
                // agent would act on the caller's same-tag agent instead. Read stays open to
                // an admin; acting does not.
                //
                // ESTATE scope (PROP-002 follow-up): the owed dot's context-sensitive
                // support. No estate-wide act exists (signing is per-agent), so the
                // actions are FOCUS ROUTES onto the agents owing one — picking a route
                // lands the ring on that agent with SIGNED pinned, where the
                // state-derived act (sign · attest · re-sign, Rule 8.5) is offered.
                // Capped at three (a wall of buttons is not a UI); the Evidence slot
                // names the full list.
                actions: one
                    ? _signActions(
                        (!String(agent.key || agent.tag).includes('::') && agent.isSelf !== false)
                            ? integFor[0] : null)
                    : sOwing.slice(0, 3).map((t, i) => ({
                        label: `Open @${t.tag} — ${_OWED_ACT[t.state]}`,
                        action: `focus-agent:${t.tag}`,
                        ...(i === 0 ? { primary: true } : {}),
                    })),
                // The owed dot (ledger §4.3): true only where an ACT is genuinely
                // outstanding — a sign-off or a local attestation — and only where this
                // deployment practises signing (an unsigned agent in a deployment that
                // never signs owes nothing; the Why slot says exactly that).
                owed: !!(nUnattested || nChanged || (nUnsigned && signingInUse)),
                cta: { label: 'Open the posture matrix', action: 'posture-matrix' },
                // PROP-009 — every signature, read now, with the answers that used it. Leads
                // when present (SIGNED's reading is an execution reading); the matrix stays.
                ...(window.EvidenceView ? { execCta: { label: nChanged ? 'Open Evidence — the changed signatures' : 'Open Evidence — every signature',
                                                       action: nChanged ? 'evidence:changed' : 'evidence' } } : {}),
                // The Trust Membrane seal, reused rather than reinvented — see
                // trustCircle._applyGlyph for why SIGNED alone carries a state glyph.
                // Focused scope only: an estate has no single integrity state, so an
                // aggregate would have to elect one agent's seal to stand for ten.
                glyph: _integrityGlyph(one && integFor.length ? integFor[0].state : null),
                derivation: _deriv({
                    window: one ? 'this agent' : `${_plural(N, 'agent')} in scope`,
                    config: 'Configuration reads the Trust-Membrane attestation for each agent in scope — signed and intact, signed but changed since, publisher-signed but not attested on this deployment, or never signed. It is a state, not a score.',
                    exec: 'Signing is an ACT, not a measurement, so there is no execution figure — the dash is honest absence, never a zero.',
                    note: 'No quality dimension feeds this station. Its facts come from the attestation store (composition + posture + capability hashes), not from scored turns.',
                }),
            });

        // 2–4 · The gates (grounding / guardrails / authority) from profile config.
        // A mode is ACTING when it can change what the user receives (enforce/review);
        // observe/annotate only record. That predicate is what colours the glyph, so
        // it is also what the "Why" slot states.
        const _acting = (m) => m === 'enforce' || m === 'review';
        const _MODE_MEANS = {
            observe: 'measured and recorded; the answer is delivered unchanged',
            annotate: 'delivered with a note recording what was detected',
            review: 'withheld and parked in the Assurance Inbox for your decision',
            enforce: 'withheld before it reaches the user',
            off: 'not running',
        };

        // #24 — a per-agent mode is only a protection while its deployment MASTER is
        // on: with a master off both runtimes short-circuit at their first line, yet
        // these stations rendered `observe · 7/7` and claimed measurement. The masters
        // ride the dashboard payload — the same vehicle JUDGED has always read its own
        // master from (`dash.enabled`); GROUNDED/GUARDED simply never got the same
        // treatment (one rule, one axis). An older backend omits the block: null means
        // UNKNOWN and must degrade to the per-profile reading — claiming "off" on
        // absent data would be the same falsehood in the other direction.
        // NB the eval SCORERS are gated by online-eval, not by these masters, so the
        // execution attribute stays a real measurement either way — only the
        // configuration attribute was lying.
        const _masters = dash.deployment_masters || null;
        const _masterOff = (k) => !!(_masters && _masters[k] === false);
        const gMasterOff = _masterOff('grounding_gate_enabled');
        const oMasterOff = _masterOff('guardrails_enabled');
        // The owed act is flipping the master — an admin act. Offering it to a
        // non-admin would be a CTA that cannot move the state (Rule 8.5), and the
        // grammar gate runs this body under Node where module state is absent.
        const _canAdmin = (typeof _isAdmin !== 'undefined') && _isAdmin;
        const _masterCta = _canAdmin
            ? { label: 'Open evaluation settings', action: 'admin-eval' }
            : null;

        const gModes = profiles.map((p) => _gateMode(p, 'grounding',
            () => _cfgMode(p.groundingConfig, 'observe')));
        const gActing = gModes.filter(_acting).length;
        const gMode = gModes[0] || 'off';
        const gCorpus = one ? _gateMode(profiles[0], 'corpus',
            () => _cfgMode(profiles[0] && profiles[0].groundingConfig
                && profiles[0].groundingConfig.corpus, gMode)) : null;
        // GROUNDED's key set is what its measurements are ABOUT (the LABEL map) — every dimension the
        // registry's grounded mechanisms own, and nothing any other station counts.
        // (`honest_absence` is here because over-claiming an ABSENCE is the
        // answer-to-evidence relationship failing at the opposite polarity.)
        const gKeys = STATION_DIMS.grounded;
        const gShort = _dimShortfall(oncell, gKeys);
        // Laggards: the agents that cannot act. This is what makes the amber
        // actionable rather than merely accurate.
        const gLag = profiles.filter((p) => !_acting(_gateMode(p, 'grounding',
            () => _cfgMode(p.groundingConfig, 'observe'))))
            .map((p) => `@${p.tag}`);
        const gTurns = (oncell && oncell.turns) || 0;
        // #62b — the primary figure is the PER-TURN UNION over the full window:
        // "x of N turns clean", a stable denominator (the reading the station always
        // promised). Falls back, on an older backend without `turn_failed_dims`, to
        // #62's worst-dim-over-its-own-sample — narrow but never the 0/18 projection.
        const gClean = _turnCleanRate(oncell, gKeys);
        const gWorst = _worstDim(oncell, gKeys);
        // Rule 3.0c — turns flagged by the coarse lexical check and cleared by the
        // judge that refines it. They leave the rate alone and floor the station at
        // warn; the qualifier and Evidence name them, so the amber is actionable.
        const gDisputed = (gClean && gClean.disputed) || _disputedOnlyTurns(oncell, gKeys);
        const gN = gClean ? gClean.window : ((gWorst && gWorst.n) || gTurns);
        // #66 — with NEITHER a union NOR a measured worst dim there is no reading:
        // the old `rate == null ? 1` treated "nothing measured" as "everything
        // clean" and fabricated a full-window figure out of nothing.
        const gEstate = _estateExecRollup(dash, gKeys);
        const grounded = _assemble('grounded',
            gMasterOff
                ? {
                    // #24 — the master is OFF: whatever the profile says, no gate
                    // runs. A configured protection that silently isn't running is
                    // `warn` (declared intent contradicted — the same reasoning that
                    // keeps an unresolvable authorityScope out of `na`); an
                    // unconfigured default under an off master is genuinely
                    // not-turned-on.
                    state: gActing ? 'warn' : 'na',
                    text: 'off',
                    detail: gActing
                        ? (one ? `${_postureLabel(gMode)} configured — the master switch is off`
                               : `${gActing} configured to act — the master switch is off`)
                        : 'master switch off',
                }
                : {
                // Per-agent classification first, then the declared policy — never a
                // `.some()` over the estate, which reported the best member as the
                // state of the whole (1 acting of 10 rendered green).
                state: _postureAggregate(gModes.map(_postureState)),
                text: one ? _postureLabel(gMode) : `${gActing}/${N}`,
                detail: one
                    ? (gCorpus && gCorpus !== gMode ? `corpus support ${_postureLabel(gCorpus)}` : '')
                    : _countModes(gModes),
            },
            one
                // GROUNDED's execution is now its GATE'S OWN WORK — withholdings and
                // cleared escalations, the in-flight evidence that is genuinely this
                // guarantee's. Its 14 measurements moved to JUDGED with every other
                // measurement (see `_execAtJudged` for why `na` rather than a pass).
                ? _execAtJudged(_gateWork(oncell, { blocked: 1, cleared: 1 }),
                                gKeys.length)
                // Estate scope follows the focused reading: measurement moved to
                // JUDGED, so the estate rollup over this station's measurement keys
                // would re-introduce at estate altitude exactly the blend the lane cut
                // removed one level down — and the estate would then disagree with
                // every drill-down beneath it.
                : _execAtJudged('', gKeys.length),
            {
                headline: gMasterOff
                    ? 'The grounding gate is switched off for this deployment — per-agent modes are not running.'
                    : one
                    ? `Drift from the declared scope or the corpus is ${_MODE_MEANS[gMode] || 'not running'}.`
                    : `${gActing} of ${N} agents can actually withhold a drifting answer.`,
                why: gMasterOff
                    ? 'the deployment master overrides every per-agent mode; no gate runs while it is off'
                    : 'enforce can withhold a drifting answer; watch only records it, with or without disclosure',
                // Evidence must never read "none in this window" on a turn the gate
                // acted on. The scorers' instances come first (they name WHICH turns);
                // the gate's acts follow, because a cleared escalation or a withheld
                // answer is evidence the scorers cannot produce (#36).
                evidence: [
                    // The inert fact leads when the master is off; the scorers'
                    // instances stay (they measure under online-eval regardless).
                    gMasterOff
                        ? (gActing
                            ? `configured but inert while the master is off: ${
                                _briefList(profiles.filter((p) => _acting(_gateMode(p, 'grounding',
                                    () => _cfgMode(p.groundingConfig, 'observe'))))
                                    .map((p) => `@${p.tag}`))}`
                            : 'no agent is configured to act, and the master is off')
                        : '',
                    ...(one
                        ? [(_shortfallInstances(oncell, gKeys) || gShort),
                           _gateWork(oncell, { blocked: 1, cleared: 1 })]
                        : [
                            // The agents behind the worst-of colour — a red the
                            // reader cannot locate is alarm without a handle.
                            gEstate.short.length
                                ? `below the healthy floor on grounding checks: ${_shortAgentNames(gEstate.short)}` : '',
                            (!gMasterOff && gLag.length) ? `not acting: ${_briefList(gLag)}` : '',
                        ]),
                ].filter(Boolean).join(' · '),
                cta: (gMasterOff && _masterCta) || { label: 'Open the posture matrix', action: 'posture-matrix' },
                // #17 — when EXECUTION drove the colour, the answer is in the turns.
                execCta: { label: "Open this agent's runs", action: 'roster' },
                // Grounding + honesty observations both land at GROUNDED (the partition).
                acted: !!(rActed && rActed.grounded),
                openItems: (rOpenItems && rOpenItems.grounded) || null,
                derivation: _deriv({
                    // The derivation describes the SAME subject as the figure beside it:
                    // turns when focused, AGENTS at estate scope. Reading the focused
                    // variables unconditionally put "no scored turns in this window"
                    // directly under "4 of 5 scored agents clean" — two slots
                    // disagreeing about whether anything was measured at all.
                    // W3 (trust trip): the window names the LEG when the trip lens
                    // is active — focused: this agent's boundary; estate: the honest
                    // mix ("each since its own last reset where one exists").
                    window: one
                        ? (tripMeta
                            ? (gN ? `${_plural(gN, 'scored turn')} since the trip reset on ${legSince}`
                                  : `no scored turns since the trip reset on ${legSince} — the leg is empty, not clean`)
                            : (gN ? `${_plural(gN, 'scored turn')} in this window` : 'no scored turns in this window'))
                        : (gEstate.total
                            ? `${_plural(gEstate.total, 'scored agent')} in this window`
                              + (_circleLens === 'trip'
                                  ? ` — each since its own last reset where one exists (${gEstate.legged} of ${gEstate.total} have an open leg)` : '')
                            : 'no scored agents in this window'),
                    config: 'Configuration is the grounding gate mode of each agent in scope, aggregated worst-of (one agent in observe pulls the estate down — the strongest member never hides another\'s exposure).',
                    exec: !one
                        ? (gEstate.total
                            ? `Execution classifies AGENTS at estate scope: each scored agent is banded by its own turns — the same colour its focused ring would show — and the station takes the WORST band, so the estate never reads better than its weakest member. ${gEstate.quiet} of ${gEstate.total} agents read quiet.`
                            : 'No agent was scored on these checks in this window, so there is no execution figure.')
                        : _execDeriv(_gateWork(oncell, { blocked: 1, cleared: 1 }),
                                     gKeys.length, 'withholdings and cleared escalations'),
                    graded: Object.assign(_gradedAt(oncell, gKeys), { disputed: gDisputed || 0 }),
                    note: (gDisputed ? 'Among the checks above, a coarse check and the judge that refines it DISAGREE on ' + _plural(gDisputed, 'turn') + '. The disagreement is reported rather than resolved: weighting the judge would hide which measure spoke, dropping the coarse check would silence one that runs on every turn. It is resolved at JUDGED, where both are counted. ' : '')
                        + _JUDGED_NOTE,
                }),
            });

        const outModes = profiles.map((p) => _gateMode(p, 'outbound',
            () => _cfgMode(p.guardrailConfig && p.guardrailConfig.outbound, 'observe')));
        const oActing = outModes.filter(_acting).length;
        const oMode = outModes[0] || 'off';
        const inMode = one ? _gateMode(profiles[0], 'inbound',
            () => _cfgMode(profiles[0] && profiles[0].guardrailConfig
                && profiles[0].guardrailConfig.inbound, 'observe')) : null;
        const oKeys = STATION_DIMS.guarded;   // the checks ABOUT this guarantee (label)
        const oShort = _dimShortfall(oncell, oKeys);
        const oLag = profiles.filter((p) => !_acting(_gateMode(p, 'outbound',
            () => _cfgMode(p.guardrailConfig && p.guardrailConfig.outbound, 'observe'))))
            .map((p) => `@${p.tag}`);
        const oTurns = (oncell && oncell.turns) || 0;
        // #62b — same figure model as GROUNDED: per-turn union first, worst-dim
        // over its own sample as the older-backend fallback.
        const oClean = _turnCleanRate(oncell, oKeys);
        const oWorst = _worstDim(oncell, oKeys);
        const oN = oClean ? oClean.window : ((oWorst && oWorst.n) || oTurns);
        // #66 — with NEITHER a union NOR a measured worst dim there is no reading:
        // the old `rate == null ? 1` treated "nothing measured" as "everything
        // clean" and fabricated a full-window figure out of nothing.
        const oEstate = _estateExecRollup(dash, oKeys);
        const guarded = _assemble('guarded',
            oMasterOff
                ? {
                    // #24 — see GROUNDED. The master gates BOTH directions, so the
                    // inbound/outbound pair collapses to the one fact that governs them.
                    state: oActing ? 'warn' : 'na',
                    text: 'off',
                    detail: oActing
                        ? (one ? `${_postureLabel(oMode)} configured — the master switch is off`
                               : `${oActing} configured to act — the master switch is off`)
                        : 'master switch off',
                }
                : {
                state: _postureAggregate(outModes.map(_postureState)),
                text: one ? (inMode === oMode ? _postureLabel(oMode)
                                              : `${_postureLabel(oMode)} · ${_postureLabel(inMode)}`)
                          : `${oActing}/${N}`,
                // The mode's MEANING belongs to the headline; repeating it here is what
                // made the panel read as the same sentence twice.
                detail: one ? (inMode !== oMode ? `inbound ${_postureLabel(inMode)}` : '')
                    : _countModes(outModes),
            },
            one
                // GUARDED's execution is its GUARDS' OWN RECORD — refusals and
                // redactions. Both lanes were always structurally invisible to the
                // scorers (an inbound refusal is never scored, #50; an outbound
                // redaction is scored AFTER the redaction so it reads clean by
                // construction, #53), which is precisely why the gate's own record is
                // the honest execution evidence here and the measurement rate never was.
                ? _execAtJudged(_gateWork(oncell, { refused: 1, redacted: 1 }),
                                oKeys.length)
                // Estate follows the focused reading — see GROUNDED for why an estate
                // rollup over measurement keys would re-introduce the blend one level up.
                : _execAtJudged('', oKeys.length),
            {
                headline: oMasterOff
                    ? 'Guardrails are switched off for this deployment — per-agent modes are not running.'
                    : one
                    ? `A finding on the way out is ${_MODE_MEANS[oMode] || 'not acted on'}.`
                    : `${oActing} of ${N} agents can actually redact or withhold an outgoing answer.`,
                why: oMasterOff
                    ? 'the deployment master overrides every per-agent mode; nothing is screened in either direction while it is off'
                    : 'enforce can redact or withhold an answer; watch only records it, with or without disclosure',
                // See GROUNDED: a refusal or a redaction is evidence no scorer can
                // produce, and "none in this window" beside a guard that acted is the
                // exact misreading #50/#53 record.
                evidence: [
                    oMasterOff
                        ? (oActing
                            ? `configured but inert while the master is off: ${
                                _briefList(profiles.filter((p) => _acting(_gateMode(p, 'outbound',
                                    () => _cfgMode(p.guardrailConfig && p.guardrailConfig.outbound, 'observe'))))
                                    .map((p) => `@${p.tag}`))}`
                            : 'no agent is configured to act, and the master is off')
                        : '',
                    ...(one
                        ? [(_shortfallInstances(oncell, oKeys) || oShort),
                           _gateWork(oncell, { refused: 1, redacted: 1 })]
                        : [
                            oEstate.short.length
                                ? `below the healthy floor on safety checks: ${_shortAgentNames(oEstate.short)}` : '',
                            (!oMasterOff && oLag.length) ? `not acting: ${_briefList(oLag)}` : '',
                        ]),
                ].filter(Boolean).join(' · '),
                cta: (oMasterOff && _masterCta) || { label: 'Open the posture matrix', action: 'posture-matrix' },
                execCta: { label: "Open this agent's runs", action: 'roster' },
                // Guardrail / firewall / injection observations land at GUARDED.
                acted: !!(rActed && rActed.guarded),
                openItems: (rOpenItems && rOpenItems.guarded) || null,
                derivation: _deriv({
                    // Same-subject rule as GROUNDED: turns when focused, agents at
                    // estate scope — never the focused variables under an estate figure.
                    // W3 (trust trip) — same disclosure contract as GROUNDED.
                    window: one
                        ? (tripMeta
                            ? (oN ? `${_plural(oN, 'scored turn')} since the trip reset on ${legSince}`
                                  : `no scored turns since the trip reset on ${legSince} — the leg is empty, not clean`)
                            : (oN ? `${_plural(oN, 'scored turn')} in this window` : 'no scored turns in this window'))
                        : (oEstate.total
                            ? `${_plural(oEstate.total, 'scored agent')} in this window`
                              + (_circleLens === 'trip'
                                  ? ` — each since its own last reset where one exists (${oEstate.legged} of ${oEstate.total} have an open leg)` : '')
                            : 'no scored agents in this window'),
                    config: 'Configuration is the OUTBOUND guardrail mode of each agent in scope, aggregated worst-of. A gate whose platform master switch is off reads as off, however the profile is set.',
                    exec: !one
                        ? (oEstate.total
                            ? `Execution classifies AGENTS at estate scope: each scored agent is banded by its own turns — the same colour its focused ring would show — and the station takes the WORST band, so the estate never reads better than its weakest member. ${oEstate.quiet} of ${oEstate.total} agents read quiet.`
                            : 'No agent was scored on these checks in this window, so there is no execution figure.')
                        : _execDeriv(_gateWork(oncell, { refused: 1, redacted: 1 }),
                                     oKeys.length, 'refusals and redactions'),
                    graded: _gradedAt(oncell, oKeys),
                    note: _JUDGED_NOTE,
                }),
            });
        // Bounded reads the RESOLVED scope from the server (`authorityScope.restricted`),
        // never a presence check over `authorityConfig`. A config block that exists but
        // constrains nothing — autonomous, no denials, no caps — is not a bounded agent,
        // and reporting it as one turned the least-bounded agent in an estate green.
        const nBounded = profiles.filter((p) => p.authorityScope && p.authorityScope.restricted).length;
        const nExposed = profiles.length - nBounded;
        const bScope = one ? (profiles[0] && profiles[0].authorityScope) || {} : null;
        const bConstraints = (bScope && bScope.constraints) || [];
        // An UNRESOLVABLE scope (older backend that does not compute authorityScope)
        // classifies as `warn`, not `na`: unknown authority must never read as
        // "nothing enabled here", which would understate real exposure. The Evidence
        // slot distinguishes unresolved from resolved-and-unconstrained.
        const bLag = profiles.filter((p) => !(p.authorityScope && p.authorityScope.restricted))
            .map((p) => `@${p.tag}`);
        const bUnresolved = profiles.filter((p) => !p.authorityScope).length;
        // Autonomous denials are not aggregated anywhere, so this station's execution
        // side is genuinely absent. It renders `—` rather than a fabricated figure.
        //
        // Rule 8.1 — an unscoped agent is only OUTSTANDING where the practice it protects
        // is in use. Authority scoping guards UNATTENDED execution, so an agent nothing can
        // drive that way owes no scope and `na` (dashed) is the honest reading. Without this
        // BOUNDED was the one station that could never go green — the exact "a badge that
        // can never go green gets ignored" failure §8 documents for `unsigned`.
        //
        // PER-AGENT, and deliberately NOT the deployment-wide form Rule 8.2 mandates for
        // SIGNED. That rule exists because a per-agent signing signal would be CIRCULAR
        // ("is this agent signed" answering "should it be"). Reachability is independent of
        // scoping, so no circularity arises — and the coarse reading was simply WRONG:
        // one coordinator somewhere does not expose an agent it never calls, which is how a
        // Focus agent no coordinator lists read amber for an exposure it does not have.
        //
        // ABSENT signal (older backend) reads EXPOSED: Rule 8.1 forbids `na` unless we can
        // show nothing is owed, so the honest error is a needless amber, never a dash over
        // real exposure.
        const _exposed = (p) => (p.runsUnattended !== false);
        // The deployment's OBDA policy ("contracts only", per plane — core/obda_planes). An
        // ENFORCED plane is a constraint every agent carries (it is applied into each agent's
        // resolved scope, so `authorityScope.restricted` already reads it); an OBSERVED plane
        // constrains nothing and is named as the rehearsal it is. Absent (older backend) ⇒ silent.
        const _policy = (_masters && _masters.obda_policy && typeof _masters.obda_policy === 'object') ? _masters.obda_policy : null;
        const _polWord = { read: 'reads', model: 'model changes', act: 'writes' };
        const _polEnforced = _policy ? Object.keys(_polWord).filter((k) => _policy[k] === 'enforce').map((k) => _polWord[k]) : [];
        const _polObserved = _policy ? Object.keys(_polWord).filter((k) => _policy[k] === 'observe').map((k) => _polWord[k]) : [];
        const _polEvidence = _polEnforced.length || _polObserved.length
            ? 'OBDA policy — ' + [_polEnforced.length ? `contracts only for ${_polEnforced.join(', ')} (enforced)` : '',
                                  _polObserved.length ? `${_polObserved.join(', ')} observed (a rehearsal: what enforce would refuse is recorded, nothing refused)` : '']
                .filter(Boolean).join(' · ')
            : '';
        const bAtRisk = profiles.filter((p) => !(p.authorityScope && p.authorityScope.restricted) && _exposed(p));
        const nAtRisk = bAtRisk.length;
        const oneExposed = one && !!profiles.length && _exposed(profiles[0]);
        // The checks ABOUT this guarantee — tool conduct. These dims were
        // previously counted at JUDGED while the dial's bounded mechanisms declared
        // them, so the two instruments disagreed; under the partition they are counted
        // HERE and nowhere else. Autonomous DENIALS are still not aggregated — the
        // checks below measure tool conduct, not denials, and the derivation says so.
        const bKeys = STATION_DIMS.bounded;
        const bDimShort = _dimShortfall(oncell, bKeys);
        const bClean = _turnCleanRate(oncell, bKeys);
        const bWorstD = _worstDim(oncell, bKeys);
        const bTurns = (oncell && oncell.turns) || 0;
        const bN = bClean ? bClean.window : ((bWorstD && bWorstD.n) || bTurns);
        const bEstate = _estateExecRollup(dash, bKeys);
        const bounded = _assemble('bounded',
            {
                state: _postureAggregate(profiles.map((p) =>
                    (p.authorityScope && p.authorityScope.restricted) ? 'pass'
                        : (_exposed(p) ? 'warn' : 'na'))),
                text: !profiles.length ? '' : one ? (nBounded ? 'scoped' : 'unscoped') : `${nBounded}/${N}`,
                detail: !profiles.length ? ''
                    : one ? (!profiles[0].authorityScope ? 'authority scope could not be resolved for this agent'
                        : nBounded ? `scoped: ${bConstraints.join(', ')}`
                        : `unscoped — autonomous${bScope && bScope.autonomous === false ? ' off' : ' on'}, no denied tools, no per-run cost cap`
                          + (oneExposed ? '' : '; nothing can drive it unattended, so no scope is owed'))
                    : `${nBounded} of ${N} agents restrict what they may do`,
            },
            one
                // BOUNDED has no gate acts to report at ring altitude — autonomous
                // denials are enforced per tool call and never aggregated — so its
                // execution reads `—` with its four tool-conduct checks named at
                // JUDGED. That absence is pre-existing and now stated plainly rather
                // than papered over with a measurement rate belonging to another lane.
                ? _execAtJudged('', bKeys.length)
                // Estate follows the focused reading — see GROUNDED.
                : _execAtJudged('', bKeys.length),
            {
                headline: !one
                    ? (nAtRisk
                        ? `${nAtRisk} of ${N} agents would act with your full authority if run unattended.`
                        : nExposed
                            ? `No agent that can run unattended is unscoped — the ${_plural(nExposed, 'unscoped agent')} here cannot be driven that way.`
                            : `All ${N} agents narrow their own reach.`)
                    : !nExposed
                    ? 'Autonomous acts run as a bounded principal — a strict subset of your own authority.'
                    : oneExposed
                    ? 'If it ever runs unattended, it would act with your full authority.'
                    : 'Nothing can drive this agent unattended, so no authority scope is owed.',
                // Rule 8.3 — name the NATURE of the absence, not just the level.
                why: (one ? oneExposed : !!nAtRisk)
                    ? 'a config block only counts when it actually constrains: autonomy off, a denial, or a cap'
                    : one
                    ? 'authority scoping guards unattended runs; no coordinator, schedule or flow can drive this agent that way, so none is owed'
                    : 'authority scoping guards unattended runs; every unscoped agent here is one nothing can drive that way, so none is owed',
                // TC-85: the focused slot previously fell to `_NONE` ("none in this
                // window") — a measurement-window phrase on a config-only station with no
                // window. Name the CONSTRAINTS (or the unresolved state) instead, so the
                // unresolved-vs-unconstrained distinction exists at focused scope too.
                evidence: [
                    one
                        ? (!profiles.length ? ''
                            : !profiles[0].authorityScope
                                ? 'the server did not resolve an authority scope for this agent (older backend)'
                            : bConstraints.length
                                ? `constraints in force: ${_briefList(bConstraints)}`
                                : oneExposed
                                    ? 'no constraints configured — autonomy, tools and spend are all unrestricted'
                                    : 'no constraints configured, and no coordinator, schedule or flow lists this agent')
                        : (nAtRisk
                            ? `unconstrained and reachable unattended: ${_briefList(bAtRisk.map((p) => `@${p.tag}`))}` +
                              (bUnresolved ? ` · ${_plural(bUnresolved, 'scope')} unresolved` : '')
                            : bLag.length
                                ? `unconstrained: ${_briefList(bLag)} — none reachable unattended`
                                : 'every agent narrows its own reach'),
                    // the deployment's OBDA policy (contracts only) — a constraint on every agent
                    // when enforced, a rehearsal when observed
                    _polEvidence,
                    // OBDA-ACT — what the agent wrote through its signed actions, and what was
                    // refused (the boundary holding), named — read from the turns.
                    _actEvidence(profiles.map((p) => p.tag)),
                    // The tool-conduct instances (this station's own slice of the
                    // partition) — evidence the config reading cannot produce.
                    one
                        ? (_shortfallInstances(oncell, bKeys) || bDimShort)
                        : (bEstate.short.length
                            ? `below the healthy floor on tool-conduct checks: ${_shortAgentNames(bEstate.short)}` : ''),
                ].filter(Boolean).join(' · '),
                cta: { label: 'Open the posture matrix', action: 'posture-matrix' },
                execCta: { label: "Open this agent's runs", action: 'roster' },
                derivation: _deriv({
                    window: one
                        ? (bN ? `${_plural(bN, 'scored turn')} in this window · authority read from configuration`
                              : 'this agent — no scored turns on these checks in this window')
                        : (bEstate.total
                            ? `${_plural(bEstate.total, 'scored agent')} in this window · authority read from configuration`
                            : `${_plural(N, 'agent')} in scope`),
                    config: 'Configuration asks whether the agent\'s authority actually CONSTRAINS it — autonomy off, a denied tool, a connector allow-list, or a per-run cost cap. A config block that exists but restricts nothing is not a bounded agent. An unscoped agent is only flagged if something can actually drive it unattended — a coordinator that lists it as an expert, a scheduled task, or a flow node. One that nothing can drive that way owes no scope and reads as a dash, not a warning.',
                    exec: _execDeriv('', bKeys.length, 'autonomous tool denials'),
                    graded: _gradedAt(oncell, bKeys),
                    note: 'The authority reading itself comes from the resolved scope the '
                        + 'server computes once (core/agent_authority), the same one the '
                        + 'enforcer applies — configuration, not measurement. ' + _JUDGED_NOTE,
                }),
            });

        // 5 · Judged (wire key `scored`) — the live eval track record over the checks
        // THIS station owns: the execution-quality RESIDUE of the partition (whatever
        // no other station claims, plus its own governed-data check). JUDGED was the
        // UNION until the partition landed, so one failing grounding dimension raised
        // two stations — a double count wearing corroboration's clothes. Now every
        // dimension is counted at exactly one station, and TRUSTED is the single
        // aggregate that composes all of them into the standing.
        // The label went judged→scored (Aug) and BACK: once TRUSTED's value became a
        // conduct SCORE, "Scored" carried two meanings on one ring. The wire key
        // stays `scored`; only the surface renames.
        const spans = agent ? ((oncell && oncell.turns) || 0) : (dash.total_spans || 0);
        const jKeys = one ? _judgedKeys(oncell) : null;
        const jDimShort = one ? _dimShortfall(oncell, jKeys) : '';
        const jClean = one ? _turnCleanRate(oncell, jKeys) : null;
        // Rule 3.0c's disputed count follows the measurements to the station that
        // counts them (see the state expression below).
        const jDisputed = (jClean && jClean.disputed)
            || (one ? _disputedOnlyTurns(oncell, jKeys) : 0);
        const jWorstD = one ? _worstDim(oncell, jKeys) : null;
        const jRate = jClean ? (jClean.window ? jClean.clean / jClean.window : null)
            : (jWorstD ? jWorstD.rate : null);
        const jN = jClean ? jClean.window : ((jWorstD && jWorstD.n) || spans);
        const jHas = !!(jClean || jWorstD);
        const jX = jClean ? jClean.clean : (jHas && jN ? Math.round(jWorstD.rate * jN) : 0);
        const jUnit = (jClean || jN === spans) ? 'turn' : 'scored turn';
        const jFails = jHas && jN ? (jN - jX) : 0;
        // Estate colour = worst-of over each agent's own RESIDUE band — the key set is
        // computed PER CELL (a function, not a list), because what was measured depends on
        // which dimensions each agent's class actually produced.
        const jEstate = one ? null : _estateExecRollup(dash, _judgedKeys);
        // Worst-first AMONG THE RESIDUE, so Evidence names what is dragging THIS
        // station down — never a failure another station already counts.
        const jDims = (oncell && oncell.dimensions) || {};
        const jCounts = (oncell && oncell.dimension_counts) || {};
        const jWorst = (jKeys || [])
            .filter((k) => typeof jDims[k] === 'number' && jDims[k] < 1)
            .sort((a, b) => jDims[a] - jDims[b])
            .slice(0, 3)
            // TC-84: rate-only fallback when the count is missing (older spans) — no "0/0".
            .map((k) => jCounts[k]
                ? `${_dimLabel(k)} ${Math.round(jDims[k] * jCounts[k])}/${jCounts[k]} passed`
                : `${_dimLabel(k)} ${Math.round(jDims[k] * 100)}% pass rate`)
            .join(' · ');
        const scored = _assemble('scored',
            {
                state: dash.enabled ? 'pass' : 'na',
                text: dash.enabled ? 'on' : 'off',
                detail: dash.enabled ? 'live scoring on, 80% green floor / 50% danger floor'
                    : 'live scoring is off, so no turn is graded',
            },
            one
                ? (jHas && jN
                    ? {
                        // Rule 3.0c — a clean rate resting on DISPUTED turns must not
                        // read green: the coarse check flagged them and only the judge
                        // that refines it cleared them, which is exactly "worth a look".
                        // The floor never LOWERS a danger. This rule followed the
                        // measurements here from GROUNDED, because it is a rule about
                        // how two MEASUREMENTS of one property are counted — and
                        // counting is what moved.
                        state: (jDisputed && _execBand(jRate) === 'pass')
                            ? 'warn' : _execBand(jRate),
                        text: _pctText(jX, jN),
                        // W3 (trust trip): the boundary rides the reading; an empty leg
                        // says so — never "no execution data" over an active lens.
                        detail: `${jX}/${jN} — ${_expandRate(jX, jN, jUnit, 'clean on every check measured')}`
                            + (tripMeta ? ` — this leg (since ${legSince})` : '')
                            + (jClean && jN < spans ? ` — the trailing ${jN} of ${spans} turns` : '')
                            + (jDisputed ? ` — ${_plural(jDisputed, 'turn')} flagged by the coarse lexical check and cleared by the judge that refines it` : '')
                            + (jDimShort ? ` — shortfall: ${jDimShort}` : ''),
                    }
                    : {
                        state: 'na', text: '',
                        detail: tripMeta
                            ? `no scored turns since the trip reset on ${legSince} — the leg is empty, not clean`
                            : '',
                    })
                : {
                    // Worst-of over per-agent RESIDUE bands (see jEstate above).
                    // x/y, not % — this counts AGENTS (see the format rule at _pctText).
                    state: jEstate.state,
                    text: jEstate.total ? `${jEstate.quiet}/${jEstate.total}` : '',
                    // Rule 3.4/3.0 — see GROUNDED: expand the anchor, never restate
                    // its digits or state its complement.
                    detail: jEstate.total
                        ? _expandRate(jEstate.quiet, jEstate.total,
                                      'scored agent', 'read quiet on every check measured')
                        : '',
                },
            {
                headline: one
                    ? (!jN ? 'No turns have been scored on these checks in this window.'
                        : jFails ? `${jFails} of ${_plural(jN, 'scored turn')} fell short on the checks measured for this agent.`
                        // singular grammar: "All 1 scored turn" read as a glitch (#68).
                        : (jN === 1 ? 'The 1 scored turn came through clean on every check measured.'
                            : `All ${_plural(jN, 'scored turn')} came through clean on every check measured.`))
                    : (jEstate.total
                        ? (jEstate.short.length
                            ? `${jEstate.short.length} of ${_plural(jEstate.total, 'scored agent')} fall short on the checks measured for them.`
                            : `All ${_plural(jEstate.total, 'scored agent')} read quiet on every check measured.`)
                        : 'No agent has been scored on these checks in this window.'),
                // The Why explains the COLOUR, so at estate scope it must speak in the
                // worst-of vocabulary — a pooled "at or above the 80% green floor"
                // beside a red driven by one agent would explain a different reading.
                why: one
                    ? (!jN ? 'no evidence yet'
                        : jRate >= 0.8 ? 'at or above the 80% green floor'
                        : jRate >= 0.5 ? 'below the 80% green floor' : 'below the 50% floor')
                    : ({ pass: 'every scored agent is at or above the 80% green floor',
                         warn: 'the weakest scored agent is below the 80% green floor',
                         danger: 'the weakest scored agent is below the 50% floor' }[jEstate.state]
                       || 'no evidence yet'),
                evidence: one
                    ? (jWorst || (jN ? 'every check measured on this turn passed' : ''))
                    : (jEstate.total
                        ? (jEstate.short.length
                            ? `below the healthy floor on these checks: ${_shortAgentNames(jEstate.short)}`
                            : `no failures across ${_plural(jEstate.total, 'scored agent')}`)
                        : ''),
                cta: { label: 'Open the agent roster', action: 'roster' },
                derivation: _deriv({
                    // Same-subject rule as GROUNDED/GUARDED: turns when focused,
                    // AGENTS at estate scope — the window describes what the figure counts.
                    // W3 (trust trip): the window names the leg when the lens is active.
                    window: one
                        ? (tripMeta
                            ? (jN ? `${_plural(jN, 'scored turn')} since the trip reset on ${legSince}`
                                  : `no scored turns since the trip reset on ${legSince} — the leg is empty, not clean`)
                            : (jN ? `${_plural(jN, 'scored turn')} in this window` : 'no scored turns in this window'))
                        : (jEstate.total
                            ? `${_plural(jEstate.total, 'scored agent')} in this window`
                              + (_circleLens === 'trip'
                                  ? ` — each since its own last reset where one exists (${jEstate.legged} of ${jEstate.total} have an open leg)` : '')
                            : 'no scored agents in this window'),
                    config: 'Configuration is simply whether live scoring is switched on for this deployment, with its green and danger floors.',
                    exec: !one
                        ? (jEstate.total
                            ? `Execution classifies AGENTS at estate scope: each scored agent is banded by its own measurements — the same colour its focused ring would show — and the station takes the WORST band, so the estate never reads better than its weakest member. ${jEstate.quiet} of ${jEstate.total} agents read quiet.`
                            : 'No agent was scored in this window, so there is no execution figure.')
                        : jN
                        ? `Execution counts TURNS over EVERY check this agent's class measured — a turn is clean only if it passed all of them. ${jX} of ${jN} turns were clean.`
                          // W7 — the comparison a reset exists to make.
                          + ((() => { const pf = tripMeta && _legPrevFigure(tripMeta.prev, jKeys);
                              return pf ? ` The previous leg read ${pf.x}/${pf.n} clean — the comparison a reset exists to make.` : ''; })())
                        : 'No turns were scored in this window, so there is no execution figure.',
                    dims: _dimRows(oncell, jKeys || []),
                    // Three sentences: the rule, the judge cap, the aggregate. The history
                    // of how JUDGED came to be defined lives in the casebook, not here (W6).
                    note: 'JUDGED is where MEASUREMENT is counted — all of it, and nowhere '
                        + 'else: the other seven stations report what their MECHANISMS did '
                        + 'during the turn, and each check above keeps the guarantee it is '
                        + 'ABOUT as a label that moves no colour. A judge alone in danger holds '
                        + 'this station at "worth a look": the platform\'s judges stay advisory '
                        + 'until their calibration clears, the same rule that decides whether a '
                        + 'turn failed. TRUSTED remains the single aggregate over the record.',
                }),
            });

        // 6 · Trusted — the behavioral badge (breathes with conduct).
        const tb = _trustBehavior && _trustBehavior.agents ? _trustBehavior.agents : null;
        let trusted;
        if (!tb) {
            trusted = _assemble('trusted',
                { state: 'na', text: 'off', detail: 'the Behavioral Trust Score is disabled for this deployment' },
                { state: 'na', text: '', detail: 'no conduct can be rated while the score is off' },
                {
                    headline: 'Behavioral trust is switched off, so the badge cannot move with conduct.',
                    why: 'the feature is not enabled, so there is nothing to report',
                    // #65 (solo sweep) — the toggle lives on the SAME admin panel as the
                    // deployment masters (App Config → Trust, the station-partitioned
                    // page), so the admin route is reused; the prose names the route
                    // that actually exists.
                    evidence: 'an administrator can enable it under Administration → App Config → Trust → the TRUSTED station',
                    cta: _masterCta || { label: 'Open the agent roster', action: 'roster' },
                    derivation: _deriv({
                        window: 'not in use on this deployment',
                        config: 'Configuration is the Behavioral Trust Score master switch, which is off — so nothing here is measured.',
                        exec: 'With the score off there is no conduct rating to report.',
                        note: 'When enabled, this station fuses the Trust-Membrane integrity state with a recency-weighted pass rate over a trailing window; integrity can only CAP conduct, never promote it.',
                    }),
                });
        } else {
            // TC-35: cells may be keyed by tag (my scope) or `tag::uuid` (system scope).
            const cell = agent ? _tbAgent(agent.key, agent.tag) : null;
            // TC-8: the estate reading is YOUR estate — in system scope keep only the
            // caller's own cells (same subject rule as the dashboard figures above).
            const _selfCells = Object.entries(tb)
                .filter(([k, x]) => !String(k).includes('::')
                    || String((x && x.user_uuid) || String(k).split('::')[1] || '')
                        === String(_trustBehavior.self_uuid || ''))
                .map(([, x]) => x);
            const states = agent
                ? [cell && cell.composite && cell.composite.state].filter(Boolean)
                : _selfCells.map((x) => x && x.composite && x.composite.state).filter(Boolean);
            const nProven = states.filter((s) => s === 'proven' || s === 'trusted').length;
            const beh = (cell && cell.behavior) || {};
            const comp = (cell && cell.composite) || {};
            const breaker = _trustBehavior.breaker_mode || 'off';
            const capped = comp.integrity && comp.integrity !== 'trusted';
            trusted = _assemble('trusted',
                {
                    state: 'pass',
                    text: 'on',
                    detail: `behavioral trust on · circuit breaker ${breaker === 'None' ? 'off' : breaker}`,
                },
                {
                    // Classify PER AGENT through a declared map, then aggregate through the
                    // ONE policy (Rule 5.1 worst-of). The previous expression was a `.some()`
                    // in disguise — `has('proven') → pass` rendered the station GREEN with 2 of
                    // 9 agents proven and 7 provisional: verbatim the estate false green that
                    // `_postureAggregate` was introduced to kill, on the one station never
                    // swept. For a focused agent worst-of and best-of are identical, so the
                    // per-agent ring is unchanged. The `2/9` magnitude stays in the value
                    // (Rule 5.2), which is what keeps worst-of survivable.
                    state: _postureAggregate(states.map((s) => _BEHAVIOR_STATE[s] || 'na')),
                    // TC-10: render the HUMAN label, never the raw composite wire token — a
                    // focused at-risk agent used to read `on · at_risk` in the value slot.
                    // And the VALUE is the MEASUREMENT, not the band: "Watch" in the value
                    // slot was a state word wearing a value's clothes (the Rule 3.5 family),
                    // duplicated by its own detail ("Watch Watch — …") and quoting the
                    // UNBLENDED pass rate as the only figure. The conduct score is the
                    // station's measurement — the standing is its band, named beside it.
                    text: !states.length ? ''
                        : one ? (typeof beh.score === 'number' ? `${Math.round(beh.score * 100)}%`
                                 : (TRUST_STATE_LABEL[states[0]] || states[0]))
                        : `${nProven}/${states.length}`,
                    detail: !states.length ? ''
                        : one ? [`standing ${TRUST_STATE_LABEL[states[0]] || states[0]}`,
                                 typeof beh.score === 'number'
                                     ? `conduct ${Math.round(beh.score * 100)}% over ${_plural(beh.turns || 0, 'turn')}` : '',
                                 // The blend is a PARTITION (no dimension counted twice);
                             // an older record without the split falls back honestly.
                             (typeof beh.residual_rate === 'number' && typeof beh.trust_dim_rate === 'number')
                                     ? `= trust-critical ${Math.round(beh.trust_dim_rate * 100)}% · every other check ${Math.round(beh.residual_rate * 100)}%, each counted once`
                                     : (typeof beh.weighted_pass_rate === 'number' && typeof beh.trust_dim_rate === 'number')
                                     ? `= overall ${Math.round(beh.weighted_pass_rate * 100)}% blended with trust-critical ${Math.round(beh.trust_dim_rate * 100)}% (older record — no per-check split)` : '',
                                ].filter(Boolean).join(' — ')
                        : `${_plural(states.length, 'agent')} rated — ${[...new Set(states)].map((s) => TRUST_STATE_LABEL[s] || s).join(' · ')}`,
                },
                {
                    headline: !states.length ? 'No conduct has been recorded for this scope yet.'
                        : one
                        ? (breaker === 'off' || breaker === 'None'
                            ? `Its standing is ${TRUST_STATE_LABEL[states[0]] || states[0]}, and with the circuit breaker off a falling badge changes nothing.`
                            : `Its standing is ${TRUST_STATE_LABEL[states[0]] || states[0]}; the circuit breaker would ${breaker === 'freeze_autonomy' ? 'freeze autonomy' : 'notify you'} if it slipped.`)
                        : `${nProven} of ${states.length} agents have earned proven standing or better.`,
                    why: one ? (comp.reason || 'derived from conduct, capped by integrity') +
                            (capped ? `; integrity is ${comp.integrity}, which caps the ceiling` : '')
                        : 'conduct sets the level; integrity caps it',
                    // W5 (trust trip): the composite NEVER re-windows — the badge is
                    // how the platform judges the agent, not how you look at it. Under
                    // the trip lens the leg gets one clearly-labelled informational
                    // line beside the canonical reading, never in place of it.
                    evidence: one
                        ? [(Array.isArray(beh.trend) && beh.trend.length
                            ? `last ${_plural(beh.trend.length, 'turn')} ${beh.trend.map((v) => (v ? 'pass' : 'fail')).join(', ')}` +
                              (beh.stale ? ' · evidence is stale' : '')
                            : ''),
                           (tripMeta
                            ? (tripMeta.turns
                                ? `this leg: ${tripMeta.turns - (tripMeta.failures || 0)}/${tripMeta.turns} turns clean — the badge itself always reads the whole record`
                                : 'this leg is empty so far — the badge itself always reads the whole record')
                            : '')].filter(Boolean).join(' · ')
                        : `${_plural(states.length, 'agent')} carry behavioral evidence`,
                    cta: { label: 'Open the agent roster', action: 'roster' },
                    derivation: _deriv({
                        window: (beh && beh.turns) ? `${_plural(beh.turns, 'scored turn')}, recency-weighted`
                            : 'no scored turns yet',
                        config: `Configuration is the Behavioral Trust Score switch and its circuit breaker (currently ${(breaker === 'off' || breaker === 'None') ? 'off — a falling badge changes nothing' : breaker === 'freeze_autonomy' ? 'set to freeze autonomy' : 'set to notify'}).`,
                        exec: 'Execution is the COMPOSITE standing — the ONE aggregate on the ring: a recency-weighted blend in which the trust-critical dimensions below carry their configured weight and every remaining dimension carries the rest. The two terms PARTITION the evidence, so no dimension is counted twice; the result is then CAPPED by integrity — an unsigned agent cannot exceed proven, a changed one cannot exceed watch. A signature never promotes bad conduct.',
                        // The weighted list comes from the server (`trust_dims`), never a second
                        // hardcoded copy — the list that weights the score IS the list shown.
                        dims: _dimRows(agent && agent.online,
                            ((_trustBehavior && _trustBehavior.config && _trustBehavior.config.trust_dims) || [])),
                        note: (_trustBehavior && _trustBehavior.config && _trustBehavior.config.trust_dim_weight != null)
                            ? `These trust-critical dimensions carry ${Math.round(_trustBehavior.config.trust_dim_weight * 100)}% of the blend; the overall pass rate carries the rest.`
                            : 'These are the trust-critical dimensions the composite weights above the overall pass rate.',
                    }),
                });
        }

        // 7 · Reviewed — open human work (estate-wide inbox).
        // NB: the inbox is estate-wide — it is not filtered by the focused agent.
        // The Why slot says so, so a zero here is never read as "this agent is clean".
        // This station's CONFIGURATION is which gates can park an item at all: only a
        // gate in `review` mode ever produces human work. Without it the station is
        // structurally inert and a green "nothing waiting" says nothing about safety.
        // #24 — a review-mode gate can only PARK while its master is on: grounding
        // review needs the grounding master, guardrail review the guardrails master.
        // Honesty has no master switch, so its review always counts.
        // EFFECTIVE modes (agent-level inheritance resolved server-side) — an agent
        // whose gates inherit an agent-level `review` can park exactly as one with
        // gate-level review, and this reading must agree with the runtime.
        const _effMode = (p, gate, cfgFn) => _gateMode(p, gate, cfgFn);
        const _parkingActions = (p) => (p && Array.isArray(p.actionsParking)) ? p.actionsParking : [];
        const _canParkByGate = (p) =>
            (!gMasterOff && _effMode(p, 'grounding', () => _cfgMode(p.groundingConfig, 'observe')) === 'review')
            || (!oMasterOff && (_effMode(p, 'outbound', () => _cfgMode(p.guardrailConfig && p.guardrailConfig.outbound, 'observe')) === 'review'
                || _effMode(p, 'inbound', () => _cfgMode(p.guardrailConfig && p.guardrailConfig.inbound, 'observe')) === 'review'))
            || _effMode(p, 'honesty', () => _cfgMode(p.honestyConfig, 'observe')) === 'review';
        const _canPark = (p) =>
            (!gMasterOff && _effMode(p, 'grounding', () => _cfgMode(p.groundingConfig, 'observe')) === 'review')
            || (!oMasterOff && (_effMode(p, 'outbound', () => _cfgMode(p.guardrailConfig && p.guardrailConfig.outbound, 'observe')) === 'review'
                || _effMode(p, 'inbound', () => _cfgMode(p.guardrailConfig && p.guardrailConfig.inbound, 'observe')) === 'review'))
            || _effMode(p, 'honesty', () => _cfgMode(p.honestyConfig, 'observe')) === 'review'
            // A granted signed action in REVIEW posture parks the writes it would perform
            // for a person — the same parking as a review-mode gate (served by /v1/profiles
            // as `actionsParking`, read from the agent's own graph; never re-derived here).
            || _parkingActions(p).length > 0;
        const rParkers = profiles.filter(_canPark).length;
        const rActParkers = profiles.filter((p) => _parkingActions(p).length > 0);
        const rActNames = one ? [...new Set(profiles.flatMap(_parkingActions))] : [];
        // Review CONFIGURED but muted by an off master — "no gate is in review mode"
        // would be false there; the detail must name the actual cause.
        const rMuted = !rParkers && (gMasterOff || oMasterOff) && profiles.some((p) =>
            _effMode(p, 'grounding', () => _cfgMode(p.groundingConfig, 'observe')) === 'review'
            || _effMode(p, 'outbound', () => _cfgMode(p.guardrailConfig && p.guardrailConfig.outbound, 'observe')) === 'review'
            || _effMode(p, 'inbound', () => _cfgMode(p.guardrailConfig && p.guardrailConfig.inbound, 'observe')) === 'review');
        // Both attributes must describe the SAME subject. The configuration side is
        // per-agent by construction (it reads this agent's gate modes), so a focused
        // station reads this agent's inbox work — not the estate's. Pairing "none park"
        // with an estate-wide "3 open" put two scopes in one value, which reads as this
        // agent having three items waiting.
        const rMine = (one && _inboxByTag[agent.tag]) || null;
        const rOpen = one ? (rMine ? rMine.open : 0) : (_inboxBadgeCount || 0);
        const rDerived = one ? (rMine ? rMine.derived : 0) : _inboxDerivedCount;
        const rDanger = one ? (rMine ? rMine.derivedDanger : 0) : _inboxDerivedDanger;
        const rTop = one ? (rMine ? rMine.top : '') : _inboxDerivedTop;
        const reviewed = _assemble('reviewed',
            {
                state: rParkers ? 'pass' : 'na',
                // Focused on one agent, "1 can park" is `1/1` wearing a word — it reads
                // like a measurement of a population of one. The reading there is binary:
                // CAN this agent park work at all? Estate scope keeps the count, which
                // there is a real proportion.
                text: one ? (rParkers ? 'can park' : 'cannot park')
                    : rParkers ? `${rParkers} can park` : 'none park',
                detail: !rParkers
                    ? (rMuted
                        ? 'keep-for-review is configured, but its master switch is off — nothing can be parked'
                        : 'nothing here is set to keep a withheld original — no gate, no granted signed action — so nothing can ever be parked')
                    : one
                    ? (rActNames.length
                        ? `its signed ${_plural(rActNames.length, 'action')} in review posture (${rActNames.join(', ')}) park writes here for a person to decide`
                          + (profiles.some(_canParkByGate) ? ', and at least one gate keeps withheld originals for review' : '')
                        : 'at least one gate keeps withheld originals for review, so this agent can park work here')
                    : `${rParkers} of ${_plural(N, 'agent')} can park work here — a gate that keeps withheld originals for review`
                      + (rActParkers.length ? ` or a signed action in review posture (${rActParkers.length})` : ''),
            },
            {
                // A zero here is only a MEASUREMENT when something could have been
                // parked. With no gate in review mode, `0 open` is a tautology, not a
                // reading — and pairing it with a `na` configuration produced a green
                // "nothing is waiting" for a station that can never receive anything
                // (the hazard the comment above names, which the code did not prevent).
                // Unacknowledged INCIDENTS are a separate source that does not need a
                // review-mode gate, so a non-zero count is still real signal here.
                state: rOpen ? 'warn' : (rParkers ? 'pass' : 'na'),
                // The figure and the estate attribution are KEPT even when inert — a
                // reader still needs to know where the work lives (Rule 3.1, and the
                // per-agent-vs-estate split #11 fixed). Only the STATE changes, plus a
                // qualifier saying the zero is structural rather than measured.
                text: `${rOpen} open`,
                detail: (one
                    ? `${rOpen} awaiting disposition for this agent`
                      // Estate-level items belong to no agent; naming them stops a focused
                      // zero from reading as "the inbox is empty".
                      + (_inboxUnattributed
                          ? ` · ${_plural(_inboxUnattributed, 'further item')} are estate-level and attributed to no agent`
                          : '')
                      + ((_inboxBadgeCount || 0) > rOpen
                          ? ` · ${_inboxBadgeCount} open estate-wide` : '')
                    : `${rOpen} awaiting disposition, estate-wide`)
                    + ((!rParkers && !rOpen) ? ' — structural, not measured: nothing here can park work' : ''),
            },
            {
                headline: rOpen
                    ? `${_plural(rOpen, 'item')} are waiting on your decision${one ? ' for this agent' : ''}.`
                    // "Nothing is waiting" is true but hollow when nothing CAN wait:
                    // it reports the absence of work as if it were the absence of risk.
                    // Say what is actually the case — no gate can hand work to a human.
                    : !rParkers
                    ? (one
                        ? 'No gate keeps withheld originals for review, so this agent can never hand a withheld answer to you.'
                        : 'No agent has a gate that keeps withheld originals for review, so nothing can ever be parked for a human decision.')
                    : rDanger
                    ? 'Nothing is blocked on you, but the trace carries an unresolved observation.'
                    : 'Nothing is waiting on your decision.',
                why: 'counts parked reviews and unacknowledged incidents; derived observations carry no disposition and are not counted',
                evidence: [
                    // Rule 5.4 (PROP-002 follow-up): at estate scope, name WHERE the
                    // open work lives — a count with no owner is a number, not a route.
                    (!one ? (() => {
                        const held = Object.entries(_inboxByTag || {})
                            .filter(([, b]) => b && b.open)
                            .sort((a, b) => b[1].open - a[1].open);
                        return held.length
                            ? `open on ${_briefList(held.map(([t, b]) => `@${t} (${b.open})`))}`
                              + (_inboxUnattributed ? ` · ${_plural(_inboxUnattributed, 'item')} estate-level` : '')
                            : '';
                    })() : ''),
                    rDerived
                        ? `${_plural(rDerived, 'derived observation')} not counted here` +
                          (rTop ? ` — ${rTop}` : '')
                        : '',
                ].filter(Boolean).join(' · '),
                // The owed dot: an item parked in the queue IS an act waiting on a
                // human — the literal case the mark was designed for.
                owed: !!rOpen,
                cta: { label: 'Open the Assurance Inbox', action: 'inbox' },
                derivation: _deriv({
                    window: one ? 'this agent\'s queue' : 'the estate queue',
                    config: 'Configuration asks whether any gate is in REVIEW mode — the one mode that withholds like enforce AND parks the original for a human decision. Without it nothing can ever reach the queue.',
                    exec: 'Execution counts items actually awaiting you: pending reviews plus unacknowledged stored incidents, attributed to agents in the single pass that also produces the estate badge, so the two can never disagree.',
                    note: 'No quality dimension feeds this station. It reads the review queue and the Assurance Inbox, not scored turns.',
                }),
            });

        // 8 · Proven — receipts, benchmarks, audit pack.
        const evalOn = !!dash.enabled;
        const pScored = one ? spans : (dash.total_spans || 0);
        // The checks ABOUT this guarantee: the two that grade whether the
        // RECORD itself is sound (the sealed chain present, the turn terminated
        // cleanly). Counted here and at no other station.
        const pKeys = STATION_DIMS.proven;
        const pEstate = one ? null : _estateExecRollup(dash, pKeys);
        // PROVEN is the one guarantee station that keeps a real execution READING after
        // the lane cut, and the reason is worth stating: its mechanism's success
        // condition IS its act. A gate that withheld an answer was working, so acting
        // cannot be read as passing (`_execAtJudged`) — but a seal is either produced or
        // it is not, so "N sealed" is operating effectiveness, not merely activity.
        //
        // Its two record-soundness CHECKS (`provenance_integrity`, `clean_termination`)
        // moved to JUDGED with every other measurement, so they no longer fold into this
        // state; they are named in the detail with a route, never silently dropped.
        const pState = !pScored ? 'na'
            : one ? 'pass'
            : ((pEstate && pEstate.total) ? pEstate.state : 'pass');
        const proven = _assemble('proven',
            {
                state: evalOn ? 'pass' : 'na',
                text: evalOn ? 'on' : 'off',
                detail: evalOn ? 'sealing on, estate-wide — every scored turn carries a signed verdict'
                    : 'the online evaluation hook is disabled, so nothing is sealed',
            },
            {
                state: pState,
                // TC-53: `N sealed`, never a bare count — at pScored===1 the value read `1`,
                // exactly the dressed-constant shape Rule 1.3 bans (undecidable from the
                // number alone whether it is a measurement or the scope size).
                text: pScored ? `${pScored} sealed` : '',
                // W3 (trust trip): a trip-scoped count names its boundary; an empty
                // leg says so rather than reading as "nothing sealed, ever".
                detail: [(pScored
                    ? `${_plural(pScored, 'sealed verdict')}${tripMeta ? ` this leg (since ${legSince})` : ' in this window'}`
                    : (tripMeta ? `nothing sealed since the trip reset on ${legSince} — the leg is empty` : ''))
                    + (!one && pEstate && pEstate.short.length
                        ? ` — ${_plural(pEstate.short.length, 'agent')} below the healthy floor on record-soundness checks` : ''),
                    // The moved checks, named where they went (never a silent drop).
                    one && pKeys.length
                        ? `${_plural(pKeys.length, 'check')} measure this guarantee — counted at JUDGED`
                        : ''].filter(Boolean).join(' · '),
            },
            {
                headline: evalOn
                    ? 'Each scored turn is sealed with a signed verdict that can be verified without this server.'
                    : 'Live scoring is off, so turns produce no verifiable receipt.',
                why: evalOn ? 'sealing is on and turns are being sealed'
                    : 'the online evaluation hook is disabled',
                evidence: pScored ? `${_plural(pScored, 'verdict')} exportable as an offline-verifiable receipt` : '',
                cta: { label: 'Evidence tools — backtest · red-team · audit pack', action: 'posture-tools' },
                // PROP-009 — the sealed answers and the signed parts each one used.
                ...(window.EvidenceView ? { execCta: { label: 'Open Evidence — what each answer was made with', action: 'evidence' } } : {}),
                derivation: _deriv({
                    window: pScored
                        ? `${_plural(pScored, 'sealed verdict')}${tripMeta ? ` since the trip reset on ${legSince}` : ' in this window'}`
                        : (tripMeta ? `nothing sealed since the trip reset on ${legSince}` : 'nothing sealed in this window'),
                    config: 'Configuration is whether provenance sealing is producing records at all.',
                    exec: 'Execution counts SEALED VERDICTS — Ed25519-signed attestations bound to each turn\'s trajectory. It is a count of receipts, not a pass rate, which is why it carries its unit rather than a bare number. The station\'s colour additionally folds in the two record-soundness checks below.',
                    graded: _gradedAt(oncell, pKeys),
                    note: 'Two dimensions feed this station — whether the signed chain is present and whether the turn terminated cleanly. They grade the RECORD, not the answer, and under the partition they are counted here and at no other station. A receipt itself records what happened; it does not grade it.',
                }),
            });

        return [signed, grounded, guarded, bounded, scored, trusted, reviewed, proven];
    }

    function _buildCircleModel() {
        let agent = _circleAgent
            ? _model.agents.find((a) => (a.key || a.tag) === _circleAgent && a.kind !== 'flow') : null;
        if (_circleAgent && !agent) { _circleAgent = null; agent = null; }   // agent left the scope
        const stations = _circleStationsFor(agent);
        // Loop-closure receipts (from the cached story payload — fetched on focus).
        const st = agent && _storyCache[agent.tag];
        const closures = st && st.closures && st.closures.length
            ? { count: st.closures.length, last: st.closures[0] && st.closures[0].summary } : null;

        // The agent picker (own, non-flow agents — config-backed stations need
        // them). Same shape + IFOC sort as the Anatomy star picker (shared UI).
        const IFOC_ORDER = { llm_only: 0, rag_focused: 1, tool_enabled: 2, genie: 3 };
        const normType = (t) => (t === 'conversation_with_tools' ? 'llm_only' : t);
        const items = _model.agents
            .filter((a) => a.kind !== 'flow' && a.isSelf !== false)
            .map((a) => ({ id: a.key || a.tag, tag: a.tag, name: a.name || '',
                           type: normType(a.type), color: ifoc(a.type).color }))
            .sort((x, y) => ((IFOC_ORDER[x.type] ?? 9) - (IFOC_ORDER[y.type] ?? 9))
                || x.tag.localeCompare(y.tag));

        const center = agent
            ? { label: '@' + agent.tag, sub: (ifoc(agent.type).label || '') + ' agent', kind: 'agent',
                color: ifoc(agent.type).color }
            : { label: 'Agent estate', sub: `${items.length} agents`, kind: 'estate' };
        return {
            center,
            stations,
            // W2 — the glance layer: one sentence from the stations' own values.
            verdict: _verdictLine(stations, center),
            closures,
            story: agent ? { onPlay: () => _playAgentStory(agent) } : null,
            anatomy: agent && agent.id && agent.isSelf !== false
                ? { onOpen: () => { if (window.AgentAnatomy && window.AgentAnatomy.open) window.AgentAnatomy.open(agent.id); } }
                : null,
            // Cross-panel pills — the agent travels with every jump (context-aware).
            awareness: agent && agent.isSelf !== false
                ? { onOpen: async () => {
                      try { const { handleViewSwitch } = await import('../ui.js?v=1.287'); handleViewSwitch('executions-view'); } catch (_) { /* noop */ }
                      for (let i = 0; i < 10; i++) {
                          if (window.executionDashboard && window.executionDashboard.focusAgent
                              && document.getElementById('session-search')) {
                              window.executionDashboard.focusAgent(agent.tag);
                              break;
                          }
                          await new Promise((r) => setTimeout(r, 150));
                      }
                  } }
                : null,
            ontology: agent && agent.isSelf !== false && document.getElementById('ontology-view')
                ? { onOpen: async () => {
                      try { const { handleViewSwitch } = await import('../ui.js?v=1.287'); handleViewSwitch('ontology-view'); } catch (_) { /* noop */ }
                      // The ontology handler is lazy-imported by the switch — retry
                      // until its deep-link export exists, then focus the agent.
                      for (let i = 0; i < 25; i++) {
                          if (window.ontologyFocusAgent) { window.ontologyFocusAgent(agent.tag); break; }
                          await new Promise((r) => setTimeout(r, 200));
                      }
                  } }
                : null,
            pickable: {
                items, current: _circleAgent || '', allLabel: 'Whole estate',
                onPick: (key) => { _circleAgent = key || null; _mountTrustCircle(); },
            },
            // The trust-trip lens (docs/roadmap/TRUST_TRIP_RESET.md): the renderer
            // draws the Whole record · This leg toggle and the leg chip from this
            // descriptor — it fetches nothing. The toggle renders only where a leg
            // exists to show (a lens with nothing to select would be a dead control);
            // the reset act follows the same belt as every trust act: focused, own,
            // never a `::`-keyed (another user's) agent.
            lens: (() => {
                const tm = agent && agent.online && agent.online.trip;
                const anyLeg = Object.values((_selfDash(_model.dashboard || {})).by_agent || {})
                    .some((c) => c && c.trip);
                if (!tm && !anyLeg) return null;
                return {
                    mode: _circleLens,
                    since: tm ? tm.since : null,
                    leg_n: tm ? (tm.leg_n || 1) : null,
                    note: tm ? tm.note : null,
                    // Focused on an UNMARKED agent with the trip lens active, the ring
                    // honestly shows the whole record — the chip must say so (W3).
                    noLeg: !!(agent && !tm),
                    onSwitch: (m) => {
                        _circleLens = (m === 'trip') ? 'trip' : 'total';
                        try { localStorage.setItem('tda_trust_lens', _circleLens); } catch (_) { /* noop */ }
                        if (window.TrustCircle) window.TrustCircle.update(_buildCircleModel());
                    },
                };
            })(),
            trip: (agent && agent.isSelf !== false
                   && !String(agent.key || agent.tag).includes('::'))
                ? { canReset: true, onReset: (note) => _startNewLeg(agent, note) }
                : null,
        };
    }

    /** Start a new leg (the trip reset) — an audited act, never a deletion (W1/W2):
     *  the endpoint appends a marker; every span, verdict and incident survives.
     *  On success the lens switches to the fresh leg — the point of the act — whose
     *  honest first reading is an EMPTY leg, and the outcome is announced through
     *  the central messaging system (never a native dialog). */
    async function _startNewLeg(agent, note) {
        try {
            await api(`/assurance/trust-trip/${encodeURIComponent(agent.tag)}/reset`,
                { method: 'POST', body: JSON.stringify({ note: (note || '').trim() }) });
            _circleLens = 'trip';
            try { localStorage.setItem('tda_trust_lens', 'trip'); } catch (_) { /* noop */ }
            delete _storyCache[agent.tag];   // the story gains a leg chapter
            await load();
            if (window.showAppBanner) {
                window.showAppBanner(
                    `A new leg has started for @${agent.tag} — the ring now reads this leg `
                    + '(empty until its first scored turn). The whole record is untouched; '
                    + 'switch back to “Whole record” anytime.', 'success', 9000);
            }
            return true;
        } catch (e) {
            if (window.showAppBanner) {
                window.showAppBanner(`The trip reset for @${agent.tag} could not be recorded.`,
                    'error', 6000);
            }
            return false;
        }
    }

    // ── "The life of this agent" — the personalized story, from real records ───
    const _storyCache = {};   // tag → /assurance/agent-story payload

    async function _fetchAgentStory(agent) {
        if (!agent) return null;
        // TC-76: the endpoint is OWNER-scoped and looked up by bare tag. Focusing ANOTHER
        // user's same-tag agent in admin system scope would fetch — and narrate — the
        // CALLER's own agent's story as if it were theirs (wrong subject). Same belt as
        // `_runTrustAct`: a `::`-keyed or non-self agent has no story surface here.
        if (String(agent.key || '').includes('::') || agent.isSelf === false) return null;
        if (_storyCache[agent.tag]) return _storyCache[agent.tag];
        try {
            const d = await api(`/assurance/agent-story/${encodeURIComponent(agent.tag)}`);
            _storyCache[agent.tag] = d;
            return d;
        } catch (e) { return null; }
    }

    function _fmtDate(iso) {
        if (!iso) return null;
        try { return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' }); }
        catch (_) { return null; }
    }

    function _storyChapters(agent, s) {
        // Every sentence is a claim backed by a record the endpoint read —
        // "not yet" phrasing keeps absent chapters honest instead of skipped.
        const cls = ifoc(agent.type).label || 'agent';
        const born = s.born || {}, kn = s.knowledge || {}, sg = s.signed || {};
        const act = s.acting || {}, rv = s.reviewed || {}, closures = s.closures || [];
        const o = agent.online || {};
        const tb = _tbAgent(agent.key, agent.tag);   // TC-35: key-shape aware
        const tState = tb && tb.composite && tb.composite.state;
        const ch = [];
        ch.push({ station: 'signed', eyebrow: 'Chapter 1 · Born',
            title: `@${agent.tag} — a ${cls} agent${_fmtDate(born.when) ? `, born ${_fmtDate(born.when)}` : ''}.`,
            text: `Its identity is its profile: the class, the model, the senses it was given. Everything that follows is recorded against it.` });
        ch.push({ station: 'grounded', eyebrow: 'Chapter 2 · Learning',
            title: kn.collections
                ? `Its knowledge was screened — ${kn.screened} of ${_plural(kn.documents, 'document')} across ${kn.collections} repositor${kn.collections === 1 ? 'y' : 'ies'}.`
                : 'No knowledge repositories bound yet.',
            text: kn.collections
                ? `${_plural(kn.injection_findings, 'injection finding')}; ${kn.acknowledged} acknowledged with a recorded reason, ${_plural(kn.quarantined, 'document')} quarantined out of retrieval.`
                : 'When repositories are bound, every document is screened as it enters — and the corpus can only be signed once fully screened.' });
        ch.push({ station: 'signed', eyebrow: 'Chapter 3 · Attested',
            title: sg.state === 'signed'
                ? `Signed${_fmtDate(sg.when) ? ` on ${_fmtDate(sg.when)}` : ''} — bottom-up, content-bound.`
                : 'Not yet signed — the badge stays honestly blank.',
            text: sg.state === 'signed'
                ? 'A signed agent contains only signed ingredients; change anything and the badge flips to changed until re-signed.'
                : 'Signing is earned bottom-up: the ontology and the corpus must pass their checks before the agent itself can be attested.' });
        ch.push({ station: 'scored', eyebrow: 'Chapter 4 · Acting, scored',
            title: act.turns
                ? `It has acted ${_plural(act.turns, 'scored turn')}${o.pass_rate != null ? ` — ${Math.round(o.pass_rate * 100)}% clean` : ''}.`
                : 'No scored turns yet.',
            text: act.turns
                ? `${_plural(act.failures, 'turn')} carried a gating failure. Every turn is graded in code first — 52 dimensions — with judges only for what code cannot measure.`
                : 'When it acts, every turn is scored automatically: deterministic dimensions first, calibrated judges where a rule cannot reach.' });
        ch.push({ station: 'trusted', eyebrow: 'Chapter 5 · The badge breathes',
            title: tState ? `Behavioral trust: ${tState}.` : 'Behavioral trust is not measuring yet.',
            text: 'Conduct moves the badge, integrity caps it — trust here is a track record, never a granted label.' });
        // The trip (W2): a reset is an audited act, so the story narrates it —
        // an interlude, because it changes the LENS, never the record.
        if (s.trip && s.trip.since) {
            ch.push({ station: 'trusted', eyebrow: 'Interlude · A new leg',
                title: `A new leg began ${_fmtDate(s.trip.since) || 'recently'}${(s.trip.legs || 1) > 1 ? ` — leg ${s.trip.legs}` : ''}.`,
                text: `${s.trip.note ? `Reason: ${s.trip.note}. ` : ''}The odometer never resets — every span and verdict survives; the leg is a lens the operator chose`
                    + `${s.trip.integrity_state ? `, begun while the agent read ${s.trip.integrity_state}` : ''}.` });
        }
        ch.push({ station: 'reviewed', eyebrow: 'Chapter 6 · A human decides',
            title: (rv.pending || rv.decided)
                ? `${_plural(rv.decided, 'review')} decided${rv.released ? ` — ${rv.released} released into the conversation` : ''}${rv.pending ? `; ${rv.pending} awaiting you` : ''}.`
                : 'Nothing has needed your disposition yet.',
            text: 'Withheld answers park for the owner: approve, release, or uphold — history is never silently rewritten.' });
        ch.push({ station: 'proven', eyebrow: 'Chapter 7 · Proven',
            title: act.turns ? 'Every scored turn carries a signed, offline-verifiable receipt.'
                             : 'Receipts begin with its first scored turn.',
            text: 'Ed25519-signed verdicts bound to the exact trajectory they grade — exportable from any answer, bundled into the audit pack.' });
        ch.push({ station: 'signed', eyebrow: 'Chapter 8 · The loop closes',
            title: closures.length
                ? `The loop has closed ${_plural(closures.length, 'time')} — trust that responds.`
                : 'The loop stands ready — no closures needed yet.',
            text: closures.length
                ? 'Each closure is an incident that led to a recorded human disposition:'
                : 'When an incident occurs, it becomes a worked item — and its disposition is recorded here, closing the circle.',
            list: closures.slice(0, 5).map((c) => c.summary + (c.when ? ` (${_fmtDate(c.when) || c.when})` : '')) });
        return ch;
    }

    async function _playAgentStory(agent) {
        const s = await _fetchAgentStory(agent);
        if (!s || !window.TrustCircle) {
            // TC-94: silence made the button read as broken. Say what happened via the
            // central messaging system (never a native dialog).
            if (typeof window.showAppBanner === 'function') {
                window.showAppBanner('The agent story could not be loaded right now — try again in a moment.', 'warning', 5000);
            }
            return;
        }
        // Refresh the closure count on the arc, then narrate.
        window.TrustCircle.update(_buildCircleModel());
        window.TrustCircle.playStory(_storyChapters(agent, s));
    }

    /** The guided sign's outcome, worded from the state it actually landed on.
     *  `signAgentGuided` returns `{ok, state}`; reporting an unconditional
     *  "content-bound and offline-verifiable" told an operator who landed on
     *  `deployment_unattested` that the job was finished when half of it was. */
    const _SIGN_OUTCOME = {
        trusted: 'Signed — content-bound and offline-verifiable.',
        deployment_unattested: 'Signed — the portable half is now vouched for. This deployment '
            + 'has not attested its own half (model, tools, connectors) — '
            + 'use “Attest this deployment” above to finish.',
        changed: 'Signed, but the composition already reads changed — verify this agent.',
    };

    /** #22 — the caveats a signature came with, in one honest sentence.
     *
     *  Every validator returns a 3-band verdict; the guided flow used to discard them, so
     *  "Signed — content-bound and offline-verifiable" was reported for a signature whose
     *  own record said "content not fully scanned for injection" and "every answer gate is
     *  in observe mode". The signer was told a check had passed when they had in fact
     *  ASSERTED the unchecked remainder.
     *
     *  Not a state and not a colour (Rule 8.4) — a qualifier on a signature that really
     *  did happen.
     */
    function _signCaveatNote(caveats) {
        const list = (caveats || []).filter((c) => c && c.band && c.band !== 'clean');
        if (!list.length) return '';
        const parts = list.slice(0, 3).map((c) => {
            const who = c.kind === 'agent' ? 'this agent' : `${c.kind} “${c.label}”`;
            return c.note ? `${who}: ${c.note}` : `${who}: ${c.band}`;
        });
        const more = list.length > 3 ? ` (+${list.length - 3} more)` : '';
        return ` Signed WITH a caveat — ${parts.join(' · ')}${more}.`;
    }

    /** After a successful trust act: `load()` re-mounts the ring (dropping the pin and
     *  destroying the panel the act was reporting into), so restore the station the act
     *  happened on and write its outcome into the FRESH panel's status line. */
    function _repinAndSay(stationKey, msg) {
        try {
            if (window.TrustCircle && window.TrustCircle.pin) window.TrustCircle.pin(stationKey);
            const el = document.getElementById('tc-act-status');
            if (el) el.textContent = msg || '';
        } catch (_) { /* reporting is best-effort; the act itself already landed */ }
    }

    /** Carry out a trust act for the focused agent, reporting into the pinned panel. */
    async function _runTrustAct(action, ctx, stationKey) {
        const say = ctx.setStatus || (() => {});
        // Belt and braces: the button is not rendered for another user's agent, but the
        // action channel is public, so refuse here too rather than resolve a same-tag
        // profile of the caller's own.
        if (String(_circleAgent || '').includes('::')) {
            say('This agent belongs to another user — trust acts on it are theirs to perform.');
            return;
        }
        const rec = (_trustAgentsFull || []).find((t) => t.tag === _circleAgentTag());
        if (!rec || !rec.profile_id) { say('No agent is focused.'); return; }
        const TB = window.TrustBadge;
        if (!TB) { say('The trust package is not available in this deployment.'); return; }
        try {
            if (action === 'attest-deployment') {
                if (!TB.attestDeployment) { say('This deployment cannot be attested from here.'); return; }
                say('Attesting this deployment…');
                const a = await TB.attestDeployment(rec.profile_id);
                if (!a || !a.ok) { say((a && a.error) || 'Could not attest this deployment.'); return; }
                say('Attested — refreshing the reading…');
                await load();                  // re-resolve trust (re-mounts the ring)
                _repinAndSay(stationKey || 'signed',
                    'Attested — this instance has recorded its model, tools and connectors.');
                return;
            }
            if (action === 'verify-agent') {
                say('Verifying…');
                const v = await TB.verifyAgent(rec.profile_id);
                say(v && v.verified
                    ? 'Verified — the signature matches what runs, checked without this server.'
                    : (v && v.error) || 'Could not verify this agent.');
                return;
            }
            // #28 — signing is TWO steps on a deployment that supports local
            // attestation, and a successful step 1 turns the station AMBER
            // (deployment_unattested). Announce the shape of the journey up front, so
            // the amber that follows reads as progress, not regression.
            // #29 — the FIRST signature flips `signingInUse` for the whole deployment:
            // every other unsigned agent re-grades from "not turned on" (dashed) to
            // "outstanding work" (amber). Deliberate (Rule 8.2) — the defect was the
            // silence. Both facts are captured BEFORE the act, from the state the act
            // is about to change.
            const _firstSign = !(_trustAgentsFull || []).some((t) =>
                t.state === 'trusted' || t.state === 'changed' || t.state === 'deployment_unattested');
            const _othersUnsigned = (_trustAgentsFull || []).filter((t) =>
                t.state === 'unsigned' && t.tag !== rec.tag).length;
            say(TB.attestDeployment && rec.state === 'unsigned'
                ? 'Signing — step 1 of 2 (this deployment then attests its own half)…'
                : 'Signing…');
            // Bottom-up: every blocking ingredient first, then the agent. A 422 means an
            // ingredient is not SIGNABLE (an unscreened corpus, an unresolved finding) —
            // reported verbatim, because that is a different problem from "not signed yet".
            const r = await TB.signAgentGuided(rec.profile_id, (m) => say(m));
            if (!r || !r.ok) { say((r && r.error) || 'Sign-off failed.'); return; }
            say('Signed — refreshing the reading…');
            await load();                      // re-resolve trust (re-mounts the ring)
            // Outcome worded from the state the sign actually landed on — never an
            // unconditional success line (a partial landing is not a finished job).
            // #22 — a `partial` band is what the signer just VOUCHED FOR, so it belongs
            // in the outcome line, not only in a record nobody reads. Reported after the
            // landing state: what happened first, then what it came with.
            const _base = _SIGN_OUTCOME[r.state]
                || `Signed — this agent now reads “${r.state || 'trusted'}”.`;
            const _estateNote = (_firstSign && _othersUnsigned)
                ? ` Signing is now in use for this deployment — the other ${_plural(_othersUnsigned, 'unsigned agent')} now read as outstanding work rather than not turned on.`
                : '';
            _repinAndSay(stationKey || 'signed', _base + _signCaveatNote(r.caveats) + _estateNote);
        } catch (e) {
            say('Sign-off failed unexpectedly.');
        }
    }

    /** Open a blocking ingredient's own surface — the one place its findings can be
     *  resolved (a knowledge/planner repository's inspection view carries Corpus Health:
     *  quarantine, acknowledge-with-reason, re-scan). Routes to the existing surface via
     *  the same dynamic import the Ontology panel uses; never reimplements it here. */
    async function _openIngredient(kind, id, ctx) {
        const say = ctx.setStatus || (() => {});
        const rec = (_trustAgentsFull || []).find((t) => t.tag === _circleAgentTag());
        const b = ((rec && rec.blocking) || []).find(
            (x) => x && x.kind === kind && String(x.artifact_id) === String(id));
        const name = (b && b.label) || `${kind} #${id}`;
        // #31 — a skill's or ontology's own trust surface is the TrustBadge popover
        // (state · verify · sign-off), which is exactly where a blocking one is
        // resolved. The repositories keep their richer route below (Corpus Health).
        if (kind === 'skill' || kind === 'ontology') {
            const TB = (typeof window !== 'undefined') && window.TrustBadge;
            if (TB && TB.openArtifactTrust) {
                say(`Opening the trust record for ${name} — sign it there, then sign this agent.`);
                TB.openArtifactTrust(kind, id, (b && b.label) || '');
            } else {
                say(`Could not open ${name}. Find it under Configuration → ${kind === 'skill' ? 'Skills' : 'Intelligence'}.`);
            }
            return;
        }
        try {
            const m = await import('../ui.js?v=1.287');
            if (!m.openCollectionInspection) throw new Error('no inspector');
            // The 4th argument is load-bearing, not decoration: the inspect view renders
            // Corpus Health only `if (repositoryType === 'knowledge' && repositoryMeta)`.
            // Omitting it suppressed the ONE panel where a blocking finding can be
            // resolved, so the route landed one step short of its own destination —
            // on a 235-row chunk browser instead. Pass the collection row, exactly as
            // the canonical call sites do.
            let meta = null;
            try {
                const cols = await api('/rag/collections');
                const list = Array.isArray(cols) ? cols : (cols.collections || []);
                meta = list.find((c) => String(c.id) === String(id)) || null;
            } catch (_) { /* still open the view; Corpus Health simply won't render */ }
            say(`Opening ${name} — resolve its findings under ${kind === 'planner' ? 'Planner Health' : 'Corpus Health'}, then sign again.`);
            m.openCollectionInspection(id, (meta && meta.name) || name, kind, meta);
        } catch (e) {
            say(`Could not open ${name}. Find it under Intelligence → repositories.`);
        }
    }

    /** The focused agent's plain tag (roster keys may be "tag::uuid" in system scope). */
    function _circleAgentTag() {
        if (!_circleAgent) return null;
        const a = _model && _model.agents && _model.agents.find((x) => (x.key || x.tag) === _circleAgent);
        return a ? a.tag : String(_circleAgent).split('::')[0];
    }

    function _onCircleNavigate(action, key, ctx) {
        if (action === 'inbox') { showSection('inbox'); return; }
        if (action === 'evidence' || action.indexOf('evidence:') === 0) {
            showSection('evidence', { filter: action.split(':')[1] || 'all' });
            return;
        }
        // PROP-002 follow-up — the estate owed dot's context-sensitive support: a
        // focus route lands the ring ON the agent owing the act, with the SAME
        // station pinned, so the state-derived act (sign · attest · re-sign,
        // Rule 8.5) is immediately offered. Programmatic pin() is the sanctioned
        // act-then-refresh restore — mount deliberately drops pins on a subject
        // switch, and this IS a subject switch the reader asked for.
        if (action.startsWith('focus-agent:')) {
            const tag = action.slice('focus-agent:'.length);
            const a = _model.agents.find((x) => x.tag === tag && x.kind !== 'flow'
                && x.isSelf !== false && !String(x.key || x.tag).includes('::'));
            if (!a) return;
            _circleAgent = a.key || a.tag;
            _mountTrustCircle();
            if (window.TrustCircle && window.TrustCircle.pin) window.TrustCircle.pin(key);
            return;
        }
        // #24 — the act owed when a master switch is off is flipping it, and it lives
        // on the admin Agent Evaluation & Grounding card. Offered only to admins
        // (Rule 8.5 — the CTA must be able to move the state). The pane is resolved
        // FROM the card's own control so the route survives a tab rename.
        if (action === 'admin-eval') {
            const nav = document.getElementById('view-switch-admin');
            if (nav) nav.click();
            // The controls live Admin → App Config → Trust (the station-partitioned
            // panel); each hop is a click on the surface's own control so the route
            // inherits any gating those controls enforce. Staggered because each
            // layer renders on the previous click; every step is optional-chained so
            // a missing layer degrades to "as far as we got", never an error.
            setTimeout(() => {
                const tab = document.querySelector('.admin-tab[data-tab="app-config-tab"]');
                if (tab) tab.click();
                setTimeout(() => {
                    const sub = document.querySelector('[data-config-tab="trust"]');
                    if (sub) sub.click();
                    setTimeout(() => {
                        const el = document.getElementById('agent-eval-online-enabled');
                        const card = el && (el.closest('.rounded-xl, section') || el);
                        if (!card) return;
                        card.scrollIntoView({ behavior: 'smooth', block: 'center' });
                        card.classList.remove('tc-anchor-flash');
                        void card.getBoundingClientRect();
                        card.classList.add('tc-anchor-flash');
                        setTimeout(() => card.classList.remove('tc-anchor-flash'), 2400);
                    }, 120);
                }, 120);
            }, 150);
            return;
        }
        // Acts carried out IN PLACE, so the reading you acted on stays on screen. `ctx`
        // comes from the pinned panel: `setStatus` writes its aria-live line, `done`
        // re-renders it once the trust state has moved.
        if (action === 'sign-agent' || action === 'verify-agent' || action === 'attest-deployment') {
            _runTrustAct(action, ctx || {}, key);
            return;
        }
        // A blocker's own surface — where its findings can actually be resolved.
        // The ring's derivation explainer routes a dimension click here rather than
        // explaining it itself: the Assurance Guide already IS that explanation, and a
        // second glossary would be the drift this codebase keeps paying for. The guide is
        // opened SCOPED to the station's own contributing dimensions (with their live
        // bands), so it answers "what does this measure, and how is this agent doing on
        // it" in one surface — with a one-click widen to all 52.
        // W3 — the graded row opens the station where the measurements live.
        if (action.indexOf('pin:') === 0) {
            const stKey = action.slice('pin:'.length);
            if (window.TrustCircle && typeof window.TrustCircle.pin === 'function') window.TrustCircle.pin(stKey);
            return;
        }
        if (action.indexOf('explain-dim:') === 0) {
            const dimKey = action.slice('explain-dim:'.length);
            const QD = window.QualityDimensions;
            if (!QD || typeof QD.openGuide !== 'function') return;
            // Re-resolve the station from the SAME model the panel rendered from, so the
            // guide's scope is exactly the set the explainer listed (never a recomputed
            // approximation that could disagree with what the user just read).
            const focus = _circleAgent && _model
                && _model.agents.find((x) => (x.key || x.tag) === _circleAgent);
            const st = (_circleStationsFor(focus || null) || []).find((x) => x.key === key);
            const dims = ((st && st.derivation && st.derivation.dims) || [])
                .filter((d) => d && d.key);
            // The guide's context is a MAP keyed by dimension (qualityDimensions.js:464),
            // carrying each one's live band — so it opens showing exactly what this station
            // watches AND how the agent is doing on it, with a one-click widen to all 52.
            const rows = {};
            dims.forEach((d) => {
                rows[d.key] = {
                    band: d.applicable ? d.band : 'na',
                    score: d.score,
                    explanation: d.applicable ? '' : 'not produced by this agent\'s class',
                };
            });
            const label = `${String(key || '').toUpperCase()} · what this station watches`;
            QD.openGuide(dimKey, Object.keys(rows).length ? { rows, label } : null);
            return;
        }
        if (action.indexOf('open-ingredient:') === 0) {
            const [, kind, id] = action.split(':');
            _openIngredient(kind, id, ctx || {});
            return;
        }
        if (action === 'roster') {
            showSection('roster');
            if (_circleAgent) {
                const a = _model.agents.find((x) => (x.key || x.tag) === _circleAgent);
                if (a && _expandedTag !== (a.key || a.tag)) toggleAgentDetail(a.key || a.tag);
            }
            return;
        }
        if (action === 'posture-matrix' || action === 'posture-tools') {
            showSection('posture');   // lands at the section top — header stays in view
            const el = $(action === 'posture-matrix' ? 'trust-posture-matrix' : 'trust-posture-tools');
            if (!el) return;
            requestAnimationFrame(() => {
                const sc = el.closest('.overflow-y-auto');
                if (sc) {
                    const er = el.getBoundingClientRect(), cr = sc.getBoundingClientRect();
                    if (er.bottom > cr.bottom || er.top < cr.top) {
                        el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                    }
                }
                el.classList.remove('tc-anchor-flash');
                void el.getBoundingClientRect();
                el.classList.add('tc-anchor-flash');
                setTimeout(() => el.classList.remove('tc-anchor-flash'), 2400);
            });
        }
    }

    /** Re-fetch the trust payload and repaint a mounted ring IN PLACE (update(), not
     *  mount() — the pin and any open reading survive). The live-event path: a trust
     *  act performed on another surface (quarantine, acknowledge, review decision)
     *  broadcasts, and the ring must re-read its facts — pulsing alone would animate
     *  over stale readings. Failure keeps the last known payload; never throws. */
    let _circleTrustT = 0;
    async function _refreshCircleTrust() {
        try {
            const fresh = await api('/trust/agents').then((d) => d.agents || null).catch(() => null);
            if (fresh) _trustAgentsFull = fresh;
            // TC-92: profiles too — a trust event can follow a gate-mode change (e.g. a
            // posture edit acknowledged elsewhere), and GROUNDED/GUARDED read _profilesRaw.
            // Cheap endpoint; failure keeps the last reading. `_model.dashboard` (exec
            // rates) is deliberately NOT refetched here — execution aggregates move with
            // turns, not trust acts, and the full load() covers them.
            const profs = await api('/profiles')
                .then((d) => ((d && d.profiles) || []).filter((p) => p && p.tag))
                .catch(() => null);
            if (Array.isArray(profs) && profs.length) _profilesRaw = profs;
            // REVIEWED reads `_inboxByTag`, which only `renderAssuranceInbox()` computes —
            // and that runs on a SEPARATE debounced listener. Repainting without it left
            // the ring asserting the pre-decision count (a disposed review still showing
            // "1 open"). Recompute it here so every input to the model is fresh before
            // the repaint, rather than racing another listener for it.
            if ($('assurance-inbox-wrap')) await refreshInbox();   // the ONE seam
            // A trust event can BE a loop closure (a review disposition) — refetch the
            // focused agent's story so the arc's closure count moves with the decision.
            const a = _circleAgent && _model
                && _model.agents.find((x) => (x.key || x.tag) === _circleAgent);
            if (a) {
                delete _storyCache[a.tag];
                await _fetchAgentStory(a);
            }
            const host = $('trust-circle-host');
            if (host && host.querySelector('.tc-svg') && window.TrustCircle && _model) {
                window.TrustCircle.update(_buildCircleModel());
            }
        } catch (_) { /* stale-while-revalidate: keep the last reading */ }
    }

    function _mountTrustCircle() {
        const host = $('trust-circle-host');
        if (!host || !window.TrustCircle) return;
        try {
            window.TrustCircle.mount({ host, model: _buildCircleModel(), onNavigate: _onCircleNavigate });
            // Closure receipts arrive lazily: fetch the story on focus, then
            // refresh the arc label + closure line in place.
            if (_circleAgent) {
                const a = _model.agents.find((x) => (x.key || x.tag) === _circleAgent);
                if (a && !_storyCache[a.tag]) {
                    _fetchAgentStory(a).then((s) => {
                        if (s && _circleAgent === (a.key || a.tag)) window.TrustCircle.update(_buildCircleModel());
                    });
                }
            }
        } catch (e) { console.warn('[TrustCircle] mount failed', e); }
    }

    // ── Trust Posture: matrix (profiles × controls) + maturity ladder + backtest ─
    // One glance answers "which of my agents are actually protected" — the ops
    // manual's control map (§3.2) and maturity ladder (§3.1), rendered live from
    // real config instead of read in a doc. The backtest panel replays a PROPOSED
    // policy over already-persisted turns (deterministic, 0 tokens) so the
    // observe → enforce climb is decided on evidence, not anxiety.
    const POSTURE_ICON = 'M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z';

    function _modeChip(mode, masterOff) {
        const m = (mode || 'observe').toLowerCase();
        // TC-9: when the platform master switch for this gate is off, the per-profile mode is
        // inert — the ring already says "off"; the matrix must not show an `enforce` chip for
        // a gate that does nothing.
        if (masterOff) {
            return `<span class="trust-chip u-tip" data-tip="The platform master switch for this gate is off — this profile setting is inert." style="opacity:.55;">off (master)</span>`;
        }
        // ONE map (W1): the chip reads `_postureState` like the ring, so the matrix and
        // the ring can never disagree about one stored mode (observe was neutral here
        // and amber on the ring). `watching` is a posture, not a finding — dashed, never
        // amber. The word is the reader's (`_postureLabel`), never the storage token.
        const st = _postureState(m);
        const cls = st === 'pass' ? 'tc-pass' : st === 'watching' ? 'tc-watching' : 'tc-na';
        const style = (m === 'off') ? 'opacity:.55;' : '';
        return `<span class="trust-chip ${cls}" style="${style}">${esc(_postureLabel(m))}</span>`;
    }

    // TC-10: the human label for an integrity state — borrowed from the seal vocabulary
    // (TrustBadge.META, Rule 8.4) so no raw wire token (`deployment_unattested`) leaks to a
    // user-facing chip. Absence-tolerant: if the trust badge is not loaded, the underscore
    // is at least spaced out rather than shown as a snake_case token.
    function _integLabel(state) {
        if (!state) return 'unknown';
        const M = (typeof window !== 'undefined' && window.TrustBadge && window.TrustBadge.META) || {};
        return (M[state] && M[state].label) || String(state).replace(/_/g, ' ');
    }

    function _integrityChip(state, signingInUse) {
        if (!state) return `<span class="trust-chip" style="opacity:.55;">unknown</span>`;
        // TC-10: severity from the ONE canonical map (_integrityState) so `changed` reads
        // danger here exactly as it does on the ring — not warn on one surface, danger on
        // the other. `unsigned` is na-vs-warn by the same deployment-wide signing-in-use rule.
        const sev = _integrityState(state, signingInUse);
        const cls = sev === 'pass' ? 'tc-pass' : sev === 'warn' ? 'tc-warn'
            : sev === 'danger' ? 'tc-danger' : '';
        return `<span class="trust-chip ${cls}">${esc(_integLabel(state))}</span>`;
    }

    // The server resolves authority once (core/agent_authority) and ships both the verdict
    // and its wording, so the ring, this matrix and the enforcer cannot describe the same
    // scope differently. Re-deriving it here is what produced the presence-vs-constraint bug.
    function _authoritySummary(p) {
        const s = p.authorityScope;
        if (!s || typeof s !== 'object') return { text: 'unscoped', scoped: false };
        const parts = s.constraints || [];
        return parts.length
            ? { text: parts.join(' · '), scoped: true }
            : { text: 'unscoped — full owner authority', scoped: false };
    }

    function _maturityLadder(m, rows) {
        const integ = _trustAgentsFull || [];
        const rungs = [
            { n: 1, label: 'Illuminate', met: !!(m.dashboard && m.dashboard.enabled),
              tip: 'Online evaluation is scoring live turns' },
            { n: 2, label: 'Foundation', met: integ.some((a) => (a.ingredients || []).some((i) => i.kind === 'ontology')),
              tip: 'At least one agent grounds on a knowledge model (ontology)' },
            { n: 3, label: 'Tighten', met: rows.some((r) => _actingMode(r.groundingMode) || _actingMode(r.outboundMode)),
              tip: 'At least one profile acts on grounding or guardrails (enforce or review)' },
            { n: 4, label: 'Govern & attest', met: integ.some((a) => a.state === 'trusted'),
              tip: 'At least one production agent is signed and intact' },
            { n: 5, label: 'Constrain autonomy', met: rows.some((r) => r.authority.scoped),
              tip: 'At least one autonomously-used profile carries an Agent Authority scope' },
        ];
        const chips = rungs.map((r) => `<span class="trust-chip u-tip ${r.met ? 'tc-pass' : ''}" data-tip="${esc(r.tip)}" style="${r.met ? '' : 'opacity:.6;'}">
            ${r.met
                ? '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M5 13l4 4L19 7"/></svg>'
                : '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="8"/></svg>'}
            ${r.n} · ${esc(r.label)}</span>`).join('');
        const stage = rungs.reduce((acc, r) => (r.met ? Math.max(acc, r.n) : acc), 0);
        return `<div class="flex flex-wrap items-center gap-1.5 mb-3">
            <span class="text-xs font-medium" style="color: var(--text-muted);">Maturity: stage ${stage} of 5</span>${chips}
        </div>`;
    }

    function renderTrustPosture(m) {
        if (!_profilesRaw.length) return '';
        // TC-35: rows are the caller's OWN profiles; resolve their cells key-shape-aware.
        const integByTag = {};
        (_trustAgentsFull || []).forEach((a) => { if (a.tag) integByTag[a.tag] = a.state; });
        // TC-10: deployment-wide "signing in use" — the same signal the ring uses so an
        // unsigned row reads na (nothing owed) vs warn (an act is owed) consistently.
        const signingInUse = Object.values(integByTag).some(
            (s) => s === 'trusted' || s === 'changed' || s === 'deployment_unattested');
        // TC-9: master-switch awareness — a gate whose platform master is off is inert.
        const masters = ((m && m.dashboard) || {}).deployment_masters || null;
        const gMasterOff = !!(masters && masters.grounding_gate_enabled === false);
        const oMasterOff = !!(masters && masters.guardrails_enabled === false);
        const rows = _profilesRaw.map((p) => {
            // TC-9: read the mode through the canonical _cfgMode (which normalises a
            // malformed non-string to 'off') — the old `.toLowerCase()` on a raw value threw
            // on one corrupt profile and took the ENTIRE Agents view down via load()'s catch.
            const gm = _cfgMode(p.groundingConfig, 'observe');
            const om = _cfgMode(p.guardrailConfig && p.guardrailConfig.outbound, 'observe');
            return { tag: p.tag, type: p.profile_type, color: p.color || ifoc(p.profile_type).color,
                     groundingMode: gm, outboundMode: om, authority: _authoritySummary(p),
                     integrity: integByTag[p.tag] || null,
                     behavior: ((_tbAgent(null, p.tag) || {}).composite) || null };
        });
        const hdr = `<div class="tp-row tp-head">
            <span>Agent</span><span>Grounding</span><span>Outbound guard</span>
            <span>Authority scope</span><span>Integrity</span>${_trustBehavior ? '<span>Behavioral</span>' : ''}</div>`;
        const body = rows.map((r) => {
            const beh = r.behavior
                ? `<span class="trust-chip u-tip ${['trusted', 'proven'].includes(r.behavior.state) ? 'tc-pass' : (r.behavior.state === 'watch' ? 'tc-warn' : (r.behavior.state === 'at_risk' ? 'tc-danger' : ''))}" data-tip="${esc(r.behavior.reason || 'behavioral state')}">${esc(TRUST_STATE_LABEL[r.behavior.state] || r.behavior.state)}</span>` : '';
            return `<div class="tp-row">
                <span class="text-xs font-semibold truncate" style="color: var(--text-primary);"><span style="display:inline-block;width:8px;height:8px;border-radius:9999px;background:${esc(r.color)};margin-right:6px;"></span>@${esc(r.tag)}</span>
                <span>${_modeChip(r.groundingMode, gMasterOff)}</span>
                <span>${_modeChip(r.outboundMode, oMasterOff)}</span>
                <span class="text-xs truncate" style="color: ${r.authority.scoped ? 'var(--text-primary)' : 'var(--text-muted)'};">${esc(r.authority.text)}</span>
                <span>${_integrityChip(r.integrity, signingInUse)}</span>
                ${_trustBehavior ? `<span>${beh}</span>` : ''}</div>`;
        }).join('');
        const cols = _trustBehavior
            ? 'minmax(110px,1.2fr) 90px 110px minmax(120px,1.4fr) 90px 100px'
            : 'minmax(110px,1.2fr) 90px 110px minmax(120px,1.4fr) 90px';
        const matrix = `<style>
            #trust-posture-matrix .tp-row{display:grid;grid-template-columns:${cols};gap:8px;align-items:center;padding:5px 4px;border-bottom:1px solid var(--border-secondary);}
            #trust-posture-matrix .tp-row:last-child{border-bottom:none;}
            /* The header row is INSIDE the scroller, so it must stick — a matrix
               whose column names scroll away turns every cell below the fold into
               an unlabelled chip. Opaque card background (never transparent, or
               rows read THROUGH it) and a z-index above the rows it outlives. */
            #trust-posture-matrix .tp-head{font-size:.64rem;text-transform:uppercase;letter-spacing:.04em;color:var(--text-muted);border-bottom:1px solid var(--border-primary);position:sticky;top:0;z-index:2;background:var(--card-bg);}
        </style><div id="trust-posture-matrix" style="max-height: 260px; overflow-y: auto;">${hdr}${body}</div>`;

        const selStyle = 'background: var(--card-bg); color: var(--text-primary); border: 1px solid var(--border-secondary);';
        const modeSel = (id, label, modes, def) => `<label class="text-xs flex items-center gap-1" style="color: var(--text-muted);">${label}
            <select id="${id}" class="text-xs rounded px-1.5 py-1" style="${selStyle}">
                ${modes.map((x) => `<option value="${x}" ${x === def ? 'selected' : ''}>${esc(_postureLabel(x))}</option>`).join('')}
            </select></label>`;
        const profileSel = `<label class="text-xs flex items-center gap-1" style="color: var(--text-muted);">agent
            <select id="trust-backtest-profile" class="text-xs rounded px-1.5 py-1" style="${selStyle}">
                <option value="">all</option>${rows.map((r) => `<option value="${esc(r.tag)}">@${esc(r.tag)}</option>`).join('')}
            </select></label>`;
        const backtest = `<div class="mt-3 pt-3" style="border-top: 1px solid var(--border-secondary);">
            <div class="text-xs font-medium mb-2" style="color: var(--text-primary);">Policy backtest
                <span class="text-[10px] font-normal" style="color: var(--text-muted);">— replay a proposed policy over your recent turns (deterministic, 0 tokens): see what enforce <em>would have</em> done before you flip it.</span></div>
            <div class="flex flex-wrap items-center gap-2">
                ${modeSel('trust-backtest-grounding', 'grounding', ['skip', 'observe', 'annotate', 'enforce'], 'enforce')}
                ${modeSel('trust-backtest-outbound', 'outbound', ['skip', 'observe', 'annotate', 'enforce'], 'enforce')}
                ${modeSel('trust-backtest-inbound', 'inbound', ['skip', 'enforce'], 'enforce')}
                ${profileSel}
                <button data-agent-action="trust-backtest-run" class="text-xs font-medium px-3 py-1.5 rounded-md" style="background: var(--teradata-orange); color: #fff; border: none;">Run backtest</button>
            </div>
            <div id="trust-backtest-out" class="mt-2"></div>
        </div>`;
        const probeOpts = rows.map((r) => `<option value="${esc(r.tag)}">@${esc(r.tag)}</option>`).join('');
        const tools = `<div class="mt-3 pt-3 flex flex-wrap items-center gap-2" style="border-top: 1px solid var(--border-secondary);">
            <button data-agent-action="resilience-test" class="text-xs font-medium px-3 py-1.5 rounded-md" style="color: var(--text-primary); border: 1px solid var(--border-secondary); background: transparent;">Run resilience self-test</button>
            <button data-agent-action="audit-pack" class="text-xs font-medium px-3 py-1.5 rounded-md" style="color: var(--text-primary); border: 1px solid var(--border-secondary); background: transparent;">Export audit pack</button>
            <button data-agent-action="retrieval-anomaly" class="text-xs font-medium px-3 py-1.5 rounded-md u-tip" data-tip="Index-poisoning detection: per-document retrieval frequency × query diversity\nover your recent turns. A doc retrieved for many UNRELATED queries is a\nsuspicious hub — flagged for YOUR disposition (nothing is auto-quarantined).\nStatistical detection, honestly labelled." style="color: var(--text-primary); border: 1px solid var(--border-secondary); background: transparent;">Scan retrieval patterns</button>
            <span class="text-[10px]" style="color: var(--text-muted);">probe the deterministic guards against an attack corpus (0 tokens) · download a signed, offline-verifiable evidence bundle</span>
            <div class="w-full flex flex-wrap items-center gap-2 mt-2 pt-2" style="border-top: 1px dashed var(--border-secondary);">
                <span class="text-[11px] font-medium" style="color: var(--text-primary);">Live probe</span>
                <select id="trust-probe-agent" class="text-xs rounded px-1.5 py-1" style="${selStyle}">${probeOpts}</select>
                <select id="trust-probe-count" class="text-xs rounded px-1.5 py-1" style="${selStyle}">
                    <option value="3" selected>3 probes</option><option value="6">6 probes (full)</option></select>
                <input id="trust-probe-corpus" type="text" placeholder="test collection id (optional)"
                    class="text-xs rounded px-1.5 py-1 u-tip" style="${selStyle} width: 170px;"
                    data-tip="Corpus lane (opt-in): name a TEST knowledge collection you own.\nPayload documents are planted, the retrieving questions asked, the\nagent's behaviour scored, and the payloads DELETED afterwards\n(cleanup status reported). Never run against a production corpus.">
                <button data-agent-action="live-redteam" class="text-xs font-medium px-3 py-1.5 rounded-md u-tip u-tip-right" data-tip="Drives REAL turns through the agent (costs tokens) and scores whether its guards actually held.\nDeterministic scoring; a failure lands in the Assurance Inbox." style="color: var(--text-primary); border: 1px solid var(--trust-warn, #fbbf24); background: transparent;">Run live probe</button>
                <span class="text-[10px]" style="color: var(--text-muted);">drives real turns (token cost) — the honest resilience test</span>
            </div>
            <div id="trust-tools-out" class="w-full mt-1"></div>
        </div>`;
        return sectionCard('Trust Posture', POSTURE_ICON, _maturityLadder(m, rows)
            + matrix + `<div id="trust-posture-tools">${backtest}${tools}</div>`);
    }

    async function runResilienceTest() {
        const out = $('trust-tools-out');
        if (!out) return;
        out.innerHTML = `<div class="text-xs" style="color: var(--text-muted);">Probing the deterministic guards…</div>`;
        try {
            const r = await api('/assurance/resilience-test', { method: 'POST' });
            const cats = r.categories || {};
            const chip = (label, c) => {
                const rec = c.recall == null ? '—' : Math.round(c.recall * 100) + '%';
                const cls = c.recall >= 0.9 ? 'tc-pass' : (c.recall >= 0.5 ? 'tc-warn' : 'tc-danger');
                return `<span class="trust-chip u-tip ${cls}" data-tip="${esc((c.misses || []).join('\n') || 'no misses')}">${label}: ${rec} (${c.caught}/${c.total})</span>`;
            };
            const fp = r.false_positives || {};
            out.innerHTML = `<div class="flex flex-wrap gap-1.5 items-center">
                <span class="text-xs font-medium" style="color: var(--text-primary);">Resilience ${Math.round((r.resilience_score || 0) * 100)}%</span>
                ${chip('injection', cats.prompt_injection || {})}${chip('PII', cats.pii || {})}${chip('toxicity', cats.toxicity || {})}
                <span class="trust-chip ${fp.false_positive_rate ? 'tc-warn' : 'tc-pass'}">false positives: ${Math.round((fp.false_positive_rate || 0) * 100)}%</span>
            </div><div class="text-[10px] mt-1" style="color: var(--text-muted);">${esc(r.note || '')}</div>`;
        } catch (e) {
            out.innerHTML = `<div class="text-xs" style="color: var(--trust-danger);">Self-test failed: ${esc(e.message)}</div>`;
        }
    }

    async function runRetrievalAnomaly() {
        const out = $('trust-tools-out');
        if (!out) return;
        out.innerHTML = `<div class="text-xs" style="color: var(--text-muted);">Analyzing retrieval patterns over your recent turns…</div>`;
        try {
            const r = await api('/assurance/retrieval-anomaly', { method: 'POST', body: '{}' });
            const flagged = r.flagged || [];
            const head = `<div class="flex flex-wrap items-center gap-1.5">
                <span class="text-xs font-medium" style="color: var(--text-primary);">Retrieval patterns</span>
                <span class="trust-chip">${_plural(r.documents_analyzed || 0, 'doc')} · ${_plural(r.turns_analyzed || 0, 'turn')}</span>
                ${flagged.length ? `<span class="trust-chip tc-warn" data-agent-action="goto-inbox" style="cursor: pointer;" role="link" tabindex="0">${_plural(flagged.length, 'suspicious hub')} — open the Assurance Inbox →</span>` : `<span class="trust-chip tc-pass">no hub signatures</span>`}
            </div>`;
            const _warnSvg = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px"><path d="M10.3 4.3 2.5 18a1.5 1.5 0 0 0 1.3 2.2h16.4a1.5 1.5 0 0 0 1.3-2.2L13.7 4.3a1.6 1.6 0 0 0-2.8 0Z"/><path d="M12 9v4M12 17h.01"/></svg>';
            const rows = flagged.slice(0, 5).map((f) => `<div class="text-[11px] py-0.5" style="color: var(--trust-warn, #f59e0b);">${_warnSvg} <code>${esc(f.filename || f.document_id)}</code> — ${esc(f.why)}</div>`).join('');
            out.innerHTML = head + (rows ? `<div class="mt-1">${rows}</div>` : '')
                + `<div class="text-[10px] mt-1" style="color: var(--text-muted);">${esc(r.note || '')}</div>`;
        } catch (e) {
            out.innerHTML = `<div class="text-xs" style="color: var(--trust-danger);">Pattern scan failed: ${esc(e.message)}</div>`;
        }
    }

    async function runLiveProbe() {
        const out = $('trust-tools-out');
        if (!out) return;
        const tag = ($('trust-probe-agent') || {}).value || '';
        const count = parseInt(($('trust-probe-count') || {}).value || '3', 10);
        if (!tag) { out.innerHTML = `<div class="text-xs" style="color:var(--trust-warn);">Pick an agent to probe.</div>`; return; }
        out.innerHTML = `<div class="text-xs" style="color: var(--text-muted);">Driving ${_plural(count, 'real probe turn')} through @${esc(tag)} — this costs tokens and may take a minute…</div>`;
        try {
            const corpusId = (($('trust-probe-corpus') || {}).value || '').trim();
            const body = { profile_tag: tag, max_probes: count };
            if (corpusId) body.corpus_collection_id = parseInt(corpusId, 10);
            const d = await api('/assurance/live-redteam', { method: 'POST',
                body: JSON.stringify(body) });
            const s = d.summary || {};
            const rchip = s.resilience == null ? `<span class="trust-chip">no adjudicable probes</span>`
                : `<span class="trust-chip ${s.resilience >= 0.99 ? 'tc-pass' : (s.resilience >= 0.5 ? 'tc-warn' : 'tc-danger')}">resilience ${Math.round(s.resilience * 100)}%</span>`;
            const _xSvg = '<svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" style="vertical-align:-1px"><path d="M6 6l12 12M18 6 6 18"/></svg>';
            const fails = (s.failures || []).map((f) => `<div class="text-[11px] py-0.5" style="color: var(--trust-danger, #f87171);">${_xSvg} ${esc(f.category)} · ${esc(f.reason)}${f.answer_excerpt ? ` — “${esc(f.answer_excerpt)}”` : ''}</div>`).join('');
            out.innerHTML = `<div class="flex flex-wrap items-center gap-1.5">
                    <span class="text-xs font-medium" style="color: var(--text-primary);">@${esc(tag)}</span>${rchip}
                    <span class="trust-chip tc-pass">${s.held || 0} held</span>
                    ${s.failed ? `<span class="trust-chip tc-danger">${s.failed} FAILED</span>` : ''}
                    ${s.unknown ? `<span class="trust-chip">${s.unknown} unknown</span>` : ''}
                </div>${fails ? `<div class="mt-1">${fails}</div>` : ''}
                ${s.corpus_cleanup ? `<div class="text-[10px] mt-1" style="color: ${s.corpus_cleanup === 'clean' ? 'var(--trust-pass, #34d399)' : 'var(--trust-danger, #f87171)'};">corpus payload cleanup: ${esc(s.corpus_cleanup)}</div>` : ''}
                <div class="text-[10px] mt-1" style="color: var(--text-muted);">${esc(s.note || '')}</div>`;
        } catch (e) {
            out.innerHTML = `<div class="text-xs" style="color: var(--trust-danger);">Live probe failed: ${esc(e.message)}</div>`;
        }
    }

    async function exportAuditPack() {
        const out = $('trust-tools-out');
        try {
            if (out) out.innerHTML = `<div class="text-xs" style="color: var(--text-muted);">Assembling signed audit pack…</div>`;
            const d = await api('/assurance/audit-pack', { method: 'POST' });
            const blob = new Blob([JSON.stringify(d, null, 2)], { type: 'application/json' });
            const stamp = (d.audit_pack && d.audit_pack.generated_at || '').slice(0, 10);
            window.FilesKeep.deliver(blob, `uderia_audit_pack_${stamp || 'export'}.json`, { what: 'audit pack' });
            const signed = d.attestation && d.attestation.signed;
            if (out) out.innerHTML = `<div class="text-xs" style="color: var(--trust-pass, #34d399);">Audit pack downloaded — ${signed ? 'Ed25519-signed, offline-verifiable' : 'content-hashed (signing key unavailable)'}.</div>`;
        } catch (e) {
            if (out) out.innerHTML = `<div class="text-xs" style="color: var(--trust-danger);">Export failed: ${esc(e.message)}</div>`;
        }
    }

    async function runBacktest() {
        const out = $('trust-backtest-out');
        if (!out) return;
        const val = (id) => ($(id) || {}).value || '';
        const policy = {};
        if (val('trust-backtest-grounding') !== 'skip') policy.grounding_mode = val('trust-backtest-grounding');
        if (val('trust-backtest-outbound') !== 'skip') policy.outbound = { mode: val('trust-backtest-outbound'), pii: true, toxicity: true };
        if (val('trust-backtest-inbound') !== 'skip') policy.inbound = { mode: val('trust-backtest-inbound') };
        if (!Object.keys(policy).length) {
            out.innerHTML = `<div class="text-xs" style="color: var(--trust-warn);">Pick at least one policy section.</div>`;
            return;
        }
        const body = { policy, max_sessions: 20, max_turns: 300 };
        const tag = val('trust-backtest-profile');
        if (tag) body.profile_tag = tag;
        out.innerHTML = `<div class="text-xs" style="color: var(--text-muted);">Replaying policy over recent turns…</div>`;
        try {
            const d = await api('/evals/backtest', { method: 'POST', body: JSON.stringify(body) });
            const s = d.summary || {};
            const g = s.grounding || {}, o = s.outbound || {}, i = s.inbound || {};
            const stat = (label, n, cls) => `<span class="trust-chip ${n ? cls : ''}" style="${n ? '' : 'opacity:.55;'}">${label}: ${n || 0}</span>`;
            const sample = (x) => `<div class="text-[11px] py-0.5" style="color: var(--text-muted); border-bottom: 1px dashed var(--border-secondary);">
                turn ${esc(x.turn)} · ${esc((x.session_id || '').slice(0, 8))}… — ${esc(x.reason || x.would || '')}${x.out_of_scope_dbs && x.out_of_scope_dbs.length ? ' · out-of-scope: ' + x.out_of_scope_dbs.map(esc).join(', ') : ''}
                <span style="opacity:.8">“${esc(x.user_query || '')}”</span></div>`;
            const samples = [...(g.samples || []), ...(o.samples || []), ...(i.samples || [])];
            out.innerHTML = `
                <div class="text-xs mb-1" style="color: var(--text-primary);">${_plural(s.turns_examined || 0, 'turn')} examined across ${_plural(d.sessions_examined || 0, 'session')}</div>
                <div class="flex flex-wrap gap-1.5 mb-1">
                    ${policy.grounding_mode ? stat('grounding: would escalate to judge', g.would_escalate_judge, 'tc-warn') + stat('would disclose', g.would_annotate, 'tc-warn') + stat('already blocked live', g.already_blocked, 'tc-danger') : ''}
                    ${policy.outbound ? stat('outbound: would mask personal data', o.would_redact, 'tc-warn') + stat('would withhold (toxicity)', o.would_block, 'tc-danger') : ''}
                    ${policy.inbound ? stat('inbound: would refuse (injection)', i.would_block, 'tc-danger') : ''}
                </div>
                ${samples.length ? `<div style="max-height:140px;overflow-y:auto;">${samples.map(sample).join('')}</div>`
                                 : `<div class="text-xs" style="color: var(--trust-pass, #34d399);">Nothing would have been altered — this policy is safe to enable on the examined traffic.</div>`}
                <div class="text-[10px] mt-1" style="color: var(--text-muted);">Guardrail projections replay the exact live engine on stored text. Grounding projections are trigger-level — the judge (an LLM) is honestly not simulated, so flagged turns read “would escalate”, never a certain block.</div>`;
        } catch (e) {
            out.innerHTML = `<div class="text-xs" style="color: var(--trust-danger);">Backtest failed: ${esc(e.message)}</div>`;
        }
    }

    // ── Assurance Inbox — trust incidents as worked items, not archaeology ──────
    // Two honest sources: STORED events (breaker trips, memory quarantines —
    // ack-able) and DERIVED events read live from the persisted turn stamps
    // (grounding blocks, guardrail actions, honesty rewrites, the multi-turn
    // session sentinel) — derived items ARE the trace, so they need no ack.
    const INBOX_ICON = 'M15 17h5l-1.4-1.4A2 2 0 0118 14.2V11a6 6 0 00-4-5.7V5a2 2 0 10-4 0v.3A6 6 0 006 11v3.2c0 .5-.2 1-.6 1.4L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9';

    /** Wire severities (info/warning/danger) are backend facts and must never reach a
     *  rendered chip as raw strings (Rule 3.0). The inbox speaks the SAME attention
     *  vocabulary as every ring: danger → "needs you now", warning → "worth a look",
     *  info → "for the record". One source: TrustGrammar (PROP-002). */
    function _sevWord(sev) {
        const TG = (typeof globalThis !== 'undefined' && globalThis.TrustGrammar) || null;
        return (TG && TG.SEVERITY_WORD && TG.SEVERITY_WORD[sev])
            || ({ danger: 'needs you now', warning: 'worth a look', info: 'for the record' })[sev]
            || 'for the record';
    }

    function _sevChip(sev) {
        const cls = sev === 'danger' ? 'tc-danger' : (sev === 'warning' ? 'tc-warn' : '');
        return `<span class="trust-chip ${cls}">${esc(_sevWord(sev))}</span>`;
    }

    /** The row's scan channel (operator-settled): a leading dot in the trust
     *  tokens — red = danger, amber = warning, muted = info. The WORD chip stays
     *  beside it: colour is never the sole carrier (Rule 9.4). */
    function _sevDot(sev) {
        const c = sev === 'danger' ? 'var(--trust-danger, #f87171)'
            : sev === 'warning' ? 'var(--trust-warn, #fbbf24)'
            : 'var(--text-muted, #94a3b8)';
        const op = sev === 'danger' || sev === 'warning' ? '1' : '.45';
        return `<span style="width:7px;height:7px;border-radius:9999px;background:${c};opacity:${op};flex-shrink:0;margin-top:5px;display:inline-block;"></span>`;
    }

    /** Every inbox item's TITLE — what happened, in a scannable phrase.
     *
     *  A row used to be one run-on sentence in one weight ("Retrieval firewall defused a
     *  chunk from 'Uderia Documentation' (pattern(s): ignore_previous, jailbreak_marker)
     *  — kept, labelled untrusted data"). Everything was equally loud, so nothing could
     *  be scanned: an operator triaging a queue reads DOWN the titles first and only then
     *  across into the one row that matters. Title carries the KIND of event; the
     *  backend's summary becomes the detail line beneath it.
     *
     *  Declared, never derived from the summary text — a title parsed out of prose breaks
     *  the moment the prose is reworded, and the summaries are written for precision, not
     *  for their first six words. An unknown type falls back to a humanised event key,
     *  which is honest and stays readable rather than inventing a heading.
     */
    const _INBOX_TITLE = {
        // derived from the sealed turn stamps
        grounding_annotate: 'Unsupported claims disclosed',
        grounding_block: 'Answer withheld — unsupported by its sources',
        guardrail_inbound_annotate: 'Inbound request flagged',
        guardrail_inbound_block: 'Request refused at the door',
        guardrail_inbound_redact: 'Inbound request redacted',
        guardrail_outbound_annotate: 'Answer annotated before delivery',
        guardrail_outbound_block: 'Answer withheld by the outbound guard',
        guardrail_outbound_redact: 'Values masked before delivery',
        honesty_flag: 'Overclaimed certainty flagged',
        honesty_rewrite: 'Answer rewritten for honesty',
        judge_condemnation: 'A judge overruled a delivered answer',
        session_sentinel: 'Repeated probing across this session',
        // stored incidents
        corpus_finding_ack: 'Corpus finding acknowledged',
        planner_finding_ack: 'Planner finding acknowledged',
        planner_case_removed: 'Champion case removed',
        corpus_ingest_injection: 'Injection pattern found at ingest',
        corpus_quarantine: 'Document quarantined',
        live_redteam_failure: 'Live probe — the agent did not hold',
        memory_quarantine: 'Memory write quarantined',
        retrieval_anomaly: 'Retrieval hub anomaly',
        retrieval_defuse: 'Retrieval firewall defused a chunk',
        review_release: 'Withheld answer released',
        tool_result_injection: 'Injection pattern in a tool result',
        trust_breaker: 'Trust budget breaker tripped',
        surface_breach: 'Surface breach — the confinement leaked',
        policy_observed: 'Policy observation — a call the contracts-only policy would refuse',
    };

    function _inboxTitle(e) {
        const t = String((e && e.event_type) || '');
        if (_INBOX_TITLE[t]) return _INBOX_TITLE[t];
        // The station-shortfall family is parameterised by station, so it is built
        // rather than listed — one entry per station would be five copies of one rule.
        if (t.startsWith('station_shortfall_')) {
            const st = _stationOfEvent(t);
            return `${_stationLabel(st)} execution below the danger floor`;
        }
        return t ? t.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()) : 'Trust incident';
    }

    /** The DETAIL line — the backend summary with the title's own words removed.
     *
     *  The summary is a complete sentence that necessarily restates what the title now
     *  says ("Retrieval firewall defused a chunk from 'X' — kept, labelled untrusted").
     *  Printing both would put the same clause on screen twice, one line apart, which is
     *  precisely the restatement Rule 3.0 bans between slots. Strip the leading clause
     *  when it duplicates the title, keep everything the title does not carry.
     */
    function _inboxDetail(e) {
        const sum = String((e && e.summary) || '').trim();
        if (!sum) return '';
        const title = _inboxTitle(e);
        // Longest shared prefix of WORDS, so a reworded summary degrades to showing more
        // rather than to showing a mangled fragment.
        const a = title.toLowerCase().split(/\s+/), b = sum.split(/\s+/);
        let i = 0;
        while (i < a.length && i < b.length && b[i].toLowerCase().replace(/[^a-z0-9]/g, '')
               === a[i].replace(/[^a-z0-9]/g, '')) i++;
        if (i < 3) return sum;                       // no meaningful overlap — show it whole
        let rest = b.slice(i).join(' ').replace(/^[\s—–\-:,]+/, '');
        // The agent already has its own slot in the meta line, so a summary that opens
        // "for @RESTO — …" says it twice one line apart (Rule 3.0: no slot restates
        // another). Drop the lead-in, keep the finding.
        if (e && e.profile_tag) {
            rest = rest.replace(new RegExp('^for\\s+@?' + e.profile_tag + '\\b[\\s—–\\-:,]*', 'i'), '');
        }
        return rest || sum;
    }

    /** Group identical recurring findings into ONE row.
     *
     *  The same firewall defusing the same pattern in the same document four times is
     *  four facts and one problem. Listed flat they filled the queue and pushed the two
     *  danger items off the first screen — the queue was sorted by severity and still
     *  read as noise, because repetition outweighed rank visually.
     *
     *  The signature is what an operator would call "the same thing": the event kind, the
     *  agent, and the payload's own subject (document / collection / tool). Time is
     *  deliberately NOT in it — recurrence over time is the very thing being collapsed.
     *  Items that carry a disposition or an ack stay separate: those are decided, and
     *  folding a decided item into an open group would hide a completed act.
     */
    function _inboxSignature(e) {
        const p = e.payload || {};
        if (e.disposition_label || e.acknowledged) return `solo:${e.id || Math.random()}`;
        return [e.event_type || '', e.profile_tag || '', e.severity || '',
                p.document_id || p.collection_id || p.tool || '',
                (p.labels || p.patterns || p.categories || []).join(',')].join('|');
    }

    function _groupInbox(items) {
        const by = new Map();
        for (const e of items) {
            const k = _inboxSignature(e);
            if (!by.has(k)) by.set(k, []);
            by.get(k).push(e);
        }
        return [...by.values()];
    }

    /** One inbox row: TITLE · detail · meta · actions.
     *
     *  `group` is every occurrence that collapsed into this row, newest first; the row
     *  renders the newest and offers the rest behind a count. A single occurrence renders
     *  identically minus the count, so there is one row anatomy, not two.
     */
    function _inboxRow(group) {
        const all = Array.isArray(group) ? group : [group];
        const e = all[0];
        const n = all.length;

        const tagHtml = _agentTagChip(e.profile_tag);
        const _rowSt = _stationOfEvent(e.event_type);
        const _rowStLabel = _stationLabel(_rowSt);
        const when = e.created_at
            ? `<span class="u-tip" data-tip="${esc(new Date(e.created_at).toLocaleString())}">${esc(relTime(new Date(e.created_at).getTime() / 1000))}</span>`
            : '<span class="u-tip" data-tip="Read live from the sealed trace — this item has no stored timestamp of its own.">live</span>';
        const where = [e.session_id ? (String(e.session_id).slice(0, 8) + '…') : '',
                       e.turn_id != null && e.turn_id !== '' ? ('turn ' + e.turn_id) : ''].filter(Boolean).join(' · ');

        // ── the batch contract ────────────────────────────────────────────────
        // A collapsed row's buttons act on EVERY occurrence it collapsed; expanding it
        // gives each occurrence its own. One shape serves both, so a single item and a
        // group of twelve run the identical code path — a group-only branch is how the
        // two would drift, and the single case is by far the better tested of the two.
        //
        // The ack target differs by source: a STORED incident is keyed by its own id, a
        // DERIVED observation by (session, turn, event_type) — it is a disposition ABOUT
        // the sealed trace, which is never rewritten. Both travel as one list.
        const _ackable = all.filter((x) => !x.acknowledged && !x.disposition_label
                                        && !(x.source === 'stored' && x.is_receipt));
        const _ackBatch = _ackable.map((x) => x.source === 'stored'
            ? { id: x.id }
            : { session_id: x.session_id || null,
                turn_id: x.turn_id == null || x.turn_id === '' ? null : x.turn_id,
                event_type: x.event_type || '' });
        const ackBtn = (batch, label) => batch.length
            ? `<button data-agent-action="inbox-ack-batch" data-batch="${esc(JSON.stringify(batch))}"
                 class="ai-btn u-tip u-tip-left"
                 data-tip="Mark ${batch.length === 1 ? 'this observation' : `all ${batch.length} occurrences`} as seen.\nThe sealed trace itself is never rewritten — the acknowledgment is a\nseparate recorded disposition about it.">${esc(label)}</button>`
            : '';
        const ack = ackBtn(_ackBatch, _ackBatch.length > 1 ? `Ack all ${_ackBatch.length}` : 'Ack');
        const p = e.payload || {};
        const docId = p.document_id;
        const _QUARANTINABLE = ['retrieval_defuse', 'corpus_ingest_injection', 'retrieval_anomaly'];
        // The document is part of the grouping signature, so every occurrence in a group
        // names the SAME document: quarantining once covers them all, and the acks that
        // follow are the batch. The button therefore never needs a per-group variant.
        const quarantineBtn = (item, batch) => {
            const q = item.payload || {};
            return q.document_id && _QUARANTINABLE.indexOf(item.event_type) >= 0
                ? `<button data-agent-action="inbox-quarantine" data-doc-id="${esc(q.document_id)}"
                     data-batch="${esc(JSON.stringify(batch))}"
                     class="ai-btn ai-btn-act u-tip u-tip-left"
                     data-tip="Quarantine '${esc(q.filename || q.document_id)}': excluded from retrieval,\nstays visible in the repository manager, reversible.\nThe corpus signature reads changed until re-signed.${
                        batch.length > 1 ? `\nAcknowledges all ${batch.length} occurrences.` : ''}">Quarantine doc</button>`
                : '';
        };
        const disp = e.disposition_label
            ? `<span class="trust-chip tc-pass ai-disp">${esc(e.disposition_label)}</span>`
            : (e.is_receipt ? `<span class="trust-chip ai-disp" style="opacity:.7;">recorded</span>`
                : (e.source === 'derived' && e.acknowledged
                    ? `<span class="trust-chip ai-disp" style="opacity:.7;">seen</span>` : ''));

        // The recurrence count is a COUNT, not a severity — it never changes the row's
        // colour, only how many times this same finding occurred.
        const countChip = n > 1
            ? `<span class="ai-count u-tip" data-tip="This finding occurred ${n} times. Times are listed below.">×${n}</span>` : '';
        // Expanding a group gives every occurrence its OWN actions — the collapsed row
        // acts on all of them, and a reader who disagrees with that can still work one
        // at a time. Collapsing without that would trade precision for tidiness, which
        // is not a trade a triage queue may make.
        const occurrences = n > 1
            ? `<details class="ai-occ"><summary>${n} occurrences</summary>${all.map((o) => {
                 const one = (!o.acknowledged && !o.disposition_label
                              && !(o.source === 'stored' && o.is_receipt))
                     ? [o.source === 'stored' ? { id: o.id }
                        : { session_id: o.session_id || null,
                            turn_id: o.turn_id == null || o.turn_id === '' ? null : o.turn_id,
                            event_type: o.event_type || '' }]
                     : [];
                 const settled = o.disposition_label || (o.acknowledged ? 'seen' : '');
                 return `<div class="ai-occ-row">
                     <span class="ai-occ-when">${esc(o.created_at ? new Date(o.created_at).toLocaleString() : 'live (from the trace)')}${
                         o.turn_id != null && o.turn_id !== '' ? ` · turn ${esc(o.turn_id)}` : ''}</span>
                     <span class="ai-occ-act">${quarantineBtn(o, one)}${ackBtn(one, 'Ack')}${
                         settled ? `<span class="ai-occ-settled">${esc(settled)}</span>` : ''}</span>
                 </div>`;
               }).join('')}</details>`
            : '';

        // ── the route: where this finding is WORKED ──────────────────────────
        // Rule 8.5 — the offered act must be the act that is OWED. An acknowledged
        // `needs you now` row offered nothing at all, which reads as "urgent, and there
        // is nothing to do": `Ack` disposes of the NOTICE, never of the condition. A
        // station shortfall in particular persists and re-derives after every ack —
        // what is owed there is to go look at the agent, not to tick the notice again.
        //
        // Routed by what the row IS, using the machinery that already exists: a
        // turn-scoped finding opens that turn's trace, an agent-scoped one opens the
        // agent's roster detail (where the failing dimension bars are). Offered on
        // EVERY row, disposed or not — a disposition settles the notice, and the place
        // it is worked stays worth reaching.
        const route = e.session_id
            ? `<button class="ai-btn" data-ap-open-session="${esc(e.session_id)}"
                 data-ap-turn="${esc(e.turn_id == null || e.turn_id === '' ? 1 : e.turn_id)}"
                 >Open turn</button>`
            : (e.profile_tag
                ? `<button class="ai-btn" data-agent-action="inbox-open-agent"
                     data-tag="${esc(e.profile_tag)}">Open @${esc(e.profile_tag)}</button>`
                : '');

        const detail = _inboxDetail(e);
        return `<div class="ai-row ai-sev-${esc(e.severity || 'info')}">
            <div class="ai-main">
                <div class="ai-title-row">
                    <span class="ai-title">${esc(_inboxTitle(e))}</span>${countChip}${disp}
                </div>
                ${detail ? `<div class="ai-detail">${esc(detail)}</div>` : ''}
                <div class="ai-meta"><span class="ai-sev">${esc(_sevWord(e.severity))}</span>${tagHtml}<span class="ai-station">${esc(_rowStLabel)}</span>${
                    where ? `<span>${esc(where)}</span>` : ''}${when}${
                    e.source === 'derived' ? '<span class="u-tip" data-tip="Recomputed from the turn\'s own sealed stamps — not a separate stored record.">from the trace</span>' : ''}</div>
                ${occurrences}
            </div>
            <div class="ai-actions">${route}${quarantineBtn(e, _ackBatch)}${ack}</div>
        </div>`;
    }

    // Pending HITL review item: the withheld ORIGINAL is the owner's own content —
    // shown on expand so they can disposition it (approve = seen and accepted;
    // uphold = the withholding stands). History is never rewritten either way.
    // Every gate that can park an original for review. A binary ternary here used to
    // mislabel anything that wasn't the grounding gate as "outbound guardrail" — since
    // review reached the inbound guardrail and both honesty branches, three of the five
    // sources would have named the wrong gate on the owner's disposition screen.
    const _REVIEW_SOURCE = {
        grounding: 'grounding gate',
        guardrail_outbound: 'outbound guardrail',
        guardrail_inbound: 'inbound guardrail',
        honesty: 'honesty gate',
        honesty_rag: 'honesty gate (retrieval sufficiency)',
        action: 'signed action',
    };

    /** #52 — the review row's category suffix.
     *
     *  It rendered `r.categories` verbatim, so a parked injection read
     *  "prompt-injection detected (2 patterns) · prompt_injection": the category once
     *  as prose and once as a raw wire token. Rule 3.0 bans snake_case reaching a slot,
     *  and the gate sweeps the ring — the inbox renderer was never covered.
     *
     *  Two rules, both needed: name the category from the ONE published vocabulary
     *  (`window.TrustVocab.category`, defined in ui.js beside the guardrail card that
     *  also renders it), and drop any category the reason already states, so the row
     *  never says the same thing twice.
     */
    function _reviewCategories(r) {
        const vocab = (window.TrustVocab && window.TrustVocab.category) || {};
        const reason = String(r.reason || '').toLowerCase();
        const said = (label) => reason.includes(String(label).toLowerCase())
            // "prompt-injection detected" already states `prompt_injection`.
            || reason.includes(String(label).toLowerCase().replace(/[\s_]/g, '-'));
        const names = (r.categories || [])
            .map((c) => vocab[c] || String(c).replace(/_/g, ' '))
            .filter((label) => label && !said(label));
        return names.length ? ' · ' + names.map(esc).join(', ') : '';
    }

    /** A parked WRITE (OBDA-ACT): the row shows the identical diff the person who asked saw — the
     *  database's own before → after, computed in a transaction that was rolled back — and
     *  ONE Approve that performs exactly that write through the signed contract. */
    function _actionReviewRow(r) {
        let p = {};
        try { p = JSON.parse(r.original_text || '{}') || {}; } catch (_) { p = {}; }
        const cons = p.consequence || {};
        const lines = Array.isArray(cons.lines) ? cons.lines : [];
        const rTagHtml = _agentTagChip(r.profile_tag);
        const where = [r.session_id ? String(r.session_id).slice(0, 8) + '…' : '',
                       r.turn_id ? 'turn ' + r.turn_id : ''].filter(Boolean).join(' · ');
        const head = `${esc(p.action || (r.categories || [])[0] || 'an action')}${p.version ? ' v' + esc(p.version) : ''}`;
        // four eyes (PROP-008 §5.4a): a write from SOMEONE ELSE's agent awaiting you names its
        // owner; your own agent's write awaiting a designated approver names them and offers no
        // decision — the server refuses the owner's approval, so a button would be a lie
        const theirs = r.owner ? ` from ${esc(r.owner)}'s agent` : '';
        const awaitingOthers = r.can_decide === false;
        const awaitWho = (r.awaiting || []).map(esc).join(', ') || 'a designated approver';
        const title = awaitingOthers
            ? `A write from your agent awaits ${awaitWho} — ${head}${p.target ? ' on ' + esc(p.target) : ''}`
            : `A write${theirs} awaits your approval — ${head}${p.target ? ' on ' + esc(p.target) : ''}`;
        const until = awaitingOthers ? `nothing is written until ${awaitWho} approves` : 'nothing is written until you approve';
        const acts = awaitingOthers
            ? `<span class="text-[11px] flex-shrink-0" style="color: var(--text-muted);">awaiting ${awaitWho}</span>`
            : null;
        return `<div class="ap-review-item py-1.5 border-b" data-review-source="action" style="border-color: var(--border-secondary);">
            <div class="flex items-start gap-2">
                ${_sevDot(awaitingOthers ? 'review' : 'danger')}
                <span class="trust-chip tc-warn">write</span>
                <div class="flex-1 min-w-0">
                    <div class="text-xs" style="color: var(--text-primary);">${title}</div>
                    <div class="text-[10px] mt-0.5" style="color: var(--text-muted);">${rTagHtml}${rTagHtml && where ? ' · ' : ''}${esc(where)}${(rTagHtml || where) ? ' · ' : ''}${until}</div>
                    ${lines.length ? `<ul class="ap-act-diff text-[11px] mt-1" style="color: var(--text-primary); list-style: none; padding: 0; margin: 4px 0 0;">${lines.map((l) => `<li class="font-mono" style="padding: 1px 0;">${esc(l)}</li>`).join('')}</ul>` : `<div class="text-[11px] mt-1" style="color: var(--text-muted);">The change was not recorded with this item — approve performs the parked invocation as compiled.</div>`}
                    ${cons.summary ? `<div class="text-[10px] mt-0.5" style="color: var(--text-muted);">${esc(cons.summary)}</div>` : ''}
                </div>
                ${acts !== null ? acts : `<button data-agent-action="review-approve" data-review-id="${esc(r.id)}"
                    class="text-[11px] px-2 py-0.5 rounded flex-shrink-0 u-tip u-tip-right" data-tip="Performs exactly this write through the signed contract, then records your approval.\nThe contract, its signature and the agent's grant are checked again first —\nif anything changed while it waited, nothing is written and the item stays open."
                    style="color: var(--trust-pass, #34d399); border: 1px solid var(--trust-pass, #34d399); background: transparent;">Approve — perform it</button>
                <button data-agent-action="review-reject" data-review-id="${esc(r.id)}"
                    class="text-[11px] px-2 py-0.5 rounded flex-shrink-0 u-tip u-tip-right" data-tip="Nothing is written; your decision is recorded on that turn."
                    style="color: var(--text-muted); border: 1px solid var(--border-secondary); background: transparent;">Reject</button>`}
            </div></div>`;
    }

    /** A platform change an agent submitted (TDA_Platform, phase 3): what it asks to do, with
     *  exactly which arguments — approving runs it as you, after it is checked again. */
    function _platformReviewRow(r) {
        let p = {};
        try { p = JSON.parse(r.original_text || '{}') || {}; } catch (_) { p = {}; }
        const rTagHtml = _agentTagChip(r.profile_tag);
        const where = [r.session_id ? String(r.session_id).slice(0, 8) + '…' : '',
                       r.turn_id ? 'turn ' + r.turn_id : ''].filter(Boolean).join(' · ');
        const args = p.args && typeof p.args === 'object' ? Object.entries(p.args) : [];
        const always = /always reviewed: (.+)$/.exec(r.reason || '');
        return `<div class="ap-review-item py-1.5 border-b" data-review-source="platform" style="border-color: var(--border-secondary);">
            <div class="flex items-start gap-2">
                ${_sevDot('danger')}
                <span class="trust-chip tc-warn">change</span>
                <div class="flex-1 min-w-0">
                    <div class="text-xs" style="color: var(--text-primary);">An agent asks to: ${esc(p.summary || (r.categories || [])[0] || 'change the platform')}</div>
                    <div class="text-[10px] mt-0.5" style="color: var(--text-muted);">${rTagHtml}${rTagHtml && where ? ' · ' : ''}${esc(where)}${(rTagHtml || where) ? ' · ' : ''}nothing changes until you approve${always ? ' · always reviewed: ' + esc(always[1]) : ''}</div>
                    <div class="text-[10px] mt-0.5 font-mono" style="color: var(--text-muted);">${esc(p.op || '')}</div>
                    ${args.length ? `<ul class="text-[11px] mt-1" style="color: var(--text-primary); list-style: none; padding: 0; margin: 4px 0 0;">${args.map(([k, v]) => `<li class="font-mono" style="padding: 1px 0;">${esc(k)} = ${esc(typeof v === 'string' ? v : JSON.stringify(v))}</li>`).join('')}</ul>` : ''}
                </div>
                <button data-agent-action="review-approve" data-review-id="${esc(r.id)}"
                    class="text-[11px] px-2 py-0.5 rounded flex-shrink-0 u-tip u-tip-right" data-tip="Runs exactly this request as you, then records your approval.\nIt is checked against the platform again first —\nif it no longer holds, nothing changes and the item stays open."
                    style="color: var(--trust-pass, #34d399); border: 1px solid var(--trust-pass, #34d399); background: transparent;">Approve — run it</button>
                <button data-agent-action="review-reject" data-review-id="${esc(r.id)}"
                    class="text-[11px] px-2 py-0.5 rounded flex-shrink-0 u-tip u-tip-right" data-tip="Nothing changes; your decision is recorded."
                    style="color: var(--text-muted); border: 1px solid var(--border-secondary); background: transparent;">Reject</button>
            </div></div>`;
    }

    function _reviewRow(r) {
        if (r && r.source === 'action') return _actionReviewRow(r);
        if (r && r.source === 'platform') return _platformReviewRow(r);
        const srcLabel = _REVIEW_SOURCE[r.source] || 'trust gate';
        // A `retained` item is plain enforce's RECOVERY-LEDGER entry: recoverable and
        // dispositionable, but never owed work — its row must not wear the owed-work
        // costume (danger dot, warn chip, "awaiting your disposition").
        const retained = r.status === 'retained';
        // The SAME chip as every other @TAG in the platform — a review row's agent is
        // not a different kind of agent, and two tag renderings in one panel is the
        // drift `_agentTagChip` exists to end.
        const rTagHtml = _agentTagChip(r.profile_tag);
        const where = [r.session_id ? String(r.session_id).slice(0, 8) + '…' : '',
                       r.turn_id ? 'turn ' + r.turn_id : ''].filter(Boolean).join(' · ');
        return `<div class="ap-review-item py-1.5 border-b" style="border-color: var(--border-secondary);">
            <div class="flex items-start gap-2">
                ${retained ? _sevDot('warning') : _sevDot('danger')}
                <span class="trust-chip${retained ? '' : ' tc-warn'}">${retained ? 'retained' : 'review'}</span>
                <div class="flex-1 min-w-0">
                    <div class="text-xs" style="color: var(--text-primary);">${
                        // Inbound is the one direction where nothing was withheld FROM the
                        // user — the incoming request was refused before the model read it.
                        retained
                            ? `Withheld by the ${srcLabel} — original retained (no action owed)`
                            : r.source === 'guardrail_inbound'
                            ? `Request refused by the ${srcLabel} — awaiting your disposition`
                            : `Withheld by the ${srcLabel} — awaiting your disposition`
                    }</div>
                    <div class="text-[10px] mt-0.5" style="color: var(--text-muted);">${rTagHtml}${rTagHtml && where ? ' · ' : ''}${esc(where)}${(rTagHtml || where) ? ' · ' : ''}${esc(r.reason || '')}${_reviewCategories(r)}</div>
                </div>
                <button data-agent-action="review-approve" data-review-id="${esc(r.id)}"
                    class="text-[11px] px-2 py-0.5 rounded flex-shrink-0 u-tip u-tip-right" data-tip="Record only: you have seen the withheld original and accept it.\nRecorded on the turn; the conversation itself is unchanged."
                    style="color: var(--trust-pass, #34d399); border: 1px solid var(--trust-pass, #34d399); background: transparent;">Approve</button>
                ${_RELEASABLE_SOURCES.has(r.source || '') ? `
                <button data-agent-action="review-release" data-review-id="${esc(r.id)}"
                    class="text-[11px] px-2 py-0.5 rounded flex-shrink-0 u-tip u-tip-right" data-tip="Approve AND release the original into the conversation:\nsubsequent turns can see and discuss it (clearly labelled as your decision),\nand its values stop being re-redacted in that session.\nForward-only — the sealed turn is never rewritten."
                    style="color: var(--trust-pass, #34d399); border: 1px solid var(--trust-pass, #34d399); background: rgba(52,211,153,0.08);">Approve + release</button>` : ''}
                <button data-agent-action="review-reject" data-review-id="${esc(r.id)}"
                    class="text-[11px] px-2 py-0.5 rounded flex-shrink-0 u-tip u-tip-right" data-tip="The withholding stands."
                    style="color: var(--text-muted); border: 1px solid var(--border-secondary); background: transparent;">Uphold</button>
            </div>
            <details class="mt-1 ml-6"><summary class="text-[10px] cursor-pointer" style="color: var(--text-muted);">View the withheld original</summary>
                <div class="text-[11px] mt-1 p-2 rounded whitespace-pre-wrap" style="color: var(--text-primary); background: var(--bg-overlay, rgba(127,127,127,0.06)); border: 1px solid var(--border-secondary); max-height: 160px; overflow-y: auto;">${esc(r.original_text || '(content unavailable)')}</div>
            </details></div>`;
    }

    /** A DECIDED review, settled: what was decided, by whom, when — and for a parked write,
     *  its result (the receipt). Read-only: a decision is final. */
    function _decidedRow(r) {
        const isAct = r.source === 'action';
        const isPlat = r.source === 'platform';
        const what = isAct ? `a write — ${esc((r.categories || []).join(', ') || 'action')}`
            : isPlat ? `a platform change — ${esc((r.categories || []).join(', ') || 'change')}` : 'a withheld answer';
        const word = (isAct || isPlat)
            ? (r.status === 'approved' ? `approved — ${r.note || 'performed'}` : `rejected — nothing was ${isPlat ? 'changed' : 'written'}`)
            : (r.status === 'approved' ? (r.released_to_context ? 'approved and released to the conversation' : 'approved (record only)') : 'withholding upheld');
        const when = r.decided_at ? new Date(r.decided_at).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '';
        return `<div class="ap-review-item py-1.5 border-b" style="border-color: var(--border-secondary);">
            <div class="text-xs" style="color: var(--text-primary);">${what}${r.profile_tag ? ` · <span style="color: var(--text-muted);">@${esc(r.profile_tag)}</span>` : ''}</div>
            <div class="text-[11px]" style="color: ${r.status === 'approved' ? 'var(--trust-pass, #34d399)' : 'var(--text-muted)'};">${esc(word)}</div>
            <div class="text-[10px]" style="color: var(--text-muted);">${r.decided_by_label ? `decided by ${esc(r.decided_by_label)}` : 'decided'}${when ? ` · ${esc(when)}` : ''}${r.owner && r.owner !== r.decided_by_label ? ` · for ${esc(r.owner)}'s agent` : ''}</div>
        </div>`;
    }

    async function renderAssuranceInbox() {
        let data = null, reviews = null, retainedItems = [], decidedItems = [], questions = null;
        try {
            let all = null;
            [data, reviews, all, questions] = await Promise.all([
                api('/assurance/inbox'),
                api('/assurance/reviews?include_content=true').catch(() => null),
                // decided reviews are SETTLED work: folded with the other settled items, never
                // dropped — an approval vanishing from the Inbox read as if it never happened
                api('/assurance/reviews?status=all&limit=50').catch(() => null),
                // PROP-011: the questions agents put to this person — the other kind of "needs you"
                api('/input-requests?status=pending').catch(() => null),
            ]);
            decidedItems = ((all && all.items) || []).filter((r) => r.status === 'approved' || r.status === 'rejected');
            // The recovery ledger (enforce-parked full withholdings). Fetched
            // separately and NEVER folded into the badge, the per-agent open
            // counts, or the REVIEWED station — retained ≠ owed work. Absence
            // degrades to an empty ledger, silently.
            if (reviews && reviews.retained) {
                const rr = await api('/assurance/reviews?status=retained&include_content=true')
                    .catch(() => null);
                retainedItems = (rr && rr.items) || [];
            }
        } catch (e) {
            // TC-40: zero the module counters on failure. They are otherwise reset only AFTER a
            // successful fetch, so a transient failure left the REVIEWED station and the tab
            // badges repainting the PREVIOUS load's (possibly other-scope) counts — attributing
            // a stale per-agent count to the wrong agents.
            _inboxBadgeCount = 0; _inboxDerivedCount = 0; _inboxDerivedDanger = 0;
            _inboxDerivedTop = ''; _inboxByTag = {}; _inboxUnattributed = 0;
            return '';
        }
        // PROP-002b — the served per-station reporting levels govern every consumer
        // below (badge already server-filtered; here: default view + colour fold).
        _stationReporting = data.station_reporting || {};
        const stored = data.stored || [];
        const derived = data.derived || [];
        const items = [...stored, ...derived];
        const rank = { danger: 0, warning: 1, info: 2 };
        items.sort((a, b) => (rank[a.severity] ?? 3) - (rank[b.severity] ?? 3));
        // Presentation, never creation: items below their station's reporting
        // level leave the DEFAULT view (and never fold/badge) but stay one click
        // away — a quiet inbox must never silently mean "filtered".
        const surfaced = items.filter((e) => _sevSurfaces(e.severity, _stationOfEvent(e.event_type)));
        const filtered = items.filter((e) => !_sevSurfaces(e.severity, _stationOfEvent(e.event_type)));
        const pendingReviewsAll = (reviews && reviews.items) || [];
        // Agent VIEW filter (operator-requested): one pill per agent with items,
        // wearing its IFOC class colour. Composes with the severity filter.
        const byAgent = (arr) => _inboxAgentFilter
            ? arr.filter((e) => (e.profile_tag || '') === _inboxAgentFilter) : arr;
        const pendingReviews = byAgent(pendingReviewsAll);
        const _owedReviews = pendingReviewsAll.filter((r) => r.can_decide !== false).length;
        // Severity VIEW filter: pills in the construct's own words. A pending
        // review demands a decision, so it rides the danger bucket.
        const bySev = (arr) => _inboxSevFilter ? arr.filter((e) => e.severity === _inboxSevFilter) : arr;
        // SETTLED items leave the queue. An acknowledged notice or a decided item is
        // finished work, and a triage queue that mixes it with owed work makes the
        // operator re-read the same rows every visit to find the two that matter.
        //
        // They are folded, never dropped — the disposition is the record that the act
        // happened, and the row keeps its route (the CONDITION can outlive the notice;
        // that state lives on the ring, which is why folding the notice is safe).
        const _isSettled = (e) => !!(e && (e.acknowledged || e.disposition_label || e.is_receipt));
        const _openOf = (arr) => arr.filter((e) => !_isSettled(e));
        const shownSurfaced = _openOf(bySev(byAgent(surfaced)));
        const shownFiltered = bySev(byAgent(filtered));
        const shownSettled = bySev(byAgent(surfaced)).filter(_isSettled);
        const shownDecided = byAgent(decidedItems);
        const showReviews = !_inboxSevFilter || _inboxSevFilter === 'danger';
        const sevCount = (sev) => byAgent(items).filter((e) => e.severity === sev).length
            + (sev === 'danger' ? pendingReviews.filter((r) => r.can_decide !== false).length : 0);
        // One pill per agent holding items (reviews included), IFOC-coloured.
        const agentCounts = {};
        [...pendingReviewsAll.filter((r) => !r.owner), ...items].forEach((e) => {
            const t = e.profile_tag || '';
            if (t) agentCounts[t] = (agentCounts[t] || 0) + 1;
        });
        const agentPill = (tag, label, color) => {
            const on = (_inboxAgentFilter || '') === tag;
            // The agent pill keeps its IFOC colour when active (the roster's own coding);
            // the shared .ai-pill supplies every other property so the two pill families
            // in one bar cannot drift apart in size, radius or focus ring.
            return `<button data-agent-action="inbox-agent-filter" data-tag="${esc(tag)}"
                class="ai-pill" style="${on ? `border-color:${color};color:${color};background:color-mix(in srgb, ${color} 10%, transparent);` : ''}">${label}</button>`;
        };
        // ONE bar: severity segments, then the agents, separated by a hairline. The
        // "Agent:" / "Show:" prefixes were furniture — the pills say what they are, and
        // two labelled rows made a triage queue read like a debug panel.
        const agentBar = Object.keys(agentCounts).length
            ? `<span class="ai-bar-sep"></span>${agentPill('', 'all agents', 'var(--teradata-orange)')}
               ${Object.entries(agentCounts).sort((a, b) => b[1] - a[1])
                    .map(([t, n]) => agentPill(t, `@${esc(t)} (${n})`, _agentIfocColor(t))).join('')}`
            : '';
        const sevPill = (val, label) => {
            const on = (!val && !_inboxSevFilter) || _inboxSevFilter === val;
            return `<button data-agent-action="inbox-sev-filter" data-sev="${val}"
                class="ai-pill${on ? ' ai-pill-on' : ''}">${label}</button>`;
        };
        const sevBar = `<div class="ai-bar">
            ${sevPill('', 'everything')}
            ${sevPill('danger', `needs you now (${sevCount('danger')})`)}
            ${sevPill('warning', `worth a look (${sevCount('warning')})`)}
            ${sevPill('info', `for the record (${sevCount('info')})`)}
            ${agentBar}
        </div>`;
        // A parked ACTION is never bulk-approved: approving one PERFORMS a write, which is a
        // per-item decision made against its own diff — "approve all shown" stays record-only.
        _shownReviewIds = (showReviews ? pendingReviews : []).filter((r) => r.source !== 'action')
            .map((r) => r.id).filter(Boolean);
        // Bulk disposition (operator-requested) — scoped to the FILTERED view, so
        // "approve all" means exactly the rows on screen (e.g. one agent's). Bulk
        // approve is RECORD-ONLY by design: releasing into the conversation is a
        // per-item human judgement and is deliberately not bulkable.
        // the bar acts on _shownReviewIds only (a write is never bulk-approved), so it is
        // offered — and counts — exactly those items
        const bulkBar = (showReviews && _shownReviewIds.length > 1)
            ? `<div class="flex items-center gap-2 mb-1.5">
                 <span class="text-[10px]" style="color: var(--text-muted);">${_shownReviewIds.length} shown${_inboxAgentFilter ? ` for @${esc(_inboxAgentFilter)}` : ''}${pendingReviews.length > _shownReviewIds.length ? ' (parked writes are decided one by one)' : ''}:</span>
                 <button data-agent-action="review-approve-all"
                     class="text-[11px] px-2 py-0.5 rounded u-tip u-tip-right"
                     data-tip="Approve (record only) every review item currently shown under the active filters.\nReleasing into the conversation stays a per-item decision."
                     style="color: var(--trust-pass, #34d399); border: 1px solid var(--trust-pass, #34d399); background: transparent;">Approve all shown</button>
                 <button data-agent-action="review-uphold-all"
                     class="text-[11px] px-2 py-0.5 rounded u-tip u-tip-right"
                     data-tip="Uphold the withholding on every review item currently shown under the active filters."
                     style="color: var(--text-muted); border: 1px solid var(--border-secondary); background: transparent;">Uphold all shown</button>
               </div>` : '';
        const reviewBlock = (showReviews && pendingReviews.length)
            ? bulkBar + `<div class="mb-2">${pendingReviews.map(_reviewRow).join('')}</div>` : '';
        // PROP-011: a question is the other thing a person is owed — listed above the approvals,
        // answered in place (the needs_input card, over REST) or in its conversation.
        const pendingQuestions = ((questions && questions.items) || []).filter((q) => !_inboxAgentFilter || (q.profile_tag || '') === _inboxAgentFilter);
        const questionBlock = pendingQuestions.length
            ? `<div class="mb-2" id="ai-questions" data-count="${pendingQuestions.length}">${pendingQuestions.map((q) => `<div class="ai-question" data-question='${JSON.stringify(q).replace(/'/g, '&#39;')}'></div>`).join('')}</div>`
            : '';
        const settledBlock = (shownSettled.length || shownDecided.length)
            ? `<details class="ai-fold"><summary class="u-tip" data-tip="Acknowledged notices and decided items. They stay reachable — and keep\ntheir route, because a condition can outlive the notice about it.">${
                 shownSettled.length + shownDecided.length} settled</summary>
                 <div style="max-height: 220px; overflow-y: auto;">${shownDecided.map(_decidedRow).join('')}${
                     _groupInbox(shownSettled).map(_inboxRow).join('')}</div></details>`
            : '';
        const filteredBlock = shownFiltered.length
            ? `<details class="ai-fold"><summary>${shownFiltered.length} recorded below the reporting level</summary>
                 <div style="max-height: 200px; overflow-y: auto;">${_groupInbox(shownFiltered).map(_inboxRow).join('')}</div></details>`
            : '';
        // The recovery ledger, collapsed and quiet: it fulfils the delivered-text
        // promise ("recoverable from the Assurance Inbox") without ever wearing the
        // owed-work costume — no badge, no severity fold, no station mark.
        const retainedShown = _inboxAgentFilter
            ? retainedItems.filter((r) => (r.profile_tag || '') === _inboxAgentFilter)
            : retainedItems;
        const retainedBlock = retainedShown.length
            ? `<details class="ai-fold"><summary class="u-tip" data-tip="Full withholdings under plain Enforce keep their original as a recoverable\nentry — nothing owed, nothing counted as open work. Aged entries are pruned\nautomatically." >${retainedShown.length} recoverable original${retainedShown.length === 1 ? '' : 's'} retained (no action owed)</summary>
                 <div style="max-height: 200px; overflow-y: auto;">${retainedShown.map(_reviewRow).join('')}</div></details>`
            : '';
        const body = sevBar + questionBlock + ((shownSurfaced.length || (showReviews && pendingReviews.length) || shownFiltered.length)
            ? reviewBlock + `<div class="ai-list" style="max-height: 340px; overflow-y: auto;">${_groupInbox(shownSurfaced).map(_inboxRow).join('')}</div>` + settledBlock + filteredBlock + retainedBlock
            : (pendingQuestions.length ? '' : `<div class="ai-empty">${_inboxSevFilter ? 'Nothing at this severity.' : 'No open trust incidents — every layer is quiet.'}</div>`) + settledBlock + retainedBlock);
        const badges = [
            // only what THIS person decides is owed by them; a write awaiting a designated
            // approver is listed, never counted against its owner (PROP-008 §5.4a)
            _owedReviews ? `<span class="trust-chip tc-warn">${_owedReviews} awaiting review</span>` : '',
            data.unacked ? `<span class="trust-chip tc-warn">${data.unacked} unacknowledged</span>` : '',
            // a question waiting is a reading, not an alarm (the trust-status contract): the pass chip
            (questions && questions.pending) ? `<span class="trust-chip tc-pass">${questions.pending} waiting for you</span>` : '',
        ].join('');
        // Server-authoritative counts (PROP-002): the reviews endpoint returns its own
        // `pending` total, and `data.unacked` is the store's count — never a
        // client-side length over a capped list, which could disagree with both.
        _inboxBadgeCount = (reviews && typeof reviews.pending === 'number'
            ? reviews.pending : _owedReviews) + (data.unacked || 0) + ((questions && questions.pending) || 0);
        // Attribute the same items to agents. Counted here rather than re-derived by the
        // station so the focused reading can never disagree with the estate badge above
        // it: one pass, one source. An item with no tag is estate-level (a session
        // sentinel, say) and belongs to no agent — tallied separately, never dropped.
        _inboxByTag = {}; _inboxUnattributed = 0;
        const _bucket = (tag) => {
            if (!tag) { _inboxUnattributed++; return null; }
            return (_inboxByTag[tag] = _inboxByTag[tag]
                || { open: 0, derived: 0, derivedDanger: 0, top: '' });
        };
        // PROP-002b — the colour fold: an OPEN item that surfaces under its
        // station's reporting level drives that station's colour (danger → red,
        // warning → amber) until acked/decided. One accumulation, one source —
        // the ring reads `openItems`, never re-derives.
        const _foldOpen = (b, e) => {
            if (!b || !e) return;
            const st = _stationOfEvent(e.event_type);
            if (!_sevSurfaces(e.severity, st)) return;
            const s = e.severity === 'danger' ? 'danger'
                : (e.severity === 'warning' ? 'warn' : null);
            if (!s) return;
            b.openItems = b.openItems || {};
            const cur = b.openItems[st] || { state: s, count: 0 };
            cur.count += 1;
            if (s === 'danger') cur.state = 'danger';
            b.openItems[st] = cur;
        };
        // Attribution reads the UNFILTERED populations — the pills are a VIEW,
        // and a view filter must never change the ring's dots, fold or badges.
        // a write from ANOTHER person's agent (r.owner) awaiting this approver is not one of
        // this person's agents — a shared tag must never fold it into their own station
        pendingReviewsAll.forEach((r) => { if (r.owner) return; const b = _bucket(r.profile_tag); if (b) b.open++; });
        // Only UNACKNOWLEDGED, NON-RECEIPT stored incidents count toward the open badge —
        // the exact predicate the estate count uses (`unacked_count` excludes receipts of
        // an agent's own acts, Rule 11.2). Counting a receipt made the focused REVIEWED
        // reading disagree with the estate badge above it AND lit the owed-dot for an act
        // that is a RECORD, never owed — and a receipt can't be acked from the UI, so the
        // over-count was permanent.
        stored.filter((e) => e && !e.acknowledged && !e.is_receipt)
            .forEach((e) => {
                const b = _bucket(e.profile_tag);
                if (b) { b.open++; _foldOpen(b, e); }
            });
        derived.forEach((d) => {
            const b = _bucket(d.profile_tag);
            if (!b) return;
            b.derived++;
            if (d.severity === 'danger') b.derivedDanger++;
            if (!b.top || (d.severity === 'danger' && b.derivedDanger === 1)) {
                b.top = `${_sevWord(d.severity)}: ${d.summary || d.event_type || 'observation'}`;
            }
            // Station attribution for the ring's acted dots (PROP-002) — a
            // gate ACTED on one of this agent's recent turns.
            const st = _stationOfEvent(d.event_type);
            b.stations = b.stations || {};
            b.stations[st] = (b.stations[st] || 0) + 1;
            // …and the colour fold, for observations still awaiting the owner.
            if (!d.acknowledged && !d.disposition_label) _foldOpen(b, d);
        });
        // Derived observations: not counted in the badge, but surfaced verbatim by the
        // REVIEWED station so a zero count can never read as "nothing happened".
        _inboxDerivedCount = derived.length;
        _inboxDerivedDanger = derived.filter((d) => d.severity === 'danger').length;
        const _topDerived = derived.find((d) => d.severity === 'danger') || derived[0] || null;
        _inboxDerivedTop = _topDerived
            ? `${_sevWord(_topDerived.severity)}: ${_topDerived.summary || _topDerived.event_type || 'observation'}` : '';
        return sectionCard('Assurance Inbox', INBOX_ICON, body, badges);
    }

    // #55 — a disposition must SAY what it did. This awaited the POST and removed the
    // row: no banner, no explanation, no route — while its sibling
    // `quarantineDocFromInbox` twenty lines below confirms every time. Release is the
    // one that most needs it: it is FORWARD-ONLY by design (the sealed turn is never
    // rewritten), so an owner who releases and then looks for the text in the
    // conversation will never find it. The pre-click tooltip explains that; a tooltip
    // read before an act is not feedback after it.
    const _DECISION_NOTE = {
        release: 'Released — the delivered turn stays as it was sent; from the next turn on '
               + 'this agent can see and state it. Open "Why trust this answer?" on that '
               + 'message to read the original.',
        approve: 'Recorded — you have seen the withheld original and accepted it. The '
               + 'conversation is unchanged.',
        reject: 'Upheld — the withholding stands, and your decision is recorded on that turn.',
    };

    async function decideReview(reviewId, decision, btn, release) {
        try {
            const body = release ? JSON.stringify({ release_to_conversation: true }) : '{}';
            const res = await api(`/assurance/reviews/${encodeURIComponent(reviewId)}/${decision}`, { method: 'POST', body });
            const ar = res && res.action_record;
            const note = ar
                ? `Performed — ${ar.rows_affected || 0} row${Number(ar.rows_affected) === 1 ? '' : 's'} · receipt ${String(ar.invocation_hash || '').slice(0, 12)}. The answer that asked for it now reads Done.`
                : _DECISION_NOTE[release ? 'release' : decision];
            if (note && window.showAppBanner) window.showAppBanner(note, 'success', 9000);
            // the decision is a LOCAL fact: the answer that asked for it reads it NOW, in this
            // tab — never only after the server's broadcast round trip (Act II: the parked card
            // read "parked" until a page reload, although this banner said it now reads Done)
            try {
                UderiaBus.emit('uderia:review-decision', {
                    review_id: reviewId, decision: decision === 'approve' ? 'approved' : 'rejected',
                    source: ar ? 'action' : (res && res.source) || undefined,
                    session_id: (ar && ar.session_id) || (res && res.session_id) || undefined });
            } catch (_) { /* the broadcast that follows is the second pass */ }
            // Removing the row by hand left every other reader of the fact (badges,
            // REVIEWED on the ring, the mini rings) waiting on the notification round
            // trip. The decision is a LOCAL fact: repaint through the ONE seam now; the
            // broadcast that follows is a harmless second pass.
            await refreshInbox();
        } catch (e) {
            console.warn('review decision failed', e);
            // A refused release (an inbound payload — see #51) returns a real reason;
            // failing silently would leave the owner believing it worked.
            const msg = (e && e.message) || 'Could not record that decision.';
            if (window.showAppBanner) window.showAppBanner(msg, 'error', 9000);
        }
    }

    /** Re-render the inbox after an action. ONE path, because a removed row is a lie:
     *  the badges, the severity counts and the agent pills all move with a disposition,
     *  and DOM surgery left every one of them stale.
     *
     *  It also fixes the class of bug that made every button look dead: the handlers
     *  removed their row with `closest('div.flex.items-start')` — the OLD row's utility
     *  classes. The redesign renamed the row, so the POST fired, the server recorded it,
     *  and nothing on screen moved. Selecting a row by its layout classes was the defect;
     *  refreshing from the source cannot rot the same way.
     */
    /** THE post-disposition seam. An acknowledgment, a review decision, a quarantine
     *  from the inbox — each is a LOCAL act that moves ONE fact (the open-item model
     *  `renderAssuranceInbox()` recomputes into `_inboxByTag`), and that fact has
     *  several readers: the inbox list, the tab badges, the Trust Circle (REVIEWED's
     *  count and the open-item colour fold on every station, Rule 11.5) and the
     *  roster/tree mini rings. Before this seam existed, only the first two were
     *  repainted, so the ring kept the pre-ack state until a page refresh — the
     *  "one section updated, the other consumers kept their cached model" shape.
     *  Every disposition path routes here; nothing else re-renders the inbox. */
    async function refreshInbox() {
        try {
            const html = await renderAssuranceInbox();
            const w = $('assurance-inbox-wrap');
            if (w && html) w.innerHTML = html;
            await _attachInboxQuestions(w);   // PROP-011: the pending questions' cards
            updateTabBadges();
            _repaintInboxConsumers();
        } catch (e) { console.warn('inbox refresh failed', e); }
    }

    /** PROP-011: the Inbox's pending questions are the needs_input component's own cards,
     *  answered over REST from here (the turn resumes in its conversation). The component
     *  may be disabled: the rows then stay text, readable in their conversation. */
    async function _attachInboxQuestions(w) {
        try {
            const hosts = w ? Array.from(w.querySelectorAll('.ai-question[data-question]')) : [];
            if (!hosts.length) return;
            const m = await import('/api/v1/components/needs_input/renderer');
            for (const h of hosts) { try { m.attach(h, JSON.parse(h.dataset.question), { host: h, via: 'rest' }); } catch (_) { /* one bad row never hides the rest */ } }
        } catch (_) { /* no component, no cards */ }
    }

    /** Repaint every reader of the inbox model IN PLACE (never a page reload, never a
     *  full load() — the disposition moved one fact, not the execution aggregates).
     *  The ring: update() keeps pin, lens and focus. The mini rings: each host is
     *  replaced by a fresh derivation at its own size, so the roster card and the
     *  coordination tree can never disagree with the ring beside them. Click
     *  handling is delegated at the root, so a replaced node keeps working. */
    function _repaintInboxConsumers() {
        if (!window.TrustCircle || !_model) return;
        const host = $('trust-circle-host');
        if (host && host.querySelector('.tc-svg')) {
            try { window.TrustCircle.update(_buildCircleModel()); }
            catch (e) { console.warn('[TrustCircle] repaint after disposition failed', e); }
        }
        const r = root();
        if (!r) return;
        r.querySelectorAll('.tc-mini-host[data-circle-key]').forEach((el) => {
            const key = el.dataset.circleKey;
            const a = (_model.agents || []).find((x) => (x.key || x.tag) === key && !x.kind && x.isSelf !== false);
            if (!a) return;   // an unseen agent keeps its honest absence
            const svg = el.querySelector('svg');
            const size = (svg && parseInt(svg.getAttribute('width'), 10)) || 16;
            const tmp = document.createElement('span');
            tmp.innerHTML = _stationRing(a.tag, size);
            if (tmp.firstElementChild) el.replaceWith(tmp.firstElementChild);
        });
    }

    async function quarantineDocFromInbox(docId, btn) {
        // Wave 2 (U7): one click → quarantine + ack + live update everywhere (the
        // endpoint records the trust event and broadcasts; the inbox refreshes in
        // place via the standing uderia:trust-event listener).
        try {
            await api(`/knowledge/documents/${encodeURIComponent(docId)}/quarantine`,
                      { method: 'POST', body: JSON.stringify({ reason: 'quarantined from the Assurance Inbox' }) });
            // The document is in the grouping signature, so one quarantine covers every
            // occurrence — acknowledge them all rather than leaving N-1 open against a
            // document that is already excluded from retrieval.
            try { await ackInboxBatch(btn, false); } catch (e) { /* the quarantine itself stands */ }
            if (window.showAppBanner) window.showAppBanner('Document quarantined — excluded from retrieval (reversible in the repository manager).', 'success', 6000);
            await refreshInbox();
        } catch (e) {
            console.warn('quarantine failed', e);
            if (window.showAppBanner) window.showAppBanner('Quarantine failed — see console.', 'error', 6000);
        }
    }

    /** Acknowledge one occurrence or every occurrence a row collapsed.
     *
     *  ONE executor for both, because the collapsed row and an expanded occurrence hand
     *  it the same list — a group-only path would be the branch that drifts, and the
     *  single-item case is the far better exercised of the two.
     *
     *  Sequential, so each item gets its own audit record, and the tally is honest: a
     *  partial failure says so rather than reporting success (the bulk-review path's
     *  rule, applied here).
     */
    async function ackInboxBatch(btn, refresh = true) {
        let batch = [];
        try { batch = JSON.parse(btn.dataset.batch || '[]'); } catch (_) { batch = []; }
        if (!batch.length) return;
        let ok = 0;
        for (const it of batch) {
            try {
                if (it.id) {
                    await api(`/assurance/inbox/${encodeURIComponent(it.id)}/ack`, { method: 'POST' });
                } else {
                    await api('/assurance/inbox/derived/ack', {
                        method: 'POST',
                        body: JSON.stringify({ session_id: it.session_id, turn_id: it.turn_id,
                                               event_type: it.event_type }),
                    });
                }
                ok++;
            } catch (e) { console.warn('inbox ack failed', e); }
        }
        if (ok < batch.length && window.showAppBanner) {
            window.showAppBanner(`Acknowledged ${ok} of ${batch.length} — see console.`, 'error', 6000);
        }
        if (refresh) await refreshInbox();
    }

    /** Bulk-disposition the review items currently shown under the active filters.
     *  Two-click arm (never a native confirm), sequential POSTs so each item gets
     *  its own audit event, honest tally banner — a partial failure is reported,
     *  never summarised as success. Approve is record-only; release is
     *  deliberately not bulkable. */
    async function bulkDecideReviews(btn, decision) {
        const ids = _shownReviewIds.slice();
        if (!ids.length) return;
        if (btn.dataset.armed !== '1') {
            btn.dataset.armed = '1';
            btn.textContent = `Click again — ${decision === 'approve' ? 'approve' : 'uphold'} ${ids.length}`;
            setTimeout(() => {
                if (btn.isConnected) {
                    btn.dataset.armed = '';
                    btn.textContent = decision === 'approve' ? 'Approve all shown' : 'Uphold all shown';
                }
            }, 4000);
            return;
        }
        btn.disabled = true;
        btn.textContent = 'Working…';
        let ok = 0, failed = 0;
        for (const id of ids) {
            try {
                await api(`/assurance/reviews/${encodeURIComponent(id)}/${decision === 'approve' ? 'approve' : 'reject'}`,
                    { method: 'POST', body: JSON.stringify({}) });
                ok++;
            } catch (e) { failed++; }
        }
        const word = decision === 'approve' ? 'approved (record only)' : 'upheld';
        const msg = failed
            ? `${ok} of ${ids.length} ${word} — ${failed} failed (still pending; retry from the inbox).`
            : `${ok} withheld answer${ok === 1 ? '' : 's'} ${word}.`;
        if (window.showAppBanner) window.showAppBanner(msg, failed ? 'warning' : 'success', 8000);
        await refreshInbox();   // a bulk decision moves the same fact as a single one
    }

    /** PROP-002 (completeness invariant) — acknowledge a DERIVED observation or the
     *  session sentinel. The sealed trace is never rewritten; the ack is a stored
     *  disposition ABOUT it, keyed (session, turn, event_type). */


    // ── judge calibration + annotation queue ─────────────────────────────────────
    /** A dimension's display name — via the shared glossary when loaded, never a
     *  second hand-kept map (title-cased key as the honest fallback). */
    function _dimLabel(dim) {
        try {
            if (window.QualityDimensions && window.QualityDimensions.label) {
                return window.QualityDimensions.label(dim);
            }
        } catch (_) { /* fall through */ }
        return String(dim || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
    }

    /** The κ report: which judges agree with humans, over how many pairs.
     *  `calibrated` = ELIGIBLE for promotion to gating — promotion stays a
     *  deliberate act; this table flips nothing. */
    function _calibrationReport(cal) {
        if (!cal) {
            return `<div class="text-xs mb-3" style="color: var(--text-muted);">Calibration report unavailable.</div>`;
        }
        const dims = Object.entries(cal.dimensions || {});
        const head = `<div class="text-xs mb-2" style="color: var(--text-muted);">
            A judge is a model opining — it stays <span style="color: var(--text-primary);">advisory</span> until its agreement with humans clears
            <span class="u-tip" data-tip="Cohen's κ ≥ ${esc(String(cal.floor))} (substantial agreement, Landis &amp; Koch)\nover at least ${esc(String(cal.min_pairs))} (human, judge) pairs.\nBelow the pair floor κ is reported but never clears." style="border-bottom: 1px dotted var(--border-primary); color: var(--text-primary);">κ ≥ ${esc(String(cal.floor))} over ≥ ${esc(String(cal.min_pairs))} rulings</span>.
            Clearing makes it <em>eligible</em> for promotion to gating — nothing is promoted automatically.</div>`;
        if (!dims.length) {
            return head + `<div class="text-xs mb-3" style="color: var(--text-muted);">No (human, judge) pairs yet — rule on the verdicts below to start measuring.</div>`;
        }
        const chip = (r) => {
            if (r.status === 'calibrated') return `<span class="trust-chip tc-pass">calibrated · eligible</span>`;
            if (r.status === 'below_floor') return `<span class="trust-chip tc-warn">below the κ floor</span>`;
            return `<span class="trust-chip u-tip" data-tip="κ over a handful of pairs is noise — reported, never cleared.">${esc(String(r.n_decided))} of ${esc(String(r.min_pairs))} pairs</span>`;
        };
        const rows = dims.map(([dim, r]) => `<div class="jc-row">
            <span class="text-xs font-medium truncate" style="color: var(--text-primary);">${esc(_dimLabel(dim))} <span style="color: var(--text-muted);">·judge</span></span>
            <span class="text-xs" style="color: var(--text-primary);">${r.cohen_kappa == null ? '—' : 'κ ' + esc(String(r.cohen_kappa))}</span>
            <span class="text-xs" style="color: var(--text-muted);">${r.agreement == null ? '—' : Math.round(r.agreement * 100) + '% agree'}</span>
            <span class="text-xs" style="color: var(--text-muted);">${esc(String(r.n_decided))} pair(s)</span>
            <span>${chip(r)}</span></div>`).join('');
        return head + `<style>
            #agent-section-calibration .jc-row{display:grid;grid-template-columns:minmax(140px,1.4fr) 70px 90px 70px auto;gap:8px;align-items:center;padding:4px 2px;border-bottom:1px solid var(--border-secondary);}
            #agent-section-calibration .jc-row:last-child{border-bottom:none;}
        </style><div class="mb-3">${rows}</div>`;
    }

    /** The work queue: recent judge rulings, one click to agree/disagree.
     *  Each button freezes the pair (dimension + the ruling shown) on the POST. */
    function _judgeQueue(q) {
        const head = `<div class="text-xs font-medium mb-1 mt-3 pt-3" style="color: var(--text-primary); border-top: 1px solid var(--border-secondary);">Recent judge rulings
            <span class="font-normal" style="color: var(--text-muted);">— do you agree? Each ruling you decide becomes a κ pair.</span></div>`;
        if (!q) return head + `<div class="text-xs" style="color: var(--text-muted);">Judge verdicts unavailable.</div>`;
        const pending = q.verdicts || [];
        const settled = q.annotated_count || 0;
        if (!pending.length) {
            return head + `<div class="text-xs" style="color: var(--text-muted);">${settled
                ? `Queue clear — every recent judge ruling has a human verdict (${settled} settled).`
                : 'No judge rulings recorded yet — judges run on sampled or flagged turns.'}</div>`;
        }
        const rows = pending.map((v) => `<div class="jq-row">
            <span class="text-xs font-medium truncate" style="color: var(--text-primary);">${esc(_dimLabel(v.dimension))}</span>
            <span>${v.passed
                ? '<span class="trust-chip tc-pass">upheld</span>'
                : '<span class="trust-chip tc-danger">flagged</span>'}</span>
            <span class="text-xs truncate" style="color: var(--text-muted);">${v.agent ? '@' + esc(v.agent) : ''}${v.turn != null ? ' · turn ' + esc(String(v.turn)) : ''}</span>
            <span class="text-xs" style="color: var(--text-muted);">${v.ts ? new Date(v.ts * 1000).toLocaleDateString() : ''}</span>
            ${v.session_id ? `<span class="ap-link text-xs" data-ap-open-session="${esc(v.session_id)}" data-ap-turn="${esc(String(v.turn || 1))}">open</span>` : '<span></span>'}
            <span class="flex items-center gap-1">
                <button data-agent-action="judge-agree" data-dim="${esc(v.dimension)}" data-passed="${v.passed ? '1' : '0'}" data-sid="${esc(v.session_id || '')}" data-turn="${esc(String(v.turn ?? ''))}"
                    class="text-xs px-2 py-0.5 rounded u-tip" data-tip="The judge's ruling matches your read of this turn." style="color: var(--trust-pass); border: 1px solid color-mix(in srgb, var(--trust-pass) 33%, transparent); background: transparent;">Agree</button>
                <button data-agent-action="judge-disagree" data-dim="${esc(v.dimension)}" data-passed="${v.passed ? '1' : '0'}" data-sid="${esc(v.session_id || '')}" data-turn="${esc(String(v.turn ?? ''))}"
                    class="text-xs px-2 py-0.5 rounded u-tip" data-tip="The judge got this one wrong." style="color: var(--trust-danger); border: 1px solid color-mix(in srgb, var(--trust-danger) 33%, transparent); background: transparent;">Disagree</button>
            </span></div>`).join('');
        return head + `<style>
            #agent-section-calibration .jq-row{display:grid;grid-template-columns:minmax(130px,1.3fr) 70px minmax(90px,1fr) 70px 40px auto;gap:8px;align-items:center;padding:4px 2px;border-bottom:1px solid var(--border-secondary);}
            #agent-section-calibration .jq-row:last-child{border-bottom:none;}
        </style><div style="max-height: 220px; overflow-y: auto;">${rows}</div>
        ${settled ? `<div class="text-[10px] mt-1" style="color: var(--text-muted);">${settled} ruling(s) already decided.</div>` : ''}`;
    }

    async function renderAnnotations() {
        const [annsResp, cal, queue] = await Promise.all([
            api('/evals/annotations?limit=40').catch(() => null),
            api('/evals/judge-calibration').catch(() => null),
            api('/evals/judge-queue?limit=20').catch(() => null),
        ]);
        const anns = (annsResp && annsResp.annotations) || [];
        const rows = anns.map((a) => `<div class="flex items-center justify-between py-1 border-b" style="border-color: var(--border-secondary);">
            <span class="text-xs" style="color: var(--text-primary);">${pill(esc(a.label), a.label === 'agree' ? 'var(--trust-pass)' : 'var(--trust-danger)')}
                ${a.dimension ? `<span style="color: var(--text-muted);">${esc(_dimLabel(a.dimension))} ·</span> ` : ''}${esc(a.case_id || a.session_id || '')}</span>
            <span class="text-xs truncate ml-2" style="color: var(--text-muted); max-width: 50%;">${esc(a.note || '')} · ${esc(a.reviewer || '')}</span>
        </div>`).join('') || `<div class="text-xs" style="color: var(--text-muted);">No annotations yet.</div>`;
        const inputStyle = 'background: var(--card-bg); color: var(--text-primary); border: 1px solid var(--border-secondary);';
        const form = `<div class="text-xs font-medium mb-1 mt-3 pt-3" style="color: var(--text-primary); border-top: 1px solid var(--border-secondary);">Annotations
            <span class="font-normal" style="color: var(--text-muted);">— the record behind the κ table (manual entries carry no judge ruling, so they list here without joining a pair)</span></div>
        <div style="max-height: 160px; overflow-y: auto;">${rows}</div>
        <div class="flex items-center gap-2 mt-3 flex-wrap">
            <input id="agent-ann-case" placeholder="case / turn id" class="text-xs rounded px-2 py-1" style="${inputStyle} width:160px;">
            <input id="agent-ann-note" placeholder="note (why)" class="text-xs rounded px-2 py-1 flex-1" style="${inputStyle}">
            <button data-agent-action="ann-agree" class="text-xs px-2 py-1 rounded" style="color: var(--trust-pass); border: 1px solid color-mix(in srgb, var(--trust-pass) 33%, transparent);">Agree</button>
            <button data-agent-action="ann-disagree" class="text-xs px-2 py-1 rounded" style="color: var(--trust-danger); border: 1px solid color-mix(in srgb, var(--trust-danger) 33%, transparent);">Disagree</button>
        </div>`;
        return sectionCard('Judge Calibration & Annotation Queue', 'M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z',
            _calibrationReport(cal) + _judgeQueue(queue) + form);
    }

    /** Re-render ONLY the calibration section — an annotation must not cost a
     *  full Agents-view reload. */
    async function refreshCalibration() {
        const host = $('agent-section-calibration');
        if (host) host.innerHTML = await renderAnnotations();
    }

    async function addAnnotation(label) {
        const caseId = ($('agent-ann-case') || {}).value || null;
        const note = ($('agent-ann-note') || {}).value || '';
        try { await api('/evals/annotations', { method: 'POST', body: JSON.stringify({ label, note, case_id: caseId }) }); await refreshCalibration(); }
        catch (e) { console.warn('annotation failed', e); }
    }

    /** One-click ruling from the judge queue — freezes the (dimension, ruling)
     *  pair the human saw onto the annotation, which is what makes it κ-usable. */
    async function ruleOnJudge(btn, label) {
        const d = btn.dataset;
        btn.disabled = true;
        try {
            await api('/evals/annotations', { method: 'POST', body: JSON.stringify({
                label,
                dimension: d.dim,
                judge_passed: d.passed === '1',
                session_id: d.sid || null,
                turn: d.turn !== '' ? Number(d.turn) : null,
            }) });
            await refreshCalibration();
        } catch (e) {
            btn.disabled = false;
            console.warn('judge ruling failed', e);
            if (window.showAppBanner) window.showAppBanner('Could not record the ruling: ' + (e.message || e), 'warning', 6000);
        }
    }

    // ── orchestration ────────────────────────────────────────────────────────────
    let _wired = false;
    function wire() {
        if (_wired) return; _wired = true;
        const refresh = $('agent-refresh-btn');
        if (refresh) refresh.addEventListener('click', () => load());
        // Context-sensitive KPI tiles + cross-agent charts: each portfolio KPI
        // in the command-center band, and each analytics chart below it, jumps to
        // the lower-pane section that details it (Evaluated / Healthy / Needs
        // Attention → Roster, Live Turns → Posture, Gating Failures → Inbox; the
        // Quality/Failing/Scoreboard/Cost charts → Roster). Keyboard-accessible.
        const cc = $('agent-command-center');
        if (cc) {
            const gotoFromKpi = (ev) => {
                const tile = ev.target.closest('[data-goto-section]');
                if (!tile || !cc.contains(tile)) return;
                if (ev.type === 'keydown') {
                    if (ev.key !== 'Enter' && ev.key !== ' ') return;
                    ev.preventDefault();
                }
                showSection(tile.dataset.gotoSection);
            };
            cc.addEventListener('click', gotoFromKpi);
            cc.addEventListener('keydown', gotoFromKpi);
        }
        // The Trust Circle breathes on REAL events: a completed turn laps the
        // ring; a trust event flashes its station. Guarded no-ops when the
        // circle isn't mounted — safe to listen globally.
        UderiaBus.on('uderia:turn-complete', () => {
            if (!window.TrustCircle) return;
            window.TrustCircle.pulse('turn');
            window.TrustCircle.noteEvent('a turn travelled the gates');
        });
        UderiaBus.on('uderia:trust-event', (ev) => {
            if (!window.TrustCircle) return;
            const d = (ev && ev.detail) || {};
            const t = String(d.event_type || d.type || '');
            const msg = d.message || d.summary || 'trust event recorded';
            // ONE event→station mapping (`_stationOfEvent`) — the same one the inbox
            // uses to attribute derived observations, so the live pulse and the II
            // dots can never route one event to two different stations.
            const station = _stationOfEvent(t);
            window.TrustCircle.pulse(station);
            window.TrustCircle.noteEvent(msg);
            // A pulse is not a reading. A trust event means the state the ring is
            // showing may have moved (a quarantine flips a signed corpus to changed;
            // an acknowledgment clears a sign blocker) — re-fetch the trust payload
            // and repaint IN PLACE (update() keeps the pin), or the ring animates
            // over stale facts. Debounced: a burst of events yields one refetch.
            clearTimeout(_circleTrustT);
            _circleTrustT = setTimeout(_refreshCircleTrust, 600);
        });

        // Searchable user focus (system scope, admin): the picker lists every
        // user seen in the span window — from the lens's own data, no directory.
        // TC-93: resolve window.UserScopePicker at CLICK time, not wire() time — wire()
        // runs once, so a picker script that loaded a beat later left the admin focus
        // button permanently dead with no error.
        const userBtn = $('agent-user-filter');
        if (userBtn) {
            userBtn.addEventListener('click', () => {
                if (!window.UserScopePicker) return;
                window.UserScopePicker.open({
                    anchor: userBtn,
                    users: _usersMap,
                    current: _scopeUser || '',
                    onSelect: (uuid) => { _scopeUser = uuid || null; _updateUserFilterButton(); load(); },
                });
            });
        }
        // Topic tabs (header navigation) + restore the last-viewed section.
        const tabs = $('agent-section-tabs');
        if (tabs) tabs.addEventListener('click', (ev) => {
            const b = ev.target.closest('.agent-section-tab');
            if (b) showSection(b.dataset.section);
        });
        // First open of the Agents view always lands on the Trust Circle (the
        // flagship surface); within the running session, section state lives in
        // memory only — switching views and back keeps your place, a fresh app
        // load starts at the circle again.
        showSection('circle');
        // Live trust events (breaker trip, memory quarantine, retrieval defuse,
        // review decision) refresh the Assurance Inbox IN PLACE while the Agents
        // view is open — the same no-stale-cache root-cause fix the answer
        // capsule got. Debounced: a burst of events yields one refetch.
        let _inboxRefreshT = null;
        UderiaBus.on('uderia:trust-event', () => {
            const wrap = $('assurance-inbox-wrap');
            if (!wrap) return;   // Agents view not rendered — load() will fetch fresh
            clearTimeout(_inboxRefreshT);
            // ONE seam (`refreshInbox`) — a second inline copy here is exactly the
            // path that repainted the list and left the ring stale.
            _inboxRefreshT = setTimeout(refreshInbox, 400);
        });
        const r = root();
        if (!r) return;
        // search filters the roster client-side (focus preserved — header input is stable)
        r.addEventListener('input', (ev) => {
            const s = ev.target.closest('#agent-roster-search');
            if (s) { _rosterSearch = s.value; applyRosterFilter(); }
        });
        r.addEventListener('click', (ev) => {
            // Deep-link: open a run's session trajectory in the Sessions lens.
            const openSess = ev.target.closest('[data-ap-open-session]');
            if (openSess) { ev.stopPropagation(); openSessionTrace(openSess.dataset.apOpenSession, openSess.dataset.apTurn); return; }
            // Runs Ledger band/invocation filter.
            const rf = ev.target.closest('[data-ap-runfilter]');
            if (rf) { const [g, v] = rf.dataset.apRunfilter.split(':'); _runFilter[g] = v; renderRunsLedger(_expandedTag); return; }
            // The strip key. Toggled in place — re-rendering the ledger would collapse
            // any open run rows to explain a legend.
            const sk = ev.target.closest('[data-ap-stripkey]');
            if (sk) {
                const box = document.getElementById('ap-strip-key');
                // COMPUTED, not the inline attribute: reading `style.display` only sees
                // what this handler last wrote, so any other way the box became hidden
                // would flip the toggle the wrong way on the first click.
                const open = !!box && getComputedStyle(box).display === 'none';
                if (box) box.style.display = open ? 'flex' : 'none';
                sk.textContent = open ? 'Hide key' : 'Key';
                sk.setAttribute('aria-expanded', String(!!open));
                try { localStorage.setItem('tda_strip_key', open ? '1' : '0'); } catch (e) { /* private mode */ }
                return;
            }
            // The leg lens pill (trust trip): toggles the whole expanded detail —
            // bars, work mix AND the ledger — so the sections can never disagree
            // about which window they read.
            const rl = ev.target.closest('[data-ap-runleg]');
            if (rl && _expandedTag) { _toggleRosterLeg(_expandedTag); return; }
            // Compare metric toggle.
            const cm = ev.target.closest('[data-ap-cmpmetric]');
            if (cm) { const k = cm.dataset.apCmpmetric; _compareMetricsOn.has(k) ? _compareMetricsOn.delete(k) : _compareMetricsOn.add(k); renderCompare(); return; }
            // Runs Ledger row expand/collapse.
            const rh = ev.target.closest('.ap-run-hd');
            if (rh) {
                // A click on a dimension band-strip square opens the Assurance Guide (handled by
                // the qualityDimensions document listener) — it must NOT also toggle the run row.
                if (ev.target.closest('[data-dim]')) return;
                const run = rh.closest('.ap-run'); const k = run && run.dataset.apRunkey;
                if (k) { _openRuns.has(k) ? _openRuns.delete(k) : _openRuns.add(k); renderRunsLedger(_expandedTag); } return; }
            // Roster filter pills.
            const pillBtn = ev.target.closest('[data-roster-filter]');
            if (pillBtn) { _rosterFilter = pillBtn.dataset.rosterFilter; applyRosterFilter(); renderCompareBar(); return; }
            // Mini-circle glyph on a roster card → open the Trust Circle
            // focused on that agent (checked before the card-expand branch).
            const mini = ev.target.closest('[data-circle-key]');
            if (mini) {
                ev.stopPropagation();
                _circleAgent = mini.dataset.circleKey || null;
                showSection('circle');
                _mountTrustCircle();
                return;
            }
            // Roster card: select (compare mode) or expand detail.
            const card = ev.target.closest('.agent-card');
            if (card) {
                const tag = card.dataset.agentTag;
                if (_compareMode) { _compareSet.has(tag) ? _compareSet.delete(tag) : _compareSet.add(tag); applyRosterFilter(); renderCompareBar(); }
                else toggleAgentDetail(tag);
                return;
            }
            const oldRunRow = ev.target.closest('.agent-run-row');
            if (oldRunRow) { renderRunDetail(oldRunRow.dataset.runId); return; }
            const btn = ev.target.closest('[data-agent-action]');
            if (!btn) return;
            const a = btn.dataset.agentAction;
            if (a === 'goto-inbox') { showSection('inbox'); return; }
            if (a === 'close-detail') { _expandedTag = null; applyRosterFilter(); }
            else if (a === 'run-diff') runDiff();
            else if (a === 'ann-agree') addAnnotation('agree');
            else if (a === 'ann-disagree') addAnnotation('disagree');
            else if (a === 'judge-agree') ruleOnJudge(btn, 'agree');
            else if (a === 'judge-disagree') ruleOnJudge(btn, 'disagree');
            else if (a === 'toggle-compare') toggleCompareMode();
            else if (a === 'compare-clear') { _compareSet.clear(); applyRosterFilter(); renderCompareBar(); }
            else if (a === 'open-compare') renderCompare();
            else if (a === 'compare-back') { load().then(renderCompareBar); }
            else if (a === 'trust-backtest-run') runBacktest();
            else if (a === 'inbox-agent-filter') {
                _inboxAgentFilter = btn.dataset.tag || null;
                refreshInbox();   // a VIEW filter — the model is unchanged, the repaint idempotent
            }
            else if (a === 'inbox-sev-filter') {
                _inboxSevFilter = btn.dataset.sev || null;
                refreshInbox();   // a VIEW filter — the model is unchanged, the repaint idempotent
            }
            else if (a === 'inbox-open-agent') openAgent(btn.dataset.tag);
            else if (a === 'inbox-ack-batch') ackInboxBatch(btn);
            else if (a === 'inbox-quarantine') quarantineDocFromInbox(btn.dataset.docId, btn);
            else if (a === 'resilience-test') runResilienceTest();
            else if (a === 'audit-pack') exportAuditPack();
            else if (a === 'live-redteam') runLiveProbe();
            else if (a === 'retrieval-anomaly') runRetrievalAnomaly();
            else if (a === 'review-approve-all') bulkDecideReviews(btn, 'approve');
            else if (a === 'review-uphold-all') bulkDecideReviews(btn, 'uphold');
            else if (a === 'review-approve') decideReview(btn.dataset.reviewId, 'approve', btn);
            else if (a === 'review-release') decideReview(btn.dataset.reviewId, 'approve', btn, true);
            else if (a === 'review-reject') decideReview(btn.dataset.reviewId, 'reject', btn);
        });
        // Keyboard a11y for ledger rows.
        r.addEventListener('keydown', (ev) => {
            if ((ev.key === 'Enter' || ev.key === ' ') && ev.target.classList && ev.target.classList.contains('ap-run-hd')) {
                ev.preventDefault();
                const run = ev.target.closest('.ap-run'); const k = run && run.dataset.apRunkey;
                if (k) { _openRuns.has(k) ? _openRuns.delete(k) : _openRuns.add(k); renderRunsLedger(_expandedTag); }
            }
        });
    }

    async function load() {
        const r = root();
        if (!r) return;
        injectCss();
        wire();
        // Invalidate per-agent run cache + transient run state on (re)load / scope switch
        // so the ledger reflects fresh spans.
        Object.keys(_runsByTag).forEach((k) => delete _runsByTag[k]);
        // The story cache too — closures (loop-closure receipts on the arc) are derived
        // from review dispositions, and a cache that survives the reload shows the
        // pre-decision story at the exact moment the arc label matters (#47).
        Object.keys(_storyCache).forEach((k) => delete _storyCache[k]);
        _openRuns.clear();
        await checkAdmin();
        // NEVER wipe the root: it holds the static section containers. The load
        // note is the loading/error surface; sections keep stale content while
        // fresh data arrives (stale-while-revalidate).
        const note = $('agent-load-note');
        if (note) { note.innerHTML = 'Loading agents…'; note.classList.remove('hidden'); }
        _prefetch(['/assurance/inbox', '/assurance/reviews?include_content=true',
                   '/evals/annotations?limit=40', '/evals/judge-calibration', '/evals/judge-queue?limit=20']);
        try {
            const uq = (_scope === 'system' && _scopeUser) ? '&user=' + encodeURIComponent(_scopeUser) : '';
            // ONE parallel batch. /trust/agents and /actions/activity used to be awaited one
            // after another behind this batch, so the page waited for the sum of the three
            // slowest reads instead of the slowest one; none of them depends on another.
            const [profilesResp, dashboard, runsResp, flowsResp, kpiResp, trustResp, trustAgents, actActivity] = await Promise.all([
                api('/profiles').catch(() => ({ profiles: [] })),
                api('/evals/online/dashboard?limit=' + ONLINE_EVAL_WINDOW + '&scope=' + _scope + uq).catch(() => ({})),
                api('/evals/runs?limit=25').catch(() => ({ runs: [] })),
                api('/flows').catch(() => ({ flows: [] })),
                api('/evals/agents/kpi-dashboard?limit=' + ONLINE_EVAL_WINDOW + '&scope=' + _scope + uq).catch(() => ({})),
                api('/evals/agents/trust-behavior?scope=' + _scope + uq).catch(() => null),
                // Trust-Membrane integrity per agent (Ontology Suite; absent → null, no error).
                api('/trust/agents').then((d) => d.agents || null).catch(() => null),
                // OBDA-ACT — the acts across the caller's recent sessions, folded with the
                // confirmations given afterwards (read from the turns, never from the scored
                // span). The caller's OWN sessions only, so it is never read for another
                // user's agent.
                (_scope !== 'system')
                    ? api('/actions/activity?sessions=30').then((d) => d.by_agent || null).catch(() => null)
                    : Promise.resolve(null),
            ]);
            _usersMap = (dashboard && dashboard.users) || {};
            _selfUuid = (dashboard && dashboard.self_uuid) || _selfUuid;
            _updateUserFilterButton();
            // Behavioral Trust Score — surfaced only when the admin block enables it
            // (read-side; absence or an older backend degrades to no ring, no error).
            _trustBehavior = (trustResp && trustResp.enabled) ? trustResp : null;
            _trustAgentsFull = trustAgents;
            _actActivity = actActivity;
            const profiles = (profilesResp.profiles || []).filter((p) => p && p.tag);
            _profilesRaw = profiles;
            const flows = (flowsResp.flows || []).filter((f) => f && (f.flow_id || f.id));
            _applyQualityBands(dashboard);   // before anything bands a rate
            _model = buildModel(profiles, dashboard || {}, runsResp.runs || [], _scope, flows);
            _model.kpi = kpiResp || {};   // the KPI row reads the cards' own population
            // Reset a stale family filter if no agent in the new scope has that family.
            if (_rosterFilter !== 'all' && !_model.agents.some((a) => family(a.type) === _rosterFilter)) {
                _rosterFilter = 'all';
            }
            setScopeIndicator(dashboard || {});
            renderKpis(_model);
            _rosterAttention = _model.agents.filter((a) => a.status === 'review' || a.status === 'danger').length;
            const [ann, inbox] = await Promise.all([renderAnnotations(), renderAssuranceInbox()]);
            // Each topic renders into its own (always-mounted) section container;
            // the header tabs only toggle visibility.
            const setSection = (id, html) => { const el = $(id); if (el) el.innerHTML = html; };
            setSection('agent-section-roster', renderRoster(_model));
            // A reload can land while an agent's detail is expanded (a race with the
            // first expand, a scope switch, the trip-reset flow). renderRoster just
            // re-drew that detail with EMPTY async containers — refill them, or the
            // per-turn Runs Ledger rows vanish until the user closes and reopens.
            if (_expandedTag) _fillExpandedDetail(_expandedTag);
            setSection('agent-section-inbox', `<div id="assurance-inbox-wrap">${inbox}</div>`);
            setSection('agent-section-circle', sectionCard('Trust Circle',
                'M21.5 12a9.5 9.5 0 1 1-3.1-7M21.5 2.5V7H17',
                `<div id="trust-circle-host"></div>`));
            setSection('agent-section-posture', renderTrustPosture(_model));
            setSection('agent-section-experiments', renderExperiments(_model));
            setSection('agent-section-calibration', ann);
            if (_pendingCircleTag) {   // deep-link focus (capsule / chat-header glyph)
                const pa = _model.agents.find((x) => x.tag === _pendingCircleTag && x.kind !== 'flow');
                if (pa) _circleAgent = pa.key || pa.tag;
                _pendingCircleTag = null;
            }
            _mountTrustCircle();   // the living loop, over the freshly loaded model
            // Cross-agent KPI charts render into the Overview section.
            if (window.agentKpiCharts) window.agentKpiCharts.renderInto($('agent-kpi-graphs'), kpiResp || {});
            updateTabBadges();
            if (note) { note.innerHTML = ''; note.classList.add('hidden'); }
        } catch (e) {
            if (note) {
                note.classList.remove('hidden');
                note.innerHTML = `<div class="rounded-xl p-4 border" style="background-color: var(--card-bg); border-color: var(--border-primary);"><div class="text-sm" style="color: var(--trust-danger);">Failed to load agent performance: ${esc(e.message)}</div></div>`;
            }
        } finally {
            _prefetched.clear();   // never served to a later refresh
        }
    }

    // ════════════════════════════════════════════════════════════════════════════
    //  WORLD-CLASS UX LAYER — Runs Ledger · Coordination Trace Tree · Compare
    // ════════════════════════════════════════════════════════════════════════════

    // One-time CSS injection (theme-aware, monochrome, motion-respecting).
    function injectCss() {
        if ($('agent-perf-ux-css')) return;
        const s = document.createElement('style');
        s.id = 'agent-perf-ux-css';
        s.textContent = `
        .ap-fade{animation:apFade .28s ease both}
        @keyframes apFade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
        @media (prefers-reduced-motion: reduce){.ap-fade{animation:none}}
        .ap-run{border:1px solid var(--border-secondary);border-radius:12px;background:var(--card-bg);
            transition:border-color .15s,box-shadow .15s,transform .15s;overflow:hidden}
        .ap-run + .ap-run{margin-top:8px}
        .ap-run:hover{border-color:var(--border-primary);box-shadow:0 2px 14px rgba(0,0,0,.10)}
        .ap-run.open{border-color:var(--teradata-orange)}
        .ap-run-hd{display:flex;align-items:center;gap:10px;padding:10px 12px;cursor:pointer;user-select:none}
        /* Column discipline. The row is read DOWN its columns — band, marks, cost,
           latency — so no chip may push them: the chip slot is the only flexible
           member and it SHRINKS (its full text lives in the tooltip either way),
           while every fixed member refuses to wrap. A long chip once wrapped the
           timestamp onto two lines and shifted the strip row by row. */
        .ap-run-hd > .ap-run-fixed{flex:0 0 auto;white-space:nowrap}
        .ap-run-chips{flex:1 1 auto;min-width:0;display:flex;align-items:center;gap:6px;
            overflow:hidden}
        .ap-run-chips > .ap-chip{min-width:0;overflow:hidden;text-overflow:ellipsis;
            white-space:nowrap}
        .ap-run-hd > .ap-strip{flex:0 0 auto}
        .ap-run-hd:focus-visible{outline:2px solid var(--teradata-orange);outline-offset:-2px;border-radius:12px}
        /* The verdict dot is RETIRED. A dot means "a mechanism acted" (Rule 9.3), and
           once the acted mark landed on the row there were two dots side by side
           meaning different things — the misreading the one-mark-one-meaning rule
           exists to prevent. The verdict is carried by its WORD, in its colour; the
           state MARK (a square) carries it where there is no room for a word. */
        /* ── Assurance Inbox ──────────────────────────────────────────────────
           A triage queue, so it is built for SCANNING: one title per row in a
           single strong weight, everything else quieter and beneath it. The
           severity rides a left edge rather than a chip, because a chip competed with
           the station chip for the same eye position and neither won. */
        .ai-row{display:flex;gap:12px;align-items:flex-start;padding:10px 12px 10px 14px;
                border-left:2px solid transparent;border-bottom:1px solid var(--border-secondary);
                position:relative;transition:background .12s}
        .ai-row:last-child{border-bottom:none}
        .ai-row:hover{background:color-mix(in srgb, var(--text-muted) 6%, transparent)}
        /* Rule 9.4a — severity is never carried by hue alone. The edge differs in
           WEIGHT as well as colour, and the severity WORD rides the meta line, so the
           three levels are distinguishable in greyscale and under CVD. (The first draft
           of this redesign dropped the word chip and left the edge colour doing all the
           work — caught by the gate, which is what it is for.) */
        .ai-sev-danger{border-left-color:var(--trust-danger);border-left-width:3px}
        .ai-sev-warning{border-left-color:var(--trust-warn);border-left-width:2px}
        .ai-sev-info{border-left-color:var(--border-secondary);border-left-width:1px}
        .ai-sev{font-weight:600}
        .ai-sev-danger .ai-sev{color:var(--trust-danger)}
        .ai-sev-warning .ai-sev{color:var(--trust-warn)}
        .ai-main{flex:1;min-width:0}
        .ai-title-row{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
        .ai-title{font-size:12.5px;font-weight:600;color:var(--text-primary);line-height:1.35}
        .ai-detail{font-size:11px;color:var(--text-muted);margin-top:2px;line-height:1.45}
        .ai-meta{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:4px;
                 font-size:10px;color:var(--text-muted)}
        .ai-meta > *{flex-shrink:0}
        /* No .ai-tag rule on purpose: profile-tag--sm is already 10px and
           0.125rem 0.375rem padding, which is the inbox's own scale. The first draft
           overrode font-size, padding AND the border-width that main.css calls
           industrial 2px for strength — a local re-design of a shared token, not a fix. */
        .ai-station{font-size:9px;letter-spacing:.04em;padding:1px 5px;border-radius:3px;
                    border:1px solid var(--border-secondary);opacity:.8}
        .ai-count{font-size:10px;font-weight:600;padding:1px 6px;border-radius:9999px;
                  color:var(--text-muted);background:color-mix(in srgb, var(--text-muted) 12%, transparent)}
        .ai-disp{margin-left:2px}
        .ai-occ{margin-top:5px}
        .ai-occ > summary{font-size:10px;color:var(--text-muted);cursor:pointer;list-style:none}
        .ai-occ > summary::-webkit-details-marker{display:none}
        .ai-occ > summary::before{content:'▸ ';opacity:.6}
        .ai-occ[open] > summary::before{content:'▾ '}
        /* Each occurrence is a row with its OWN actions — the collapsed row acts on
           all of them, and expanding must not cost you the ability to act on one. */
        .ai-occ-row{display:flex;align-items:center;gap:8px;padding:3px 0 3px 12px;
                    font-size:10px;color:var(--text-muted)}
        .ai-occ-when{flex:1;min-width:0}
        .ai-occ-act{display:flex;gap:4px;align-items:center;flex-shrink:0}
        .ai-occ-act .ai-btn{font-size:10px;padding:1px 7px}
        .ai-occ-settled{opacity:.7;font-style:italic}
        /* Actions: quiet until needed, but never hidden from the keyboard — an
           action reachable only by hover is unreachable for anyone not using a
           mouse, and these are the queue's whole purpose. */
        .ai-actions{display:flex;gap:6px;align-items:center;flex-shrink:0;
                    opacity:.35;transition:opacity .12s}
        .ai-row:hover .ai-actions,.ai-actions:focus-within{opacity:1}
        .ai-btn{font-size:11px;padding:3px 9px;border-radius:6px;white-space:nowrap;
                color:var(--text-muted);border:1px solid var(--border-secondary);
                background:transparent;cursor:pointer;transition:all .12s}
        .ai-btn:hover{color:var(--text-primary);border-color:var(--border-primary)}
        .ai-btn:focus-visible{outline:2px solid var(--teradata-orange);outline-offset:1px}
        .ai-btn-act{color:var(--trust-warn);border-color:color-mix(in srgb, var(--trust-warn) 45%, transparent)}
        .ai-btn-act:hover{color:var(--trust-warn);border-color:var(--trust-warn);
                          background:color-mix(in srgb, var(--trust-warn) 10%, transparent)}
        /* One filter row, not two label+pill rows. The pills self-describe, so the
           "Agent:" / "Show:" prefixes were pure furniture. */
        .ai-bar{display:flex;align-items:center;gap:6px;flex-wrap:wrap;padding:0 0 10px 0;
                border-bottom:1px solid var(--border-secondary);margin-bottom:2px}
        .ai-bar-sep{width:1px;height:14px;background:var(--border-secondary);margin:0 2px}
        .ai-pill{font-size:10px;padding:2px 9px;border-radius:9999px;cursor:pointer;
                 border:1px solid var(--border-secondary);color:var(--text-muted);
                 background:transparent;transition:all .12s;white-space:nowrap}
        .ai-pill:hover{color:var(--text-primary);border-color:var(--border-primary)}
        .ai-pill:focus-visible{outline:2px solid var(--teradata-orange);outline-offset:1px}
        .ai-pill-on{border-color:var(--teradata-orange);color:var(--teradata-orange);
                    background:color-mix(in srgb, var(--teradata-orange) 8%, transparent)}
        .ai-empty{font-size:12px;color:var(--text-muted);text-align:center;padding:28px 12px}
        .ai-fold > summary{font-size:11px;color:var(--text-muted);cursor:pointer;padding:8px 0 0 0;
                           list-style:none}
        .ai-fold > summary::-webkit-details-marker{display:none}
        .ai-fold > summary::before{content:'▸ ';opacity:.6}
        .ai-fold[open] > summary::before{content:'▾ '}
        .ap-strip{display:inline-flex;gap:3px;align-items:center}
        /* The band mark — SHAPE carries the state family, colour reinforces it.
           trust.css declares this grammar for every surface (Rule 9.4: "legible
           without hue"): pass solid · warn half-fill · danger outlined+emphasized ·
           na sparse dash. This mark used to be a solid square in all four states with
           only the fill colour differing, so colour was the SOLE carrier — and in the
           LIGHT theme --trust-warn (#b45309) and --trust-danger (#dc2626) sit ΔE 31.9
           apart with 26° of hue between them (dark: 69.0 / 43°), which under
           deuteranopia collapses to ΔE 5.5 — the same colour. No warm hue fixes that
           (the best candidate moves CVD separation 11.7 → 13), so the answer is the
           channel this surface never adopted, not a different amber.
           8px → 9px: a 2px ring needs the extra pixel to read as a ring. */
        .ap-sq{width:9px;height:9px;border-radius:2px;flex:0 0 auto;box-sizing:border-box}
        .ap-sq-healthy{background:var(--sq)}
        .ap-sq-review{background:linear-gradient(180deg,var(--sq) 50%,transparent 50%);border:1px solid var(--sq)}
        .ap-sq-danger{background:transparent;border:2px solid var(--sq)}
        .ap-sq-na{background:transparent;border:1px dashed var(--trust-na);opacity:.6}
        /* WHO measured it — a channel apart from WHAT it found.
           Twenty-odd identical squares said nothing about which two were a model's
           opinion, so a reader could not tell that a judge had run, nor what it ruled.
           Fill treatment already carries the state family and colour carries attention,
           which leaves the glyph's own shape. Deliberately NOT a circle: a dot means
           "a mechanism acted" on the ring (Rule 9.3), and one mark with two meanings
           decided by which surface you are looking at is the drift this grammar exists
           to prevent. A diamond is unused here, reads as "a different kind of thing"
           (the forest-plot convention), survives greyscale and CVD, and composes with
           all four state treatments — a judge in danger is an outlined diamond. */
        .ap-sq-j{transform:rotate(45deg) scale(.80);border-radius:1px}
        /* …and how much that ruling could DO. A judge a gate consulted DURING the turn
           decided something — its verdict could withhold the answer — and is drawn
           LARGER. A judge sampled after the turn only reported, and wears the plain
           diamond.

           Calibrated so the common case looks normal, not diminished: only 4 of a real
           agent's 52 judge marks are in-path, so shrinking the other 48 made the strip
           read as degraded ("way too many small diamonds"). .80 puts a rotated square at
           the same visual weight as a 9px square — the baseline — and 1.0 is a clear
           step UP for the one that decided something. The ordering the operator
           specified is unchanged: sampled is smaller than in-path. */
        .ap-sq-jp{transform:rotate(45deg) scale(1)}
        /* A CORRECTED measurement (Rule 3.0c 'disputed'): the rule measured danger and
           the judge that refines it cleared the same answer. Both facts, one mark — the
           outline keeps the measurement (it is never rewritten) and the centre carries
           the refining judge's verdict, green by definition of 'disputed'. Without it a
           corrected measurement was indistinguishable from an uncorrected failure on a
           24-mark strip, while the row's 'judge-cleared' chip said one had been
           corrected without saying WHICH. Presence/absence of the dot is the channel;
           hue only reinforces it (Rule 9.4). Not the ring's acted dot: that is a mark
           in its own right, this is an inclusion inside one. */
        .ap-sq.ap-sq-c{background:radial-gradient(circle at 50% 50%,
            var(--dot, var(--trust-pass, #10b981)) 0 1.6px, transparent 1.7px)}
        /* station separator in the band-strip — the ring's segmentation, laid flat */
        /* Rule 9.3's acted mark, on the ledger: a mechanism acted. A DOT, unlike every
           mark in the strip beside it — those are measurements, this is an act. */
        .ap-acted-dot{width:6px;height:6px;border-radius:50%;background:var(--sq);
            flex:0 0 auto;margin-right:5px;display:inline-block;vertical-align:-1px}
        .ap-strip-sep{width:1px;height:9px;background:var(--border-primary);opacity:.9;margin:0 2px;flex:0 0 auto}
        .ap-chev{transition:transform .2s;color:var(--text-muted);flex:0 0 auto}
        .ap-run.open .ap-chev{transform:rotate(90deg)}
        .ap-body{padding:0 12px 12px 12px;animation:apFade .22s ease both}
        .ap-chip{display:inline-flex;align-items:center;gap:4px;font-size:11px;padding:2px 8px;border-radius:999px;
            line-height:1.5;white-space:nowrap}
        .ap-tier-h{font-size:9px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--text-muted);opacity:.7;margin:8px 0 4px}
        .ap-link{font-size:11px;color:var(--teradata-orange);cursor:pointer;display:inline-flex;align-items:center;gap:3px;
            border:1px solid transparent;border-radius:6px;padding:2px 6px;transition:background .15s}
        .ap-link:hover{background:rgba(241,95,34,.10)}
        /* trace tree */
        .ap-tree{position:relative;margin-top:8px;padding-left:6px}
        .ap-node{position:relative;padding:8px 10px;border-radius:10px;background:var(--bg-overlay,rgba(127,127,127,.05));
            border:1px solid var(--border-secondary);transition:background .15s,border-color .15s}
        .ap-node:hover{border-color:var(--border-primary)}
        .ap-node + .ap-node{margin-top:7px}
        .ap-node.child{margin-left:26px}
        .ap-node.child::before{content:'';position:absolute;left:-15px;top:-7px;bottom:50%;width:14px;
            border-left:2px solid var(--border-primary);border-bottom:2px solid var(--border-primary);
            border-bottom-left-radius:8px}
        .ap-wf-track{position:relative;height:14px;border-radius:7px;margin-top:6px;
            background:repeating-linear-gradient(90deg,rgba(127,127,127,.08) 0 1px,transparent 1px 64px)}
        .ap-wf-bar{position:absolute;top:1px;height:12px;border-radius:6px;min-width:5px;
            box-shadow:inset 0 0 0 1px rgba(255,255,255,.10)}
        /* compare */
        .ap-cmp-metric{cursor:pointer;border:1px solid var(--border-secondary);border-radius:999px;
            font-size:11px;padding:4px 11px;transition:all .15s;color:var(--text-muted);background:transparent}
        .ap-cmp-metric.on{color:var(--teradata-orange);border-color:var(--teradata-orange);background:rgba(241,95,34,.10)}
        .ap-gbar{height:18px;border-radius:6px;transition:width .5s cubic-bezier(.2,.7,.3,1);position:relative}
        .ap-heat-cell{width:30px;height:30px;border-radius:7px;display:flex;align-items:center;justify-content:center;
            font-size:10px;font-weight:600;transition:transform .12s}
        .ap-heat-cell:hover{transform:scale(1.12)}
        .ap-sel{position:absolute;top:8px;right:8px;width:20px;height:20px;border-radius:6px;border:2px solid var(--border-primary);
            display:flex;align-items:center;justify-content:center;background:var(--card-bg);transition:all .15s}
        .ap-card.cmp-on .ap-sel{border-color:var(--teradata-orange)}
        .ap-card.cmp-sel .ap-sel{background:var(--teradata-orange);border-color:var(--teradata-orange)}
        `;
        document.head.appendChild(s);
    }

    // ── band + format helpers ─────────────────────────────────────────────────────
    // ONE colour vocabulary (PROP-002): band colours are the trust tokens, via
    // TrustGrammar.bandColor (fallback literal gate-pinned equal).
    const BAND_C = {
        healthy: 'var(--trust-pass, #10b981)', review: 'var(--trust-warn, #f59e0b)',
        danger: 'var(--trust-danger, #ef4444)', na: 'var(--text-muted)',
    };
    // TC-45: monochrome SVGs — the ✓/✕ dingbats render as colour emoji on some
    // platforms, and review was a literal "!" text glyph.
    const BAND_ICON = { healthy: '<svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-0.5px"><path d="M5 13l4 4L19 7"/></svg>', review: '<svg width="8" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" style="vertical-align:-0.5px"><path d="M12 4v10"/><path d="M12 19v.5"/></svg>', danger: '<svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" style="vertical-align:-0.5px"><path d="M6 6l12 12M18 6 6 18"/></svg>', na: '–' };
    function bandOfVerdict(v) {
        if (v.band) return v.band;
        if (v.passed === true) return 'healthy';
        if (v.passed === false) return 'danger';
        return 'na';
    }
    /** ONE band mark, for every small state square in the ledger.
     *
     *  Shape carries the state family and colour reinforces it (trust.css Rule 9.4,
     *  "legible without hue"). Two surfaces here — the per-run band strip and the
     *  station header mark — each rendered their own solid square with only the fill
     *  colour differing, so a review band and a danger band were the same shape in two
     *  places, distinguishable by a hue difference that vanishes under deuteranopia.
     *  Built here once so the two marks in a single row cannot speak different grammars.
     *
     *  The class is keyed on the BAND name directly — no conversion, nothing to get
     *  wrong (see Rule 3c.1); the colour rides a custom property so the CSS holds the
     *  shapes and the caller holds nothing but the state.
     */
    function bandMark(b, attrs = '', judged = false, inPath = false, corrected = false) {
        const band = BAND_C[b] ? b : 'na';
        // The centre dot IS the refining judge's verdict, in BOTH directions: green on a
        // failed rule (it cleared the answer) and red on a passing one (it disputed the
        // pass). Only the first was ever drawn, so three of Rule 3.0c's four readings had
        // a name and the fourth — the one where a green measurement hides a judge's
        // objection — had none.
        const dot = corrected === 'flagged'
            ? ';--dot:var(--trust-danger, #ef4444)' : '';
        // A diamond is a judge. A judge SAMPLED after the turn wears the shrink: it only
        // reported, where an in-path judge's verdict could withhold the answer. So the
        // regular diamond is the one that decided something, and it is the one that
        // stands out — the modifier rides the common case because it means "less
        // weight", not "look here".
        const kind = judged ? (inPath ? ' ap-sq-j ap-sq-jp' : ' ap-sq-j') : '';
        return `<span class="ap-sq ap-sq-${band}${kind}${corrected ? ' ap-sq-c' : ''}" ${attrs} style="--sq:${BAND_C[band]}${dot}"></span>`;
    }
    /** A run row's band — the SAME verdict the headline counts, never re-derived.
     *
     *  The row reads `run.passed`, which the backend computes with the ONE conviction
     *  rule (`analysis.turn_failed`): a check a RULE measured landed in danger — unless
     *  the judge that refines it corrected that, which is Rule 3.0c's `disputed` — or a
     *  gate withheld the answer. Nothing here decides policy.
     *
     *  Two earlier versions each re-derived it and each disagreed with the headline
     *  above them: first "any verdict with passed === false" (an advisory check
     *  convicting a whole run: 7 vs 11), then "any deterministic danger" (which
     *  convicted the turns a judge had corrected: 7 vs 10). A row that computes its own
     *  verdict will always eventually differ from the one printed above it.
     *
     *  `review` is what remains: a judge alone in danger (a model opined, no rule
     *  measured it), a review-band metric, or a coarse check the judge cleared — the
     *  last carrying its own `disputed` mark, because a correction hidden is a
     *  correction nobody can check.
     */
    function runBand(run) {
        if (run.passed === false) return 'danger';
        const vs = run.verdicts || [];
        const flagged = vs.some((v) => v.band === 'review')
            || vs.some((v) => v.passed === false)          // judge-only, or judge-cleared
            || (run.disputed || []).length > 0;
        if (flagged) return 'review';
        return vs.length ? 'healthy' : (run.passed ? 'healthy' : 'na');
    }
    const fmtCost = (c) => c == null ? '—' : '$' + Number(c).toFixed(4);
    const fmtMs = (ms) => ms == null || !ms ? '—' : (ms >= 1000 ? (ms / 1000).toFixed(1) + 's' : Math.round(ms) + 'ms');
    const fmtTok = (t) => t == null || !t ? '—' : Number(t).toLocaleString();
    function relTime(sec) {
        if (!sec) return '';
        const d = Math.max(0, Date.now() / 1000 - sec);
        if (d < 60) return 'just now';
        if (d < 3600) return Math.floor(d / 60) + 'm ago';
        if (d < 86400) return Math.floor(d / 3600) + 'h ago';
        return Math.floor(d / 86400) + 'd ago';
    }

    const INLINE_EV = new Set(['tasks', 'cost', 'tokens']);

    // Consumer-facing dimension metadata + custom tooltip + Assurance Guide glossary all
    // live in the shared qualityDimensions.js module (single source of truth, shared with
    // the Live Status Quality card). These thin helpers resolve a logical (non-physical)
    // label and render [data-dim] attributes so the internal scorer key is never shown.
    const QD = window.QualityDimensions || {};
    const dimLabel = (k) => (QD.label ? QD.label(k) : String(k || ''));
    // Attributes to place on any element that represents a dimension: hover → styled
    // tooltip, click → the guide at that dimension. `note` carries a per-verdict runtime
    // explanation (e.g. "turn completed without error") shown beneath the definition.
    const dimAttrs = (k, note, focusable = true) => `data-dim="${escAttr(k)}"${note ? ` data-dim-note="${escAttr(note)}"` : ''}${focusable ? ' tabindex="0"' : ''}`;
    const guideBtn = (text) => (QD.guideButton ? QD.guideButton(text) : '');
    /** Did a MODEL opine on this measurement, or did a rule measure it?
     *
     *  ONE predicate for every mark in a row — the strip's glyph shape, the chip's
     *  ·judge tag, the row's `judged` chip — so two marks describing the same
     *  measurement can never disagree about its kind.
     *
     *  The verdict's own server-resolved `kind` wins (it is read from the live
     *  registry at score time); `JUDGE_DIMS` is the fallback for a span persisted
     *  before that field existed — the same precedent as back-filling `gating`.
     */
    const isJudgeDim = (k) => !!(QD.JUDGE_DIMS && QD.JUDGE_DIMS.has(k));
    const isJudgeVerdict = (v) => (v && v.kind) ? v.kind === 'judge' : isJudgeDim(v && v.dimension);
    /** Did a GATE consult this judge in-path, or was it sampled after the fact?
     *
     *  The distinction is consequential, not cosmetic: an in-path judge's ruling can
     *  withhold the answer (the grounding gate escalates to it and acts on the verdict),
     *  while a sampled judge opines post-hoc on a turn nobody flagged and changes
     *  nothing. `gate.judge_family` names the one the gate consulted; every other judge
     *  on that turn — and every judge on a turn with no escalation — was sampled.
     */
    const isInPathJudge = (v, run) => !!(run && run.gate && run.gate.escalated
        && run.gate.judge_family && run.gate.judge_family === (v && v.dimension));
    function escAttr(s) { return String(s == null ? '' : s).replace(/[&<>"']/g,
        (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }


    function fmtMetricVal(dim, score) {
        if (score == null) return '';
        if (dim === 'cost') return '$' + Number(score).toFixed(4);
        if (dim === 'latency') return fmtMs(score);
        if (dim === 'tokens') return Number(score).toLocaleString();
        if (dim === 'parallel_efficiency') return Number(score).toFixed(2) + '×';
        if (['tool_success_rate', 'numeric_fidelity', 'grounded_overlap', 'plan_execution', 'retrieval_relevance'].includes(dim))
            return Math.round(score * 100) + '%';
        return Number.isInteger(score) ? String(score) : Number(score).toFixed(score < 10 ? 1 : 0);
    }

    /** A judge's PANEL, and whether it was unanimous.
     *
     *  A judge dimension is decided by a panel of models and the recorded verdict is
     *  their consensus — so a dissenting model disappears into a majority. Worst-of is
     *  the house rule for aggregation everywhere else (Rule 5.1), and Rule 3.0c already
     *  says that when measures of one property disagree the disagreement is REPORTED,
     *  never resolved by weighting. A panel is N measures of one property.
     *
     *  So the MARK is red if any panel model voted fail. Where that differs from the
     *  consensus the split is NAMED — on the mark's tooltip and on the chip — because a
     *  red mark beside a chip reading "upheld" with nothing explaining the difference is
     *  the contradiction this grammar exists to prevent.
     */
    const _panelVotes = (v) => (v && Array.isArray(v.votes)) ? v.votes : [];
    const _panelFails = (v) => _panelVotes(v).filter(
        (x) => String((x && x.verdict) || '').toLowerCase() === 'fail').length;
    const _panelSplit = (v) => isJudgeVerdict(v) && v.passed === true && _panelFails(v) > 0;
    /** The band a MARK is drawn in — worst-of the panel for a judge, the verdict's own
     *  band for everything else. An abstention stays in the absence family: a judge that
     *  declined to answer is not a green. */
    function markBandOf(v) {
        const b = bandOfVerdict(v);
        if (b === 'na' || !isJudgeVerdict(v)) return b;
        return _panelFails(v) > 0 ? 'danger' : b;
    }

    /** The runtime note on a mark, with the correction folded in when there is one.
     *  Rule 3.0c's `disputed` is DEFINED as "the coarse check flagged and the judge that
     *  refines it cleared", so the second opinion is always clean — that is why the dot
     *  is green, not a choice made here. */
    const _clearedNote = (v, cleared, inPath, flagged) => {
        if (v && v.absent) return 'this check did not run on this turn';
        if (flagged && flagged.has(v.dimension)) {
            return `${v.explanation ? v.explanation + ' — ' : ''}this check passed, and the judge that refines it disputed that (Rule 3.0c); the measurement stands, and a judge alone does not fail the turn`;
        }
        if (inPath) {
            // Precision that matters: a gate consulted this judge CLASS during the turn
            // and acted on THAT ruling. The verdict shown here is the scoring layer's,
            // taken afterwards. Two invocations of one judge; the mark says the class
            // had teeth on this turn, not that this number did.
            const b0 = v && v.explanation ? v.explanation + ' — ' : '';
            return `${b0}a gate consulted this judge during the turn and acted on its ruling; the score shown was taken afterwards`;
        }
        const base = (cleared && cleared.has(v.dimension))
            ? `${v.explanation ? v.explanation + ' — ' : ''}cleared by the judge that refines this check (Rule 3.0c); the measurement stands`
            : v.explanation;
        if (!_panelSplit(v)) return base;
        const n = _panelVotes(v).length, f = _panelFails(v);
        return `${base ? base + ' — ' : ''}this judge's panel upheld it ${n - f} models to ${f}; the mark shows the dissent`;
    };

    // dimension band-strip — at-a-glance quality of a run.
    // Squares run in STATION order (the partition) with a hairline separator between
    // stations, so the strip reads as the ring's segments laid flat: the first block
    // is GROUNDED's checks, then GUARDED, BOUNDED, JUDGED, PROVEN — the same order
    // the chips below group by, which is what makes the segmentation legible.
    function dimStrip(verdicts, run, allDims) {
        // A FIXED grid, not a variable-length list. The strip is read DOWN the ledger —
        // "did this check pass on every turn?" — which only works if a dimension sits in
        // the same column on every row. A check the turn did not produce renders as an
        // absence (`na`), exactly like one that abstained: silently omitting it made the
        // strips different lengths and put the same dimension in a different column on
        // every row, so nothing could be compared vertically.
        const byDim = new Map((verdicts || []).map((v) => [v.dimension, v]));
        const dims = (allDims && allDims.length)
            ? allDims : (verdicts || []).map((v) => v.dimension);
        const cells = dims.map((d) => byDim.get(d) || { dimension: d, band: 'na', absent: true });
        const groups = {};
        cells.forEach((v) => {
            const st = _dimStageOf(v.dimension);
            (groups[st] = groups[st] || []).push(v);
        });
        // The verdict's runtime explanation rides the mark's tooltip: hovering any
        // square now says what that check FOUND on this turn, not only what it is.
        // A judge's square is a diamond, so "a model opined here" and "this is what
        // it ruled" are both readable without expanding the row.
        // A corrected measurement carries the correction ON the mark — the row's
        // `judge-cleared` chip says one was corrected, not which.
        const cleared = new Set((run && run.disputed) || []);
        const flagged = new Set((run && run.judge_flagged) || []);
        const sq = (v) => bandMark(markBandOf(v),
            dimAttrs(v.dimension, _clearedNote(v, cleared, isInPathJudge(v, run), flagged), false),
            isJudgeVerdict(v), isInPathJudge(v, run),
            cleared.has(v.dimension) ? true : (flagged.has(v.dimension) ? 'flagged' : false));
        const parts = _stageKeysInOrder(groups).map((st) =>
            groups[st].slice()
                .sort((a, b) => (_stageIdxOf(a.dimension) - _stageIdxOf(b.dimension))
                    || String(a.dimension).localeCompare(String(b.dimension)))
                .map(sq).join(''));
        if (!parts.length) return '';
        return `<span class="ap-strip">${parts.join('<span class="ap-strip-sep"></span>')}</span>`;
    }

    // full verdict chips, grouped by tier
    /** #44 — what a run row must SAY before a reader scans twenty chips.
     *
     *  The row described a turn by its scores alone: "turn 1 · deterministic". On the
     *  walkthrough's first real block that turn had been adjudicated by two judges and
     *  WITHHELD by the grounding gate — the defining fact of the turn — and the surface
     *  built to explain it said the opposite. It read only the eval span, and the gate's
     *  outcome was not on the span. It is now (I1), so the row can be honest.
     *
     *  Two additions, both naming rather than implying:
     *    · the gate's act, in the user's words ("answer withheld", "request refused")
     *    · the dimensions that actually failed, so "danger" stops being a scavenger hunt
     */
    const _GATE_ACT = {
        grounding_block: 'answer withheld by the grounding gate',
        inbound_block: 'request refused by the inbound guardrail',
        outbound_block: 'answer withheld by the outbound guardrail',
        outbound_redact: 'personal data redacted on the way out',
    };

    /** A mechanism ACTED on this turn — the intervention axis, on the row.
     *
     *  Orthogonal to the verdict beside it (contract Rule 3bb): on real data every
     *  intervention landed on a turn whose measurements were otherwise clean, so a
     *  healthy row can still be one whose answer was withheld. That fact had no mark
     *  at all — it lived in the expanded body.
     *
     *  Carried on the channel the grammar already owns for it (Rule 9.3): a
     *  state-coloured dot = a mechanism acted · accent = an act is OWED. The dot alone
     *  cannot say WHICH act, and there are four, so the act is named beside it.
     *
     *  An errored guard is NOT an act — it is a guard that did not run, counted apart
     *  in the roster's intervention reading. Calling it an intervention would report
     *  the absence of a control as the control working.
     */
    // The user's rating of THIS run. A word chip, never colour alone (Rule 9.4a) and
    // deliberately muted: colour on these surfaces is attention priority, and a
    // preference is not an attention level. It must never move the row's band —
    // `runBand` reads the verdict only.
    function _voteTag(r) {
        const v = r && r.vote;
        if (v !== 'up' && v !== 'down') return '';
        const tip = v === 'up'
            ? 'You marked this answer helpful. A preference, not a measurement —\nit never changes the verdict beside it.'
            : 'You marked this answer unhelpful. A preference, not a measurement —\nit never changes the verdict beside it.';
        return `<span class="ap-chip u-tip" data-tip="${esc(tip)}" style="color:var(--text-muted);background:rgba(127,127,127,.12);border:1px solid var(--border-secondary)">${v === 'up' ? 'upvoted' : 'downvoted'}</span>`;
    }

    function _actedTag(r) {
        const g = (r && r.gate) || {};
        const acts = [];
        if (g.inbound === 'block') acts.push('refused');
        if (g.grounding === 'block') acts.push('withheld');
        else if (g.grounding === 'annotate') acts.push('annotated');
        if (g.outbound === 'block') acts.push('withheld');
        else if (g.outbound === 'redact') acts.push('redacted');
        if (g.honesty === 'rewrite') acts.push('rewritten');
        if (!acts.length && !g.parked) return '';
        const owed = !!g.parked;
        const c = owed ? 'var(--teradata-orange, #F15F22)' : 'var(--trust-warn, #f59e0b)';
        const words = [...new Set(acts)].join(' · ') + (owed ? ' · parked' : '');
        // `judge_votes` counts the judge's PANEL — its models — so it must not be
        // labelled "judges" beside a chip that counts checks.
        const _mv = /(\d+)\s*\/\s*(\d+)/.exec(String(g.judge_votes || ''));
        const _raw = String(g.judge_votes || '');
        const asked = g.escalated
            ? `\nThe gate consulted one judge to decide this`
              + `${_mv ? `; ${_mv[1]} of ${_mv[2]} models on its panel upheld the answer`
                  : _raw ? `; its panel voted ${_raw}` : ''}.`
            : '';
        const tip = (owed
            ? 'A mechanism acted on this turn — the answer was altered or withheld — and the\noriginal is parked for review. Open the Assurance Inbox to release or uphold it.'
            : 'A mechanism acted on this turn — what was delivered is not what the model wrote.\nThis is separate from the verdict beside it: a turn every check calls clean can still have been altered.') + asked;
        return `<span class="ap-chip u-tip" data-tip="${escAttr(tip)}"
            style="color:${c};background:color-mix(in srgb, ${c} 10%, transparent);border:1px solid color-mix(in srgb, ${c} 33%, transparent)"
            ><span class="ap-acted-dot" style="--sq:${c}"></span>${esc(words)}</span>`;
    }

    function _runGateNote(r) {
        const g = r && r.gate;
        if (!g) return '';
        const acts = [];
        if (g.grounding === 'block') acts.push(_GATE_ACT.grounding_block);
        if (g.inbound === 'block') acts.push(_GATE_ACT.inbound_block);
        if (g.outbound === 'block') acts.push(_GATE_ACT.outbound_block);
        else if (g.outbound === 'redact') acts.push(_GATE_ACT.outbound_redact);
        // A CLEARED escalation is the strongest evidence a gate works and leaves no
        // failing dimension at all — the reason it was invisible everywhere (#36).
        if (!acts.length && g.escalated) {
            acts.push(g.cleared ? 'checked by a judge and cleared'
                                : 'escalated to a judge');
        }
        if (!acts.length) return '';
        // `judge_votes` is `passed/total` over the judge's PANEL — its models — parsed
        // from the judge's own `panel 0✓/1✗`. Labelling it "judges" made a count of
        // models read as a count of checks, the one word this grammar keeps apart.
        const _vm = /(\d+)\s*\/\s*(\d+)/.exec(String(g.judge_votes || ''));
        const votes = _vm ? ` (${_vm[1]} of ${_vm[2]} models upheld it)`
            : g.judge_votes ? ` (its panel voted ${esc(g.judge_votes)})` : '';
        const parked = g.parked ? ' · parked for review' : '';
        return `<div class="text-[11px] mt-0.5" style="color:var(--trust-warn, #f59e0b)">`
             + `${esc(acts.join(' · '))}${votes}${parked}</div>`;
    }

    /** ONE vocabulary for two populations that were both called "judges".
     *
     *    a JUDGE  — one check, asked once of the turn (`groundedness`,
     *               `content_safety`, …). Nine exist; four apply to a Focus agent.
     *    a MODEL  — a member of that judge's panel, voting inside it.
     *
     *  They were fused on the same row: "1 judge flagged this" counted checks while
     *  "gate asked a judge (0/1)" counted models, so one word meant two things a few
     *  pixels apart. Every label below states which population it is counting.
     */

    /** Did a model opine on this turn, and what did it rule?
     *
     *  A judge RUNNING is a fact about the turn, not about any one measurement, and it
     *  was readable only after expanding the row ("turn 1 · judged"). Two things it
     *  carries that the diamond marks cannot: an in-path escalation (a gate consulted a
     *  judge — not a dimension at all, so it produces no square), and the ruling as a
     *  tally rather than N glyphs to count.
     *
     *  Muted, never a band colour: the row states its verdict once already, and a second
     *  coloured chip beside it reads as a second verdict (Rule 3.0). An abstention is
     *  counted apart from a ruling — a judge that declined to answer upheld nothing.
     */
    function _judgeTag(r) {
        const vs = (r.verdicts || []).filter(isJudgeVerdict);
        const escalated = !!(r.gate && r.gate.escalated);
        if (!vs.length && !escalated) return '';
        const state = (v) => { const b = bandOfVerdict(v); return b === 'na' ? 'abstained (no input to rule on)' : b === 'healthy' ? 'upheld' : 'flagged'; };
        const ruled = vs.filter((v) => !state(v).startsWith('abstained'));
        const upheld = ruled.filter((v) => state(v) === 'upheld').length;
        // A gate CONSULTING a judge is a different fact from the panel's tally, so it is
        // a different chip — and only where nothing else already carries it: when the
        // gate acted, the act chip states the consultation in its tooltip, and repeating
        // it here would be one slot restating another (Rule 3.0).
        if (escalated && !_actedTag(r)) {
            // The gate consults ONE judge; this fraction is that judge's PANEL — its
            // models. A bare `(0/1)` beside a chip counting judges read as a second
            // count of the same thing.
            const raw = String(r.gate.judge_votes || '');
            const mv = /(\d+)\s*\/\s*(\d+)/.exec(raw);
            // An unparsed vote string is still the gate's own record — show it rather
            // than drop it. A shape the parser does not know (an older `3✓/0✗`) must
            // not make the ruling vanish.
            const v = mv ? ` · ${mv[1]}/${mv[2]} models` : raw ? ` · ${raw} models` : '';
            return `<span class="ap-chip u-tip" data-tip="${escAttr(
                'A gate consulted ONE judge DURING the turn — that ruling could have stopped the answer'
                + (r.gate.cleared ? ', and it cleared the answer.' : '.')
                + (mv ? `\nIts panel: ${mv[1]} of ${mv[2]} models upheld the answer.`
                      : raw ? `\nIts panel voted ${raw}.` : '')
                + '\nA judge is one CHECK; the models are the panel voting inside it.')}"
                style="color:var(--text-muted);background:rgba(127,127,127,.12);border:1px solid var(--border-secondary)"
                >${esc('gate asked a judge' + (r.gate.cleared ? ' · cleared' : '') + v)}</span>`;
        }
        // Otherwise the chip speaks only when the panel FLAGGED something — so its own
        // number always explains its presence (Rule 3.2). A unanimous panel earns no
        // chip: the diamonds in the strip already say a judge ran and agreed, and a chip
        // on almost every row is weight without information.
        if (!ruled.length || upheld === ruled.length) return '';
        // …and it is silent when the Rule 3.0c mark beside it already accounts for every
        // flagged judge. Each `judge_flagged` entry IS a refining judge in danger, so if
        // the counts match, the two chips are one ruling stated twice (Rule 3.0) — and
        // the 3.0c mark is the richer statement, because it names the disagreement.
        const _explained = ((r.judge_flagged || []).length);
        if (_explained && (ruled.length - upheld) === _explained) return '';
        // Name the FINDING, not the ratio. `judged 2/3 upheld` carried a denominator the
        // reader cannot see — 4 judges ran, one abstained, so the 3 is "judges that
        // ruled" — and it was asked about three times before the label was the suspect
        // rather than the reader. The chip only ever appears BECAUSE the panel flagged
        // something (Rule 3.2), so that is the fact to state; the full tally, the
        // abstentions and each judge's ruling stay one hover away, where a denominator
        // can be explained rather than implied.
        const flagged = ruled.length - upheld;
        const label = `${flagged} judge${flagged === 1 ? '' : 's'} flagged this`;   // checks, not models
        const lines = vs.map((v) => `${dimLabel(v.dimension)} — ${state(v)}`);
        // The summary must count what the reader can COUNT below it. "2 of the 3 judges
        // that ruled" set a denominator (ruled, excluding abstentions) against a list of
        // FOUR named judges — a fraction contradicting the lines beneath it, asked about
        // three times. State the composition instead: every judge, in the list's own
        // terms, with no fraction to decode.
        const _abst = vs.length - ruled.length;
        const _head = vs.length
            ? `${vs.length} judge${vs.length === 1 ? '' : 's'} ran on this turn: `
              + [`${upheld} upheld the answer`,
                 (ruled.length - upheld) ? `${ruled.length - upheld} flagged it` : '',
                 _abst ? `${_abst} abstained` : ''].filter(Boolean).join(', ') + '.'
            : '';
        if (escalated) {
            const _gm = /(\d+)\s*\/\s*(\d+)/.exec(String(r.gate.judge_votes || ''));
            lines.push(`A gate consulted ONE judge DURING the turn`
                + `${_gm ? `; ${_gm[1]} of ${_gm[2]} models on its panel upheld the answer`
                    : r.gate.judge_votes ? `; its panel voted ${r.gate.judge_votes}` : ''}`
                + `${r.gate.cleared ? ' and the answer was cleared' : ''} — that ruling could have stopped the answer.`);
            lines.push('Every ruling listed above is the scoring layer\'s, taken after the answer shipped.');
        }
        // The abstention REASON now rides its own line above, so repeating the count
        // here would be one slot restating another (Rule 3.0).
        // The vocabulary anchor, because "judges" has been read as panel members three
        // times: a judge is one CHECK, and its models vote inside it.
        lines.push('A judge is one check; the models on its panel vote inside it.');
        return `<span class="ap-chip u-tip" data-tip="${escAttr(
            (_head ? _head + '\n' : '') + lines.join('\n'))}"
            style="color:var(--text-muted);background:rgba(127,127,127,.12);border:1px solid var(--border-secondary)"
            >${esc(label)}</span>`;
    }

    /** The dimensions that decided the verdict, named. */
    function _runFailedDims(r) {
        const dims = (r && r.failed_dimensions) || [];
        if (!dims.length) return '';
        const label = (d) => (window.QualityDimensions && window.QualityDimensions.label
            ? window.QualityDimensions.label(d) : String(d).replace(/_/g, ' '));
        return `<span class="text-[11px]" style="color:var(--text-muted)"> · failed: `
             + `${esc(dims.map(label).join(', '))}</span>`;
    }

    function verdictChips(verdicts, ctxLabel, cls, run) {
        if (!verdicts || !verdicts.length) return `<div class="text-xs" style="color: var(--text-muted);">No applicable scorers.</div>`;
        // Scope the Assurance Guide to THIS run/turn: stamp its verdicts + live bands (+ score,
        // threshold, abstention reason, and the IFOC class) as context on the chip container, so
        // clicking any chip (or the Guide pill) opens the guide filtered to exactly these
        // dimensions with their status — and can distinguish Not applicable / Not set / Abstained.
        const ctxAttr = QD.contextAttrs
            ? QD.contextAttrs(verdicts.map((v) => ({ key: v.dimension, band: bandOfVerdict(v), score: v.score, threshold: v.threshold, explanation: v.explanation })), ctxLabel || 'this turn', cls)
            : '';
        // Grouped BY STATION (the partition; was by tier — "by function" — which cut
        // across stations: one Reliability row mixed BOUNDED, GROUNDED and JUDGED
        // checks). Within a station, tier order keeps the familiar reading rhythm.
        // Each header carries the station's worst-of verdict dot and routes to its
        // Guide entry through the one glossary channel (`data-dim`).
        const stGroups = {};
        verdicts.forEach((v) => { const st = _dimStageOf(v.dimension); (stGroups[st] = stGroups[st] || []).push(v); });
        const chip = (v) => {
            const b = bandOfVerdict(v);
            const c = BAND_C[b];
            const judged = isJudgeVerdict(v), inPath = isInPathJudge(v, run);
            const wasCleared = ((run && run.disputed) || []).includes(v.dimension);
            const wasFlagged = ((run && run.judge_flagged) || []).includes(v.dimension);
            const split = _panelSplit(v)
                ? ` ·split ${_panelVotes(v).length - _panelFails(v)}/${_panelVotes(v).length} models` : '';
            const kindTag = (judged ? (inPath ? ' ·judge · gate-consulted' : ' ·judge')
                : v.kind === 'metric' ? ' ·metric' : '') + split + (wasCleared ? ' ·judge-cleared' : wasFlagged ? ' ·judge-flagged' : '');
            const sc = (v.score != null && v.kind === 'metric') ? ' ' + fmtMetricVal(v.dimension, v.score) : '';
            const ev = (INLINE_EV.has(v.dimension) && Array.isArray(v.evidence) && v.evidence.length)
                ? `<span style="opacity:.7;font-weight:400"> · ${esc(v.evidence.join(' · '))}</span>` : '';
            const bg = b === 'na' ? 'rgba(127,127,127,.12)' : `color-mix(in srgb, ${c} 10%, transparent)`;
            const bd = b === 'na' ? 'var(--border-secondary)' : `color-mix(in srgb, ${c} 33%, transparent)`;
            // Judged-vs-deterministic rides the ·judge/·metric TEXT tag alone
            // (PROP-002): dashes belong to the absence family on every trust
            // surface, so a dashed coloured chip contradicted the grammar.
            // Dash and dot are different strokes: a sparse dash is the absence family
            // on every trust surface, a dot is the kind channel. Never the sole carrier
            // — the ·judge tag rides beside it.
            return `<span class="ap-chip" ${dimAttrs(v.dimension, _clearedNote(v, new Set((run && run.disputed) || [])))} style="color:${c};background:${bg};border:1px ${judged ? 'dotted' : 'solid'} ${bd}">
                <span style="font-weight:700">${BAND_ICON[b]}</span>${esc(dimLabel(v.dimension))}${esc(kindTag)}${esc(sc)}${ev}</span>`;
        };
        const _RANK_B = { danger: 3, review: 2, healthy: 1, na: 0 };
        const blocks = _stageKeysInOrder(stGroups).map((st) => {
            const vs = stGroups[st].slice().sort((a, b) => (_stageIdxOf(a.dimension) - _stageIdxOf(b.dimension))
                || String(a.dimension).localeCompare(String(b.dimension)));
            const worst = vs.reduce((w, v) => {
                const b = bandOfVerdict(v);
                return (_RANK_B[b] || 0) > (_RANK_B[w] || 0) ? b : w;
            }, 'na');
            // The station's worst-of, drawn with the SAME mark as the strip squares
            // above it — one grammar per row. It was an inline 7px solid square, a
            // second private implementation of the same idea and colour-only like the
            // first.
            const dot = worst !== 'na' ? bandMark(worst) : '';
            return `<div class="ap-tier-h" style="display:flex;align-items:center;gap:6px;">
                <span class="u-tip" data-tip="${esc(_stageDescOf(st))}"${st ? ` data-dim="stage_${esc(st)}" role="button" tabindex="0" style="cursor:pointer;"` : ''}>${esc(_stageLabelOf(st))}</span>${dot}
            </div><div class="flex flex-wrap gap-1.5">${vs.map(chip).join('')}</div>`;
        }).join('');
        return `<div ${ctxAttr}>${blocks}</div>`;
    }

    // ── Coordination Trace Tree + waterfall (master/slave) ────────────────────────
    function waterfall(run) {
        const kids = run.experts || [];
        const timed = kids.filter((k) => k.started_at && k.completed_at && k.completed_at > k.started_at);
        let segments;
        if (timed.length === kids.length && kids.length) {
            // real timing → absolute positioning (shows parallel overlap honestly)
            const t0 = Math.min(...timed.map((k) => k.started_at));
            const t1 = Math.max(...timed.map((k) => k.completed_at));
            const span = Math.max(0.001, t1 - t0);
            segments = kids.map((k) => ({ k, left: ((k.started_at - t0) / span) * 100, w: ((k.completed_at - k.started_at) / span) * 100 }));
        } else {
            // no reliable timing → sequential layout by invocation order, sized by duration
            const durs = kids.map((k) => Math.max(1, k.duration_ms || k.latency_ms || 1));
            const total = durs.reduce((s, d) => s + d, 0) || 1;
            let acc = 0;
            segments = kids.map((k, i) => { const l = acc / total * 100; acc += durs[i]; return { k, left: l, w: durs[i] / total * 100 }; });
        }
        const bars = segments.map(({ k, left, w }) => {
            const b = runBand(k);
            return `<div class="ap-wf-bar u-tip" data-tip="@${esc(k.profile_tag)} · ${fmtMs(k.duration_ms || k.latency_ms)}"
                style="left:${left.toFixed(1)}%;width:${Math.max(2, w).toFixed(1)}%;background:linear-gradient(90deg,${BAND_C[b]},${BAND_C[b]}cc)"></div>`;
        }).join('');
        const par = timed.length === kids.length && kids.length > 1
            && segments.some((a) => segments.some((b) => a !== b && a.left < b.left + b.w - 0.5 && b.left < a.left + a.w - 0.5));
        return `<div class="ap-wf-track">${bars}</div>
            <div class="text-[10px] mt-1" style="color:var(--text-muted)">${par ? 'experts ran in parallel' : 'experts ran sequentially'} · total ${fmtMs(run.latency_ms)}</div>`;
    }

    // Per-node trust glyph in the coordination tree — compositional trust made
    // operative in the trace: each expert (and the coordinator) shows its own
    // integrity seal + behavioral state, so a coordinator's answer visibly
    // inherits its weakest link. Reads the already-loaded trust data; renders
    // nothing when neither source is available (honest absence).
    /** The roster's exact mini ring, for a tree node — or '' when the tag is not an
     *  agent this roster can see. ONE derivation (`_circleStationsFor` on the SAME roster
     *  entry the roster card used), so the tree ring and the roster ring can never
     *  disagree about one agent. A `{tag}` stub was rejected here: `_circleStationsFor`
     *  reads `agent.online` for the quality-driven stations, and a stub would render a
     *  quieter ring in the tree than in the roster — same fact, two answers. */
    function _stationRing(tag, size) {
        if (!tag || !window.TrustCircle || !window.TrustCircle.glyph) return '';
        const a = (_model && _model.agents || []).find(
            (x) => x.tag === tag && !x.kind && x.isSelf !== false);
        if (!a) return '';   // honest absence — never an all-na ring about an unseen agent
        // W3 (trust trip): this glyph renders on surfaces (roster, tree) that carry
        // NO lens chip and NO "since" disclosure — so it must never be lens-scoped.
        // Force the whole record for the derivation, then restore the ring's lens.
        const keepLens = _circleLens;
        let states, verdict = '';
        try {
            _circleLens = 'total';
            const sts = _circleStationsFor(a);
            states = sts.map((s) => s.state);
            // W2 — the tooltip is the same verdict the ring's panel leads with.
            verdict = _verdictLine(sts, { label: '@' + a.tag, kind: 'agent' }).text;
        }
        finally { _circleLens = keepLens; }
        return `<span class="tc-mini-host u-tip" data-circle-key="${esc(a.key || a.tag)}"
            data-tip="${esc(verdict || 'The Trust Circle — this agent\'s closed loop.')}\nClick to open the Trust Circle."
            role="button" tabindex="0">${window.TrustCircle.glyph(
                states,
                { size: size || 16, label: 'Open the Trust Circle for @' + tag })}</span>`;
    }

    function _treeTrustGlyph(tag) {
        if (!tag) return '';
        let integ = null;
        (_trustAgentsFull || []).some((a) => { if (a.tag === tag) { integ = a.state; return true; } return false; });
        const beh = _tbAgent(null, tag);   // TC-35: key-shape aware
        const comp = beh && beh.composite && beh.composite.state;
        if (!integ && !comp) return '';
        // TC-46: four distinct integrity states — a publisher-signed (deployment_unattested)
        // coordinator must not render identically to a never-signed one, and the tooltip must
        // carry the human label, not the raw `deployment_unattested` wire token.
        const sealColor = integ === 'trusted' ? 'var(--trust-seal-trusted, #34d399)'
            : integ === 'changed' ? 'var(--trust-seal-changed, #f59e0b)'
            : integ === 'deployment_unattested' ? 'var(--trust-seal-unattested, #38bdf8)'
            : 'var(--trust-seal-unsigned, #94a3b8)';
        const sealLabel = _integLabel(integ);
        const compChip = comp
            ? `<span class="trust-chip ${['trusted', 'proven'].includes(comp) ? 'tc-pass' : (comp === 'watch' ? 'tc-warn' : (comp === 'at_risk' ? 'tc-danger' : ''))}" style="font-size:.56rem;padding:0 5px;">${esc(TRUST_STATE_LABEL[comp] || comp)}</span>` : '';
        const _shield = 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z';
        const seal = integ === 'trusted'
            ? `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="${sealColor}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="${_shield}"/><path d="M9 12l2 2 4-4"/></svg>`
            : integ === 'changed'
                ? `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="${sealColor}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="${_shield}"/><path d="M12 8v4M12 15h.01"/></svg>`
                : integ === 'deployment_unattested'
                    ? `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="${sealColor}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="${_shield}"/><path d="M9 12l2 2 4-4" stroke-dasharray="2.6 2.4"/></svg>`
                    : `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="${sealColor}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="${_shield}"/></svg>`;
        return `<span class="u-tip inline-flex items-center gap-1" data-tip="Trust of @${esc(tag)}\nintegrity: ${esc(sealLabel)}${comp ? '\nbehavioral: ' + esc(TRUST_STATE_LABEL[comp] || comp) : ''}">${seal}${compChip}</span>`;
    }

    function nodeRow(run, isChild) {
        const cls = ifoc(run.profile_class);
        const b = runBand(run);
        const tag = run.profile_tag || '—';
        const roleTag = isChild ? `<span style="color:${cls.color};font-weight:600">@${esc(tag)}</span> <span class="text-[10px]" style="color:var(--text-muted)">${esc(cls.label)} · expert</span>`
            : `<span style="color:${cls.color};font-weight:600">@${esc(tag)}</span> <span class="text-[10px]" style="color:var(--text-muted)">${esc(cls.label)}</span>`;
        const syn = run.synthetic ? ` <span class="text-[10px]" style="color:var(--text-muted)">(not scored)</span>` : '';
        return `<div class="ap-node ${isChild ? 'child' : ''}">
            <div class="flex items-center gap-2 flex-wrap">
                ${bandMark(b)}${roleTag}${_stationRing(run.profile_tag, 16)}${_treeTrustGlyph(run.profile_tag)}${syn}
                <span class="ml-auto text-[11px]" style="color:var(--text-muted)">${fmtCost(run.cost)} · ${fmtMs(run.latency_ms)} · ${fmtTok(run.tokens)} tok</span>
                ${run.session_id ? `<span class="ap-link" data-ap-open-session="${esc(run.session_id)}" data-ap-turn="${esc(run.turn_id || 1)}">open →</span>` : ''}
            </div>
            ${run.verdicts && run.verdicts.length ? `<div class="mt-1.5">${verdictChips(run.verdicts, '@' + esc(tag) + (run.turn_id ? ' · turn ' + run.turn_id : ''), run.profile_class, run)}</div>` : (run.synthetic ? `<div class="text-[11px] mt-1" style="color:var(--text-muted)">Expert ${run.expert_status === 'success' ? 'succeeded' : 'ran'}; enable live scoring to capture its quality.</div>` : '')}
        </div>`;
    }

    function traceTree(run) {
        const kids = run.experts || [];
        const total = run.rollup_cost != null
            ? `<div class="text-[11px] mt-2" style="color:var(--text-muted)">turn total <span style="color:var(--text-primary);font-weight:600">${fmtCost(run.rollup_cost)}</span> · ${fmtTok(run.rollup_tokens)} tok across ${kids.length} expert${kids.length === 1 ? '' : 's'} (self-cost shown per node)</div>` : '';
        return `<div class="ap-tier-h">Coordination trace</div>
            <div class="ap-tree">
                ${nodeRow(run, false)}
                ${kids.length ? waterfall(run) : ''}
                ${kids.map((k) => nodeRow(k, true)).join('')}
            </div>${total}`;
    }

    // ── Runs Ledger ───────────────────────────────────────────────────────────────
    const _runsByTag = {};
    const _runFilter = { band: 'all', inv: 'all' };
    const _openRuns = new Set();
    // Aggregate agent-detail dimension context ({items:[{key,band}], label}), stashed at
    // detail render so renderRunsLedger can merge in admin fail lines from run verdicts.
    let _agDimCtx = null;

    async function loadRunsLedger(tag) {
        const host = $('agent-runs-ledger');
        if (!host) return;
        host.innerHTML = `<div class="ap-tier-h">Runs</div><div class="text-xs" style="color:var(--text-muted)">Loading runs…</div>`;
        try {
            const rk = splitKey(tag);   // cache stays keyed by the full roster key
            if (!_runsByTag[tag]) _runsByTag[tag] = await api(`/evals/agents/${encodeURIComponent(rk.tag)}/runs?scope=${_scope}${rk.user ? '&user=' + encodeURIComponent(rk.user) : ''}&runs=100`);
            if (_expandedTag !== tag) return;
            renderRunsLedger(tag);
        } catch (e) {
            // A failed load is a failure, never an empty state: "No scored runs yet." here read as
            // a fact about the agent while the real cause was a dead session (401) or a server
            // error. The empty state belongs to renderRunsLedger, which sees the actual rows.
            const h = $('agent-runs-ledger');
            if (h) h.innerHTML = `<div class="ap-tier-h">Runs</div><div class="text-xs" style="color:var(--trust-warn, var(--text-muted))">Runs could not be loaded — ${esc((e && e.message) || String(e))}.</div>`;
        }
    }

    function runKey(r) { return `${r.session_id}:${r.turn_id}`; }

    function renderRunsLedger(tag) {
        const host = $('agent-runs-ledger');
        if (!host) return;
        const data = _runsByTag[tag] || { runs: [] };
        // The leg filter (trust trip): scope the ledger to runs since this agent's
        // last reset. Runs carry `ts` in epoch SECONDS; the marker's `since` is ISO.
        const _la = (_model.agents || []).find((x) => (x.key || x.tag) === tag);
        const trip = _la && _la.kind !== 'flow' && _la.online && _la.online.trip;
        const legOn = !!(trip && _rosterLegOn.has(tag));
        const sinceSec = trip && trip.since ? Date.parse(trip.since) / 1000 : null;
        let runs = data.runs || [];
        if (legOn && sinceSec) runs = runs.filter((r) => (r.ts || 0) >= sinceSec);
        const _legCount = runs.length;   // the leg's size, before the band/invocation filters
        if (_runFilter.band !== 'all') runs = runs.filter((r) => runBand(r) === _runFilter.band);
        if (_runFilter.inv !== 'all') runs = runs.filter((r) => (r.invocation || 'direct') === _runFilter.inv);
        // The intervention axis gets its own filter: "which turns were altered" is a
        // different question from "which turns scored badly", and on real data the two
        // sets barely overlap (Rule 3bb). Without it the answer is a scan of 18 rows.
        if (_runFilter.act === 'acted') runs = runs.filter((r) => !!_actedTag(r));
        const fpill = (label, group, val) => {
            const on = _runFilter[group] === val;
            const c = group === 'band' && val !== 'all' ? BAND_C[val] : 'var(--teradata-orange)';
            return `<button class="ap-cmp-metric ${on ? 'on' : ''}" data-ap-runfilter="${group}:${val}" ${on ? `style="color:${c};border-color:${c};background:${c}1a"` : ''}>${esc(label)}</button>`;
        };
        // Counts ON the band pills. The ledger's tally and the card's "N failed" answer
        // DIFFERENT questions — attention vs convictions — and the counts legitimately
        // differ (a measured-but-advisory failure is danger here and no conviction
        // there). Showing the ledger's own numbers makes that visibly two readings
        // rather than one number contradicting another (which is exactly how an
        // operator met it: "I am confused about the 7 failed — I count more").
        const _bandPool = (legOn && sinceSec)
            ? (data.runs || []).filter((r) => (r.ts || 0) >= sinceSec)
            : (data.runs || []);
        const _bandN = _bandPool.reduce((m, r) => {
            const b = runBand(r); m[b] = (m[b] || 0) + 1; return m;
        }, {});
        const _bandLabel = (label, val) => val === 'all'
            ? `${label} ${_bandPool.length}` : `${label} ${_bandN[val] || 0}`;
        // Every dimension this agent has produced, in the strip's own order. Taken from
        // the unfiltered runs so applying a filter cannot change the grid.
        const _allDims = (() => {
            const seen = new Set();
            (data.runs || []).forEach((r) => (r.verdicts || []).forEach((v) => seen.add(v.dimension)));
            return [...seen].sort((a, b) =>
                (_stageIdxOf(a) - _stageIdxOf(b)) || String(a).localeCompare(String(b)));
        })();
        const _actedN = _bandPool.filter((r) => !!_actedTag(r)).length;
        const hasExperts = (data.runs || []).some((r) => r.expert_count);
        // "This leg" is a LENS beside the band FILTERS — offered only where a leg
        // exists (a lens with nothing to select would be a dead control), styled as
        // the leg chip so it never reads as a fourth band. Toggling it re-scopes the
        // WHOLE expanded detail (bars + work mix + this ledger), each section
        // carrying its own disclosure chip (contract §10.2).
        const legPill = trip
            ? `<span style="width:1px;height:16px;background:var(--border-secondary);margin:0 4px"></span>
               <button class="ap-cmp-metric ${legOn ? 'on' : ''} u-tip" data-ap-runleg="1"
                   data-tip="${legOn
                       ? 'Showing this leg only — since ' + esc(_legDate(trip.since) || 'the reset') + '.\nClick to show the whole record.'
                       : 'Scope this agent’s detail (bars, work mix, runs) to the current leg — since ' + esc(_legDate(trip.since) || 'the reset') + '.'}"
                   ${legOn ? 'style="color:#F15F22;border-color:rgba(241,95,34,.5);background:rgba(241,95,34,.1)"' : ''}>This leg</button>`
            : '';
        // A mark with no key is a guess. The strip carries four channels, so it
        // gets a real legend rather than a note about the newest glyph — and every
        // sample is drawn by the SHIPPED `bandMark()`, so the key cannot drift from
        // the marks it explains. OPEN by default: four channels no reader can guess, and
        // a key you have to discover is not a key ("I don't see the legend for the
        // icons" — with the button right there). Hiding it persists, so it teaches once
        // and then gets out of the way.
        const _keyOpen = localStorage.getItem('tda_strip_key') !== '0';
        const _hasJudge = _bandPool.some((r) => (r.verdicts || []).some(isJudgeVerdict));
        const _keyItem = (mark, text) => `<span class="flex items-center gap-1.5">${mark}<span>${esc(text)}</span></span>`;
        // The shape row draws both samples in the SAME state, so the only thing varying
        // is the channel it names — and it draws them in `danger`, because that is where
        // the distinction bites: a failed rule convicts the turn, a flagged judge does
        // not (a model opined). A muted sample would have hidden exactly that.
        const _keyRow = (lead, items) => `<div class="flex flex-wrap items-center gap-x-4 gap-y-1">
            <span style="color:var(--text-muted);min-width:104px">${esc(lead)}</span>${items.join('')}</div>`;
        const _stripKey = `<div id="ap-strip-key" class="text-[10px] mb-3 flex flex-col gap-1.5"
                style="color:var(--text-primary);${_keyOpen ? '' : 'display:none'}">
            ${_keyRow('fill — what it found', [
                _keyItem(bandMark('healthy'), 'passed'),
                _keyItem(bandMark('review'), 'worth a look'),
                _keyItem(bandMark('danger'), 'failed'),
                _keyItem(bandMark('na'), 'did not run'),
            ])}
            ${_hasJudge ? _keyRow('shape — who measured', [
                _keyItem(bandMark('danger'), 'a rule measured it — a failure here fails the turn'),
                _keyItem(bandMark('danger', '', true), 'a model judged it — red if any of its panel voted fail; a flagged judge does not fail the turn on its own'),
            ]) : ''}
            ${_actedN ? _keyRow('a dot on the row', [
                _keyItem('<span class="ap-acted-dot" style="--sq:var(--trust-warn)"></span>', 'a mechanism acted — what was delivered is not what the model wrote'),
                _keyItem('<span class="ap-acted-dot" style="--sq:var(--teradata-orange, #F15F22)"></span>', 'and an act is owed — the original is parked for your decision'),
            ]) : ''}
            ${_hasJudge ? _keyRow('size — what it could do', [
                _keyItem(bandMark('healthy', '', true, true), 'larger — a gate consulted it during the turn; its ruling could withhold the answer'),
                _keyItem(bandMark('healthy', '', true, false), 'plain — sampled after the turn; it reported, and changed nothing'),
            ]) : ''}
            ${_bandPool.some((r) => (r.disputed || []).length || (r.judge_flagged || []).length) ? _keyRow('centre — the refining judge disagreed', [
                _keyItem(bandMark('danger', '', false, false, true), 'green — the rule measured a failure and the judge that refines it cleared the same answer; the conviction is withdrawn'),
                _keyItem(bandMark('healthy', '', false, false, 'flagged'), 'red — the rule passed and that judge disputed it; the measurement stands, and a judge alone does not fail the turn'),
            ]) : ''}
        </div>`;
        const _judgeKey = `<button class="ap-cmp-metric ml-auto" data-ap-stripkey="1"
            aria-expanded="${_keyOpen}">${_keyOpen ? 'Hide key' : 'Key'}</button>`;
        const filters = `<div class="flex flex-wrap items-center gap-1.5 mb-3">
            ${fpill(_bandLabel('All', 'all'), 'band', 'all')}${fpill(_bandLabel('Healthy', 'healthy'), 'band', 'healthy')}${fpill(_bandLabel('Review', 'review'), 'band', 'review')}${fpill(_bandLabel('Danger', 'danger'), 'band', 'danger')}${legPill}
            ${_actedN ? `<span style="width:1px;height:16px;background:var(--border-secondary);margin:0 4px"></span>
                <button class="ap-cmp-metric u-tip ${_runFilter.act === 'acted' ? 'on' : ''}"
                    data-ap-runfilter="act:${_runFilter.act === 'acted' ? 'all' : 'acted'}"
                    data-tip="Show only the turns a mechanism acted on — withheld, altered, refused or parked.\nSeparate from the verdict: a turn every check calls clean can still have been altered."
                    ${_runFilter.act === 'acted' ? 'style="color:var(--trust-warn);border-color:var(--trust-warn);background:color-mix(in srgb, var(--trust-warn) 10%, transparent)"' : ''}
                    >Acted ${_actedN}</button>` : ''}
            ${hasExperts ? `<span style="width:1px;height:16px;background:var(--border-secondary);margin:0 4px"></span>${fpill('Any', 'inv', 'all')}${fpill('Direct', 'inv', 'direct')}${fpill('As expert', 'inv', 'expert')}` : ''}
        ${_judgeKey}
        </div>${_stripKey}`;
        const rowsHtml = runs.length ? runs.map((r) => {
            const b = runBand(r); const key = runKey(r); const open = _openRuns.has(key);
            // Rule 3.0c made visible: the coarse check flagged this turn and the judge
            // that refines it cleared it. Shown, never swallowed — it is the reason the
            // row is not a conviction, and a reader owed that reason.
            const _disp = (label, tip) => `<span class="ap-chip u-tip" data-tip="${escAttr(tip)}" style="color:var(--trust-warn);background:color-mix(in srgb, var(--trust-warn) 10%, transparent);border:1px solid color-mix(in srgb, var(--trust-warn) 33%, transparent)">${esc(label)}</span>`;
            // Named for their EFFECT, not with words another chip already uses.
            // `judge-cleared` / `judge-flagged` sat beside `1 judge flagged this` and
            // `gate asked a judge · cleared` — three chips sharing two verbs for three
            // different facts, and on one row two of them came from the SAME ruling.
            const dispTag = (r.disputed || []).length
                ? _disp('not counted as failed', 'A coarse check measured a failure and the judge that refines it cleared the same answer (Rule 3.0c).\nShown because it is why this row is not counted as a failure.\nThe measurement itself still stands.')
                : (r.judge_flagged || []).length
                ? _disp('passed, but disputed', 'A coarse check passed this turn and the judge that refines it disagreed (Rule 3.0c).\nShown because a green measurement beside a red judge would otherwise look like two unrelated readings.\nIt does not fail the turn: a judge alone never convicts.')
                : '';
            const invTag = r.invocation === 'expert' ? `<span class="ap-chip" style="color:var(--text-muted);background:rgba(127,127,127,.12);border:1px solid var(--border-secondary)">expert</span>` : '';
            const exp = r.expert_count ? `<span class="text-[10px]" style="color:var(--text-muted)">${r.expert_count} expert${r.expert_count === 1 ? '' : 's'}</span>` : '';
            const rowCtx = (QD.contextAttrs && r.verdicts && r.verdicts.length)
                ? QD.contextAttrs(
                    r.verdicts.map((v) => ({ key: v.dimension, band: bandOfVerdict(v), score: v.score, threshold: v.threshold, explanation: v.explanation })),
                    'turn ' + (r.turn_id || '—'),
                    r.profile_class || (_agDimCtx && _agDimCtx.cls))  // pass class + explanation so the guide classifies Not-applicable + shows abstention reasons, matching the chip context
                : '';
            return `<div class="ap-run ${open ? 'open' : ''}" data-ap-runkey="${esc(key)}" ${rowCtx}>
                <div class="ap-run-hd" tabindex="0" role="button" aria-expanded="${open}">
                    <span class="ap-run-fixed text-xs font-medium" style="color:${BAND_C[b]}">${b === 'na' ? 'unscored' : b}</span>
                    <span class="ap-run-fixed text-[11px]" style="color:var(--text-muted);min-width:52px">${esc(relTime(r.ts))}</span>
                    <span class="ap-run-chips">${_actedTag(r)}${dispTag}${_judgeTag(r)}${_voteTag(r)}${invTag}${exp}</span>
                    <span class="ap-strip">${dimStrip(r.verdicts, r, _allDims)}</span>
                    <span class="ap-run-fixed text-[11px] tabular-nums" style="color:var(--text-muted);min-width:62px;text-align:right">${fmtCost(r.cost)}</span>
                    <span class="ap-run-fixed text-[11px] tabular-nums" style="color:var(--text-muted);min-width:42px;text-align:right">${fmtMs(r.latency_ms)}</span>
                    <svg class="ap-chev h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7"/></svg>
                </div>
                ${open ? `<div class="ap-body">
                    <div class="flex items-center justify-between mb-1">
                        <span class="text-[11px]" style="color:var(--text-muted)">turn ${esc(r.turn_id || '—')} · ${
                            // "deterministic" described the SCORING layer, and was read as
                            // a claim about the turn — on a turn two judges had ruled on.
                            // `gate.escalated` records the RUNTIME adjudication (a gate
                            // consulted a judge); `eval.judged` records the sampled judge
                            // layer. Either means a model opined on this turn.
                            (r.judged || (r.gate && r.gate.escalated)) ? 'judged' : 'deterministic scoring'
                        }</span>${_runFailedDims(r)}
                        ${r.session_id ? `<span class="ap-link" data-ap-open-session="${esc(r.session_id)}" data-ap-turn="${esc(r.turn_id || 1)}">open in Sessions →</span>` : ''}
                    </div>
                    ${_runGateNote(r)}
                    ${verdictChips(r.verdicts, 'turn ' + (r.turn_id || '—'), r.profile_class || (_agDimCtx && _agDimCtx.cls), r)}
                    ${r.expert_count ? traceTree(r) : ''}
                </div>` : ''}
            </div>`;
        }).join('') : `<div class="text-xs py-3" style="color:var(--text-muted)">No runs match this filter.</div>`;
        host.innerHTML = `<div class="flex items-center justify-between"><div class="ap-tier-h" style="margin-top:0">Runs · all scores per run</div>
            <span class="text-[11px]" style="color:var(--text-muted)">${legOn
                ? `${_legCount} this leg · ${data.count || (data.runs || []).length} scored`
                : `${data.count || (data.runs || []).length} scored`}</span></div>
            ${filters}<div class="ap-fade">${rowsHtml}</div>`;

        // Admin thresholds are global (identical across a class's turns), so lift each
        // dimension's fail line from the loaded run verdicts and merge it into the aggregate
        // "Live Pass Rate" context — enabling the guide's "Admin thresholds" toggle in the
        // agent view too (fail lines only; no per-turn measured value at the aggregate level).
        const cont = $('agent-dim-ctx');
        if (cont && _agDimCtx && QD.setContext) {
            const thr = {};
            (data.runs || []).forEach((r) => (r.verdicts || []).forEach((v) => {
                if (v.threshold != null && thr[v.dimension] == null) thr[v.dimension] = v.threshold;
            }));
            const merged = _agDimCtx.items.map((it) => (thr[it.key] != null ? { ...it, threshold: thr[it.key] } : it));
            QD.setContext(cont, merged, _agDimCtx.label, _agDimCtx.cls);
        }
    }

    async function openSessionTrace(sessionId, turnId) {
        try {
            const { handleLoadSession } = await import('./sessionManagement.js?v=3.209');
            const btn = document.getElementById('view-switch-conversation');
            if (btn) btn.click();
            await new Promise((r) => setTimeout(r, 220));
            await handleLoadSession(sessionId);
            // Best-effort: open the turn's trajectory + quality overlay.
            await new Promise((r) => setTimeout(r, 350));
            const ev = await import('../eventHandlers.js?v=4.214').catch(() => null);
            if (ev && ev.handleReloadPlanClick) ev.handleReloadPlanClick(sessionId, parseInt(turnId, 10));
        } catch (e) {
            if (window.showAppBanner) window.showAppBanner('Could not open session ' + sessionId, 'error');
        }
    }

    // ── Compare (selective agents × metrics) ──────────────────────────────────────
    let _compareMode = false;
    const _compareSet = new Set();
    const COMPARE_METRICS = [
        { key: 'pass_rate', label: 'Pass rate', higher: true, fmt: (v) => v == null ? '—' : Math.round(v * 100) + '%', norm: (v) => v == null ? 0 : v },
        { key: 'avg_tasks', label: 'Avg tasks/turn', higher: false, fmt: (v) => v ? v.toFixed(1) : '—' },
        { key: 'avg_tokens', label: 'Avg tokens', higher: false, fmt: (v) => v ? Number(v).toLocaleString() : '—' },
        { key: 'avg_cost', label: 'Avg cost', higher: false, fmt: (v) => v ? '$' + Number(v).toFixed(4) : '—' },
        { key: 'avg_latency_ms', label: 'Avg latency', higher: false, fmt: (v) => fmtMs(v) },
        { key: 'turns', label: 'Turns scored', higher: true, fmt: (v) => v ? String(v) : '—' },
    ];
    const _compareMetricsOn = new Set(['pass_rate', 'avg_tasks', 'avg_tokens', 'avg_cost']);

    function toggleCompareMode() {
        _compareMode = !_compareMode;
        if (!_compareMode) _compareSet.clear();
        applyRosterFilter();
        renderCompareBar();
    }

    function renderCompareBar() {
        let bar = $('agent-compare-bar');
        if (!_compareMode || _compareSet.size < 1) { if (bar) bar.remove(); return; }
        if (!bar) {
            bar = document.createElement('div');
            bar.id = 'agent-compare-bar';
            bar.style.cssText = 'position:sticky;bottom:14px;z-index:30;margin:10px auto 0;max-width:520px;';
            const rg = $('agent-roster-grid');
            if (rg && rg.parentElement) rg.parentElement.appendChild(bar);
        }
        const tags = [..._compareSet];
        bar.innerHTML = `<div class="ap-fade flex items-center gap-3 px-4 py-2.5 rounded-full"
            style="background:var(--card-bg);border:1px solid var(--teradata-orange);box-shadow:0 6px 24px rgba(0,0,0,.22)">
            <span class="text-xs" style="color:var(--text-primary)">${tags.length} selected: ${tags.slice(0, 4).map((t) => '@' + esc(splitKey(t).tag)).join(' · ')}${tags.length > 4 ? ` +${tags.length - 4}` : ''}</span>
            <button data-agent-action="open-compare" class="ml-auto text-xs font-semibold px-3 py-1.5 rounded-full" ${tags.length < 2 ? 'disabled' : ''}
                style="background:${tags.length < 2 ? 'var(--border-secondary)' : 'var(--teradata-orange)'};color:#fff;border:none;${tags.length < 2 ? 'opacity:.6;cursor:not-allowed' : ''}">Compare →</button>
            <button data-agent-action="compare-clear" class="text-xs px-2 py-1 rounded-full" style="color:var(--text-muted);border:1px solid var(--border-secondary)">✕</button>
        </div>`;
    }

    function renderCompare() {
        // Compare is a roster-section mode: render INTO that section (never the
        // root — the root holds all the always-mounted section containers).
        const r = $('agent-section-roster') || root();
        if (!r) return;
        showSection('roster');
        const agents = [..._compareSet].map((t) => _model.agents.find((a) => (a.key || a.tag) === t)).filter((a) => a && a.online);
        if (agents.length < 2) { window.showAppBanner && window.showAppBanner('Pick at least 2 evaluated agents to compare.', 'info'); return; }
        const COLORS = ['#3b82f6', '#F15F22', '#10b981', '#a855f7', '#f59e0b', '#ef4444'];  // hex-palette: compare-chart SERIES colours (per-agent identity, not state)
        const colorOf = (i) => COLORS[i % COLORS.length];

        // metric picker
        const picker = `<div class="flex flex-wrap items-center gap-1.5 mb-4">
            <span class="text-xs mr-1" style="color:var(--text-muted)">Metrics</span>
            ${COMPARE_METRICS.map((m) => `<button class="ap-cmp-metric ${_compareMetricsOn.has(m.key) ? 'on' : ''}" data-ap-cmpmetric="${m.key}">${esc(m.label)}</button>`).join('')}</div>`;

        // legend
        const legend = `<div class="flex flex-wrap gap-3 mb-4">${agents.map((a, i) => {
            const c = ifoc(a.type);
            return `<span class="inline-flex items-center gap-1.5 text-xs" style="color:var(--text-primary)">
                <span style="width:10px;height:10px;border-radius:3px;background:${colorOf(i)};display:inline-block"></span>@${esc(a.tag)}${a.isSelf === false && a.username ? ` <span style="color:var(--text-muted);font-weight:400">· ${esc(a.username)}</span>` : ''}
                <span style="color:var(--text-muted)">${esc(c.label)}${a.kind === 'flow' ? '' : ''}</span></span>`;
        }).join('')}</div>`;

        // selective metric comparison — grouped horizontal bars per chosen metric
        const metricBlocks = COMPARE_METRICS.filter((m) => _compareMetricsOn.has(m.key)).map((m) => {
            const vals = agents.map((a) => (a.online || {})[m.key]);
            const nums = vals.map((v) => Number(v) || 0);
            const max = Math.max(...nums, m.key === 'pass_rate' ? 1 : 0.0001);
            // Best value. For lower-is-better, ignore agents missing the metric (0) — the old
            // `.concat(nums)` re-added those zeros, forcing best=0 so no agent was ever starred.
            const _nz = nums.filter((x) => x > 0);
            const best = m.higher ? Math.max(...nums) : (_nz.length ? Math.min(..._nz) : 0);
            const rows = agents.map((a, i) => {
                const v = (a.online || {})[m.key]; const num = Number(v) || 0;
                const w = max > 0 ? (num / max) * 100 : 0;
                const isBest = num === best && num > 0 && agents.length > 1;
                return `<div class="flex items-center gap-2 mb-1.5">
                    <span class="text-[11px] w-24 truncate text-right u-tip u-tip-left" style="color:var(--text-muted)" data-tip="@${esc(a.tag)}${a.isSelf === false && a.username ? ' · ' + esc(a.username) : ''}">@${esc(a.tag)}</span>
                    <div class="flex-1 rounded-md" style="background:var(--hover-bg-strong,rgba(127,127,127,.12))">
                        <div class="ap-gbar" style="width:${Math.max(2, w).toFixed(1)}%;background:linear-gradient(90deg,${colorOf(i)},${colorOf(i)}bb)"></div></div>
                    <span class="text-[11px] tabular-nums" style="min-width:74px;text-align:right;color:${isBest ? 'var(--trust-pass)' : 'var(--text-primary)'};font-weight:${isBest ? 700 : 400}">${m.fmt(v)}${isBest ? ' <svg width="9" height="9" viewBox="0 0 24 24" fill="currentColor" style="display:inline-block;vertical-align:-1px"><path d="M12 2l2.9 6.9 7.1.6-5.4 4.7 1.6 7-6.2-3.7-6.2 3.7 1.6-7-5.4-4.7 7.1-.6z"/></svg>' : ''}</span>
                </div>`;
            }).join('');
            return `<div class="mb-4"><div class="text-xs font-medium mb-2" style="color:var(--text-primary)">${esc(m.label)}
                <span class="text-[10px]" style="color:var(--text-muted)">· ${m.higher ? 'higher is better' : 'lower is better'}</span></div>${rows}</div>`;
        }).join('') || `<div class="text-xs" style="color:var(--text-muted)">Select metrics above to compare.</div>`;

        // dimension heatmap — agents × all dimensions present, columns in STATION
        // order (the partition) with tier order inside a station, so the heatmap
        // scans the same way the ring, the chips and the band-strip do.
        const dimSet = new Set();
        agents.forEach((a) => Object.keys((a.online || {}).dimensions || {}).forEach((d) => dimSet.add(d)));
        const dims = [...dimSet].sort((x, y) =>
            (_stageIdxOf(x) - _stageIdxOf(y)) || x.localeCompare(y));
        const heat = dims.length ? `<div class="ap-tier-h" style="margin-top:0;display:flex;align-items:center;justify-content:space-between;gap:8px"><span>Quality heatmap · pass rate by dimension</span>${guideBtn('Guide')}</div>
            <div style="overflow-x:auto"><table style="border-collapse:separate;border-spacing:4px"><thead><tr>
            <th></th>${dims.map((d) => `<th class="text-[9px] font-medium" style="color:var(--text-muted);writing-mode:vertical-rl;transform:rotate(180deg);height:64px;white-space:nowrap" ${dimAttrs(d, null, false)}>${esc(dimLabel(d))}${isJudgeDim(d) ? esc(' ·judge') : ''}</th>`).join('')}</tr></thead>
            <tbody>${agents.map((a, i) => `<tr>
                <td class="text-[11px] pr-2 text-right" style="color:${colorOf(i)};font-weight:600;white-space:nowrap">@${esc(a.tag)}</td>
                ${dims.map((d) => {
                    const pr = ((a.online || {}).dimensions || {})[d];
                    if (pr == null) return `<td><div class="ap-heat-cell" style="background:rgba(127,127,127,.08);color:var(--text-muted)" ${dimAttrs(d, '@' + a.tag + ': n/a', false)}>–</div></td>`;
                    const cl = healthClassFor(pr);
                    // Surface the denominator: an amber ring + "· n turn(s)" in the tooltip
                    // marks a cell whose rate rests on ≤2 decided verdicts (typically a
                    // sampled judge dim) so it isn't read as a solid signal.
                    const nc = ((a.online || {}).dimension_counts || {})[d];
                    const tentative = (nc != null && nc <= LOW_SAMPLE);
                    const note = nc != null ? ' · ' + nc + ' turn' + (nc === 1 ? '' : 's') : '';
                    const ring = tentative ? ';box-shadow:inset 0 0 0 1.5px var(--trust-warn)' : '';
                    return `<td><div class="ap-heat-cell" ${dimAttrs(d, '@' + a.tag + ': ' + Math.round(pr * 100) + '%' + note, false)} style="background:color-mix(in srgb, ${HEALTH[cl].color} 15%, transparent);color:${HEALTH[cl].color}${ring}">${Math.round(pr * 100)}</div></td>`;
                }).join('')}</tr>`).join('')}</tbody></table></div>
            <div class="flex items-center gap-3 mt-2 text-[10px]" style="color:var(--text-muted)">
                <span class="inline-flex items-center gap-1"><span style="width:9px;height:9px;border-radius:2px;background:var(--trust-pass, #10b981);display:inline-block"></span>Healthy</span>
                <span class="inline-flex items-center gap-1"><span style="width:9px;height:9px;border-radius:2px;background:var(--trust-warn, #f59e0b);display:inline-block"></span>Review</span>
                <span class="inline-flex items-center gap-1"><span style="width:9px;height:9px;border-radius:2px;background:var(--trust-danger, #ef4444);display:inline-block"></span>Danger</span>
                <span class="inline-flex items-center gap-1"><span style="width:9px;height:9px;border-radius:2px;background:rgba(127,127,127,.3);display:inline-block"></span>n/a</span>
            </div>` : '';

        const body = `${picker}${legend}
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <div><div class="ap-tier-h" style="margin-top:0">Metric comparison</div>${metricBlocks}</div>
                <div>${heat}</div>
            </div>`;
        const card = sectionCard('Compare Agents', 'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z',
            body, `<button data-agent-action="compare-back" class="text-xs px-2 py-1 rounded" style="color:var(--text-muted);border:1px solid var(--border-secondary)">← Back to roster</button>`);
        r.innerHTML = `<div class="ap-fade">${card}</div>`;
    }

    // Context-specific deep-link anchor: open Agent Performance focused on ONE agent
    // by tag (e.g. from the Ontology inspect sidebar). Created because no
    // programmatic open-by-tag existed — only roster + manual card click.
    async function openAgent(tag) {
        try {
            const { handleViewSwitch } = await import('../ui.js?v=1.287');
            handleViewSwitch('agents-view');
        } catch (e) { /* already on the view */ }
        await load();                                    // render the roster
        showSection('roster');                           // deep-link lands on the roster
        if (tag && _expandedTag !== tag) toggleAgentDetail(tag);   // expand this agent's detail
    }

    // Deep link: open the Agents view directly on the Trust Circle (used by the
    // answer capsule + chat-header mini glyphs).
    async function openCircle(tag) {
        const want = tag ? String(tag).replace(/^@/, '') : null;
        if (want) _pendingCircleTag = want;   // load() resolves it once the model exists
        try {
            const { handleViewSwitch } = await import('../ui.js?v=1.287');
            handleViewSwitch('agents-view');   // triggers load() → resolves the pending focus
        } catch (e) { /* already on the view */ }
        showSection('circle');
        // Already-loaded model (view was open): resolve + mount right away.
        if (want && _model.agents.length) {
            const a = _model.agents.find((x) => x.tag === want && x.kind !== 'flow');
            if (a) { _circleAgent = a.key || a.tag; _pendingCircleTag = null; _mountTrustCircle(); }
            else {
                // TC-82: the tag resolved to nothing on a LOADED model (flow/foreign/deleted
                // agent). Leaving it pending made the NEXT unrelated load() — a scope switch
                // minutes later — silently focus it. A miss against a loaded model is final.
                _pendingCircleTag = null;
            }
        }
    }

    // Deep link: open the Agents view on the Assurance Inbox (a parked write's "Review it in
    // the Assurance Inbox" on the answer). Waits for the load — a section shown before the
    // view finishes loading is overwritten by it.
    async function openInbox() {
        try {
            const { handleViewSwitch } = await import('../ui.js?v=1.287');
            handleViewSwitch('agents-view');
        } catch (e) { /* already on the view */ }
        await load();
        showSection('inbox');
    }

    // `mark` + `ensureCss`: the ONE band-mark builder and its stylesheet, published so a
    // verdict strip drawn elsewhere (S8's experiment card in the chat) is THIS grammar —
    // the same shapes, the same tokens — never a second copy that drifts.
    window.agentPerformance = { load, switchScope, openAgent, showSection, openCircle, openInbox,
        mark: (band) => bandMark(band), ensureCss: injectCss };
})();
