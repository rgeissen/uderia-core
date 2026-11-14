/**
 * Trust Grammar — the ONE vocabulary for every trust surface (PROP-002).
 *
 * Every trust ring in the product — the Trust Circle, the per-turn assurance
 * dial, the mini glyph (roster / chat header / coordination tree / capsule),
 * the Quality chips and the Assurance Inbox — draws its words, ranks and
 * colour aliases from THIS module. A surface that re-declares any of them is
 * the "five hand-rolled posture ladders" defect one level up, and the grammar
 * gate (test/test_trust_circle_grammar.mjs) asserts every consumer's fallback
 * literal equals the canonical map here.
 *
 * THE FOUR CHANNELS — each visual channel has exactly one job, on every
 * surface (TRUST_STATUS_COMMUNICATION.md §4/§9):
 *
 *   colour        ATTENTION PRIORITY — "how urgently should a human look here"
 *                 (quiet · worth a look · needs you now · absence family grey).
 *                 Never a verdict: the verdict is the WORD on the chip.
 *   shape/fill    the STATE FAMILY, legible without hue (Rule 9.4):
 *                 pass solid · warn half-fill · danger 2px outline ·
 *                 na sparse dash · pending dense MOVING dash · unknown
 *                 ultra-sparse dash.
 *   the dot       ENGRAVING — ONE mark, ONE meaning: a single state-coloured
 *                 dot = a mechanism ACTED on this subject (this turn, or ≥1 turn in
 *                 the window). No pairs, no count channel — the I/II depth is
 *                 retired. Global — identical on the dial and the ring
 *                 (Rule 9.3, no longer per-surface).
 *   accent orange the ACTIONABILITY channel — "you can act here / an act is
 *                 owed" (the owed dot, hover/focus, the plus). Never a state.
 *
 * THE ALTITUDE RULE — surface does not change meaning; altitude does:
 *   estate  worst-of across agents — `x/y agents`
 *   agent   configuration · execution — mode word · windowed rate
 *   window  rates over scored turns — `n/N turns`, boundary always named
 *   turn    EVENTS — states are outcomes, dots are acts, nothing is a rate
 *
 * Plain script (same pattern as qualityDimensions.js): loaded BEFORE every
 * consumer via templates/index.html and exposed on both `window` and
 * `globalThis` so the grammar gate can execute consumers under Node with the
 * primary path live.
 */
(function () {
    'use strict';

    // ── the six states ──────────────────────────────────────────────────────
    // The per-turn dial proved four states are not enough: `pending` (not in
    // yet — owes you a second look) and `unknown` (ran, outcome unrecorded —
    // never summarised as a pass) are real, distinct facts. They are part of
    // the grammar on EVERY surface; a renderer fed one must draw it, never
    // drop it (the mini glyph once rendered them as invisible segments).
    // `watching` (TRUST_CIRCLE_LEGIBILITY.md W1) — a CHOSEN posture that records and
    // stops nothing (observe / observe + disclose), with nothing found. Never amber (a
    // chosen setting is not an alarm — Rule 4.4 extended); it draws in the QUIET colour
    // because nothing needs you, and HOLLOW because nothing is protected — the shape and
    // the word carry "not protecting", never the hue. It exists only at the
    // window/agent/estate altitudes — a TURN has events, never a posture.
    const STATES = ['pass', 'warn', 'danger', 'na', 'pending', 'unknown', 'watching'];

    // ── colour = attention priority (the four attention words) ──────────────
    const ATTN_WORD = { pass: 'quiet', warn: 'worth a look', danger: 'needs you now', na: 'not turned on' };

    // ── the absence family — three kinds of "nothing here", never folded ────
    //   na       nothing is owed (a chosen posture)
    //   pending  not in yet (owes you a second look — Rule 9.1)
    //   unknown  ran, outcome unrecorded (never mistakable for a pass)
    //   watching turned on, records only, stops nothing (a chosen posture)
    const ABSENCE_WORD = { na: 'not turned on', pending: 'not in yet', unknown: 'not recorded',
                           watching: 'watching' };

    /** The word for any state. Attention words first, absence family second,
     *  '' for a state we cannot interpret — a wrong word is worse than none. */
    function word(state) { return ATTN_WORD[state] || ABSENCE_WORD[state] || ''; }

    // ── ranks ───────────────────────────────────────────────────────────────
    // ATTN_RANK — the window/agent/estate altitudes (states are postures and
    // rates; `na` sits below `pass` everywhere, including strip connectors).
    // `watching` sits between `na` and `pass`: more than nothing, less than a
    // protection — but it is NOT an attention level, so the attention fold reads the
    // three attention levels first and falls back to it (see `attention()`).
    const ATTN_RANK = { na: 0, watching: 1, pass: 2, warn: 3, danger: 4 };
    // TURN_RANK — the per-turn altitude (states are events): `unknown`
    // outranks `pass` (a gate whose outcome the turn does not record must
    // never be summarised as though it had passed), `pending` sits above pass
    // (it owes you a second look) and below any real finding.
    const TURN_RANK = { na: 0, pass: 1, pending: 2, unknown: 3, warn: 4, danger: 5 };

    // ── the mark channel (engraving) — GLOBAL semantics ─────────────────────
    // "An act is OWED" is deliberately NOT this mark: it rides the accent-orange
    // actionability channel (the owed dot). The retired owed double-tick once
    // inverted the dial's meaning of the same mark — the exact collision this
    // module exists to prevent.
    // The mark has ONE meaning now (the I/II depth channel is retired — a single
    // state-coloured dot = a mechanism acted; the accent dot = an act is owed). Key `2`
    // is kept as the API surface consumers already read; key `1` is gone with the
    // "has detail" fact it named.
    const TICK_ARIA = { 2: 'a mechanism acted' };

    // ── quality-band → state aliasing (one colour vocabulary) ───────────────
    // The eval layer's bands (healthy/review/danger) are the SAME three
    // attention levels wearing verdict-era names. Alias them so the Quality
    // chips, the Guide dots and the ring bands can never use four different
    // ambers for one meaning.
    const BAND_STATE = { healthy: 'pass', review: 'warn', danger: 'danger' };

    // ── posture → state / label (ONE map each; mirrors core/posture_modes.py) ──
    // A configured gate mode is a CHOICE, never a finding. Three surfaces (ring,
    // posture matrix, anatomy) each held their own copy of this map and disagreed
    // about `observe`; every reader now asks here. Pinned byte-equal to the Python
    // declaration by test_assurance_points.
    //   protecting (enforce / review)   → pass      nothing is owed
    //   watching   (observe / annotate) → watching  turned on, stops nothing
    //   off                             → na        not turned on
    const POSTURE_STATE = { off: 'na', observe: 'watching', annotate: 'watching',
                            enforce: 'pass', review: 'pass' };
    // The reader's word for a STORED mode — the editor's own vocabulary (Observe /
    // Enforce + "disclose" / "keep for review"). `annotate` and `review` are storage
    // tokens; they never reach a reading surface bare.
    const POSTURE_LABEL = { off: 'off', observe: 'watch', annotate: 'watch + disclose',
                            enforce: 'enforce', review: 'enforce + keep for review' };
    function postureState(mode) { return POSTURE_STATE[String(mode || '').toLowerCase()] || 'na'; }
    function postureLabel(mode) {
        const m = String(mode || '').toLowerCase();
        return POSTURE_LABEL[m] || m;
    }
    /** Attention first, posture second, absence last. A real attention reading
     *  (pass/warn/danger) on either side decides; otherwise a `watching` side is the
     *  reading; otherwise `na`. `na` never masks a reading and never invents one. */
    function attention(a, b) {
        const ATT = { pass: 1, warn: 2, danger: 3 };
        const live = [a, b].filter((s) => s && ATT[s]);
        if (live.length) return live.reduce((w, s) => (ATT[s] > ATT[w] ? s : w), 'pass');
        return (a === 'watching' || b === 'watching') ? 'watching' : 'na';
    }
    /** Estate aggregation — worst-of, one declared policy (Rule 5.1):
     *  any danger → danger · any warn → warn · all pass → pass · all na → na ·
     *  otherwise `watching` (a mix of protecting and watching members is a posture
     *  fact — the value carries the magnitude — never a finding). */
    function postureAggregate(states) {
        if (!states.length) return 'na';
        if (states.includes('danger')) return 'danger';
        if (states.includes('warn')) return 'warn';
        if (states.every((s) => s === 'pass')) return 'pass';
        if (states.every((s) => s === 'na')) return 'na';
        return 'watching';
    }

    // Token references, never hardcoded hex (Rule 9.5) — the hex here is only
    // the var() fallback and mirrors trust.css.
    const STATE_TOKEN = {
        pass: 'var(--trust-pass, #34d399)',
        warn: 'var(--trust-warn, #fbbf24)',
        danger: 'var(--trust-danger, #f87171)',
        na: 'var(--trust-na, #94a3b8)',
        pending: 'var(--text-muted, #94a3b8)',   // NEVER amber (Rule 3b.8) and never --trust-na (Rule 9.1)
        unknown: 'var(--trust-na, #94a3b8)',
        watching: 'var(--trust-pass, #34d399)', // quiet (nothing needs you) — hollow, never filled
    };

    /** The colour token for a quality band ('healthy'|'review'|'danger'),
     *  muted for anything else. Use in inline styles; combine with
     *  `color-mix(in srgb, <token> N%, transparent)` for washes. */
    function bandColor(band) {
        const st = BAND_STATE[band];
        return st ? STATE_TOKEN[st] : 'var(--text-muted, #94a3b8)';
    }

    // ── the severity CONSTRUCT (PROP-002b) — one level, one behaviour ───────
    // What a severity level MEANS is defined once, as behaviour across every
    // surface: word, ring state it folds to while OPEN, banner tone, badge
    // membership, and what it demands. Mirror of
    // assurance_points.SEVERITY_CONSTRUCT — test_assurance_points pins the two
    // equal. `floor: true` = never filterable by a station's reporting level
    // (a breaker trip that can be configured silent is a false green).
    const SEVERITY_CONSTRUCT = {
        danger: { word: 'needs you now', state: 'danger', tone: 'error',
                  badge: true, fold: true, demand: 'ack_or_decision', floor: true },
        warning: { word: 'worth a look', state: 'warn', tone: 'warning',
                   badge: true, fold: true, demand: 'ack', floor: false },
        info: { word: 'for the record', state: null, tone: 'info',
                badge: false, fold: false, demand: null, floor: false },
    };
    const SEVERITY_WORD = { danger: 'needs you now', warning: 'worth a look', info: 'for the record' };
    const SEVERITY_STATE = { danger: 'danger', warning: 'warn', info: null };

    // Per-station REPORTING levels — the one configurable layer (assigned by the
    // admin from the one construct; served on the inbox payload). Presentation,
    // never creation.
    const REPORTING_LEVELS = ['all', 'warning', 'danger'];
    const STATION_REPORTING_DEFAULT = 'warning';
    const _SEV_RANK = { info: 0, warning: 1, danger: 2 };
    const _LEVEL_MIN = { all: 0, warning: 1, danger: 2 };

    /** Does an item at `severity` SURFACE (badge · colour fold · default view)
     *  under its station's reporting level? Danger always (the floor); an
     *  unknown severity behaves as warning — an item we cannot rank must not
     *  vanish. Mirror of assurance_points.severity_surfaces. */
    function severitySurfaces(severity, station, reporting) {
        if (severity === 'danger' || (SEVERITY_CONSTRUCT[severity] || {}).floor) return true;
        const lvl = (reporting || {})[station] || STATION_REPORTING_DEFAULT;
        return (_SEV_RANK[severity] ?? 1) >= (_LEVEL_MIN[lvl] ?? 1);
    }

    // ── the eight stations — ONE registry (W5; mirrors assurance_points.STATION_TABLE) ──
    // key = the wire key (persisted; `scored` displays JUDGED) · label · sub = the
    // station's character (Rule 3.3b) · q = the question it answers · lane = WHEN it
    // speaks (in flight / after the turn / an act / a decision / the record / the
    // aggregate — the lane cut, W3). Seven JS copies
    // and four subtitle copies used to hold this and drifted; every surface now reads
    // here, and test_assurance_points pins this byte-equal to the Python table.
    const STATIONS = [
        { key: 'signed',   label: 'Signed',   sub: 'born, then attested',        q: 'Is it signed, and attested here?',
          lane: 'an act · attested, not measured' },
        { key: 'grounded', label: 'Grounded', sub: 'truth · faithfulness',       q: 'Does it stay on its corpus?',
          lane: 'in flight · can change the answer' },
        { key: 'guarded',  label: 'Guarded',  sub: 'rules · safety',             q: 'Does it stay within the rules?',
          lane: 'in flight · can change the answer' },
        { key: 'bounded',  label: 'Bounded',  sub: 'authority, borrowed',        q: 'Does it act only with the authority it was given?',
          lane: 'in flight · can change the answer' },
        { key: 'scored',   label: 'Judged',   sub: 'execution quality, graded',  q: 'How did its answers grade?',
          lane: 'after the turn · grades only' },
        { key: 'trusted',  label: 'Trusted',  sub: 'the badge breathes',         q: 'Is it trusted over time?',
          lane: 'the aggregate · one standing over the record' },
        { key: 'reviewed', label: 'Reviewed', sub: 'a human decides',            q: 'Is a person deciding?',
          lane: 'a decision · a person in the path' },
        { key: 'proven',   label: 'Proven',   sub: 'receipts, not promises',     q: 'Can its answers be proven?',
          lane: 'the record · sealed after delivery' },
    ];
    const STATION_KEYS = STATIONS.map((r) => r.key);
    const _STATION_ROW = Object.fromEntries(STATIONS.map((r) => [r.key, r]));
    function stationRow(key) { return _STATION_ROW[String(key || '').toLowerCase()] || null; }
    /** The upper-case display label every surface prints (`scored` → JUDGED). An
     *  unknown key is upper-cased so it stays visible rather than silently renamed. */
    function stationLabel(key) {
        const r = stationRow(key);
        return (r ? r.label : String(key || '')).toUpperCase();
    }

    // The eight station ICONS — ONE registry (monochrome 24-viewBox stroke paths), read by
    // the Trust Circle AND the per-turn dial, so a station looks the same on the answer
    // and on the ring. Each consumer keeps a fallback literal pinned equal by the gate.
    const STATION_ICONS = {
        signed:   '<circle cx="12" cy="10" r="6"/><path d="M9.5 10l1.8 1.8L15 8.2"/><path d="M8.5 15.4L7 21l5-2.4L17 21l-1.5-5.6"/>',
        grounded: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/><line x1="12" y1="1.5" x2="12" y2="5"/><line x1="12" y1="19" x2="12" y2="22.5"/><line x1="1.5" y1="12" x2="5" y2="12"/><line x1="19" y1="12" x2="22.5" y2="12"/>',
        guarded:  '<path d="M12 2l8 4v6c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V6l8-4z"/>',
        bounded:  '<rect x="5" y="10" width="14" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/><circle cx="12" cy="15" r="1.4"/>',
        scored:   '<rect x="5" y="3.5" width="14" height="17" rx="2"/><path d="M9 3.5V6h6V3.5"/><path d="M8.5 11l2 2 4-4"/><line x1="8.5" y1="16.5" x2="15.5" y2="16.5"/>',
        trusted:  '<path d="M2.5 12h4l2.5-6 4 12 2.5-6h6"/>',
        reviewed: '<path d="M15 20v-1.6a3.6 3.6 0 0 0-3.6-3.6H5.6A3.6 3.6 0 0 0 2 18.4V20"/><circle cx="8.5" cy="7.5" r="3.5"/><path d="M15.5 9.5l2 2 4.5-4.5"/>',
        proven:   '<path d="M6 2.5h12v19l-2-1.4-2 1.4-2-1.4-2 1.4-2-1.4-2 1.4z"/><line x1="9" y1="7.5" x2="15" y2="7.5"/><line x1="9" y1="11" x2="15" y2="11"/><line x1="9" y1="14.5" x2="13" y2="14.5"/>',
    };

    const API = {
        STATIONS, STATION_KEYS, stationRow, stationLabel, STATION_ICONS,
        STATES, ATTN_WORD, ABSENCE_WORD, word,
        ATTN_RANK, TURN_RANK, TICK_ARIA,
        BAND_STATE, STATE_TOKEN, bandColor,
        POSTURE_STATE, POSTURE_LABEL, postureState, postureLabel, attention, postureAggregate,
        SEVERITY_WORD, SEVERITY_STATE,
        SEVERITY_CONSTRUCT, REPORTING_LEVELS, STATION_REPORTING_DEFAULT, severitySurfaces,
    };

    // Exposed on BOTH roots: browsers read `window`, the grammar gate executes
    // consumers under Node where only `globalThis` exists (and stubs a partial
    // `window` of its own).
    if (typeof globalThis !== 'undefined') globalThis.TrustGrammar = API;
    if (typeof window !== 'undefined' && window !== (typeof globalThis !== 'undefined' ? globalThis : window)) {
        window.TrustGrammar = API;
    }
})();
