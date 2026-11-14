/**
 * The Trust Circle — the closed trust loop as a living, clickable instrument.
 *
 * One radial SVG: the agent (a star — the anatomy metaphor) at the center,
 * the loop's eight stations arranged clockwise around it, enclosed by the
 * dashed Trust Membrane. Every station renders its REAL state with the shared
 * trust-token grammar (solid = pass, half = warn, danger, dashed = absent —
 * honest absence stays visible). Live pulses travel the ring as real turns
 * and trust events happen. Clicking a station navigates to its operating
 * surface — the circle is trust *navigation*, not a poster.
 *
 * Pure renderer: data arrives as a prepared model (built by the Agents lens,
 * which owns all the endpoints); this file fetches nothing.
 *
 *   window.TrustCircle.mount({ host, model, onNavigate })
 *   window.TrustCircle.update(model)     // re-render states in place
 *   window.TrustCircle.pulse(kind)       // 'turn' = lap the ring · station key = flash
 *
 * Model: {
 *   center:   { label, sub },
 *   stations: [{ key, state: pass|warn|danger|na|pending|unknown, value, headline,
 *                facts: [..], acted, owed, cta: { label, action } }],  // 8, canonical order
 *                // acted → ONE muted state-coloured dot (a mechanism acted in the window);
 *                // owed  → the accent owed dot (an act is owed — pin to perform it)
 *   pickable: { items: [{id,tag,name,type}], current, allLabel, onPick },  // optional agent picker (IFOC-sorted array)
 *   lens:     { mode: 'total'|'trip', since, leg_n, note, noLeg, onSwitch },  // optional trust-trip lens (TRUST_TRIP_RESET.md)
 *   trip:     { canReset, onReset(note) },        // optional "start a new leg" act (focused, own agent)
 * }
 *
 * Visual language borrowed from static/trust/agent-anatomy-of-trust.html:
 * the dashed membrane, the star, mono uppercase station labels with
 * serif-italic annotations, the faint constellation. Theme-aware via the
 * trust tokens (trust.css); motion respects prefers-reduced-motion.
 */
(function () {
    'use strict';

    // Canonical loop order (clockwise from the top) — mirrors the story's arc:
    // born/signed → acts through the gates → scored → trust breathes → a human
    // decides → proven — and the accent arc closes the loop back to Signed.
    /** The acted mark's word, from the ONE trust vocabulary (`TrustGrammar.TICK_ARIA`),
     *  which is also what the aria-label reads. The legend hardcoded its own copy, so
     *  the mark could be described one way to a screen reader and another in the key
     *  beneath it — and when the noun was corrected ("a gate acted" narrowed a mark nine
     *  mechanisms can set, only four of them gates) the two copies drifted apart. */
    function _actedWord() {
        const TG = (typeof window !== 'undefined' && window.TrustGrammar)
                || (typeof globalThis !== 'undefined' && globalThis.TrustGrammar);
        return ((TG && TG.TICK_ARIA && TG.TICK_ARIA[2]) || 'a mechanism acted');
    }

    // The ONE station registry lives in trustGrammar.js (W5 — mirrored from
    // assurance_points.STATION_TABLE). The literal below is the absence-tolerant
    // fallback, gate-pinned equal to the canonical. `scored` is labelled JUDGED: the
    // wire key never moves (spans, payloads and Guide keys are persisted); TRUSTED's
    // value is a conduct SCORE, so "Scored" carried two meanings on one ring. `q` is
    // the QUESTION each station answers — the lead a reader needs before the
    // participle means anything; `sub` names its character (Rule 3.3b).
    const STATIONS = (globalThis.TrustGrammar && Array.isArray(globalThis.TrustGrammar.STATIONS)
        && globalThis.TrustGrammar.STATIONS.length)
        ? globalThis.TrustGrammar.STATIONS.map((r) => ({ key: r.key, label: r.label, sub: r.sub, q: r.q, lane: r.lane }))
        : [
        { key: 'signed',   label: 'Signed',   sub: 'born, then attested', q: 'Is it signed, and attested here?' },
        { key: 'grounded', label: 'Grounded', sub: 'truth · faithfulness', q: 'Does it stay on its corpus?' },
        { key: 'guarded',  label: 'Guarded',  sub: 'rules · safety', q: 'Does it stay within the rules?' },
        { key: 'bounded',  label: 'Bounded',  sub: 'authority, borrowed', q: 'Does it act only with the authority it was given?' },
        { key: 'scored',   label: 'Judged',   sub: 'execution quality, graded', q: 'How did its answers grade?' },
        { key: 'trusted',  label: 'Trusted',  sub: 'the badge breathes', q: 'Is it trusted over time?' },
        { key: 'reviewed', label: 'Reviewed', sub: 'a human decides', q: 'Is a person deciding?' },
        { key: 'proven',   label: 'Proven',   sub: 'receipts, not promises', q: 'Can its answers be proven?' },
    ];

    // Monochrome 24-viewBox stroke icons, one per station.
    // Icons from the ONE registry (trustGrammar.STATION_ICONS — shared with the per-turn
    // dial); the literal is the absence-tolerant fallback, gate-pinned equal.
    const ICONS = (globalThis.TrustGrammar && globalThis.TrustGrammar.STATION_ICONS)
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

    // The anatomy page's star (the agent), verbatim.
    const STAR_PATH = 'M0,-11 L2.7,-3.72 L10.46,-3.4 L4.37,1.42 L6.47,8.9 L0,4.6 L-6.47,8.9 L-4.37,1.42 L-10.46,-3.4 L-2.7,-3.72 Z';

    /* The prototype's radial budget, verbatim — its README records that this layout
     * was verified NUMERICALLY (text boxes at real character widths, band annuli, the
     * star walked vertex by vertex), and the first port kept the elements but not the
     * proportions: strokes designed for a 780-wide canvas landed on a 600-wide one, the
     * subject star was 2.4× the prototype's and swallowed the ring, and the three-line
     * plates clipped at the bottom and left edges. Porting the numbers inherits the
     * verification; inventing new ones re-opens it.
     *      0–48    the star + its two lines (the subject)
     *      118     inner hairline
     *      170     THE BAND — arcs centred on the station angle, markers on top
     *      181–189 the acted dot (ONE state-coloured dot = a mechanism acted; no count channel)
     *      195     the accent owed dot (an act is owed — actionability channel)
     *      204     the loop arc (the empty proven→signed sector only)
     *      228     station plates (name · reading · motto)
     *      270     the membrane                                              */
    const CX = 400, CY = 350, R = 170, LABEL_R = 228, MEMBRANE_R = 270;
    const STAR_SCALE = 3.9;   // the SUBJECT, not the stage — the ring is the instrument
    const NS = 'http://www.w3.org/2000/svg';

    let _host = null, _model = null, _onNavigate = null;
    let _railPath = null;         // the pulse rail
    // The PINNED station. Hover is a peek; a click pins that station's panel and reveals
    // its actions. Without this, acting on a station meant navigating away — which lost
    // the reading you were acting on, and allowed exactly one action per station however
    // many the state actually warranted (SIGNED offered "open the posture matrix", a
    // surface that cannot sign). Read stays in the panel; act happens beside it.
    let _pinned = null;
    //: The station whose derivation explainer ("?") is open. Panel-scoped, not global:
    //: pinning another station or unpinning closes it, so an explanation can never
    //: outlive the reading it explains.
    let _derivOpen = null;
    let _dismissWired = false;    // document-level Escape + light dismiss, wired once per page
    let _returningFocus = false;  // true only while unpin() hands focus back (not a user peek)
    // What the panel is CURRENTLY showing. `renderPanel` is idempotent against this: it
    // never rewrites innerHTML to draw what is already drawn.
    //
    // This is not an optimisation — it is a correctness fix. Hover/focus handlers call
    // renderPanel() on every mouseleave and blur, and while a station is pinned those
    // calls resolve to the SAME pinned panel. Rebuilding it destroyed the live nodes,
    // and clicking the ✕ does exactly that mid-gesture: mousedown moves focus off the
    // station → `blur` → rebuild → the button that received mousedown no longer exists →
    // `mouseup` lands elsewhere → the browser fires NO click on the button. Hence "one
    // click to focus, a second to close". Any future handler that calls renderPanel()
    // during a gesture would have re-broken it, so the guard belongs in the renderer.
    let _renderedKey;             // undefined = nothing rendered yet; null = the narrative
    let _renderedDeriv = false;   // whether the rendered panel has its explainer open
    let _renderedPinned = false;
    /** Force the next renderPanel() to repaint (the model or subject changed). */
    function _invalidatePanel() { _renderedKey = undefined; }
    let _pulseRaf = 0;

    const reduceMotion = () =>
        window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    function esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function pt(angleDeg, r) {
        const a = (angleDeg * Math.PI) / 180;
        return { x: CX + r * Math.cos(a), y: CY + r * Math.sin(a) };
    }
    const stationAngle = (i) => -90 + i * 45;   // clockwise from the top
    /** Band-segment arc centred ON the station's canonical angle. Centring on segment
     *  midpoints instead rotated the whole ring by half a segment in the prototype —
     *  which is what threw every label and value out of place. */
    const SEG = 45, SEG_GAP = 9;
    function segArc(r, i, spread) {
        const half = (spread != null ? spread : (SEG - SEG_GAP)) / 2;
        const p0 = pt(stationAngle(i) - half, r), p1 = pt(stationAngle(i) + half, r);
        return `M ${p0.x.toFixed(2)} ${p0.y.toFixed(2)} A ${r} ${r} 0 0 1 ${p1.x.toFixed(2)} ${p1.y.toFixed(2)}`;
    }
    function radial(r0, r1, angleDeg) {
        const a = pt(angleDeg, r0), b = pt(angleDeg, r1);
        return `M ${a.x.toFixed(2)} ${a.y.toFixed(2)} L ${b.x.toFixed(2)} ${b.y.toFixed(2)}`;
    }

    // ── SVG scaffolding ─────────────────────────────────────────────────────────
    function buildSvg(model) {
        // Re-spread for the 780×680 canvas — every star sits OUTSIDE the membrane
        // (distance from centre > 285 > MEMBRANE_R), checked numerically like the
        // plates: the field is decoration and must never read as content inside the
        // boundary. Deterministic positions, so a collision cleared stays cleared.
        const constellation = [[50, 64, 1.0], [140, 36, 0.62], [712, 60, 0.85], [748, 196, 0.6],
                               [36, 520, 0.7], [730, 560, 0.78], [120, 640, 0.55], [650, 646, 0.9],
                               [700, 380, 0.5], [66, 300, 0.45]]
            .map(([x, y, s]) => `<path d="${STAR_PATH}" transform="translate(${x},${y}) scale(${s})" class="tc-constellation"/>`)
            .join('');

        // Station nodes + labels.
        let nodes = '';
        // Per-sector plate anchoring, from the prototype's numeric verification: text
        // grows AWAY from the ring (never across it), and the LDY offsets are the ones
        // proven to clear the band with a THREE-line plate at every angle — the two-line
        // trigonometric offsets this replaced were what clipped SCORED at the bottom.
        const ANCH = ['middle', 'start', 'start', 'start', 'middle', 'end', 'end', 'end'];
        const LDY = [-40, -26, -16, -6, 14, -6, -16, -26];
        const VAL_DY = 18, SUB_DY = 36;   // line spacing for the readable type
        STATIONS.forEach((st, i) => {
            const ang = stationAngle(i);
            const p = pt(ang, R);
            const lp0 = pt(ang, LABEL_R);
            const anchor = ANCH[i];
            const baseDy = LDY[i];
            const pad = anchor === 'middle' ? 0 : (anchor === 'start' ? 12 : -12);
            const lp = { x: lp0.x + pad, y: lp0.y };
            // Markers on a band (ledger §4.3): each station is an arc of ONE
            // continuous band — literally the same geometry as the Live Status dial,
            // so the two instruments are recognisably one family — with the marker
            // sitting ON the band and a three-line engraved plate OUTSIDE it:
            // name · reading · motto. Data before decoration — the reading sits
            // directly under the name, never under the marker where the band curves
            // through.
            //
            // The mark means the same on EVERY instrument (PROP-002 as amended —
            // ONE dot, ONE meaning): a single state-coloured dot = a mechanism ACTED on
            // this subject (here: on ≥1 turn in the window — the dial's own mark
            // at ring altitude). "An act is OWED" rides the accent-orange
            // actionability channel as the owed dot. No pairs, no count channel —
            // the retired I/II depth is the drift this comment used to document.
            // Both are pre-drawn and toggled by applyModel, because acted/owed are
            // the MODEL's facts, not the renderer's.
            nodes += `
            <g class="tc-node" data-tc-station="${st.key}" role="button" tabindex="0"
               aria-expanded="false" aria-label="${esc(st.label)} — ${esc(st.q || st.sub)}">
                <path class="tc-bandhit" d="${segArc(R, i, SEG - 2)}"/>
                <path class="tc-bandseg" d="${segArc(R, i)}"/>
                <circle class="tc-acted-dot" cx="${pt(stationAngle(i), R + 15).x.toFixed(2)}" cy="${pt(stationAngle(i), R + 15).y.toFixed(2)}" r="1.8" style="display:none"/>
                <circle class="tc-owed-dot" cx="${pt(stationAngle(i), R + 25).x.toFixed(2)}" cy="${pt(stationAngle(i), R + 25).y.toFixed(2)}" r="3.1" style="display:none"/>
                <rect class="tc-plate-hit" x="${(anchor === 'middle' ? lp.x - 110 : anchor === 'start' ? lp.x - 6 : lp.x - 214).toFixed(1)}"
                      y="${(lp.y + baseDy - 16).toFixed(1)}" width="220" height="${SUB_DY + 28}" rx="6"/>
                <rect class="tc-node-halo" x="${p.x - 19}" y="${p.y - 19}" width="38" height="38" rx="11"/>
                <rect class="tc-node-box" x="${p.x - 16}" y="${p.y - 16}" width="32" height="32" rx="9"/>
                <g class="tc-node-icon" transform="translate(${p.x - 9}, ${p.y - 9}) scale(0.75)"
                   fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
                    ${ICONS[st.key]}</g>
                <!-- The plate text keeps pointer-events:none (thin glyphs are a miserable
                     hit target) — the tc-plate-hit rect above is what makes the LABEL a
                     working handle for the station: hover, focus and click land on the
                     group through it, same as the band and the marker. -->
                <text class="tc-label" x="${lp.x}" y="${lp.y + baseDy}" text-anchor="${anchor}">${esc(st.label).toUpperCase()}</text>
                <text class="tc-value" x="${lp.x}" y="${lp.y + baseDy + VAL_DY}" text-anchor="${anchor}"></text>
                <text class="tc-sub" x="${lp.x}" y="${lp.y + baseDy + SUB_DY}" text-anchor="${anchor}">${esc(st.sub)}</text>
            </g>`;
        });

        const SY = CY - 16;   // star above centre; the identity block reads below it
        // The loop-closing arc (proven → signed) — radially OUTSIDE the band, in the
        // sector between the two stations, because the band itself now occupies R.
        const LOOPR = 204;
        const a0 = pt(230 - 360, LOOPR), a1 = pt(263 - 360, LOOPR);
        const closeArc = `M ${a0.x.toFixed(2)} ${a0.y.toFixed(2)} A ${LOOPR} ${LOOPR} 0 0 1 ${a1.x.toFixed(2)} ${a1.y.toFixed(2)}`;
        const mid = pt(246 - 360, LOOPR + 19);

        return `
        <!-- The viewBox carries ±32px side margins beyond the prototype's 800-wide
             canvas: the side plates grow OUTWARD from LABEL_R, and a long value
             ("observe · review · 100%") overran x=800 and was CLIPPED mid-glyph
             ("10" for "100%"). The margin is canvas, not geometry — every
             coordinate is untouched, so the numeric layout verification holds. -->
        <svg viewBox="-32 0 864 700" class="tc-svg" role="img"
             aria-label="The Uderia trust circle — the closed loop of the eight trust stations around the agent.">
            <defs>
                <linearGradient id="tcStar" x1="0" y1="0" x2="0.35" y2="1">
                    <stop offset="0" stop-color="#a6c0d6"/><stop offset="1" stop-color="#3c5273"/>
                </linearGradient>
                <marker id="tcArrow" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="6.5" markerHeight="6.5" orient="auto-start-reverse">
                    <path d="M0,0 L10,5 L0,10 z" fill="var(--teradata-orange, #F15F22)"/>
                </marker>
                <filter id="tcBlur" x="-60%" y="-60%" width="220%" height="220%">
                    <feGaussianBlur stdDeviation="16"/>
                </filter>
                <filter id="tcGlow" x="-80%" y="-80%" width="260%" height="260%">
                    <feGaussianBlur stdDeviation="3.2" result="b"/>
                    <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
                </filter>
            </defs>

            ${constellation}

            <!-- The Trust Membrane — everything the agent is, inside one boundary -->
            <circle class="tc-membrane" cx="${CX}" cy="${CY}" r="${MEMBRANE_R}"/>
            <text class="tc-membrane-label" x="${pt(34 - 90, MEMBRANE_R + 10).x.toFixed(1)}"
                  y="${pt(34 - 90, MEMBRANE_R + 10).y.toFixed(1)}" text-anchor="start">TRUST&#8202;&#8202;MEMBRANE</text>

            <!-- The rail the pulses travel — kept for the pulse path, quietened to a
                 ground line: the band segments carry the state that used to sit here. -->
            <path class="tc-rail" d="M ${CX} ${CY - R} A ${R} ${R} 0 1 1 ${CX - 0.01} ${CY - R}"/>
            <circle class="tc-gnd" cx="${CX}" cy="${CY}" r="118"/>

            <!-- The loop closes: proven → signed -->
            <path class="tc-close-arc" d="${closeArc}" marker-end="url(#tcArrow)"/>
            <text class="tc-close-label" x="${mid.x.toFixed(1)}" y="${mid.y.toFixed(1)}" text-anchor="middle">the loop closes</text>

            <!-- The agent — a star at the center (genie: a star made of stars) -->
            <g class="tc-center" role="button" tabindex="0" aria-label="Choose the agent in focus">
                <!-- The star marks the SUBJECT; the identity reads below it. The first
                     port kept the shipped 9.5× star, which filled the ring and put the
                     labels inside its own body — the subject wearing the stage. -->
                <circle class="tc-center-halo" cx="${CX}" cy="${SY}" r="54"/>
                <circle class="tc-class-glow" cx="${CX}" cy="${SY}" r="72" filter="url(#tcBlur)" style="display:none"/>
                <!-- The prototype's subject verbatim: an OUTLINE star (faint fill,
                     hairline stroke) with the plus seated in its body — the plus means
                     "you can change it", one glyph, one meaning, three surfaces. The
                     steel-gradient fill was the old stage-sized star's dress; at subject
                     size it read as a solid blue badge and the plus had nowhere to sit. -->
                <g transform="translate(${CX},${SY}) scale(${STAR_SCALE})">
                    <path class="tc-star-i" d="${STAR_PATH}"/>
                    <path class="tc-star tc-star-f" d="${STAR_PATH}"/>
                </g>
                <g class="tc-tip-dots" style="display:none">${
                    [[0, -11], [10.46, -3.4], [6.47, 8.9], [-6.47, 8.9], [-10.46, -3.4]]
                        .map(([tx, ty]) => `<circle cx="${CX + tx * STAR_SCALE}" cy="${SY + ty * STAR_SCALE}" r="2.4"/>`) .join('')}</g>
                <path class="tc-subject-plus" d="M ${CX - 5} ${SY + 1} H ${CX + 5}"/>
                <path class="tc-subject-plus" d="M ${CX} ${SY - 4} V ${SY + 6}"/>
                <text class="tc-center-label" x="${CX}" y="${CY + 50}" text-anchor="middle"></text>
                <text class="tc-center-sub" x="${CX}" y="${CY + 68}" text-anchor="middle"></text>
                <text class="tc-center-hint" x="${CX}" y="${CY + 86}" text-anchor="middle">change &#9662;</text>
            </g>

            <circle class="tc-pulse" r="5" filter="url(#tcGlow)" style="display:none"/>
            ${nodes}
        </svg>`;
    }

    // The attention tally, grouped by what the colour asks of you. Three rows — attention ·
    // quiet · absent — each naming the stations in it from the ONE label registry, so the
    // consumer never scans eight glyphs to find the one that wants them, and the ring stays
    // honest for someone who never hovers. `na` is a chosen posture and is deliberately NOT
    // an attention count — counting it would put every fresh install in permanent alarm.
    // Rule 3.7 — a watching station is never summarised as quiet: it is turned on and stops
    // nothing, so it is named in the quiet row with that said. Rule 3.0 — the figures live in
    // the verdict prose above; this block carries the NAMES, never a second copy of a number.
    function _attentionSummary() {
        const st = (_model && _model.stations) || [];
        if (!st.length) return '';
        const { now, look, watching, pass, absent } = _attentionCounts();
        // The LABEL, never the wire key — `s.key.toUpperCase()` printed SCORED in the
        // summary while the plate below read JUDGED: two names for one station on one
        // screen, from a second label source. STATIONS is the only registry.
        const lbl = Object.fromEntries(STATIONS.map((x) => [x.key, x.label.toUpperCase()]));
        const name = (arr) => arr.map((s) => lbl[s.key] || s.key.toUpperCase()).join(', ');
        const watchLine = watching.length
            ? `${watching.length} watching — ${esc(name(watching))} (recording, not stopping)` : '';
        const attnParts = [];
        if (now.length) attnParts.push(`<strong>${now.length} needs you now</strong> — ${esc(name(now))}`);
        if (look.length) attnParts.push(`${look.length} worth a look — ${esc(name(look))}`);
        const attnText = attnParts.length ? attnParts.join(' · ')
            : (watching.length ? 'Nothing needs you right now.' : 'All stations quiet — nothing needs you right now.');
        const quietText = [pass.length ? esc(name(pass)) : '', watchLine].filter(Boolean).join(' · ') || 'none';
        const absentText = absent.length ? esc(name(absent)) : 'none';
        // Word + names only. What each colour asks is said once, in the folded key below —
        // repeating it on every row was the wordiness the operator flagged.
        const row = (cls, word, names) => `<div class="tc-group"><span class="tc-group-sw ${cls}" aria-hidden="true"></span>`
            + `<span><b>${word}</b> <span class="tc-group-names">${names}</span></span></div>`;
        return `<div class="tc-groups">${
            row(now.length ? 'tc-g-danger' : (look.length ? 'tc-g-warn' : 'tc-g-attn'), 'attention', attnText)}${
            row('tc-g-quiet', 'quiet', quietText)}${
            row('tc-g-absent', 'absent', absentText)}</div>`;
    }

    function _attentionCounts() {
        const st = (_model && _model.stations) || [];
        const by = (k) => st.filter((s) => s.state === k);
        return { now: by('danger'), look: by('warn'), watching: by('watching'), pass: by('pass'),
                 absent: st.filter((s) => s.state === 'na' || s.state === 'pending' || s.state === 'unknown'
                                        || !['danger', 'warn', 'watching', 'pass'].includes(s.state)) };
    }
    // ── the verdict (W2) — the glance layer, as ONE sentence in the Anatomy pane's voice: the
    // colour word leads (from the same counts the groups below are named from), then the
    // host's rows (`_verdictLine`: needs you · posture · record) read as sentences. The subject
    // is the eyebrow, so it is not repeated here.
    function _verdictProse() {
        const v = _model && _model.verdict;
        const { now, look } = _attentionCounts();
        const pl = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
        // The lead counts what the sentence then lists: the host's needs-you row carries the
        // amber findings too, so a danger lead names the amber count beside it.
        const lead = now.length ? { st: 'danger', t: `${pl(now.length, 'station')} ${now.length === 1 ? 'needs' : 'need'} you${look.length ? `, ${look.length} worth a look` : ''}:` }
            : look.length ? { st: 'warn', t: `${pl(look.length, 'station')} worth a look:` }
            : { st: 'pass', t: 'Nothing needs you right now.' };
        const cap = (t) => t.charAt(0).toUpperCase() + t.slice(1);
        const sentences = (v && v.lines ? v.lines : []).map((l) => cap(l.text).replace(/\.?$/, '.'));
        if (_model && _model.closures && _model.closures.count) {
            sentences.push(`The loop has closed ${_model.closures.count}×${_model.closures.last ? ` — last, ${_model.closures.last}` : ''}.`);
        }
        const body = sentences.length ? esc(sentences.join(' ')) : 'No readings yet.';
        return `<p class="tc-verdict-prose"><span class="tc-v-lead tc-v-${lead.st}">${esc(lead.t)}</span> ${body}</p>`;
    }

    // ── the side panel (default narrative / station detail) ─────────────────────
    function defaultPanel() {
        const c = (_model && _model.center) || {};
        const one = c.kind === 'agent';
        return `
        <div class="tc-panel-head">
            <p class="tc-eyebrow">${esc([c.label, c.sub, 'right now'].filter(Boolean).join(' · '))}</p>
            <h4 class="tc-headline">Born · signed · grounded · guarded · judged <em class="tc-headline-tail">— and the loop closes.</em></h4>
            ${_verdictProse()}
            <p class="tc-copy">Every station is a live reading of this ${one ? 'agent' : 'estate'} —
               <em>configuration · execution</em>. Colour calls for attention, not a verdict:
               hover to read both sides, click to pin and act.</p>
        </div>
        ${_attentionSummary()}
        <details class="tc-howto">
            <summary>How to read this ring · the full key</summary>
            <p class="tc-copy">Red needs you now, amber is worth a look, solid green is protected. A hollow
               green station is <em>watching</em> — on, recording, stopping nothing. A dashed station is
               honest absence: not turned on, not in yet, or not recorded. A muted dot means a mechanism
               acted in this window; an orange dot means an act is owed — pin the station to perform it.</p>
            <div class="tc-legend">
                <span class="tc-leg"><span class="tc-leg-glyph tc-leg-pass"></span>quiet</span>
                <span class="tc-leg"><span class="tc-leg-glyph tc-leg-warn"></span>worth a look</span>
                <span class="tc-leg"><span class="tc-leg-glyph tc-leg-danger"></span>needs you now</span>
                <span class="tc-leg"><span class="tc-leg-glyph tc-leg-watching"></span>watching — quiet, but stops nothing</span>
                <span class="tc-leg"><span class="tc-leg-glyph tc-leg-na"></span>not turned on</span>
                <span class="tc-leg"><span class="tc-leg-glyph tc-leg-pending"></span>not in yet</span>
                <span class="tc-leg"><span class="tc-leg-glyph tc-leg-unknown"></span>not recorded</span>
            </div>
            <div class="tc-legend tc-legend-marks">
                <span class="tc-leg"><span class="tc-leg-mark">•</span>${_actedWord()}</span>
                <span class="tc-leg"><span class="tc-leg-mark tc-leg-owed">●</span>an act is owed — pin the station to perform it</span>
            </div>
        </details>
        ${_model && _model.anatomy && _model.center && _model.center.kind === 'agent'
            ? `<button type="button" class="tc-cta tc-anatomy-link" style="margin-top:10px;margin-right:8px;background:transparent;">
                 <svg width="11" height="11" viewBox="-13 -13 26 26" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M0,-11 L2.7,-3.72 L10.46,-3.4 L4.37,1.42 L6.47,8.9 L0,4.6 L-6.47,8.9 L-4.37,1.42 L-10.46,-3.4 L-2.7,-3.72 Z"/></svg>
                 Open Anatomy</button>` : ''}
        ${_model && _model.awareness && _model.center && _model.center.kind === 'agent'
            ? `<button type="button" class="tc-cta tc-awareness-link" style="margin-top:10px;margin-right:8px;background:transparent;">
                 <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/></svg>
                 Open Awareness</button>` : ''}
        ${_model && _model.ontology && _model.center && _model.center.kind === 'agent'
            ? `<button type="button" class="tc-cta tc-estate-link" style="margin-top:10px;margin-right:8px;background:transparent;">
                 <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg>
                 Open Ontology</button>` : ''}
        ${_model && _model.story && _model.center && _model.center.kind === 'agent'
            ? `<button type="button" class="tc-cta tc-play" style="margin-top:10px;">
                 <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M7 4l13 8-13 8z"/></svg>
                 Play its life</button>` : ''}
        ${_model && _model.trip && _model.trip.canReset && _model.center && _model.center.kind === 'agent'
            ? `<button type="button" class="tc-cta tc-newleg" style="margin-top:10px;margin-right:8px;background:transparent;">
                 <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="13" r="8"/><path d="M12 13l4-4"/><path d="M12 3v2"/></svg>
                 Start a new leg</button>` : ''}
        <div class="tc-foot">
            <a class="tc-story-link" href="https://trust.uderia.com/the-life-of-a-uderia-agent.html" target="_blank" rel="noopener">
                Read the full story — the life of a Uderia agent
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 17L17 7M9 7h8v8"/></svg></a>
            <div class="tc-lastevent" id="tc-lastevent"><span class="tc-lastevent-dot"></span><span id="tc-lastevent-text">Watching for live trust events…</span></div>
        </div>`;
    }

    /** The derivation explainer — how THIS station's two figures were computed.
     *
     *  A distinct zone, deliberately **not** a fifth slot: the four slots carry the
     *  reading (what it says and why), this carries the METHOD (how the number was
     *  arrived at). Conflating them would have made every hover longer for the many
     *  readers who don't need the method, and Rule 3.0 forbids a slot that restates
     *  another.
     *
     *  Where quality dimensions contribute, they are rendered as real buttons that open
     *  the existing Assurance Guide — the ring never explains a dimension itself, it
     *  routes to the one surface that already does (one source, no second glossary).
     *  A station with NO dimension input says so explicitly: four of the eight have
     *  none, and leaving that unsaid would imply the ring is a view onto the Quality
     *  card, which it is not.
     */
    function derivationPanel(d) {
        if (!d) return '';
        const row = (label, text) => text
            ? `<div class="tc-dk">${esc(label)}</div><div class="tc-dd">${esc(text)}</div>` : '';
        const dims = (d.dims || []).filter((x) => x && x.key);
        // W3 — a guarantee station carries ONE line for its measurements, not the grid:
        // the checks about it are counted at JUDGED, and the row says how they graded
        // and opens JUDGED, where every chip lives. The grid rendered here used to be
        // the strongest visual on the panel while colouring nothing on it.
        const g = d.graded;
        const gradedRow = g
            ? `<div class="tc-dk">Graded at JUDGED</div><div class="tc-dd">${
                g.of ? `${g.clean} of ${g.of} checks measured clean` : 'no check measured in this window'}${
                g.total > g.of ? ` · ${g.total - g.of} not produced by this class` : ''}${
                g.disputed ? ` · ${g.disputed} disputed` : ''} · <button type="button" class="tc-dim tc-dim-route"
                data-tc-act="pin:scored">open JUDGED</button></div>`
            : '';
        // W6 — the method lives in the Assurance Guide; the explainer keeps only rows
        // that carry a live value. When neither attribute has one, say where the method is.
        const methodRow = (!d.config && !d.exec)
            ? `<div class="tc-dk">Method</div><div class="tc-dd">how this station is derived is in the Assurance Guide — open it below</div>`
            : '';
        const chips = dims.map((x) => {
            const band = x.applicable ? (x.band || 'na') : 'na';
            // Rule 3.5 — the figure carries its own denominator, and at n<=2 the PERCENTAGE
            // FORM ITSELF is the over-claim (0% and 100% are the only values one sample can
            // produce). So a tentative dimension renders as a fraction; everything else keeps
            // the percentage. Identical rule to the roster bar — one vocabulary, two surfaces.
            const val = !(x.applicable && x.score != null) ? ''
                : (x.tentative && x.n != null)
                    ? ` <span class="tc-dim-v">${Math.round(x.score * x.n)}/${x.n}</span>`
                    : ` <span class="tc-dim-v">${Math.round(x.score * 100)}%</span>`;
            // The badge carries the denominator only where the value doesn't already: at
            // n<=2 the fraction IS the denominator, so repeating it would restate (Rule 3.0).
            // Never coloured — confidence is not attention (Rule 4.1); shape does that job.
            const nBadge = (x.applicable && x.partial && !x.tentative && x.n != null)
                ? ` <span class="tc-dim-n">n=${x.n}</span>` : '';
            const tip = x.applicable
                ? `${x.label}${x.n != null ? ` · decided on ${x.n} of ${x.total != null ? x.total : '?'} scored turn${x.n === 1 ? '' : 's'}` : ''}`
                  + `${x.tentative ? ' — a single verdict drives this rate' : ''}\nClick to read what it measures.`
                : `${x.label} · this agent's class does not produce this dimension\nClick to read what it measures.`;
            return `<button type="button" class="tc-dim tc-dim-${esc(band)}${x.tentative ? ' tentative' : ''} u-tip"
                data-tc-act="explain-dim:${esc(x.key)}" data-tip="${esc(tip)}"
                >${esc(x.label)}${val}${nBadge}</button>`;
        }).join('');
        return `<div class="tc-deriv" id="tc-deriv" role="region" aria-label="How these figures are derived">
            <div class="tc-deriv-h">How these figures are derived</div>
            <div class="tc-drows">
                ${row('Measured over', d.window)}
                ${row('Configuration', d.config)}
                ${row('Execution', d.exec)}
                ${gradedRow}
                ${methodRow}
            </div>
            ${dims.length ? `<div class="tc-deriv-h tc-deriv-h2">Contributing quality dimensions</div>
                <div class="tc-dims">${chips}</div>
                <p class="tc-deriv-note tc-deriv-fixed">Which dimensions this station watches is fixed in
                code. Their <em>pass lines</em> are configurable — for the deterministic ones; a judge
                dimension has no threshold to set. Click any chip for its line and where to change it.</p>` : ''}
            ${d.note ? `<p class="tc-deriv-note">${esc(d.note)}</p>` : ''}
        </div>`;
    }

    // Triage words, not verdict words: the colour encodes "does this need me?", and
    // the four hover slots carry the reading itself. THE canonical map lives in
    // trustGrammar.js (PROP-002 — one vocabulary module); this reads it, and the
    // literal below is only the absence-tolerant fallback, gate-pinned equal to the
    // canonical. Still EXPORTED (`TrustCircle.stateWord`) so older consumers keep
    // working — but a NEW instrument should read `TrustGrammar` directly.
    // Attention words plus the absence family (`watching` needs a word here too).
    const stateWord = (globalThis.TrustGrammar && globalThis.TrustGrammar.ATTN_WORD)
        ? Object.assign({}, globalThis.TrustGrammar.ABSENCE_WORD || {}, globalThis.TrustGrammar.ATTN_WORD)
        : { pass: 'quiet', warn: 'worth a look', danger: 'needs you now', na: 'not turned on', watching: 'watching' };

    /** The attention word for a state. Unknown states return '' rather than guessing —
     *  a wrong triage word is worse than none. */
    function attentionWord(state) { return stateWord[state] || ''; }

    function stationPanel(st, data, pinned) {
        const stateWord = attentionWord(data.state);
        // W3 — the LANE in the eyebrow: when this station speaks (in flight and able
        // to change the answer, or after the turn and grading only). The lane cut
        // carried where a reader sees it, not in a paragraph under sixteen chips.
        const laneChip = st.lane ? ` <span class="tc-lane">${esc(st.lane)}</span>` : '';
        // Rows are STRUCTURED (label / value / qualifier), so the panel can give the two
        // attributes visual priority and demote the reasoning — instead of four lines of
        // identical grey prose in which the actual data is buried mid-sentence.
        // A hairline separates the READING (what it says) from the REASONING (why).
        const rows = (data.facts || []).filter(Boolean);
        let seenReason = false;
        const facts = rows.map((f) => {
            if (typeof f === 'string') return `<div class="tc-fk">&nbsp;</div><div class="tc-fd">${esc(f)}</div>`;
            const rule = (f.t === 'reason' && !seenReason && (seenReason = true))
                ? '<div class="tc-frule"></div>' : '';
            const val = f.v ? `<span class="tc-fv">${esc(f.v)}</span>` : '';
            const det = f.d ? `<span class="tc-fd">${esc(f.d)}</span>` : '';
            return `${rule}<div class="tc-fk">${esc(f.k)}</div>`
                + `<div class="tc-fb">${val}${val && det ? ' ' : ''}${det}</div>`;
        }).join('');
        // Unpinned: the station itself is the affordance and a quiet hint keeps the click
        // discoverable. Pinned: the panel stays put and its ACTIONS appear — derived from
        // this station's state, never hardcoded per station (an unsigned station needs
        // "sign", a changed one needs "re-sign" AND "verify", a quiet one needs "verify").
        const acts = (data.actions || []).filter((a) => a && a.label && a.action);
        if (!pinned) {
            const hint = data.cta || acts.length || data.derivation
                ? `<p class="tc-hint">Click the station to keep this open${
                    acts.length ? ', act on it' : ''}${
                    data.derivation ? ` and see how ${data.execution && data.execution.text ? 'these figures are' : 'this is'} derived` : ''}.</p>`
                : '';
            return `
            <div class="tc-panel-head">
                <p class="tc-eyebrow">${esc(st.label)} <span class="tc-state-chip tc-state-${esc(data.state)}">${esc(stateWord)}</span>${laneChip}</p>
                ${st.q ? `<p class="tc-q">${esc(st.q)}</p>` : ''}
                <h4 class="tc-headline">${esc(data.headline || st.sub)}</h4>
                ${facts ? `<div class="tc-rows">${facts}</div>` : ''}
            </div>
            ${hint}`;
        }
        // Navigation stays available as one of the buttons, so pinning never removes a
        // route that used to exist. A station may offer TWO routes (#17): the reading's
        // DRIVER decides which leads — an execution shortfall points at the turns, a
        // configuration shortfall at the posture matrix — and the other stays beneath
        // it, because reordering routes is honest while deleting one is not.
        const routes = [data.cta, data.cta2].filter((c) => c && c.label && c.action);
        // The Guide route is a property of BEING a station, not per-station data: all
        // eight have a `station_<key>` entry, so declaring it in each station's model
        // would be the same route written eight times. It rides the ONE Guide channel
        // the dimension chips already use (`explain-dim:`), which resolves the station
        // from the same model this panel rendered from — so the Guide opens on the
        // GUARANTEE (its controls, its measurements) scoped to exactly the dimensions
        // listed here, with their live bands, at either scope.
        //
        // Guarded on the Guide being present: a button that cannot open anything is
        // worse than no button, because it reads as the route (Rule 8.5).
        const guideReady = typeof window !== 'undefined' && window.QualityDimensions
            && typeof window.QualityDimensions.openGuide === 'function';
        if (guideReady) {
            routes.push({ label: 'Open in the Assurance Guide',
                          action: `explain-dim:station_${st.key}` });
        }
        const all = acts.concat(routes.map((c) => ({ label: c.label, action: c.action })));
        // An act of signing a deployment is not entitled to stays visible (the owed act is the owed
        // act) and says where it lives; pressing it tells the reader (auth.js seam), never a 404.
        const withheld = typeof window.signingWithheld === 'function' && window.signingWithheld();
        const isSigningAct = (a) => /sign|attest|revoke/i.test(String(a.action || '')) && !/verify|open|guide/i.test(String(a.action || ''));
        const buttons = all.map((a) => `<button type="button" class="tc-cta tc-act${a.primary ? ' tc-act-primary' : ''}"
             data-tc-act="${esc(a.action)}"${a.primary ? '' : ' style="background:transparent;"'}>${esc(a.label)}${withheld && isSigningAct(a) ? ' <span class="tc-ent" style="font-size:.7em;opacity:.7;letter-spacing:.06em;">· ENTERPRISE</span>' : ''}</button>`).join('');
        // The `?` sits with the close control, not among the action buttons: it changes
        // nothing about the agent, so grouping it with acts (which do) would misrepresent
        // it. Rendered only when the station actually carries a derivation.
        const derivOpen = _derivOpen === st.key;
        const helpBtn = data.derivation
            ? `<button type="button" class="tc-help${derivOpen ? ' on' : ''}" data-tc-deriv
                 aria-expanded="${derivOpen}" aria-controls="tc-deriv"
                 aria-label="How these figures are derived">?</button>` : '';
        return `
        <div class="tc-panel-head">
            <p class="tc-eyebrow">${esc(st.label)} <span class="tc-state-chip tc-state-${esc(data.state)}">${esc(stateWord)}</span>
               ${laneChip}<span class="tc-head-ctl">${helpBtn}<button type="button" class="tc-unpin" data-tc-unpin aria-label="Close this station">✕</button></span></p>
            ${st.q ? `<p class="tc-q">${esc(st.q)}</p>` : ''}
            <h4 class="tc-headline">${esc(data.headline || st.sub)}</h4>
            ${facts ? `<div class="tc-rows">${facts}</div>` : ''}
        </div>
        ${derivOpen ? derivationPanel(data.derivation) : ''}
        ${buttons ? `<div class="tc-acts">${buttons}</div>` : ''}
        <p class="tc-hint" id="tc-act-status" role="status" aria-live="polite"></p>`;
    }

    // ── rendering states onto the ring ──────────────────────────────────────────
    /** Swap a station's glyph for a state-specific one, or restore the station default.
     *
     *  COLOUR encodes attention priority and nothing else — so two states that warrant the
     *  same attention (an unsigned agent and an unattested deployment are both "worth a
     *  look") are the same amber, correctly. That left them visually identical while being
     *  different acts. The second channel is the SHAPE, and for SIGNED the shape vocabulary
     *  already exists: the Trust Membrane seal rendered on profile cards, marketplace
     *  listings and the badge popover (`TrustBadge.META[state].icon`). Reusing it means the
     *  ring stops disagreeing with the rest of the product about what a signed agent looks
     *  like — and adds no fifth state and no invented vocabulary.
     *
     *  Deliberately SIGNED-only. The other seven stations have no external vocabulary to
     *  inherit; giving them bespoke per-state icons would be inventing exactly the thing
     *  this grammar exists to avoid. See TRUST_STATUS_COMMUNICATION.md.
     *
     *  `glyph` arrives as a complete `<svg>` string; the station's `<g>` already carries
     *  the stroke attributes, so only the children transfer.
     */
    function _applyGlyph(g, key, glyph) {
        const host = g.querySelector('.tc-node-icon');
        if (!host) return;
        let want = glyph || null;
        // TC-95: this is the pure renderer's ONE innerHTML sink for caller-supplied markup.
        // The only legitimate producer is the borrowed TrustBadge.META seal (Rule 8.4), so
        // WHITELIST to exactly those icons — an unrecognised string falls back to the
        // station's own icon rather than being injected verbatim. Absence-tolerant: without
        // TrustBadge there is no legitimate producer, so nothing foreign is accepted.
        if (want) {
            const M = (typeof window !== 'undefined' && window.TrustBadge && window.TrustBadge.META) || null;
            const allowed = M && Object.values(M).some((m) => m && m.icon === want);
            if (!allowed) want = null;
        }
        if (host.dataset.tcGlyph === (want || '')) return;   // idempotent — no repaint churn
        host.dataset.tcGlyph = want || '';
        if (!want) { host.innerHTML = ICONS[key] || ''; return; }
        try {
            const tmp = document.createElement('div');
            tmp.innerHTML = want;
            const src = tmp.querySelector('svg');
            host.innerHTML = src ? src.innerHTML : (ICONS[key] || '');
        } catch (e) { host.innerHTML = ICONS[key] || ''; }
    }

    function applyModel() {
        if (!_host || !_model) return;
        const byKey = {};
        (_model.stations || []).forEach((s) => { byKey[s.key] = s; });
        STATIONS.forEach((st) => {
            const g = _host.querySelector(`[data-tc-station="${st.key}"]`);
            if (!g) return;
            const d = byKey[st.key] || { state: 'na' };
            g.setAttribute('data-tc-state', d.state || 'na');
            const v = g.querySelector('.tc-value');
            if (v) v.textContent = d.value != null ? String(d.value) : '';
            // ONE mark, ONE meaning — a single state-coloured dot = a mechanism ACTED in
            // the window; the accent dot further out = an act is OWED. Colour alone
            // separates the jobs (no pairs, no count channel); both are the model's
            // facts, never the renderer's inference.
            g.querySelectorAll('.tc-acted-dot').forEach((t) => {
                t.style.display = d.acted ? '' : 'none';
            });
            g.querySelectorAll('.tc-owed-dot').forEach((t) => {
                t.style.display = d.owed ? '' : 'none';
            });
            _applyGlyph(g, st.key, d.glyph);
        });
        const c = _model.center || {};
        const cl = _host.querySelector('.tc-center-label');
        const cs = _host.querySelector('.tc-center-sub');
        if (cl) {
            // One line below the star (the prototype's core-l) — the stacked tspans
            // belonged to the era when the label lived inside the star's body.
            cl.textContent = c.kind === 'estate'
                ? (c.label || 'Agent estate').toUpperCase()
                : (c.label || '');
        }
        if (cs) cs.textContent = c.sub || '';
        // The Anatomy star visual: class glow + hairline class stroke + tip dots
        // when one agent is in focus; the estate keeps the plain steel star.
        const ccolor = c.color || null;
        const glowEl = _host.querySelector('.tc-class-glow');
        if (glowEl) {
            glowEl.style.display = ccolor ? '' : 'none';
            if (ccolor) { glowEl.setAttribute('fill', ccolor); glowEl.setAttribute('opacity', '0.10'); }
        }
        const starEl = _host.querySelector('.tc-center .tc-star');
        if (starEl) {
            if (ccolor) {
                starEl.setAttribute('stroke', ccolor);
                starEl.setAttribute('stroke-width', '0.45');  // path units — ~1.8px at scale 3.9
                starEl.setAttribute('stroke-opacity', '0.9');
                starEl.setAttribute('stroke-linejoin', 'round');
            } else {
                starEl.removeAttribute('stroke');
                starEl.removeAttribute('stroke-width');
            }
        }
        const dotsEl = _host.querySelector('.tc-tip-dots');
        if (dotsEl) {
            dotsEl.style.display = ccolor ? '' : 'none';
            if (ccolor) dotsEl.querySelectorAll('circle').forEach((d) => d.setAttribute('fill', ccolor));
        }
        if (cl) cl.style.fill = ccolor || '';
        const closeLbl = _host.querySelector('.tc-close-label');
        if (closeLbl) {
            const n = _model.closures && _model.closures.count;
            closeLbl.textContent = n ? `the loop closes · ${n}\u00d7` : 'the loop closes';
        }
        const hint = _host.querySelector('.tc-center-hint');
        const canPick = !!(_model.pickable && Object.keys(_model.pickable.items || {}).length > 1);
        if (hint) hint.style.display = canPick ? '' : 'none';
        // The plus rides with the hint — it PROMISES the centre can be changed, and a
        // mark must never promise something the surface cannot do (design README).
        _host.querySelectorAll('.tc-subject-plus').forEach((el) => {
            el.style.display = canPick ? '' : 'none';
        });
        // The trip lens control rides the stage — re-rendered with every model
        // (the mode, the leg boundary, or the subject may all have changed).
        _renderLens();
        // The readings themselves changed — repaint even if the same station is showing.
        _invalidatePanel();
        renderPanel();
    }

    /** Should a peek be swallowed to protect an open explainer?
     *
     * An OPEN explainer outranks a POINTER peek. Opening "?" is a deliberate request to
     * read one station's derivation in depth; a pointer crossing the ring on its way
     * somewhere else is not a request to replace it. Same rule the pin already states one
     * notch weaker — hover is a peek, the deeper read is the resting state.
     *
     * Pointer ONLY, deliberately. Keyboard focus IS the navigation: a Tab to the next
     * station is the user asking to be there, and swallowing the panel change would leave
     * a keyboard reader with no idea which station they had reached. Enter then pins it,
     * which clears the explainer through the normal path (see togglePin).
     *
     * Pure and fully parameterised so the gate can drive its truth table — this predicate
     * decides whether a reader's deeper read survives, which is not a thing to leave
     * inline and unmeasured.
     */
    function peekSuppressed(hoverKey, viaPointer, pinnedKey, derivKey) {
        return !!(viaPointer && hoverKey && pinnedKey
                  && derivKey === pinnedKey && hoverKey !== pinnedKey);
    }

    function renderPanel(hoverKey, viaPointer) {
        if (_story) return;   // story mode owns the panel until exited
        const panel = _host && _host.querySelector('.tc-panel');
        if (!panel) return;
        // Hover is a PEEK; the pin is the resting state. So a hovered station wins while
        // the pointer is on it, and `mouseleave` falls back to the pinned station rather
        // than to the narrative — otherwise peeking a neighbour would silently discard the
        // station you were working on.
        if (peekSuppressed(hoverKey, viaPointer, _pinned, _derivOpen)) return;
        const key = hoverKey || _pinned;
        // Nothing would change ⇒ leave the live DOM alone (see _renderedKey).
        const wantPinned = !!key && (key === _pinned) && !hoverKey;
        const wantDeriv = wantPinned && _derivOpen === key;
        if (_renderedKey === (key || null) && _renderedPinned === wantPinned
            && _renderedDeriv === wantDeriv) return;
        _renderedKey = key || null;
        _renderedPinned = wantPinned;
        _renderedDeriv = wantDeriv;
        if (!key) {
            panel.innerHTML = defaultPanel();
            wireLastEvent();
            const play = panel.querySelector('.tc-play');
            if (play) play.addEventListener('click', () => {
                if (_model && _model.story && _model.story.onPlay) _model.story.onPlay();
            });
            const anat = panel.querySelector('.tc-anatomy-link');
            if (anat) anat.addEventListener('click', () => {
                if (_model && _model.anatomy && _model.anatomy.onOpen) _model.anatomy.onOpen();
            });
            const aw = panel.querySelector('.tc-awareness-link');
            if (aw) aw.addEventListener('click', () => {
                if (_model && _model.awareness && _model.awareness.onOpen) _model.awareness.onOpen();
            });
            const est = panel.querySelector('.tc-estate-link');
            if (est) est.addEventListener('click', () => {
                if (_model && _model.ontology && _model.ontology.onOpen) _model.ontology.onOpen();
            });
            const leg = panel.querySelector('.tc-newleg');
            if (leg) leg.addEventListener('click', (ev) => {
                // stopPropagation — see the lens pills: the light-dismiss must not
                // swallow the confirm the click is about to open.
                ev.stopPropagation();
                _legConfirm(panel);
            });
            return;
        }
        const st = STATIONS.find((s) => s.key === key);
        const data = (_model.stations || []).find((s) => s.key === key) || { state: 'na' };
        // Actions render only for the PINNED station — a peek must not put a live
        // "sign this agent" button under a pointer that is merely passing through.
        const pinned = wantPinned;
        panel.innerHTML = stationPanel(st, data, pinned);
        if (!pinned) return;
        const closeBtn = panel.querySelector('[data-tc-unpin]');
        if (closeBtn) closeBtn.addEventListener('click', (ev) => {
            ev.preventDefault(); ev.stopPropagation(); unpin();
        });
        const helpBtn = panel.querySelector('[data-tc-deriv]');
        if (helpBtn) helpBtn.addEventListener('click', (ev) => {
            // stopPropagation: the document-level light-dismiss would otherwise treat this
            // as an outside click and unpin the very panel being explained.
            ev.preventDefault(); ev.stopPropagation();
            _derivOpen = (_derivOpen === key) ? null : key;
            renderPanel(hoverKey);
            // Keep focus on the control the user pressed — the panel's innerHTML was
            // replaced, so the original node is gone.
            const again = panel.querySelector('[data-tc-deriv]');
            if (again) again.focus();
        });
        const status = panel.querySelector('#tc-act-status');
        panel.querySelectorAll('[data-tc-act]').forEach((b) => b.addEventListener('click', () => {
            if (!_onNavigate) return;
            // One channel for navigation AND for acts: an action is just another action
            // string, so the host decides what it means and the ring stays a renderer.
            _onNavigate(b.dataset.tcAct, key, {
                setStatus: (msg) => { if (status) status.textContent = msg || ''; },
                done: () => { _invalidatePanel(); renderPanel(); },
            });
        }));
    }

    /** Pin/unpin a station. Only one is ever pinned — eight open panels is not a UI. */
    function togglePin(key) {
        if (_derivOpen && _derivOpen !== key) _derivOpen = null;   // never outlive its subject
        _pinned = (_pinned === key) ? null : key;
        renderPanel();
        _syncPinnedNodes();
    }

    /** Pin a station programmatically (idempotent set, not a toggle). Exists for the
     *  host's act-then-refresh flow: a successful trust act calls the full data reload,
     *  which re-mounts the ring and deliberately drops the pin — correct for a subject
     *  switch, but it also destroyed the very panel whose status line was reporting the
     *  act's outcome. The host re-pins the station after the reload and writes the
     *  outcome into the fresh panel. */
    function pin(key) {
        if (!_host) return;
        _pinned = key || null;
        _invalidatePanel();
        renderPanel();
        _syncPinnedNodes();
    }

    function _syncPinnedNodes() {
        if (!_host) return;
        _host.querySelectorAll('.tc-node').forEach((n) => {
            const on = n.dataset.tcStation === _pinned;
            n.classList.toggle('tc-node-pinned', on);
            n.setAttribute('aria-expanded', on ? 'true' : 'false');
        });
    }

    /** Dismiss the pin. Returns focus to the station that owned it — a non-modal popover
     *  must not drop a keyboard user at the top of the document when it closes.
     *
     *  The return-focus must NOT re-open that station's panel. A station's `focus` handler
     *  is its keyboard equivalent of hover (focus ⇒ peek), so focusing the owner here fired
     *  a peek that repainted the panel with the very station we had just dismissed — the
     *  close worked, then was instantly covered by something that looked identical, so it
     *  read as "the ✕ needs two clicks". Programmatic focus is not a user peek; the flag
     *  distinguishes the two, and the panel is drawn AFTER focus settles so the ordering
     *  cannot silently matter again.
     */
    function unpin() {
        if (!_pinned) return;
        const owner = _host && _host.querySelector(`.tc-node[data-tc-station="${_pinned}"]`);
        _pinned = null;
        _derivOpen = null;   // panel-scoped: the explainer dies with its panel
        _syncPinnedNodes();
        if (owner && typeof owner.focus === 'function') {
            _returningFocus = true;
            try { owner.focus(); } finally { _returningFocus = false; }
        }
        renderPanel();
    }

    // ── the trust-trip lens control (TRUST_TRIP_RESET.md) ───────────────────────
    /** A leg boundary as a short human date; ISO date part as the honest fallback. */
    function _legDate(iso) {
        if (!iso) return '';
        try {
            return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
        } catch (e) { return String(iso).slice(0, 10); }
    }

    /** Render the Whole record · This leg pill-pair + the leg chip onto the stage.
     *  Absent `model.lens` ⇒ nothing renders (absence-tolerant: an older host, or a
     *  deployment with no marker anywhere, keeps a byte-identical ring). The chip is
     *  W3's ambient disclosure: while the trip lens is active the boundary is visible
     *  ON the instrument, not only inside the panels. */
    function _renderLens() {
        const host = _host && _host.querySelector('.tc-lens-host');
        if (!host) return;
        const L = _model && _model.lens;
        if (!L) { host.innerHTML = ''; return; }
        const trip = L.mode === 'trip';
        const chip = !trip ? ''
            : L.noLeg
                ? '<span class="tc-leg-chip tc-leg-none">no reset yet — showing the whole record</span>'
                : `<span class="tc-leg-chip${L.note ? ' u-tip' : ''}"${L.note ? ` data-tip="${esc(L.note)}"` : ''}>this leg · since ${esc(_legDate(L.since) || 'recently')}${(L.leg_n || 1) > 1 ? ` · leg ${L.leg_n}` : ''}</span>`;
        host.innerHTML = `
            <div class="tc-lens" role="group" aria-label="Reading lens — the whole record or the current leg">
                <button type="button" class="tc-lens-pill${trip ? '' : ' on'}" data-tc-lens="total"
                    aria-pressed="${!trip}">Whole record</button>
                <button type="button" class="tc-lens-pill${trip ? ' on' : ''}" data-tc-lens="trip"
                    aria-pressed="${trip}">This leg</button>
            </div>${chip}`;
        host.querySelectorAll('[data-tc-lens]').forEach((b) => b.addEventListener('click', (ev) => {
            // stopPropagation: the document light-dismiss must not treat a lens
            // switch as an outside click and close a pinned station panel.
            ev.stopPropagation();
            if (L.onSwitch) L.onSwitch(b.dataset.tcLens);
        }));
    }

    /** The "start a new leg" inline confirm — two steps, never a native dialog.
     *  The note is optional but RECORDED (W2); the copy states the invariant that
     *  makes the act safe to offer at all: a reset deletes nothing. */
    function _legConfirm(panel) {
        if (!panel || panel.querySelector('#tc-leg-confirm')) return;
        const btn = panel.querySelector('.tc-newleg');
        if (!btn) return;
        const div = document.createElement('div');
        div.id = 'tc-leg-confirm';
        div.className = 'tc-leg-confirm';
        div.innerHTML = `
            <p class="tc-hint">A reset deletes nothing — every turn, verdict and incident stays
               on the whole record. It starts a new LEG the ring can read on its own, and the
               act itself is recorded in this agent's trust history.</p>
            <input type="text" class="tc-leg-note" maxlength="280"
                   placeholder="Why does a new leg begin? (optional — recorded with the marker)"/>
            <div class="tc-acts">
                <button type="button" class="tc-cta tc-act tc-act-primary tc-leg-go">Start the leg</button>
                <button type="button" class="tc-cta tc-act tc-leg-cancel" style="background:transparent;">Cancel</button>
            </div>`;
        btn.style.display = 'none';
        btn.insertAdjacentElement('afterend', div);
        const note = div.querySelector('.tc-leg-note');
        if (note) note.focus();
        div.addEventListener('click', (ev) => ev.stopPropagation());
        div.querySelector('.tc-leg-cancel').addEventListener('click', () => {
            div.remove(); btn.style.display = '';
        });
        div.querySelector('.tc-leg-go').addEventListener('click', async () => {
            const go = div.querySelector('.tc-leg-go');
            go.disabled = true; go.textContent = 'Starting…';
            const ok = (_model && _model.trip && _model.trip.onReset)
                ? await _model.trip.onReset(note ? note.value : '') : false;
            // Success re-mounts the ring from fresh facts — this panel is gone.
            // Only a FAILURE needs the button back.
            if (!ok && div.isConnected) { go.disabled = false; go.textContent = 'Start the leg'; }
        });
    }

    let _lastEventText = null;
    function wireLastEvent() {
        if (_lastEventText) {
            const el = _host && _host.querySelector('#tc-lastevent-text');
            if (el) el.textContent = _lastEventText;
        }
    }
    function noteEvent(text) {
        _lastEventText = `${text} · ${new Date().toLocaleTimeString()}`;
        wireLastEvent();
    }

    // ── pulses ──────────────────────────────────────────────────────────────────
    function flashStation(key) {
        const g = _host && _host.querySelector(`[data-tc-station="${key}"]`);
        if (!g) return;
        g.classList.remove('tc-flash');
        void g.getBoundingClientRect();   // restart the animation
        g.classList.add('tc-flash');
        setTimeout(() => g.classList.remove('tc-flash'), 1600);
    }

    function lapPulse() {
        if (!_host || !_railPath) return;
        const dot = _host.querySelector('.tc-pulse');
        if (!dot) return;
        if (reduceMotion()) {   // no travel — a quiet ripple on the acting stations
            ['grounded', 'guarded', 'scored', 'proven'].forEach(flashStation);
            return;
        }
        cancelAnimationFrame(_pulseRaf);
        const total = _railPath.getTotalLength();
        const t0 = performance.now();
        const DUR = 2400;
        dot.style.display = '';
        const stationAt = (frac) => STATIONS[Math.floor(frac * 8) % 8].key;
        let lastStation = null;
        const step = (now) => {
            const t = Math.min(1, (now - t0) / DUR);
            const eased = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
            const p = _railPath.getPointAtLength(eased * total);
            dot.setAttribute('cx', p.x);
            dot.setAttribute('cy', p.y);
            const sk = stationAt(eased);
            if (sk !== lastStation) { lastStation = sk; flashStation(sk); }
            if (t < 1) { _pulseRaf = requestAnimationFrame(step); }
            else { dot.style.display = 'none'; }
        };
        _pulseRaf = requestAnimationFrame(step);
    }

    function pulse(kind) {
        if (!_host) return;
        if (kind === 'turn') lapPulse();
        else flashStation(kind);
    }

    // ── mount / update ──────────────────────────────────────────────────────────
    function mount(opts) {
        stopStory();
        _host = opts.host;
        _model = opts.model;
        _onNavigate = opts.onNavigate || null;
        if (!_host) return;
        _host.classList.add('tc-root');
        // A re-render means a new subject (agent switch / scope change) — drop the pin so
        // an action button is never left over from the agent you were previously reading.
        _pinned = null;
        _derivOpen = null;   // a new subject drops any open explainer
        _host.innerHTML = `
            <div class="tc-stage">${buildSvg(_model)}<div class="tc-lens-host"></div></div>
            <div class="tc-panel"></div>`;
        _railPath = _host.querySelector('.tc-rail');

        // Station interactions: hover previews, click pins + panel CTA.
        _host.querySelectorAll('.tc-node').forEach((g) => {
            const key = g.dataset.tcStation;
            g.addEventListener('mouseenter', () => renderPanel(key, true));
            g.addEventListener('mouseleave', () => renderPanel());
            // focus ⇒ peek is the keyboard equivalent of hover — but only for a REAL focus
            // move. See unpin(): programmatic return-focus must not reopen what it closed.
            g.addEventListener('focus', () => { if (!_returningFocus) renderPanel(key); });
            g.addEventListener('blur', () => { if (!_returningFocus) renderPanel(); });
            const activate = () => togglePin(key);
            // stopPropagation so the document light-dismiss below does not immediately
            // close what this very click just opened.
            g.addEventListener('click', (ev) => { ev.stopPropagation(); activate(); });
            g.addEventListener('keydown', (ev) => {
                if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); activate(); }
                if (ev.key === 'Escape') unpin();
                // R17 — arrows walk the loop. The ledger claimed this on every
                // instrument; the shipped ring had only Enter/Escape (the R-3 route
                // gate found the gap). Wrap-around, because the loop closes.
                let step = 0;
                if (ev.key === 'ArrowRight' || ev.key === 'ArrowDown') step = 1;
                else if (ev.key === 'ArrowLeft' || ev.key === 'ArrowUp') step = -1;
                if (step) {
                    ev.preventDefault();
                    const i = STATIONS.findIndex((st) => st.key === g.getAttribute('data-tc-station'));
                    const next = STATIONS[(i + step + STATIONS.length) % STATIONS.length];
                    const el = _host && _host.querySelector(`[data-tc-station="${next.key}"]`);
                    if (el) el.focus();
                }
            });
        });

        // Five dismissal routes, none of them the only one — a pinned panel that can only
        // be closed one way is a trap, and a non-modal popover must never trap:
        //   1. Escape (document-wide)                     4. the ✕ control (touch + AT)
        //   2. clicking the same station again (toggle)    5. pinning another station
        //   3. clicking anywhere outside it (light dismiss)
        // Deliberately NOT on `mouseleave`: the pointer has to travel from the station to
        // the panel's buttons, and a pin that evaporates on the way is the classic
        // hover-menu defect. Nor on scroll, nor when an action completes — a long-running
        // sign-off must not yank the panel out from under its own status line.
        if (!_dismissWired) {
            document.addEventListener('keydown', (ev) => {
                // TC-80: this listener is document-wide and permanent. When the ring is not
                // visible (another view/section active), a stale pin made Escape run
                // `unpin()` → `owner.focus()` on a HIDDEN SVG node — yanking focus out of
                // whatever modal the user was actually in. Act only while the ring is
                // genuinely on screen.
                if (ev.key !== 'Escape') return;
                if (!_host || !_host.isConnected || !_host.offsetParent) return;
                unpin();
            });
            document.addEventListener('click', (ev) => {
                if (!_pinned || !_host) return;
                if (_host.contains(ev.target)) return;   // inside the ring or its panel
                // A click whose target was RE-RENDERED AWAY by its own handler (the
                // graded row's "open JUDGED" pins another station synchronously, so the
                // button it was on is gone by the time this listener runs) came from
                // inside: a detached target is never "outside". Without this the pin
                // landed and was dismissed in the same tick — the default panel, as if
                // nothing had happened.
                if (!(ev.target && ev.target.isConnected)) return;
                unpin();
            });
            _dismissWired = true;
        }

        // Center = the agent picker — EXACTLY the Anatomy star picker (one shared
        // code path: search + IFOC class pills + canonical chips + keyboard nav),
        // anchored over the ring's stage; "Whole estate" is the special row.
        const center = _host.querySelector('.tc-center');
        if (center) center.addEventListener('click', () => {
            const p = _model.pickable;
            if (!p || !window.AgentAnatomy || !window.AgentAnatomy.openAgentPicker) return;
            const stage = _host.querySelector('.tc-stage') || _host;
            window.AgentAnatomy.openAgentPicker({
                hostEl: stage,
                items: p.items || [],
                currentId: p.current || null,
                specialRow: { name: p.allLabel || 'Whole estate' },
                onPick: (id) => { if (p.onPick) p.onPick(id || ''); },
            });
        });

        applyModel();
    }

    function update(model) {
        if (!_host) return;
        _model = model;
        applyModel();
    }

    // ═══ The mini-circle glyph — the universal trust mark ══════════════════════
    // A compact radial distillation of the ring: N arc segments, one per
    // station/stage, in the shared trust-token grammar. Consumers (roster
    // cards, answer capsule, chat header) map their own data to states.
    function glyph(states, opts = {}) {
        const size = opts.size || 20;
        const list = (states && states.length) ? states : Array(8).fill('na');
        const n = list.length;
        const c = size / 2, r = c - 2.5, gapDeg = Math.min(18, 120 / n);
        const segs = list.map((st, i) => {
            const a0 = (-90 + (360 / n) * i + gapDeg / 2) * Math.PI / 180;
            const a1 = (-90 + (360 / n) * (i + 1) - gapDeg / 2) * Math.PI / 180;
            const large = (a1 - a0) > Math.PI ? 1 : 0;
            return `<path d="M ${(c + r * Math.cos(a0)).toFixed(2)} ${(c + r * Math.sin(a0)).toFixed(2)}` +
                   ` A ${r} ${r} 0 ${large} 1 ${(c + r * Math.cos(a1)).toFixed(2)} ${(c + r * Math.sin(a1)).toFixed(2)}"` +
                   ` class="tc-mini-seg tc-mini-${esc(st || 'na')}"/>`;
        }).join('');
        return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}"` +
               ` class="tc-mini${opts.cls ? ' ' + esc(opts.cls) : ''}" role="img"` +
               ` aria-label="${esc(opts.label || 'Trust circle')}">${segs}` +
               `<circle cx="${c}" cy="${c}" r="1.5" class="tc-mini-core"/></svg>`;
    }

    function glyphInto(hostOrId, states, opts = {}) {
        const el = typeof hostOrId === 'string' ? document.getElementById(hostOrId) : hostOrId;
        if (!el) return;
        el.innerHTML = glyph(states, opts);
        if (!reduceMotion()) {
            el.classList.remove('tc-mini-pulse');
            void el.getBoundingClientRect();
            el.classList.add('tc-mini-pulse');
        }
    }

    // ═══ Story mode — "the life of this agent", narrated around the ring ═══════
    // Chapters: [{ station, eyebrow, title, text, list?: [..] }] — every line is
    // backed by a real record (the caller assembles them from the story
    // endpoint + its own loaded model; nothing is invented here).
    let _story = null;

    function _storySpot(stationKey) {
        _host.querySelectorAll('.tc-node').forEach((g) => {
            const on = g.dataset.tcStation === stationKey;
            g.classList.toggle('tc-spot', on);
            g.classList.toggle('tc-dim', !on);
        });
        if (stationKey) flashStation(stationKey);
    }

    function stopStory() {
        if (!_story) return;
        clearTimeout(_story.timer);
        _story = null;
        if (_host) {
            _host.querySelectorAll('.tc-node').forEach((g) => g.classList.remove('tc-spot', 'tc-dim'));
            const svg = _host.querySelector('.tc-svg');
            if (svg) svg.classList.remove('tc-storying');
        }
        _invalidatePanel();   // story mode wrote the panel behind our back
        renderPanel();
    }

    function _showChapter(i) {
        if (!_story) return;
        // TC-81: the auto-advance timer survives a section/view switch, so chapters kept
        // firing (and writing the hidden panel) for up to ~56 s after navigating away. A
        // hidden ring means nobody is watching the story — stop it rather than let it
        // narrate to nothing.
        if (!_host || !_host.isConnected || !_host.offsetParent) { stopStory(); return; }
        const chapters = _story.chapters;
        if (i >= chapters.length) { stopStory(); return; }
        _story.i = i;
        clearTimeout(_story.timer);
        const ch = chapters[i];
        _storySpot(ch.station);
        const panel = _host.querySelector('.tc-panel');
        if (!panel) return;
        const dots = chapters.map((_, d) =>
            `<button type="button" class="tc-dot${d === i ? ' on' : ''}" data-tc-ch="${d}" aria-label="Chapter ${d + 1}"></button>`).join('');
        const listHtml = (ch.list && ch.list.length)
            ? `<ul class="tc-facts">${ch.list.map((l) => `<li>${esc(l)}</li>`).join('')}</ul>` : '';
        panel.innerHTML = `
            <div class="tc-panel-head tc-story-chapter">
                <p class="tc-eyebrow">${esc(ch.eyebrow || `Chapter ${i + 1}`)}</p>
                <h4 class="tc-headline">${esc(ch.title)}</h4>
                <p class="tc-copy">${esc(ch.text)}</p>
                ${listHtml}
            </div>
            <div class="tc-story-nav">
                <div class="tc-dots">${dots}</div>
                <div class="tc-story-btns">
                    ${(!reduceMotion() && i < chapters.length - 1)
                        ? `<button type="button" class="tc-story-btn tc-story-pause" data-tc-nav="pause" aria-label="${_story.paused ? 'Resume' : 'Pause'} the story">${_story.paused ? '<svg width="9" height="9" viewBox="0 0 24 24" fill="currentColor"><path d="M7 4l13 8-13 8z"/></svg> Resume' : '<svg width="9" height="9" viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="4" width="5" height="16" rx="1"/><rect x="14" y="4" width="5" height="16" rx="1"/></svg> Pause'}</button>` : ''}
                    ${i > 0 ? '<button type="button" class="tc-story-btn" data-tc-nav="back">Back</button>' : ''}
                    <button type="button" class="tc-story-btn tc-story-next" data-tc-nav="next">${i === chapters.length - 1 ? 'Finish' : 'Next'}</button>
                    <button type="button" class="tc-story-btn" data-tc-nav="exit" aria-label="Exit the story">Exit</button>
                </div>
            </div>`;
        panel.querySelectorAll('[data-tc-nav]').forEach((b) => b.addEventListener('click', () => {
            const nav = b.dataset.tcNav;
            if (nav === 'exit') { stopStory(); return; }
            if (nav === 'pause') {
                if (!_story) return;
                _story.paused = !_story.paused;
                clearTimeout(_story.timer);
                if (!_story.paused && i < chapters.length - 1) {
                    _story.timer = setTimeout(() => _showChapter(i + 1), 7000);
                }
                b.innerHTML = _story.paused ? `<svg width="9" height="9" viewBox="0 0 24 24" fill="currentColor"><path d="M7 4l13 8-13 8z"/></svg> Resume` : `<svg width="9" height="9" viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="4" width="5" height="16" rx="1"/><rect x="14" y="4" width="5" height="16" rx="1"/></svg> Pause`;
                b.setAttribute('aria-label', (_story.paused ? 'Resume' : 'Pause') + ' the story');
                return;
            }
            if (nav === 'back') _showChapter(Math.max(0, i - 1));
            else _showChapter(i + 1);
        }));
        panel.querySelectorAll('[data-tc-ch]').forEach((b) => b.addEventListener('click',
            () => _showChapter(parseInt(b.dataset.tcCh, 10))));
        // Auto-advance (motion allowed only, not while paused); manual
        // navigation re-arms it unless the reader paused deliberately.
        if (!reduceMotion() && !_story.paused && i < chapters.length - 1) {
            _story.timer = setTimeout(() => _showChapter(i + 1), 7000);
        }
    }

    function playStory(chapters) {
        if (!_host || !chapters || !chapters.length) return;
        stopStory();
        _story = { chapters, i: 0, timer: 0, paused: false };
        const svg = _host.querySelector('.tc-svg');
        if (svg) svg.classList.add('tc-storying');
        _showChapter(0);
    }

    window.TrustCircle = { mount, update, pulse, pin, noteEvent, glyph, glyphInto,
                       playStory, stopStory, stateWord, attentionWord,
                       // The registry, exported (W5) so no consumer keeps a second copy.
                       STATIONS };
})();
