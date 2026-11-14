/**
 * Default Scoring Thresholds — modern banded dual-handle sliders.
 *
 * Each metric renders as one rounded track with two draggable handles and the
 * Healthy / Review / Danger bands painted green / amber / red. Drag to set — no
 * typing. A one-click presets bar (Strict / Balanced / Lenient / Clear) fills
 * everything at once. State lives here; adminManager reads getThresholds() on save
 * and calls setThresholds() on load. Stored shape: { key: {healthy, danger} }.
 *
 * Theme-aware (CSS variables), monochrome, pointer-events (mouse + touch).
 */
(function () {
    'use strict';

    // RANGES carries only the slider's own concerns — the track bounds, the drag step,
    // and which group the row sits in. `label`, `unit` and `dir` are NOT repeated here:
    // they are the canonical metric spec in `qualityDimensions.js`, which the Quality
    // Dimensions Guide reads to print each metric's live band. Two copies would let the
    // Guide render a band backwards (`Healthy <= 75%` on a higher-is-better metric)
    // while this page rendered it correctly, and nothing would notice. QD is loaded
    // before this script on every page that has these sliders; the grammar gate asserts
    // the two key sets agree in both directions.
    const RANGES = [
        // The three resource metrics span decades between a one-call Ideate turn and a
        // multi-expert Coordinate turn (a KG-grounded Optimize turn is routinely 100–200k
        // tokens; a coordinator runs minutes), so their tracks are LOGARITHMIC: equal
        // drag = equal ratio, and 30k vs 60k is as easy to set as 300k vs 600k. A log
        // track needs a positive floor — `min` is that floor, `step` is unused (values
        // snap to two significant figures instead).
        { key: 'max_cost_usd',            min: 0.01, max: 50,      step: 0.01, group: 'Resource', scale: 'log' },
        { key: 'max_tokens',              min: 1000, max: 2000000, step: 500,  group: 'Resource', scale: 'log' },
        { key: 'max_latency_ms',          min: 1000, max: 600000,  step: 500,  group: 'Resource', scale: 'log' },
        { key: 'max_tasks',               min: 0, max: 40,    step: 1,    group: 'Tasks' },
        { key: 'min_parallel_efficiency', min: 1, max: 8,     step: 0.25, group: 'Tasks' },
        { key: 'max_steps',               min: 0, max: 12,    step: 1,    group: 'Planning' },
        { key: 'max_self_corrections',    min: 0, max: 10,    step: 1,    group: 'Reliability' },
        { key: 'min_tool_success_rate',   min: 0, max: 1,     step: 0.05, group: 'Reliability' },
        { key: 'max_tool_repeats',        min: 0, max: 10,    step: 1,    group: 'Reliability' },
        { key: 'max_tool_calls',          min: 0, max: 40,    step: 1,    group: 'Reliability' },
        { key: 'max_out_of_scope_dbs',    min: 0, max: 10,    step: 1,    group: 'Reliability' },
        { key: 'min_numeric_fidelity',    min: 0, max: 1,     step: 0.05, group: 'Quality' },
        { key: 'min_grounded',            min: 0, max: 1,     step: 0.05, group: 'Quality', defaultGate: 0.5 },
        { key: 'min_retrieval_relevance', min: 0, max: 1,     step: 0.05, group: 'Quality' },
        { key: 'min_context_utilization', min: 0, max: 1,     step: 0.05, group: 'Quality' },
        { key: 'min_citation_fidelity',  min: 0, max: 1,     step: 0.05, group: 'Quality' },
        { key: 'max_knowledge_age_days',  min: 0, max: 365,   step: 1,    group: 'Quality' },
        { key: 'min_governed_data_ratio', min: 0, max: 1,     step: 0.05, group: 'Quality' },
    ];
    // Merge in the canonical label/unit/dir. A missing entry is a WIRING error, not a
    // cosmetic one — an absent `dir` would paint the bands the wrong way round — so it
    // is reported loudly and the row falls back to a safe, obviously-unstyled reading
    // rather than silently rendering a lie.
    const _QDSPEC = (window.QualityDimensions && window.QualityDimensions.THRESHOLD_SPEC) || null;
    if (!_QDSPEC) {
        console.error('thresholdSliders: QualityDimensions.THRESHOLD_SPEC unavailable — '
            + 'metric labels/units/directions cannot be resolved. Check script order in index.html.');
    }
    const METRICS = RANGES.map((r) => {
        const spec = (_QDSPEC && _QDSPEC[r.key]) || null;
        if (!spec) console.error(`thresholdSliders: no canonical spec for metric "${r.key}"`);
        return { ...r, label: (spec && spec.label) || r.key, unit: (spec && spec.unit) || '',
                 dir: (spec && spec.dir) || 'lower' };
    });
    const SPEC = Object.fromEntries(METRICS.map((m) => [m.key, m]));

    // Boolean expectation flags — assertions with no numeric band. Stored as
    // {danger: 1} so the backend's assertion flattening (danger → the key) makes
    // the scorer's `case.assertions.get(key)` truthy on live turns. Off = absent.
    const FLAGS = [
        { key: 'require_citations', label: 'Citations required', group: 'Quality',
          desc: 'Focus answers must carry citation markers ([1], (source …), refs).\nOff = the Citations Present check abstains (the default).' },
    ];
    const FLAG_SPEC = Object.fromEntries(FLAGS.map((f) => [f.key, f]));
    const GREEN = '#10b981', AMBER = '#f59e0b', RED = '#ef4444';

    // Preset values are {healthy, danger} in metric units (respecting direction).
    const PRESETS = {
        strict:   { max_cost_usd: [0.15, 0.35], max_tokens: [25000, 75000], max_tasks: [6, 12], min_parallel_efficiency: [2.0, 1.2], max_steps: [3, 5], max_latency_ms: [5000, 15000], max_self_corrections: [0, 2], min_tool_success_rate: [0.95, 0.85], max_tool_repeats: [1, 2], max_tool_calls: [5, 10], max_out_of_scope_dbs: [0, 1], min_numeric_fidelity: [0.95, 0.85], min_grounded: [0.85, 0.7], min_retrieval_relevance: [0.7, 0.55], min_context_utilization: [0.75, 0.5], max_knowledge_age_days: [7, 30], min_governed_data_ratio: [0.9, 0.6], min_citation_fidelity: [1.0, 0.75] },
        balanced: { max_cost_usd: [0.3, 0.6], max_tokens: [60000, 150000], max_tasks: [10, 20], min_parallel_efficiency: [1.5, 1.05], max_steps: [4, 7], max_latency_ms: [10000, 30000], max_self_corrections: [1, 3], min_tool_success_rate: [0.9, 0.7], max_tool_repeats: [2, 4], max_tool_calls: [7, 14], max_out_of_scope_dbs: [0, 1], min_numeric_fidelity: [0.9, 0.7], min_grounded: [0.75, 0.5], min_retrieval_relevance: [0.6, 0.4], min_context_utilization: [0.6, 0.35], max_knowledge_age_days: [30, 90], min_governed_data_ratio: [0.75, 0.4], min_citation_fidelity: [0.75, 0.5] },
        lenient:  { max_cost_usd: [0.6, 1.0], max_tokens: [150000, 400000], max_tasks: [18, 35], min_parallel_efficiency: [1.2, 1.0], max_steps: [6, 10], max_latency_ms: [30000, 90000], max_self_corrections: [3, 6], min_tool_success_rate: [0.8, 0.5], max_tool_repeats: [4, 8], max_tool_calls: [12, 22], max_out_of_scope_dbs: [1, 3], min_numeric_fidelity: [0.8, 0.5], min_grounded: [0.6, 0.35], min_retrieval_relevance: [0.5, 0.3], min_context_utilization: [0.45, 0.2], max_knowledge_age_days: [90, 180], min_governed_data_ratio: [0.5, 0.25], min_citation_fidelity: [0.5, 0.25] },
    };

    let _state = {};         // key -> {healthy, danger}  (absent/empty = Off)
    let _containerId = null;
    let _drag = null;        // {key, handle:'low'|'high'}

    // ── value helpers ──────────────────────────────────────────────────────────
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
    function snap(spec, v) {
        if (spec.scale === 'log') {
            // two significant figures: 1,000 … 9,900 … 12,000 … 150,000 — the resolution a
            // reader can tell apart on a decade track, never a 500-token step at 1M
            const n = Number(v);
            const sig = n > 0 ? Number(n.toPrecision(2)) : spec.min;
            return clamp(sig, spec.min, spec.max);
        }
        const stepped = Math.round((v - spec.min) / spec.step) * spec.step + spec.min;
        return clamp(Math.round(stepped * 1e6) / 1e6, spec.min, spec.max);
    }
    // value ↔ track position: linear by default, geometric on a log track
    function toRatio(spec, v) {
        if (spec.scale === 'log') return (Math.log(clamp(v, spec.min, spec.max)) - Math.log(spec.min)) / (Math.log(spec.max) - Math.log(spec.min));
        return (v - spec.min) / (spec.max - spec.min);
    }
    function fromRatio(spec, r) {
        r = clamp(r, 0, 1);
        if (spec.scale === 'log') return Math.exp(Math.log(spec.min) + r * (Math.log(spec.max) - Math.log(spec.min)));
        return spec.min + r * (spec.max - spec.min);
    }
    function pct(spec, v) { return toRatio(spec, v) * 100; }
    // Delegates to the canonical formatter so this page and the Quality Dimensions Guide
    // print the SAME number the same way. The local branch is a defensive fallback for a
    // page where QD failed to load — it must never become a second implementation people
    // edit, which is why it is deliberately minimal.
    function fmt(spec, v) {
        if (v == null) return '—';
        const QD = window.QualityDimensions;
        if (QD && typeof QD.fmtThreshold === 'function') return QD.fmtThreshold(spec.key, v);
        return (spec.step < 1 ? Number(v) : Math.round(v)).toLocaleString();
    }
    function isActive(key) { const s = _state[key]; return s && (s.healthy != null || s.danger != null); }
    // Ascending handle values [low, high] from {healthy, danger}.
    function toHandles(spec, s) {
        if (spec.dir === 'lower') return [s.healthy, s.danger];
        return [s.danger, s.healthy];
    }
    function fromHandles(spec, low, high) {
        if (spec.dir === 'lower') return { healthy: low, danger: high };
        return { healthy: high, danger: low };
    }
    function defaults(spec) {
        const preset = PRESETS.balanced[spec.key];
        if (preset) {
            const [h, d] = preset;
            return { healthy: h, danger: d };
        }
        // Defensive: a metric without a preset entry still activates with a band
        // derived from its range (never a thrown TypeError that kills the click).
        return spec.dir === 'lower'
            ? { healthy: snap(spec, fromRatio(spec, 0.25)), danger: snap(spec, fromRatio(spec, 0.6)) }
            : { healthy: snap(spec, fromRatio(spec, 0.75)), danger: snap(spec, fromRatio(spec, 0.4)) };
    }

    // ── rendering ──────────────────────────────────────────────────────────────
    function rowHtml(spec) {
        const active = isActive(spec.key);
        const leftLabel = spec.dir === 'lower' ? 'Healthy ≤' : 'Danger <';
        const rightLabel = spec.dir === 'lower' ? 'Danger >' : 'Healthy ≥';
        return `
        <div class="thr-row" data-key="${spec.key}" style="opacity:${active ? 1 : 0.55};">
            <div class="flex items-center justify-between mb-1">
                <span class="text-[11px] font-medium" style="color: var(--text-primary);">${spec.label}
                    <span class="text-[10px]" style="color: var(--text-muted);">(${spec.dir === 'lower' ? 'lower' : 'higher'} = better)</span>
                </span>
                <div class="flex items-center gap-2 text-[10px]">
                    <span id="thr-read-low-${spec.key}" style="color: var(--text-muted);"></span>
                    <span id="thr-read-high-${spec.key}" style="color: var(--text-muted);"></span>
                    <button type="button" class="thr-off" data-act="off" data-key="${spec.key}" title="${active ? 'Clear (informational only)' : 'Activate'}"
                        style="font-size:10px; padding:1px 7px; border-radius:9999px; border:1px solid var(--border-secondary); color: var(--text-muted); background:transparent; cursor:pointer;">${active ? 'Off' : 'Set'}</button>
                </div>
            </div>
            <div class="thr-track" id="thr-track-${spec.key}" data-key="${spec.key}"
                style="position:relative; height:10px; border-radius:9999px; background: var(--hover-bg-strong, rgba(127,127,127,0.16)); cursor:${active ? 'pointer' : 'default'}; touch-action:none;">
                <div id="thr-seg-a-${spec.key}" style="position:absolute; top:0; bottom:0; left:0;  border-radius:9999px 0 0 9999px;"></div>
                <div id="thr-seg-b-${spec.key}" style="position:absolute; top:0; bottom:0;"></div>
                <div id="thr-seg-c-${spec.key}" style="position:absolute; top:0; bottom:0; right:0; border-radius:0 9999px 9999px 0;"></div>
                <div class="thr-handle" data-handle="low"  data-key="${spec.key}" id="thr-h-low-${spec.key}"
                    style="position:absolute; top:50%; width:16px; height:16px; margin-left:-8px; border-radius:9999px; background:var(--teradata-orange,#F15F22); border:2px solid var(--card-bg); box-shadow:0 1px 4px rgba(0,0,0,0.35); transform:translateY(-50%); cursor:grab; display:${active ? 'block' : 'none'};"></div>
                <div class="thr-handle" data-handle="high" data-key="${spec.key}" id="thr-h-high-${spec.key}"
                    style="position:absolute; top:50%; width:16px; height:16px; margin-left:-8px; border-radius:9999px; background:var(--teradata-orange,#F15F22); border:2px solid var(--card-bg); box-shadow:0 1px 4px rgba(0,0,0,0.35); transform:translateY(-50%); cursor:grab; display:${active ? 'block' : 'none'};"></div>
                <span id="thr-off-hint-${spec.key}" style="position:absolute; inset:0; display:${active ? 'none' : 'flex'}; align-items:center; justify-content:center; font-size:9px; color: var(--text-muted); pointer-events:none;">${spec.defaultGate != null ? `defaults to ${fmt(spec, spec.defaultGate)} fail-line when off` : 'informational — click to set thresholds'}</span>
            </div>
            <div class="flex items-center justify-between mt-0.5 text-[9px]" style="color: var(--text-muted); opacity:0.7;">
                <span>${leftLabel}</span><span>${rightLabel}</span>
            </div>
        </div>`;
    }

    function _flagBtn(f) {
        const on = isActive(f.key);
        return `<button type="button" class="thr-flag u-tip" data-key="${f.key}" data-tip="${f.desc}"
            style="font-size:10px; padding:2px 10px; border-radius:9999px; cursor:pointer;
                   border:1px solid ${on ? 'var(--trust-pass, #34d399)' : 'var(--border-secondary)'};
                   color:${on ? 'var(--trust-pass, #34d399)' : 'var(--text-muted)'}; background:transparent;">
            ${f.label}: ${on ? 'required' : 'off'}</button>`;
    }

    /** One host's markup: metric rows (tier-subgrouped only when more than one tier is
     *  present — a single-tier block does not need a heading over itself) + flag pills. */
    function _sectionHtml(metrics, flags, subgroup) {
        const groups = {};
        metrics.forEach((m) => { (groups[m.group] = groups[m.group] || []).push(m); });
        const keys = Object.keys(groups);
        const useHeads = subgroup && keys.length > 1;
        const body = keys.map((g) => `
            <div class="${useHeads ? 'mb-3' : ''}">
                ${useHeads ? `<p class="text-[11px] font-semibold uppercase tracking-wide mb-2" style="color: var(--text-muted);">${g}</p>` : ''}
                <div class="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-3">${groups[g].map(rowHtml).join('')}</div>
            </div>`).join('');
        const flagRow = (flags || []).map(_flagBtn).join(' ');
        return body + (flagRow ? `<div class="mt-2 flex flex-wrap gap-2">${flagRow}</div>` : '');
    }

    /** The station a metric/flag key belongs to — resolved through the PARTITION
     *  (`QualityDimensions.stationOfThreshold`, one source), never a local copy. The
     *  admin Trust page groups its sliders by station through this seam, so when
     *  dimension→station assignment becomes dynamic the page regroups itself. Falls
     *  back to `scored` (JUDGED, the residue) only when QD cannot resolve — loudly. */
    function _stageOfKey(key) {
        const QD = window.QualityDimensions;
        if (QD && typeof QD.stageOfThreshold === 'function') {
            const st = QD.stageOfThreshold(key);
            if (st) return st;
            // The served stage model has not landed yet — not a gap. `init` re-renders
            // once `ensureVocabulary` resolves; until then no host receives a slider.
            if (typeof QD.stagesReady === 'function' && !QD.stagesReady()) return '';
        }
        // Loud, not silent: an unresolvable key would otherwise land in whichever host
        // happens to be first and read as a setting for a stage it has nothing to do
        // with. The caller renders it in the unplaced host instead.
        console.error(`thresholdSliders: no stage resolved for "${key}" — check `
            + 'QualityDimensions.stageOfThreshold / the TUNING map.');
        return '';
    }

    function render() {
        // STAGE MODE: the admin Trust page provides one host per STAGE of the turn
        // (`[data-thresh-stage]`); each receives exactly the sliders whose dimension is
        // measured at that stage, and the hosts appear in turn order.
        //
        // This replaced one host per STATION. Grouping a slider under a guarantee said
        // it belonged to that guarantee — true while stations owned dimensions, false
        // once measurement became a lane. Sliders tune MEASUREMENTS, so they group the
        // way measurements group. It also distributes far better: three station hosts
        // held 7/2/9 of the 18, five stage hosts hold 3/2/6/4/3.
        //
        // The legacy single host remains the fallback so any older page keeps working.
        const stageHosts = Array.from(document.querySelectorAll('[data-thresh-stage]'));
        if (stageHosts.length) {
            stageHosts.forEach((el) => {
                const st = el.dataset.threshStage;
                const metrics = METRICS.filter((m) => _stageOfKey(m.key) === st);
                const flags = FLAGS.filter((f) => _stageOfKey(f.key) === st);
                el.innerHTML = (metrics.length || flags.length)
                    ? _sectionHtml(metrics, flags, false)
                    : '';
            });
            METRICS.forEach((m) => paint(m));
            return;
        }
        const host = document.getElementById(_containerId);
        if (!host) return;
        host.innerHTML = _sectionHtml(METRICS, FLAGS, true);
        METRICS.forEach((m) => paint(m));
    }

    function paint(spec) {
        const active = isActive(spec.key);
        const segA = document.getElementById(`thr-seg-a-${spec.key}`);
        const segB = document.getElementById(`thr-seg-b-${spec.key}`);
        const segC = document.getElementById(`thr-seg-c-${spec.key}`);
        const hLow = document.getElementById(`thr-h-low-${spec.key}`);
        const hHigh = document.getElementById(`thr-h-high-${spec.key}`);
        const rLow = document.getElementById(`thr-read-low-${spec.key}`);
        const rHigh = document.getElementById(`thr-read-high-${spec.key}`);
        if (!segA) return;
        if (!active) {
            [segA, segB, segC].forEach((s) => { s.style.background = 'transparent'; });
            if (rLow) rLow.textContent = ''; if (rHigh) rHigh.textContent = '';
            return;
        }
        const [low, high] = toHandles(spec, _state[spec.key]);
        const p1 = pct(spec, low), p2 = pct(spec, high);
        // lower-is-better: green | amber | red ;  higher-is-better: red | amber | green
        const leftColor = spec.dir === 'lower' ? GREEN : RED;
        const rightColor = spec.dir === 'lower' ? RED : GREEN;
        segA.style.background = leftColor;  segA.style.width = p1 + '%';
        segB.style.background = AMBER;       segB.style.left = p1 + '%'; segB.style.width = Math.max(0, p2 - p1) + '%';
        segC.style.background = rightColor;  segC.style.left = p2 + '%'; segC.style.right = '0';
        hLow.style.left = p1 + '%'; hHigh.style.left = p2 + '%';
        hLow.style.borderColor = leftColor; hHigh.style.borderColor = rightColor;
        // readouts in {healthy, danger} terms
        const s = _state[spec.key];
        const _dot = (c) => `<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:${c};vertical-align:middle;"></span>`;
        if (rLow) rLow.innerHTML = `${_dot(spec.dir === 'lower' ? GREEN : RED)} ${spec.dir === 'lower' ? 'H' : 'D'} ${fmt(spec, low)}`;
        if (rHigh) rHigh.innerHTML = `${_dot(spec.dir === 'lower' ? RED : GREEN)} ${spec.dir === 'lower' ? 'D' : 'H'} ${fmt(spec, high)}`;
        void s;
    }

    // ── interaction ──────────────────────────────────────────────────────────────
    function valueFromPointer(spec, clientX) {
        const track = document.getElementById(`thr-track-${spec.key}`);
        const r = track.getBoundingClientRect();
        const ratio = clamp((clientX - r.left) / r.width, 0, 1);
        return snap(spec, fromRatio(spec, ratio));
    }
    function setHandle(spec, handle, val) {
        let [low, high] = toHandles(spec, _state[spec.key]);
        if (handle === 'low') low = Math.min(val, high);
        else high = Math.max(val, low);
        _state[spec.key] = fromHandles(spec, low, high);
        paint(spec);
    }

    function onPointerDown(e) {
        const handle = e.target.closest('.thr-handle');
        const track = e.target.closest('.thr-track');
        const flagBtn = e.target.closest('.thr-flag');
        if (flagBtn) {
            const key = flagBtn.dataset.key;
            _state[key] = isActive(key) ? {} : { danger: 1 };
            render();
            return;
        }
        const offBtn = e.target.closest('.thr-off');
        if (offBtn) {
            const key = offBtn.dataset.key;
            _state[key] = isActive(key) ? {} : defaults(SPEC[key]);
            render();
            return;
        }
        if (handle) {
            const key = handle.dataset.key;
            if (!isActive(key)) return;
            _drag = { key, handle: handle.dataset.handle };
            handle.style.cursor = 'grabbing';
            e.preventDefault();
            return;
        }
        if (track) {
            const key = track.dataset.key;
            const spec = SPEC[key];
            if (!isActive(key)) { _state[key] = defaults(spec); render(); return; }
            // click on track moves the nearest handle there
            const val = valueFromPointer(spec, e.clientX);
            const [low, high] = toHandles(spec, _state[key]);
            const nearLow = Math.abs(val - low) <= Math.abs(val - high);
            setHandle(spec, nearLow ? 'low' : 'high', val);
        }
    }
    function onPointerMove(e) {
        if (!_drag) return;
        const spec = SPEC[_drag.key];
        setHandle(spec, _drag.handle, valueFromPointer(spec, e.clientX));
    }
    function onPointerUp() {
        if (_drag) {
            const h = document.getElementById(`thr-h-${_drag.handle}-${_drag.key}`);
            if (h) h.style.cursor = 'grab';
        }
        _drag = null;
    }

    // ── public API ───────────────────────────────────────────────────────────────
    function normalize(obj) {
        const out = {};
        if (obj && typeof obj === 'object') {
            Object.keys(obj).forEach((k) => {
                if (FLAG_SPEC[k]) {
                    const v = obj[k];
                    const on = (typeof v === 'object' && v) ? !!v.danger : !!v;
                    if (on) out[k] = { danger: 1 };
                    return;
                }
                const spec = SPEC[k];
                if (!spec) return;
                const v = obj[k];
                if (v == null) return;
                const band = (typeof v === 'object') ? v : { danger: v };
                let healthy = band.healthy != null ? Number(band.healthy) : null;
                let danger = band.danger != null ? Number(band.danger) : null;
                if (healthy == null && danger == null) return;
                // The slider is a two-handle control — complete any single-bound legacy
                // config from defaults, then enforce ascending handle order per direction.
                const def = defaults(spec);
                if (healthy == null) healthy = def.healthy;
                if (danger == null) danger = def.danger;
                let [low, high] = toHandles(spec, { healthy, danger });
                low = snap(spec, low); high = snap(spec, high);
                if (low > high) { const t = low; low = high; high = t; }
                out[k] = fromHandles(spec, low, high);
            });
        }
        return out;
    }
    function setThresholds(obj) { _state = normalize(obj); render(); }
    function getThresholds() {
        const out = {};
        Object.keys(_state).forEach((k) => { if (isActive(k)) out[k] = { ..._state[k] }; });
        return out;
    }
    function applyPreset(name) {
        if (name === 'clear') { _state = {}; render(); return; }
        const p = PRESETS[name];
        if (!p) return;
        const next = {};
        Object.keys(p).forEach((k) => { next[k] = { healthy: p[k][0], danger: p[k][1] }; });
        // Expectation flags are orthogonal to the numeric tiers — carry them over.
        FLAGS.forEach((f) => { if (isActive(f.key)) next[f.key] = { ..._state[f.key] }; });
        _state = next;
        render();
    }
    function init(containerId) {
        _containerId = containerId;
        // Delegated pointer handlers survive re-renders (attached once to document).
        if (!window.__thrSlidersWired) {
            document.addEventListener('pointerdown', onPointerDown);
            document.addEventListener('pointermove', onPointerMove);
            document.addEventListener('pointerup', onPointerUp);
            window.__thrSlidersWired = true;
        }
        // Stage mode needs the SERVED process model; render immediately, then re-render
        // once it lands — the same fetch-and-repaint transient the Assurance Guide
        // accepts. State is module-level, so the repaint loses nothing.
        const QD = window.QualityDimensions;
        if (QD && typeof QD.ensureVocabulary === 'function'
            && document.querySelector('[data-thresh-stage]')) {
            QD.ensureVocabulary().then(render);
        }
        render();
    }

    window.thresholdSliders = { init, setThresholds, getThresholds, applyPreset };
})();
