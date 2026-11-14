/**
 * The Assurance dial — one instrument, two scales (migration R-2).
 *
 * Replaces the Trust Journey strip in Live Status and the mini-ring + linear glyph row
 * in the answer capsule. Those were two renderings of the same facts that had drifted
 * apart; collapsing them into one instrument is the point, not a side effect.
 *
 * IT DERIVES NOTHING.
 * The strip carried its own ladder over the turn's stamps, which is how it enumerated
 * ten stages while six real gates went unrendered. This module renders `turn.assurance`
 * — produced server-side by `agent/assurance_points.py`, the single registry — and owns
 * no mapping from a stamp to a state. If a reading looks wrong, the registry is wrong,
 * and there is exactly one place to fix it.
 *
 * FORM RULES IT IMPLEMENTS (contract §9)
 *   9.2  the star marks the subject; NO plus here — you cannot change the agent from an
 *        answer, and a mark must not promise what its surface cannot do
 *   9.3  ONE mark, ONE meaning (PROP-002 as amended): a single state-coloured dot
 *        = a mechanism ACTED — no count channel, identical on every surface; "an act
 *        is owed" is the ring's accent owed dot, never this mark
 *   9.4  weight before colour — segment stroke width carries state before hue does
 *   9.6  a station section LOCATES (highlight + scroll); it never filters
 *   9.7  the reading is written into a fixed-height region, so a pointer crossing the
 *        dial cannot reflow the panel underneath it
 *
 * Sections ≡ stations, 1:1, derived from ORDER. A parallel list is how the trace
 * stopped being obvious the first time.
 */
(function () {
    'use strict';

    // The ONE station registry (trustGrammar.STATIONS — W5); the literal is the
    // absence-tolerant fallback, gate-pinned equal to the canonical.
    const _TGS = (typeof window !== 'undefined' && window.TrustGrammar && window.TrustGrammar.STATIONS)
        || (typeof globalThis !== 'undefined' && globalThis.TrustGrammar && globalThis.TrustGrammar.STATIONS)
        || null;
    const ORDER = _TGS ? _TGS.map((r) => r.key) : ['signed', 'grounded', 'guarded', 'bounded',
                   'scored', 'trusted', 'reviewed', 'proven'];
    const SEG = 360 / ORDER.length;
    const GAP = 6;

    // Rank for worst-of — the PER-TURN rank (TrustGrammar.TURN_RANK, PROP-002; the
    // literal is the absence-tolerant fallback, gate-pinned equal to it). `unknown`
    // outranks `pass`: a gate whose outcome the turn does not record must never be
    // summarised as though it had passed. On a pre-tracing turn the registry has
    // already rewritten unknown → na, so this never alarms on history. `pending`
    // sits above pass (it owes you a second look) and below a real finding. It is
    // NOT a flavour of `na`: folding "has not reported yet" into "switched off" is
    // the defect Rule 9.1 exists to prevent.
    const RANK = (window.TrustGrammar && window.TrustGrammar.TURN_RANK)
        || { na: 0, pass: 1, pending: 2, unknown: 3, warn: 4, danger: 5 };

    // A BAND is not a STATE, and THIS FILE CONVERTS NEITHER. The registry resolves each
    // measurement's contribution server-side and ships it as `st`, already in the state
    // vocabulary, so there is nothing here to get wrong — a missing `st` degrades to
    // `na` (counted, never colouring), never to a guess.
    //
    // There used to be three inline `b === 'healthy' ? 'pass' : b` aliases. They looked
    // right because the two vocabularies overlap on `danger` and `healthy` was special-
    // cased — leaving `review`, the ONE value where they genuinely differ, to fall
    // through as an unrecognised word ranking below `na`. An amber measurement therefore
    // could never raise its station: GROUNDED read "quiet" directly above its own
    // `context_utilization 0.56`, for a whole release, past 387 assertions that only
    // ever fixtured `healthy` and `danger`. The gate now forbids band conversion here.
    const _st = (d) => ((d && RANK[d.st] !== undefined) ? d.st : 'na');

    // The REVERSE relation: a MECHANISM has a state but no band, and the Guide's context
    // rows are band-shaped. Derived by inverting the one declaration rather than written
    // out again — a hand-copied inverse is the same defect wearing a mirror.
    const BAND_OF = (() => {
        const src = (window.TrustGrammar && window.TrustGrammar.BAND_STATE)
            || { healthy: 'pass', review: 'warn', danger: 'danger', na: 'na' };
        const inv = {};
        for (const band of Object.keys(src)) inv[src[band]] = band;
        return inv;
    })();

    // The vocabulary comes from ONE source (TrustGrammar — PROP-002). Never re-declare
    // it here — a sixth copy is how `review` fell out of a posture ladder. The absence
    // family (`pending` → "not in yet", `unknown` → "not recorded") resolves FIRST:
    // delegating those to the attention lookup returned '' and fell through to a
    // wrong word — the "not in yet" branch was unreachable whenever TrustCircle was
    // loaded, so `pending` carried three different words on one instrument.
    function word(state) {
        const TG = window.TrustGrammar;
        if (TG && TG.word) return TG.word(state);
        if (window.TrustCircle && window.TrustCircle.attentionWord) {
            return window.TrustCircle.attentionWord(state)
                || (state === 'pending' ? 'not in yet' : 'not recorded');
        }
        if (state === 'pending') return 'not in yet';
        return state === 'unknown' ? 'not recorded' : '';
    }


    // Per-station icons, verbatim from the approved prototype. Without them the
    // segments are anonymous arcs: you can see that something is amber but not WHICH
    // guarantee is amber, which is most of the reading.
    // Icons from the ONE registry (trustGrammar.STATION_ICONS — shared with the Trust
    // Circle); the literal is the absence-tolerant fallback, gate-pinned equal.
    const ICONS = ((typeof window !== 'undefined' && window.TrustGrammar) || (typeof globalThis !== 'undefined' && globalThis.TrustGrammar) || {}).STATION_ICONS
        || {
        signed:   '<circle cx="12" cy="10" r="6"/><path d="M9.5 10l1.8 1.8L15 8.2"/><path d="M8.5 15.4L7 21l5-2.4L17 21l-1.5-5.6"/>',
        grounded: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/><line x1="12" y1="1.5" x2="12" y2="5"/><line x1="12" y1="19" x2="12" y2="22.5"/><line x1="1.5" y1="12" x2="5" y2="12"/><line x1="19" y1="12" x2="22.5" y2="12"/>',
        guarded:  '<path d="M12 2l8 4v6c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V6l8-4z"/>',
        bounded:  '<rect x="5" y="10" width="14" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/><circle cx="12" cy="15" r="1.4"/>',
        scored:   '<rect x="5" y="3.5" width="14" height="17" rx="2"/><path d="M9 3.5V6h6V3.5"/><path d="M8.5 11l2 2 4-4"/><line x1="8.5" y1="16.5" x2="15.5" y2="16.5"/>',
        trusted:  '<path d="M2.5 12h4l2.5-6 4 12 2.5-6h6"/>',
        reviewed: '<path d="M15 20v-1.6a3.6 3.6 0 0 0-3.6-3.6H5.6A3.6 3.6 0 0 0 2 18.4V20"/><circle cx="8.5" cy="7.5" r="3.5"/><path d="M15.5 9.5l2 2 4.5-4.5"/>',
        proven:   '<path d="M6 2.5h12v19l-2-1.4-2 1.4-2-1.4-2 1.4-2-1.4-2 1.4z"/><line x1="9" y1="7.5" x2="15" y2="7.5"/><line x1="9" y1="11" x2="15" y2="11"/><line x1="9" y1="14.5" x2="13" y2="14.5"/>',
    };

    // Rule 9.2 — the star marks the subject. No plus: you cannot change the agent from
    // an answer, and a mark must not promise what its surface cannot do.
    const STAR = 'M0,-11 L2.7,-3.72 L10.46,-3.4 L4.37,1.42 L6.47,8.9 L0,4.6 '
               + 'L-6.47,8.9 L-4.37,1.42 L-10.46,-3.4 L-2.7,-3.72 Z';

    // Labels from the same registry (JUDGED on the `scored` wire key).
    const LABEL = _TGS ? Object.fromEntries(_TGS.map((r) => [r.key, r.label])) : {
        signed: 'Signed', grounded: 'Grounded', guarded: 'Guarded', bounded: 'Bounded',
        scored: 'Judged', trusted: 'Trusted', reviewed: 'Reviewed', proven: 'Proven',
    };
    const stationLabel = (k) => String(LABEL[k] || k || '').toUpperCase();

    const esc = (s) => String(s == null ? '' : s)
        .replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

    // ── the model: registry readings → per-station dial data ──────────────────
    // The ONLY computation in this file, and it is aggregation, not derivation: state is
    // worst-of its mechanisms, and the acted dot marks where one of them acted.
    /** The point that DROVE a station to `state`: one that acted, else one sitting at
     *  that state, else the worst-ranked. Never `points[0]` — when a failing DIMENSION
     *  raises the state, no point matches it, and the first point is simply whichever
     *  reader registered first. That is how a red GROUNDED came to read
     *  "no model bound · no knowledge model bound to this turn" — the trusted-scope
     *  reader's `na` word and detail, quoted beside confirmed drift. */
    const _driverPoint = (pts, state) => (pts || []).find((p) => p.acted)
        || (pts || []).find((p) => p.state === state)
        || (pts || []).slice().sort((a, b) => (RANK[b.state] || 0) - (RANK[a.state] || 0))[0];

    /** Dimensions of a station's points sitting at `state` — the measures that drove it
     *  there when no mechanism did. Labels come from the Assurance Guide so the reading
     *  and the Guide entry it links to cannot use two names for one dimension. */
    function _failingDims(d, state) {
        const QD = (typeof window !== 'undefined' && window.QualityDimensions) || null;
        const seen = new Set();
        const out = [];
        for (const dim of (Array.isArray(d.dims) ? d.dims : [])) {
            // The CONTRIBUTION, because that is what drove the station here. A pair the
            // judge cleared contributes `warn` while still measuring `danger` (Rule
            // 3.0c) — naming it under the state it did not cause would credit the
            // station's colour to the wrong finding.
            if (!dim || !dim.k) continue;
            if (_st(dim) !== state || seen.has(dim.k)) continue;
            seen.add(dim.k);
            out.push(QD && QD.label ? QD.label(dim.k) : dim.k);
        }
        return out;
    }


    /** The one-line reading for a station: its label, its attention word, and a detail
     *  that explains THAT state. Pure, so it is testable without a DOM — every earlier
     *  defect in this string was found by eye because the gate could only read source. */
    /** Put the scorecard in its ONE home. Declared once; the dial owns it because the
     *  dial owns the slot.
     *
     *  MIGRATED MEANS MIGRATED. The scorecard IS JUDGED's content, so when the dial is on
     *  screen it lives in JUDGED's section and the card's shell collapses — permanently,
     *  not per-pin. Pinning JUDGED reveals it exactly as pinning any station reveals that
     *  station's content; the ring's own summary already names JUDGED when it needs
     *  attention, so the reader is never left guessing that there is something to open.
     *
     *  A previous cut made placement follow the PIN, on the theory that results sitting
     *  inside a hidden section were a defect. They are not — that is the migration
     *  working. The actual defect was narrower: `qc-adopted` was applied by the ui.js
     *  adoption only, and on RELOAD the card is created BEFORE the dial injects, so that
     *  path never marked the shell and left an empty container behind. Following the pin
     *  fixed nothing and added an oddity of its own: a Scorecard box appearing because
     *  the reader pinned GROUNDED, a station it has nothing to do with.
     *
     *  No dial (the legacy strip path) ⇒ no slot ⇒ the results stay on the card, which is
     *  their home in that world. Idempotent and defensive: a placement failure must never
     *  cost the reader the scorecard. */
    const SCORECARD_SLOT = '#assurance-dial .ad-scorecard-slot';
    function placeScorecard(root) {
        try {
            const host = root || document;
            const res = document.getElementById('trajectory-eval-results');
            if (!res) return;
            const card = document.getElementById('trajectory-eval-overlay');
            // ONLY the Live Status dial's slot (#assurance-dial): the scorecard is the Live
            // Status turn's measures. A capsule dial is another answer's, and taking the one
            // scorecard into it emptied JUDGED in Live Status — and showed the wrong turn's
            // measures in the capsule (a capsule's JUDGED lists its own: `measureChips`).
            const slot = host.querySelector(SCORECARD_SLOT) || document.querySelector(SCORECARD_SLOT);
            if (slot) {
                if (!slot.contains(res)) slot.appendChild(res);
                res.classList.remove('px-3', 'pb-2');
                if (card) card.classList.add('qc-adopted');
            } else if (card) {
                if (!card.contains(res)) card.appendChild(res);
                res.classList.add('px-3', 'pb-2');
                card.classList.remove('qc-adopted');
            }
        } catch (_) { /* the scorecard stays where it is — never break the dial */ }
    }

    function stationReading(d, k) {
        const lead = _driverPoint(d.points, d.state);
        // If nothing among the points reaches the station's state, a MEASURE drove it.
        // Naming a mechanism there would be wrong twice: it did not cause this, and it
        // reported something quieter. Name the measures instead.
        const drivers = (RANK[d.state] || 0) > (RANK[(lead || {}).state] || 0)
            ? _failingDims(d, d.state) : [];
        // The verb follows the STATE. "is failing" was written when only a `danger`
        // measurement could reach here — because `review` could not fold at all — so it
        // over-claimed the moment amber started arriving. A review band asks for a look;
        // it does not convict, and saying it does is the same over-statement the
        // scorecard was corrected for ("flagged for review", not "failed").
        const verb = d.state === 'danger'
            ? (drivers.length === 1 ? 'is failing' : 'are failing')
            : (drivers.length === 1 ? 'is in the review band' : 'are in the review band');
        const detail = drivers.length
            ? `${drivers.join(', ')} ${verb}`
            : String((lead || {}).detail || '').replace(/^[^:]*:\s*/, '');
        return detail ? `${LABEL[k]} — ${stationWord(d)} · ${detail}`
                      : `${LABEL[k]} — ${stationWord(d)}`;
    }

    function stations(assurance) {
        const by = (assurance && assurance.by_station) || {};
        // The station's SECOND population, allocated by the registry partition — NOT
        // gathered from whichever mechanisms rendered. Ownership is a static fact, so a
        // measurement reaches its station by lookup; it can no longer be lost because
        // the mechanism that owns it does not apply to this agent's class. Absent ⇒ the
        // turn has not been scored yet (a live turn is graded after the answer).
        const meas = (assurance && assurance.measures) || {};
        const out = {};
        for (const k of ORDER) {
            // Every entry is validated, not assumed. This renders a trace view: a
            // malformed reading must degrade to a quieter one, never take the panel
            // down with it — the trace is most needed exactly when something is wrong.
            const pts = (Array.isArray(by[k]) ? by[k] : [])
                .filter((p) => p && typeof p === 'object' && typeof p.state === 'string');
            const dims = (Array.isArray(meas[k]) ? meas[k] : [])
                .filter((d) => d && typeof d === 'object' && d.k);
            let worst = 'na';
            let acted = false;
            for (const p of pts) {
                if ((RANK[p.state] || 0) > (RANK[worst] || 0)) worst = p.state;
                if (p.acted) acted = true;
            }
            // The station's scored dimensions, as the server allocated them. Since the lane
            // cut `allocate_measurements` puts EVERY one at JUDGED (wire key `scored`), so a
            // guarantee station receives none and its colour is the worst of its MEMBERS
            // alone; this loop only ever moves JUDGED. Each dimension is still counted once.
            for (const d of dims) {
                const st = _st(d);
                if ((RANK[st] || 0) > (RANK[worst] || 0)) worst = st;
            }
            // ONE dot, ONE job (the count channel is dead): a mark means A GATE
            // ACTED, nothing else. The old I/II depth (1 = detail engraved, 2 =
            // acted) survived the tick→dot rename as a two-dot pair — but the ring
            // only ever rendered the pair, the estate scope rendered nothing, and
            // three surfaces speaking different subsets of one "global" channel is
            // the meaning-drift this grammar exists to end. "The click will pay"
            // (Rule 9.3) is carried by the state itself now — a station with a
            // reading has detail behind it, and a dashed/absent one does not.
            const marks = acted ? 1 : 0;
            // The word comes from the mechanism DRIVING the state, because `na` collapses
            // several different facts and the generic word ("not turned on") is false for
            // most of them: TRUSTED is on and simply has no per-turn reading; REVIEWED is
            // available and nothing was parked. The reader that knows supplies the word.
            const lead = _driverPoint(pts, worst);
            // A custom word names a SPECIFIC fact. If the point carrying it is not what
            // drove the state, the word is about something else — fall back to the plain
            // attention word rather than describe the station with a quieter point's news.
            const ownWord = lead && lead.state === worst ? (lead.word || '') : '';
            // `dims` travels ON the station, because it BELONGS to the station. Every
            // consumer below reads it from here — the panel row, the chip list, the
            // count — so no surface re-derives which measurements a station holds.
            out[k] = { state: worst, marks, points: pts, dims, word: ownWord };
        }
        return out;
    }

    function summarise(st, assurance) {
        const acted = ORDER.filter((k) => st[k].points.some((p) => p.acted));
        const reported = ORDER.filter((k) => st[k].points.some(
            (p) => p.state === 'pass' || p.state === 'warn' || p.state === 'danger'));
        let worst = 'na';
        for (const k of ORDER) if ((RANK[st[k].state] || 0) > (RANK[worst] || 0)) worst = st[k].state;

        if (assurance && assurance.predates_tracing) {
            // V4 — never "not turned on" for a turn written before the trace existed.
            return { worst: 'na', text: 'This turn predates the assurance trace — '
                                      + 'what ran was not recorded.' };
        }
        if (!acted.length) {
            // A station can read amber without any mechanism having ACTED — an
            // unresolved reading, a station with nothing configured. Saying "every
            // layer quiet" beside a coloured segment is two slots contradicting each
            // other, which is the defect the whole grammar exists to prevent.
            const waiting = ORDER.filter((k) => st[k].state === 'pending');
            if (waiting.length) {
                return { worst: 'pending', text: waiting.length === ORDER.length
                    ? 'This turn is still running — nothing has reported yet.'
                    : `${ORDER.length - waiting.length} of ${ORDER.length} reported so far.` };
            }
            if (worst === 'warn' || worst === 'danger' || worst === 'unknown') {
                // Name every station wanting attention, each in ITS OWN word. Reporting
                // only the worst rank dropped the amber ones, and "need a look" was the
                // warn phrase applied to danger — so the summary said two stations in
                // warn vocabulary while the header beside it counted three across two
                // severities. Two slots contradicting each other about the same turn.
                const named = (state) => ORDER.filter((k) => st[k].state === state)
                                              .map((k) => LABEL[k]);
                const bits = [];
                const urgent = named('danger');
                const look = named('warn');
                // `unknown` is "not recorded" — a gap in the trace, not a finding. Folding
                // it into "worth a look" made the summary claim four stations wanted
                // attention while the header beside it counted one.
                const silent = named('unknown');
                if (urgent.length) bits.push(`${urgent.join(', ')} `
                    + `${urgent.length === 1 ? 'needs' : 'need'} you now`);
                if (look.length) bits.push(`${look.join(', ')} worth a look`);
                if (silent.length) bits.push(`${silent.join(', ')} not recorded`);
                return { worst, text: `Nothing acted on this answer · ${bits.join(' · ')}.` };
            }
            return { worst, text: reported.length
                ? `${reported.length} of ${ORDER.length} stations reported — every layer quiet.`
                : 'No layer reported on this turn.' };
        }
        const names = acted.map((k) => LABEL[k]);
        const list = names.length === 1 ? names[0]
            : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
        return { worst, text: `${list} acted on this answer.` };
    }


    /**
     * The instrument's headline — where to look, not a state name.
     *
     * Printing the worst-of state word here produced "ASSURANCE not recorded", which
     * reads as "none of this was recorded" when the truth was "one station out of eight
     * has no record". A whole-instrument headline has to be a count, not a label.
     */
    function routing(st) {
        const n = (s) => ORDER.filter((k) => st[k].state === s).length;
        const bits = [];
        if (n('danger')) bits.push(`${n('danger')} needs you now`);
        if (n('warn')) bits.push(`${n('warn')} worth a look`);
        // ONE word for pending everywhere ("not in yet") — before the vocabulary
        // was unified (PROP-002) this line, the chip and a dead fallback branch
        // each used a different phrase for the same state.
        if (n('pending')) bits.push(`${n('pending')} not in yet`);
        if (n('unknown')) bits.push(`${n('unknown')} not recorded`);
        if (bits.length) return bits.join(' · ');
        return n('pass') ? 'all quiet' : 'nothing to report';
    }

    // ── geometry (matches the approved prototype) ─────────────────────────────
    function polar(c, r, deg) {
        const a = (deg - 90) * Math.PI / 180;
        return [c + r * Math.cos(a), c + r * Math.sin(a)];
    }
    function arcPath(c, r, i) {
        const p0 = polar(c, r, i * SEG + GAP / 2);
        const p1 = polar(c, r, (i + 1) * SEG - GAP / 2);
        return `M ${p0[0].toFixed(2)} ${p0[1].toFixed(2)} A ${r} ${r} 0 0 1 `
             + `${p1[0].toFixed(2)} ${p1[1].toFixed(2)}`;
    }
    function linePath(c, r0, r1, deg) {
        const a = polar(c, r0, deg); const b = polar(c, r1, deg);
        return `M ${a[0].toFixed(2)} ${a[1].toFixed(2)} L ${b[0].toFixed(2)} ${b[1].toFixed(2)}`;
    }

    // Rule 9.4 — weight carries state before hue does, so it survives a colourblind
    // reader. Same ordering as the shape+fill grammar in trust.css.
    function strokeFor(state, base) {
        if (!Number.isFinite(base)) return 0;
        if (state === 'na') return base * 0.36;
        if (state === 'pending') return base * 0.7;
        if (state === 'unknown') return base * 0.6;
        if (state === 'warn') return base * 1.35;
        if (state === 'danger') return base * 1.6;
        return base;
    }

    function dialSvg(st, size) {
        const c = size / 2;
        const r = c - (size < 120 ? 15 : 19);
        const w = size < 120 ? 3 : 4;
        const ss = size < 120 ? 0.46 : 0.54;
        let o = `<circle class="ad-gnd" cx="${c}" cy="${c}" r="${r + (size < 120 ? 7 : 10)}"/>`
              + `<g transform="translate(${c},${c}) scale(${ss})">`
              + `<path class="ad-star-fill" d="${STAR}"/><path class="ad-star" d="${STAR}"/></g>`;
        ORDER.forEach((k, i) => {
            const d = st[k];
            const mid = i * SEG + SEG / 2;
            // ONE mark family, ONE meaning: a single state-coloured DOT = a gate
            // ACTED on this answer. No pairs, no depth channel — colour alone
            // separates the jobs (state colours = acted engraving; the accent dot
            // on the ring = an act is owed). Identical on every surface.
            let ticks = '';
            if (d.marks > 0) {
                const dr = r + (size < 120 ? 5.5 : 8);          // dot orbit radius
                const ds = size < 120 ? 1.5 : 1.8;              // dot radius
                const td = d.state === 'pending'
                    ? ` style="animation-delay:${(i * 0.16).toFixed(2)}s"` : '';
                // polar() returns an ARRAY [x, y] — reading .x/.y here crashed the
                // whole render for any turn with an ACTED station (dots only render
                // then), which — with `_settled` stamped pre-mount at the time —
                // froze the live dial forever and blanked reloads. Caught by the
                // headless repro's console capture, pinned by the dial gate below.
                const [dx, dy] = polar(c, dr, mid);
                ticks += `<circle class="ad-dot ad-dot-${esc(d.state)}"${td} cx="${dx.toFixed(2)}" cy="${dy.toFixed(2)}" r="${ds}"/>`;
            }
            // The mark reaches assistive tech with its ONE meaning (TrustGrammar
            // TICK_ARIA[2] — the acted word; the key survives as API surface from
            // the retired depth channel, the rendered mark is a single dot).
            const tickAria = (window.TrustGrammar && window.TrustGrammar.TICK_ARIA)
                || { 2: 'a mechanism acted' };
            const detail = d.marks ? `, ${tickAria[2] || 'a mechanism acted'}` : '';
            const ip = polar(c, r - (size < 120 ? 13 : 17), mid);
            const sc = size < 120 ? 0.458 : 0.6;
            const half = 12 * sc;
            // The glyph is stroked line-art with `fill: none`, so only the drawn pixels
            // were hittable — the icon LOOKED like the station's affordance and was not
            // one. A transparent disc behind it makes the whole glyph the target, which
            // is what it already appeared to be.
            const icon = `<circle class="ad-ico-hit" cx="${ip[0].toFixed(2)}"`
                       + ` cy="${ip[1].toFixed(2)}" r="${(half * 1.35).toFixed(2)}"/>`
                       + `<g class="ad-ico ad-ico-${esc(d.state)}" transform="translate(`
                       + `${(ip[0] - half).toFixed(2)},${(ip[1] - half).toFixed(2)}) `
                       + `scale(${sc})">${ICONS[k] || ''}</g>`;
            // Stagger the waiting animation by position, so the dashes chase each other
            // ROUND the ring rather than pulsing in place. The sweep therefore covers
            // only what is still outstanding, and visibly shrinks as stations land —
            // the extent of the motion IS the remaining work, which makes the waiting
            // state carry information rather than merely signal "busy".
            const wait = d.state === 'pending'
                ? ` style="animation-delay:${(i * 0.16).toFixed(2)}s"` : '';
            o += `<g class="ad-st" data-ad-station="${esc(k)}" tabindex="0" role="button"`
               + ` aria-label="${esc(`${LABEL[k]} — ${word(d.state)}${detail}`)}">`
               + `<path class="ad-hit" d="${arcPath(c, r, i)}" style="stroke-width:${size < 120 ? 18 : 24}"/>`
               + `<path class="ad-foc" d="${arcPath(c, r + (size < 120 ? 7 : 10), i)}"/>`
               + `<path class="ad-seg ad-seg-${esc(d.state)}" d="${arcPath(c, r, i)}"`
               + ` style="stroke-width:${strokeFor(d.state, w).toFixed(1)}`
               + `${d.state === 'pending' ? `;animation-delay:${(i * 0.16).toFixed(2)}s` : ''}"`
               + `/>${ticks}${icon}</g>`;
        });
        return o;
    }


    // Ledger R2 — a station click also LOCATES its step in the trace below. The strip
    // did this and the dial must not lose it: the fact would still be on screen, but
    // the route to its evidence would be gone (reach loss, §0).
    //
    // Locate, never filter (Rule 9.6): this scrolls and flashes; it removes nothing.
    // A reading is "guided" when it CARRIES dimensions — the registry decides which
    // mechanisms are measured and by what, and this file must not hold a second opinion.
    // The first version hardcoded four keys here, disagreed with the registry, and left
    // several rows with no link at all while others opened the full glossary.
    // A row is linkable when it has scored dimensions to show, OR its own Guide entry —
    // which every registered mechanism has. Keying on dimensions alone left the
    // unscored-but-real gates as dead ends.
    function guideDims(p) {
        if (Array.isArray(p.dims) && p.dims.length) return p.dims;
        return p && p.guide ? [{ k: p.guide.k, b: 'na' }] : null;
    }

    const TRACE_STEP = {
        grounding_gate: ['Grounding Gate'],
        honesty_gate: ['Honesty'],
        preflight_sql: ['Preflight'],
        inbound_guard: ['Guardrail'],
        outbound_guard: ['Guardrail'],
        evaluation: ['Agent Quality Judged'],
        governed_data: ['Governed Data Operations'],
        review_parking: ['Human Review'],
        tool_enforcement: ['Agent Authority'],
    };
    function scrollTraceTo(key) {
        try {
            const markers = TRACE_STEP[key];
            if (!markers) return;
            const host = document.getElementById('status-window-content');
            if (!host) return;
            for (const step of host.querySelectorAll('.status-step')) {
                const txt = step.textContent || '';
                if (markers.some((m) => txt.includes(m))) {
                    step.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    step.classList.add('ad-located');
                    setTimeout(() => step.classList.remove('ad-located'), 1400);
                    return;
                }
            }
        } catch (_) { /* locating is a convenience; never break the panel */ }
    }

    // ── rendering ─────────────────────────────────────────────────────────────
    // A station's word: its driving mechanism's, else the generic attention word.
    function stationWord(d) { return d.word || word(d.state); }

    // ── metric vocabulary ────────────────────────────────────────────────────
    // MODULE SCOPE, above every use. These lived INSIDE `sectionHtml`, BELOW the line
    // that calls `modulesHtml` — so `const` left them in the temporal dead zone at the
    // moment the call ran. Every inject() threw `Cannot access 'METRIC_FLAG' before
    // initialization`, and since `_settled` is stamped only after a successful mount,
    // each retry threw again: the dial sat on "8 not in yet" forever, with the throw
    // swallowed as a console warning that claimed the trace was unaffected.
    //
    // The gate DID execute `modulesHtml` — but it sliced the source starting at these
    // declarations, which put them before the call and silently reordered the very
    // thing that was broken. A renderer tested outside its own scope is not tested.
    // Kinds are DERIVED from the key, never from the value. `max_repeats: 1` rendered
    // as "100%" under a value-based rule — a count wearing a ratio's clothes, the same
    // defect class as rendering a scope size as a measurement. And a suffix rule that
    // covered only `_ratio` left `numeric_fidelity` reading as "1" rather than "100%".
    //
    // So the vocabulary is explicit and `test_assurance_substrate` asserts every
    // metric any assessor emits matches one kind: a new metric with an ambiguous name
    // fails the gate instead of rendering wrong.
    const METRIC_RATIO = /(_ratio|_fidelity|_rate)$/;
    const METRIC_SCORE = /_score$/;
    const METRIC_FLAG = /^(acted|restricted)$/;

    // "1 tool calls" is the same plural defect one level down — in the LABEL rather
    // than the sentence. A count of one takes a singular noun; a ratio or a flag has
    // no number to agree with, so its label never changes.
    function metricLabel(key, v) {
        const words = key.replace(/_/g, ' ');
        if (v !== 1 || METRIC_RATIO.test(key) || METRIC_SCORE.test(key)
            || METRIC_FLAG.test(key)) return words;
        return words.replace(/s\b/, '');
    }

    function fmtMetric(key, v) {
        if (!Number.isFinite(v)) return '';
        if (METRIC_FLAG.test(key)) return v ? 'yes' : 'no';
        if (METRIC_RATIO.test(key)) return `${Math.round(v * 100)}%`;
        if (METRIC_SCORE.test(key)) return v.toFixed(2);
        return Number.isInteger(v) ? String(v) : v.toFixed(2);
    }

    // ── the modules BEHIND a mechanism ──────────────────────────────────
    // A mechanism row said WHAT it did ("1 withheld") and never which module produced
    // the finding it acted on, nor what that module measured. Both facts exist —
    // `assurance_points.active_modules` re-assesses the turn through the CURRENTLY
    // BOUND assessor, so after a remap this shows the new module rather than a fossil
    // of the old one's output.
    //
    // Collapsed by default: the mechanism's own verdict is the headline, and a wall of
    // numbers under every row would bury it.
    const ROLE_MARK = '<svg viewBox="0 0 12 12" width="10" height="10" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1 6h6M5 3l3 3-3 3M10 2v8"/></svg>';
    function modulesHtml(p) {
        const mods = Array.isArray(p && p.modules) ? p.modules : [];
        if (!mods.length) return '';
        const acted = mods.filter((m) => m.triggered && !m.abstained).length;
        const abstained = mods.filter((m) => m.abstained).length;
        const quiet = mods.length - acted - abstained;
        const n = mods.length, noun = `${n} module${n === 1 ? '' : 's'}`;
        // The summary states what is inside, so opening is a decision rather than a
        // gamble — and a row with a finding says so before you open it. An abstention is
        // a module that RAN and declined to grade (its row says why) — it used to read
        // "none ran", as if the modules had not been consulted at all.
        const lead = acted
            ? `${acted} of ${n} found something`
            : (abstained === 0 ? `${noun}, all quiet`
               : quiet === 0 ? `${noun}, all abstained`
                             : `${noun}: ${quiet} quiet, ${abstained} abstained`);

        const rows = mods.map((m) => {
            // A FLAG is a word, not a number with a label: `acted: 0` rendered as
            // "no acted". A false flag is also dropped entirely — the summary already
            // says what happened, and "no acted" beside "no sensitive content found"
            // is the same fact twice in worse English.
            const vals = Object.entries(m.metrics || {})
                .filter(([kk, v]) => Number.isFinite(v)
                    && !(METRIC_FLAG.test(kk) && !v))
                .slice(0, 3)
                .map(([kk, v]) => `<span class="ad-mod-val">`
                    + (METRIC_FLAG.test(kk)
                        ? `<b>${esc(metricLabel(kk, v))}</b>`
                        : `<b>${esc(fmtMetric(kk, v))}</b> ${esc(metricLabel(kk, v))}`)
                    + `</span>`).join('');
            // Three states, three shapes — never colour alone (Rule 9.4a): a module
            // that found something carries a solid accent edge, one that could not run
            // is dashed and dimmed, a quiet one is plain.
            const cls = m.abstained ? 'ad-mod ad-mod-abstained'
                : (m.triggered ? 'ad-mod ad-mod-triggered' : 'ad-mod');
            // A property with NO bound module is shown, not omitted: "nothing is
            // assessing this" is the reading that must never be invisible.
            const name = m.assessor
                ? esc(m.assessor_label || m.assessor)
                : '<em>nothing bound</em>';
            // Routes through the ONE navigation channel to the property's own entry —
            // never a second glossary.
            const link = m.property ? ` data-dim="${esc('property_' + m.property)}"` : '';
            // The property and its assessor are DIRECT grid children — column 1 is the
            // property, column 2 everything about the module currently bound to it. A
            // wrapper around the two put both in column 1, where two inline spans with no
            // separator rendered as "Answer scopeData scope adherence"; it also defeated
            // the left rail that lets a reader scan the properties down the panel, which
            // is the whole reason the row is a grid.
            // The module's relation to the gate is a MARK (words on hover), never a
            // sentence: an arrow into a bar = the gate consulted this module during the
            // turn, so its reading is part of the decision above; no mark = graded after.
            const role = m.decides
                ? `<span class="ad-mod-role u-tip" data-tip="consulted by the gate during the turn — its reading is part of the gate's decision" aria-label="consulted by the gate during the turn">${ROLE_MARK}</span>`
                : '';
            return `<li class="${cls}"${link}>`
                 + `<span class="ad-mod-prop">${esc(m.label || m.property)}</span>`
                 + `<span class="ad-mod-name">${name}${role}</span>`
                 + `<div class="ad-mod-sum">${esc(m.summary || '')}</div>`
                 + (vals ? `<div class="ad-mod-vals">${vals}</div>` : '')
                 + `</li>`;
        }).join('');

        return `<details class="ad-mods">`
             + `<summary class="ad-mods-sum">${esc(lead)}</summary>`
             + `<ul class="ad-mod-list">${rows}</ul>`
             + `</details>`;
    }

    //: The SAME shelf the Assurance Guide's station entry uses — a station's members by
    //: the verb of their class: upheld by · controls, bounded by · boundaries, attested
    //: by · attestations, evidenced by · measures — so a reader meets one structure on
    //: both surfaces. One table (the Guide's MECH_VERB), mirrored here.
    //:
    //: FOLD BY STATE, never by default: this panel's job is to show what happened to
    //: THIS answer. A group folds only when every member is quiet or did not run; a
    //: group with a member that acted, or reads amber or red, stays open. The fold line
    //: carries the worst state's dot, the count and the reading, so a folded group still
    //: tells the truth at a glance. Narrow panel: one short line, wrapping if it must.
    const GRP_VERB = { control: 'upheld by', boundary: 'bounded by', attestation: 'attested by', measure: 'evidenced by' };
    const GRP_NOUN = { control: ['control', 'controls'], boundary: ['boundary', 'boundaries'],
                       attestation: ['attestation', 'attestations'], measure: ['measure', 'measures'] };
    const GRP_ORDER = ['control', 'boundary', 'attestation', 'measure'];
    function groupRows(points, htmls) {
        const groups = new Map();
        points.forEach((p, i) => {
            const c = GRP_VERB[(p.guide && p.guide.cls) || ''] ? p.guide.cls : 'control';
            if (!groups.has(c)) groups.set(c, []);
            groups.get(c).push({ p, html: htmls[i] });
        });
        return GRP_ORDER.filter((c) => groups.has(c)).map((c) => {
            const items = groups.get(c), n = items.length;
            const states = items.map((x) => (x.p.applies === false ? 'na' : x.p.state));
            const worst = states.reduce((w, st) => ((RANK[st] || 0) > (RANK[w] || 0) ? st : w), 'na');
            const acted = items.filter((x) => x.p.acted && x.p.applies !== false).length;
            const nWorst = states.filter((st) => st === worst).length;
            const attention = ['danger', 'warn', 'pending', 'unknown'].includes(worst) || acted > 0;
            const sum = ['danger', 'warn', 'pending', 'unknown'].includes(worst) ? `${nWorst} ${word(worst)}`
                      : acted ? `${acted} acted`
                      : states.every((st) => st === 'na') ? 'did not run' : 'all quiet';
            const noun = GRP_NOUN[c][n === 1 ? 0 : 1];
            return `<details class="ad-grp"${attention ? ' open' : ''}>`
                 + `<summary class="ad-grp-h ad-gate-${esc(worst)}"><span class="ad-gate-dot" aria-hidden="true"></span>`
                 + `<span class="ad-grp-lead">${esc(GRP_VERB[c])}</span>`
                 + `<span class="ad-grp-kind">${esc(noun)} \u00b7 ${n}</span>`
                 + `<span class="ad-grp-sum">${esc(sum)}</span></summary>`
                 + items.map((x) => x.html).join('') + `</details>`;
        }).join('');
    }

    function sectionHtml(k, d) {
        // Class scope (legibility follow-up): a mechanism outside this agent's class is
        // listed, dimmed and dashed, and counted apart — so this list and the Guide's
        // station entry name the same mechanisms, and the count says why they differ.
        const offClass = d.points.filter((p) => p.applies === false).length;
        const rowHtmls = d.points.map((p) => {
            // Only a gate with a step in the trace is a route; the rest stay inert
            // rather than promising a jump that goes nowhere (Rule 9.3, in prose form).
            const dims = guideDims(p);
            const guided = !!dims
                && !!(window.QualityDimensions && window.QualityDimensions.openGuide);
            const locatable = guided || !!TRACE_STEP[p.key];
            // An aggregate opens via the context alone (no single highlight), so it
            // needs the guide-button attribute to be routed at all.
            const aggAttr = (guided && !!(p.guide && p.guide.aggregate))
                ? ' data-dim-guide' : '';
            const tag = locatable ? 'button' : 'div';
            // ONE tooltip per row. A guided row already gets the Guide's own hover
            // (via `data-dim`), so adding `u-tip` too showed two boxes at once —
            // overlapping, saying different things, neither obviously the answer.
            const cls = `ad-gate ad-gate-${p.state}${locatable ? ' ad-gate-linked' : ''}`
                      + `${guided ? ' ad-gate-guided' : ' u-tip'}`
                      + `${p.applies === false ? ' ad-gate-offclass' : ''}`;
            // Open AT the mechanism, not at what measures it. Using `data-dim-guide`
            // made the delegation pass `null` as the highlight, so clicking "Inbound
            // guard" landed on `safety` — the thing that measures it rather than the
            // thing that was clicked. `data-dim="<mechanism>"` opens at the row's own
            // subject; its dimensions are right there as chips, one step further.
            //
            // Rule 9.6 still holds: the context carries the mechanism AND its
            // dimensions, so nothing is filtered out — the reader can move between them
            // without leaving the view.
            // The scope is the thing you CLICKED — one row. Including its dimensions
            // put two entries on screen for one click, and the reader has to work out
            // which is the answer. They are already reachable as chips on that row,
            // which is the whole point of the two-way links.
            // An AGGREGATE mechanism's reading is its dimensions, so clicking it shows
            // them. Every other mechanism HAS dimensions — it is not made of them — so
            // its scope is itself, with the dimensions one chip away.
            const isAgg = !!(p.guide && p.guide.aggregate);
            const ctxRows = !guided ? []
                : (isAgg && Array.isArray(p.dims) && p.dims.length
                    ? p.dims
                    : [{ k: p.key, b: BAND_OF[p.state] || 'na' }]);
            const ctx = guided
                ? (isAgg ? '' : ` data-dim="${esc(p.key)}"`)
                  + ` data-dim-context="${esc(JSON.stringify(ctxRows))}"`
                  + ` data-dim-context-label="${esc(`${p.label} — this turn`)}"`
                  // The class rides the context, so the Guide can mark what does not
                  // apply to THIS agent the same way this row does.
                  + (_profileClass ? ` data-dim-context-cls="${esc(_profileClass)}"` : '')
                : '';
            const attrs = locatable
                ? ` type="button" data-ad-gate="${esc(p.key)}"${ctx}${aggAttr}`
                  + `${guided ? '' : ' data-tip="Find this step in the trace below"'}`
                : '';
            return `<${tag} class="${esc(cls)}"${attrs}>`
                 + `<span class="ad-gate-dot" aria-hidden="true"></span>`
                 + `<span class="ad-gate-label">${esc(p.label)}</span>`
                 + `<span class="ad-gate-detail">`
                 + `${esc(String(p.detail || '').replace(/^[^:]*:\s*/, ''))}</span>`
                 + `</${tag}>`
                 // A mechanism outside the class has no modules to report on this turn —
                 // a "1 module, none ran" line under it would read as a module that
                 // could have run here.
                 + (p.applies === false ? '' : modulesHtml(p));
        });
        const rows = groupRows(d.points, rowHtmls);
        // A station holds TWO populations and its colour is worst-of BOTH: the
        // MECHANISMS that ran (rows above) and the MEASUREMENTS that grade what they
        // guarantee. Only the mechanisms had rows, so a station turned red by a failing
        // measurement showed nothing but green dots — the driver was named in the
        // headline and had nowhere to be pointed at. It also made the panel and the
        // scorecard list different things under one station name with no way to tell
        // they were different KINDS of thing. Both are listed now, each labelled.


        return `<section class="ad-section" data-ad-section="${esc(k)}" hidden>
              <h4 class="ad-section-head"><span class="ad-section-name" data-dim="station_${esc(k)}" role="button" tabindex="0"
                    aria-label="${esc(LABEL[k])} — open its entry in the Assurance Guide">${esc(LABEL[k])}</span>
                <span class="ad-chip ad-chip-${esc(d.state)}">${esc(stationWord(d))}</span></h4>
              ${k === JUDGED && Array.isArray(d.dims) && d.dims.length
                    // JUDGED's members are the measures it counts on this turn (the server
                    // allocates every scored measure here, once). The header carries their
                    // count, so the adopted scorecard drops its own (`.qc-total`, trust.css).
                    ? `<div class="ad-note">${esc(NOTHING_RUNS)}</div>`
                      + `<div class="ad-sub-head ad-mem-head u-tip" data-tip="${esc(SUB_TIP.ran)}">members \u00b7 ${d.dims.length}`
                      + `<span class="ad-sub-count">the worst grade of these colours ${esc(LABEL[k])}</span></div>`
                    : rows ? `<div class="ad-sub-head ad-mem-head u-tip" data-tip="${esc(SUB_TIP.ran)}">${d.points.length === 1 ? 'member' : 'members'} \u00b7 ${d.points.length}`
                       + `<span class="ad-sub-count">the worst of these colours ${esc(LABEL[k])}${offClass
                           ? ` · ${d.points.length - offClass} of ${d.points.length} apply to this class` : ''}</span></div>${rows}`
                     : `<div class="ad-note">${esc(k === JUDGED ? NOTHING_RUNS : 'no mechanism applies to this class')}</div>`}
              ${k === JUDGED
                    // JUDGED's body IS the scorecard — adopted from the Quality card
                    // (see ui.js `_adoptScorecard`). The flat chip list `measuresHtml`
                    // renders for the other stations is retired here: it was the same
                    // 20 checks with no order and no grouping, beside a card that had
                    // both. Empty until the card lands, never a placeholder claiming
                    // there is nothing to show.
                    // Only the Live Status dial holds it; an answer's capsule lists its own
                    // turn's measures here instead (the scorecard is one element, one turn).
                    // In Live Status the turn's own measures stand under the slot and give way
                    // the moment the scorecard shows (trust.css `.ad-judged-own`): a turn with no
                    // stored score, or one whose score has not arrived, still lists its members.
                    ? (_holdsScorecard ? `<div class="ad-scorecard-slot"></div><div class="ad-judged-own">${judgedMembersHtml(d)}</div>` : judgedMembersHtml(d))
                    : measuresHtml(d)}
              ${markKey(d)}
            </section>`;
    }

    //: A station holds two populations, and which one ACTS is the fact a reader cannot
    //: recover from the lists themselves. Put side by side with no statement of the
    //: relationship, proximity reads as causation — an operator concluded the
    //: measurements open and close the gates, and nothing on screen corrected it. They
    //: never touch the gates: a mechanism decides during the turn and can withhold; a
    //: measurement grades the same property afterwards and can only report.
    //: The one station where NOTHING RUNS — the lane cut stated at the exact place a
    //: reader asks. The generic empty-state ("no mechanism applies to this class")
    //: implies class-dependence, as if another agent type might have one here. None
    //: does, ever: measurement is taken after the answer and can only report.
    const JUDGED = 'scored';
    const NOTHING_RUNS = 'nothing runs here — this station reports what the '
                       + 'measurements found after the turn';

    const SUB_TIP = {
        // The Guide's own definition of a member, so the two surfaces say one thing.
        // On the dial a station's colour IS the worst of its members (`byStation`), for
        // every station. The Guide's definition, in the dial's altitude (one answer).
        ran: 'A member is what a station\u2019s colour is the worst of, and each belongs to exactly one '
           + 'station. At a guarantee: whatever upholds, bounds, attests or evidences it. At JUDGED: '
           + 'every measure scored on this answer, counted there once.',
        measures: 'Measurements — they grade the same guarantee AFTER the turn. They report '
                + 'only: nothing here can open or close a gate.',
    };

    /** The measurements this station OWNS, worst-first, as Guide-routed chips.
     *
     *  Same partition and the same navigation channel (`data-dim`) as the scorecard, so
     *  the two surfaces name the same things — the point of listing them.
     *
     *  The set comes straight off the station (`d.dims`), allocated by the registry. It
     *  used to be unioned out of the station's mechanisms behind a `seen` set — which was
     *  this renderer re-deriving station ownership by hand, and would have silently
     *  dropped any measurement whose owning mechanism did not apply to the class.
     *
     *  Two fields, deliberately: the chip shows the MEASUREMENT (`b`, what was scored),
     *  while sorting uses the CONTRIBUTION (`st`, what the station folded) — a pair the
     *  judge cleared measures `danger` and contributes `warn` (Rule 3.0c), and the chip
     *  must never be rewritten to match the station. */
    function measuresHtml(d) {
        const chips = measureChips(d);
        if (!chips) return '';
        const n = d.dims.filter((x) => x && x.k).length;
        return `<div class="ad-sub-head u-tip" data-tip="${esc(SUB_TIP.measures)}">what it measures`
             + `<span class="ad-sub-count">graded after · ${n} counted here</span></div>`
             + chips;
    }
    /** The measures as chips, worst first — a guarantee's "what it measures" list, and the
     *  member list of JUDGED on a dial that does not hold the scorecard (an answer's capsule). */
    function measureChips(d) {
        const QD = (typeof window !== 'undefined' && window.QualityDimensions) || null;
        const dims = (Array.isArray(d.dims) ? d.dims : []).filter((x) => x && x.k);
        if (!dims.length) return '';
        dims.sort((a, b) => (RANK[_st(b)] || 0) - (RANK[_st(a)] || 0));
        const chips = dims.map((dim) => {
            // `bst` — the MEASUREMENT as a state, never the contribution. Both are
            // resolved by the registry (`band_state` / `dimension_state`); this file
            // chooses between them and converts neither.
            const st = RANK[dim.bst] !== undefined ? dim.bst : 'na';
            const label = QD && QD.label ? QD.label(dim.k) : dim.k;
            const disp = dim.disp ? ' · disputed' : '';
            return `<button type="button" class="ad-meas ad-meas-${esc(st)}" data-dim="${esc(dim.k)}"`
                 + ` data-dim-context="${esc(JSON.stringify([{ k: dim.k, b: dim.b }]))}"`
                 + ` data-dim-context-label="${esc(`${label} — this turn`)}">`
                 + `<span class="ad-gate-dot" aria-hidden="true"></span>${esc(label)}${esc(disp)}</button>`;
        }).join('');
        return `<div class="ad-meas-wrap">${chips}</div>`;
    }

    /** JUDGED's members on a dial that does not hold the scorecard element: the turn's own
     *  measures, through the scorecard's ONE renderer (ui.js `UderiaScorecard`) — the same
     *  headline and the same moment shelves, open only where a measure failed or is flagged.
     *  A member's band is its measurement; a red band whose contribution is not red (a
     *  judge cleared it) is advisory, as the scorecard reads it. No renderer → the chips. */
    function judgedMembersHtml(d) {
        const dims = (Array.isArray(d.dims) ? d.dims : []).filter((x) => x && x.k);
        const SC = (typeof window !== 'undefined' && window.UderiaScorecard) || null;
        if (!dims.length || !SC || typeof SC.renderMeasures !== 'function') return measureChips(d);
        try {
            // Only the scorecard's three bands; anything else (an abstention) is no verdict,
            // which it draws as a neutral chip.
            const BANDS = { healthy: true, review: true, danger: false };
            const verdicts = dims.map((m) => {
                const band = m.b in BANDS ? m.b : null;
                return { dimension: m.k, band, passed: band ? BANDS[band] : null,
                         gating: !(band === 'danger' && _st(m) !== 'danger') };
            });
            // The moments come from the stage vocabulary; until it has loaded the shelves
            // degrade to one list, so a pending render is redrawn once it lands.
            const QD = window.QualityDimensions || null;
            const pending = !!(QD && QD.stagesReady && !QD.stagesReady() && QD.ensureVocabulary);
            if (pending) {
                QD.ensureVocabulary().then(() => {
                    document.querySelectorAll('.ad-judged-measures[data-stage-pending]').forEach((el) => {
                        try { el.innerHTML = SC.renderMeasures(JSON.parse(el.getAttribute('data-verdicts'))); el.removeAttribute('data-stage-pending'); } catch (_) { /* keep the list */ }
                    });
                }).catch(() => { /* the list stays */ });
            }
            return `<div class="ad-judged-measures"${pending ? ` data-stage-pending data-verdicts="${esc(JSON.stringify(verdicts))}"` : ''}>`
                 + `${SC.renderMeasures(verdicts)}</div>`;
        } catch (_) { return measureChips(d); }
    }

    // ── the mark key ────────────────────────────────────────────────────────
    // The dot beside each control is the COLOUR=attention channel (Rule 4.0), and a
    // mark with no key is a guess (the runs-ledger lesson, applied to the dial). Each
    // sample is drawn with the SHIPPED `.ad-gate-STATE .ad-gate-dot` rules, so the key
    // cannot drift from the marks it explains, and its words come from the ONE
    // vocabulary (`word`) — the same source as the station chips. Only the marks
    // actually present in this reading are shown: an absent mark is not a gap the
    // reader has to reconcile.
    //
    // `na` and `unknown` render identically here (both dashed), so they fold to one
    // sample; `na` is glossed at the TURN altitude, where "not turned on" is false for
    // most of what the dashed mark covers — the rows themselves say "did not run".
    const _KEY_ROWS = [
        { st: 'pass',    cls: 'ad-gate-pass',    has: (p) => p === 'pass' },
        { st: 'warn',    cls: 'ad-gate-warn',    has: (p) => p === 'warn' },
        { st: 'danger',  cls: 'ad-gate-danger',  has: (p) => p === 'danger' },
        { st: 'na',      cls: 'ad-gate-na',      has: (p) => p === 'na' || p === 'unknown',
          word: 'did not run this turn' },
        { st: 'offclass', cls: 'ad-gate-offclass ad-gate-na', has: (p) => p === 'offclass',
          word: 'not part of this class' },
        { st: 'pending', cls: 'ad-gate-pending', has: (p) => p === 'pending' },
    ];
    //
    // PER PANEL. The marks only ever appear inside an open station panel, so the key
    // lives at the foot of that panel and explains only the marks it shows. It used to
    // sit once below every panel, listing the marks of all eight stations — outside the
    // box whose marks it explained, and reading as a heading for whatever came next.
    // JUDGED's marks are its measures' (the scorecard groups carry the same dots).
    function markKey(d) {
        const present = new Set();
        for (const p of (d && d.points) || []) present.add(p.applies === false ? 'offclass' : p.state);
        for (const m of (d && d.dims) || []) present.add(_st(m));
        const items = _KEY_ROWS
            .filter((r) => [...present].some((p) => r.has(p)))
            .map((r) => `<span class="ad-key-item">`
                + `<span class="ad-key-mark ${r.cls}" aria-hidden="true"><span class="ad-gate-dot"></span></span>`
                + `<span class="ad-key-word">${esc(r.word || word(r.st))}</span></span>`)
            .join('');
        if (!items) return '';
        return `<div class="ad-key" role="group" aria-label="What the marks beside each control mean">`
             + `<span class="ad-key-lead">What the marks mean</span>${items}</div>`;
    }

    /**
     * Render the instrument into `host`.
     * @param {HTMLElement} host
     * @param {object} assurance  the turn's `assurance` payload (server-derived)
     * @param {object} [opts]     { size }
     */
    let _profileClass = null;   // the turn's IFOC class, for the Guide's class marks
    let _holdsScorecard = true; // this render is the Live Status dial (#assurance-dial), the scorecard's home
    function render(host, assurance, opts) {
        if (!host) return null;
        _profileClass = (assurance && assurance.profile_class) || null;
        // Any item this instrument lists must be lookupable in the Guide — including the
        // mechanisms no single dimension scores. Registered before anything can be
        // clicked, so there is no window in which a row is a dead end.
        try {
            if (window.QualityDimensions && window.QualityDimensions.registerEntries) {
                const all = [];
                Object.values((assurance && assurance.by_station) || {}).forEach((ps) => {
                    (Array.isArray(ps) ? ps : []).forEach((p) => {
                        if (p && p.guide) all.push(Object.assign({}, p.guide,
                            { label: p.guide.label || p.label }));
                    });
                });
                window.QualityDimensions.registerEntries(all);
            }
        } catch (_) { /* the Guide is a convenience; never break the instrument */ }
        const size = (opts && opts.size) || 132;
        const st = stations(assurance);
        const sum = summarise(st, assurance);

        host.classList.add('ad-root');
        // The strip carried a header; dropping it left the instrument unlabelled and
        // floating under the filter bar. The subtitle is the summary's own worst-of, so
        // the two can never disagree.
        const SHIELD = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            + 'stroke-width="2" stroke-linecap="round" aria-hidden="true">'
            + '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>';

        // R-6 — the scoring controls (Guide · the scoring button) are ADOPTED from the
        // Quality card into this header: scoring is a TURN-level act, so the band is
        // its honest home. RESCUE before the innerHTML wipe, re-seat after —
        // the nodes (with their wired handlers) survive the move; the Quality card
        // keeps them only when no dial renders (the legacy-strip path).
        // Only the Live Status dial adopts them (like the scorecard): an answer's capsule
        // rendering last took the Guide and scoring buttons out of Live Status.
        _holdsScorecard = host.id === 'assurance-dial';
        const _ctl = !_holdsScorecard ? null : (host.querySelector('#quality-ctl-cluster')
            || document.getElementById('quality-ctl-cluster'));
        if (_ctl) _ctl.remove();
        // The adopted SCORECARD needs the same rescue: it lives inside a station panel,
        // which this innerHTML wipe destroys. Detached here and re-seated below (or, if
        // JUDGED is not pinned this render, returned to the card it came from) — the
        // nodes keep their wired handlers and the reader never loses the scores.
        const _sc = host.querySelector('#trajectory-eval-results');
        if (_sc) _sc.remove();

        host.innerHTML = `
          <div class="ad-head">${SHIELD}<span class="ad-title">Assurance</span>
            <span class="ad-sub ad-sub-${esc(sum.worst)}">${esc(routing(st))}</span>
            <span class="ad-ctl-slot"></span>
            <button type="button" class="ad-pin u-tip u-tip-right" data-ad-pin
              aria-pressed="false" aria-label="Pin the assurance panel">${PIN_SVG}</button></div>
          <div class="ad-instrument">
            <svg class="ad-dial" viewBox="0 0 ${size} ${size}" style="width:${size}px;height:${size}px"
                 role="img" aria-label="Assurance dial">${dialSvg(st, size)}</svg>
            <div class="ad-side">
              <p class="ad-summary ad-summary-${esc(sum.worst)}">${esc(sum.text)}</p>
              <!-- Rule 9.7: the hover reading is written HERE, into a region whose
                   height does not depend on what is written in it. -->
              <div class="ad-read" data-ad-read aria-live="polite"></div>
            </div>
          </div>
          <div class="ad-sections">${ORDER.map((k) => sectionHtml(k, st[k])).join('')}</div>`;

        // Re-seat the adopted controls (R-6): rescued above, or — when the Quality
        // card rendered first and the dial is mounting fresh — pulled from the card
        // now. Whichever side renders second performs the move (ui.js does the same).
        try {
            const _slot = _holdsScorecard ? host.querySelector('.ad-ctl-slot') : null;
            const _ctl2 = _ctl || document.getElementById('quality-ctl-cluster');
            if (_slot && _ctl2) _slot.appendChild(_ctl2);
        } catch (_) { /* controls stay wherever they are — never break the dial */ }
        // Re-place through the ONE rule: the rescued node is detached, so `placeScorecard`
        // reattaches it wherever it is readable. A rescue that cannot re-seat must
        // RESTORE — the card is the home when JUDGED is not pinned — or a re-render
        // silently costs the reader the whole scorecard.
        if (_sc && !_sc.isConnected) {
            const _card = document.getElementById('trajectory-eval-overlay');
            if (_card) _card.appendChild(_sc);
        }
        placeScorecard(host);

        const readEl = host.querySelector('[data-ad-read]');
        const restText = ORDER.some((k) => st[k].marks)
            ? 'Hover a station to read it · click to open it' : '';
        const rest = () => { if (readEl) readEl.textContent = restText; };
        rest();

        let pinned = null;
        const sectionOf = (k) => host.querySelector(`[data-ad-section="${k}"]`);

        function paint() {
            for (const k of ORDER) {
                const g = host.querySelector(`[data-ad-station="${k}"]`);
                if (g) g.classList.toggle('ad-on', pinned === k);
                const s = sectionOf(k);
                // Rule 9.6 — locate, never filter: opening a station reveals ITS section
                // and leaves every other one exactly as it was.
                if (s) s.hidden = pinned !== k;
            }
        }

        function readInto(k) {
            if (!readEl) return;
            const d = st[k];
            // The point that DRIVES the state, not merely the first one — otherwise
            // the headline says "not recorded" while the detail beside it quotes a
            // mechanism that reported cleanly.
            readEl.textContent = stationReading(d, k);
        }

        host.querySelectorAll('[data-ad-station]').forEach((g) => {
            const k = g.getAttribute('data-ad-station');
            g.addEventListener('mouseenter', () => readInto(k));
            g.addEventListener('focus', () => readInto(k));
            g.addEventListener('mouseleave', () => { if (!pinned) rest(); else readInto(pinned); });
            g.addEventListener('blur', () => { if (!pinned) rest(); else readInto(pinned); });
            // R3 — clicking again collapses; both routes to a station toggle identically.
            // Ledger R2: the station click opens the panel; the TRACE SCROLL lives on
            // the gate row inside it. Attaching it here yanked the trace on every
            // station click — two intentions merged into one gesture, and the reader
            // loses the choice. A station also spans several mechanisms, so scrolling
            // from it could only ever pick one of them arbitrarily.
            g.addEventListener('click', () => {
                pinned = pinned === k ? null : k;
                paint(); readInto(k);
            });
            g.addEventListener('keydown', (e) => {
                const i = ORDER.indexOf(k);
                let n = null;
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault(); pinned = pinned === k ? null : k; paint(); readInto(k);
                } else if (e.key === 'ArrowRight' || e.key === 'ArrowDown') n = (i + 1) % ORDER.length;
                else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') n = (i + ORDER.length - 1) % ORDER.length;
                else if (e.key === 'Escape') { pinned = null; paint(); rest(); }
                if (n != null) {
                    e.preventDefault();
                    const t = host.querySelector(`[data-ad-station="${ORDER[n]}"]`);
                    if (t) t.focus();
                }
            });
        });

        // The gate row is the scroll route (ledger R2). Delegated, so sections that
        // re-render keep it.
        host.addEventListener('click', (ev) => {
            const row = ev.target && ev.target.closest && ev.target.closest('[data-ad-gate]');
            if (!row) return;
            const key = row.getAttribute('data-ad-gate');
            // The Guide answers "what does this measure?"; the trace answers "what
            // happened?". Guided rows carry `data-dim` + `data-dim-context`, which
            // qualityDimensions.js already delegates on — so the dial routes through the
            // platform's ONE glossary channel instead of opening a parallel one, and
            // inherits its scope toggle and live band pills unchanged.
            // A guided row is handled by qualityDimensions.js's own delegation; falling
            // through would ALSO yank the trace, which is two destinations for one click.
            // A guided row is handled by qualityDimensions' own delegation.
            if (row.hasAttribute('data-dim') || row.hasAttribute('data-dim-guide')) return;
            scrollTraceTo(key);
        });

        paint();
        return { stations: st, summary: sum };
    }



    // ── the pin ───────────────────────────────────────────────────────────────
    // OFF by default: the band scrolls away with the events, because most of the time a
    // reader wants the trace and the assurance reading is context they have already
    // taken in. Pinned, it stays while only the events scroll beneath — for when the
    // reading is the thing being worked and the trace is the evidence for it.
    //
    // A preference, so it persists: a control you have to re-set every turn is one you
    // stop using.
    // Two scopes, the platform's existing idiom: Context Modes use "hold Alt for a
    // single query, Shift+Alt to lock". Here — single click pins for NOW, double click
    // locks the current state as the default for every turn afterwards.
    //
    // Without the split, one gesture had to serve both, and it silently chose: pinning
    // once changed every future turn. That is a preference set by accident.
    const PIN_KEY = 'tda_assurance_pinned';   // the locked DEFAULT
    let _pinNow = null;                       // this session's override, if any

    /** Whether a default has been LOCKED at all (distinct from what it is). */
    function hasDefault() {
        try { return localStorage.getItem(PIN_KEY) != null; } catch (_) { return false; }
    }
    function pinnedDefault() {
        try { return localStorage.getItem(PIN_KEY) === '1'; } catch (_) { return false; }
    }
    function pinned() {
        if (_pinNow != null) return _pinNow;
        // Built-in default: PINNED. The dial is the panel's headline instrument —
        // unpinned-by-default meant it scrolled away with the trace and most readers
        // never saw the settled reading. A locked preference still wins both ways.
        return hasDefault() ? pinnedDefault() : true;
    }
    /** Set for now. Locking is a separate, deliberate act (`lockPin`). */
    function setPinned(on) { _pinNow = !!on; }
    /**
     * Toggle whether the current state is the DEFAULT.
     *
     * Locking stores it; unlocking removes the stored preference so it falls back to the
     * built-in default, while leaving the panel as it is right now. The first version
     * only ever committed, so a second double click did nothing — a toggle you cannot
     * undo with the same gesture is a trap, not a shortcut.
     */
    function lockPin() {
        try {
            if (hasDefault()) {
                _pinNow = pinned();          // keep what is on screen
                localStorage.removeItem(PIN_KEY);
                return false;                // no longer locked
            }
            localStorage.setItem(PIN_KEY, pinned() ? '1' : '0');
            _pinNow = null;                  // the default now speaks for itself
            return true;
        } catch (_) { return false; }
    }

    const PIN_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        + 'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        + '<path d="M12 17v5"/><path d="M9 10.76V4h6v6.76a2 2 0 0 0 .55 1.38L18 15H6l2.45-2.86'
        + 'A2 2 0 0 0 9 10.76z"/></svg>';

    function wirePin(band) {
        if (!band) return;
        let timer = null;
        const apply = (flash) => {
            const on = pinned();
            const isDefault = hasDefault() && _pinNow == null;
            band.classList.toggle('ad-band-pinned', on);
            const b = band.querySelector('[data-ad-pin]');
            if (!b) return;
            b.classList.toggle('on', on);
            // Locked and merely-on must LOOK different, or the two states are
            // indistinguishable and the double click has no visible effect.
            b.classList.toggle('locked', on && isDefault);
            b.setAttribute('aria-pressed', String(on));
            b.setAttribute('data-tip', (on ? 'Pinned' : 'Unpinned')
                + (isDefault ? ' — this is the default.\nDouble-click to unlock it.'
                             : ' for now.\nDouble-click to make it the default.'));
            if (flash) {
                b.classList.add('ad-pin-locked-flash');
                setTimeout(() => b.classList.remove('ad-pin-locked-flash'), 700);
            }
        };
        band.addEventListener('click', (ev) => {
            const b = ev.target && ev.target.closest && ev.target.closest('[data-ad-pin]');
            if (!b) return;
            // The same 240ms disambiguation the KG inspector uses, so a double click is
            // not also processed as two toggles.
            if (timer) { clearTimeout(timer); timer = null; return; }
            timer = setTimeout(() => { timer = null; setPinned(!pinned()); apply(); }, 240);
        });
        band.addEventListener('dblclick', (ev) => {
            const b = ev.target && ev.target.closest && ev.target.closest('[data-ad-pin]');
            if (!b) return;
            if (timer) { clearTimeout(timer); timer = null; }
            lockPin();
            apply(true);
        });
        apply();
    }



    /** Trace cards for the SILENT interventions (operator-approved R-2 closeout).
     *  Preflight withholding a query, the retrieval firewall defusing a chunk, and an
     *  answer parked for review all CHANGE the answer — and none emitted a trace step,
     *  which broke the trace's core promise: an intervention that changed the answer
     *  must leave a step. Rendered from the turn's own assurance readings at inject
     *  time, so live and reload are ONE implementation by construction. Idempotent
     *  (keyed per mechanism); appended at the trace end as turn-end attestations. */
    const _INTERVENTIONS = ['preflight_sql', 'retrieval_firewall', 'review_parking'];
    function _interventionCards(host, assurance) {
        const by = (assurance && assurance.by_station) || {};
        const esc2 = (v) => String(v == null ? '' : v).replace(/[&<>"']/g,
            (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
        for (const stKey of Object.keys(by)) {
            for (const r of (Array.isArray(by[stKey]) ? by[stKey] : [])) {
                if (!r || !r.acted || !_INTERVENTIONS.includes(r.key)) continue;
                if (host.querySelector(`[data-ad-intervention="${r.key}"]`)) continue;
                const card = document.createElement('div');
                // The shared step classes: the assurance stripe, the filter, the layout
                // all come from the one system the real steps use — no private chrome.
                card.className = 'status-step completed assurance';
                card.dataset.adIntervention = r.key;
                card.dataset.filterCategory = 'assurance';
                card.innerHTML = `
                    <div class="status-step-title" style="font-weight:600;">${esc2(r.label)}</div>
                    <div class="status-kv-grid" style="margin-top:4px;">
                        <div class="status-kv-key"><span class="status-kv-mech" data-dim="${esc2(r.key)}" role="button" tabindex="0">${esc2(r.label)}</span></div>
                        <div class="status-kv-value"><span class="trust-chip tc-${r.state === 'danger' ? 'danger' : 'warn'}">${esc2(r.word || 'acted')}</span>
                            <span class="status-kv-station" data-dim="station_${esc2(r.station)}" role="button" tabindex="0">${esc2(stationLabel(r.station))}</span></div>
                        <div class="status-kv-key">What happened</div>
                        <div class="status-kv-value">${esc2(String(r.detail || '').replace(/^[^:]*:\s*/, ''))}</div>
                    </div>`;
                host.appendChild(card);
            }
        }
    }

    /** Build the band + dial. ONE implementation for the live start and the final
     *  reading, so the segment, the pin and the Quality adoption cannot drift apart. */
    function _mount(host, sessionId, turnId, assurance) {
        const existing = host.querySelector('#assurance-dial');
        if (existing) {                       // already up — re-render in place
            render(existing, assurance);
            return existing;
        }
        const band = document.createElement('div');
        band.id = 'assurance-band';
        // See ui.js: the band is filterable, so the ASSURANCE pill hides the
        // instrument together with the messages it summarises.
        band.dataset.filterCategory = 'assurance';
        const el = document.createElement('div');
        el.id = 'assurance-dial';
        el.setAttribute('data-session-id', String(sessionId));
        el.setAttribute('data-turn-id', String(turnId));
        band.appendChild(el);
        host.insertBefore(band, host.firstChild);
        // The band arrives after the last filter pass, so it announces itself instead of
        // reading the filter state. One owner of that fact, no second reader to drift.
        UderiaBus.emit('uderia:filterable-added');
        render(el, assurance);
        const quality = host.querySelector('#trajectory-eval-overlay');
        if (quality && quality.parentNode !== band) band.appendChild(quality);
        wirePin(band);
        return el;
    }

    // ── the live turn ─────────────────────────────────────────────────────────
    // The dial used to appear only at the end, because it was mounted by the post-turn
    // eval overlay and needed the persisted `assurance` payload. Pinned, it therefore
    // pinned nothing until the turn was over.
    //
    // What the live view is, precisely: a PROGRESS reading, not a second derivation.
    // Each gate's SSE step carries the SAME stamp the registry will later read
    // (`ran`/`acted`/`action`/`decision`, added by P1), so the live path reads that
    // stamp rather than inventing a verdict from the event's shape. When the turn ends
    // the authoritative payload replaces the whole thing, so the two can never drift
    // into disagreeing — one is superseded, not merged.
    //
    // Honest about the shape it reveals: assurance is front- and back-loaded. The
    // inbound guard runs before the model sees anything; grounding, honesty, the
    // outbound guard and governed-data all run AFTER the answer is formed. So this is
    // not a slow reveal — it is one gate early, then most at once. That is a true and
    // slightly surprising fact about where the guarantees actually sit, and showing it
    // is the point.
    let _live = null;      // { sid, turn, stations: {key: reading[]} }
    let _settled = null;   // `${sid}:${turn}` whose AUTHORITATIVE payload is rendered
    // Turns whose SCORE has reached this page (stream or notification). A turn settled
    // at stream end reads JUDGED honestly pending until then; the fallback below is
    // for the score that never arrives (a dropped notification, a tab that was not the
    // one the event reached) — bounded, idempotent, and never a second derivation.
    const _scored = new Set();
    let _scoreFallbackT = [];
    function _clearScoreFallback() {
        _scoreFallbackT.forEach((t) => clearTimeout(t));
        _scoreFallbackT = [];
    }
    /** Re-fetch the authoritative reading if no score has landed: once inside the
     *  scoring window (a lost notification), once after the registry's own pending
     *  window has passed (so "not in yet" becomes the truthful "no score recorded"). */
    function _armScoreFallback(sessionId, turnId) {
        _clearScoreFallback();
        const key = `${sessionId}:${turnId}`;
        for (const wait of SCORE_FALLBACK_MS) {
            _scoreFallbackT.push(setTimeout(() => {
                if (_scored.has(key) || _settled !== key) return;
                inject(sessionId, turnId, null, { force: true });
            }, wait));
        }
    }
    /** Already rendered AND still on screen. The flag outlives the DOM: a plan reload
     *  clears the status window and rebuilds it, so "settled" alone answered a question
     *  nobody asked — reloading the same turn returned early and left the band unmounted
     *  while the Quality card, which renders independently, stayed visible. */
    const _isOnScreen = (host, key) => _settled === key && !!host.querySelector('#assurance-dial');

    //: Which station each live trust step feeds. Only the gates that STREAM appear
    //: here; SIGNED and TRUSTED are computed at read time and stay pending until the
    //: payload lands — which is the truth, not a gap.
    // Score-fallback schedule (ms after the stream closed): the judge layer is bounded
    // at 25 s server-side, so 45 s covers a late score; the registry stops calling an
    // unscored turn "pending" after EVAL_PENDING_WINDOW_SECONDS, so the second re-fetch
    // lands just past it and renders the honest absence instead of a forever-pending.
    const SCORE_FALLBACK_MS = [45000, 200000];
    const LIVE_STATION = {
        guardrail: 'guarded',            // both directions; the stamp says which
        grounding_gate: 'grounded',
        honesty: 'grounded',
        retrieval: 'grounded',           // mid-turn PROGRESS only — GROUNDED's machinery
                                         // begins to have something to say (never a verdict)
        obda: 'grounded',   // governed-data lane — its home is GROUNDED (re-home), not JUDGED
        compute_lineage: 'grounded',   // S3 — number lineage is evidence for GROUNDED
        llm_recovery: 'signed',        // S4 — evidence for SIGNED: which model answered, inside the signature or not
        turn_budget: 'bounded',        // S5 — a declared boundary on what one turn may spend
        action_record: 'bounded',      // OBDA-ACT — a signed action acted on, parked or refused
        tool_surface: 'bounded',       // S6 — what the model was shown, and what it could never reach
        outcome_loop: 'grounded',      // S7 — the gate's own rubric acting one step earlier
        attachment_sources: 'grounded', // S9 — what the platform read from an attachment is GROUNDED's evidence
        turn_score_complete: 'scored',
    };

    function _pendingModel() {
        const by = {};
        for (const k of ORDER) {
            by[k] = [{ key: `${k}_pending`, label: LABEL[k], station: k, state: 'pending',
                       detail: `${LABEL[k]}: waiting on this turn`, acted: false,
                       word: 'not in yet', dims: [], guide: null }];
        }
        return by;
    }

    /** Mount the dial for a turn that is STARTING, with every station pending. */
    function begin(sessionId, turnId) {
        try {
            const host = document.getElementById('status-window-content');
            if (!host) return;
            // ALWAYS fresh. The caller runs this ONCE per stream (`dialBegun`), so a
            // `_live` guard here can only ever match a PREVIOUS turn's model — and that
            // is exactly the model a turn whose settle was lost leaves behind. Bailing on
            // it, after the status window had been cleared for the new query, mounted no
            // dial at all (the "not showing up" half of the collapsed-panel report).
            // The previous turn's band is still in the DOM: remove it, start over.
            _clearScoreFallback();
            const stale = host.querySelector('#assurance-band');
            if (stale) stale.remove();
            _settled = null;
            _live = { sid: String(sessionId), turn: String(turnId), stations: _pendingModel() };
            _mount(host, sessionId, turnId, { by_station: _live.stations, live: true });
        } catch (e) { console.warn('[assurance-dial] begin failed:', e); }
    }

    /**
     * A trust step arrived. `details` is the gate's own stamp.
     *
     * Reading the stamp is not re-deriving: it is the same object the registry reads
     * server-side, so a live segment and the final one describe the same fact.
     */
    function note(sessionId, type, details) {
        try {
            if (!_live || String(sessionId) !== _live.sid) return;   // another session
            const station = LIVE_STATION[type];
            if (!station || !details || typeof details !== 'object') return;

            const acted = details.acted === true
                || (details.decision && details.decision !== 'pass')
                || ['block', 'redact', 'annotate', 'flag'].includes(details.action);
            const ran = details.ran !== false;
            const decision = details.decision || details.action || '';
            let state = 'pass';
            if (!ran) state = 'na';
            else if (details.action === 'error') state = 'unknown';
            else if (decision === 'block') state = 'danger';
            else if (acted) state = 'warn';

            const dir = details.direction === 'inbound' ? 'Inbound guard'
                : (details.direction === 'outbound' ? 'Outbound guard'
                : (type === 'retrieval' ? 'Retrieval' : LABEL[station]));
            // PROGRESS IS NOT A VERDICT.
            //
            // The first version replaced the pending reading with the arrival, so a station
            // took its COLOUR on the first mechanism to report. That was invisible only
            // because every wired event fires in the same end-of-turn seam. The moment any
            // of them reports earlier — the inbound guard runs at dispatch, before a single
            // LLM call — GUARDED would go green while the OUTBOUND guard had not run, and
            // could then flip to red. A green segment meaning "half the machinery reported
            // clean" is the vacuous clean the contract bans (Rule 3.7), one level down.
            //
            // So an arrival adds PROGRESS and the station stays `pending` until the
            // authoritative payload lands. How many mechanisms have reported is a fact
            // about the TURN; what the station found is a claim about the ANSWER, and only
            // the second needs every mechanism in.
            //
            // ONE exception, and it is not a verdict either: a mechanism that ACTED is
            // reporting something that already happened to the answer. Withholding that
            // until the turn ends would hide a redaction the reader is entitled to see as
            // it occurs. It is surfaced and LABELLED as an act, never as the station's
            // reading.
            const prior = (_live.stations[station] || []).filter((r) => !/_pending$/.test(r.key));
            prior.push({ key: `${type}_${prior.length}`, label: dir, station,
                         state: acted ? state : 'pending',
                         detail: acted ? `${dir}: ${decision || 'acted'}` : `${dir}: reported`,
                         acted, word: acted ? '' : 'in', dims: [], guide: null });
            const anyActed = prior.some((r) => r.acted);
            const arrivals = anyActed ? prior : prior.concat([{
                key: `${station}_pending`, label: LABEL[station], station, state: 'pending',
                detail: `${LABEL[station]}: ${prior.length} in, more to come`,
                acted: false, word: `${prior.length} in`, dims: [], guide: null,
            }]);
            _live.stations[station] = arrivals;

            const el = document.getElementById('assurance-dial');
            if (!el) return;
            render(el, { by_station: _live.stations, live: true });

            // Flash the station that just landed. The CHANGE is the informative moment —
            // a reader watching a scrolling trace needs to be told WHERE to look, once,
            // and then left alone. Applied after render because render replaces the DOM.
            const g = el.querySelector(`[data-ad-station="${station}"]`);
            if (g) {
                g.classList.add('ad-arrived');
                setTimeout(() => g.classList.remove('ad-arrived'), 700);
            }
            const sub = el.querySelector('.ad-sub');
            if (sub) {
                sub.classList.add('ad-sub-changed');
                setTimeout(() => sub.classList.remove('ad-sub-changed'), 520);
            }
        } catch (e) { /* a live update must never break the trace */ }
    }

    // The turn's PHASES — what the agent is doing right now, in plain words. Distinct from
    // the station verdicts: a trust station reports post-answer (grounding, scoring), so a
    // fast turn on any engine — a bare Ideate turn above all — has NOTHING to fill the ring
    // while the model reasons. The phase line closes that gap with the truth ("Reasoning…"),
    // never a faked verdict. Driven by the execution events every engine already emits.
    const PHASE_LABEL = {
        context: 'Assembling context…',
        retrieving: 'Retrieving knowledge…',
        reasoning: 'Reasoning…',
        drafting: 'Drafting the answer…',
        screening: 'Checking the answer…',
        scoring: 'Scoring the turn…',
    };

    /**
     * A live PHASE cue (not a verdict). Updates the summary line in place while the turn
     * runs, so the consumer sees the agent move through its work. Cheap: no full re-render
     * (which would disturb the adopted controls); the next authoritative render clears it.
     */
    function phase(sessionId, key) {
        try {
            if (!_live || String(sessionId) !== _live.sid) return;   // another session / no live turn
            const label = PHASE_LABEL[key];
            if (!label) return;
            _live.phase = key;
            const el = document.getElementById('assurance-dial');
            const sub = el && el.querySelector('.ad-summary');
            if (!sub) return;
            // Only paint over a still-pending summary — never over a real reading that has
            // already landed (an acted mechanism, a settled station).
            if (!/pending|not in yet|still running|waiting/i.test(sub.textContent || '')
                && !sub.classList.contains('ad-phase-live')) return;
            sub.textContent = label;
            sub.classList.add('ad-phase-live');
        } catch (_) { /* a live cue must never break the trace */ }
    }

    /**
     * The turn ended without an authoritative payload (error, cancel, no scoring).
     *
     * Anything still pending becomes `unknown` — "not recorded" — never `pass`. A dial
     * frozen mid-flight would otherwise leave stations claiming a reading is still
     * coming when nothing is.
     */
    function settle(sessionId) {
        try {
            if (!_live || String(sessionId) !== _live.sid) return;
            for (const k of ORDER) {
                _live.stations[k] = (_live.stations[k] || []).map((r) =>
                    r.state === 'pending'
                        ? Object.assign({}, r, { state: 'unknown', word: 'not recorded',
                              detail: `${LABEL[k]}: the turn ended before this reported` })
                        : r);
            }
            const el = document.getElementById('assurance-dial');
            if (el) render(el, { by_station: _live.stations, live: true });
            // The turn is OVER — the live model must die with it. Keeping it made the
            // NEXT turn's begin() bail on this turn's corpse (`_live.sid` still
            // matching), so turn 2 showed turn 1's stale reading until authoritative.
            _live = null;
        } catch (_) { /* noop */ }
    }

    /** The stream closed on a DELIVERED turn. The live model was a PROGRESS reading;
     *  the persisted turn now carries the authoritative stamps — render them. This is
     *  the settle a completed turn never had: before it, only Stop settled the dial and
     *  a normal completion waited on `turn_score_complete` alone — which rides the
     *  notification channel once the stream has closed, and a single lost delivery left
     *  the live model (pending words, pulsing phase dot) on screen until a page refresh.
     *  JUDGED reads honestly pending until the score lands (the registry's own rule);
     *  the score path re-renders with force. If the reading cannot be fetched even
     *  after the retries, settle to "not recorded" — never leave the live model behind. */
    async function complete(sessionId, turnId) {
        try {
            if (sessionId == null || turnId == null) { settle(sessionId); return; }
            const ok = await inject(sessionId, turnId, null, { force: true, retry: true });
            if (!ok) { settle(sessionId); return; }
            _live = null;   // superseded — a late note()/phase() must not repaint pending
            if (!_scored.has(`${sessionId}:${turnId}`)) _armScoreFallback(sessionId, turnId);
        } catch (e) { console.warn('[assurance-dial] complete failed:', e); settle(sessionId); }
    }

    // ── Live Status mount ─────────────────────────────────────────────────────
    // Symmetrical with TrustJourney.inject: same host, same idempotence guard, same
    // flow-turn exemption, same failure posture (a broken instrument must never take
    // the trace down with it). Kept here rather than branching inside the strip so the
    // two renderers stay independently removable — the flag has to be a real revert.
    /** Render the AUTHORITATIVE reading for a turn.
     *
     *  `opts.force`  — render even if this turn's payload is already on screen. The
     *                  score paths need it: a stream-end render (`complete`) settles the
     *                  dial with JUDGED honestly pending, and the score that lands later
     *                  must be allowed to re-render the same turn, or `_isOnScreen` would
     *                  turn the settle into a lock.
     *  `opts.retry`  — the turn is persisted AFTER `final_answer` is emitted; a fetch at
     *                  stream end can race it. Bounded retries, then give up honestly.
     *  `opts.scored` — the caller carries the score: cancel the score fallback.
     *  Returns true when a reading was rendered (or is already on screen), false when
     *  nothing could be rendered — `complete()` needs to know, so it never leaves the
     *  live model behind. */
    async function inject(sessionId, turnId, turnData, opts) {
        const o = opts || {};
        try {
            const why = (m) => { console.info(`[assurance] dial not rendered — ${m}`); return false; };
            const host = document.getElementById('status-window-content');
            if (!host) return why('#status-window-content is not in the DOM');
            if (sessionId == null || turnId == null) return why('no session/turn id');
            const key = `${sessionId}:${turnId}`;
            if (o.scored) _scored.add(key);
            // NB: an existing dial is the LIVE one — do not bail on it. Bail only if
            // the authoritative payload for this same turn is already rendered.
            if (!o.force && _isOnScreen(host, key)) return true;

            let td = turnData;
            // The readings are derived server-side; a live turn payload does not carry
            // them yet, so fetch when absent rather than rendering an empty dial.
            if (!(td && td.assurance)) {
                const waits = o.retry ? [0, 1500, 4000] : [0];
                for (const w of waits) {
                    if (w) await new Promise((res) => setTimeout(res, w));
                    const token = localStorage.getItem('tda_auth_token');
                    const r = await fetch(`/api/session/${sessionId}/turn/${turnId}/details`,
                        { headers: token ? { Authorization: `Bearer ${token}` } : {} })
                        .catch(() => null);
                    td = r && r.ok ? await r.json().catch(() => null) : null;
                    if (td && td.assurance) break;
                }
            }
            if (!td) return why('the turn-details fetch returned nothing');
            if (!td.assurance) return why('the turn carries no `assurance` block — '
                                        + 'the server predates the R-2 backend change');
            if (td.profile_type === 'flow' || td.flow_run_id) {
                return why('flow turns have no assurance path (by design)');
            }
            // Re-check post-await: the trace can be rebuilt while the fetch is in flight.
            if (!o.force && _isOnScreen(host, key)) return true;

            // Both renderers ship present — but present is not the same as exclusive.
            // A strip mounted by an earlier call (a prior turn, a session switch, a
            // page load before the flag flipped) has nothing that removes it, so the
            // dial would mount underneath it and the screen would look unchanged. That
            // is exactly what "no change" looked like. Retire it here, at the moment
            // its replacement is known to have data to draw.
            const legacy = host.querySelector('#trust-journey-strip');
            if (legacy) legacy.remove();

            // The authoritative payload SUPERSEDES the live model wholesale — it is
            // never merged into it. Merging two derivations of one fact is how they
            // drift into disagreeing; replacing means the live view can only ever have
            // been an earlier, coarser rendering of the same stamps.
            //
            // `_settled` is stamped AFTER a successful mount, never before: stamped
            // first, a single render exception left the flag claiming a settle that
            // never happened, and every retry (`_isOnScreen`) early-returned forever —
            // the frozen live dial the polar()-shape crash produced. A failed mount
            // must leave the state retryable.
            _live = null;
            _mount(host, sessionId, turnId, td.assurance);
            _settled = `${sessionId}:${turnId}`;
            _interventionCards(host, td.assurance);

            // The ambient chat-header mark stays the ring's, not a second glyph system.
            const glyphHost = document.getElementById('chat-trust-glyph-host');
            if (glyphHost && window.TrustCircle && window.TrustCircle.glyphInto) {
                glyphHost.style.display = 'inline-flex';
                glyphHost.dataset.tag = td.profile_tag || '';
                const st = stations(td.assurance);
                window.TrustCircle.glyphInto(glyphHost, ORDER.map((k) => st[k].state),
                    { size: 20, label: 'Latest assurance reading — open the Trust Circle' });
            }
            return true;
        } catch (e) {
            console.warn('[assurance-dial] inject failed (trace unaffected):', e);
            return false;
        }
    }

    /**
     * Whether the dial replaces the strip and the capsule's mini-ring.
     *
     * ONE predicate for both consumers: a flag read in two places can be half-on, and
     * "the capsule migrated but Live Status did not" is the split-brain that shipping
     * this release as a pair exists to prevent.
     *
     * DEFAULT ON, on this branch. It shipped default-off and that was the wrong call
     * for a branch whose entire purpose is to be looked at: three verification rounds
     * were lost to the flag itself rather than to anything it gated. Both renderers are
     * still present, so the revert the flag exists for is intact — it is now opt-OUT:
     *
     *   ?dial=0                              → the legacy strip (latches)
     *   localStorage.tda_assurance_dial='0'  → same
     *   window.__assuranceDial = false       → console poke, wins over both
     *
     * Revisit at merge: production should ship default-off until R-2 is verified there.
     */
    const DEFAULT_ON = true;

    // Read the URL param at PARSE time, not on DOMContentLoaded. An inline script later
    // in the document can call history.replaceState and wipe the query string before
    // any deferred listener runs — a latch that reads too late is not a latch.
    (function latch() {
        try {
            const q = new URLSearchParams(window.location.search).get('dial');
            if (q === '1' || q === 'true') localStorage.setItem('tda_assurance_dial', '1');
            else if (q === '0' || q === 'false') localStorage.setItem('tda_assurance_dial', '0');
        } catch (_) { /* private mode — fall through to the default */ }
    }());

    function enabled() {
        try {
            if (window.__assuranceDial != null) return !!window.__assuranceDial;
            const v = localStorage.getItem('tda_assurance_dial');
            if (v === '1') return true;
            if (v === '0') return false;
            return DEFAULT_ON;
        } catch (_) { return DEFAULT_ON; }
    }

    /**
     * Say which renderer is live. "I still see the old version" has several possible
     * causes that look identical on screen; one line separates them.
     */
    function announce() {
        try {
            console.info(`[assurance] ${enabled() ? 'DIAL (R-2)' : 'legacy strip'}`
                + ` — flip with ?dial=${enabled() ? '0' : '1'}`);
        } catch (_) { /* never break a page over a log line */ }
    }
    try { announce(); } catch (_) { /* noop */ }

    window.AssuranceDial = {
        placeScorecard, render, inject, begin, note, phase, settle, complete,
        // TEST SEAMS — the station section and the marks key, so the class-scope gate
        // executes the shipped renderers rather than a regex over their source.
        _sectionHtml: sectionHtml, _markKey: markKey,
                             stations, summarise, routing, enabled,
                             announce, pinned, pinnedDefault, hasDefault, setPinned, lockPin,
                             // dialSvg is exported as a TEST SEAM: the gate must EXECUTE
                             // the acted-dot render path (a shape bug there crashed every
                             // acted-turn mount and froze the live dial — caught only by
                             // a headless browser, because no gate ran this code).
                             ORDER, LABEL, RANK, strokeFor, stationReading, dialSvg };
}());
