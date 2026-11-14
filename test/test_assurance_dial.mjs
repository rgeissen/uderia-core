#!/usr/bin/env node
/**
 * The Assurance dial — R-2 ledger coverage.
 *
 * The migration's imperative is that no information previously provisioned is lost, and
 * a promise is not a mechanism. So the ledger rows for §4.1 (strip → dial) and §4.2
 * (capsule → dial + sections) are asserted here as executable coverage: each row names a
 * fact, and the fact must resolve to a station.
 *
 * The module is loaded and RUN — not grepped. A gate that only reads source text passes
 * on code that cannot execute, which is how the other checkers in this design were wrong.
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.join(HERE, '..', 'static', 'js', 'handlers', 'assuranceDial.js');

let pass = 0; const failures = [];
function check(name, got, want) {
    if (JSON.stringify(got) === JSON.stringify(want)) { pass++; return; }
    failures.push(`${name}\n      got=${JSON.stringify(got)} want=${JSON.stringify(want)}`);
}
function section(t) { console.log(`\n── ${t} ${'─'.repeat(Math.max(0, 64 - t.length))}`); }

// Load the real module under a minimal window, plus the real attention vocabulary so the
// gate exercises the shared source rather than the fallback (TC-50's lesson).
const win = {};
// A REAL localStorage stub. A no-op one made `lockPin()` write nowhere, so the test
// could not observe the behaviour it was asserting — and would have passed a module
// that never persisted anything, which is exactly the defect it exists to catch.
const _store = {};
globalThis.localStorage = {
    getItem: (k) => (k in _store ? _store[k] : null),
    setItem: (k, v) => { _store[k] = String(v); },
    removeItem: (k) => { delete _store[k]; },
};
// TrustGrammar FIRST — it is the vocabulary every other module reads. The comment above
// claimed the gate exercised "the real attention vocabulary", but only trustCircle was
// ever loaded, so the dial silently ran on its own literal fallbacks and this gate could
// not have detected them drifting from the source they are pinned to.
new Function('window', 'document',
    fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'handlers', 'trustGrammar.js'), 'utf8')
).call(null, win, { addEventListener() {} });
new Function('window', 'document',
    fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'handlers', 'trustCircle.js'), 'utf8')
).call(null, win, { addEventListener() {}, querySelector: () => null, createElement: () => ({ style: {} }) });
new Function('window', 'document', fs.readFileSync(SRC, 'utf8'))(win, {});
const AD = win.AssuranceDial;
const BAND_STATE = win.TrustGrammar.BAND_STATE;

const P = (key, label, station, state, detail, acted) =>
    ({ key, label, station, state, detail, acted: !!acted });

// ── 1 · the capsule invariant ────────────────────────────────────────────────
section('capsule invariant — sections ≡ stations, 1:1, derived from ORDER');
check('ORDER is the ring\'s eight, in the ring\'s order', AD.ORDER,
    ['signed', 'grounded', 'guarded', 'bounded', 'scored', 'trusted', 'reviewed', 'proven']);
check('every station has a human label', AD.ORDER.every((k) => !!AD.LABEL[k]), true);

// A section list that is not derived from ORDER is how the trace stopped being obvious
// the first time — so the renderer's section markup must come from ORDER itself.
const src = fs.readFileSync(SRC, 'utf8');
check('sections are rendered by mapping ORDER, never a parallel list',
    /ORDER\.map\(\(k\) => sectionHtml\(k, st\[k\]\)\)/.test(src), true);
check('there is exactly one station list in the module',
    (src.match(/const ORDER = /g) || []).length, 1);
// W5 — the list is READ from the one registry (trustGrammar.STATIONS); the literal is
// only the absence-tolerant fallback, and it equals the canonical.
check('ORDER derives from trustGrammar.STATIONS', /const ORDER = _TGS \? _TGS\.map\(\(r\) => r\.key\)/.test(src), true);
check('ORDER equals the registry', AD.ORDER.join(','), win.TrustGrammar.STATIONS.map((r) => r.key).join(','));
check('LABEL equals the registry', JSON.stringify(AD.LABEL),
    JSON.stringify(Object.fromEntries(win.TrustGrammar.STATIONS.map((r) => [r.key, r.label]))));

// ── class scope — an out-of-class mechanism is rendered, dimmed, keyed and counted ──
section('class scope — a mechanism outside the class is listed and marked, never dropped');
{
    const P2 = (key, label, station, state, extra) => Object.assign({ key, label, station, state, detail: `${label}: d`, acted: false, word: '', dims: [], guide: null, modules: [] }, extra || {});
    const off = P2('preflight_sql', 'Preflight SQL', 'grounded', 'na', { applies: false, word: 'not part of this class', classes: ['Ideate + tools', 'Optimize'], detail: 'Preflight SQL: not part of this class — runs for Ideate + tools, Optimize agents' });
    const on = P2('grounding_gate', 'Grounding gate', 'grounded', 'pass', { applies: true });
    const st = AD.stations({ by_station: { grounded: [on, off] } });
    check('an off-class row never moves the station', st.grounded.state, 'pass');
    check('…and is still a row', st.grounded.points.length, 2);
    const html = AD._sectionHtml ? AD._sectionHtml('grounded', st.grounded) : null;
    check('the section renderer is exported for the gate', typeof AD._sectionHtml, 'function');
    if (html) {
        check('the off-class row carries its mark', /ad-gate-offclass/.test(html), true);
        check('…its words', /not part of this class — runs for Ideate \+ tools, Optimize agents/.test(html), true);
        check('the sub-head counts what applies', /1 of 2 apply to this class/.test(html), true);
        const offMods = P2('governed_data', 'Governed data', 'grounded', 'na', { applies: false, modules: [{ id: 'm1', name: 'M1', ran: false }] });
        check('an off-class row carries no modules line',
            /module/.test(AD._sectionHtml('grounded', AD.stations({ by_station: { grounded: [on, offMods] } }).grounded).split('Governed data')[1] || ''), false);
        check('an all-applicable station has no count', /apply to this class/.test(AD._sectionHtml('grounded', AD.stations({ by_station: { grounded: [on] } }).grounded)), false);
    }
    // ── the members shelved by class, as the Guide shelves them — folded by STATE ──
    // upheld by · controls, bounded by · boundaries, attested by · attestations,
    // evidenced by · measures: one structure on both surfaces. A group folds only when
    // every member is quiet or did not run; a member that acted or reads amber/red keeps
    // its group open, and the fold line carries the worst dot, the count and the reading.
    if (html) {
        const G = (key, cls, state, extra) => P2(key, key, 'grounded', state, Object.assign({ applies: true, guide: { k: key, cls } }, extra || {}));
        const quiet = AD._sectionHtml('grounded', AD.stations({ by_station: { grounded: [
            G('gate_a', 'control', 'pass'), G('gate_b', 'control', 'na'), G('bound_a', 'boundary', 'pass'), G('meas_a', 'measure', 'na')] } }).grounded);
        check('members are shelved by the verb of their class', /ad-grp-lead">upheld by<[\s\S]*ad-grp-lead">bounded by<[\s\S]*ad-grp-lead">evidenced by</.test(quiet), true);
        check('…with the kind and the count on the fold line', /ad-grp-kind">controls \u00b7 2</.test(quiet) && /ad-grp-kind">boundary \u00b7 1</.test(quiet) && /ad-grp-kind">measure \u00b7 1</.test(quiet), true);
        check('a quiet group is FOLDED, and says so', !/<details class="ad-grp" open>/.test(quiet) && /ad-grp-sum">all quiet</.test(quiet), true);
        check('…a group that did not run says that instead', /ad-grp-kind">measure \u00b7 1<\/span><span class="ad-grp-sum">did not run</.test(quiet), true);
        check('the rows are still inside their group (nothing dropped)', (quiet.match(/class="ad-gate /g) || []).length, 4);
        const loud = AD._sectionHtml('grounded', AD.stations({ by_station: { grounded: [
            G('gate_a', 'control', 'danger', { acted: true }), G('gate_b', 'control', 'pass'), G('bound_a', 'boundary', 'pass')] } }).grounded);
        check('a group with a red member stays OPEN and names it', /<details class="ad-grp" open><summary class="ad-grp-h ad-gate-danger">[\s\S]*ad-grp-sum">1 needs you now</.test(loud), true);
        check('…while the quiet group beside it folds', /<details class="ad-grp"><summary class="ad-grp-h ad-gate-pass">[\s\S]*bounded by/.test(loud), true);
        const acted = AD._sectionHtml('grounded', AD.stations({ by_station: { grounded: [G('gate_a', 'control', 'pass', { acted: true }), G('gate_b', 'control', 'pass')] } }).grounded);
        check('a group where a member acted stays open and counts the acts', /<details class="ad-grp" open>[\s\S]*ad-grp-sum">1 acted</.test(acted), true);
        check('the fold uses no new route or data attribute (details/summary only)', !/data-ad-grp|data-grp/.test(quiet), true);
        // The station NAME is its Guide entry: a `data-dim="station_<key>"` route, like the
        // status line's station chip — a name you can read but not open is a dead end.
        check('the station name in the section head opens the Guide at the station',
            /ad-section-name" data-dim="station_grounded" role="button" tabindex="0"/.test(quiet), true);
    }
    // The key is PER PANEL: it explains the marks of the station it sits in, inside that
    // station's box — never once below all eight, outside the box whose marks it explains.
    const key = AD._markKey ? AD._markKey(st.grounded) : '';
    check('the legend names the mark', /not part of this class/.test(key), true);
    check('…beside the did-not-run mark, not instead of it', /did not run this turn/.test(AD._markKey(AD.stations({ by_station: { grounded: [P2('x', 'X', 'grounded', 'na')] } }).grounded)), true);
    {
        const sec = AD._sectionHtml('grounded', st.grounded);
        check('the key sits INSIDE the station panel, at its foot', /<div class="ad-key"[\s\S]*<\/section>\s*$/.test(sec.trim() + ''), true);
        check('…and the dial renders no key outside the panels',
            !/<\/div>\s*<div class="ad-key"/.test(fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'handlers', 'assuranceDial.js'), 'utf8')
                .replace(/^\s*\/\/.*$/gm, '')) && !/\$\{markKey\(st\)\}/.test(fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'handlers', 'assuranceDial.js'), 'utf8')), true);
        const quietOnly = AD._markKey(AD.stations({ by_station: { guarded: [P2('g', 'G', 'guarded', 'pass')] } }).guarded);
        check('…listing only the marks that panel shows (a quiet panel keys only quiet)',
            /quiet/.test(quietOnly) && !/not part of this class/.test(quietOnly) && !/did not run/.test(quietOnly), true);
    }
    check('the CSS draws the mark', /\.ad-gate-offclass/.test(fs.readFileSync(path.join(HERE, '..', 'static', 'css', 'trust.css'), 'utf8')), true);
}

// ── 2 · it derives nothing ───────────────────────────────────────────────────
section('the renderer owns no ladder — the registry is the only source');
// The strip carried its own stamp→state mapping. Any of these appearing here means a
// second ladder has been reintroduced.
for (const forbidden of ['guardrail_inbound', 'provenance_meta', 'kg_enrichment_event',
                         'out_of_scope_dbs', 'execution_trace', 'conversation_agent_events']) {
    check(`the dial never reads turn.${forbidden} directly`, src.includes(forbidden), false);
}
check('the attention vocabulary is borrowed, not re-declared',
    /TrustCircle\.attentionWord/.test(src) && !/'worth a look'/.test(src), true);

// ── 3 · ledger §4.1 + §4.2 — every fact resolves to a station ────────────────
section('ledger coverage — each row\'s information lands somewhere');
// A realistic turn exercising every mechanism family the ledger names.
const FULL = {
    predates_tracing: false,
    by_station: {
        signed: [],
        grounded: [
            P('trusted_scope', 'Trusted scope', 'grounded', 'pass', 'Trusted scope stamped: fitness_db'),   // I3
            P('preflight_sql', 'Preflight SQL', 'grounded', 'pass', 'Preflight SQL: every query named a known database'),
            P('grounding_gate', 'Grounding gate', 'grounded', 'warn', 'Grounding gate: out-of-scope references — answer annotated', true),
            P('honesty_gate', 'Honesty', 'grounded', 'pass', 'Honesty check: no unsupported absence claim'),
        ],
        guarded: [
            P('inbound_guard', 'Inbound guard', 'guarded', 'pass', 'Inbound guard: screened — clean'),      // I1
            P('retrieval_firewall', 'Retrieval firewall', 'guarded', 'pass', 'Retrieval firewall: 9 chunk(s) screened — clean'),
            P('corpus_quarantine', 'Corpus quarantine', 'guarded', 'pass', 'Corpus quarantine: nothing under quarantine'),
            P('outbound_guard', 'Outbound guard', 'guarded', 'warn', 'Outbound guard: personal data masked (pii:email)', true), // I4
        ],
        bounded: [
            P('authority', 'Authority', 'bounded', 'pass', 'Authority: autonomous run (flow)'),             // I6
            P('tool_enforcement', 'Tool enforcement', 'bounded', 'pass', 'Tool enforcement: 4 call(s) executed'), // I7
            P('sensitivity_ceiling', 'Sensitivity ceiling', 'bounded', 'na', 'Sensitivity ceiling: no ceiling set'),
        ],
        scored: [
            P('governed_data', 'Governed data', 'scored', 'warn', 'Governed data: 1 free-formed / 3 governed'), // I8
            P('evaluation', 'Evaluation', 'scored', 'pass', 'Evaluation: every applicable dimension passed'),   // I9
        ],
        trusted: [],
        reviewed: [P('review_parking', 'Human review', 'reviewed', 'warn', 'Human review: parked (item 42)', true)],
        proven: [P('provenance_seal', 'Provenance seal', 'proven', 'pass', 'Provenance: sealed · tip 9f2c41b8e0d3…')], // I5
    },
};
const st = AD.stations(FULL);

// I1–I9 · every ledger fact must be reachable through a station.
const LEDGER = {
    I1: 'inbound_guard', I3: 'trusted_scope', I4: 'outbound_guard', I5: 'provenance_seal',
    I6: 'authority', I7: 'tool_enforcement', I8: 'governed_data', I9: 'evaluation',
};
const reachable = new Set(AD.ORDER.flatMap((k) => st[k].points.map((p) => p.key)));
for (const [row, key] of Object.entries(LEDGER)) {
    check(`ledger ${row}: '${key}' is reachable through a station`, reachable.has(key), true);
}
// I13 — the capsule's 9–10 linear glyphs become sections, one gesture deeper, not gone.
// (Clickability is carried by the station STATE now, not a mark — the count channel
// is retired; see the mark-grammar section below.)
check('I13 stations with mechanisms carry a reading (never silently empty)',
    AD.ORDER.filter((k) => st[k].points.length && st[k].state === 'na'), []);

// ── 4 · the state and mark grammar ───────────────────────────────────────────
// ONE dot, ONE meaning (operator amendment to PROP-002): a mark exists ⟺ a gate
// ACTED. The I/II depth channel (1 = detail, 2 = acted) is dead — the dial rendered
// pairs, the ring pair-or-nothing, the estate nothing: three surfaces speaking
// different subsets of one "global" channel, which is the drift this gate guards.
section('grammar — worst-of, one acted dot, weight before colour');
check('a station is the worst of its mechanisms', st.guarded.state, 'warn');
check('…and a clean one stays quiet', st.proven.state, 'pass');
check('an empty station is `na`, never `pass`', st.signed.state, 'na');
check('an acted station carries exactly ONE mark', st.guarded.marks, 1);
check('a reported-but-quiet station carries NO mark (no more detail-dot)',
    st.proven.marks, 0);
check('no mark on an empty station either', st.signed.marks, 0);
check('marks are binary — the count channel is dead',
    AD.ORDER.every((k) => st[k].marks === 0 || st[k].marks === 1), true);

// The dot path must EXECUTE, not merely parse: the polar()-shape crash lived only
// on turns with an ACTED station (dots render only then), so every structural grep
// passed while the real render threw on the first acted turn and froze the dial.
// Render the SVG for the fixture (guarded has acted=true) and assert the dot is IN it.
{
    let svg = '';
    let threw = null;
    try { svg = AD.dialSvg(st, 132); } catch (e) { threw = e; }
    check('dialSvg renders an acted fixture without throwing', threw === null, true);
    check('…and the acted station carries its single dot',
        /class="ad-dot ad-dot-warn"/.test(svg), true);
    check('…exactly one dot per acted station (no pair)',
        (svg.match(/class="ad-dot /g) || []).length,
        AD.ORDER.filter((k) => st[k].marks === 1).length);
    check('…with real coordinates (the crash surfaced as cx="NaN"/undefined)',
        /cx="[0-9.]+" cy="[0-9.]+"/.test(svg), true);
}

// Rule 9.4 — every state must be distinguishable by stroke weight alone.
const widths = ['na', 'unknown', 'pass', 'warn', 'danger'].map((s) => AD.strokeFor(s, 4));
check('the five states have five distinct stroke weights', new Set(widths).size, widths.length);
// na < unknown < pass < warn < danger — the same ordering as the attention rank, so a
// reader who cannot see hue still reads the priority off the line weight.
check('weight rises monotonically with attention',
    widths.every((w, i) => i === 0 || w > widths[i - 1]), true);
check('an unrecognised state never yields NaN', Number.isFinite(AD.strokeFor('bogus', 4)), true);

// `unknown` must outrank `pass`: a gate whose outcome is unrecorded is not a pass.
check('unknown outranks pass in the aggregation', AD.RANK.unknown > AD.RANK.pass, true);
check('…and warn outranks unknown', AD.RANK.warn > AD.RANK.unknown, true);

// ── 5 · the summary, and V4 ──────────────────────────────────────────────────
section('summary — names what acted; a pre-tracing turn says so');
const s1 = AD.summarise(st, FULL);
check('the summary names every station that acted',
    ['Guarded', 'Grounded', 'Reviewed'].every((n) => s1.text.includes(n)), true);
check('the summary carries the worst state', s1.worst, 'warn');

const CLEAN = { predates_tracing: false, by_station: Object.fromEntries(AD.ORDER.map((k) => [k,
    [P(`${k}_g`, 'G', k, 'pass', 'clean')]])) };
const s2 = AD.summarise(AD.stations(CLEAN), CLEAN);
check('a wholly clean turn says every layer was quiet', /every layer quiet/.test(s2.text), true);
check('…and does not use a plural placeholder', /\(s\)/.test(s2.text), false);

// V4 — a turn written before the trace existed must never read "not turned on".
const PRE = AD.summarise(AD.stations({ by_station: {} }), { predates_tracing: true });
check('a pre-tracing turn says so, in words', /predates the assurance trace/.test(PRE.text), true);
check('…and never claims a layer was switched off', /not turned on/.test(PRE.text), false);
check('…and does not sit in attention', PRE.worst, 'na');

// ── 6 · form rules the module must implement ─────────────────────────────────
section('form rules (contract §9)');
// 9.2 — you cannot change the agent from an answer, so no plus may appear here.
check('9.2 the dial renders no plus (you cannot pick an agent from an answer)',
    /ad-plus|PLUS_GLYPH/.test(src), false);
// 9.6 — a section is revealed in place; nothing is removed or filtered away.
// Scope to paint()'s OWN body. The first version sliced everything after the function
// header, which swept in inject() and flagged the line that retires the legacy strip —
// a different act entirely, and legitimate. A gate that cannot tell "a station hid its
// neighbour" from "the old renderer was replaced" would push toward leaving both on
// screen, which is the bug it was written beside.
const paintBody = (() => {
    const i = src.indexOf('function paint()');
    if (i < 0) return null;
    let depth = 0;
    for (let j = src.indexOf('{', i); j < src.length; j += 1) {
        if (src[j] === '{') depth += 1;
        else if (src[j] === '}') { depth -= 1; if (!depth) return src.slice(i, j + 1); }
    }
    return null;
})();
check('9.6 paint() is extractable (the slice must not silently miss)', paintBody != null, true);
check('9.6 opening a station hides no sibling permanently',
    /\.remove\(\)|innerHTML = ''/.test(paintBody || ''), false);
// …and the dial must RETIRE the strip rather than mount underneath it. "Both renderers
// present" is not the same as mutually exclusive, and the difference looked exactly
// like the feature not shipping.
check('the dial retires the legacy strip when it mounts',
    /#trust-journey-strip[\s\S]{0,120}\.remove\(\)/.test(src), true);
// 9.7 — the reading region is fixed height, in CSS, not by luck.
const css = fs.readFileSync(path.join(HERE, '..', 'static', 'css', 'trust.css'), 'utf8');
const adRead = (css.match(/\.ad-read\s*\{([^}]*)\}/) || [, ''])[1];
check('9.7 .ad-read is extractable', adRead.length > 0, true);
check('9.7 the hover-written region has a fixed height',
    /height:\s*\d+px/.test(adRead) && /overflow:\s*hidden/.test(adRead), true);
const adInstr = (css.match(/\.ad-instrument\s*\{([^}]*)\}/) || [, ''])[1];
check('9.7 the instrument row does not stretch its columns',
    /align-items:\s*start/.test(adInstr), true);
// Keyboard parity (V6) — every pointer route has a keyboard route.
check('V6 stations are focusable and keyboard-operable',
    /tabindex="0"/.test(src) && /addEventListener\('keydown'/.test(src), true);
check('V6 …and announce themselves', /aria-label=/.test(src), true);
check('V6 …and the reading region is a live region', /aria-live="polite"/.test(src), true);

// ── 6b · what the first render actually shipped without ────────────────────
// Every assertion here corresponds to something that was MISSING on screen while this
// gate was green. The gate proved the model and never looked at what got drawn.
section('the drawn instrument — pinned because the first one shipped without these');

check('every station has an icon (anonymous arcs are not a reading)',
    AD.ORDER.filter((k) => !new RegExp(`${k}:\\s*'`).test(src)), []);
check('…and the icons are actually drawn into the segment', /\$\{icon\}/.test(src), true);
check('9.2 the star marks the subject', /const STAR = /.test(src) && /ad-star/.test(src), true);
check('9.2 …and still renders no plus', /ad-plus|PLUS_GLYPH/.test(src), false);
check('the instrument is labelled (the strip had a header)',
    /class="ad-head"/.test(src) && /ad-title/.test(src), true);

// A summary that says "quiet" beside a coloured segment is two slots contradicting
// each other — the defect the whole grammar exists to prevent, on the dial itself.
const noneActed = { by_station: Object.fromEntries(AD.ORDER.map((k) => [k,
    k === 'trusted' ? [P('t', 'T', 'trusted', 'warn', 'x')] : []])) };
const sNone = AD.summarise(AD.stations(noneActed), noneActed);
check('the summary never says "quiet" while a station is amber',
    /quiet/.test(sNone.text), false);
check('…it names which station needs a look', /Trusted/.test(sNone.text), true);
check('…and carries that state', sNone.worst, 'warn');

// The reading must quote the mechanism DRIVING the state, or the headline and the
// detail beside it describe different things ("not recorded · scope stamped"). ONE rule
// now serves both the word and the reading — they described the same station and
// disagreed, because each had its own fallback. Behaviour is asserted in §7d.
check('the reading picks the point that drives the state',
    /const _driverPoint = \(pts, state\) =>/.test(src), true);
check('…and both callers use it', (src.match(/_driverPoint\(/g) || []).length, 2);

// Ledger R2 — a station click also locates its step in the trace. Losing the route to
// the evidence is reach loss even though the fact is still on screen.
// Asserting the function EXISTS is not enough — deleting the call leaves it defined
// and the gate green while the route is gone. Assert the click handler calls it.
// Ledger R2 is precise: the station click OPENS the panel; the trace scroll lives on
// the gate row inside it. Attaching it to the segment merged two intentions into one
// gesture — the reader asked to see a station and got the page yanked instead — and a
// station spans several mechanisms, so it could only ever pick one arbitrarily.
const stationClick = (src.match(/g\.addEventListener\('click',[\s\S]{0,240}?\}\);/) || [''])[0];
check('R2 clicking a STATION does not move the trace',
    /scrollTraceTo\(/.test(stationClick), false);
check('R2 the gate row is the scroll route',
    /data-ad-gate[\s\S]{0,1600}?scrollTraceTo\(/.test(src), true);
// Two questions, two destinations. "What happened?" is the trace; "what does this
// measure?" is the Quality Dimensions Guide — the platform's ONE glossary, so this is
// also the single navigation channel (Rule 3b.3). Sending a scored dimension to a trace
// card showed another rendering of the same fact instead of what it means.
// Assert it in the EMITTED markup, not anywhere in the file: the first version matched
// the word in a comment, so deleting the attribute left the gate green and the route
// gone. Scope every source assertion to the construct it is about.
const attrsBlock = (src.match(/const attrs = locatable[\s\S]{0,600}?: '';/) || [''])[0];
check('a scored mechanism routes to the Guide, not the trace',
    /guideDims\(p\)/.test(src) && /data-dim="\$\{esc\(p\.key\)\}"/.test(src), true);
// A row must open at ITS OWN subject. `data-dim-guide` makes the delegation pass `null`
// as the highlight, so clicking "Inbound guard" landed on `safety` — the thing that
// measures it rather than the thing that was clicked.
check('…and opens AT the mechanism, not at what measures it',
    /data-dim-guide/.test(attrsBlock) || /data-dim-guide="/.test(src), false);
// The scope is exactly what was CLICKED — one row. Adding its dimensions put two
// entries on screen for one click and left the reader to work out which is the answer;
// they are already reachable as chips, which is what the two-way links are for.
// Scope = what was clicked — EXCEPT for the one mechanism whose reading IS its
// dimensions. `evaluation` is the union over every applicable dimension, so showing a
// single row with its own name back was the opposite of the answer.
check('…scoped to the clicked mechanism alone',
    /\[\{ k: p\.key, b: BAND_OF\[p\.state\] \|\| 'na' \}\]/.test(src), true);
check('…except an aggregate, which opens ITS dimensions',
    /isAgg && Array\.isArray\(p\.dims\) && p\.dims\.length\s*\n?\s*\? p\.dims/.test(src), true);
check('…and being an aggregate is declared, not guessed from a count',
    /p\.guide && p\.guide\.aggregate/.test(src), true);
check('…and does not also pull in what measures it',
    /\}\]\s*\n\s*\.concat\(Array\.isArray\(p\.dims\)/.test(src), false);
// The registry decides which mechanisms are measured and by what. A second list here
// disagreed with it, which is why some rows opened the whole glossary and others had no
// link at all — the exact "two ladders over one fact" class.
check('…and the dial holds no second opinion about which are measured',
    /GUIDE_ROUTE/.test(src), false);
// Scoped, not the whole glossary: opening 52 entries from one reading answers a
// question nobody asked.
check('…and the Guide opens on THIS turn\'s dimensions',
    /data-dim-context="\$\{esc\(JSON\.stringify\(ctxRows\)\)\}/.test(src), true);
check('…and the context rides on the same row as the route',
    /\$\{ctx\}/.test(attrsBlock)
    && /const ctx = guided[\s\S]{0,200}?data-dim-context/.test(src), true);
// One channel: qualityDimensions.js already delegates on these attributes, so the dial
// must not open a parallel route (Rule 3b.3).
check('…through the platform\'s existing channel, not a second one',
    /QualityDimensions\.openGuide\(/.test(src), false);
// Scope to the delegated handler. A bare indexOf matched `function scrollTraceTo(key)`
// — the DEFINITION, hundreds of lines earlier — and reported the order backwards.
const gateHandler = (src.match(/const key = row\.getAttribute[\s\S]{0,1200}?scrollTraceTo\(key\);/) || [''])[0];
check('the gate handler is extractable', gateHandler.length > 0, true);
check('…and the Guide route is taken BEFORE the trace route',
    gateHandler.indexOf("hasAttribute('data-dim')") < gateHandler.indexOf('scrollTraceTo(key)'), true);
check('…and a guided row never falls through to the trace route',
    /hasAttribute\('data-dim'\) \|\| row\.hasAttribute\('data-dim-guide'\)\) return;/
        .test(gateHandler), true);
check('…and it never defines a dimension itself (one glossary)',
    /const DIM = \{|function label\(/.test(src), false);
check('R2 …and the locator is implemented', /function scrollTraceTo/.test(src)
    && /scrollIntoView/.test(src), true);
// Only a gate with a step is offered as a route; a row that cannot jump must not look
// like it can.
check('R2 trace markers are per-MECHANISM, not per-station',
    /grounding_gate:\s*\['Grounding Gate'\]/.test(src), true);
check('a guided row is actually offered as a control',
    /const guided = !!dims/.test(src), true);
// Every row that carries dimensions must be offered; the complaint was that some had no
// link at all.
const withDims = { by_station: { grounded: [
    P('trusted_scope', 'Trusted scope', 'grounded', 'pass', 'stamped'),
    P('grounding_gate', 'Grounding gate', 'grounded', 'pass', 'in scope')] } };
withDims.by_station.grounded.forEach((x) => { x.dims = [{ k: 'scope_adherence', b: 'pass' }]; });
check('every reading carrying dimensions is link-eligible',
    AD.stations(withDims).grounded.points.every((x) => x.dims && x.dims.length), true);
check('R2 a gate with neither route stays inert',
    /const locatable = guided \|\| !!TRACE_STEP\[p\.key\]/.test(src), true);
check('R2 …and locating flashes rather than isolating',
    /ad-located/.test(src) && !/style\.display = 'none'/.test(src), true);

// The focused <g> drew the browser's default outline — a rounded rect across a
// quadrant — because the reset only covered :focus-visible.
const adFocus = /\.ad-st:focus\s*\{[^}]*outline:\s*none/.test(css);
check('a clicked station draws no default focus rectangle', adFocus, true);


// ── 6c · why only three stations lit ───────────────────────────────────────
// Two stations had NO mechanism at all, so they could never report on any turn —
// which reads as "nothing here" when the truth was "nobody wired it".
section('every station can report, or says why it cannot');
check('pending outranks pass but not a real finding',
    AD.RANK.pending > AD.RANK.pass && AD.RANK.pending < AD.RANK.warn, true);
check('pending has its own stroke weight',
    new Set(['na', 'pending', 'pass'].map((x) => AD.strokeFor(x, 4))).size, 3);

// Rule 9.1 — "has not reported yet" must never render as "switched off".
const PEND = { by_station: Object.fromEntries(AD.ORDER.map((k) => [k,
    k === 'scored' ? [P('evaluation', 'Evaluation', 'scored', 'pending', 'not in yet')] : []])) };
const sp = AD.summarise(AD.stations(PEND), PEND);
check('a pending station is not summarised as quiet', /quiet/.test(sp.text), false);
check('…and pending is not folded into na', AD.stations(PEND).scored.state, 'pending');


// The glyph is the station's most obvious affordance. Stroked line-art with fill:none
// registers hits only on the drawn pixels, so it LOOKED clickable and was not — only
// the arc was. A mark that appears interactive and is not is worse than no mark.
check('the station glyph has a real hit area', /ad-ico-hit/.test(src), true);
check('…and the disc, not the line-art, owns the hit',
    /\.ad-ico-hit\s*\{[^}]*pointer-events:\s*all/.test(css)
    && /\.ad-ico\s*\{[^}]*pointer-events:\s*none/.test(css), true);

// SCORED must read from where the score LIVES. Printing "not in yet" above a Quality
// card reading "passing — 20 checks" is two surfaces disagreeing about one fact.
check('a scored turn is not reported as pending',
    AD.stations({ by_station: { scored: [P('evaluation', 'Evaluation', 'scored', 'pass',
        'Evaluation: every applicable dimension passed of 20 checks', false)] } }).scored.state,
    'pass');


// ── 6d · every listed item is lookupable ───────────────────────────────────
section('any item the instrument lists has a representative in the Guide');
// The complaint that produced this: some rows opened the Guide, some did nothing, and
// nothing on screen explained the difference.
check('a row with no scored dimension is still linkable via its own entry',
    /return p && p\.guide \? \[\{ k: p\.guide\.k/.test(src), true);
check('mechanisms are registered BEFORE anything can be clicked',
    src.indexOf('registerEntries(all)') < src.indexOf("addEventListener('click'"), true);
// Where a mechanism spans several dimensions, those dimensions must be CLICKABLE so a
// reader can move back and forth in the Guide rather than hitting a dead end.
const qdSrc = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'handlers',
    'qualityDimensions.js'), 'utf8');
check('spanned dimensions render as chips, not as prose',
    /function relatedChips/.test(qdSrc) && /qd-rel-chip/.test(qdSrc), true);
// Executed, not grepped: a grep for one exact source line pins an IMPLEMENTATION —
// adding the judge class to the same element broke it while every chip still routed
// correctly. The property is that a chip carries the Guide's class AND its data-dim.
// The harness supplies every collaborator the REAL function needs. A missing one throws
// at call time and kills the run rather than naming a failure — the faithful-stub rule.
const _chipRow = new Function('esc', 'label', 'JUDGE_DIMS', 'decidesInPath',
    'ENTRY_CLASS', 'sharedModule', 'appliesToGuideCls', 'leadHtml', 'kindCount', 'ENTRY_EXCLUDED',
    `${qdSrc.slice(qdSrc.indexOf('function chipRow('), qdSrc.indexOf('\n    }', qdSrc.indexOf('function chipRow(')) + 6)}\nreturn chipRow;`)(
    (x) => String(x), (k) => String(k), new Set(['groundedness']),
    // a judge a gate can consult — the shipped chip row tags it `·decides`
    (k) => k === 'groundedness',
    {}, () => null,    // no property entries and no shared module in this fixture
    (d) => d !== 'groundedness',   // class scope: the judge is outside the opened class
    // the lead word with its definition on hover — stubbed to the bare lead here
    (lead, text) => `<span class="qd-rel-lead">${text || lead}</span>`,
    // the kind-and-count tag beside the lead — not under test here
    () => '',
    // the mechanisms recorded as off a turn's path — none in this fixture
    new Set());
const _chips = _chipRow('graded by', ['grounded_overlap', 'groundedness'], 'grounding_gate');
const _btns = _chips.match(/<button[\s\S]*?>/g) || [];
check('…and the chips route through the Guide\'s OWN data-dim channel',
    _btns.length === 2 && _btns.every((b) => /qd-rel-chip/.test(b) && /data-dim="/.test(b)), true);
// Class scope (legibility follow-up): the chip outside the opened class is marked,
// in words and by class, and the row lead counts what applies.
check('a chip outside the opened class carries the mark', /qd-rel-offclass[^>]*data-dim="groundedness"/.test(_chips), true);
check('…and says so in words', /·not this class/.test(_chips), true);
check('…while the applicable chip is clean', /qd-rel-offclass[^>]*data-dim="grounded_overlap"/.test(_chips), false);
check('…and the row lead counts what applies', /1 of 2 for this class/.test(_chips), true);
check('…and the chips are rendered into the row',
    /\$\{relatedChips\(k\)\}/.test(qdSrc), true);
check('…and are keyboard-reachable',
    /\.qd-rel-chip:focus-visible/.test(qdSrc), true);
// DIM entries are OBJECTS ({label, desc}). Assigning a string broke BOTH lookups
// silently — the label fell back to title-case and the tooltip read "No description
// available" for a mechanism that has one.
check('registered entries use DIM\'s own shape',
    /DIM\[e\.k\] = \{ label: [^}]*desc: /.test(qdSrc), true);
// The dial registers the CURRENT TURN's points; the vocabulary endpoint later serves
// the full registry. Two projections of one source, so this asserts the PROPERTY —
// a registered entry never overwrites a real dimension, and a partial one never
// blocks a richer one — by executing the module. It used to grep for the guard's
// exact source line, which pinned an implementation rather than a behaviour and
// passed while that very guard was discarding the station of every mechanism the
// dial happened to register first.
{
    const w = { addEventListener() {}, removeEventListener() {} };
    const d = {
        readyState: 'complete', addEventListener() {}, querySelectorAll() { return []; },
        getElementById() { return null; }, querySelector() { return null; },
        body: { appendChild() {} }, head: { appendChild() {} }, documentElement: {},
        createElement() {
            return { style: {}, setAttribute() {}, appendChild() {},
                     classList: { add() {}, remove() {} } };
        },
    };
    // The gate's own localStorage stub is REAL (line 34) and the pin assertions below
    // read it back. Loading a module here must not leave a no-op in its place.
    const _ls = globalThis.localStorage;
    globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
    // eslint-disable-next-line no-new-func
    new Function('window', 'document', qdSrc)(w, d);
    globalThis.localStorage = _ls;
    const QD = w.QualityDimensions;
    const realLabel = QD.label('grounded_overlap');
    QD.registerEntries([{ k: 'grounded_overlap', label: 'HIJACKED', desc: 'x' }]);
    check('…and registration never overwrites a real dimension',
        QD.label('grounded_overlap') === realLabel && realLabel !== 'HIJACKED', true);
    // A dial-shaped (station-less) pre-registration must not block the vocabulary's
    // richer entry — the defect that made one partition give two answers.
    QD.registerEntries([{ k: 'zz_mech', desc: 'partial', dims: ['zz_dim'] }]);
    QD.registerEntries([{ k: 'zz_mech', desc: 'partial', dims: ['zz_dim'], station: 'bounded' }]);
    check('…and a partial pre-registration never blocks a richer payload',
        QD.stationOf('zz_dim') === 'bounded', true);
}


// ── 6e · the Assurance Guide: naming, facets, filters, links ───────────────
section('the Assurance Guide — one name, two facets, links both ways');

// The rename must reach every surface. A rename that stops at the rendered string
// leaves the next reader with two names for one thing — the SCORED/JUDGED lesson.
check('the modal is named the Assurance Guide',
    /qd-modal-title">Assurance Guide</.test(qdSrc), true);
check('…and the old name is gone from the rendered surfaces',
    /Quality Dimensions — Guide/.test(qdSrc), false);
const subBlock = (qdSrc.match(/const sub = _guideCtx[\s\S]{0,420}?;/) || [''])[0];
check('…the subtitle no longer claims everything is a score',
    /graded on these signals/.test(subBlock), false);
check('…and says what it actually covers',
    /measure, control and check/.test(subBlock), true);
check('…and the search says what is searchable',
    /Search measures, controls and checks/.test(qdSrc), true);

// Two facets. Class determines the SETTING an entry takes; measurement determines what
// a CLICK gives you. They are independent — that is why they are two facets, not five
// classes — and both must be filterable.
// SIX classes now — MOMENTS joined them. The Guide is the mental model of the runtime,
// and it could explain a GUARANTEE and a MECHANISM but not a MOMENT OF THE TURN, while
// a moment is how the 52 measurements are grouped.
check('the six classes exist, moments included',
    /station: 'Guarantees', stage: 'Moments', measure: 'Measures'/.test(qdSrc)
    && /control: 'Controls'/.test(qdSrc), true);
check('…and a moment says what it is', /A point in the turn/.test(qdSrc), true);
check('…each with a note saying what setting it takes',
    /CLASS_NOTE/.test(qdSrc) && /Tuned by a threshold/.test(qdSrc)
    && /Set by a mode/.test(qdSrc), true);
check('the three measurement values exist',
    /MEASURED_LABEL = \{ atomic:/.test(qdSrc), true);
// The Guide's shelf order, read from source. Used by more than one assertion, so it is
// extracted once rather than re-grepped — a second copy of a fact is what this whole
// subsystem has spent the session removing.
const _norm2 = (src) => ((src.match(/const blocks = \[([^\]]*)\]/) || [])[1] || '')
    .split(',').map((y) => y.trim().replace(/['"]/g, '')).filter(Boolean);

const facetFn = (() => {
    const i = qdSrc.indexOf('function facetBar(inScope)');
    if (i < 0) return '';
    let d = 0;
    for (let j = qdSrc.indexOf('{', i); j < qdSrc.length; j += 1) {
        if (qdSrc[j] === '{') d += 1;
        else if (qdSrc[j] === '}') { d -= 1; if (!d) return qdSrc.slice(i, j + 1); }
    }
    return '';
})();
check('facetBar is extractable (a fixed window silently missed it before)',
    facetFn.length > 0, true);
// The PROPERTY, not the list. This pinned the exact class names, so ADDING a class broke
// it while what it stands for held perfectly — the 16th implementation-pinning grep in
// this subsystem. What matters is that every class the Guide SHELVES is also filterable:
// a class you can see but not filter is a gap, and a class you can filter but never see
// is a dead pill.
const _shelved = (qdSrc.match(/const blocks = \[([^\]]*)\]/) || [])[1] || '';
const _facetable = (qdSrc.match(/const cls = \[([^\]]*)\]/) || [])[1] || '';
const _norm = (x) => x.split(',').map((y) => y.trim().replace(/['"]/g, '')).filter(Boolean).sort();
check('every class the Guide shelves is also filterable',
    JSON.stringify(_norm(_shelved)), JSON.stringify(_norm(_facetable)));
check('…and the list is non-trivial', _norm(_shelved).length >= 6, true);
check('both facets render pills',
    /pill\('class'/.test(facetFn) && /pill\('measured'/.test(facetFn), true);
// The MOMENT facet — the Guide could be filtered by five things and not by the one it
// is actually organised around.
check('…and the moment facet exists, guarded on the served model landing',
    /pill\('stage'/.test(facetFn) && /stagesReady\(\)/.test(facetFn), true);
check('…and both are rendered into the bar',
    /\$\{cls\}/.test(facetFn) && /\$\{meas\}/.test(facetFn), true);
check('…filters NARROW rather than restrict by default',
    /!_fClass\.size \|\| _fClass\.has/.test(qdSrc), true);
check('…and reset on each open, so a past narrowing cannot hide rows silently',
    /_fClass = new Set\(\);\s*\n\s*_fMeasured = new Set\(\);/.test(qdSrc), true);
check('…with a clear control once anything is on',
    /qd-facet-clear/.test(qdSrc), true);
// Counts shift with scope and with each other; a pill reading "Controls 9" beside a
// scope showing 4 is a filter lying about what it would give you.
check('facet counts are recomputed, not fixed',
    /facetWrap\.innerHTML = facetBar\(scopeKeys\(\)\)/.test(qdSrc), true);

// Links both ways. A forward-only link is a dead end one hop later — the same complaint
// as a row with no link, just deferred.
// Asserted as a PROPERTY, by executing the renderer. These greps used to pin the exact
// `chipRow('measured by', RELATED[k])` source line, so renaming a label that was
// actively misleading readers broke two assertions while the property they name held
// perfectly — the same implementation-pinning shape as the registration grep above.
{
    const w2 = { addEventListener() {}, removeEventListener() {} };
    const d2 = {
        readyState: 'complete', addEventListener() {}, querySelectorAll() { return []; },
        getElementById() { return null; }, querySelector() { return null; },
        body: { appendChild() {} }, head: { appendChild() {} }, documentElement: {},
        createElement() {
            return { style: {}, setAttribute() {}, appendChild() {},
                     classList: { add() {}, remove() {} } };
        },
    };
    const _ls2 = globalThis.localStorage;
    globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
    // eslint-disable-next-line no-new-func
    new Function('window', 'document', qdSrc)(w2, d2);
    globalThis.localStorage = _ls2;
    const QD2 = w2.QualityDimensions;
    QD2.registerEntries([{ k: 'mech_a', desc: 'x', station: 'grounded',
                           dims: ['grounded_overlap', 'numeric_fidelity'] }]);
    const fwd = QD2.relatedChips('mech_a');
    check('a mechanism links out to the dimensions that measure it',
        fwd.includes('data-dim="grounded_overlap"') && fwd.includes('data-dim="numeric_fidelity"'), true);
    const back = QD2.relatedChips('grounded_overlap');
    check('…and a dimension links back to what relies on it',
        back.includes('data-dim="mech_a"'), true);
}
check('…the reverse index is built as entries register',
    /USED_BY\[d\] = USED_BY\[d\] \|\| \[\]/.test(qdSrc), true);

// Completeness must not depend on the entry point: the Guide opened from the Quality
// card and from the dial are the same question.
check('the vocabulary is served, not inferred from whatever rendered',
    /assurance\/vocabulary/.test(qdSrc), true);
check('…loaded without blocking the Guide on a round trip',
    /ensureVocabulary\(\)\.then/.test(qdSrc), true);

// Link-back: following a chip must LAND on the entry. A target the current scope or
// filter hides is a broken link wearing a working one's clothes.
// The landing is now the ENTRY scope: opened at a key, the Guide shows that key alone,
// whatever the turn's rows hold — and `_scopeKeys` falls back to everything while the
// vocabulary defining the key is still landing, so the modal can never open empty.
check('following a link lands on the entry (the entry scope), never on a scoped list without it',
    /_guideScope = _guideEntry \? 'entry' : \(_guideCtx \? 'context' : 'all'\)/.test(qdSrc), true);
check('…and the entry scope falls back to everything while the entry is still unknown',
    /_guideScope === 'entry' && _guideEntry && DIM\[_guideEntry\]\) return \[_guideEntry\]/.test(qdSrc)
    && /return Object\.keys\(DIM\);\s*\n\s*\}/.test(qdSrc), true);
check('…and filters reset on open, so a stale narrowing cannot hide the target',
    /_fClass = new Set\(\);/.test(qdSrc), true);
check('…chips carry the Guide\'s own data-dim, so both directions use one channel',
    _btns.every((b) => /data-dim="/.test(b)) && !/data-dim-open|qd-open-dim/.test(qdSrc), true);


// ── 6f · the Live Status trust cards reach the Guide too ───────────────────
section('every Live Status card naming a mechanism links to the Guide');
// The rule applies to EVERY surface that lists a mechanism, not just the dial. The trust
// cards name "Grounding", "Guardrail", "Honesty", "Data lane" — and none of them linked,
// so a reader could look up `answer_groundedness` but not the gate that uses it.
const uiSrc = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'ui.js'), 'utf8');
for (const [card, key] of [['GroundingGate', "_mechLink('grounding_gate')"],
                           ['Guardrail', "_mechLink(_mechKey(details, 'guardrail'))"],
                           ['Obda', "_mechLink('governed_data')"],
                           ['Honesty', "_mechLink('honesty_gate')"]]) {
    check(`the ${card} card's headline links to the Guide`, uiSrc.includes(key), true);
}
// The guardrail card renders BOTH directions; linking both to the outbound entry would
// send an inbound finding to the wrong mechanism.
check('…and the guardrail card resolves its direction',
    /direction\) === 'inbound' \? 'inbound_guard' : 'outbound_guard'/.test(uiSrc), true);
// One affordance, one channel: the same `data-dim` the dimension chips use.
check('…through the same data-dim channel as a dimension chip',
    /_mechLink = \(key\) => [\s\S]{0,120}?_dimAttrs\(key\)/.test(uiSrc), true);
check('…and is keyboard-reachable',
    /\.status-kv-mech:focus-visible/.test(css), true);


// ── 6g · filter by status, so a count becomes a link ───────────────────────
section('the third facet — how it scored on THIS turn');
// The Guide already showed a Healthy/Review/Danger/Not-applicable legend: a key with no
// control. Making it filterable is what lets "2 of 20 below the line" be a link to those
// two rather than a number the reader has to go hunting behind.
// Assert the predicate APPLIES it — the first version checked only that `_fBand`
// existed, so deleting the clause that uses it left the gate green and the pills inert.
// The window grew when the ENGINE facet joined (operator request): four facets now
// compose in one predicate, and the extraction must span all of them.
const passesFn = (qdSrc.match(/const passes = \(k\) =>[\s\S]{0,800}?;/) || [''])[0];
check('status is a filterable facet', /pill\('band', b, BAND_LABEL\[b\]/.test(qdSrc), true);
check('…and the row filter actually applies it',
    /_fBand\.size \|\| _fBand\.has/.test(passesFn), true);
check('…alongside the others, so all four compose',
    /_fClass\.has/.test(passesFn) && /_fMeasured\.has/.test(passesFn)
    && /_fEngine\.has/.test(passesFn), true);
check('…and the engine facet is cleared with the rest',
    /_fEngine\.clear\(\)/.test(qdSrc) && /_fEngine = new Set\(\)/.test(qdSrc), true);
check('…using the legend\'s own four words',
    /BAND_LABEL = \{ danger: 'Danger', review: 'Review', healthy: 'Healthy'/.test(qdSrc), true);
// A band is a reading; with no turn in context there is nothing to read, so the group
// must not appear at all rather than showing four empty pills.
check('…offered only where a band can mean something',
    /const bands = _guideCtx \?/.test(qdSrc), true);
check('…and cleared by the same Clear control',
    /_fClass\.clear\(\); _fMeasured\.clear\(\); _fBand\.clear\(\);/.test(qdSrc), true);
check('…and reset on open like the others', /_fBand = new Set\(\);/.test(qdSrc), true);

// The link itself: a caller pre-sets the narrowing.
check('a caller can open the Guide already narrowed',
    /data-dim-filter/.test(qdSrc) && /_guideCtx\.bands\.forEach/.test(qdSrc), true);
// Rule 9.6 decides this: opening pre-narrowed IS filtering. It hides the denominator —
// "2 of 20" loses the 20 — and leaves a filter on that the reader never set, which is the
// same defect as a summary reading "quiet" beside an amber segment.
check('…and never carries a pre-set narrowing',
    /data-dim-filter/.test(src), false);
// ONE tooltip per row. A guided row gets the Guide's own hover (via `data-dim`); adding
// `u-tip` as well showed two boxes at once, overlapping and saying different things,
// with neither obviously the answer.
check('a guided row carries only the Guide\'s tooltip',
    /guided \? '' : ' data-tip="Find this step in the trace below"'/.test(src), true);
check('…and does not also get the u-tip class',
    /guided \? ' ad-gate-guided' : ' u-tip'/.test(src), true);


// ── 6h · one segment, and the pin ──────────────────────────────────────────
section('the assurance band — one segment on the header ground, with a pin');
// The reading and the verdict are two halves of one statement about the turn. As two
// floating cards they read as unrelated findings.
check('the dial builds a band rather than a bare card', /id = 'assurance-band'/.test(src), true);
check('…and the band ADOPTS the Quality card, whichever rendered first',
    /#trajectory-eval-overlay'\)[\s\S]{0,120}?band\.appendChild\(quality\)/.test(src), true);
check('…and ui.js joins the band when it already exists',
    /if \(_band\) _band\.appendChild\(wrap\);/.test(uiSrc), true);
// Same ground as CONFIGURATION and FILTER — those sit on the header's --gradient-card.
check('the band uses the header\'s own ground',
    /#assurance-band\s*\{[^}]*background:\s*var\(--gradient-card\)/.test(css), true);
// A fine rule INSIDE (two halves of one thing) and a heavier one closing it off from the
// events (a different kind of thing).
check('a fine separator divides the two halves',
    /#assurance-band > #trajectory-eval-overlay\s*\{[^}]*border-top:\s*1px solid var\(--border-secondary\)/
        .test(css), true);
check('…and a heavier rule closes the segment before the events',
    /#assurance-band\s*\{[^}]*border-bottom:\s*1px solid var\(--border-primary\)/.test(css), true);

// The pin: ON by default (operator decision — the dial is the panel's headline
// instrument; unpinned-by-default scrolled the settled reading away and most
// readers never saw it), persisted, and a LOCKED preference wins both ways.
// Two scopes, the platform's own idiom (Context Modes: "hold Alt for a single query,
// Shift+Alt to lock"). One gesture serving both meant pinning once silently changed
// every future turn — a preference set by accident.
check('the pin defaults to ON (the built-in default)', AD.pinned(), true);
check('…and nothing is locked yet', AD.hasDefault(), false);
AD.setPinned(true);
check('a single click pins for NOW', AD.pinned(), true);
check('…and locks nothing', AD.hasDefault(), false);
AD.lockPin();
check('a double click locks the current state as the default', AD.hasDefault(), true);
check('…and that default is the state that was showing', AD.pinnedDefault(), true);
// A toggle you cannot undo with the same gesture is a trap, not a shortcut. The first
// version only ever committed, so a second double click did nothing at all.
AD.lockPin();
check('a SECOND double click unlocks it', AD.hasDefault(), false);
check('…while leaving the panel exactly as it is', AD.pinned(), true);
AD.setPinned(false);
check('…and a later single click still locks nothing', AD.hasDefault(), false);
check('the choice is persisted, not per-page', /tda_assurance_pinned/.test(src), true);
// A double click must have a VISIBLE effect, or locked and merely-on are the same thing
// on screen and the gesture reads as doing nothing.
// Assert the rule that actually paints it. The first version matched the `::after`
// rule too, so renaming the base selector left the gate green and the state invisible.
check('locked looks different from pinned-for-now',
    /classList\.toggle\('locked', on && isDefault\)/.test(src)
    && /\.ad-pin\.locked\s*\{[^}]*box-shadow:\s*inset/.test(css), true);
// `.u-tip` renders its tooltip through `::after`. Giving a u-tip host its own `::after`
// overrides the tooltip's background and radius — it painted as a large orange blob on
// hover. No u-tip host may own either pseudo-element.
{
    const hosts = [...css.matchAll(/\.([a-z-]+)(?:\.[a-z-]+)?::(?:after|before)\s*\{/g)]
        .map((m) => m[1]);
    const utipHosts = ['ad-pin', 'ad-gate-linked', 'status-kv-mech'];
    check('no u-tip host declares its own pseudo-element',
        hosts.filter((h) => utipHosts.includes(h)), []);
}
check('…and the tip says which scope you are in, and the way out',
    /Double-click to unlock it/.test(src)
    && /Double-click to make it the default/.test(src), true);
// The 240ms disambiguation the KG inspector already uses — without it a double click is
// also processed as two toggles and cancels itself out.
check('a double click is not also processed as two toggles',
    /if \(timer\) \{ clearTimeout\(timer\); timer = null; return; \}/.test(src), true);
check('…pinning makes the band sticky',
    /#assurance-band\.ad-band-pinned\s*\{[^}]*position:\s*sticky/.test(css), true);
// Pinning changes BEHAVIOUR, not appearance: an opaque swap made the band look like a
// different component the moment it was pinned. It keeps the header ground it already
// had, and legibility over scrolling content comes from a backdrop blur instead.
const pinRule = (css.match(/#assurance-band\.ad-band-pinned\s*\{[^}]*\}/) || [''])[0];
check('…without changing its appearance', /background:\s*var\(--card-bg\)/.test(pinRule), false);
check('…staying legible over scrolling content via a backdrop blur',
    /backdrop-filter:\s*blur/.test(pinRule), true);
check('…prefixed for Safari', /-webkit-backdrop-filter/.test(pinRule), true);
// Legibility is the floor, the transparency is the preference — a browser without blur
// support gets the opaque ground rather than text reading straight through.
check('…and falls back to opaque where blur is unsupported',
    /@supports not \(\(backdrop-filter[\s\S]{0,220}?background:\s*var\(--card-bg\)/.test(css), true);
check('…and the control is keyboard-reachable',
    /\.ad-pin:focus-visible/.test(css), true);
// The variants describe ALIGNMENT, not direction: `u-tip-left` grows RIGHTWARD. The pin
// sits at the band's right edge, so it needs `u-tip-right` to grow inward — the other
// way pushed it off the panel, where a scroll container clips it and a pseudo-element
// cannot escape.
check('the pin\'s tooltip grows INWARD from the right edge',
    /class="ad-pin u-tip u-tip-right"/.test(src), true);
check('…and no tooltip can be wider than the viewport',
    /max-width:\s*min\(300px, calc\(100vw - 32px\)\)/.test(css), true);


// ── 6i · the live turn ─────────────────────────────────────────────────────
section('the dial exists while the turn runs, and stays honest doing it');
// Before this it was mounted by the post-turn overlay, so pinning it pinned an empty
// space until the turn was over.
check('the dial mounts at turn START', typeof AD.begin, 'function');
check('…updates as each gate reports', typeof AD.note, 'function');
check('…and settles when the stream ends', typeof AD.settle, 'function');

// `pending` is a turn still running, NOT attention — saying stations "need a look"
// while they are merely waiting would open every turn in alarm.
const allPending = AD.stations({ by_station: Object.fromEntries(AD.ORDER.map((k) =>
    [k, [P(k, k, k, 'pending', 'waiting')]])) });
check('a turn that just started says so',
    /still running/.test(AD.summarise(allPending, {}).text), true);
check('…and does not read as attention',
    /need a look|worth a look/.test(AD.summarise(allPending, {}).text), false);
check('…while the header counts what is outstanding',
    // PROP-002: ONE word for pending everywhere — "not in yet" (the header said
    // "still coming in" while the chip said "not in yet" for the same state).
    /8 not in yet/.test(AD.routing(allPending)), true);

// Partial progress must show progress, not a verdict.
const partial = AD.stations({ by_station: Object.fromEntries(AD.ORDER.map((k) =>
    [k, [P(k, k, k, k === 'guarded' ? 'pass' : 'pending', 'd')]])) });
check('partial progress is reported as progress',
    /1 of 8 reported so far/.test(AD.summarise(partial, {}).text), true);

// The live path must READ the stamp, not invent a verdict — it is the same object the
// registry reads server-side, which is what keeps the two from disagreeing.
check('the live path reads the gate\'s own stamp',
    /details\.acted === true[\s\S]{0,160}?details\.action/.test(src), true);
// The guards that predate begin() made inject() bail on the LIVE dial, so the payload
// never replaced the pending model and the turn ended stuck on "8 still coming in".
check('inject does not bail merely because a dial element exists',
    /if \(host\.querySelector\('#assurance-dial'\)\) return;\s*\/\/ already mounted/.test(src), false);
// Sep 2026: the guard gained `force` — a stream-end settle renders this turn's payload
// with JUDGED honestly pending, and the score that lands later must be allowed to
// re-render the SAME turn; without force, the settle would have become a lock.
check('…it bails only on an already-rendered, still-visible payload — unless forced',
    (src.match(/if \(!o\.force && _isOnScreen\(host, key\)\) return true;/g)
        || []).length, 2);
check('…and marks it settled when it renders', /_settled = `\$\{sessionId\}:\$\{turnId\}`;/.test(src), true);
// Mirror problem: the previous turn's dial is still in the DOM when a new turn starts.
// Sep 2026: begin() ALWAYS starts fresh. The caller runs it once per stream, so a
// `_live` guard could only ever match a PREVIOUS turn's model — the exact model a lost
// settle leaves behind — and bailing on it mounted no dial at all for the new turn.
check('a new turn replaces the previous turn\'s dial rather than bailing on it',
    /if \(_live && _live\.sid === String\(sessionId\)\) return;/.test(src), false);
check('…the previous band is removed and the live model restarted',
    /function begin\(sessionId, turnId\)[\s\S]{0,1200}stale\.remove\(\)[\s\S]{0,200}_settled = null;[\s\S]{0,120}_live = \{ sid: String\(sessionId\)/.test(src), true);
check('…and an authoritative payload SUPERSEDES it, never merges',
    /_live = null;[\s\S]{0,120}?_mount\(host, sessionId, turnId, td\.assurance\)/.test(src), true);
// Merging two derivations of one fact is how they drift; replacing means the live view
// can only ever have been an earlier, coarser rendering of the same stamps.
check('…through ONE mount shared by the live start and the final reading',
    (src.match(/function _mount\(/g) || []).length, 1);

// A turn that dies mid-flight must not leave stations promising a reading.
check('settle turns pending into "not recorded", never a pass',
    /state: 'unknown', word: 'not recorded'/.test(src), true);
// Concurrency: a reading must never be painted onto the wrong session.
// EVERY live entry point must guard on the session — note(), phase(), and settle() (begin
// starts the live model, the rest mutate it). Asserting the pattern exists anywhere let a
// mutation strip one while another kept the gate green, so it counts them.
check('every live entry point is session-guarded',
    (src.match(/String\(sessionId\) !== _live\.sid/g) || []).length, 3);

// ── the stream's OWN end settles the dial (Sep 2026) ──────────────────────────
// Before: only Stop called settle(); a normal completion waited on turn_score_complete
// alone — which rides the notification channel once the stream has closed, and a single
// lost delivery left the live model (pending words + pulsing phase dot) on screen until
// a page refresh. Prose is not code: every check here runs over comment-stripped source.
const adCode = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
const ehCode = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'eventHandlers.js'), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
const nfCode = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'notifications.js'), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
check('complete() is a real, exported entry point', typeof AD.complete, 'function');
check('the stream returns the turn it DELIVERED',
    /deliveredTurnId = eventData\.turn_id;[\s\S]*return deliveredTurnId;/.test(ehCode), true);
check('…and the stream\'s own end completes a delivered turn',
    /finally \{[\s\S]{0,600}AssuranceDial\.complete\(originSessionId, deliveredTurnId\)/.test(ehCode), true);
check('…or settles an undelivered one (error/cancel → "not recorded", never a pass)',
    /AssuranceDial\.complete\(originSessionId, deliveredTurnId\);[\s\S]{0,120}else if[\s\S]{0,80}AssuranceDial\.settle\(originSessionId\)/.test(ehCode), true);
check('complete renders the persisted reading with force + retry (the turn persists after final_answer)',
    /async function complete\(sessionId, turnId\)[\s\S]{0,400}inject\(sessionId, turnId, null, \{ force: true, retry: true \}\)/.test(adCode), true);
check('…falls back to settle when nothing could be rendered (never leaves the live model behind)',
    /async function complete\(sessionId, turnId\)[\s\S]{0,700}if \(!ok\) \{ settle\(sessionId\); return; \}/.test(adCode), true);
check('…and arms the bounded score fallback',
    /async function complete\(sessionId, turnId\)[\s\S]{0,900}_armScoreFallback\(sessionId, turnId\)/.test(adCode), true);
check('the fallback re-fetches only while no score has landed and the turn is still the one on screen',
    /if \(_scored\.has\(key\) \|\| _settled !== key\) return;/.test(adCode), true);
check('…on a bounded schedule that outlives the registry\'s pending window',
    /SCORE_FALLBACK_MS = \[45000, 200000\]/.test(adCode), true);
check('the stream score path FORCES the re-render and marks the turn scored',
    /AssuranceDial\.inject\(originSessionId, d\.turn_id,[\s\S]{0,260}\{ force: true, scored: true \}/.test(ehCode), true);
check('the notification score path does the same',
    /AssuranceDial\.inject\(p\.session_id, p\.turn_id,[\s\S]{0,120}\{ force: true, scored: true \}/.test(nfCode), true);
check('a new turn clears a previous turn\'s fallback timers',
    /function begin\(sessionId, turnId\)[\s\S]{0,600}_clearScoreFallback\(\);/.test(adCode), true);

// Only mechanisms that actually STREAM are lit live; SIGNED and TRUSTED are computed at
// read time, so they stay pending until the payload lands — which is the truth, not a gap.
// `retrieval` is a mid-turn PROGRESS mechanism (the executor streams an assurance_progress
// when knowledge is retrieved) — it moves the instrument, never renders a verdict.
const liveBlock = (src.match(/const LIVE_STATION = \{([\s\S]*?)\};/) || [, ''])[1];
check('only streaming mechanisms have a live station',
    [...liveBlock.matchAll(/^\s*(\w+):/gm)].map((m) => m[1]).sort(),
    ['action_record', 'attachment_sources', 'compute_lineage', 'grounding_gate', 'guardrail', 'honesty', 'llm_recovery', 'obda', 'outcome_loop', 'retrieval', 'tool_surface', 'turn_budget', 'turn_score_complete']);   // S3: number lineage streams too; S4: a recovery streams too; S5: a budget skip streams too; S6: the surface streams too; S7: a revision streams too; S9: the attachments-read step streams too; ACT: an act on a signed action streams too
check('ACT: an action record routes to BOUNDED (the agent wrote only through a signed contract)', /action_record: 'bounded'/.test(src), true);
check('S9: attachment sources route to GROUNDED (what the platform read is the gate\'s evidence)', /attachment_sources: 'grounded'/.test(src), true);
check('S4: a recovery routes to SIGNED (a fallback is declared deployment drift)', /llm_recovery: 'signed'/.test(src), true);
check('S5: the turn budget routes to BOUNDED (a declared boundary on what one turn may spend)', /turn_budget: 'bounded'/.test(src), true);
check('S6: the tool surface routes to BOUNDED (what the model was shown, what it could never reach)', /tool_surface: 'bounded'/.test(src), true);
check('S7: the outcome loop routes to GROUNDED (the gate\'s own rubric, one step earlier)', /outcome_loop: 'grounded'/.test(src), true);
check('…and SIGNED / TRUSTED are deliberately absent (computed at read time)',
    /SIGNED and TRUSTED are computed at read/.test(src), true);
// The mid-turn retrieval cue lands on GROUNDED and reads as its own PROGRESS label —
// "GROUNDED's machinery begins to have something to say", never a verdict.
check('retrieval progress routes to GROUNDED', /retrieval: 'grounded'/.test(src), true);
check('…and carries a distinct progress label', /type === 'retrieval' \? 'Retrieval'/.test(src), true);

// The live PHASE cue — what the agent is doing right now, so a fast turn (a bare Ideate
// turn above all) is not a blank ring until the end. It is progress, NEVER a verdict.
check('phase() is a public entry point', /placeScorecard, render, inject, begin, note, phase, settle/.test(src), true);
check('PHASE_LABEL names the turn\'s phases (reasoning is the long pole)',
    /reasoning: 'Reasoning/.test(src) && /screening:/.test(src) && /context:/.test(src), true);
// It must never paint over a real reading — only a still-pending / already-phase summary.
check('phase() only overwrites a pending summary (never a landed reading)',
    /pending\|not in yet\|still running\|waiting/.test(src)
    && /sub\.classList\.contains\('ad-phase-live'\)/.test(src), true);
check('phase() colours no station — it touches only the summary line',
    /_live\.phase = key/.test(src) && !/state = 'pass'[\s\S]{0,120}_live\.phase/.test(src), true);


// ── 6j · a station cannot ignore what measures it ──────────────────────────
section('a station reflects its dimensions, not only its gates');
// GROUNDED read "quiet" from its in-path gates while `grounded_overlap` — a dimension
// that measures grounding — sat as a gating failure directly beneath it. Both facts are
// true (the gate passed; the measurement did not), but a station that NAMES its measures
// cannot then ignore them.
//
// The fixtures put measurements where the REGISTRY puts them — on the station, in
// `assurance.measures` — not on a mechanism. A dimension reaches its station by
// ownership lookup, so it cannot be lost when the mechanism that owns it does not apply
// to the agent's class. Each record carries `st` (the contribution) and `bst` (the band
// as a state, for its own chip), both resolved server-side.
const gate = (state = 'pass', extra = {}) => ({
    key: 'grounding_gate', label: 'Grounding gate', station: 'grounded', state,
    detail: 'in scope', acted: false, dims: [], ...extra });
/** A measurement record shaped exactly as `allocate_measurements` emits it. */
const meas = (k, b, st = null) => ({ k, b, st: st || BAND_STATE[b] || 'na',
                                     bst: BAND_STATE[b] || 'na' });

const gateCleanDimFailed = { by_station: { grounded: [gate()] },
    measures: { grounded: [meas('answer_groundedness', 'healthy'),
                           meas('grounded_overlap', 'danger')] } };
check('a failed dimension raises its station',
    AD.stations(gateCleanDimFailed).grounded.state, 'danger');
// …and a healthy one does not drag it down.
check('a healthy dimension leaves it quiet',
    AD.stations({ by_station: { grounded: [gate()] },
                  measures: { grounded: [meas('answer_groundedness', 'healthy')] } })
      .grounded.state, 'pass');
// `na` dimensions must not alarm — an abstained measure is not a finding.
check('an abstained dimension does not raise the station',
    AD.stations({ by_station: { grounded: [gate()] },
                  measures: { grounded: [meas('knowledge_freshness', 'na')] } })
      .grounded.state, 'pass');

// ── G2 · THE MIDDLE BAND ────────────────────────────────────────────────────
// `review` is the ONE value where the band and state vocabularies genuinely differ, and
// for a whole release nothing exercised it: every dims fixture here used `healthy` or
// `danger`, which coincide or were special-cased. So an amber measurement silently could
// not raise its station — GROUNDED read "quiet" above its own `context_utilization 0.56`
// — while 387 assertions passed. Every station is checked, because the defect lived in
// shared code and a single-station fixture would have missed it just as thoroughly.
for (const st of AD.ORDER) {
    const payload = {
        by_station: { [st]: [{ key: `${st}_mech`, label: st, station: st, state: 'pass',
                               detail: 'd', acted: false, dims: [] }] },
        measures: { [st]: [meas('context_utilization', 'review')] },
    };
    check(`a review-band measurement raises ${st} to warn`,
        AD.stations(payload)[st].state, 'warn');
}
// …and it must be NAMED, not merely counted: a station that goes amber without saying
// which measurement did it is a composite with invisible arithmetic (Rule 4.3).
{
    const p = { by_station: { grounded: [gate()] },
                measures: { grounded: [meas('context_utilization', 'review')] } };
    const reading = AD.stationReading(AD.stations(p).grounded, 'grounded');
    check('…and the reading names the measurement that did it',
        /context_utilization/.test(reading), true);
    // The verb follows the state. "is failing" was written when only `danger` could
    // reach here; a review band asks for a look, it does not convict.
    check('…without calling a review band a failure',
        /is in the review band/.test(reading) && !/failing/.test(reading), true);
}

// ── G1 · THE PARTITION, AS THE RENDERER SEES IT ─────────────────────────────
// A measurement allocated to one station must move that station and NO other. Behavioural
// on purpose: this assertion used to be a grep for the exact source line that read
// `p.dims`, which pinned an implementation rather than the property — and would have
// passed unchanged through the very rewrite that moved allocation off the mechanisms.
{
    const all = { by_station: Object.fromEntries(AD.ORDER.map((k) => [k,
                    [{ key: `${k}_m`, label: k, station: k, state: 'pass', detail: 'd',
                       acted: false, dims: [] }]])),
                  measures: { grounded: [meas('grounded_overlap', 'danger')] } };
    const st = AD.stations(all);
    check('an allocated measurement moves its own station', st.grounded.state, 'danger');
    check('…and no other station', AD.ORDER.filter((k) => k !== 'grounded')
        .every((k) => st[k].state === 'pass'), true);
}

// ── G3 · NO SURFACE CONVERTS A BAND ─────────────────────────────────────────
// The bug was three inline `b === 'healthy' ? 'pass' : b` aliases: a hand-written copy of
// a vocabulary declared once, each omitting the same value. The renderer now receives
// contributions already in the state vocabulary and converts nothing.
check('the dial declares no band→state alias of its own',
    /['"]healthy['"]\s*\?\s*['"]pass['"]/.test(src.replace(/\/\/[^\n]*/g, '')), false);
check('…and reads the resolved contribution instead',
    /RANK\[d\.st\]/.test(src), true);


// ── 6k · motion carries information, or it is not there ────────────────────
section('the running dial — animated only where something is true');
// Two facts are true while a turn runs: a station has not reported, and one just did.
// Pending is NOT attention. Amber means "worth a look"; using it for "not yet" made a
// turn start read as eight warnings — overloading the strongest signal the instrument
// has, which is the meaning-loss class this whole grammar exists to prevent.
const pendRule = (css.match(/\.ad-seg-pending\s*\{[^}]*\}/) || [''])[0];
check('pending is outside the attention channel',
    /--trust-(warn|danger|pass)/.test(pendRule), false);
check('…and is told apart from `na` by density and motion, not colour',
    /stroke-dasharray/.test(pendRule) && /animation/.test(pendRule), true);
for (const el of ['ad-dot-pending', 'ad-ico-pending', 'ad-chip-pending']) {
    const r = (css.match(new RegExp(`\\.${el}\\s*\\{[^}]*\\}`)) || [''])[0];
    // The rule must EXIST (a vacuous grep passed when the tick→dot rename left the
    // old selector matching nothing) and stay off the attention tokens.
    check(`${el} rule exists`, r.length > 0, true);
    check(`${el} is neutral too`, /--trust-(warn|danger|pass)/.test(r), false);
}
check('waiting is animated', /@keyframes ad-waiting\b/.test(css)
    && /\.ad-seg-pending\s*\{[^}]*animation:\s*ad-waiting/.test(css), true);
// Indeterminate BY CONSTRUCTION: a dash that travels at a constant rate cannot imply a
// progress fraction the dial does not know. A filling arc would be a claim.
// Indeterminate BY CONSTRUCTION: the dash travels at a constant rate and returns to
// where it began. A keyframe that ended at a fixed fill would be a progress claim the
// dial cannot support.
const waitKf = (css.match(/@keyframes ad-waiting \{[\s\S]*?\n\}/) || [''])[0];
check('…indeterminately, so it claims no progress it cannot know',
    /stroke-dashoffset/.test(waitKf) && !/width|scale/.test(waitKf), true);
// The sweep must cover ONLY what is outstanding, so its extent is the remaining work.
check('the sweep is staggered by position, so it chases round the ring',
    /animation-delay:\$\{\(i \* 0\.16\)/.test(src), true);
check('…and only a pending segment is staggered — a settled one is still',
    /d\.state === 'pending'\s*\n?\s*\? ` style="animation-delay/.test(src), true);
check('arrival gets one shot, on the segment AND its icon',
    /\.ad-st\.ad-arrived \.ad-seg\s*\{[^}]*ad-arrive-seg/.test(css)
    && /\.ad-st\.ad-arrived \.ad-ico\s*\{[^}]*ad-arrive-ico/.test(css), true);
check('…fired at the moment the station lands, then removed',
    /classList\.add\('ad-arrived'\)[\s\S]{0,120}?remove\('ad-arrived'\)/.test(src), true);
// A settled station must be STILL. An instrument that keeps moving after it is done
// reads as busy rather than finished, and trains the eye to ignore it.
for (const settled of ['ad-seg-pass', 'ad-seg-warn', 'ad-seg-danger', 'ad-seg-na']) {
    const rule = (css.match(new RegExp(`\\.${settled}\\s*\\{[^}]*\\}`)) || [''])[0];
    check(`a settled segment is still (${settled})`, /animation:/.test(rule), false);
}
// Motion is the accent, never the signal: with it reduced the dial must still read.
check('every animation is disabled under prefers-reduced-motion',
    ['ad-seg-pending', 'ad-dot-pending', 'ad-sub-changed'].every((c) =>
        new RegExp(`prefers-reduced-motion[\\s\\S]*?\\.${c}`).test(css)), true);


// ── 6l · a card may not contradict its own evidence ────────────────────────
section('no trust card states a headline its own rows refute');
// In observe mode the grounding decision is `pass` even when nothing was supported —
// the gate measured and chose not to act. Labelling from the decision alone printed a
// green "Corpus-supported" directly above "0/1 claim(s) supported".
check('the grounding headline reflects the FINDING, not only the decision',
    /_unsupported = \(details\.unsupported_claims \|\| \[\]\)\.length/.test(uiSrc), true);
check('…so unsupported claims cannot read green',
    /decision === 'annotate' \|\| _unsupported \? 'warn'/.test(uiSrc), true);
// It must also say WHY nothing was disclosed. "observed, not withheld" was printed for
// every pass and read as observe mode on an agent set to disclose (prod 2c24e2a1 turn 1),
// when the real reason was the trigger's threshold. The two reasons are distinct labels.
check('…and the label names why it was not disclosed: observe mode',
    /unsupported — observe mode, not disclosed/.test(uiSrc), true);
check('…or the trigger threshold, never a blanket "observed"',
    /unsupported — below the trigger threshold, not disclosed/.test(uiSrc)
      && !/observed, not withheld/.test(uiSrc), true);


// ── 6m · the same card is filed the same way, live and on reload ───────────
section('a trust card does not change category between live and reload');
// Live, a grounding verdict arrives on `interactive` and routes to `knowledge`. On
// reload the same verdict replays out of `system_events`, whose source short-circuits
// before the type is consulted — so it came back as `system` and changed colour. The
// same fact filed two ways is the defect this whole grammar exists to prevent, showing
// up as a colour change nobody could explain.
const catFn = (uiSrc.match(/function getFilterCategory\(source, type\) \{[\s\S]*?\n\}/) || [''])[0];
check('getFilterCategory is extractable', catFn.length > 0, true);
// It delegates the type-only decision to `categoryByType` — the shared authority that
// also overrules the reload path's explicit stamp. Load it, or this exercises a function
// that cannot run.
const authFn = (uiSrc.match(/function categoryByType\(type\) \{[\s\S]*?\n\}/) || [''])[0];
check('categoryByType is extractable', authFn.length > 0, true);
const category = new Function(`${authFn}${catFn}; return getFilterCategory;`)();
for (const t of ['grounding_gate', 'guardrail', 'obda', 'honesty']) {
    const seen = new Set(['interactive', 'rest', 'system', 'lifecycle', 'genie',
                          'conversation_agent'].map((src) => category(src, t)));
    check(`'${t}' has ONE category whatever list replays it`, [...seen], ['assurance']);
}
// …and the type test must come FIRST, or a source short-circuit reaches it again. The
// literal list moved into `categoryByType`; what must stay true is that the delegation
// happens before any source is examined.
check('…because the type is resolved before any source short-circuit',
    catFn.indexOf('categoryByType(type)') < catFn.indexOf("source === 'lifecycle'"), true);


// ── 6n · reload parity, and every card names its guarantee ─────────────────
section('a card renders the same live and on reload, and names its station');
const ehSrc = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'eventHandlers.js'), 'utf8');

// Reload parity is the generalisation of the colour bug: a card synthesised on reload
// out of a different list, or not synthesised at all, is the same fact told two ways.
// Every type emitted live MUST have a reload synthesiser, and vice versa.
const LIVE_TYPES = ['grounding_gate', 'guardrail', 'honesty', 'obda'];
for (const t of LIVE_TYPES) {
    check(`'${t}' is rendered live`, new RegExp(`case '${t}':`).test(uiSrc), true);
}
for (const [t, key] of [['grounding_gate', 'grounding'], ['guardrail', 'guardrail'],
                        ['honesty', 'honesty'], ['guardrail', 'guardrail_inbound']]) {
    check(`'${key}' is synthesised on reload`,
        new RegExp(`turnData\\.${key}\\b`).test(ehSrc), true);
}
// …and the reload synthesiser must use the SAME gate as the live emitter, or a card
// appears on one path and not the other.
check('reload gates match the live gates (acted / decision)',
    /turnData\.grounding\.decision/.test(ehSrc)
    && /turnData\.guardrail\.acted/.test(ehSrc)
    && /turnData\.honesty\.acted/.test(ehSrc)
    && /turnData\.guardrail_inbound\.acted/.test(ehSrc), true);

// Every card names the GUARANTEE it upholds — a finding with no frame leaves the ring
// above it looking like a separate picture rather than the same story at another scale.
check('a card links to its station', /_stationLink\(key\)/.test(uiSrc), true);
check('…and every mechanism the cards render has a station',
    ['grounding_gate', 'honesty_gate', 'inbound_guard', 'outbound_guard',
     'governed_data'].every((k) => new RegExp(`${k}: '`).test(uiSrc)), true);
// The station is itself lookupable — the link would otherwise open a title-cased stub.
check('…which resolves to a station Guide entry',
    /_dimAttrs\(`station_\$\{st\}`\)/.test(uiSrc), true);
check('…and the Guide has a class for guarantees',
    /station: 'Guarantees'/.test(qdSrc), true);
// The PROPERTY — guarantees lead the shelf order — rather than the literal list, which
// broke when a class was added while what it stands for held.
check('…rendered as its own group, and first',
    _norm2(qdSrc)[0], 'station');
// A MOMENT is lookupable too — the shelf header and the per-check chip both route to it,
// so neither is a dead end.
check('a moment resolves to its own Guide entry',
    /data-dim="stage_\$\{esc\(g\.stage\)\}"/.test(uiSrc), true);


// ── 7b · the filter governs the whole surface ────────────────────────────────
// A pill that removed the assurance MESSAGES but left the dial summarising them would be
// a filter that lies about what it filtered — so the band carries the category too.
section('filter — one category for the trust surface, instrument included');

const htmlSrc = fs.readFileSync(path.join(HERE, '..', 'templates', 'index.html'), 'utf8');
const mainCss = fs.readFileSync(path.join(HERE, '..', 'static', 'css', 'main.css'), 'utf8');

// The real function, executed — not grepped. Reload passes a different `source`, and an
// earlier defect was exactly that: a source short-circuit reached before the type did, so
// the same card changed colour between live and reload.
const gfcSrc = uiSrc.match(/function getFilterCategory\(source, type\)\s*\{[\s\S]*?\n\}/)[0];
// It delegates the type-only decision to the shared authority — load that too, or the
// gate exercises a function that cannot run.
const cbtSrc = uiSrc.match(/function categoryByType\(type\) \{[\s\S]*?\n\}/)[0];
const getFilterCategory = new Function(`${cbtSrc}${gfcSrc}; return getFilterCategory;`)();

const TRUST_TYPES = ['grounding_gate', 'guardrail', 'honesty', 'obda',
                     'judge_evaluation_complete'];
for (const t of TRUST_TYPES) {
    // Every source a card can arrive under. Parity across them IS the reload-hardening.
    const seen = new Set(['interactive', 'system', 'genie', 'conversation', undefined]
        .map((src) => getFilterCategory(src, t)));
    check(`${t} → assurance, from every source`, [...seen], ['assurance']);
}
// The COLOUR comes from getEventCategory, not getFilterCategory. Testing the filter
// function proved the card would be filtered correctly and said nothing about how it was
// painted — so a card stayed System yellow on reload while every filter assertion passed.
// Assert the function the stripe actually reads, including the unstamped case.
const OV = uiSrc.match(/const EVENT_CATEGORY_OVERRIDES = \{[\s\S]*?\n\};/)[0];
const GEC = uiSrc.match(/function getEventCategory\(eventType, filterCategory\)\s*\{[\s\S]*?\n\}/)[0];
const getEventCategory = new Function(
    `${OV}${cbtSrc}${gfcSrc}${GEC}; return getEventCategory;`)();

for (const t of TRUST_TYPES) {
    // undefined/null is the reload wrappers' default — the case that shipped yellow.
    for (const stamp of [undefined, null, '']) {
        check(`${t} paints assurance with no stamp (${JSON.stringify(stamp)})`,
            getEventCategory(t, stamp), 'assurance');
    }
    check(`${t} paints what it filters`,
        getEventCategory(t, getFilterCategory('interactive', t)), 'assurance');
}
// Non-trust types must be unaffected: computing a category is not the same as changing one.
check('an unstamped unknown type still lands somewhere sane',
    getEventCategory('some_unknown_event', undefined), 'system');
// THE reload call: eventHandlers replays trust verdicts out of `system_events` and passes
// 'system' explicitly. A computed FALLBACK still lost to that, so the card stayed yellow
// after a plan reload even once the fallback was right. The type must be the authority.
for (const t of TRUST_TYPES) {
    for (const forced of ['system', 'knowledge', 'agent', 'coordination']) {
        check(`${t} keeps its category when replayed as '${forced}'`,
            getEventCategory(t, forced), 'assurance');
    }
}
check('one owner for the type-only decision',
    (uiSrc.match(/function categoryByType\(type\)/g) || []).length, 1);
check('…consulted by both category functions',
    (uiSrc.match(/categoryByType\(/g) || []).length, 3);
// A non-trust type must still honour its stamp — authority is not a takeover.
check('a non-trust type still follows its stamp',
    getEventCategory('some_other_event', 'coordination'), 'coordination');

// Claims are spans lifted from the answer — usually prose. Rendering them in <code> told
// the reader to hunt for a schema name that was never there.
const claimSrc = uiSrc.match(/function _claimHtml\(raw\) \{[\s\S]*?\n\}/)[0];
const _claimHtml = new Function('escapeHtml', `${claimSrc}; return _claimHtml;`)((x) => String(x));
check('a prose claim is quoted, not code',
    /trust-claim-ident/.test(_claimHtml('technologies. Just as the **X** uses a')), false);
check('…with markdown emphasis stripped',
    /\*\*/.test(_claimHtml('the **Uderia Platform** uses a')), false);
check('…and quotation marks around it',
    /“/.test(_claimHtml('a sentence fragment here')), true);
check('a single identifier keeps monospace',
    /trust-claim-ident/.test(_claimHtml('fitness_db.members')), true);
check('…and is not quoted', /“/.test(_claimHtml('fitness_db.members')), false);
check('an over-long span is ellipsised',
    /…/.test(_claimHtml('x '.repeat(120))), true);
check('claims are no longer wrapped in <code>',
    /map\(c => `<code>\$\{c\}<\/code>`\)/.test(uiSrc), false);

// Prose must break at spaces: `break-all` produced "unsupported by the k / nowledge base".
check('status values do not break mid-word',
    /\.status-kv-value \{[^}]*word-break: break-all/.test(mainCss), false);
check('…they break at word boundaries',
    /\.status-kv-value \{[^}]*overflow-wrap: anywhere/.test(mainCss), true);
check('chips wrap as units', /\.trust-chip-row \{/.test(
    fs.readFileSync(path.join(HERE, '..', 'static', 'css', 'trust.css'), 'utf8')), true);

check('the chip exists', /data-filter="assurance"/.test(htmlSrc), true);
// Reserved: green/amber/red carry STATE. A category dot in a state colour would read as a
// verdict on the messages it groups, which is the one thing a category must never say.
const chipHex = (htmlSrc.match(
    /data-filter="assurance"[\s\S]{0,320}?background:\s*(#[0-9a-fA-F]{6})/) || [])[1];
check('…with a colour of its own', typeof chipHex, 'string');
check('…that is not a state colour',
    ['#10b981', '#22c55e', '#f59e0b', '#facc15', '#ef4444', '#dc2626']
        .includes((chipHex || '').toLowerCase()), false);
check('…and is not already spoken for',
    (htmlSrc.match(new RegExp(chipHex, 'gi')) || []).length, 1);
// The instrument is filtered with its messages, by the SAME mechanism — one code path, so
// the two can never disagree about whether assurance is on screen.
check('the band is filterable', /filterCategory = 'assurance'/.test(src), true);
check('…by the mechanism that hides the cards',
    /\[data-filter-category\]/.test(uiSrc), true);
// The Quality card is ADOPTED into the band, so one pill governs both readings. Two
// sibling elements each carrying the category would drift the moment one moved.
check('…and the Quality card rides inside it',
    /#trajectory-eval-overlay'\);[\s\S]{0,200}band\.appendChild\(quality\)/.test(src),
    true);
// Inserted mid-turn, so it asks to be filtered rather than waiting for the next update.
check('…and a late-mounted band asks to be filtered',
    /UderiaBus\.emit\('uderia:filterable-added'\)/.test(src), true);
check('…which ui.js answers',
    /UderiaBus\.on\('uderia:filterable-added'/.test(uiSrc), true);
// The shared hide class is scoped to trace steps; the band is not one, so it must opt in
// or the class lands with nothing to do — hidden messages under a visible instrument.
// Every filter chip must own a stripe colour, and it must be THE SAME colour. A chip
// without a matching `.completed.<cat>` rule falls through to the default stripe and the
// card wears another category's identity — which is how this one shipped reading System.
const chipHues = [...htmlSrc.matchAll(
    /data-filter="([a-z-]+)"[\s\S]{0,320}?background:\s*(#[0-9a-fA-F]{6})/g)]
    .map(([, cat, hex]) => [cat, hex.toLowerCase()]);
check('every chip has a colour', chipHues.length >= 8, true);
for (const [cat, hex] of chipHues) {
    const rule = mainCss.match(
        new RegExp(`\\.status-step\\.completed\\.${cat}[,\\s{][\\s\\S]{0,400}?border-left-color:\\s*(#[0-9a-fA-F]{6})`));
    check(`${cat} chip and card stripe are the same colour`,
        (rule ? rule[1].toLowerCase() : 'MISSING'), hex);
    // Two rules draw one edge: the border and the ::before bar over it. Colour one and
    // the stripe reads two categories at once — which is how assurance shipped fuchsia
    // over green.
    const bar = mainCss.match(
        new RegExp(`\\.status-step\\.completed\\.${cat}::before[,\\s{][\\s\\S]{0,400}?background:\\s*(#[0-9a-fA-F]{6})`));
    check(`${cat} bar matches its border`, (bar ? bar[1].toLowerCase() : 'MISSING'), hex);
}

// A trust card's links opened the Guide UNSCOPED — the whole catalogue, with no sign of
// which entry the card was about. The card carries its own subject as context now, and it
// must do so in EVERY dispatcher, or a genie turn silently loses the scope a standard turn
// has. Three dispatchers is the count of `classList.add(category)` sites.
const stampSites = (uiSrc.match(/_stampTrustGuideContext\(stepEl, type, details\)/g) || []).length;
const catSites = (uiSrc.match(/stepEl\.classList\.add\(category\)/g) || []).length;
check('every step dispatcher scopes the Guide', stampSites - 1, catSites);
check('…and the scope carries the station too',
    /items\.push\(\{ key: `station_\$\{station\}` \}\)/.test(uiSrc), true);
// Bands come from the Quality card's own context. A second reading of the same turn is
// how two slots start disagreeing.
check('…with bands read from the Quality card, not recomputed',
    /#trajectory-eval-overlay\[data-dim-context\]/.test(uiSrc), true);
check('…and only for dimensions this turn actually scored',
    /if \(bands\[d\]\) items\.push/.test(uiSrc), true);
check('…using one source for which dimensions measure a mechanism',
    /relatedOf:/.test(qdSrc), true);
// The clicked entry must stay MARKED. openGuide highlights on first paint, but the async
// vocabulary fetch repaints the list afterwards — and that repaint was wiping the
// highlight and the scroll, so the Guide arrived correctly scoped with no sign of which
// entry was chosen. One idempotent applier, re-run by the repaint from a stamp that a
// later openGuide overwrites.
check('the chosen entry is marked by one applier',
    /function _applyHighlight\(list, key(?:, opts)?\)/.test(qdSrc), true);
// …and by every OTHER repaint too (search, scope, facets, thresholds): the first facet
// click used to wipe the mark, because four call sites each rebuilt the list on their own.
check('…and re-applied by the one repaint every control shares',
    /const repaint = \(\) => \{\s*list\.innerHTML = bodyHtml\(search\.value\);\s*if \(list\.dataset\.qdHighlight\) _applyHighlight\(list, list\.dataset\.qdHighlight, \{ scroll: false \}\);/.test(qdSrc)
    && !/list\.innerHTML = bodyHtml\(search\.value\);\s*\}\)\);/.test(qdSrc), true);
check('…re-applied after the vocabulary repaint',
    /if \(l && l\.dataset\.qdHighlight\) _applyHighlight\(l, l\.dataset\.qdHighlight\)/.test(qdSrc), true);
check('…from a stamp set even before the entry exists',
    /list\.dataset\.qdHighlight = highlightKey;/.test(qdSrc), true);
check('…and previous marks are cleared, not accumulated',
    /qd-row\.qd-hi'\)\.forEach\(\(r\) => r\.classList\.remove\('qd-hi'\)\)/.test(qdSrc), true);

// A plan reload clears the status window and rebuilds it. The "already rendered" flag is
// module state and outlives that DOM, so comparing it alone answered a question nobody
// asked: reloading the SAME turn returned early and left the band unmounted, while the
// Quality card — which renders independently — stayed on screen. Every early-return must
// confirm the dial is still THERE, not merely that it was drawn once.
check('the render guard checks the DOM, not just the flag',
    /_settled === key && !!host\.querySelector\('#assurance-dial'\)/.test(src), true);
check('…and no guard compares the flag bare',
    /if \(_settled === /.test(src), false);

// The band closes with a rule. Without bottom padding the Quality card's own border sat
// on it — two lines touching read as one thick divider, putting the card visually outside
// the segment it belongs to.
check('the band leaves room above its closing rule',
    /#assurance-band \{[\s\S]*?padding-bottom: 12px/.test(
        fs.readFileSync(path.join(HERE, '..', 'static', 'css', 'trust.css'), 'utf8')), true);

check('…and the class actually hides it',
    /#assurance-band\.event-filtered-hidden\s*\{\s*display:\s*none/.test(
        fs.readFileSync(path.join(HERE, '..', 'static', 'css', 'trust.css'), 'utf8')), true);


// ── 7c · the summary agrees with the header above it ─────────────────────────
// These two slots describe the same turn. A shipped build had the header counting
// "2 needs you now · 1 worth a look" while the summary beside it named two stations in
// the WARN phrase — dropping the amber one and demoting the red ones in the same breath.
section('summary — every counted station is named, in its own word');

const mkSt = (states) => Object.fromEntries(AD.ORDER.map((k) => [k, {
    state: states[k] || 'na', points: [{ acted: false, state: states[k] || 'na' }],
}]));

{
    const st = mkSt({ grounded: 'danger', scored: 'danger', reviewed: 'warn' });
    const { text } = AD.summarise(st, {});
    const head = AD.routing(st);
    check('header counts both severities', head, '2 needs you now · 1 worth a look');
    // Every station the header counts must be findable in the summary. A count without
    // names is not actionable; names missing a counted station is a lie about the count.
    for (const [k, want] of [['grounded', true], ['scored', true], ['reviewed', true]]) {
        check(`summary names ${k}`, text.includes(AD.LABEL[k]), want);
    }
    check('…danger stations get the danger word', /needs? you now/.test(text), true);
    check('…and the amber one keeps its own', /worth a look/.test(text), true);
    check('…never the warn phrase for a red station',
        /Grounded, Scored need a look/.test(text), false);
}
{
    // Amber only: no "needs you now" may appear, or amber reads as an alarm.
    const { text } = AD.summarise(mkSt({ reviewed: 'warn' }), {});
    check('amber alone does not borrow the danger word', /needs? you now/.test(text), false);
    check('…and is still named', text.includes(AD.LABEL.reviewed), true);
}
{
    // Red only: no stray "worth a look" clause when nothing is amber.
    const { text } = AD.summarise(mkSt({ grounded: 'danger' }), {});
    check('red alone does not append an empty amber clause',
        /worth a look/.test(text), false);
    check('…and reads in the singular', /Grounded needs you now/.test(text), true);
}


// ── 7d · a station's reading explains the state beside it ────────────────────
// Shipped defect, from a real turn (rag_focused, grounding gate `decision:"pass"`, three
// KNOWLEDGE dimensions in danger): GROUNDED rendered RED and read
//   "Grounded — no model bound · no knowledge model bound to this turn"
// The station's attention is worst-of its mechanisms AND their dimensions, so when a
// DIMENSION raises the state no point matches it — and both the word and the detail fell
// back to `points[0]`, which is merely whichever reader registered first. The reader that
// had nothing to do with the red described it.
section('station reading — the word and the detail come from what drove the state');

const mkPt = (key, state, detail, extra = {}) =>
    ({ key, label: key, state, detail, acted: false, dims: [], ...extra });

{
    // Point order is the real one: trusted_scope registers first and is `na`.
    const d = {
        state: 'danger',
        points: [
            mkPt('trusted_scope', 'na', 'Trusted scope: no knowledge model bound to this turn',
                 { word: 'no model bound' }),
            mkPt('grounding_gate', 'pass', 'Grounding gate: corpus-supported'),
        ],
        // Measurements live ON the station now, allocated by the registry — the shape
        // `stations()` produces and every consumer reads.
        dims: [meas('grounded_overlap', 'danger'), meas('retrieval_relevance', 'danger')],
    };
    const txt = AD.stationReading(d, 'grounded');
    check('never quotes a point that did not drive the state',
        /no model bound|no knowledge model bound/.test(txt), false);
    check('…names the measures that did', /grounded_overlap|Grounding Overlap/.test(txt), true);
    check('…and all of them', /retrieval_relevance|Retrieval Relevance/.test(txt), true);
    // Two DANGER measurements, so the failing verb is right here — the review-band
    // wording is asserted at G2 above.
    check('…reading as a failure, not a fact', /are failing/.test(txt), true);
}
{
    // The word too: `stations()` sets it, and it had the same `pts[0]` fallback.
    const st = AD.stations({ by_station: { grounded: [
        { key: 'trusted_scope', state: 'na', detail: 'x', word: 'no model bound', dims: [] },
        { key: 'grounding_gate', state: 'pass', detail: 'y', dims: [] },
    ] }, measures: { grounded: [meas('grounded_overlap', 'danger')] } });
    check('the station is red', st.grounded.state, 'danger');
    check('…and does not borrow a quieter point\'s word', st.grounded.word, '');
}
{
    // Unchanged where a point DOES drive it: the mechanism keeps its own words.
    const d = { state: 'danger', points: [
        mkPt('trusted_scope', 'na', 'Trusted scope: none', { word: 'no model bound' }),
        mkPt('grounding_gate', 'danger', 'Grounding gate: confirmed drift — answer withheld',
             { word: 'drift' }),
    ] };
    const txt = AD.stationReading(d, 'grounded');
    check('a real gate failure still speaks for itself',
        /confirmed drift — answer withheld/.test(txt), true);
    check('…and keeps its own word', /drift/.test(txt), true);
}
{
    // An acted point always wins, whatever the dimensions say.
    const d = { state: 'danger', dims: [meas('safety', 'danger')], points: [
        mkPt('trusted_scope', 'na', 'Trusted scope: none'),
        mkPt('outbound_guard', 'danger', 'Outbound guard: redacted 2 values',
             { acted: true }),
    ] };
    check('an acted mechanism outranks its own measures',
        /redacted 2 values/.test(AD.stationReading(d, 'grounded')), true);
}
check('one driver rule, not one per caller',
    (src.match(/\|\| pts\[0\]|\|\| d\.points\[0\]/g) || []).length, 0);


// ── R-5 · the mini ring speaks ONE vocabulary on every path ──────────────────
// The chat-header glyph and the capsule's ring are 8-STATION instruments everywhere
// (roster, tree, the dial's own refresh) — but the legacy-flag path fed them the TEN
// journey stages, so a rendering preference silently changed the glyph's segment count
// and meaning. Both legacy sites now derive stations when the payload carries the
// assurance block, and fall back to stages only for a pre-R-2 payload.
section('R-5 — chat-header + capsule ring derive station semantics on every path');
{
    const tj = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'handlers',
        'trustJourney.js'), 'utf8');
    const ac = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'handlers',
        'answerCapsule.js'), 'utf8');
    for (const [name, s2] of [['trustJourney', tj], ['answerCapsule', ac]]) {
        // The guard's HEAD is pinned: a `false &&` disable keeps every usage line
        // intact, so grepping the usages passed on dead code (found by mutation).
        check(`${name}: derives stations from the assurance block`,
            /= \(td\.assurance && AD && AD\.stations|= \(td && td\.assurance && _AD && _AD\.stations/.test(s2), true);
        check(`${name}: walks the dial's own ORDER (never a private list)`,
            /AD\.ORDER\.map|_AD\.ORDER\.map/.test(s2), true);
        check(`${name}: keeps the stage fallback for pre-R-2 payloads`,
            /stages\.map\(\(s\) => s\.state\)/.test(s2), true);
    }
    check('the dial exports ORDER for them', typeof AD.ORDER, 'object');
    check('…and stations()', typeof AD.stations, 'function');
}


// ── the card decision (operator-approved): trace = what happened, dial = what is ─
section('cards — clean passes leave the trace; silent interventions enter it');
{
    // The predicate, EXECUTED across its truth table.
    const m = uiSrc.match(/function _assuranceCardSuppressed\(type, details\) \{[\s\S]*?\n\}/);
    check('suppression predicate extracted', !!m, true);
    const sup = new Function(`${m[0]}; return _assuranceCardSuppressed;`)();
    check('obda card is always suppressed', sup('obda', { lane: 'governed' }), true);
    check('a CLEAN grounding pass is suppressed',
        sup('grounding_gate', { decision: 'pass', unsupported_claims: [] }), true);
    check('…but a finding keeps the card',
        sup('grounding_gate', { decision: 'pass', unsupported_claims: ['x'] }), false);
    check('…a judge escalation keeps the card',
        sup('grounding_gate', { decision: 'pass', judged: true }), false);
    check('…and so does a TRIGGERED judge that has not yet ruled',
        sup('grounding_gate', { decision: 'pass', needs_judge: true }), false);
    check('…an acting decision keeps the card',
        sup('grounding_gate', { decision: 'block' }), false);
    check('…second-chance clears keep the card (honest generosity is a fact)',
        sup('grounding_gate', { decision: 'pass', second_chance_cleared: ['y'] }), false);
    check('guardrail cards are untouched', sup('guardrail', {}), false);
    // The dial note must fire BEFORE suppression at every site — the instrument moves
    // even when the trace stays quiet.
    const sites = [...uiSrc.matchAll(/AssuranceDial\.note\(_adSession\(\), '(grounding_gate|obda)'[\s\S]{0,220}?_assuranceCardSuppressed\(type, details\)/g)];
    check('note fires before suppression at every grounding/obda site', sites.length, 4);
}
{
    // The three silent interventions get trace cards, from the turn's own readings,
    // at the ONE inject seam (live + reload by construction).
    check('the interventions are enumerated',
        /_INTERVENTIONS = \['preflight_sql', 'retrieval_firewall', 'review_parking'\]/.test(src), true);
    check('…rendered only when the mechanism ACTED', /if \(!r \|\| !r\.acted \|\| !_INTERVENTIONS/.test(src), true);
    check('…idempotent per mechanism', /data-ad-intervention="\$\{r\.key\}"/.test(src), true);
    check('…wearing the shared step chrome + the assurance filter',
        /className = 'status-step completed assurance'/.test(src)
        && /card\.dataset\.filterCategory = 'assurance'/.test(src), true);
    // `_settled` is stamped BETWEEN mount and the intervention cards now (a failed
    // mount must leave the flag unstamped, or every retry early-returns forever —
    // the freeze the polar()-shape crash produced).
    check('…called at the inject seam', /_mount\(host, sessionId, turnId, td\.assurance\);\s*_settled = `\$\{sessionId\}:\$\{turnId\}`;\s*_interventionCards\(host, td\.assurance\)/.test(src), true);
    check('…and _settled is stamped only AFTER a successful mount',
        (() => { const a = src.indexOf('_mount(host, sessionId, turnId, td.assurance)');
                 const b = src.indexOf('_settled = `${sessionId}:${turnId}`');
                 return a > -1 && b > a; })(), true);
    check('…with the JUDGED label, never the wire key (one rule — W5)',
        /stationLabel\(r\.station\)/.test(src) && !/r\.station === 'scored' \? 'JUDGED'/.test(src), true);
}


// ── 7 · it cannot break the surface ──────────────────────────────────────────
section('robustness — a malformed payload must not break the trace view');
for (const bad of [null, undefined, {}, { by_station: null }, { by_station: { grounded: 'x' } },
                   { by_station: { grounded: [null, 3] } }]) {
    let ok = true;
    try {
        const s = AD.stations(bad);
        ok = AD.ORDER.every((k) => s[k] && typeof s[k].state === 'string');
        AD.summarise(s, bad);
    } catch (e) { ok = false; }
    check(`survives ${JSON.stringify(bad)}`.slice(0, 60), ok, true);
}

console.log('');
// ── the modules BEHIND a mechanism ───────────────────────────────────────────
// A mechanism row said WHAT it did ("1 withheld") and never which module produced the
// finding it acted on, nor what that module measured. The expansion carries both — and
// re-assesses through the CURRENTLY BOUND assessor, so after a remap it shows the new
// module rather than a fossil of the old one's output.
section('the module expansion under a mechanism row');
{
    // The whole section runs guarded: a scope-evaluation regression (a const used above
    // its declaration) throws at extraction time, and an unguarded throw would KILL the
    // gate instead of naming the defect — the difference between a failure you can read
    // and an exit code you have to bisect.
    try {
        const src = fs.readFileSync(SRC, 'utf8');
        // WHOLE-SCOPE extraction, deliberately. The first version of this gate sliced from
        // the metric-vocabulary declarations to the end of the function — which placed them
        // BEFORE the call site and reordered the exact thing that was broken. In the shipped
        // file the constants sat below `modulesHtml(p)`, so `const` left them in the temporal
        // dead zone, every inject() threw, and the dial never left "not in yet".
        //
        // A renderer tested outside its own scope is not tested. Taking `sectionHtml` entire
        // preserves declaration order, so a regression re-throws here instead of in a browser.
        // Start at the EARLIEST module-scope anchor above sectionHtml, whichever it is.
        // Anchoring on the vocabulary alone made the gate CRASH when the vocabulary moved
        // back inside sectionHtml (the slice went unbalanced) — and a gate that dies without
        // naming what broke is half a gate. This way the regression is REPORTED.
        const _s = src.indexOf('    function sectionHtml(k, d) {');
        const i = Math.min(...['    // ── metric vocabulary ─',
                               '    // ── the modules BEHIND a mechanism']
            .map((a) => src.indexOf(a)).filter((x) => x > 0 && x < _s));
        // End at sectionHtml's own closing brace — the slice must be a BALANCED scope, or
        // it is not the scope the browser evaluates.
        let _d = 0, j = -1;
        for (let n = _s; n < src.length; n++) {
            if (src[n] === '{') _d++;
            else if (src[n] === '}' && --_d === 0) { j = n + 1; break; }
        }
        check('the expansion renderer was extracted', i > 0 && j > i, true);
        // The collaborators sectionHtml calls are STUBBED, not reimplemented — this gate is
        // about the scope's own evaluation order, and each has its own assertions elsewhere.
        const scope = new Function('esc', 'guideDims', 'measuresHtml', 'stationWord',
            'TRACE_STEP', 'SUB_TIP', 'LABEL', 'BAND_OF', 'NOTHING_RUNS', 'JUDGED',
            // the member shelf (groupRows) reads the state rank and the attention word
            'RANK', 'word',
            // the per-panel marks key (it has its own assertions above)
            'markKey',
            src.slice(i, j) + '\nreturn { modulesHtml, sectionHtml };')(
            (x) => String(x ?? ''), () => '', () => '', () => 'quiet',
            {}, {}, new Proxy({}, { get: (_, k) => String(k) }), () => 'pass', {}, 'scored',
            { na: 0, pass: 1, pending: 2, unknown: 3, warn: 4, danger: 5 }, (st) => st, () => '');
        const modulesHtml = scope.modulesHtml;

        // The defect itself: the section renderer must run where it actually lives.
        let sectionThrew = null;
        try {
            scope.sectionHtml('grounded', { label: 'Grounded', state: 'pass', points: [
                { key: 'grounding_gate', label: 'Grounding gate', state: 'pass', detail: 'x: y',
                  modules: [{ property: 'answer_support', assessor: 'corpus_support',
                              assessor_label: 'Corpus support', triggered: false,
                              abstained: false, metrics: { supported_ratio: 1 }, summary: 'ok' }] }] });
        } catch (e) { sectionThrew = String(e); }
        check('sectionHtml renders a modules-bearing station IN ITS OWN SCOPE '
            + '(a const used above its declaration throws here)', sectionThrew, null);

        check('a mechanism with no modules renders nothing',
            modulesHtml({ modules: [] }), '');
        check('…and so does one with no modules field', modulesHtml({}), '');

        const mods = [
            { property: 'answer_support', label: 'Answer support',
              assessor: 'corpus_support', assessor_label: 'Corpus support',
              summary: 'all 3 checkable claims supported', triggered: false, abstained: false,
              metrics: { support_ratio: 1, claims_checked: 3, numeric_fidelity: 1 } },
            { property: 'answer_scope', label: 'Answer scope',
              assessor: 'kg_scope_diff', assessor_label: 'Data scope adherence',
              summary: '1 database outside the declared scope', triggered: true,
              abstained: false, metrics: { out_of_scope: 1, tool_calls: 1 } },
            { property: 'absence_claim', label: 'Absence claim',
              assessor: 'honesty_absence', assessor_label: 'Absence claim backing',
              summary: 'no raw-SQL data queries this turn', triggered: false,
              abstained: true, metrics: {} },
        ];
        const html = modulesHtml({ modules: mods });
    // The row is a two-column ledger: the PROPERTY in column 1 (a left rail a reader can
    // scan down the panel), everything about the module bound to it in column 2. Wrapping
    // the two in a div put both in column 1, where adjacent inline spans with no separator
    // rendered as "Answer scopeData scope adherence".
    check('the property and its assessor are direct grid children, not wrapped',
        /ad-mod-head/.test(html), false);
    check('…the property leads', /<span class="ad-mod-prop">[^<]+<\/span><span class="ad-mod-name">/
        .test(html.replace(/\n\s*/g, '')), true);

        // The summary states what is inside, so opening is a decision rather than a gamble.
        check('the closed state says whether anything was found',
            /1 of 3 found something/.test(html), true);
        check('…and an all-quiet mechanism says THAT instead',
            /2 modules, all quiet/.test(modulesHtml({ modules: [mods[0], mods[0]] })), true);
        // An abstention is a module that ran and declined to grade; it read "none ran"
        // (prod 2026-10-30: three grounding modules abstained on an annotated answer).
        const abst = mods.filter((m) => m.abstained);
        check('…and all-abstained says so, never "none ran"',
            /2 modules, all abstained/.test(modulesHtml({ modules: [abst[0], abst[0]] }))
            && !/none ran/.test(modulesHtml({ modules: [abst[0], abst[0]] })), true);
        // The module's relation to the gate is a MARK with words on hover, never a sentence
        // (prod 510f7011: nothing showed that the gate had consulted the three modules).
        const decided = modulesHtml({ modules: [Object.assign({}, mods[0], { decides: true })] });
        check('a module the gate consults in flight carries the role mark inside its name cell',
            /<span class="ad-mod-name">[^]*?<span class="ad-mod-role u-tip" data-tip="consulted by the gate during the turn[^"]*" aria-label="[^"]+"><svg/.test(decided)
            && !/title=/.test(decided), true);
        check('…one graded after the turn carries none', !/ad-mod-role/.test(modulesHtml({ modules: [mods[0]] })), true);
        check('…a mix counts both', /2 modules: 1 quiet, 1 abstained/.test(modulesHtml({ modules: [mods[0], abst[0]] })), true);

        // Three states, three SHAPES — never colour alone (Rule 9.4a).
        check('a module that found something carries its own class',
            /ad-mod ad-mod-triggered/.test(html), true);
        check('…one that could not run is marked abstained',
            /ad-mod ad-mod-abstained/.test(html), true);

        // The VALUES are the reason to expand.
        check('a ratio renders as a percentage', /<b>100%<\/b> support ratio/.test(html), true);
        // `max_repeats: 1` rendered as "100%" under a value-based rule, and `numeric_fidelity`
        // read as "1" under a suffix rule that did not cover it.
        check('…and a fidelity is a ratio too', /<b>100%<\/b> numeric fidelity/.test(html), true);
        check('a count stays a count', /<b>3<\/b> claims checked/.test(html), true);
        // "1 tool calls" — the plural defect one level down, in the LABEL.
        check('…and a count of one takes a singular label',
            /<b>1<\/b> tool call</.test(html) && !/<b>1<\/b> tool calls/.test(html), true);
        // `acted: 0` rendered as "no acted". Asserting only that the phrase is absent was too
        // weak: dropping the filter renders a bare "acted" for a FALSE flag, which claims the
        // opposite of the truth and would have passed. The property is that a false flag does
        // not appear AT ALL.
        const flagged = modulesHtml({ modules: [{ ...mods[0], metrics: { acted: 0, findings: 0 } }] });
        check('a false flag is dropped entirely, never rendered as a word',
            /acted/.test(flagged), false);
        check('…nor as "no acted"', /no acted/.test(flagged), false);
        check('…while a true flag reads as a word',
            /<b>acted<\/b>/.test(modulesHtml({ modules: [{ ...mods[0], metrics: { acted: 1 } }] })), true);

        // "nothing is assessing this" is the reading that must never be invisible.
        const unbound = modulesHtml({ modules: [{ property: 'context_use', label: 'Context use',
            assessor: null, assessor_label: null, summary: 'no module is bound to this property',
            metrics: {}, abstained: true }] });
        check('a property with NO bound module is shown, not omitted',
            /nothing bound/.test(unbound), true);

        // The ONE navigation channel — never a second glossary.
        check('each row routes to its property through data-dim',
            /data-dim="property_answer_support"/.test(html), true);
    } catch (e) {
        check('the module-expansion scope evaluates (a const used above its declaration '
            + 'throws here)', String(e), null);
    }
}




// ── live progress is not a verdict ───────────────────────────────────────────
{
    const DSRC = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'handlers',
        'assuranceDial.js'), 'utf8');
    const NOTE = DSRC.slice(DSRC.indexOf('function note('), DSRC.indexOf('function settle('));

    // The first version replaced the pending reading with the arrival, so a station took
    // its COLOUR on the first mechanism to report. Invisible only because every wired
    // event fires in the same end-of-turn seam — and a live latent defect the moment one
    // reports earlier, which is exactly what the inbound guard now does.
    check('a clean arrival leaves the station PENDING, not coloured',
        /state: acted \? state : 'pending'/.test(NOTE), true);
    check('…and the station keeps a pending reading while more are expected',
        /anyActed \? prior : prior\.concat/.test(NOTE), true);
    check('…showing how many have come in',
        /\$\{prior\.length\} in/.test(NOTE), true);
    // An act already happened to the answer. Holding it until the turn ends would hide a
    // redaction the reader is entitled to see as it occurs — but it is an ACT, not the
    // station's reading.
    check('an arrival that ACTED surfaces immediately', /const anyActed/.test(NOTE), true);

    // The early emission that makes any of this visible, and the rule that it must not
    // draw a card: a line per clean pass would bury the steps describing the actual turn.
    const EX = fs.readFileSync(path.join(HERE, '..', 'src', 'trusted_data_agent',
        'agent', 'executor.py'), 'utf8');
    check('the inbound guard reports a CLEAN pass, not only an act',
        /elif _inbound_event:[\s\S]{0,900}assurance_progress/.test(EX), true);
    check('…and it is not persisted as a step (progress, not trace)',
        /assurance_progress[\s\S]{0,400}\}\)/.test(EX)
        && !/_log_system_event\([\s\S]{0,80}assurance_progress/.test(EX), true);
    // The other mid-turn mechanisms. Preflight and Agent Authority run inside
    // `invoke_mcp_tool`, which is a plain coroutine — it RETURNS, it cannot yield, so it
    // has no path to the stream. They could only ever speak by changing the result, which
    // is why a denial was visible and a clean pass silent. The tally they already write to
    // is drained at the dispatch seam instead.
    check('the tally has a live drain, and it is idempotent',
        /def drain_new/.test(fs.readFileSync(path.join(HERE, '..', 'src',
            'trusted_data_agent', 'agent', 'assurance_tally.py'), 'utf8')), true);
    const TALLY = fs.readFileSync(path.join(HERE, '..', 'src', 'trusted_data_agent',
        'agent', 'assurance_tally.py'), 'utf8');
    check('…its bookkeeping is a SEPARATE contextvar, never a key in the stamps',
        /_SEEN: contextvars\.ContextVar/.test(TALLY)
        && !/fields\["_progress_seen"\]/.test(TALLY), true,
        'snapshot() copies every stamp onto the persisted turn — a private key would reach the audit');
    check('the executor drains at the dispatch seam, once for every engine',
        (EX.match(/self\._with_progress\(/g) || []).length >= 5, true);
    check('…and it REPORTS what a mechanism recorded, never infers from a clean tool call',
        /drain_new\(\)/.test(EX) && !/therefore.*passed/.test(EX), true);

    const UI = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'ui.js'), 'utf8');
    // Anchored in the CASE BLOCK, not by distance. The first version bounded the gap at
    // 700 chars and failed on correct code because the comment explaining WHY it renders
    // nothing is longer than that. Fifth time this trap has cost a fix in this programme:
    // a bound is not an assertion.
    // ONE rule, asked FIRST by the live chokepoint and by EVERY step renderer — the
    // standard renderer once lacked a case and dumped the event as raw JSON.
    const H = UI.slice(UI.indexOf('function _consumeAssuranceProgress'));
    check('…and the surface renders no card for it (one helper notes the dial)',
        /type !== 'assurance_progress'/.test(H.slice(0, 600)) && /AssuranceDial\.note/.test(H.slice(0, 600)), true);
    for (const fn of ['function _renderStandardStep(', 'function _renderGenieStep(',
                      'function _renderConversationAgentStep(', 'export function updateStatusWindow(']) {
        const body = UI.slice(UI.indexOf(fn), UI.indexOf(fn) + 2000)
            .replace(/\/\/[^\n]*/g, '');
        const first = body.split('\n').slice(1).find((l) => l.trim() && !/kg_enrichment|eventData = |catch \(e\)|^\s*\}\s*$|^\s*try /.test(l));
        check(`${fn.replace(/\($/, '')} consumes assurance_progress before rendering`,
            /_consumeAssuranceProgress\(eventData\)/.test(body.slice(0, body.indexOf('const {') > 0 ? body.indexOf('const {') + 1 : 400)) || /_consumeAssuranceProgress/.test(first || ''), true);
    }
}

if (failures.length) {
    console.log(`FAILED — ${pass} passed, ${failures.length} failed\n`);
    failures.forEach((f) => console.log(`  ✗ ${f}`));
    process.exit(1);
}

console.log(`ALL PASS — ${pass} assertions across the R-2 dial ledger.\n`);
