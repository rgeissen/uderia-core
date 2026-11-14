/**
 * The Agent Anatomy — configuration as an organism.
 *
 * The third altitude of the agent-as-star system (Ontology = the estate,
 * Trust Circle = time, Anatomy = composition): the anatomy-of-trust page's
 * star rendered INSIDE the product, on the Anatomy view, as a live
 * navigable map of what an agent is made of.
 *
 * Two modes, one renderer:
 *   atlas — no agent selected: the capability atlas; every element is a door
 *           into the surface that configures it.
 *   agent — a profile selected: every faculty annotated with the profile's
 *           REAL configuration; click lands in the profile editor at exactly
 *           that section.
 *
 * Honesty grammar (binding, docs/roadmap/AGENT_ANATOMY_PANEL.md §0/§9):
 *   configured / platform-default / off — quiet states, never judgmental;
 *   n/a-by-class — structurally absent for this IFOC class (no dot at all);
 *   trust tokens ONLY on the two foundations (there the ladder IS a posture);
 *   no composite score, ever.
 *
 * Split: RENDERER (pure — window.AgentAnatomy.mount/update, fetches nothing)
 * + CONTROLLER (assembles the model from existing endpoints, all defensive,
 * and owns the staleness contract: the model rebuilds when the profile
 * editor closes).
 */
(function () {
    'use strict';

    // ════════════════════════════════════════════════════════════════════════
    //  Shared vocabulary
    // ════════════════════════════════════════════════════════════════════════

    const STAR_PATH = 'M0,-11 L2.7,-3.72 L10.46,-3.4 L4.37,1.42 L6.47,8.9 L0,4.6 L-6.47,8.9 L-4.37,1.42 L-10.46,-3.4 L-2.7,-3.72 Z';

    const IFOC = {
        llm_only: { label: 'Ideate', color: '#4ade80' },
        rag_focused: { label: 'Focus', color: '#3b82f6' },
        tool_enabled: { label: 'Optimize', color: '#9333ea' },
        genie: { label: 'Coordinate', color: '#F15F22' },
    };
    const cls = (t) => IFOC[t] || { label: t || 'Agent', color: '#94a3b8' };

    // The platform's agent-class pill — the same badge the profile editor's header and the
    // profile cards wear (configurationHandler.js `ifocTagConfig`: label, colour, a 15%
    // tint, a 1px border in the colour). A class is SHOWN, never spelt as its internal
    // type string (`llm_only`) — the IFOC naming rule.
    function _ifocBadge(type) {
        const c = cls(type);
        const hex = /^#([0-9a-f]{6})$/i.exec(c.color || '');
        const tint = hex
            ? `rgba(${parseInt(hex[1].slice(0, 2), 16)}, ${parseInt(hex[1].slice(2, 4), 16)}, ${parseInt(hex[1].slice(4, 6), 16)}, 0.15)`
            : 'transparent';
        return `<span class="an-ifoc-badge px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wide" `
             + `style="color:${esc(c.color)};background:${tint};border:1px solid ${esc(c.color)};display:inline-block;line-height:1.4;">`
             + `${esc(c.label)}</span>`;
    }

    function esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // Stage geometry (viewBox 1000 × 720; star centered, faculties at its tips)
    const C = { x: 500, y: 345 }, SCALE = 13;
    const TIP = {
        memory: { x: C.x, y: C.y - 11 * SCALE },
        session: { x: C.x + 10.46 * SCALE, y: C.y - 3.4 * SCALE },
        intelligence: { x: C.x + 6.47 * SCALE, y: C.y + 8.9 * SCALE },
        components: { x: C.x - 6.47 * SCALE, y: C.y + 8.9 * SCALE },
        configuration: { x: C.x - 10.46 * SCALE, y: C.y - 3.4 * SCALE },
    };
    // Label anchor per faculty: [line end x/y, text x, anchor, metaphor y]
    const LBL = {
        memory: { lx: 500, ly: 158, tx: 500, anchor: 'middle', ty: 120 },
        configuration: { lx: 306, ly: 278, tx: 296, anchor: 'end', ty: 262 },
        session: { lx: 660, ly: 278, tx: 670, anchor: 'start', ty: 262 },
        components: { lx: 344, ly: 512, tx: 356, anchor: 'end', ty: 534 },
        intelligence: { lx: 656, ly: 512, tx: 644, anchor: 'start', ty: 534 },
    };
    const FACULTY_ORDER = ['memory', 'configuration', 'session', 'components', 'intelligence'];
    // The universe of agents behind the map — deterministic positions, numerically
    // filtered against every plate, the constellation, the subject and the membrane
    // (test/scratch script; a field that reshuffles per render cannot be reviewed,
    // and a collision cleared today would return tomorrow).
    const STARFIELD = [[361,62,0.73],[606,56,0.81],[225,110,0.49],[317,100,0.49],[358,122,0.7],[658,108,0.76],[719,103,0.78],[788,101,0.57],[184,160,0.76],[248,150,0.57],[282,174,0.78],[669,160,0.51],[733,153,0.89],[670,209,0.65],[918,221,0.62],[177,376,0.57],[222,383,0.59],[302,396,0.62],[753,394,0.84],[783,368,0.49],[867,388,0.51],[908,365,0.51],[170,427,0.46],[689,420,0.68],[739,416,0.62],[797,423,0.68],[872,429,0.76],[179,476,0.57],[228,495,0.49],[317,497,0.84],[610,501,0.62],[811,488,0.81],[497,555,0.57],[497,594,0.81]];

    // ════════════════════════════════════════════════════════════════════════
    //  RENDERER — pure; consumes a prepared model
    // ════════════════════════════════════════════════════════════════════════

    let _host = null, _model = null, _onNavigate = null;

    // Faculty + foundation glyphs — monochrome industrial stroke icons in the
    // BLUEPRINT channel (never a category colour): texture without a second paint.
    // `grounding`/`guardrails` are trustCircle.js's `grounded`/`guarded` VERBATIM —
    // one concept, one icon, across surfaces (Rule 8.4: borrow, never invent twice).
    const FAC_ICONS = {
        memory:        '<path d="M5 4h11a3 3 0 0 1 3 3v13l-4-3H8a3 3 0 0 1-3-3z"/><line x1="9" y1="9" x2="15" y2="9"/><line x1="9" y1="13" x2="13" y2="13"/>',
        configuration: '<path d="M8 3c4 3.5 4 6.5 0 10s-4 6.5 0 10"/><path d="M16 3c-4 3.5-4 6.5 0 10s4 6.5 0 10"/><line x1="7" y1="7.6" x2="17" y2="7.6"/><line x1="7" y1="16.4" x2="17" y2="16.4"/><line x1="9.2" y1="12" x2="14.8" y2="12"/>',
        session:       '<path d="M2.5 12s3.5-6.5 9.5-6.5S21.5 12 21.5 12s-3.5 6.5-9.5 6.5S2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>',
        components:    '<rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><path d="M17 14v6M14 17h6"/>',
        intelligence:  '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M4 20.5V5.5"/><line x1="8" y1="7.5" x2="16" y2="7.5"/><line x1="8" y1="11" x2="14" y2="11"/>',
        grounding:     '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/><line x1="12" y1="1.5" x2="12" y2="5"/><line x1="12" y1="19" x2="12" y2="22.5"/><line x1="1.5" y1="12" x2="5" y2="12"/><line x1="19" y1="12" x2="22.5" y2="12"/>',
        guardrails:    '<path d="M12 2l8 4v6c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V6l8-4z"/>',
    };

    function _facultyBlock(key, f) {
        const t = TIP[key], l = LBL[key];
        // I37: category colour is retired — the metaphor line is BLUEPRINT (the map
        // layer), category travels in TYPE. The orange birth-mark on Configuration
        // said "where agents are born" in a channel reserved for what you can act on.
        const metaCls = 'an-metaphor';
        // Endpoint marker per state — the ring's marker idiom (a seated box with a
        // monochrome stroke icon) instead of a bare 5px dot; the map was reading
        // sterile with text alone. State lives in the BLUEPRINT channel (weight +
        // dash, never a trust colour); n/a-by-class keeps NO marker (structural
        // absence stays structural).
        let dot = '';
        if (f.state !== 'na-class') {
            const mCls = { configured: 'an-fac-on', default: 'an-fac-quiet', off: 'an-fac-off' }[f.state] || 'an-fac-quiet';
            dot = `<g class="an-fac-mark ${mCls}">
                <rect class="an-fac-box" x="${t.x - 14}" y="${t.y - 14}" width="28" height="28" rx="8"/>
                <g class="an-fac-icon" transform="translate(${t.x - 8.4}, ${t.y - 8.4}) scale(0.7)"
                   fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
                    ${FAC_ICONS[key] || ''}</g>
            </g>`;
        }
        const dy = l.anchor === 'middle' ? 20 : 20;
        const vshort = f.value && f.value.length > 38 ? f.value.slice(0, 36) + '…' : f.value;
        const valueLine = vshort
            ? `<text class="an-value" x="${l.tx}" y="${l.ty + dy + 20}" text-anchor="${l.anchor}">${esc(vshort)}</text>` : '';
        const naLine = f.state === 'na-class' && f.naNote
            ? `<text class="an-value an-na" x="${l.tx}" y="${l.ty + dy + 20}" text-anchor="${l.anchor}">${esc(f.naNote)}</text>` : '';
        return `
        <g class="an-node" data-an-key="${key}" role="button" tabindex="0" aria-label="${esc(f.concept)}">
            <line class="an-leader" x1="${t.x}" y1="${t.y}" x2="${l.lx}" y2="${l.ly}"/>
            ${dot}
            <text class="${metaCls}" x="${l.tx}" y="${l.ty}" text-anchor="${l.anchor}">${esc(f.metaphor).toUpperCase()}</text>
            <text class="an-concept" x="${l.tx}" y="${l.ty + dy}" text-anchor="${l.anchor}">${esc(f.concept)}</text>
            ${valueLine}${naLine}
            <rect class="an-hit" x="${l.anchor === 'end' ? l.tx - 250 : (l.anchor === 'middle' ? l.tx - 125 : l.tx)}" y="${l.ty - 18}" width="250" height="${f.value || f.naNote ? 64 : 44}" rx="8"/>
        </g>`;
    }

    function _foundationBlock(key, f, x) {
        const st = f.state || 'na';   // trust tokens: pass | warn | danger | na
        return `
        <g class="an-node an-found" data-an-key="${key}" role="button" tabindex="0" aria-label="${esc(f.concept)}">
            <text class="an-found-sub" x="${x}" y="602" text-anchor="middle">${esc(f.metaphor)}</text>
            <rect class="an-found-box an-tok-${st}" x="${x - 110}" y="614" width="220" height="52" rx="9"/>
            <g class="an-found-icon an-ftok-${st}" transform="translate(${x - 98}, 633) scale(0.58)"
               fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${FAC_ICONS[key] || ''}</g>
            <text class="an-found-label" x="${x + 7}" y="${f.value ? 638 : 645}" text-anchor="middle">${esc(f.concept).toUpperCase()}</text>
            ${f.value ? `<text class="an-value" x="${x + 7}" y="${656}" text-anchor="middle">${esc(f.value)}</text>` : ''}
        </g>`;
    }

    function _sealGlyph(seal) {
        if (!seal) return '';
        // Rendered as a clickable group (wired in mount): the seal opens the
        // Trust Membrane detail / sign-off for this agent.
        const color = seal === 'trusted' ? 'var(--trust-seal-trusted, #34d399)'
            : seal === 'changed' ? 'var(--trust-seal-changed, #f59e0b)'
            : 'var(--trust-seal-unsigned, #94a3b8)';
        const word = seal === 'trusted' ? 'signed & intact' : seal === 'changed' ? 'changed since signing' : 'unsigned';
        // Its own quiet line under the membrane label — never colliding with it.
        return `<g class="an-seal" role="button" tabindex="0" aria-label="Trust Membrane: ${esc(word)} — open the sign-off detail">
            <circle cx="922" cy="60" r="5.5" fill="none" stroke="${color}" stroke-width="1.6"${seal === 'unsigned' ? ' stroke-dasharray="2.6 2.2"' : ''}/>
            ${seal === 'trusted' ? `<path d="M919.4 60l1.8 1.8 3.4-3.4" fill="none" stroke="${color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>` : ''}
            <text class="an-seal-word" x="910" y="64" text-anchor="end" style="fill:${color}">${esc(word)}</text></g>`;
    }

    function _constellation(model) {
        // Nine slots, each cleared NUMERICALLY against every label plate, the frame
        // and the starfield (test/test_agent_anatomy_routes.mjs re-runs the filter
        // from these literals): an expert's star AND its tag beneath it must never
        // sit under a faculty label — slot #2 once landed its tag inside the
        // Configuration plate, #6 under Awareness, #8/#9 under the two lower arms,
        // so a coordinator with 3+ experts buried a star behind a title.
        const pts = [[118, 150, 1.0], [196, 200, 0.68], [102, 346, 0.85], [142, 476, 0.6],
                     [880, 170, 0.9], [906, 405, 0.62], [872, 476, 0.8], [172, 626, 0.7], [872, 626, 0.75]];
        const lines = `<g class="an-const-lines"><polyline points="118,150 196,200 102,346 142,476"/>
            <polyline points="880,170 906,405 872,476"/><polyline points="102,346 172,626"/></g>`;
        const kids = model.constellation && model.constellation.children;
        let stars, motto = 'a star made of stars';
        if (kids && kids.length) {
            // A coordinator's REAL experts — each its own star, class-tinted,
            // labeled, click-to-pivot. Up to 9 positions; overflow stated honestly.
            stars = kids.slice(0, 9).map((ch, i) => {
                const [x, y, s] = pts[i];
                return `<g class="an-child" data-an-child="${esc(ch.key)}" role="button" tabindex="0"
                           aria-label="Pivot to @${esc(ch.tag)}">
                    <path d="${STAR_PATH}" transform="translate(${x},${y}) scale(${Math.max(s, 0.9) * 1.5})"
                          fill="${esc(ch.color)}" fill-opacity="0.85" class="an-child-star"/>
                    <text class="an-child-tag" x="${x}" y="${y + 26}" text-anchor="middle">@${esc(ch.tag)}</text>
                </g>`;
            }).join('');
            if (model.constellation.overflow > 0) {
                stars += `<text class="an-const-sub" x="74" y="98" text-anchor="start">+${model.constellation.overflow} more expert(s)</text>`;
            }
            motto = 'its experts — click one to pivot';
        } else {
            stars = pts.map(([x, y, s]) =>
                `<path d="${STAR_PATH}" transform="translate(${x},${y}) scale(${s})" class="an-const-star"/>`).join('');
        }
        return `${lines}<g>${stars}</g>
            <text class="an-membrane" x="74" y="62" text-anchor="start">COORDINATION · GENIE</text>
            <text class="an-const-sub" x="74" y="80" text-anchor="start">${esc(motto)}</text>`;
    }

    function _centerBlock(model) {
        const c = model.center || {};
        const hint = model.pickable
            ? `<text class="an-center-hint" x="${C.x}" y="${C.y + 135}" text-anchor="middle">change &#9662;</text>` : '';
        const core = model.mode === 'agent'
            ? `<text class="an-core" x="${C.x}" y="${C.y - 6}" text-anchor="middle" style="${c.color ? `fill:${esc(c.color)};` : ''}">${esc(c.label)}</text>
               <text class="an-core-sub" x="${C.x}" y="${C.y + 18}" text-anchor="middle">${esc(c.sub || '')}</text>`
            : `<text class="an-core" x="${C.x}" y="${C.y - (c.count != null ? 22 : 16)}" text-anchor="middle">AGENT</text>
               <text class="an-core" x="${C.x}" y="${C.y + (c.count != null ? 1 : 7)}" text-anchor="middle">IDENTITY</text>
               ${c.count != null ? `<text class="an-core-sub" x="${C.x}" y="${C.y + 24}" text-anchor="middle">${esc(c.count)} agent${c.count === 1 ? '' : 's'}</text>` : ''}`;
        // The plus means "you can change it" — the same mark the Trust Circle carries
        // and the Live Status dial deliberately omits. Rendered only when picking is
        // actually possible: a mark must never promise what the surface cannot do.
        const py = model.mode === 'agent' ? C.y - 38 : C.y - 52;
        const plus = model.pickable
            ? `<path class="an-subject-plus" d="M ${C.x - 8} ${py} H ${C.x + 8}"/>
               <path class="an-subject-plus" d="M ${C.x} ${py - 8} V ${py + 8}"/>` : '';
        // The star IS the selector: one generous, quiet hit target at the center.
        return `<g class="an-center" role="button" tabindex="0" aria-haspopup="listbox" aria-label="Choose the agent in focus">
            <circle class="an-center-hit" cx="${C.x}" cy="${C.y}" r="98"/>
            ${plus}${core}${hint}</g>`;
    }

    function buildSvg(model) {
        const byKey = {};
        (model.faculties || []).forEach((f) => { byKey[f.key] = f; });
        const faculties = FACULTY_ORDER.map((k) => _facultyBlock(k, byKey[k] || { metaphor: k, concept: k, state: 'default' })).join('');
        const fnd = {};
        (model.foundations || []).forEach((f) => { fnd[f.key] = f; });
        // Class identity: a soft glow behind the star + a hairline stroke.
        // NB the star path is scaled ×13 — stroke-width is in PATH units
        // (0.14 ≈ 1.8px on screen); a naive pixel width renders as a blob.
        const tint = model.mode === 'agent' && model.center && model.center.color
            ? esc(model.center.color) : null;
        // Class identity overrides the blueprint stroke; path units (scaled ×13).
        const starStroke = tint ? ` stroke="${tint}" stroke-width="0.14" stroke-opacity="0.85"` : '';
        const classGlow = tint
            ? `<circle cx="${C.x}" cy="${C.y - 10}" r="150" fill="${tint}" opacity="0.09" filter="url(#anBlur)"/>` : '';
        return `
        <svg viewBox="0 0 1000 720" class="an-svg" role="img"
             aria-label="The Uderia agent anatomy — a five-pointed star of faculties inside the trust membrane, seated on the grounding and guardrail foundations.">
            <defs>
                <filter id="anBlur" x="-60%" y="-60%" width="220%" height="220%">
                    <feGaussianBlur stdDeviation="34"/>
                </filter>
            </defs>
            ${STARFIELD.map(([x, y, s]) =>
                `<path d="${STAR_PATH}" transform="translate(${x},${y}) scale(${s})" class="an-backdrop-star"/>`)
                .join('')}
            <rect class="an-membrane-rect" x="56" y="36" width="888" height="628" rx="18"/>
            <text class="an-membrane" x="944" y="26" text-anchor="end">TRUST&#8202;&#8202;MEMBRANE</text>
            ${_sealGlyph(model.membrane && model.membrane.seal)}
            ${model.membrane && model.membrane.authority
                ? `<text class="an-membrane" x="928" y="652" text-anchor="end">BOUNDED AGENT PRINCIPAL</text>` : ''}
            ${_constellation(model)}
            ${classGlow}
            <!-- The subject, schematic (prototype Change 3): faint blueprint fill +
                 hairline stroke, never the steel-gradient badge — the map layer must
                 not out-render the readings. Agent mode tints the stroke with the
                 class colour through the same hook the gradient star used. -->
            <path d="${STAR_PATH}" transform="translate(${C.x},${C.y}) scale(${SCALE})" class="an-star-i"/>
            <path d="${STAR_PATH}" transform="translate(${C.x},${C.y}) scale(${SCALE})"${starStroke} class="an-star an-star-f"/>
            ${_centerBlock(model)}
            ${faculties}
            ${_foundationBlock('grounding', fnd.grounding || { metaphor: 'truth · faithfulness', concept: 'Grounding' }, 360)}
            ${_foundationBlock('guardrails', fnd.guardrails || { metaphor: 'rules · safety', concept: 'Guardrails' }, 640)}
        </svg>`;
    }

    // I37 — the CATEGORY legend is gone: it spent green and orange on "what kind of
    // label this is", and its green sat a hair from --trust-pass, so a map read as a
    // health reading. This legend explains what colour MEANS instead — the ring's
    // three-channel rule, stated once on the surface that had violated it.
    function _legend() {
        return `<div class="an-legend">
            <span><span class="an-leg-swatch" style="background:var(--an-map,#8ab4e8)"></span><b>blueprint</b> — the map layer: star, spokes, labels. It never reports state.</span>
            <span><span class="an-leg-swatch" style="background:var(--trust-warn,#fbbf24)"></span><b>trust</b> — reserved for the two foundations, the only parts with a posture.</span>
            <span><span class="an-leg-swatch" style="background:var(--teradata-orange,#F15F22)"></span><b>accent</b> — what you can act on: hover, focus, the plus.</span>
        </div>`;
    }

    function _defaultPanel() {
        if (_model.mode === 'agent') {
            const c = _model.center || {};
            const back = _model.backlink
                ? `<button type="button" class="an-back" data-an-link="back">&larr; back to ${esc(_model.backlink.label)}</button>` : '';
            const links = _model.links ? `<div class="an-links an-links--pills">
                <button type="button" class="tc-cta" data-an-link="circle"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 12a9.5 9.5 0 1 1-3.1-7"/><path d="M21.5 2.5V7H17"/><circle cx="12" cy="12" r="2.4"/></svg> Open Trust</button>
                <button type="button" class="tc-cta" data-an-link="awareness"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/></svg> Open Awareness</button>
                <button type="button" class="tc-cta" data-an-link="estate"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg> Open Ontology</button>
            </div>` : '';
            return `${back}<div class="an-panel-head">
                <p class="an-eyebrow" style="color:${esc(c.color || 'var(--teradata-orange)')}">${esc(c.label)} · ${esc(c.sub || '')}</p>
                <h4 class="an-headline">The living anatomy of this agent.</h4>
                ${_model.summary ? `<p class="an-desc">${esc(_model.summary)}</p>` : ''}
                <p class="an-copy">Every faculty shows its real configuration — quiet on platform defaults,
                   dashed where a capability is off, absent where the class has no such organ.
                   Hover a faculty to read it; click to open the editor at exactly that place.</p></div>
                ${_legend()}
                ${links}
                <a class="an-story-link" href="https://trust.uderia.com/agent-anatomy-of-trust.html" target="_blank" rel="noopener" style="margin-top:14px;">Read the anatomy of trust
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 17L17 7M9 7h8v8"/></svg></a>`;
        }
        return `<div class="an-panel-head">
            <p class="an-eyebrow">The capability atlas</p>
            <h4 class="an-headline">The whole platform, laid out as one agent.</h4>
            <p class="an-copy">Five faculties and two foundations, inside the trust membrane — every part
               a real door to the surface that configures it.</p>
            <p class="an-copy">Hover to learn; click to open. Pick an agent at the star's center to see
               <em>its</em> living anatomy.</p></div>
            ${_legend()}
            <a class="an-story-link" href="https://trust.uderia.com/agent-anatomy-of-trust.html" target="_blank" rel="noopener" style="margin-top:14px;">Read the anatomy of trust
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 17L17 7M9 7h8v8"/></svg></a>`;
    }

    function _nodePanel(key) {
        if (String(key).startsWith('child:')) {
            const id = String(key).slice(6);
            const ch = ((_model.constellation || {}).children || []).find((x) => x.key === id);
            if (!ch) return _defaultPanel();
            return `<div class="an-panel-head">
                <p class="an-eyebrow" style="color:${esc(ch.color)}">@${esc(ch.tag)} · ${esc(ch.clsLabel || 'expert')}</p>
                <h4 class="an-headline">One of the coordinator's experts.</h4>
                <p class="an-copy">A coordinator is a star made of stars — each expert is a full agent with its
                   own anatomy.</p>
                <p class="an-hint">Click — pivot the anatomy to @${esc(ch.tag)}.</p></div>`;
        }
        const all = [...(_model.faculties || []), ...(_model.foundations || [])];
        const f = all.find((x) => x.key === key);
        if (!f || !f.panel) return _defaultPanel();
        const stateWord = { configured: 'configured', default: 'platform default', off: 'off',
                            'na-class': 'not part of this class', pass: 'active', warn: 'worth a look',
                            watching: 'watching', danger: 'needs attention', na: 'not enabled' }[f.state] || '';
        const all2 = f.panel.facts || [];
        // A fact's value is text, escaped — unless the fact says `html: true`, in which case it
        // is markup the model built from escaped parts (the agent-class badge).
        const specs = all2.filter((x) => x && typeof x === 'object')
            .map((x) => `<div class="an-spec-k">${esc(x.k)}</div><div class="an-spec-v">${x.html ? x.v : esc(x.v)}</div>`).join('');
        const prose = all2.filter((x) => typeof x === 'string')
            .map((x) => `<p class="an-copy" style="margin-bottom:6px;">${esc(x)}</p>`).join('');
        return `<div class="an-panel-head">
            <p class="an-eyebrow">${esc(f.concept)}${stateWord ? ` <span class="an-state-chip an-chip-${esc(f.state)}">${esc(stateWord)}</span>` : ''}</p>
            <h4 class="an-headline">${esc(f.panel.headline || f.metaphor)}</h4>
            ${specs ? `<div class="an-spec">${specs}</div>` : ''}
            ${prose}
            ${f.nav ? `<p class="an-panel-action"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>${esc(f.nav.label)}</p>` : ''}
        </div>`;
    }

    function _renderPanel(hoverKey) {
        const panel = _host && _host.querySelector('.an-panel');
        if (!panel) return;
        panel.innerHTML = hoverKey ? _nodePanel(hoverKey) : _defaultPanel();
    }

    function mount(opts) {
        _closeStarPicker();
        _host = opts.host;
        _model = opts.model;
        _onNavigate = opts.onNavigate || null;
        if (!_host) return;
        _host.classList.add('an-root');
        // The Trust panel's exact section-card chrome: icon + title header, the
        // stage + editorial panel as the card body.
        _host.innerHTML = `<div class="perf-panel-card">
            <div class="flex items-center justify-between mb-3">
                <h3 class="font-semibold flex items-center gap-2" style="color: var(--text-primary);">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" style="color: var(--teradata-orange);" fill="none" viewBox="-13 -13 26 26" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"><path d="${STAR_PATH}" /></svg>
                    Agent Anatomy
                </h3>
            </div>
            <div class="an-card-body">
                <div class="an-stage-col">
                    <div class="an-stage">${buildSvg(_model)}</div>
                </div>
                <div class="an-panel"></div>
            </div>
        </div>`;

        _host.querySelectorAll('.an-node').forEach((g) => {
            const key = g.dataset.anKey;
            g.addEventListener('mouseenter', () => _renderPanel(key));
            g.addEventListener('mouseleave', () => _renderPanel());
            g.addEventListener('focus', () => _renderPanel(key));
            g.addEventListener('blur', () => _renderPanel());
            const go = () => {
                const all = [...(_model.faculties || []), ...(_model.foundations || [])];
                const f = all.find((x) => x.key === key);
                if (f && f.nav && _onNavigate) _onNavigate(f.nav.action, key);
            };
            g.addEventListener('click', go);
            g.addEventListener('keydown', (ev) => {
                if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); go(); }
            });
        });

        _host.querySelectorAll('.an-child').forEach((g) => {
            const id = g.dataset.anChild;
            g.addEventListener('mouseenter', () => _renderPanel('child:' + id));
            g.addEventListener('mouseleave', () => _renderPanel());
            g.addEventListener('focus', () => _renderPanel('child:' + id));
            g.addEventListener('blur', () => _renderPanel());
            const go = () => { if (_onNavigate) _onNavigate('child:' + id, 'constellation'); };
            g.addEventListener('click', go);
            g.addEventListener('keydown', (ev) => {
                if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); go(); }
            });
        });
        // Panel footer links (estate / circle / breadcrumb) — delegated: the
        // panel re-renders on every hover cycle.
        _host.querySelector('.an-panel').addEventListener('click', (ev) => {
            const l = ev.target.closest('[data-an-link]');
            if (l && _onNavigate) _onNavigate('link:' + l.dataset.anLink, 'panel');
        });

        // The seal → Trust Membrane detail / sign-off (TrustBadge is the shipping
        // surface for it; absent suite ⇒ the seal simply isn't rendered).
        const seal = _host.querySelector('.an-seal');
        if (seal) {
            const openSeal = () => { if (_onNavigate) _onNavigate('seal:open', 'membrane'); };
            seal.addEventListener('click', openSeal);
            seal.addEventListener('keydown', (ev) => {
                if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); openSeal(); }
            });
        }

        const center = _host.querySelector('.an-center');
        if (center) {
            const openPick = () => { if (_model.pickable) _openStarPicker(); };
            center.addEventListener('click', openPick);
            center.addEventListener('keydown', (ev) => {
                if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); openPick(); }
            });
        }

        _renderPanel();
    }

    // ═══ The star picker — selection lives AT the star center ═══════════════
    // Professional selector: auto-focused search, IFOC class filter pills,
    // the atlas entry, keyboard navigation (arrows/Enter/Esc), class-tinted
    // star glyphs per agent. Anchored over the star itself — no toolbar row.
    let _pickFilter = 'all';
    let _pickPop = null;      // the open popover (shared across host panels)

    function _closeStarPicker() {
        if (_pickPop) { _pickPop.remove(); _pickPop = null; }
        document.removeEventListener('mousedown', _pickDocDown, true);
    }
    function _pickDocDown(ev) {
        if (_pickPop && !_pickPop.contains(ev.target)) _closeStarPicker();
    }

    function _openStarPicker() {
        const stage = _host && _host.querySelector('.an-stage');
        const p = _model.pickable;
        if (!stage || !p) return;
        openAgentPicker({
            hostEl: stage,
            items: p.items || [],
            currentId: p.current || null,
            specialRow: { name: 'The capability atlas' },
            onPick: (id) => { if (p.onPick) p.onPick(id); },
        });
    }

    // The ONE agent picker (search + IFOC pills + canonical chips + keyboard
    // nav), reusable by any panel that anchors it over a position:relative
    // host — the Anatomy star center AND the Trust Circle center use exactly
    // this code path. `specialRow` is the no-agent entry (atlas / whole estate);
    // `specialRow.icon` (an inline SVG string, monochrome) replaces the default star
    // where the empty choice means something else (a flow slot: "Leave this slot open").
    function openAgentPicker({ hostEl, items, currentId, specialRow, onPick }) {
        _closeStarPicker();
        if (!hostEl) return;
        items = items || [];
        const p = { current: currentId || '', onPick };
        const pop = document.createElement('div');
        pop.className = 'an-pick';
        pop.setAttribute('role', 'listbox');
        const pills = [['all', 'All', null],
                       ['llm_only', 'Ideate', IFOC.llm_only.color],
                       ['rag_focused', 'Focus', IFOC.rag_focused.color],
                       ['tool_enabled', 'Optimize', IFOC.tool_enabled.color],
                       ['genie', 'Coordinate', IFOC.genie.color]];
        pop.innerHTML = `
            <input type="text" class="an-pick-search" placeholder="Search agents…" aria-label="Search agents">
            <div class="an-pick-pills">${pills.map(([k, label, color]) =>
                `<button type="button" class="an-pick-pill${_pickFilter === k ? ' on' : ''}" data-an-pill="${k}"${color ? ` style="--pill:${color}"` : ''}>
                    ${color ? `<span class="an-pick-dot" style="background:${color}"></span>` : ''}${label}</button>`).join('')}</div>
            <div class="an-pick-list" data-an-list></div>`;
        hostEl.appendChild(pop);
        _pickPop = pop;
        const input = pop.querySelector('.an-pick-search');
        const list = pop.querySelector('[data-an-list]');
        let active = -1;

        const starIco = (color) =>
            `<svg width="15" height="15" viewBox="-12.5 -12.5 25 25" ${color ? `fill="${esc(color)}" fill-opacity="0.9"` : 'fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"'}><path d="${STAR_PATH}"/></svg>`;
        const check = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" style="margin-left:auto;color:var(--teradata-orange,#F15F22)"><polyline points="20 6 9 17 4 12"/></svg>`;

        const rows = () => {
            const q = (input.value || '').trim().toLowerCase();
            const filtered = items.filter((a) =>
                (_pickFilter === 'all' || a.type === _pickFilter) &&
                (!q || a.tag.toLowerCase().includes(q) || (a.name || '').toLowerCase().includes(q)));
            const atlasRow = (specialRow && _pickFilter === 'all' && !q)
                ? [{ id: '', tag: '', name: specialRow.name, icon: specialRow.icon || null, atlas: true }] : [];
            return [...atlasRow, ...filtered];
        };
        const render = () => {
            const rs = rows();
            active = Math.min(active, rs.length - 1);
            list.innerHTML = rs.map((a, i) => {
                const cur = a.atlas ? !p.current : p.current === a.id;
                const c = a.atlas ? null : (a.color || cls(a.type).color);
                return `<button type="button" class="an-pick-row${i === active ? ' kb' : ''}" data-an-row="${esc(a.id)}" role="option" aria-selected="${cur}">
                    ${a.atlas
                        ? `${a.icon || starIco(null)}<span class="an-pick-name" style="font-style:italic">${esc(a.name)}</span>`
                        : `<span class="an-pick-chip" style="color:${esc(c)};background:${esc(c)}1a;border:1px solid ${esc(c)}4d;">@${esc(a.tag)}</span><span class="an-pick-name">${esc(a.name || '')}</span>`}
                    ${cur ? check : ''}</button>`;
            }).join('') || '<div class="an-pick-empty">No matching agent.</div>';
        };
        render();

        const select = (id) => { _closeStarPicker(); if (onPick) onPick(id || null); };
        list.addEventListener('click', (ev) => {
            const r = ev.target.closest('.an-pick-row');
            if (r) select(r.dataset.anRow);
        });
        pop.querySelectorAll('.an-pick-pill').forEach((b) => b.addEventListener('click', () => {
            _pickFilter = b.dataset.anPill;
            pop.querySelectorAll('.an-pick-pill').forEach((x) => x.classList.toggle('on', x === b));
            active = -1; render(); input.focus();
        }));
        input.addEventListener('input', () => { active = -1; render(); });
        pop.addEventListener('keydown', (ev) => {
            const rs = rows();
            if (ev.key === 'Escape') { ev.stopPropagation(); _closeStarPicker(); return; }
            if (ev.key === 'ArrowDown') { ev.preventDefault(); active = Math.min(active + 1, rs.length - 1); render(); }
            else if (ev.key === 'ArrowUp') { ev.preventDefault(); active = Math.max(active - 1, 0); render(); }
            else if (ev.key === 'Enter') {
                ev.preventDefault();
                const pick = rs[active >= 0 ? active : 0];
                if (pick) select(pick.atlas ? '' : pick.id);
            }
        });
        document.addEventListener('mousedown', _pickDocDown, true);
        input.focus();
    }

    function update(model) {
        if (!_host) return;
        const opts = { host: _host, model, onNavigate: _onNavigate };
        mount(opts);   // full re-render is cheap and keeps one code path
    }

    // ════════════════════════════════════════════════════════════════════════
    //  CONTROLLER — model assembly from existing endpoints (all defensive)
    // ════════════════════════════════════════════════════════════════════════

    const API = '/api/v1';
    let _data = null;           // { profiles, llms, mcps, cwts, kgs, trust, memOwners }
    let _selected = null;       // profile id or null (atlas)
    let _pivotFrom = null;      // { id, tag } of the coordinator we pivoted away from
    let _perProfile = {};       // profileId → { components, relationships }
    let _inited = false;

    async function _get(path) {
        try {
            const token = localStorage.getItem('tda_auth_token');
            const res = await fetch(API + path, { headers: { Authorization: `Bearer ${token}` } });
            if (!res.ok) return null;
            return await res.json();
        } catch (_) { return null; }
    }

    async function _fetchAll(force) {
        if (_data && !force) return _data;
        const [profiles, llms, mcps, cwts, kgs, trust, memOwners] = await Promise.all([
            _get('/profiles'), _get('/llm/configurations'), _get('/mcp/servers'),
            _get('/context-window-types'), _get('/knowledge-graph/list'),
            _get('/trust/agents'), _get('/memory/owners'),
        ]);
        _data = {
            profiles: (profiles && profiles.profiles) || [],
            llms: (llms && (llms.configurations || llms.llm_configurations)) || [],
            mcps: (mcps && (mcps.servers || mcps.mcp_servers)) || [],
            cwts: (cwts && (cwts.types || cwts.context_window_types)) || [],
            kgs: (kgs && (kgs.graphs || kgs.knowledge_graphs)) || [],
            trust: (trust && trust.agents) || null,
            memOwners: (memOwners && (memOwners.owners || memOwners.buckets)) || null,
        };
        return _data;
    }

    /** Active (non-archived) sessions bound to this agent, or null when the
     *  relationships endpoint could not answer — null renders as absence, never
     *  as a confident "0 session(s)". */
    function _activeSessionCount(rel) {
        const s = rel && rel.relationships && rel.relationships.sessions;
        if (!s) return null;
        if (typeof s.active_count === 'number') return s.active_count;
        return Array.isArray(s.items)
            ? s.items.filter((x) => x && !x.is_archived).length : null;
    }

    async function _profileExtras(p) {
        if (_perProfile[p.id]) return _perProfile[p.id];
        const [components, rel, sk] = await Promise.all([
            _get(`/profiles/${encodeURIComponent(p.id)}/components`),
            _get(`/artifacts/profile/${encodeURIComponent(p.id)}/relationships`),
            _get(`/profiles/${encodeURIComponent(p.id)}/skills`),
        ]);
        _perProfile[p.id] = {
            components: (components && components.components) || null,
            // The relationships endpoint reports sessions as `active_count`
            // (authoritative, uncapped) beside an `items` list that is CAPPED at the
            // endpoint's limit and mixes active with archived. There is no `active`
            // array — reading one made every agent report `0 session(s)` forever.
            // `items` is the fallback only, and must drop the archived rows itself.
            sessions: _activeSessionCount(rel),
            // The APPLIED skill set — the endpoint runs the SAME resolve_auto_skill_specs
            // as the runtime, so auto-enabled companion skills appear (a raw skillsConfig
            // read misses them: auto entries follow component enablement and often carry
            // no explicit active flag). null = endpoint unavailable — honest absence,
            // never rendered as "0 skills".
            skills: (sk && Array.isArray(sk.skills))
                ? sk.skills.filter((s) => s && s.active && s.available !== false)
                : null,
        };
        return _perProfile[p.id];
    }

    const _nameOf = (list, id, keys) => {
        const hit = (list || []).find((x) => x && (x.id === id || x.configuration_id === id || x.server_id === id));
        if (!hit) return null;
        for (const k of keys) { if (hit[k]) return hit[k]; }
        return null;
    };

    // ── faculty reads — ONE rule per faculty, shared by the KPI tiles, the atlas
    //    star and the Faculty Adoption chart. Each used to re-derive from the raw
    //    profile JSON and disagree with the runtime (and with each other).
    /** Component ids ENABLED for the agent under THE enablement chain — the server
     *  resolves them onto `componentsEnabled` (global gate · per-user override ·
     *  componentConfig · manifest default · relevance). Read raw, a profile with no
     *  componentConfig counted as having nothing while it runs with memory and the
     *  action components on by default. A payload without the field (older server)
     *  falls back to the explicit config. */
    function _enabledComponents(p) {
        if (Array.isArray(p.componentsEnabled)) return p.componentsEnabled;
        const cc = p.componentConfig || {};
        return Object.keys(cc).filter((k) => cc[k] && cc[k].enabled);
    }
    /** Skill ids that APPLY for the agent — explicit actives ∪ auto companions, as
     *  the runtime resolver (`resolve_auto_skill_specs`) answers it on `skillsApplied`;
     *  fallback = the explicit actives only. */
    function _appliedSkills(p) {
        if (Array.isArray(p.skillsApplied)) return p.skillsApplied;
        return (((p.skillsConfig || {}).skills) || []).filter((s) => s && s.active && s.id).map((s) => s.id);
    }
    /** Knowledge repository ids bound to the agent. Entries are `{id, reranking}`
     *  objects (a bare id in older configs): `String(entry)` on an object collapsed
     *  every repository to one "[object Object]" key, so the Intelligence tile read
     *  1 for any estate with a repository and never anything else. */
    function _knowledgeRepoIds(p) {
        return (((p.knowledgeConfig || {}).collections) || [])
            .map((c) => (c && typeof c === 'object') ? c.id : c)
            .filter((id) => id != null && id !== '')
            .map(String);
    }
    /** The estate's faculty counts — distinct instruments / repositories / skills
     *  and the agents carrying them. One computation for every estate surface. */
    function _facultyStats(profiles) {
        const memAgents = profiles.filter((p) => _enabledComponents(p).includes('memory')).length;
        const instruments = new Set(); let instrumentedAgents = 0;
        const repos = new Set(), skills = new Set();
        profiles.forEach((p) => {
            const on = _enabledComponents(p);
            if (on.length) instrumentedAgents += 1;
            on.forEach((k) => instruments.add(k));
            _knowledgeRepoIds(p).forEach((id) => repos.add(id));
            _appliedSkills(p).forEach((id) => skills.add(id));
        });
        return { memAgents, instruments, instrumentedAgents, repos, skills };
    }

    // ── estate-wide aggregates (the atlas star reads the WHOLE estate) ────────
    // Every number here is config-level truth over all profiles — the same
    // grammar the KPI tiles use, surfaced ON the star so the atlas is a live
    // reading, not a diagram (mirrors the Trust Circle's estate mode).
    /** The two guardrail lanes, folded worst-of into one per-agent token.
     *  `guardBest` was I36's sibling — a `.some()` across the lanes, so outbound
     *  enforce + inbound OFF read green while its own value line said `in off ·
     *  out enforce`: the colour contradicting its own text. An agent's guardrails
     *  act only when BOTH edges act; one dead lane is an open edge, not a detail.
     *    acting  — both lanes enforce/review
     *    partial — anything mixed (incl. an acting lane beside an off one)
     *    off     — both lanes off                                            */
    function _lanesToken(inMode, outMode) {
        const acts = (m) => m === 'enforce' || m === 'review';
        if (acts(inMode) && acts(outMode)) return 'acting';
        if (inMode === 'off' && outMode === 'off') return 'off';
        return 'partial';
    }

    function _estateStats() {
        const profiles = (_data && _data.profiles) || [];
        const total = profiles.length;
        const split = { llm_only: 0, rag_focused: 0, tool_enabled: 0, genie: 0 };
        profiles.forEach((p) => { if (split[p.profile_type] != null) split[p.profile_type] += 1; });

        const brains = new Set(), providers = new Set(), senses = new Set();
        profiles.forEach((p) => {
            if (p.llmConfigurationId) brains.add(p.llmConfigurationId);
            if (p.providerName) providers.add(p.providerName);
            if (p.mcpServerId) senses.add(p.mcpServerId);
        });

        const { memAgents, instruments, instrumentedAgents, repos, skills } = _facultyStats(profiles);
        const memHeld = Array.isArray(_data && _data.memOwners)
            ? _data.memOwners.reduce((s, o) => s + (o && o.count || 0), 0) : null;
        const kgs = ((_data && _data.kgs) || []).length;

        // Awareness — the estate's situational consciousness at rest.
        const cwts = ((_data && _data.cwts) || []).length;
        const primed = profiles.filter((p) => p.session_primer).length;
        const scheduled = profiles.filter((p) => ((p.componentConfig || {}).scheduler || {}).enabled).length;

        // Foundations — the strongest posture in force across the estate.
        const g = { enforce: 0, observe: 0, off: 0 };
        const gu = { enforce: 0, observe: 0, off: 0 };
        profiles.forEach((p) => {
            const gMode = ((p.groundingConfig || {}).mode) || 'observe';
            if (gMode === 'enforce' || gMode === 'review') g.enforce += 1;
            else if (gMode === 'off') g.off += 1;
            else g.observe += 1;
            const outMode = (((p.guardrailConfig || {}).outbound || {}).mode) || 'observe';
            const inMode = (((p.guardrailConfig || {}).inbound || {}).mode) || 'observe';
            const lane = _lanesToken(inMode, outMode);
            gu[lane === 'acting' ? 'enforce' : lane === 'off' ? 'off' : 'observe'] += 1;
        });

        return { total, split, brains, providers, senses, memAgents, memHeld,
                 instruments, instrumentedAgents, repos, skills, kgs, cwts, primed, scheduled, g, gu };
    }

    // Estate posture → the two-foundation trust token, aggregated WORST-OF
    // (ledger I36; Rule 5.1 — the Circle's own policy). The previous form was a
    // `.some()` in disguise: 'pass' if ANY agent enforced, so ONE enforcing agent
    // of nine rendered the foundation green — the strongest member hiding the
    // other eight's exposure. A meaning loss that shipped.
    //   pass — every agent enforces/reviews (the foundation actually holds)
    //   warn — mixed: anything less than all, including enforcers beside OFF
    //   na   — all off / empty estate (dashed, honest absence — not alarm)
    //   watching — anything less than all protecting (W1): a mix of protecting and
    //              watching members is a POSTURE fact (the value carries the count),
    //              never a finding — so it is the dashed posture state, not amber.
    //              Same policy as trustGrammar.postureAggregate / the ring.
    function _postureToken(c) {
        const total = c.enforce + c.observe + c.off;
        if (!total || c.off === total) return 'na';
        return c.enforce === total ? 'pass' : 'watching';
    }

    // ── atlas model ─────────────────────────────────────────────────────────
    function _atlasModel() {
        const s = _estateStats();
        // Aggregate value lines — kept terse (the renderer clips at ~38 chars).
        const brainN = s.brains.size || s.providers.size;
        const memVal = s.memAgents
            ? `${s.memAgents}/${s.total} remember${s.memHeld != null && s.memHeld > 0 ? ` · ${s.memHeld} held` : ''}`
            : 'none remember yet';
        const cfgVal = s.total
            ? `${s.total} agent${s.total === 1 ? '' : 's'} · ${brainN} brain${brainN === 1 ? '' : 's'}`
              + (s.senses.size ? ` · ${s.senses.size} source${s.senses.size === 1 ? '' : 's'}` : '')
            : 'no agents yet';
        const awrVal = s.cwts
            ? `${s.cwts} context type${s.cwts === 1 ? '' : 's'}` + (s.primed ? ` · ${s.primed} primed` : '')
            : (s.primed ? `${s.primed} primed` : 'platform default');
        const compVal = s.instruments.size
            ? `${s.instruments.size} across ${s.instrumentedAgents} agent${s.instrumentedAgents === 1 ? '' : 's'}`
            : 'none enabled yet';
        const intParts = [];
        if (s.repos.size) intParts.push(`${s.repos.size} repo${s.repos.size === 1 ? '' : 's'}`);
        if (s.kgs) intParts.push(`${s.kgs} KG`);
        if (s.skills.size) intParts.push(`${s.skills.size} skill${s.skills.size === 1 ? '' : 's'}`);
        const intVal = intParts.length ? intParts.join(' · ') : 'none bound yet';

        const F = (key, metaphor, concept, value, headline, facts, navLabel, action, accent) => ({
            key, metaphor, concept, accent, value, state: 'default',
            panel: { headline, facts }, nav: { label: navLabel, action },
        });
        return {
            mode: 'atlas',
            center: { label: 'AGENT IDENTITY', count: s.total },
            faculties: [
                F('memory', 'local / global remembrance', 'Memory', memVal,
                  'What the agent remembers about you — across every session.',
                  [`Across the estate: ${s.memAgents} of ${s.total} agent(s) remember${s.memHeld != null && s.memHeld > 0 ? `, holding ${s.memHeld} memories` : ''}.`,
                   'Durable facts recalled into every conversation; a shared pool for all agents or private per agent.',
                   'Working memory (the context window) is managed separately, budget-aware, per turn.'],
                  'Open the Memory configuration', 'tab:context-window-tab'),
                F('configuration', 'DNA · brain · senses', 'Configuration', cfgVal,
                  'The constitution: its class, its model, the data it can sense.',
                  [`${s.total} agent(s): ${s.split.llm_only} Ideate · ${s.split.rag_focused} Focus · ${s.split.tool_enabled} Optimize · ${s.split.genie} Coordinate.`,
                   `${brainN} distinct brain(s) across ${s.providers.size} provider(s) and ${s.senses.size} data source(s).`,
                   'One profile = one agent — everything else in the platform hangs off it.'],
                  'Open the Agent Profiles', 'tab:profiles-tab', 'orange'),
                F('session', 'situational consciousness', 'Awareness', awrVal,
                  'What the agent holds in mind right now.',
                  [`${s.cwts} context window type(s); ${s.primed} agent(s) with a session primer, ${s.scheduled} on a timetable.`,
                   'Context window types govern the per-turn token budget across twelve modules.',
                   'Session primers pre-educate a fresh session before the first question.'],
                  'Open Awareness', 'view:executions-view'),
                F('components', 'acquired instruments', 'Components', compVal,
                  'The instruments it has learned to play.',
                  [`${s.instruments.size} distinct component(s) configured across ${s.instrumentedAgents} agent(s).`,
                   'Charts, canvases, dashboards, flows, schedulers, knowledge graphs — each an intelligent component the agent invokes as a tool.',
                   'Every action component ships with a companion skill that teaches taste, not just correctness.'],
                  'Open the Components view', 'view:components-view'),
                F('intelligence', 'knowledge & expertise', 'Intelligence', intVal,
                  'What it knows and how it learned it.',
                  [`${s.repos.size} knowledge repositor(y/ies), ${s.kgs} ontolog(y/ies), ${s.skills.size} active skill(s).`,
                   'Knowledge repositories (screened, signable corpora), planner repositories (proven strategies), knowledge graphs (the ontology), and skills.',
                   'Retrieval is governed: quarantine, sensitivity ceilings, and provenance ride along.'],
                  'Open the repositories', 'view:rag-maintenance-view'),
            ],
            foundations: [
                { key: 'grounding', metaphor: 'truth · faithfulness', concept: 'Grounding',
                  state: _postureToken(s.g),
                  value: s.total ? `${s.g.enforce} enforcing · ${s.g.observe} watching` : null,
                  panel: { headline: 'Answers are checked against the declared scope and the corpus — before delivery.',
                           facts: [`Across the estate: ${s.g.enforce} enforcing · ${s.g.observe} watching · ${s.g.off} off.`,
                                   'One gate, two trigger families: scope drift and corpus support (with second-chance retrieval).',
                                   'Postures per agent: watch (optionally disclosing) · enforce (optionally keeping originals for review).'] },
                  nav: { label: 'Open the Trust Posture', action: 'agents:posture' } },
                { key: 'guardrails', metaphor: 'rules · safety', concept: 'Guardrails',
                  state: _postureToken(s.gu),
                  value: s.total ? `${s.gu.enforce} enforcing · ${s.gu.observe} watching` : null,
                  panel: { headline: 'Protection on both edges — plus the three injection firewalls.',
                           facts: [`Across the estate: ${s.gu.enforce} enforcing · ${s.gu.observe} watching · ${s.gu.off} off.`,
                                   'Inbound injection screening; outbound PII redaction, toxicity blocking, exfil-URL defanging.',
                                   'Memory, retrieval, and tool-result firewalls close the indirect paths.'] },
                  nav: { label: 'Open the Trust Posture', action: 'agents:posture' } },
            ],
            membrane: { seal: null, authority: false },
            summary: null,
            pickable: _pickable(),
        };
    }

    // ── agent model ─────────────────────────────────────────────────────────
    function _agentModel(p, extras) {
        const k = cls(p.profile_type);
        const isGenie = p.profile_type === 'genie';
        const isFocus = p.profile_type === 'rag_focused';
        const isOptimize = p.profile_type === 'tool_enabled';
        const isIdeate = p.profile_type === 'llm_only';

        // Configuration (DNA · brain · senses) — always configured.
        const llmName = _nameOf(_data.llms, p.llmConfigurationId, ['name', 'model', 'configuration_name'])
            || p.providerName || 'LLM';
        const dual = p.dualModelConfig && p.dualModelConfig.enabled
            ? ` · dual (${p.dualModelConfig.tacticalModel || 'tactical'})` : '';
        const mcpName = _nameOf(_data.mcps, p.mcpServerId, ['name', 'server_name']);
        const senses = isGenie ? null
            : (isOptimize || (isIdeate && p.useMcpTools)) ? (mcpName || 'MCP server') : null;
        // Skills are NOT restated here — Intelligence owns them (Rule 3.0: no slot
        // restates another). The applied set, incl. auto companions, reads there.
        const configuration = {
            key: 'configuration', metaphor: 'DNA · brain · senses', concept: 'Configuration',
            accent: 'orange', state: 'configured',
            value: `${llmName}${dual}${senses ? ` · ${senses}` : ''}`,
            panel: { headline: 'Its constitution — the class, the brain, the senses.',
                     // The class as the platform draws it everywhere — the IFOC pill — never the
                     // internal type string. An Ideate agent that senses through tools says so.
                     facts: [{ k: 'Class', html: true,
                               v: _ifocBadge(p.profile_type) + (isIdeate && p.useMcpTools ? ' <span class="an-copy">with tools</span>' : '') },
                             { k: 'Brain', v: `${llmName}${dual}` },
                             { k: 'Senses', v: senses || (isGenie ? 'its experts — see the constellation' : 'none — pure reasoning') },
                            ].filter(Boolean) },
            nav: { label: 'Open the editor — AI stack', action: 'editor:ai-stack' },
        };

        // Memory
        // On/off is the RESOLVED enablement (memory is default-on for every class): the
        // stored `componentConfig.memory.enabled` is absent on an agent that never
        // touched the toggle, and read raw it showed such an agent as "off" while it
        // remembers. `learn` is read as the runtime reads it (memory/adapter: default off).
        const mem = (p.componentConfig || {}).memory || {};
        const memOn = _enabledComponents(p).includes('memory');
        const memCount = _memCountFor(p.id);
        const memory = {
            key: 'memory', metaphor: 'local / global remembrance', concept: 'Memory',
            state: memOn ? 'configured' : 'off',
            value: memOn
                ? `on${mem.learn ? ' · learns' : ''}${memCount != null ? ` · ${memCount} memories` : ''}`
                : 'off',
            panel: { headline: memOn
                        ? 'What it remembers about you — across every session.'
                        : 'No durable memory — every session starts fresh.',
                     facts: [{ k: 'Recall', v: memOn ? 'enabled' : 'off' },
                             { k: 'Learning', v: memOn ? (mem.learn ? 'extracts as it converses' : 'explicit writes only') : '—' },
                             memCount != null ? { k: 'Memories', v: `${memCount} in its pool · shared global pool included` } : null,
                             !memOn ? 'Enable the Memory component to give it durable recall.' : null].filter(Boolean) },
            nav: { label: 'Open the editor — capabilities', action: 'editor:capabilities' },
        };

        // Session
        const cwtName = _nameOf(_data.cwts, p.contextWindowTypeId, ['name', 'type_name']) || 'Balanced (default)';
        const sched = ((p.componentConfig || {}).scheduler || {}).enabled;
        const sessParts = [`CW ${cwtName}`];
        if (extras && extras.sessions != null) sessParts.unshift(`${extras.sessions} session(s)`);
        if (p.session_primer) sessParts.push('primer set');
        if (sched) sessParts.push('scheduler on');
        const session = {
            key: 'session', metaphor: 'situational consciousness', concept: 'Awareness',
            state: p.contextWindowTypeId || p.session_primer || sched ? 'configured' : 'default',
            value: sessParts.join(' · '),
            panel: { headline: 'Its situational consciousness, turn by turn.',
                     facts: [{ k: 'Context window', v: cwtName },
                             extras && extras.sessions != null ? { k: 'Sessions', v: `${extras.sessions} active` } : null,
                             { k: 'Primer', v: p.session_primer ? 'pre-educates every fresh session' : '—' },
                             { k: 'Scheduler', v: sched ? 'may act on a timetable' : '—' }].filter(Boolean) },
            nav: { label: 'Open Awareness', action: 'view:executions-view' },
        };

        // Components (governance-merged truth)
        const comps = (extras && extras.components || [])
            .filter((c) => c && c.profile_enabled && c.component_type !== 'structural')
            .map((c) => c.component_id || c.id);
        const components = {
            key: 'components', metaphor: 'acquired instruments', concept: 'Components',
            state: comps.length ? 'configured' : 'off',
            value: comps.length ? comps.slice(0, 3).join(' · ') + (comps.length > 3 ? ` +${comps.length - 3}` : '') : 'none',
            panel: { headline: comps.length
                        ? 'The instruments it has learned to play.'
                        : 'No instruments enabled yet.',
                     facts: comps.length
                        ? [{ k: 'Enabled', v: `${comps.length} instrument(s)` }, comps.join(' · ')]
                        : ['Enable components in the editor to let it chart, canvas, schedule, or graph.'] },
            nav: { label: 'Open the editor — capabilities', action: 'editor:capabilities' },
        };

        // Intelligence (class-aware)
        let intelligence;
        if (isGenie) {
            const n = ((p.genieConfig || {}).slaveProfiles || []).length;
            intelligence = {
                key: 'intelligence', metaphor: 'knowledge & expertise', concept: 'Intelligence',
                state: 'na-class', naNote: 'its experts carry it',
                panel: { headline: 'A coordinator knows through its experts.',
                         facts: [{ k: 'Experts', v: `${n} agent(s) in its constellation` },
                                 'Each expert is a full agent with its own anatomy — click one in the constellation to pivot.'] },
                nav: { label: 'Open the editor — capabilities', action: 'editor:capabilities' },
            };
        } else {
            const knColls = ((p.knowledgeConfig || {}).collections || []).length;
            const planner = (p.ragCollections || []).length;
            // The applied set when the extras fetch resolved it (incl. auto
            // companions); else the explicit entries — skillsConfig is
            // {skills: [...]}, and the old Object.values read treated it as a
            // map, which counted 0 for every profile.
            const skills = (extras && Array.isArray(extras.skills))
                ? extras.skills.length
                : _appliedSkills(p).length;
            const kgName = _activeKgName(p);
            const parts = [];
            if (knColls) parts.push(`${knColls} repo(s)`);
            if (planner && isOptimize) parts.push(`${planner} planner`);
            if (kgName) parts.push(`KG ${kgName}`);
            if (skills) parts.push(`${skills} skill(s)`);
            intelligence = {
                key: 'intelligence', metaphor: 'knowledge & expertise', concept: 'Intelligence',
                state: parts.length ? 'configured' : (isFocus ? 'off' : 'off'),
                value: parts.length ? parts.join(' · ') : (isFocus ? 'none — required for Focus' : 'none'),
                panel: { headline: parts.length ? 'What it knows — and where it learned it.' : 'No knowledge bound yet.',
                         facts: [{ k: 'Knowledge', v: knColls ? `${knColls} screened repositor${knColls === 1 ? 'y' : 'ies'}` : '—' },
                                 isOptimize ? { k: 'Planner', v: planner ? `${planner} repositor${planner === 1 ? 'y' : 'ies'} of proven strategies` : '—' } : null,
                                 { k: 'Ontology', v: kgName || '—' },
                                 { k: 'Skills', v: skills ? `${skills} active` : '—' },
                                 isFocus && !knColls ? 'A Focus agent requires a knowledge repository to answer.' : null].filter(Boolean) },
                nav: { label: 'Open the editor — resources', action: 'editor:resources' },
            };
        }

        // Foundations (trust tokens)
        // ONE posture→state map (trustGrammar.POSTURE_STATE, W1) — the ring, the
        // posture matrix and this panel read the same word for the same stored mode.
        const TGm = (typeof window !== 'undefined' && window.TrustGrammar) || null;
        const postureState = (m) => (TGm && TGm.postureState) ? TGm.postureState(m)
            : ({ off: 'na', observe: 'watching', annotate: 'watching', enforce: 'pass', review: 'pass' }[m] || 'na');
        const postureLabel = (m) => (TGm && TGm.postureLabel) ? TGm.postureLabel(m)
            : ({ off: 'off', observe: 'watch', annotate: 'watch + disclose',
                 enforce: 'enforce', review: 'enforce + keep for review' }[m] || m);
        const gMode = ((p.groundingConfig || {}).mode) || 'observe';
        const gState = postureState(gMode);
        const outMode = (((p.guardrailConfig || {}).outbound || {}).mode) || 'observe';
        const inMode = (((p.guardrailConfig || {}).inbound || {}).mode) || 'observe';
        const guardLane = _lanesToken(inMode, outMode);
        // An open edge (one lane off beside an acting one) is a FINDING — the colour
        // contradicted its own text once — so `partial` with an off lane stays warn;
        // two watching lanes, or one watching beside one acting, is a posture.
        const guardState = guardLane === 'acting' ? 'pass' : guardLane === 'off' ? 'na'
            : ((inMode === 'off' || outMode === 'off') ? 'warn' : 'watching');
        const foundations = [
            { key: 'grounding', metaphor: 'truth · faithfulness', concept: 'Grounding',
              state: gState, value: postureLabel(gMode),
              panel: { headline: 'The truth gate — every answer checked before delivery.',
                       facts: [{ k: 'Posture', v: postureLabel(gMode) },
                               { k: 'Checks', v: 'scope drift · corpus support (with second-chance retrieval)' },
                               gState === 'watching' ? 'Watching only — it records drift and stops nothing; Enforce is one click away.' : null].filter(Boolean) },
              nav: { label: 'Open the editor — trust configuration', action: 'editor:trust' } },
            { key: 'guardrails', metaphor: 'rules · safety', concept: 'Guardrails',
              state: guardState, value: `in ${postureLabel(inMode)} · out ${postureLabel(outMode)}`,
              panel: { headline: 'Protection on both edges of the conversation.',
                       facts: [{ k: 'Inbound', v: `${postureLabel(inMode)} — prompt-injection screening` },
                               { k: 'Outbound', v: `${postureLabel(outMode)} — PII · toxicity · exfil-URLs` },
                               'The memory, retrieval, and tool-result firewalls close the indirect paths.'] },
              nav: { label: 'Open the editor — trust configuration', action: 'editor:trust' } },
        ];

        // A coordinator's constellation: its REAL experts, class-tinted, pivotable.
        let constellation = null;
        if (isGenie) {
            const ids = ((p.genieConfig || {}).slaveProfiles || []);
            const children = ids
                .map((id) => (_data.profiles || []).find((x) => x && x.id === id))
                .filter(Boolean)
                .map((ch) => ({ key: ch.id, tag: ch.tag, color: ch.color || cls(ch.profile_type).color,
                                clsLabel: cls(ch.profile_type).label + ' expert' }));
            constellation = { children, overflow: Math.max(0, children.length - 9) };
        }

        return {
            mode: 'agent',
            center: { label: '@' + p.tag, sub: `${k.label} agent`, color: p.color || k.color },
            summary: p.description || null,
            faculties: [memory, configuration, session, components, intelligence],
            foundations,
            membrane: { seal: _sealFor(p), authority: !!(p.authorityConfig && Object.keys(p.authorityConfig).length) },
            constellation,
            links: { estate: true, circle: true },
            backlink: _pivotFrom ? { label: '@' + _pivotFrom.tag } : null,
            pickable: _pickable(),
        };
    }

    function _sealFor(p) {
        if (!_data.trust) return null;
        const hit = _data.trust.find((t) => t && (t.profile_id === p.id || t.tag === p.tag));
        return hit ? (hit.state || null) : 'unsigned';
    }

    function _activeKgName(p) {
        const hit = (_data.kgs || []).find((g) => g && (g.profile_id === p.id
            || (g.assigned_profiles || []).some((a) => a && a.profile_id === p.id && a.is_active)) && (g.is_active !== false));
        return hit ? (hit.kg_name || hit.name || null) : null;
    }

    function _memCountFor(profileId) {
        if (!Array.isArray(_data.memOwners)) return null;
        const hit = _data.memOwners.find((o) => o && (o.profile_id === profileId || o.id === profileId));
        return hit ? (hit.count != null ? hit.count : hit.total) : null;
    }

    function _pickable() {
        // Canonical IFOC order (Ideate → Focus → Optimize → Coordinate), then
        // alphabetically by tag — the platform's standard profile sorting.
        const IFOC_ORDER = { llm_only: 0, rag_focused: 1, tool_enabled: 2, genie: 3 };
        const items = (_data.profiles || [])
            .filter((p) => p && p.tag)
            .map((p) => ({ id: p.id, tag: p.tag, name: p.name || '', type: p.profile_type,
                           color: p.color || cls(p.profile_type).color }))
            .sort((a, b) => ((IFOC_ORDER[a.type] ?? 9) - (IFOC_ORDER[b.type] ?? 9))
                            || a.tag.localeCompare(b.tag));
        return {
            items, current: _selected || '',
            onPick: (id) => { _selected = id || null; _pivotFrom = null; _rebuild(); },
        };
    }

    // ── navigation ──────────────────────────────────────────────────────────
    async function _navigate(action) {
        const [kind, target] = String(action || '').split(':');
        if (kind === 'tab') {
            const btn = document.querySelector(`.config-tab[data-tab="${target}"]`);
            if (btn) btn.click();
            return;
        }
        if (kind === 'view') {
            try {
                const { handleViewSwitch } = await import('../ui.js?v=1.287');
                handleViewSwitch(target);
            } catch (_) { /* noop */ }
            // A profile in focus travels with the jump: the Sessions panel opens
            // pre-filtered to this agent's sessions (retry — the view mounts async).
            if (target === 'executions-view' && _selected) {
                const p2 = (_data.profiles || []).find((x) => x.id === _selected);
                if (p2 && p2.tag) {
                    for (let i = 0; i < 10; i++) {
                        if (window.executionDashboard && window.executionDashboard.focusAgent
                            && document.getElementById('session-search')) {
                            window.executionDashboard.focusAgent(p2.tag);
                            break;
                        }
                        await new Promise((r) => setTimeout(r, 150));
                    }
                }
            }
            return;
        }
        if (kind === 'agents') {
            try {
                const { handleViewSwitch } = await import('../ui.js?v=1.287');
                handleViewSwitch('agents-view');
            } catch (_) { /* noop */ }
            for (let i = 0; i < 10; i++) {
                if (window.agentPerformance && window.agentPerformance.showSection) {
                    window.agentPerformance.showSection(target || 'posture');
                    return;
                }
                await new Promise((r) => setTimeout(r, 150));
            }
            return;
        }
        if (kind === 'circle') {
            if (window.agentPerformance) {
                if (_selected && window.agentPerformance.openCircle) {
                    const p = (_data.profiles || []).find((x) => x.id === _selected);
                    window.agentPerformance.openCircle(p ? p.tag : null);
                } else if (window.agentPerformance.openCircle) {
                    window.agentPerformance.openCircle();
                }
            }
            return;
        }
        if (kind === 'child') {
            const from = (_data.profiles || []).find((x) => x.id === _selected);
            if (from) _pivotFrom = { id: from.id, tag: from.tag };
            _selected = target;
            _rebuild();
            return;
        }
        if (kind === 'link') {
            if (target === 'back' && _pivotFrom) {
                _selected = _pivotFrom.id;
                _pivotFrom = null;
                _rebuild();
                return;
            }
            if (target === 'estate') {
                try {
                    const { handleViewSwitch } = await import('../ui.js?v=1.287');
                    handleViewSwitch('ontology-view');
                } catch (_) { /* suite may be absent — honest no-op */ }
                // Context-aware: the selected agent travels with the jump. The
                // ontology handler is lazy-imported by the view switch, so RETRY
                // until its deep-link export exists (same pattern as Awareness).
                if (_selected) {
                    const pe = (_data.profiles || []).find((x) => x.id === _selected);
                    if (pe && pe.tag) {
                        for (let i = 0; i < 25; i++) {
                            if (window.ontologyFocusAgent) { window.ontologyFocusAgent(pe.tag); break; }
                            await new Promise((r) => setTimeout(r, 200));
                        }
                    }
                }
                return;
            }
            if (target === 'awareness') {
                _navigate('view:executions-view');   // pre-filters to the agent
                return;
            }
            if (target === 'circle') {
                const p = (_data.profiles || []).find((x) => x.id === _selected);
                if (window.agentPerformance && window.agentPerformance.openCircle) {
                    window.agentPerformance.openCircle(p ? p.tag : null);
                }
            }
            return;
        }
        if (kind === 'seal') {
            const p3 = (_data.profiles || []).find((x) => x.id === _selected);
            if (p3 && window.TrustBadge && window.TrustBadge.openDetail) {
                window.TrustBadge.openDetail(p3.id, p3.tag);
            } else if (p3) {
                _navigate('agents:posture');   // honest fallback: the posture matrix
            }
            return;
        }
        if (kind === 'editor') {
            if (!_selected) return;
            try {
                const mod = await import('./configurationHandler.js?v=2.214');
                if (mod.openProfileForEdit) mod.openProfileForEdit(_selected, target);
            } catch (_) {
                // fallback: the profile card's own edit button
                const btn = document.querySelector(`[data-action="edit-profile"][data-profile-id="${CSS.escape(_selected)}"]`);
                if (btn) btn.click();
            }
        }
    }

    // ── Composition KPIs — the estate's anatomy as numbers (config-level truth,
    //    always estate-wide: per-agent detail is the star's job). The five tiles
    //    ARE the faculties; Session's numbers live in Awareness, not duplicated.
    function _renderKpis() {
        const host = document.getElementById('anatomy-kpis');
        if (!host || !_data) return;
        const profiles = _data.profiles || [];

        // Agents + IFOC split (the compositional fingerprint)
        const split = { llm_only: 0, rag_focused: 0, tool_enabled: 0, genie: 0 };
        profiles.forEach((p) => { if (split[p.profile_type] != null) split[p.profile_type] += 1; });
        const total = profiles.length;
        const segBar = total ? `<div class="an-kpi-bar">` + Object.entries(split)
            .filter(([, n]) => n > 0)
            .map(([t, n]) => `<div style="width:${(n / total * 100).toFixed(1)}%;background:${cls(t).color};"></div>`)
            .join('') + `</div>` : '';

        // Brains & senses
        const brains = new Set(), providers = new Set(), senses = new Set();
        profiles.forEach((p) => {
            if (p.llmConfigurationId) brains.add(p.llmConfigurationId);
            if (p.providerName) providers.add(p.providerName);
            if (p.mcpServerId) senses.add(p.mcpServerId);
        });

        // Memory · Instruments · Intelligence — the resolved faculty reads (what RUNS,
        // not what the raw config happens to spell out).
        const { memAgents, instruments, instrumentedAgents, repos, skills } = _facultyStats(profiles);
        const memHeld = Array.isArray(_data.memOwners)
            ? _data.memOwners.reduce((s, o) => s + (o && o.count || 0), 0) : null;
        const kgs = (_data.kgs || []).length;

        const tile = (label, big, sub, accent, ico, tip, extra, action) => `
            <div class="perf-kpi-card"${tip ? ` data-tooltip="${esc(tip)}"` : ''}${action ? ` data-an-action="${action}" role="button" tabindex="0" style="cursor:pointer;"` : ''}>
                <div class="flex items-center justify-between">
                    <div class="min-w-0">
                        <p class="text-sm font-medium" style="color: var(--text-muted);">${label}</p>
                        <p class="text-3xl font-bold mt-1" style="color: var(--text-primary);">${big}</p>
                        ${sub ? `<p class="text-[11px] mt-1 an-kpi-sub" style="color: var(--text-muted);">${sub}</p>` : ''}
                        ${extra || ''}
                    </div>
                    <div class="perf-kpi-icon" style="--kpi-accent: ${accent};">${ico}</div>
                </div>
            </div>`;
        const I = (path, vb) => `<svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6" fill="none" viewBox="${vb || '0 0 24 24'}" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`;

        host.innerHTML =
            tile('Agents', total, `${split.llm_only} Ideate · ${split.rag_focused} Focus · ${split.tool_enabled} Optimize · ${split.genie} Coordinate`,
                 'var(--teradata-orange)', I(`<path d="${STAR_PATH}" transform="translate(12,12) scale(0.95)"/>`, '0 0 24 24'),
                 `${total} agent(s) — the estate's compositional fingerprint by IFOC class: ${split.llm_only} Ideate, ${split.rag_focused} Focus, ${split.tool_enabled} Optimize, ${split.genie} Coordinate. Click to pick an agent and see its anatomy.`,
                 segBar, 'pick-agent')
            + tile('Brains', brains.size || providers.size,
                   `${providers.size} provider(s) · ${senses.size} data source(s)`,
                   '#F15F22', I('<circle cx="12" cy="12" r="3.2"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.2 2.2M16.9 16.9l2.1 2.1M19.1 4.9l-2.2 2.2M7.1 16.9l-2.2 2.1"/>'),
                   `${brains.size || providers.size} distinct LLM configuration(s) power your agents, across ${providers.size} provider(s) and ${senses.size} data source(s).`)
            + tile('Memory', memAgents,
                   memHeld != null ? `${memHeld} memories held` : 'agents that remember',
                   '#4ade80', I('<path d="M12 3a7 7 0 0 1 7 7v2a7 7 0 0 1-3 5.7V21h-8v-3.3A7 7 0 0 1 5 12v-2a7 7 0 0 1 7-7z"/><path d="M9 10h6M9 13h4"/>'),
                   `${memAgents} agent(s) with the memory component enabled — resolved as the runtime resolves it (admin gate, your override, the agent's config, the default)${memHeld != null ? ` — holding ${memHeld} memories in total` : ''}.`)
            + tile('Instruments', instruments.size,
                   `enabled across ${instrumentedAgents} agent(s)`,
                   '#3b82f6', I('<rect x="3" y="4" width="8" height="7" rx="1.5"/><rect x="13" y="4" width="8" height="7" rx="1.5"/><rect x="3" y="13" width="8" height="7" rx="1.5"/><path d="M17 13.5v6M14 16.5h6"/>'),
                   `${instruments.size} distinct component(s) enabled across ${instrumentedAgents} agent(s) — the instruments your agents can wield, resolved as the runtime resolves them. Click to open Components.`,
                   '', 'view:components')
            + tile('Intelligence', repos.size,
                   `${kgs} ontolog${kgs === 1 ? 'y' : 'ies'} · ${skills.size} skill(s) active`,
                   '#9333ea', I('<path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5"/><path d="M9 18h6"/><path d="M10 22h4"/>'),
                   `${repos.size} distinct knowledge repositor${repos.size === 1 ? 'y' : 'ies'} bound to your agents, ${kgs} ontolog${kgs === 1 ? 'y' : 'ies'} held (owned or subscribed; the Faculty chart counts the agents with one active) and ${skills.size} distinct skill(s) applied (explicit and auto companions). Click to open Intelligence.`,
                   '', 'view:rag-maintenance');

        // Context-sensitive tile actions: pick an agent (star picker) or jump to
        // the estate view that manages that faculty. Idempotent per refresh.
        host.onclick = (ev) => _runAnatomyKpiAction(ev.target.closest('[data-an-action]'));
        host.onkeydown = (ev) => {
            if (ev.key !== 'Enter' && ev.key !== ' ') return;
            const t = ev.target.closest('[data-an-action]');
            if (t) { ev.preventDefault(); _runAnatomyKpiAction(t); }
        };
    }

    // Resolve a KPI-tile action: 'pick-agent' opens the star picker; 'view:<id>'
    // switches the top-level estate view via its nav button.
    function _runAnatomyKpiAction(tileEl) {
        if (!tileEl) return;
        const action = tileEl.dataset.anAction;
        if (action === 'pick-agent') { _openStarPicker(); return; }
        if (action && action.startsWith('view:')) {
            const btn = document.getElementById('view-switch-' + action.slice(5));
            if (btn) btn.click();
        }
    }

    // ── Composition charts — the Trust panel's chart-row idiom, composition-
    //    native: which parts of the anatomy the estate actually uses, and which
    //    brains power it (configuration counts — usage lives in Awareness).
    function _renderCharts() {
        const host = document.getElementById('anatomy-charts');
        if (!host || !_data) return;
        const profiles = _data.profiles || [];
        const total = profiles.length || 1;

        // Faculty adoption
        const has = {
            memory: (p) => _enabledComponents(p).includes('memory'),
            senses: (p) => !!p.mcpServerId || !!p.useMcpTools,
            instruments: (p) => _enabledComponents(p).length > 0,
            knowledge: (p) => _knowledgeRepoIds(p).length > 0,
            ontology: (p) => !!_activeKgName(p),
            skills: (p) => _appliedSkills(p).length > 0,
        };
        const FROWS = [['Memory', 'memory', '#4ade80'], ['Data senses', 'senses', '#F15F22'],
                       ['Instruments', 'instruments', '#3b82f6'], ['Knowledge', 'knowledge', '#9333ea'],
                       ['Ontology', 'ontology', '#10b981'], ['Skills', 'skills', '#eab308']];  // hex-palette: anatomy faculty identity colours (categorical, not trust state)
        const frows = FROWS.map(([label, key, color]) => {
            const n = profiles.filter(has[key]).length;
            const pct = Math.round(n / total * 100);
            return `<div class="an-chart-row">
                <span class="an-chart-label">${label}</span>
                <span class="an-chart-track"><span class="an-chart-fill" style="width:${pct}%;background:${color};"></span></span>
                <span class="an-chart-num">${n} · ${pct}%</span></div>`;
        }).join('');

        // Brains in use (agents per model — configuration, not usage)
        const byBrain = {};
        profiles.forEach((p) => {
            const name = _nameOf(_data.llms, p.llmConfigurationId, ['name', 'model']) || p.providerName || 'unassigned';
            byBrain[name] = (byBrain[name] || 0) + 1;
        });
        const brains = Object.entries(byBrain).sort((a, b) => b[1] - a[1]);
        const top = brains.slice(0, 6);
        const overflow = brains.length - top.length;
        const maxB = top.length ? top[0][1] : 1;
        // hex-palette: brand progress-fill gradient (decorative identity, not a trust state)
        const BRAND_FILL = 'linear-gradient(90deg,var(--teradata-orange,#F15F22),#f59e0b)';  // hex-palette
        const brows = top.map(([name, n]) => `<div class="an-chart-row">
                <span class="an-chart-label" data-tooltip="${esc(name)}">${esc(name.length > 26 ? name.slice(0, 24) + '…' : name)}</span>
                <span class="an-chart-track"><span class="an-chart-fill" style="width:${Math.round(n / maxB * 100)}%;background:${BRAND_FILL};"></span></span>
                <span class="an-chart-num">${n} agent${n === 1 ? '' : 's'}</span></div>`).join('')
            + (overflow > 0 ? `<div class="an-chart-more">+${overflow} more model(s)</div>` : '');
        const providers = new Set(); const sensesN = new Set();
        profiles.forEach((p) => { if (p.providerName) providers.add(p.providerName); if (p.mcpServerId) sensesN.add(p.mcpServerId); });

        const card = (title, note, body, tip) => `
            <div class="rounded-xl p-5 border"${tip ? ` data-tooltip="${esc(tip)}"` : ''} style="background-color: var(--card-bg); border-color: var(--border-primary);">
                <div class="flex items-center justify-between mb-4">
                    <span class="font-bold" style="color: var(--text-primary);">${title}</span>
                    <span class="text-xs" style="color: var(--text-muted);">${note}</span>
                </div>${body}</div>`;
        host.innerHTML =
            card('Faculty Adoption', `across ${profiles.length} agent(s)`, frows,
                 'How many of your agents have each faculty configured — memory, data senses, instruments, knowledge, ontology and skills.')
            + card('Brains in Use', `${providers.size} provider(s) · ${sensesN.size} data source(s)`, brows,
                 'Which LLM models power your agents, by number of agents on each — configuration counts, not live usage.');
    }

    // ── lifecycle ───────────────────────────────────────────────────────────
    async function _rebuild() {
        const host = document.getElementById('agent-anatomy-host');
        if (!host) return;
        await _fetchAll();
        _renderKpis();
        _renderCharts();
        let model;
        if (_selected) {
            const p = (_data.profiles || []).find((x) => x.id === _selected);
            if (!p) { _selected = null; model = _atlasModel(); }
            else model = _agentModel(p, await _profileExtras(p));
        } else {
            model = _atlasModel();
        }
        mount({ host, model, onNavigate: _navigate });
    }

    async function init(profileId) {
        if (profileId !== undefined) _selected = profileId;
        const host = document.getElementById('agent-anatomy-host');
        if (!host) return;
        if (!_inited) {
            _inited = true;
            // Staleness contract (§9.2): the editor closing means config may have
            // changed — drop every cache and rebuild if the anatomy is visible.
            const modal = document.getElementById('profile-modal');
            if (modal && typeof MutationObserver !== 'undefined') {
                let wasOpen = !modal.classList.contains('hidden');
                new MutationObserver(() => {
                    const open = !modal.classList.contains('hidden');
                    if (wasOpen && !open) {
                        _data = null; _perProfile = {};
                        const pane = document.getElementById('anatomy-tab');
                        if (pane && !pane.classList.contains('hidden')) _rebuild();
                    }
                    wasOpen = open;
                }).observe(modal, { attributes: true, attributeFilter: ['class'] });
            }
            // The profile list changed somewhere else — an approved platform write, another
            // editor, an import: drop the caches, rebuild if visible. A deleted agent stays
            // selectable otherwise (live, Nov 2026).
            if (window.UderiaBus) {
                window.UderiaBus.on('uderia:profile-modified', () => {
                    _data = null; _perProfile = {};
                    const pane = document.getElementById('anatomy-tab');
                    if (pane && !pane.classList.contains('hidden')) _rebuild();
                });
            }
        }
        await _rebuild();
    }

    /** Deep link: open the Anatomy view on its Anatomy tab, agent selected. */
    async function open(profileId) {
        _selected = profileId || null;
        try {
            const { handleViewSwitch } = await import('../ui.js?v=1.287');
            handleViewSwitch('credentials-view');
        } catch (_) { /* already there */ }
        const btn = document.querySelector('.config-tab[data-tab="anatomy-tab"]');
        if (btn) btn.click();
        await init(_selected);
    }

    // ── Phase C: the editor-header mini star — the agent's state at a glance ──
    // Compact, informative-only (no click: the editor is already open); tip
    // summarises. Same states as the full anatomy, labels suppressed.
    function _miniSvg(model) {
        // Industrial at-a-glance mark: the steel star with a soft class glow,
        // a clean seal ring top-right, and one twin status bar (grounding |
        // guardrails) at the base. Nothing else — state without ornament.
        const mc = { x: 62, y: 54 }, ms = 4.2;
        const fnd = {};
        (model.foundations || []).forEach((f) => { fnd[f.key] = f; });
        const tok = (f) => ({ pass: 'var(--trust-pass, #34d399)', warn: 'var(--trust-warn, #fbbf24)',
                              danger: 'var(--trust-danger, #f87171)' }[(f && f.state)] || null);
        const seg = (f, x) => {
            const c = tok(f);
            return `<rect x="${x}" y="101" width="44" height="3" rx="1.5" fill="var(--border-secondary)" opacity="0.5"/>` +
                   (c ? `<rect x="${x}" y="101" width="44" height="3" rx="1.5" fill="${c}" opacity="0.9"/>` : '');
        };
        const tint = model.center && model.center.color ? esc(model.center.color) : null;
        const seal = model.membrane && model.membrane.seal ? (() => {
            const s = model.membrane.seal;
            const color = s === 'trusted' ? 'var(--trust-seal-trusted, #34d399)'
                : s === 'changed' ? 'var(--trust-seal-changed, #f59e0b)' : 'var(--trust-seal-unsigned, #94a3b8)';
            const op = s === 'unsigned' ? '0.55' : '1';
            return `<circle cx="107" cy="17" r="4" fill="none" stroke="${color}" stroke-width="1.5" opacity="${op}"/>
                ${s === 'trusted' ? `<path d="M105.3 17l1.2 1.2 2.2-2.2" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>` : ''}`;
        })() : '';
        return `<svg viewBox="0 0 124 118" width="112" height="107" class="an-mini-svg" role="img" aria-label="Agent anatomy at a glance">
            <defs>
                <filter id="anBlurMini" x="-80%" y="-80%" width="260%" height="260%"><feGaussianBlur stdDeviation="11"/></filter>
            </defs>
            <rect x="1" y="1" width="122" height="116" rx="10" class="an-mini-card"/>
            ${tint ? `<circle cx="${mc.x}" cy="${mc.y - 3}" r="30" fill="${tint}" opacity="0.12" filter="url(#anBlurMini)"/>` : ''}
            <!-- Schematic, like the main star — the mini kept a private copy of the
                 retired steel gradient (and its drop shadow), which would have left the
                 editor header disagreeing with the panel it links to. -->
            <path d="${STAR_PATH}" transform="translate(${mc.x},${mc.y}) scale(${ms})" class="an-star-i"/>
            <path d="${STAR_PATH}" transform="translate(${mc.x},${mc.y}) scale(${ms})" class="an-star-f"${tint ? ` stroke="${tint}" stroke-width="0.2" stroke-opacity="0.85"` : ''}/>
            ${seal}
            ${seg(fnd.grounding, 15)}${seg(fnd.guardrails, 65)}
        </svg>`;
    }

    async function miniInto(host, profileId) {
        if (!host || !profileId) return;
        try {
            await _fetchAll();
            const p = (_data.profiles || []).find((x) => x.id === profileId);
            if (!p || !host.isConnected) return;
            const model = _agentModel(p, await _profileExtras(p));
            if (!host.isConnected) return;
            host.innerHTML = _miniSvg(model);
            const g = model.foundations.find((f) => f.key === 'grounding') || {};
            const gu = model.foundations.find((f) => f.key === 'guardrails') || {};
            host.setAttribute('data-tip',
                `The anatomy at a glance\ngrounding ${g.value || 'observe'} · guardrails ${gu.value || 'observe'}` +
                (model.membrane.seal ? `\nmembrane: ${model.membrane.seal}` : ''));
        } catch (_) { /* decorative — never break the editor */ }
    }

    // Returning to the tab after visiting another config tab: the generic
    // switcher only toggles panes — rebuild ourselves on the button click.
    function _wireTab() {
        const btn = document.querySelector('.config-tab[data-tab="anatomy-tab"]');
        if (btn && !btn.dataset.anWired) {
            btn.dataset.anWired = '1';
            btn.addEventListener('click', () => init());
        }
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _wireTab);
    else _wireTab();

    /** Fill the view-level composition KPIs (they sit above the tab bar and are
        visible on EVERY Configuration tab — light: fetch + render only). */
    async function refreshKpis() {
        await _fetchAll();
        _renderKpis();
        _renderCharts();
    }

    window.AgentAnatomy = { mount, update, init, open, miniInto, refreshKpis, openAgentPicker };
})();
