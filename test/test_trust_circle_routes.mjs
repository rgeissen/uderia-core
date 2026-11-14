#!/usr/bin/env node
/**
 * R-3 route-parity gate — written BEFORE the markers-on-a-band restyle.
 *
 * The Trust Circle is unlike the R-2 dial: it already ships and is already correct, with
 * eight action keys wired into real endpoints. The R-3 risk therefore inverts — the danger
 * is not inventing a wrong reading, it is LOSING a route during the restyle. This gate
 * pins the full gesture set of ledger rows R4–R11 + I27–I34 so a dropped route fails a
 * test instead of a demo. §6 of the migration plan lists the story, the agent picker, the
 * live pulse and the estate↔agent scope as "nobody has seen these in the new language" —
 * exactly the set asserted here.
 *
 * House rule: execute what is executable (the module loads under a stub window; glyph()
 * and attentionWord() are pure), assert source anchors for what needs a full DOM. A gate
 * that greps where it could run is weaker than it looks — but a gate that skips a route
 * because it could not run it is the blind spot that cost R-2 six defects.
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const TC = path.join(HERE, '..', 'static', 'js', 'handlers', 'trustCircle.js');
const APH = path.join(HERE, '..', 'static', 'js', 'handlers', 'agentPerformanceHandler.js');

let pass = 0; const failures = [];
function check(name, got, want) {
    if (JSON.stringify(got) === JSON.stringify(want)) { pass++; return; }
    failures.push(`${name}\n      got=${JSON.stringify(got)} want=${JSON.stringify(want)}`);
}
function section(t) { console.log(`\n── ${t} ${'─'.repeat(Math.max(0, 64 - t.length))}`); }

const tcSrc = fs.readFileSync(TC, 'utf8');
const aphSrc = fs.readFileSync(APH, 'utf8');

// Load the real module. A stub document is enough for parse + public API; mount() needs a
// DOM and is asserted at source level below.
const win = {};
new Function('window', 'document', tcSrc)(win, {});
const AD = win.TrustCircle;

// ── 1 · public API — every consumer's entry point survives ───────────────────
section('public API — the surface other modules stand on');
for (const k of ['mount', 'update', 'pulse', 'pin', 'noteEvent', 'glyph', 'glyphInto',
                 'playStory', 'stopStory', 'stateWord', 'attentionWord']) {
    check(`TrustCircle.${k} exists`, typeof AD[k],
          k === 'stateWord' ? 'object' : 'function');
}
check('agentPerformance exports openCircle',
    /window\.agentPerformance = \{[^}]*openCircle/.test(aphSrc), true);
check('…and switchScope (estate↔agent / my↔system scope)',
    /window\.agentPerformance = \{[^}]*switchScope/.test(aphSrc), true);

// ── 2 · the 8 action keys — each must be dispatched somewhere real ───────────
section('action keys — 8 acts, each with a live dispatch branch (ledger R4–R8)');
const KEYS = ['sign-agent', 'verify-agent', 'attest-deployment', 'inbox',
              'roster', 'posture-matrix', 'posture-tools', 'admin-eval'];
for (const k of KEYS) {
    // A key is only a route if the handler DISPATCHES it — offering the button is not
    // enough. Both comparison shapes count ('===' and includes-lists).
    const dispatched = new RegExp(
        `action === '${k}'|\\[[^\\]]*'${k}'[^\\]]*\\]\\.includes\\(action\\)`).test(aphSrc);
    check(`'${k}' is dispatched`, dispatched, true);
}
check('station panels render the action buttons',
    /data-tc-act="\$\{esc\(a\.action\)\}"/.test(tcSrc), true);
check('…and the panel wires every [data-tc-act]',
    /querySelectorAll\('\[data-tc-act\]'\)/.test(tcSrc), true);

// ── 3 · the Guide routes — dimension chips + the ? derivation ────────────────
section('guide routes — explain-dim chips route through ONE navigation channel');
check('derivation chips carry explain-dim:<key>',
    /data-tc-act="explain-dim:\$\{esc\(x\.key\)\}"/.test(tcSrc), true);
check('the handler routes explain-dim to the Assurance Guide',
    /action\.indexOf\('explain-dim:'\) === 0/.test(aphSrc)
    && /QD\.openGuide|QualityDimensions\.openGuide/.test(aphSrc), true);
check('…re-resolving the station from the SAME model the panel rendered from',
    /Re-resolve the station from the SAME model/.test(aphSrc), true);

// ── 4 · story — play · pause/resume · back · next/finish · exit ──────────────
section('story controls — the full R18 set (not prototyped, highest risk)');
for (const nav of ['pause', 'back', 'next', 'exit']) {
    check(`story control '${nav}' present`,
        new RegExp(`data-tc-nav="${nav}"`).test(tcSrc), true);
}
check('playStory and stopStory are real functions',
    typeof AD.playStory === 'function' && typeof AD.stopStory === 'function', true);
check('pause toggles to Resume (state, not a dead label)',
    /Resume/.test(tcSrc) && /_story\.paused/.test(tcSrc), true);

// ── 5 · the agent picker + centre ────────────────────────────────────────────
section('agent picker — the centre is a real control (I31)');
check('the centre is a button', /tc-center" role="button" tabindex="0"/.test(tcSrc), true);
check('…labelled as the picker',
    /aria-label="Choose the agent in focus"/.test(tcSrc), true);
check('the model carries a pickable contract', /pickable/.test(tcSrc), true);
check('the handler opens the shared picker (one picker, not a second one)',
    /UserScopePicker\.open\(/.test(aphSrc), true);

// ── 6 · live pulse + last-event (I29–I30) ────────────────────────────────────
section('live pulses — the ring answers real events');
check('a completed turn laps the ring',
    /uderia:turn-complete[\s\S]{0,200}TrustCircle\.pulse\('turn'\)/.test(aphSrc), true);
check('…and notes it', /noteEvent\('a turn travelled the gates'\)/.test(aphSrc), true);
check('a trust event flashes its station',
    /uderia:trust-event[\s\S]{0,600}TrustCircle\.pulse\(station\)/.test(aphSrc), true);
check('…and feeds the last-event line',
    /uderia:trust-event[\s\S]{0,700}TrustCircle\.noteEvent\(msg\)/.test(aphSrc), true);

// ── 7 · keyboard (R17) ───────────────────────────────────────────────────────
section('keyboard — arrows walk, Enter pins, Escape unpins');
// Ledger R17 claimed arrows on every instrument; the shipped circle had only Enter and
// Escape — a gap THIS GATE found on its first run, closed by R-3. Wrap-around, because
// the loop closes.
check('arrow navigation walks the loop (R17, landed in R-3)',
    /ArrowRight|ArrowDown/.test(tcSrc) && /ArrowLeft|ArrowUp/.test(tcSrc), true);
check('…with wrap-around', /\(i \+ step \+ STATIONS\.length\) % STATIONS\.length/.test(tcSrc), true);
check('Enter/Space pins', /ev\.key === 'Enter' \|\| ev\.key === ' '/.test(tcSrc), true);
check('Escape unpins', /Escape[\s\S]{0,40}unpin\(\)/.test(tcSrc), true);
check('stations are focusable buttons',
    /data-tc-station="\$\{st\.key\}" role="button" tabindex="0"/.test(tcSrc), true);

// ── 8 · the mini glyph — roster · chat header · capsule (R9–R11) ─────────────
section('mini glyph — one derivation feeds every consumer');
{
    // Pure — EXECUTE it. Eight states in, eight segments out, each carrying its state.
    const states = ['pass', 'warn', 'danger', 'na', 'pass', 'unknown', 'pending', 'pass'];
    const svg = AD.glyph(states, { size: 20, label: 'x' });
    check('glyph renders one segment per station',
        (svg.match(/tc-mini-seg/g) || []).length, 8);
    for (const s of new Set(states)) {
        check(`…segment class for '${s}'`, svg.includes(`tc-mini-${s}`), true);
    }
    check('…and is labelled for AT', /aria-label="x"/.test(svg), true);
    check('glyph survives an empty input',
        (AD.glyph(null, {}).match(/tc-mini-seg/g) || []).length, 8);
}
// R-3.3 — ONE construction site. The roster built its ring inline and the tree had
// none; now `_stationRing` is the single builder (deriving from `_circleStationsFor` on
// the SAME roster entry), and both surfaces call it — so the tree ring and the roster
// ring cannot disagree about one agent.
// W2 — the ONE derivation now feeds two readings (the segment states AND the verdict
// tooltip); it is staged into a local and still happens exactly once.
check('one ring builder, deriving from _circleStationsFor',
    /function _stationRing\(tag, size\)[\s\S]{0,900}const sts = _circleStationsFor\(a\);[\s\S]{0,80}states = sts\.map\(\(s\) => s\.state\)/.test(aphSrc), true);
check('…and the tooltip is the same verdict the ring leads with (W2)',
    /verdict = _verdictLine\(sts, \{ label: '@' \+ a\.tag, kind: 'agent' \}\)\.text/.test(aphSrc), true);
check('…refusing to render an all-na ring about an unseen agent',
    /if \(!a\) return '';/.test(aphSrc), true);
check('…used by the roster card', /_stationRing\(a\.tag, 20\)/.test(aphSrc), true);
check('…and by every tree node', /_stationRing\(run\.profile_tag, 16\)/.test(aphSrc), true);
// The builder STAGES the derivation into a local since the trust-trip lens: this
// glyph renders on surfaces with no lens chip and no "since" disclosure, so it
// must never be lens-scoped (W3) — `_stationRing` forces the whole record around
// the ONE derivation and restores the ring's lens after. Exactly one derivation
// site, and it is the guarded one.
check('…with no second inline construction left',
    (aphSrc.match(/const sts = _circleStationsFor\(a\);/g) || []).length === 1
    && (aphSrc.match(/_circleStationsFor\(a\)\.map\(\(s\) => s\.state\)/g) || []).length === 0, true);
check('…and that one site is lens-guarded (W3: no undisclosed trip scoping)',
    /const keepLens = _circleLens;[\s\S]{0,220}_circleLens = 'total';[\s\S]{0,420}finally \{ _circleLens = keepLens; \}/.test(aphSrc), true);

// ── the roster leg filter (trust trip §10.2/10.4) ────────────────────────────
// The expanded detail may read the leg BECAUSE it carries its own disclosure
// chips; the pill exists only where a leg exists; one toggle re-scopes every
// section together so bars and runs can never disagree about their window.
check('roster: the detail selects the trip cell only behind the leg toggle',
    /const legOn = !!\(trip && _rosterLegOn\.has\(a\.key \|\| a\.tag\)\);[\s\S]{0,80}const o = legOn \? trip : \(a\.online \|\| \{\}\);/.test(aphSrc), true);
check('roster: every scoped section carries the leg chip disclosure',
    (aphSrc.match(/\$\{legChip\}/g) || []).length >= 3, true);
check('roster: the leg pill renders only where a leg exists',
    /const legPill = trip\s*\?/.test(aphSrc), true);
// ONE filler for the detail's async sections (Runs Ledger / timeline / Trust
// History), called from EVERY path that re-renders the detail html — the card
// click, the leg toggle, and the full load(). A render path that skips it leaves
// the per-turn execution rows as empty containers until a close/reopen (the
// shipped defect this pins against).
check('roster: one filler owns the async detail sections',
    /function _fillExpandedDetail\(tag\)[\s\S]{0,900}renderAgentTimeline\(tag\);[\s\S]{0,400}renderTrustHistory\(tag\);[\s\S]{0,400}loadRunsLedger\(tag\);/.test(aphSrc), true);
check('roster: the leg toggle re-renders the WHOLE detail through it',
    /function _toggleRosterLeg\(tag\)[\s\S]{0,300}applyRosterFilter\(\);[\s\S]{0,120}_fillExpandedDetail\(tag\);/.test(aphSrc), true);
check('roster: the card click fills through it too',
    /function toggleAgentDetail\(tag\)[\s\S]{0,500}_fillExpandedDetail\(tag\);/.test(aphSrc), true);
check('roster: a full load() refills an expanded detail (the close/reopen defect)',
    /setSection\('agent-section-roster', renderRoster\(_model\)\);[\s\S]{0,500}if \(_expandedTag\) _fillExpandedDetail\(_expandedTag\);/.test(aphSrc), true);
check('roster: an empty leg reads empty, never clean',
    /the leg is empty, not clean/.test(aphSrc), true);
check('…and opens the circle on click', /data-circle-key/.test(aphSrc)
    && /openCircle/.test(aphSrc), true);

// ── 9 · the coordination tree — per-node trust, honest absence ───────────────
section('coordination tree — per-node glyphs (I22, §6 highest risk)');
check('every tree node row carries the trust glyph',
    /_treeTrustGlyph\(run\.profile_tag\)/.test(aphSrc), true);
check('…which renders nothing when no trust source is loaded',
    /if \(!integ && !comp\) return '';/.test(aphSrc), true);
check('…and never leaks the wire token to the tooltip',
    /_integLabel\(integ\)/.test(aphSrc), true);

// ── 10 · what the restyle must keep on the canvas (I28, I32–I33) ─────────────
section('kept geometry — membrane · loop arc · star · legend anchors');
check('the membrane stays', /tc-membrane/.test(tcSrc), true);
check('the loop-closing arc stays', /tc-close-arc/.test(tcSrc), true);
check('…with its closure count', /the loop closes · \$\{n\}/.test(tcSrc), true);
check('the star stays', /tc-star/.test(tcSrc), true);
check('the routing summary reaches the panel', /_attentionSummary/.test(tcSrc), true);
check('one attention vocabulary — attentionWord is the shared source',
    AD.attentionWord('danger') !== '' && typeof AD.attentionWord, 'function');

// ── 11 · markers on a band (R-3.4, ledger §4.3) ──────────────────────────────
section('the band — weight before colour, ticks from the model, plate order');
const cssSrc = fs.readFileSync(path.join(HERE, '..', 'static', 'css', 'trust.css'), 'utf8');
check('every station renders a band segment', /class="tc-bandseg"/.test(tcSrc), true);
check('…centred on the canonical station angle, not a segment midpoint',
    /segArc\(R, i\)/.test(tcSrc), true);
// Weight before colour (Rule 9.4): the stroke must RISE with attention so the state
// survives a colourblind reader. Extract the three widths and assert the ordering —
// asserting the literal values would break on a tuning pass that preserves the rule.
{
    const w = (st) => parseFloat((cssSrc.match(new RegExp(
        `data-tc-state="${st}"\\]\\s+\\.tc-bandseg \\{[^}]*stroke-width:\\s*([\\d.]+)`)) || [])[1]);
    check('band weight rises pass < warn < danger',
        w('pass') < w('warn') && w('warn') < w('danger'), true);
    check('…and na is the thinnest, dashed',
        w('na') < w('pass') && /data-tc-state="na"\]\s+\.tc-bandseg,[\s\S]{0,120}stroke-dasharray/.test(cssSrc), true);
}
// The plate is part of the CONTROL: label/value/motto keep pointer-events:none (thin
// glyphs are a miserable hit target), so without a hit rect the plate was a dead zone —
// hover and click worked on the marker but not on the words naming it.
check('every plate has a hit rect', /class="tc-plate-hit"/.test(tcSrc), true);
check('…anchor-aware, so it covers the side the text grows toward',
    /anchor === 'middle' \? lp\.x - 110 : anchor === 'start' \? lp\.x - 6 : lp\.x - 214/.test(tcSrc), true);
check('…transparent and gesture-bearing', /\.tc-plate-hit \{ fill: transparent; stroke: none; cursor: pointer; \}/.test(
    fs.readFileSync(path.join(HERE, '..', 'static', 'css', 'trust.css'), 'utf8')), true);

// PROP-002 — ONE dot, ONE meaning: a single state-coloured dot means A MECHANISM
// ACTED; the accent dot means an act is OWED. Ticks and the count channel are both
// retired, so these assert the CURRENT marks. Both are pre-drawn per station and
// toggled from the MODEL, never inferred by the renderer.
//
// These two assertions were stale for a release: they still grepped `tc-acted-tick`
// (a class the renderer no longer emits) and the exact whitespace of one toggle
// statement. That is the implementation-pinning shape this suite has been converting
// — a grep for a source line tests that the line exists, not that the property holds,
// and it goes red on a rename while saying nothing about behaviour.
check('the acted dot is pre-drawn per station — exactly one (the count channel is dead)',
    (tcSrc.match(/class="tc-acted-dot"/g) || []).length, 1);
check('the owed dot is pre-drawn per station',
    (tcSrc.match(/class="tc-owed-dot"/g) || []).length, 1);
check('…acted toggled from the MODEL, never inferred by the renderer',
    /tc-acted-dot'\)[\s\S]{0,160}?d\.acted/.test(tcSrc), true);
check('…owed toggled from the MODEL too',
    /tc-owed-dot'\)[\s\S]{0,160}?d\.owed/.test(tcSrc), true);
check('…which the assembler supplies as facts',
    /owed: !!o\.owed/.test(aphSrc) && /acted: !!o\.acted/.test(aphSrc), true);
check('…set where sign or attest is outstanding',
    /owed: !!\(nUnattested \|\| nChanged \|\| \(nUnsigned && signingInUse\)\)/.test(aphSrc), true);
check('…and where an item is parked for a human', /owed: !!rOpen/.test(aphSrc), true);
// The engraved plate: name · reading · motto — the value sits under the NAME, not under
// the marker where the band now curves through.
check('the plate orders label · value · sub',
    /tc-label[\s\S]{0,220}tc-value[\s\S]{0,220}tc-sub/.test(
        tcSrc.slice(tcSrc.indexOf('nodes += `'))), true);
check('the loop arc moved radially outside the band',
    /const LOOPR = 204/.test(tcSrc), true);
// The blueprint channel reaches the ring (operator decision, and the language's own
// rule): a station NAME is the map layer, so it shares --an-map with the Anatomy —
// structure in blue, readings in primary. ONE fill: a second declaration below the
// blueprint one is how the italics fix silently lost to the cascade.
check('station names are blueprint', /\.tc-label \{[\s\S]{0,500}?fill: var\(--an-map/.test(cssSrc), true);
check('…with exactly one fill declaration',
    ((cssSrc.match(/\.tc-label \{[\s\S]*?\}/) || [''])[0].match(/fill:/g) || []).length, 1);
check('…and the membrane shares the map softener',
    /\.tc-membrane \{[^}]*var\(--an-map-soft/.test(cssSrc), true);
check('readings stay primary (data outranks chrome)',
    /\.tc-value \{[\s\S]{0,400}?fill: var\(--text-primary\)/.test(cssSrc), true);

check('the rail is kept for the pulse, quietened',
    /tc-rail/.test(tcSrc) && /\.tc-rail \{[^}]*opacity: \.35/.test(cssSrc), true);

// ── 12 · inbox attribution — the per-agent open count matches the estate badge (FE-H1) ──
// A receipt is the RECORD of an act (a quarantine, a release), never owed work — the
// server's `unacked_count` excludes it, so the per-agent `open` must too, or the focused
// REVIEWED reading disagrees with the estate badge and lights an owed-dot for nothing
// (Rule 11.2). Execute the SHIPPED stored-open predicate against fixtures.
section('inbox attribution — receipts never count as open work (Rule 11.2)');
const predMatch = aphSrc.match(/stored\.filter\((\(e\) => [^\n]+?)\)\s*\n\s*\.forEach/);
check('the shipped stored-open predicate is present', !!predMatch, true);
if (predMatch) {
    const pred = new Function('return (' + predMatch[1] + ')')();
    check('an unacked non-receipt incident counts',
          pred({ acknowledged: false, is_receipt: false }), true);
    check('an acknowledged incident does not', pred({ acknowledged: true, is_receipt: false }), false);
    check('a RECEIPT never counts toward open (the estate badge excludes it too)',
          pred({ acknowledged: false, is_receipt: true }), false);
    check('a null entry is skipped', !!pred(null), false);
}
// The fold (station colour) already excluded receipts; the open count must share that rule.
check('open and the colour fold share the receipt exclusion',
    /stored\.filter\(\(e\) => e && !e\.acknowledged && !e\.is_receipt\)/.test(aphSrc), true);


// ── one post-disposition seam (Sep 2026) ─────────────────────────────────────
// An acknowledgment / review decision / inbox quarantine moves ONE fact — the
// open-item model `renderAssuranceInbox()` recomputes — and that fact has several
// readers: the inbox list, the badges, the ring (REVIEWED + the colour fold) and
// the mini rings. The defect: only the first two were repainted, so the ring kept
// the pre-ack state until a page refresh. The rule: every disposition routes
// through `refreshInbox`, and `refreshInbox` repaints EVERY reader in place.
// Prose about the code is not the code: a commented-out call (`// _repaintInboxConsumers();`)
// satisfied the first version of this assertion — a mutation that ESCAPED. Every check in
// this section runs over the comment-stripped source.
const aphCode = aphSrc.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
check('refreshInbox repaints the readers of the inbox model',
    /async function refreshInbox\(\)[\s\S]{0,400}_repaintInboxConsumers\(\);/.test(aphCode), true);
const repaint = (aphCode.match(/function _repaintInboxConsumers\(\) \{[\s\S]*?\n    \}\n/) || [''])[0];
check('…the ring, in place (update keeps pin/lens/focus), only when mounted',
    /querySelector\('\.tc-svg'\)[\s\S]{0,120}TrustCircle\.update\(_buildCircleModel\(\)\)/.test(repaint), true);
check('…and every mini ring, re-derived through the ONE builder',
    /querySelectorAll\('\.tc-mini-host\[data-circle-key\]'\)[\s\S]{0,600}_stationRing\(a\.tag, size\)/.test(repaint), true);
check('…at its own size (roster 20 and tree 16 stay distinct)',
    /parseInt\(svg\.getAttribute\('width'\), 10\)/.test(repaint), true);
check('…never a page reload or a full load()',
    /location\.reload|\bload\(\)/.test(repaint), false);
// Every disposition path routes through the seam.
const fnBody = (name) => (aphCode.match(new RegExp(`async function ${name}\\([^)]*\\) \\{[\\s\\S]*?\\n    \\}\\n`)) || [''])[0];
for (const fn of ['ackInboxBatch', 'decideReview', 'bulkDecideReviews', 'quarantineDocFromInbox', '_refreshCircleTrust']) {
    check(`disposition path ${fn} routes through refreshInbox`, /refreshInbox\(\)/.test(fnBody(fn)), true);
}
check('the trust-event listener routes through the seam (no second inline copy)',
    /_inboxRefreshT = setTimeout\(refreshInbox, 400\)/.test(aphCode), true);
// The inbox is rendered by exactly two sites: the seam, and the initial load().
check('no inline inbox render survives outside the seam and load()',
    (aphCode.match(/renderAssuranceInbox\(\)/g) || []).length
        - (aphCode.match(/function renderAssuranceInbox\(\)/g) || []).length, 2);
check('…the awaited one lives in refreshInbox',
    /async function refreshInbox\(\)[\s\S]{0,120}await renderAssuranceInbox\(\)/.test(aphSrc), true);


console.log('');
if (failures.length) {
    console.log(`FAILED — ${pass} passed, ${failures.length} failed\n`);
    failures.forEach((f) => console.log(`  ✗ ${f}`));
    process.exit(1);
}
console.log(`ALL PASS — ${pass} assertions across the R-3 route-parity gate.\n`);
