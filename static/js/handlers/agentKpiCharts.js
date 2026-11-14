/*
 * Agent Performance — KPI graph visualizations (WS3)
 *
 * A world-class, actionable dashboard across ALL agents, rendered from the eval
 * quality spans. Dependency-free inline SVG (crisp, theme-aware, instant), no chart
 * lib to load. Four decision-driving charts:
 *   1. Quality Trend      — rolling pass rate over recent turns (are we improving?)
 *   2. Top Failing Checks — which checks fail most across agents (what to fix)
 *   3. Agent Scoreboard   — pass rate per agent, worst-first (who to fix)
 *   4. Cost & Latency     — spend + speed per agent (where the waste is)
 *
 * window.agentKpiCharts.renderInto(hostEl, data)
 */
(function () {
    'use strict';

    const fmtPct = (v) => (v == null ? '—' : Math.round(v * 100) + '%');
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    const money = (v) => '$' + Number(v || 0).toFixed(v >= 0.1 ? 2 : 4);
    const secs = (ms) => (ms >= 1000 ? (ms / 1000).toFixed(1) + 's' : Math.round(ms) + 'ms');
    // Canonical 4-class health taxonomy (kept in lockstep with agentPerformanceHandler.HEALTH):
    //   Healthy ≥0.80 green · Review 0.50–0.79 amber · Danger <0.50 red · Inactive (null) grey.
    // The third copy of the band floors. Now fed from the ONE configured source
    // (`dashboard.quality_bands`, via agentPerformanceHandler._applyQualityBands); the
    // literals remain only as the historical default so an untouched deployment renders
    // byte-identically. Colours stay the health palette used across the charts.
    let _healthyFloor = 0.8, _warnFloor = 0.5;
    function setBands(healthy, warn) {
        if (!(healthy > 0 && healthy <= 1) || !(warn > 0 && warn <= 1) || warn >= healthy) return;
        _healthyFloor = healthy; _warnFloor = warn;
    }
    // Health → trust-STATE colour (green/amber/red by pass rate) — theme-aware tokens, not
    // literals, so a chart bar reads the same as the ring it summarises.
    const healthColor = (pr) => pr == null ? 'var(--text-muted)'
        : pr >= _healthyFloor ? 'var(--trust-pass)'
        : pr >= _warnFloor ? 'var(--trust-warn)' : 'var(--trust-danger)';
    // hex-palette: chart SERIES colours (cost / latency identity, not trust state)
    const C_COST = '#f59e0b', C_LAT = '#3b82f6';  // hex-palette

    // IFOC accent for an agent's class
    const CLASS_COLOR = { llm_only: '#4ade80', conversation_with_tools: '#4ade80', rag_focused: '#3b82f6', tool_enabled: '#9333ea', genie: '#F15F22' };

    function card(title, subtitle, bodyHtml, tip, goto) {
        const clickAttrs = goto
            ? ` data-goto-section="${goto}" role="button" tabindex="0" style="background-color: var(--card-bg); border-color: var(--border-primary); cursor: pointer;"`
            : ` style="background-color: var(--card-bg); border-color: var(--border-primary);"`;
        return `<div class="rounded-xl p-4 border kpi-graph-card"${tip ? ` data-tooltip="${esc(tip)}"` : ''}${clickAttrs}>
            <div class="flex items-baseline justify-between mb-3">
                <h4 class="text-sm font-semibold" style="color: var(--text-primary);">${esc(title)}</h4>
                <span class="text-[11px]" style="color: var(--text-muted);">${esc(subtitle || '')}</span>
            </div>
            ${bodyHtml}
        </div>`;
    }

    function empty(msg) {
        return `<div class="text-xs py-6 text-center" style="color: var(--text-muted);">${esc(msg)}</div>`;
    }

    // ── 1. Quality trend (rolling pass rate, area+line) ────────────────────────
    function trendChart(data) {
        const recent = data.recent || [];
        const ov = data.overall || {};
        if (recent.length < 2) {
            return card('Quality Trend', `${ov.turns || 0} scored`,
                `<div class="flex items-baseline gap-2 mb-2"><span class="text-3xl font-bold" style="color:${healthColor(ov.pass_rate)};">${fmtPct(ov.pass_rate)}</span><span class="text-xs" style="color: var(--text-muted);">overall pass rate</span></div>${empty('Not enough turns for a trend yet.')}`,
                'Rolling pass rate across your agents\u2019 recently scored turns \u2014 are we improving? Click to open the Agent Roster.', 'roster');
        }
        // rolling pass-rate (window) to smooth
        const win = Math.max(3, Math.round(recent.length / 8));
        const pts = recent.map((_, i) => {
            const slice = recent.slice(Math.max(0, i - win + 1), i + 1);
            return slice.filter((r) => r.passed).length / slice.length;
        });
        const W = 320, H = 90, pad = 4;
        const x = (i) => pad + (i / (pts.length - 1)) * (W - 2 * pad);
        const y = (v) => H - pad - v * (H - 2 * pad);
        const line = pts.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ');
        const area = `${line} L${x(pts.length - 1).toFixed(1)} ${H - pad} L${x(0).toFixed(1)} ${H - pad} Z`;
        const last = pts[pts.length - 1];
        const stroke = healthColor(last);
        const svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" preserveAspectRatio="none">
            <defs><linearGradient id="qtg" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stop-color="${stroke}" stop-opacity="0.28"/><stop offset="100%" stop-color="${stroke}" stop-opacity="0"/></linearGradient></defs>
            <line x1="${pad}" x2="${W - pad}" y1="${y(0.8).toFixed(1)}" y2="${y(0.8).toFixed(1)}" stroke="var(--border-secondary)" stroke-width="1" stroke-dasharray="3 3"/>
            <path d="${area}" fill="url(#qtg)"/><path d="${line}" fill="none" stroke="${stroke}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>`;
        const body = `<div class="flex items-baseline gap-2 mb-1">
                <span class="text-3xl font-bold" style="color:${stroke};">${fmtPct(ov.pass_rate)}</span>
                <span class="text-xs" style="color: var(--text-muted);">overall · ${ov.turns} turns · ${ov.fail_turns} failed</span>
            </div>${svg}
            <div class="flex items-center justify-between text-[11px] mt-1" style="color: var(--text-muted);">
                <span>avg cost ${money(ov.avg_cost)}/turn</span><span>avg ${secs(ov.avg_latency_ms)}</span></div>`;
        return card('Quality Trend', `${ov.turns} turns · trend over the last ${recent.length}`, body,
            'Rolling pass rate across your agents\u2019 recently scored turns \u2014 are we improving? Click to open the Agent Roster.', 'roster');
    }

    // ── horizontal-bar primitive ───────────────────────────────────────────────
    function hbar(label, value, pct, color, valueText) {
        const w = Math.max(0, Math.min(1, pct)) * 100;
        return `<div class="flex items-center gap-2 mb-1.5">
            <div class="text-xs w-36 flex-shrink-0 truncate u-tip" style="color: var(--text-muted);" data-tip="${esc(label)}">${esc(label)}</div>
            <div class="flex-1 h-3 rounded-full overflow-hidden" style="background: var(--hover-bg-strong, rgba(127,127,127,0.14));"><div style="width:${w}%; height:100%; background:${color}; border-radius:9999px;"></div></div>
            <div class="text-xs w-16 text-right font-medium" style="color: var(--text-primary);">${esc(valueText)}</div>
        </div>`;
    }

    // ── 2. Top failing checks ───────────────────────────────────────────────────
    function failingChart(data) {
        const f = data.failing_checks || [];
        if (!f.length) {
            return card('Top Failing Checks', 'across all agents',
                `<div class="text-xs py-6 text-center" style="color:var(--trust-pass);">No gating failures \u2014 every check is passing.</div>`,
                'The quality checks that fail most across all your agents \u2014 what to fix first. Click to open the Agent Roster.', 'roster');
        }
        const max = Math.max(...f.map((x) => x.fail_rate), 0.0001);
        const rows = f.map((x) => hbar(x.dimension, x.fail_rate, x.fail_rate / max, 'var(--trust-danger)',
            `${fmtPct(x.fail_rate)} (${x.fails})`)).join('');
        return card('Top Failing Checks', 'failure rate \u00b7 count', rows,
            'The quality checks that fail most across all your agents \u2014 what to fix first. Click to open the Agent Roster.', 'roster');
    }

    // ── 3. Agent scoreboard ─────────────────────────────────────────────────────
    function scoreboardChart(data) {
        const a = (data.agents || []).filter((x) => x.pass_rate != null);
        if (!a.length) return card('Agent Scoreboard', 'pass rate', empty('No scored agents yet.'),
            'Pass rate per agent, worst-first \u2014 who to fix. Click to open the Agent Roster.', 'roster');
        const rows = a.slice(0, 8).map((x) => hbar(`@${x.tag}`, x.pass_rate, x.pass_rate,
            healthColor(x.pass_rate), `${fmtPct(x.pass_rate)} \u00b7 ${x.turns}`)).join('');
        return card('Agent Scoreboard', 'pass rate \u00b7 turns (worst first)', rows,
            'Pass rate per agent, worst-first \u2014 who to fix. Click to open the Agent Roster.', 'roster');
    }

    // ── 4. Cost & latency by agent ──────────────────────────────────────────────
    function costLatencyChart(data) {
        const a = (data.agents || []).filter((x) => x.turns);
        if (!a.length) return card('Cost & Latency', 'per agent', empty('No scored agents yet.'),
            'Average spend and speed per agent \u2014 where the waste is. Click to open the Agent Roster.', 'roster');
        const maxCost = Math.max(...a.map((x) => x.avg_cost), 0.0001);
        const maxLat = Math.max(...a.map((x) => x.avg_latency_ms), 1);
        const rows = a.slice(0, 8).map((x) => {
            const cc = CLASS_COLOR[x.profile_class] || 'var(--teradata-orange)';
            return `<div class="mb-2">
                <div class="flex items-center justify-between text-[11px] mb-0.5"><span style="color: var(--text-primary);">@${esc(x.tag)}</span><span style="color: var(--text-muted);">${money(x.avg_cost)}/turn · ${secs(x.avg_latency_ms)}</span></div>
                <div class="flex gap-1 items-center">
                    <div class="flex-1 h-2 rounded-full overflow-hidden u-tip" style="background: var(--hover-bg-strong, rgba(127,127,127,0.14));" data-tip="avg cost"><div style="width:${(x.avg_cost / maxCost) * 100}%;height:100%;background:${C_COST};"></div></div>
                    <div class="flex-1 h-2 rounded-full overflow-hidden u-tip" style="background: var(--hover-bg-strong, rgba(127,127,127,0.14));" data-tip="avg latency"><div style="width:${(x.avg_latency_ms / maxLat) * 100}%;height:100%;background:#3b82f6;"></div></div>
                </div></div>`;
        }).join('');
        const legend = `<div class="flex items-center gap-3 text-[10px] mt-1" style="color: var(--text-muted);"><span><span style="display:inline-block;width:8px;height:8px;background:${C_COST};border-radius:2px;"></span> cost</span><span><span style="display:inline-block;width:8px;height:8px;background:${C_LAT};border-radius:2px;"></span> latency</span></div>`;
        return card('Cost & Latency', 'per agent (relative)', rows + legend,
            'Average spend and speed per agent \u2014 where the waste is. Click to open the Agent Roster.', 'roster');
    }

    // ── per-agent timeline (track ONE agent over time) ─────────────────────────
    function _areaLine(vals, W, H, pad, color, withArea) {
        if (vals.length < 2) return '';
        const min = Math.min(...vals), max = Math.max(...vals), rng = (max - min) || 1;
        const x = (i) => pad + (i / (vals.length - 1)) * (W - 2 * pad);
        const y = (v) => H - pad - ((v - min) / rng) * (H - 2 * pad);
        const line = vals.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ');
        const area = withArea ? `${line} L${x(vals.length - 1).toFixed(1)} ${H - pad} L${x(0).toFixed(1)} ${H - pad} Z` : '';
        return { line, area };
    }

    function _miniSpark(vals, color, h, fmt, label) {
        if (!vals.length) return `<div class="text-[10px]" style="color: var(--text-muted);">${esc(label)}: —</div>`;
        const W = 120, pad = 2;
        const p = _areaLine(vals, W, h, pad, color, false);
        const svg = vals.length >= 2
            ? `<svg viewBox="0 0 ${W} ${h}" width="100%" height="${h}" preserveAspectRatio="none"><path d="${p.line}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round"/></svg>`
            : '';
        return `<div><div class="flex items-baseline justify-between text-[10px] mb-0.5"><span style="color: var(--text-muted);">${esc(label)}</span><span style="color:${color}; font-weight:600;">${esc(fmt(vals[vals.length - 1]))}</span></div>${svg}</div>`;
    }

    function timelineCard(data) {
        if (!data || !data.turns) {
            return card('Performance Over Time', '', empty('No scored turns for this agent yet — runs appear here over time.'));
        }
        const tl = data.timeline || [];
        const recent = data.recent || [];
        const useBuckets = tl.length >= 2;
        // primary: rolling pass rate
        let prVals, xLabels, costVals, latVals;
        if (useBuckets) {
            prVals = tl.map((b) => b.pass_rate == null ? 0 : b.pass_rate);
            xLabels = [tl[0].label, tl[tl.length - 1].label];
            costVals = tl.map((b) => b.avg_cost);
            latVals = tl.map((b) => b.avg_latency_ms);
        } else {
            const win = Math.max(2, Math.round(recent.length / 8));
            prVals = recent.map((_, i) => {
                const sl = recent.slice(Math.max(0, i - win + 1), i + 1);
                return sl.filter((r) => r.passed).length / sl.length;
            });
            xLabels = [`turn 1`, `turn ${recent.length}`];
            costVals = recent.map((r) => r.cost);
            latVals = recent.map((r) => r.latency_ms);
        }
        const cur = prVals.length ? prVals[prVals.length - 1] : null;
        const stroke = healthColor(cur);
        const W = 320, H = 96, pad = 5;
        const p = _areaLine(prVals.map((v) => v), W, H, pad, stroke, true);
        const yLine = H - pad - 0.8 * (H - 2 * pad);
        const svg = prVals.length >= 2 ? `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" preserveAspectRatio="none">
            <defs><linearGradient id="tlg" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="${stroke}" stop-opacity="0.26"/><stop offset="100%" stop-color="${stroke}" stop-opacity="0"/></linearGradient></defs>
            <line x1="${pad}" x2="${W - pad}" y1="${yLine.toFixed(1)}" y2="${yLine.toFixed(1)}" stroke="var(--border-secondary)" stroke-dasharray="3 3"/>
            <path d="${p.area}" fill="url(#tlg)"/><path d="${p.line}" fill="none" stroke="${stroke}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>` : empty('Not enough history for a trend yet.');
        const body = `<div class="flex items-baseline gap-2 mb-1">
                <span class="text-2xl font-bold" style="color:${stroke};">${fmtPct(cur)}</span>
                <span class="text-xs" style="color: var(--text-muted);">pass rate · ${data.turns} turns</span>
            </div>${svg}
            <div class="flex items-center justify-between text-[10px] mt-1" style="color: var(--text-muted);"><span>${esc(xLabels[0])}</span><span>${esc(xLabels[1])}</span></div>
            <div class="grid grid-cols-2 gap-3 mt-2">${_miniSpark(costVals, C_COST, 22, money, 'cost / turn')}${_miniSpark(latVals, C_LAT, 22, secs, 'latency')}</div>`;
        return card('Performance Over Time', useBuckets ? 'by day' : 'recent turns', body);
    }

    function renderInto(host, data) {
        if (!host) return;
        const ov = data && data.overall;
        if (!ov || !ov.turns) {
            host.innerHTML = `<div class="rounded-xl p-4 border" style="background-color: var(--card-bg); border-color: var(--border-primary);">
                <h4 class="text-sm font-semibold mb-2" style="color: var(--text-primary);">Performance Insights</h4>
                <div class="text-xs py-4 text-center" style="color: var(--text-muted);">No scored turns yet. Enable <strong>Automatic Agent Scoring</strong> (Administration → Config → Trust → Scoring) or run an eval — charts appear as data arrives.</div></div>`;
            return;
        }
        host.innerHTML = `<div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
            ${trendChart(data)}${failingChart(data)}${scoreboardChart(data)}${costLatencyChart(data)}
        </div>`;
    }

    window.agentKpiCharts = { renderInto, timelineCard, setBands };
})();
