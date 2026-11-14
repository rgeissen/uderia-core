#!/usr/bin/env node
/**
 * Quality-card partition gate — one grammar for the whole Live Status panel.
 *
 * The decision this pins: every scored dimension is counted at exactly ONE Trust
 * Circle station (the partition; JUDGED holds only the unowned residue), and the
 * Quality card GROUPS BY THAT PARTITION — the old KPI-tier taxonomy is retired on
 * that surface. Beside a dial that says "all N checks this station owns", a card
 * speaking a second vocabulary was an apparent double count.
 *
 * Method: derive dim→station from the BACKEND source (assurance_points.py:
 * POINT_DIMENSIONS × the AssurancePoint registry's station column) — never a
 * hand-copied map — and execute ui.js's real `_stationGroupsForCard` (verbatim
 * extraction) against it. If the shipped grouping disagrees with the server
 * partition, or the taxonomy resurfaces, this fails.
 */
import { readFileSync } from 'node:fs';
import { execSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const ROOT = join(here, '..');
const uiSrc = readFileSync(join(here, '..', 'static', 'js', 'ui.js'), 'utf8');
const apSrc = readFileSync(join(here, '..', 'src', 'trusted_data_agent', 'agent', 'assurance_points.py'), 'utf8');
const ringSrc = readFileSync(join(here, '..', 'static', 'js', 'handlers', 'agentPerformanceHandler.js'), 'utf8');
const dialSrc = readFileSync(join(here, '..', 'static', 'js', 'handlers', 'assuranceDial.js'), 'utf8');

let pass = 0, fail = 0;
const check = (label, cond) => {
    if (cond) { pass++; } else { fail++; console.log(`  FAIL: ${label}`); }
};

// ── 1 · derive the partition from the backend source ────────────────────────
// mechanism → station (AssurancePoint("key", "Label", "station", ...))
const mechStation = {};
for (const m of apSrc.matchAll(/AssurancePoint\("([\w]+)",\s*"[^"]*",\s*"([\w]+)"/g)) {
    mechStation[m[1]] = m[2];
}
check('registry parsed (≥12 mechanisms)', Object.keys(mechStation).length >= 12);

// mechanism → owned dims (POINT_DIMENSIONS)
const pdBlockM = apSrc.match(/POINT_DIMENSIONS[\s\S]*?=\s*\{([\s\S]*?)\n\}/);
check('POINT_DIMENSIONS block found', !!pdBlockM);
const dimStation = {};
let partitionConflict = null;
if (pdBlockM) {
    for (const e of pdBlockM[1].matchAll(/"([\w]+)":\s*\(([^)]*)\)/g)) {
        const mech = e[1];
        const st = mechStation[mech];
        if (!st) continue;
        for (const d of e[2].matchAll(/"([\w]+)"/g)) {
            const dim = d[1];
            if (dimStation[dim] && dimStation[dim] !== st) {
                partitionConflict = `${dim}: ${dimStation[dim]} vs ${st}`;
            }
            dimStation[dim] = st;
        }
    }
}
// THE INVARIANT AT THE SOURCE: no dimension is owned by mechanisms at two stations.
check(`backend partition is conflict-free${partitionConflict ? ` (${partitionConflict})` : ''}`,
      partitionConflict === null);
const stationOf = (k) => dimStation[k] || 'scored';

// ── 2 · ring parity: STATION_DIMS mirrors the same partition ────────────────
const ringBlockM = ringSrc.match(/const STATION_DIMS = \{([\s\S]*?)\n    \};/);
check('ring STATION_DIMS found', !!ringBlockM);
if (ringBlockM) {
    for (const e of ringBlockM[1].matchAll(/(\w+):\s*\[([^\]]*)\]/g)) {
        const st = e[1];
        for (const d of e[2].matchAll(/'([\w]+)'/g)) {
            check(`ring & backend agree: ${d[1]} → ${st}`, stationOf(d[1]) === st);
        }
    }
    // Both directions: every backend-owned dim appears in the ring's mirror.
    const ringDims = new Set([...ringBlockM[1].matchAll(/'([\w]+)'/g)].map((m) => m[1]));
    for (const [dim, st] of Object.entries(dimStation)) {
        if (st === 'scored') continue;   // residue is computed, not listed
        check(`backend-owned ${dim} present in the ring mirror`, ringDims.has(dim));
    }
}

// ── 3 · execute the REAL card grouping ──────────────────────────────────────
// The scorecard groups by the STAGE of the turn a check measures (eval/stages.py,
// served through the assurance vocabulary), in the order the turn ran. It used to group
// by the station that OWNED a dimension; ownership ended with the lane cut, and a
// station name on this surface kept implying a relationship the code no longer has.
const startM = uiSrc.indexOf('function _stageGroupsForCard');
const endMarker = '    return out;\n}';
const endM = uiSrc.indexOf(endMarker, startM);
check('card grouping block extracted', startM > 0 && endM > startM);
const block = uiSrc.slice(startM, endM + endMarker.length);
// eslint-disable-next-line no-new-func
const groupsFor = new Function(`${block}\n return _stageGroupsForCard;`)();

// The served model, read from the ONE declaration so this gate cannot drift from it.
const STAGE_MODEL = JSON.parse(execSync(
    `python3 -c "import sys,json;sys.path.insert(0,'src');`
    + `from trusted_data_agent.eval import stages as s;print(json.dumps(s.vocabulary_payload()))"`,
    { cwd: ROOT }).toString());
const QD_STUB = {
    stagesReady: () => true,
    groupByStage(keys) {
        const seen = new Set(keys || []);
        const out = [];
        STAGE_MODEL.order.forEach((st) => {
            const members = [...seen].filter((k) => STAGE_MODEL.dimensions[k] === st);
            if (members.length) out.push({ stage: st, label: STAGE_MODEL.labels[st],
                                           desc: STAGE_MODEL.descriptions[st], keys: members });
        });
        return out;
    },
};

// The screenshot-realistic Focus turn: 24 checks.
const TURN_DIMS = ['completed', 'answer_present', 'safety', 'clean_termination',
    'output_sanity', 'format', 'cost', 'latency', 'tokens', 'tasks', 'efficiency',
    'context_utilization', 'retrieval_performed', 'grounded_overlap', 'numeric_fidelity',
    'retrieval_relevance', 'knowledge_freshness', 'source_consistency', 'citations',
    'provenance_integrity', 'context_health', 'groundedness', 'content_safety',
    'answer_correctness'];
const verdicts = TURN_DIMS.map((d) => ({ dimension: d }));
const groups = groupsFor(verdicts, QD_STUB);

// Disjoint + total: every verdict lands in exactly one group.
const placed = groups.flatMap((g) => g.verdicts);
check('grouping is TOTAL (every check placed)', placed.length === verdicts.length);
const seen = new Set();
let dup = null;
placed.forEach((v) => { if (seen.has(v.dimension)) dup = v.dimension; seen.add(v.dimension); });
check(`grouping is DISJOINT (no dim in two groups${dup ? `: ${dup}` : ''})`, dup === null);
check('every group is a declared stage',
      groups.every((g) => STAGE_MODEL.order.includes(g.stage)));

// ORDER is the model's main asset — the card must read as the turn ran, never
// alphabetically and never in registry order.
const idx = groups.map((g) => STAGE_MODEL.order.indexOf(g.stage));
check('groups render in TURN ORDER',
      idx.every((v, i) => i === 0 || v > idx[i - 1]));
check('the first group is the earliest stage present',
      groups[0].stage === 'plan');
check('the whole-turn group comes last',
      groups[groups.length - 1].stage === 'the_run');

// Every group carries a renderable label and its own description (the header tooltip
// that replaced the retired station route — see the inventory ledger).
check('every group carries a user-facing label, never a wire key',
      groups.every((g) => g.label && g.label !== g.stage));
check('every group carries a description for its header tooltip',
      groups.every((g) => (g.desc || '').length > 20));

// ── 3b · the provenance line — three facts a reader cannot recover otherwise ──
// The scorecard now lives inside JUDGED's panel, so the line under its headline is the
// only place three facts are stated. Executed, not grepped: the point of each is what it
// RENDERS, and a source match would pass on a variable that never reaches the string.
{
    const start = uiSrc.indexOf('function _qualityScoreNote(');
    const end = uiSrc.indexOf('\nfunction _ifocLabel(');
    // eslint-disable-next-line no-new-func
    const note = new Function('_ifocLabel',
        `${uiSrc.slice(start, end)}\n return _qualityScoreNote;`)((t) => 'Focus');

    const det = note({ source: 'auto', cost_usd: 0 }, { profile_type: 'rag_focused' });
    // 1 · the COST is labelled. `Cost ·metric $0.0007` — the TURN's cost — sits a few
    //     groups below in the same panel. Two costs on one surface with one unlabelled
    //     is a misreading waiting to happen: a figure carries what it is OF.
    check('provenance: the scoring cost is labelled as such', /scoring cost \$0\.000000/.test(det));
    check('…never a bare figure that could be read as the turn cost',
          !/ \$0\.000000/.test(det.replace('scoring cost $0.000000', '')));
    // 2 · the judge absence is NAMED, not implied by the word "deterministic".
    check('provenance: no-judges is STATED', /no judges ran/.test(det));
    check('…and the retired implication is gone', !/deterministic/.test(det));
    const judged = note({ source: 'manual', judged: true, input_tokens: 1200,
                          output_tokens: 340, cost_usd: 0.0021 },
                        { profile_type: 'rag_focused' });
    check('provenance: when judges DO run it says so, with their spend',
          /judges ran \(1,200 in \/ 340 out\)/.test(judged)
          && /scoring cost \$0\.002100/.test(judged));
    check('…and how the scoring was triggered', /scored manually/.test(judged)
          && /scored automatically/.test(det));
    // 3 · WHEN. The panel now asserts "nothing runs here", which provokes the obvious
    //     next question. The `graded after` sub-head that used to answer it is replaced
    //     by the moment groups, so this line is the only place left that does.
    check('provenance: says when the checks were taken', /taken after the answer shipped/.test(det));
    // The SCOPE is the one fact recoverable from the checks themselves (the moments and
    // their counts ARE the suite), so it is the one that may be dropped if the line runs
    // long — pinned as present today, and as the FIRST element so a trim is obvious.
    check('provenance: names the scorer suite, first', /^<div[^>]*>Focus scorers ·/.test(det));
}

// ── 3c · layout: a statement is not a mechanism row; an anchor precedes its qualifier ──
// Both were invisible until the panel was seen rendered, and both are shape defects that
// no logic assertion could reach — so they are pinned on the shape.
{
    const cssSrc = readFileSync(join(ROOT, 'static', 'css', 'trust.css'), 'utf8');
    // 1 · A station-level STATEMENT ("nothing runs here…") used `.ad-gate`, whose grid is
    //     [6px dot | 8.5rem label | 1fr detail], while emitting only TWO children — so a
    //     90-character sentence landed in the FIXED-WIDTH label column and wrapped to
    //     four lines beside a floating dot. A statement is not a row.
    check('the absence line has its own full-width shape, not a mechanism row',
          /\.ad-note\s*\{/.test(cssSrc) && !/ad-gate ad-gate-na/.test(dialSrc));
    check('…and it is used for EVERY station, not special-cased at JUDGED',
          /class="ad-note">\$\{esc\(k === JUDGED \? NOTHING_RUNS : '[^']+'\)\}/.test(dialSrc));
    check('…with a measure, so it never runs edge to edge',
          /\.ad-note[\s\S]{0,240}max-width:\s*\d+ch/.test(cssSrc));
    check('…and no dot: a statement has no state to colour',
          !/ad-note[^>]*>[\s\S]{0,80}ad-gate-dot/.test(dialSrc));

    // 2 · ANCHOR BEFORE QUALIFIER. The provenance was PREPENDED by the caller, so it
    //     printed above the headline it qualifies — invisible while the scorecard had
    //     its own title bar, wrong the moment the headline became the first thing in
    //     JUDGED's panel. Collapsed, the headline IS the answer.
    const vcStart = uiSrc.indexOf('function _renderVerdictChips(verdicts, noteHtml)');
    const vcEnd = uiSrc.indexOf('\nexport function renderHistoricalTrace', vcStart);
    check('the chip renderer takes the note rather than being prepended to', vcStart > -1);
    const vcBody = uiSrc.slice(vcStart, vcEnd);
    check('the headline renders BEFORE the provenance',
          vcBody.indexOf('quality-chip-toggle') < vcBody.indexOf('${noteHtml'));
    // The provenance is the LAST fold, closed — it qualifies the headline and sits beneath
    // the moment shelves, never above them (the shelves are the dial's fold idiom).
    check('…and the provenance sits INSIDE a closed fold, after the moment shelves',
          /<details class="ad-grp"><summary class="ad-grp-h"><span class="ad-grp-lead">scoring run<[\s\S]*\$\{noteHtml\}/.test(vcBody)
          && vcBody.indexOf('ad-grp-lead">taken at<') < vcBody.indexOf('ad-grp-lead">scoring run<'));
    check('JUDGED shelves its measures by MOMENT as fold lines, folded by state',
          /<details class="ad-grp"\$\{worst === 'pass' \? '' : ' open'\}><summary class="ad-grp-h ad-gate-\$\{worst\}">/.test(vcBody)
          && /'all quiet'/.test(vcBody) && /flagged for review` : 'all quiet'/.test(vcBody));
    check('…so the caller never prepends it again',
          !/prefixHtml \+ _renderVerdictChips/.test(uiSrc));

    // 3 · The station row ADAPTS to the width it gets. A fixed label column starved the
    //     reading in this ~300px panel — "confirmed drift — answer withheld" wrapped to
    //     three lines beside a half-empty label. Stacked is the DEFAULT (the width this
    //     panel is usually at, and the no-container-query fallback); columns return only
    //     where they fit.
    check('the row stacks by default rather than starving the reading',
          /\.ad-gate\s*\{[^}]*grid-template-columns:\s*6px minmax\(0, 1fr\)/.test(cssSrc));
    check('…and the section declares a container so the row can adapt at all',
          /\.ad-section\s*\{[^}]*container-type:\s*inline-size/.test(cssSrc));
    check('…returning to columns only where there is room',
          /@container \(min-width:[^)]+\)\s*\{[\s\S]{0,320}\.ad-gate\b/.test(cssSrc));
    check('…with both parts in the content column when stacked',
          /\.ad-gate-label,\s*\.ad-gate-detail\s*\{\s*grid-column:\s*2/.test(cssSrc));
    // The marker marks the MECHANISM, so it sits on that line — not centred on a block
    // whose second line is the reading.
    check('the marker sits on the first line, not the block centre',
          /\.ad-gate-dot\s*\{[^}]*margin-top/.test(cssSrc)
          && !/\.ad-gate-dot\s*\{[^}]*align-self:\s*center/.test(cssSrc));
    // Two populations must read as two blocks, not one list with a stray label in it.
    check('the second population is separated from the first',
          /\.ad-gate \+ \.ad-sub-head,\s*\.ad-note \+ \.ad-sub-head\s*\{[^}]*margin-top/.test(cssSrc));
    // One rule per selector: the duplicate silently overrode the first's margin.
    check('the section head is declared once', (cssSrc.match(/\.ad-section-head \{/g) || []).length === 1);
}

// ── 3d · the scorecard has exactly ONE home ─────────────────────────────────
// MIGRATED MEANS MIGRATED. The scorecard is JUDGED's content, so with the dial on screen
// it lives in JUDGED's section and the card's shell collapses — permanently, not per-pin.
//
// The defect this closes was narrow and was shipped: `qc-adopted` was applied by the
// ui.js adoption ONLY, and on RELOAD the card is created BEFORE the dial injects — so
// that path never marked the shell and left an empty, unopenable container behind. An
// intermediate fix made placement follow the PIN, on the theory that results inside a
// hidden section were themselves a defect. They are not; that is the migration working.
// Following the pin fixed nothing and added an oddity of its own — a Scorecard box
// appearing because the reader pinned GROUNDED, a station it has nothing to do with.
{
    const dialStart = dialSrc.indexOf("const SCORECARD_SLOT = '#assurance-dial .ad-scorecard-slot';");
    const dialEnd = dialSrc.indexOf('\n    function stationReading(', dialStart);
    check('the placement rule is declared', dialStart > -1);
    const place = new Function(`${dialSrc.slice(dialStart, dialEnd)}\n return placeScorecard;`)();

    const scenario = (hasSlot) => {
        const res = { classList: { add() {}, remove() {} }, _at: null };
        const card = { classList: { _s: new Set(), add(c) { this._s.add(c); },
                                    remove(c) { this._s.delete(c); }, contains(c) { return this._s.has(c); } },
                       contains: () => res._at === 'card', appendChild: () => { res._at = 'card'; } };
        const slot = hasSlot ? { contains: () => res._at === 'slot',
                                 appendChild: () => { res._at = 'slot'; } } : null;
        global.document = { getElementById: (id) => (id === 'trajectory-eval-results' ? res : card),
                            querySelector: () => slot };
        place(global.document);
        return { at: res._at, collapsed: card.classList.contains('qc-adopted') };
    };
    const withDial = scenario(true);
    const noDial = scenario(false);
    check('with the dial on screen the scores live in JUDGED', withDial.at === 'slot');
    check('…and the card shell collapses, leaving no empty container',
          withDial.collapsed);
    check('no dial (legacy strip) → the card is still their home', noDial.at === 'card');
    check('…and its shell stays visible there', !noDial.collapsed);
    // Placement must NOT depend on which station is pinned — that was the oddity.
    check('placement does not consult the pin',
          !/offsetParent/.test(dialSrc.slice(dialStart, dialEnd)));
    check('…and the pin handler does not re-place',
          !/s\.hidden = pinned !== k;[\s\S]{0,200}placeScorecard/.test(dialSrc));
    // ONE rule: ui.js delegates, never keeps a second copy of the decision — a second
    // copy is exactly how the live and reload paths diverged.
    check('ui.js delegates to the dial rather than re-deciding',
          /D\.placeScorecard/.test(uiSrc) && !/\.ad-scorecard-slot/.test(uiSrc));
    // The scorecard is ONE element holding the Live Status turn's measures. Placing it in
    // whichever dial rendered last took it into an answer's capsule (another turn) and left
    // Live Status's JUDGED showing "members · 24" over nothing — on a reloaded turn, the
    // moment a capsule was opened.
    check('the scorecard goes only into the Live Status dial (#assurance-dial), never a capsule',
          /const SCORECARD_SLOT = '#assurance-dial \.ad-scorecard-slot';/.test(dialSrc)
          && /host\.querySelector\(SCORECARD_SLOT\) \|\| document\.querySelector\(SCORECARD_SLOT\)/.test(dialSrc));
}

// ── 3e · the scorecard renders a RED turn ──────────────────────────────────
// `stateOf` called `isGating` before its `const` declaration: every turn with a failing
// measure threw "Cannot access 'isGating' before initialization" (since 2026-10-23), so the
// scorecard and JUDGED's members came up empty exactly when a measure needed the reader.
// The suite only ever rendered amber and green. Execute the real renderer with each band.
{
    const a = uiSrc.indexOf('function _renderVerdictChips(verdicts, noteHtml)');
    const z = uiSrc.indexOf('\n}\n', a) + 2;
    check('the scorecard renderer is extracted', a > 0 && z > a);
    // Its collaborators are stubbed; the body under test is the shipped one.
    const render = new Function('_stageGroupsForCard', '_dimAttrs', '_dimLabel', '_fmtMetricScore', '_INLINE_EVIDENCE', 'window',
        `${uiSrc.slice(a, z)}\n return _renderVerdictChips;`)(
        (vs) => [{ label: 'Retrieve', stage: 'retrieve', desc: '', verdicts: vs }],
        () => '', (d) => d, (d, s) => String(s), new Set(), {});
    const tryRender = (vs) => { try { return render(vs, ''); } catch (e) { return 'THREW: ' + e.message; } };
    const red = tryRender([{ dimension: 'retrieval_relevance', band: 'danger', passed: false },
                           { dimension: 'answer_present', band: 'healthy', passed: true }]);
    check('a turn with a failing measure renders (no TDZ throw)', !/^THREW/.test(red));
    check('…its moment is open and reads failing', /<details class="ad-grp" open>/.test(red) && /1 failing/.test(red));
    check('…an advisory failure (gating:false) reads flagged, not failing',
        /flagged for review/.test(tryRender([{ dimension: 'numeric_fidelity', band: 'danger', passed: false, gating: false }])));
    check('amber and green still render', !/^THREW/.test(tryRender([{ dimension: 'x', band: 'review', passed: true }, { dimension: 'y', band: 'healthy', passed: true }])));
    check('isGating is declared before the shelves use it',
        uiSrc.indexOf('const isGating = (v) => v.gating !== false;', a) < uiSrc.indexOf('const stateOf = (v) =>', a));
}

// ── 4 · the judges question, pinned: judges span the STAGES ─────────────────
// (Why the judges toggle is turn-level, never group-level: the 9 judge dimensions are
// spread across four different stages, so no single group's control could govern them.)
const _jStage = (d) => STAGE_MODEL.dimensions[d];
check('groundedness judge → COMPOSE', _jStage('groundedness') === 'compose');
check('scope_faithfulness judge → COMPOSE', _jStage('scope_faithfulness') === 'compose');
check('answer_groundedness judge → COMPOSE', _jStage('answer_groundedness') === 'compose');
check('source_consistency judge → COMPOSE', _jStage('source_consistency') === 'compose');
check('synthesis_quality judge → COMPOSE', _jStage('synthesis_quality') === 'compose');
check('content_safety judge → DELIVER', _jStage('content_safety') === 'deliver');
check('answer_correctness judge → DELIVER', _jStage('answer_correctness') === 'deliver');
check('instruction_following judge → DELIVER', _jStage('instruction_following') === 'deliver');
check('tool_arg_optimality judge → ACT', _jStage('tool_arg_optimality') === 'act');
check('judges span MORE THAN ONE stage — so the control cannot be per-group',
      new Set(['groundedness', 'content_safety', 'tool_arg_optimality']
          .map(_jStage)).size > 1);

// ── 5 · absence tolerance ───────────────────────────────────────────────────
// Before the served model lands (or without QualityDimensions at all) the card must show
// ONE flat, unheaded list — never a confident wrong grouping, and never an invented
// taxonomy. This is the same honest-absence rule the station grouping had.
const flat = groupsFor(verdicts, null);
check('no model → one unheaded group holding everything',
      flat.length === 1 && flat[0].verdicts.length === verdicts.length && flat[0].label === '');
const notReady = groupsFor(verdicts, { stagesReady: () => false, groupByStage: () => [] });
check('model not ready → the same flat list, not a partial grouping',
      notReady.length === 1 && notReady[0].verdicts.length === verdicts.length);
const thrower = groupsFor([{ dimension: 'x' }],
    { stagesReady: () => true, groupByStage: () => { throw new Error('boom'); } });
check('a throwing resolver degrades to the flat list, never breaks the card',
      thrower.length === 1 && thrower[0].verdicts.length === 1);
// An unplaced check is SHOWN under no heading — a registry gap must not vanish from the
// surface that would reveal it (the backend gate fails on one; the card must not hide it).
const partial = groupsFor([{ dimension: 'cost' }, { dimension: 'not_a_dimension' }], QD_STUB);
check('an unplaced check is shown, headless, never dropped',
      partial.flatMap((g) => g.verdicts).length === 2
      && partial.some((g) => g.label === '' && g.verdicts[0].dimension === 'not_a_dimension'));

// ── 6 · structural pins ─────────────────────────────────────────────────────
check('the KPI-tier taxonomy is retired from ui.js', !uiSrc.includes('_KPI_TIER'));
check('the station grouping is retired from the scorecard',
      !uiSrc.includes('_stationGroupsForCard') && !uiSrc.includes('_CARD_STATIONS'));
check('stage headers carry their description as a tooltip (the retired route\'s job)',
      uiSrc.includes('data-tip="${esc(g.desc)}"'));
check('card grouping routes through the extracted function',
      uiSrc.includes('_stageGroupsForCard(verdicts'));
check('the card declares no taxonomy of its own — it reads the served model',
      !uiSrc.includes('const STAGE_DIMENSIONS') && !/const\s+STAGE_OF\s*=/.test(uiSrc));
// R-6 — the scoring controls are adopted onto the band from BOTH sides, and the
// dial RESCUES them before its innerHTML wipe (else a live re-render destroys
// the wired buttons).
check('ui.js adopts the control cluster into the band slot',
      uiSrc.includes("'#assurance-band .ad-ctl-slot'") && uiSrc.includes('quality-ctl-cluster'));
check('dial exposes the slot', dialSrc.includes('ad-ctl-slot'));
// Bounded by the NEXT top-level declaration, not by a magic character count: a fixed
// window silently stops covering the thing it was written for the moment the function
// grows, which is how this assertion came to pass over code it no longer reached.
const renderStart = dialSrc.indexOf('function render(host, assurance');
const _afterRender = dialSrc.indexOf('\n    function ', renderStart + 10);
const renderSlice = dialSrc.slice(renderStart, _afterRender > 0 ? _afterRender : undefined);
const rescueAt = renderSlice.indexOf('_ctl.remove()');
const wipeAt = renderSlice.indexOf('host.innerHTML');
check('dial rescues the adopted controls BEFORE the innerHTML wipe',
      rescueAt > -1 && wipeAt > -1 && rescueAt < wipeAt);
check('dial re-seats after the wipe', renderSlice.indexOf('_slot.appendChild', wipeAt) > wipeAt);
// The adopted SCORECARD needs the identical treatment — it lives inside a station panel,
// which the same wipe destroys. Rescue before, re-seat after, and RESTORE to the card
// when JUDGED is not pinned: a rescue that cannot re-seat must give the nodes back, or a
// re-render silently costs the reader the entire scorecard.
const scRescueAt = renderSlice.indexOf('_sc.remove()');
check('dial rescues the adopted scorecard BEFORE the wipe',
      scRescueAt > -1 && scRescueAt < wipeAt);
// Re-placed through the ONE rule after the wipe, rather than by a second copy of the
// decision inline here — the placement itself is asserted by execution in §3d.
check('…re-places it through the shared rule after',
      renderSlice.indexOf('placeScorecard(host)', wipeAt) > wipeAt);
// A rescue that cannot re-seat must RESTORE: the rescued node is detached, so the card
// takes it back before the rule runs, or a re-render drops the scorecard on the floor.
check('…and a detached rescue is handed back to the card first',
      /_sc && !_sc\.isConnected[\s\S]{0,200}_card\.appendChild\(_sc\)/.test(renderSlice));
// The Guide's turn scope travels with the adopted cluster.
check('guide context is stamped on the travelling cluster',
      uiSrc.includes('quality-ctl-cluster') && uiSrc.includes('!wrap.contains(cluster)'));

// ── 6 · the WARM PATH: a partition nobody fed is not a partition ─────────────
// Sections 1–5 hand `_stationGroupsForCard` a working resolver, so they prove the
// grouping is right and can say nothing about whether it ever RUNS. It did not:
// `stationOf` reads a table filled by one fetch, and two optimistic latches
// ("we called it") let an empty table render as a confident all-JUDGED card —
// attributing GROUNDED's failures to the wrong station. The defect is the latch
// semantics, so that is what is pinned here, against the REAL module.
const qdPath = join(here, '..', 'static', 'js', 'handlers', 'qualityDimensions.js');
const qdSrc = readFileSync(qdPath, 'utf8');

/** Load a fresh qualityDimensions.js with a controllable vocabulary fetch.
 *  `plan` is called per request and returns { ok, entries } or throws. */
function loadQD(plan) {
    const win = { addEventListener() {}, removeEventListener() {} };
    const doc = {
        readyState: 'complete', addEventListener() {}, querySelectorAll() { return []; },
        getElementById() { return null; }, querySelector() { return null; },
        body: { appendChild() {} }, head: { appendChild() {} }, documentElement: {},
        createElement() {
            return { style: {}, setAttribute() {}, appendChild() {},
                     classList: { add() {}, remove() {} } };
        },
    };
    let calls = 0;
    globalThis.localStorage = { getItem: () => 'tok', setItem() {}, removeItem() {} };
    globalThis.fetch = () => {
        const r = plan(++calls);
        return Promise.resolve({ ok: !!r.ok, json: () => Promise.resolve({ entries: r.entries || [] }) });
    };
    new Function('window', 'document', qdSrc)(win, doc);
    return { QD: win.QualityDimensions, calls: () => calls };
}
// A mechanism entry shaped like the live endpoint: it carries the station, and the
// dims it owns are what `stationOf` walks back through.
const VOCAB = [{ k: 'grounding_gate', label: 'Grounding gate', station: 'grounded',
                 dims: ['grounded_overlap', 'numeric_fidelity'] }];

check('QD exposes stationsReady (readiness is asked of the owner)',
      typeof loadQD(() => ({ ok: true, entries: VOCAB })).QD.stationsReady === 'function');

// 6a — the in-flight race: a second caller must not resolve over an unloaded table.
{
    const { QD } = loadQD(() => ({ ok: true, entries: VOCAB }));
    check('before the warm, an owned dim falls to the residue',
          QD.stationOf('grounded_overlap') !== 'grounded' && QD.stationsReady() === false);
    const first = QD.ensureVocabulary();
    const second = QD.ensureVocabulary();          // DURING flight — the race
    check('a concurrent caller gets the SAME in-flight promise, not an instant resolve',
          first === second);
    await second;
    check('after the warm, the owned dim resolves to its station',
          QD.stationOf('grounded_overlap') === 'grounded' && QD.stationsReady() === true);
}
// 6b — success latches: no refetch once the table is populated.
{
    const { QD, calls } = loadQD(() => ({ ok: true, entries: VOCAB }));
    await QD.ensureVocabulary();
    await QD.ensureVocabulary();
    check('a successful warm latches (one fetch, not two)', calls() === 1);
    // An older backend that serves entries but NO stage block must still latch — both
    // models ride one response, so retrying cannot produce a block the server does not
    // send, and keying the latch on both would refetch on every call forever.
    const older = loadQD(() => ({ ok: true, entries: VOCAB }));   // no `stages` key
    await older.QD.ensureVocabulary();
    await older.QD.ensureVocabulary();
    check('a payload with no stage block still latches (no refetch storm)',
          older.calls() === 1);
    check('...and the stage model stays empty, so grouping degrades to a flat list',
          older.QD.stagesReady() === false);
}
// 6c — failure stays RETRYABLE. A 401 before login latched the empty table for the
// whole page session; every card after it skipped the warm and rendered flat.
{
    const { QD, calls } = loadQD((n) => (n === 1 ? { ok: false } : { ok: true, entries: VOCAB }));
    await QD.ensureVocabulary();
    check('a failed warm leaves the table empty', QD.stationsReady() === false);
    await QD.ensureVocabulary();
    check('a failed warm is retried by the next caller', calls() === 2);
    check('the retry populates the partition', QD.stationsReady() === true);
}
// 6d — an empty payload is a failure, not a success to latch.
{
    const { QD, calls } = loadQD((n) => (n === 1 ? { ok: true, entries: [] }
                                                 : { ok: true, entries: VOCAB }));
    await QD.ensureVocabulary();
    check('an empty payload does not latch', QD.stationsReady() === false);
    await QD.ensureVocabulary();
    check('an empty payload is retried', calls() === 2 && QD.stationsReady() === true);
}

// 6e — ui.js keeps NO second latch. Two latches over one fact IS the defect: the
// local one recorded "we called it" and, because `ensureVocabulary` resolves even
// when nothing registered, one miss disabled the warm for every later card.
check('ui.js holds no private vocabulary-readiness latch',
      !/_stationVocabReady/.test(uiSrc));
check('ui.js asks the owner for readiness', uiSrc.includes('QD.stationsReady'));

// 6f — execute the REAL repaint decision. Extracted with its collaborators injected,
// so this is the shipped control flow, not a paraphrase of it.
{
    const pStart = uiSrc.indexOf('function _paintQualityResults');
    const pEnd = uiSrc.indexOf('\nfunction _renderVerdictChips', pStart);
    check('_paintQualityResults extracted', pStart > 0 && pEnd > pStart);
    const body = uiSrc.slice(pStart, pEnd);
    const make = (QDStub) => {
        let paints = 0;
        // eslint-disable-next-line no-new-func
        const fn = new Function('_renderVerdictChips', '_setQualityGuideContext', '_QD',
            `${body}\n return _paintQualityResults;`)(
            () => { paints++; return ''; }, () => {}, () => QDStub);
        return { fn, paints: () => paints };
    };
    const out = { innerHTML: '', isConnected: true };
    // Ready up-front → paint once, no warm, no second paint.
    const readyQD = { stationsReady: () => true, stagesReady: () => true, ensureVocabulary: () => Promise.resolve() };
    const a = make(readyQD);
    a.fn(out, '', [], null, null);
    await Promise.resolve();
    check('an already-partitioned card paints once', a.paints() === 1);
    // Warm delivers the STAGE model → repaint. The card groups by stage, so readiness
    // is asked of the stage model: waiting on `stationsReady` would return early on a
    // different fact and paint a flat list that never repaints.
    let ready = false;
    const b = make({ stationsReady: () => true, stagesReady: () => ready,
                     ensureVocabulary: () => Promise.resolve().then(() => { ready = true; }) });
    b.fn(out, '', [], null, null);
    await new Promise((r) => setTimeout(r, 0));
    check('a warm that delivers the stage model repaints', b.paints() === 2);
    // The complement: readiness must be asked of the model the card GROUPS BY.
    let sReady = false;
    const stagedOnly = make({ stationsReady: () => sReady, stagesReady: () => true,
                     ensureVocabulary: () => Promise.resolve().then(() => { sReady = true; }) });
    stagedOnly.fn(out, '', [], null, null);
    await new Promise((r) => setTimeout(r, 0));
    check('a card whose STAGE model is already in does not wait on the station links',
          stagedOnly.paints() === 1);
    // Warm delivers nothing → no repaint (redrawing the same residue is spend, not news).
    const c = make({ stationsReady: () => false, ensureVocabulary: () => Promise.resolve() });
    c.fn(out, '', [], null, null);
    await new Promise((r) => setTimeout(r, 0));
    check('a warm that delivers nothing does not repaint the same residue', c.paints() === 1);
    // Detached card → never repaint (the trace can rebuild mid-fetch).
    const gone = { innerHTML: '', isConnected: false };
    let ready2 = false;
    const d = make({ stationsReady: () => ready2,
                     ensureVocabulary: () => Promise.resolve().then(() => { ready2 = true; }) });
    d.fn(gone, '', [], null, null);
    await new Promise((r) => setTimeout(r, 0));
    check('a detached card is not repainted', d.paints() === 1);
}

// ── 7 · registration is ADDITIVE and order-independent ──────────────────────
// TWO producers feed the same registry into the Guide: the assurance dial registers
// the CURRENT TURN's points on every render (`guide_entry`), and the vocabulary
// endpoint serves the full registry when the Guide first opens. They are projections
// of one source, so the answer must not depend on which arrived first — it did:
// `registerEntries` bailed on a known key, so a mechanism the dial registered first
// kept its dims and lost its station, and its dimensions fell to the JUDGED residue
// while an identical mechanism absent from that turn read its real station. One
// partition, two answers, decided by load order.
const apGuideM = apSrc.match(/def guide_entry[\s\S]*?\n\n\n/);
check('guide_entry extracted', !!apGuideM);
check('the per-turn guide projection carries its station (complete projection)',
      !!apGuideM && /"station":/.test(apGuideM[0]));

// The dial's payload shape: identified, described, dims — historically no station.
const DIAL_PARTIAL = [{ k: 'authority', desc: 'Whose authority the turn ran under.',
                        dims: ['must_not_call', 'tool_subset'], cls: 'boundary' }];
const FULL_VOCAB = [{ k: 'authority', label: 'Authority', station: 'bounded',
                      desc: 'Whose authority the turn ran under.',
                      dims: ['must_not_call', 'tool_subset'], cls: 'boundary' }];
{   // dial first, vocabulary second — the shipped order, and the failing one.
    const { QD } = loadQD(() => ({ ok: true, entries: [] }));
    QD.registerEntries(DIAL_PARTIAL);
    check('a station-less pre-registration renders its measures',
          QD.relatedOf('authority').length === 2);
    QD.registerEntries(FULL_VOCAB);
    check('a later, richer payload COMPLETES the entry (dial→vocabulary)',
          QD.stationOf('tool_subset') === 'bounded' && QD.stationOf('must_not_call') === 'bounded');
}
{   // vocabulary first, dial second — must not be downgraded by the partial payload.
    const { QD } = loadQD(() => ({ ok: true, entries: [] }));
    QD.registerEntries(FULL_VOCAB);
    QD.registerEntries(DIAL_PARTIAL);
    check('a later, POORER payload never downgrades an entry (vocabulary→dial)',
          QD.stationOf('tool_subset') === 'bounded');
}
{   // the guard that started it: a real dimension's definition is still never clobbered.
    const { QD } = loadQD(() => ({ ok: true, entries: [] }));
    const before = QD.label('grounded_overlap');
    QD.registerEntries([{ k: 'grounded_overlap', label: 'HIJACKED', desc: 'no',
                          station: 'guarded' }]);
    check('a registered entry never overwrites a real dimension definition',
          QD.label('grounded_overlap') === before && before !== 'HIJACKED');
}
{   // the whole point: no dimension's station may depend on load order.
    const dims = ['must_not_call', 'tool_subset'];
    const a = loadQD(() => ({ ok: true, entries: [] })).QD;
    a.registerEntries(DIAL_PARTIAL); a.registerEntries(FULL_VOCAB);
    const b = loadQD(() => ({ ok: true, entries: [] })).QD;
    b.registerEntries(FULL_VOCAB); b.registerEntries(DIAL_PARTIAL);
    check('the partition is order-independent across producers',
          dims.every((d) => a.stationOf(d) === b.stationOf(d) && a.stationOf(d) === 'bounded'));
}

// ── 8 · the headline convicts only what is gate-eligible, and drops nothing ──
// A failure CONVICTS only if its dimension is gate-eligible. The card counted every
// `passed === false` and called it a "gating failure", so an advisory
// `numeric_fidelity` read as a conviction while the span it was built from correctly
// reported 0 — the same shape `eval/online.py:gating_failures()` was written to kill,
// one surface further out. The flag is DERIVED server-side from the scorer registry
// (`Verdict.gating`) and shipped on every verdict; the card must never re-derive it.
check('the card reads the shipped gating flag, never a local policy list',
      /v\.gating !== false/.test(uiSrc));
check('the headline counts only gate-eligible failures',
      /gatingFails = verdicts\.filter\(\(v\) => v\.passed === false && isGating\(v\)\)/.test(uiSrc));
{   // Extract the summary arithmetic and drive it — nothing may vanish.
    // `isGating` is declared above the moment shelves (3e), so it travels as its own line.
    const g = uiSrc.indexOf('const isGating = (v) => v.gating !== false;');
    const s = uiSrc.indexOf('const gatingFails', g);
    const e = uiSrc.indexOf('const flaggedTxt', s);
    check('summary arithmetic extracted', g > 0 && s > g && e > s);
    // eslint-disable-next-line no-new-func
    const count = new Function('verdicts', 'bandOf',
        `const isGating = (v) => v.gating !== false;\n${uiSrc.slice(s, e)}\n return { gatingFails, flagged };`);
    const bandOf = (v) => v.band;
    // The live turn: one advisory failure + one review-band metric.
    const turn = [
        { dimension: 'numeric_fidelity', passed: false, gating: false, band: 'danger' },
        { dimension: 'tokens', passed: true, gating: true, band: 'review' },
        { dimension: 'completed', passed: true, gating: true, band: null },
    ];
    const r = count(turn, bandOf);
    check('an advisory failure is NOT a gating failure', r.gatingFails === 0);
    check('…and it is NOT silently dropped — it is flagged', r.flagged === 2);
    // A real conviction still convicts, and the advisory one still shows beside it.
    const r2 = count([{ dimension: 'safety', passed: false, gating: true, band: 'danger' },
                      ...turn], bandOf);
    check('a gate-eligible failure convicts', r2.gatingFails === 1);
    check('…and the advisory ones still appear beside it', r2.flagged === 2);
    // Absent flag (payload older than the serialization) → gating, the backend's rule.
    const r3 = count([{ dimension: 'x', passed: false, band: 'danger' }], bandOf);
    check('an absent flag resolves to gating (unknown never hides a failure)',
          r3.gatingFails === 1);
    // Every non-clean verdict is represented: convictions + flagged == not-clean.
    const all = [...turn, { dimension: 'safety', passed: false, gating: true, band: 'danger' },
                 { dimension: 'cost', passed: true, gating: true, band: 'healthy' }];
    const r4 = count(all, bandOf);
    const notClean = all.filter((v) => v.passed === false || bandOf(v) === 'review').length;
    check('no measure of relevance is dropped from the headline',
          r4.gatingFails + r4.flagged === notClean);
}
// The card is the turn's FULL scorecard grouped by station — not the JUDGED station's
// card. Naming it "Quality" beside a JUDGED group invited exactly that reading.
check('the card is titled Scorecard', />Scorecard</.test(uiSrc));
check('…and no stale "Quality" title survives', !/>Quality</.test(uiSrc));

// ── 9 · a station segment lists BOTH populations ────────────────────────────
// A station's colour is worst-of its MECHANISMS and the MEASUREMENTS they are graded
// by; only the mechanisms had rows, so a station turned red by a measurement showed
// nothing but green dots, and this panel and the scorecard listed different things
// under one station name with no way to tell they were different KINDS of thing.
check('the station panel labels the mechanisms it lists',
      dialSrc.includes("'what ran'") || dialSrc.includes('what ran'));
check('…and lists the measurements for a GUARANTEE station', /function measuresHtml/.test(dialSrc));
check('…rendered into the section for those stations', /measuresHtml\(d\)/.test(dialSrc));
// JUDGED is the exception, and it is the point of the migration: its body is the
// SCORECARD, adopted from the card. Rendering the flat chip list there as well would be
// the same 20 checks twice on one panel — with the copy that has no order and no
// grouping sitting above the copy that has both.
check('JUDGED renders the scorecard slot instead of the flat list',
      /k === JUDGED[\s\S]{0,1400}ad-scorecard-slot/.test(dialSrc));
{
    // Comments sit between the ternary arms, so a distance-bounded regex measures prose
    // rather than code. Strip the comments and assert the property: the slot is the
    // TRUE arm and the flat list the FALSE arm, so JUDGED can never render both.
    const bare = dialSrc.replace(/\/\/[^\n]*/g, '');
    check('…and the flat list is NOT also rendered for JUDGED',
        /k === JUDGED\s*\?\s*\(_holdsScorecard \? `<div class="ad-scorecard-slot"><\/div><div class="ad-judged-own">\$\{judgedMembersHtml\(d\)\}<\/div>` : judgedMembersHtml\(d\)\)\s*:\s*measuresHtml\(d\)/.test(bare));
    // A reloaded turn showed "members · 24" over an empty JUDGED: the one scorecard element had
    // moved into an answer's capsule, or the turn had no stored score to paint into it.
    check('…the slot only on the dial that holds the scorecard; every JUDGED lists its turn\'s own members',
        /_holdsScorecard = host\.id === 'assurance-dial';/.test(bare) && /function judgedMembersHtml\(d\)/.test(bare));
    check('…under their MOMENTS, through the scorecard\'s one renderer (open only where a measure failed or is flagged)',
        /window\.UderiaScorecard = Object\.assign\(window\.UderiaScorecard \|\| \{\}, \{ renderMeasures: \(verdicts\) => _renderVerdictChips\(verdicts, ''\) \}\)/.test(uiSrc)
        && /SC\.renderMeasures\(verdicts\)/.test(bare) && /<details class="ad-grp"\$\{worst === 'pass' \? '' : ' open'\}>/.test(uiSrc));
    check('…an abstention is no verdict (a neutral chip), never an unknown band',
        /const BANDS = \{ healthy: true, review: true, danger: false \};/.test(bare) && /const band = m\.b in BANDS \? m\.b : null;/.test(bare));
    const trustCss = readFileSync(join(here, '..', 'static', 'css', 'trust.css'), 'utf8');
    check('…and in Live Status they give way the moment the scorecard shows (no second copy)',
        /\.ad-section:has\(\.ad-scorecard-slot > #trajectory-eval-results:not\(\.hidden\):not\(:empty\)\) \.ad-judged-own \{ display: none; \}/.test(trustCss));
    check('the scoring controls stay in Live Status too (a capsule rendering last took them)',
        /const _ctl = !_holdsScorecard \? null :/.test(bare) && /const _slot = _holdsScorecard \? host\.querySelector\('\.ad-ctl-slot'\) : null;/.test(bare));
    // Excluding its own declaration — `function measuresHtml(d)` matches a naive
    // call-site pattern, which would let a real second call hide behind the count.
    check('…and measuresHtml is CALLED exactly once (no second, unguarded render)',
        (bare.match(/(?<!function\s)measuresHtml\(d\)/g) || []).length === 1);
}
// The empty-mechanism line at JUDGED must be the STATION's own sentence. The generic
// one implies class-dependence — as if another agent type might have a mechanism here.
// None does, ever, and that is the lane cut stated where a reader asks.
check('JUDGED says nothing runs there, in its own words',
      /nothing runs here/.test(dialSrc) && /k === JUDGED \? NOTHING_RUNS/.test(dialSrc));
check('…routed through the Guide\'s OWN data-dim channel (one navigation channel)',
      /class="ad-meas ad-meas-\$\{esc\(st\)\}" data-dim="/.test(dialSrc));
check('…labelled as measurements, with the count that station owns',
      dialSrc.includes('what it measures') && dialSrc.includes('counted here'));
// The chip-vs-station distinction and the ordering are asserted BEHAVIOURALLY below,
// by executing the real renderer. They used to be greps for two exact source lines —
// which pinned an implementation rather than a property, and would have sailed through
// the rewrite that moved allocation off the mechanisms entirely.
{   // Execute it: the extracted renderer must emit one chip per owned dimension.
    const ms = dialSrc.indexOf('function measuresHtml');
    const me = dialSrc.indexOf('\n    // ── the mark key', ms);   // measuresHtml + measureChips, together
    check('measuresHtml extracted', ms > 0);
    // RANK comes from the ONE trust vocabulary (TrustGrammar), loaded for real so the
    // gate ranks exactly as the shipped dial does rather than against a stub copy.
    const tgWin = {}; const tgDoc = { addEventListener() {}, createElement() { return {}; },
                                      head: { appendChild() {} }, getElementById() { return null; } };
    // eslint-disable-next-line no-new-func
    new Function('window', 'document',
        readFileSync(join(here, '..', 'static', 'js', 'handlers', 'trustGrammar.js'), 'utf8'))(tgWin, tgDoc);
    const RANK = (globalThis.TrustGrammar && globalThis.TrustGrammar.TURN_RANK)
        || (tgWin.TrustGrammar && tgWin.TrustGrammar.TURN_RANK);
    check('the REAL trust-vocabulary rank is in play', !!RANK && RANK.danger > RANK.pass);
    // The real SUB_TIP declaration travels with the function — one source, so the gate
    // executes the shipped strings rather than a stub that could drift from them.
    const tipSrc = dialSrc.match(/const SUB_TIP = \{[\s\S]*?\n    \};/);
    check('SUB_TIP extracted', !!tipSrc);
    // `_st` — the shipped contribution reader — travels with the function for the same
    // reason SUB_TIP does: the gate must execute the real one. It is the seam where a
    // band conversion would reappear, so a stub copy here would defeat the point.
    const stSrc = dialSrc.match(/const _st = \([\s\S]*?;\n/);
    check('the shipped contribution reader is extracted', !!stSrc);
    // eslint-disable-next-line no-new-func
    const mh = new Function('esc', 'RANK', `${tipSrc[0]}\n${stSrc[0]}\n${dialSrc.slice(ms, me > ms ? me : ms + 2600)}\n return measuresHtml;`)(
        (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])), RANK);
    // The station's own allocated set, as `allocate_measurements` emits it: `b` is what
    // was measured, `st` what it contributes, `bst` the measurement as a state for the
    // chip. The renderer chooses between them and converts nothing.
    const D = (k, b, st) => ({ k, b, st: st || { healthy: 'pass', review: 'warn', danger: 'danger' }[b] || 'na',
                               bst: { healthy: 'pass', review: 'warn', danger: 'danger' }[b] || 'na' });
    const html = mh({ dims: [D('grounded_overlap', 'healthy'), D('numeric_fidelity', 'danger')] });
    check('one chip per owned dimension', (html.match(/class="ad-meas /g) || []).length === 2);
    check('the failing measurement leads',
          html.indexOf('numeric_fidelity') < html.indexOf('grounded_overlap'));
    check('a station with no measurements renders nothing', mh({ dims: [] }) === '');
    // The middle band, which nothing exercised for a whole release: it must render as
    // amber and it must sort above a healthy one.
    const amber = mh({ dims: [D('grounded_overlap', 'healthy'), D('context_utilization', 'review')] });
    check('a review-band chip renders amber, never an unstyled class',
          /ad-meas-warn/.test(amber) && !/ad-meas-review/.test(amber));
    check('…and sorts above a healthy measurement',
          amber.indexOf('context_utilization') < amber.indexOf('grounded_overlap'));
    // Rule 3.0c: the chip shows what was MEASURED even where the station folded
    // something quieter — a cleared pair measures danger and contributes warn.
    const disputed = mh({ dims: [D('grounded_overlap', 'danger', 'warn')] });
    check('the chip shows the MEASUREMENT, not the station contribution',
          /ad-meas-danger/.test(disputed));
}
check('the two populations share the scorecard\'s band vocabulary',
      /\.ad-meas-danger \{[\s\S]*?--trust-danger/.test(
          readFileSync(join(here, '..', 'static', 'css', 'trust.css'), 'utf8')));

// ── 10 · the panel STATES which population acts ─────────────────────────────
// Listing both side by side is not enough: with no statement of the relationship,
// proximity reads as causation — an operator concluded the measurements open and
// close the gates, and nothing on screen corrected it. The distinction that matters
// is WHEN each runs and whether it can act.
check('the members are named as what the station\u2019s colour is the worst of — words, no glyph',
      /'members'\} \\u00b7 \$\{d\.points\.length\}/.test(dialSrc) && dialSrc.includes('the worst of these colours')
      && !/memberMark|qd-mem-mark/.test(dialSrc) && !dialSrc.includes('acted during the turn'));
check('the graded population is marked as graded after',
      dialSrc.includes('graded after'));
check('…and the full relationship is one hover away, from ONE declaration',
      /const SUB_TIP = \{/.test(dialSrc)
      && /A member is what a station\\u2019s colour is the worst of/.test(dialSrc) && /At JUDGED: /.test(dialSrc)
      && /nothing here can open or close a gate/.test(dialSrc));
check('…carried by the platform tooltip, never a native title',
      /class="ad-sub-head u-tip" data-tip=/.test(dialSrc));

// ── 11 · ONE definition of "the delivered text is not the model's" ──────────
// Every scorer that compares the delivered text against its sources must abstain
// when a gate replaced that text with the platform's own. It was stated in ONE of
// the three, so a blocked turn showed `grounded_overlap` correctly abstaining beside
// `numeric_fidelity` red and `context_utilization` at 3% — both grading a ~30-word
// withholding notice that QUOTES the spans it reports as unsupported. The gate acting
// blinded the measurements at the exact moment they are needed to explain the block.
const typesSrc = readFileSync(join(here, '..', 'src', 'trusted_data_agent', 'eval', 'types.py'), 'utf8');
const relSrc = readFileSync(join(here, '..', 'src', 'trusted_data_agent', 'eval', 'scorers', 'reliability.py'), 'utf8');
const detSrc = readFileSync(join(here, '..', 'src', 'trusted_data_agent', 'eval', 'scorers', 'deterministic.py'), 'utf8');
check('the rule is declared ONCE, on the record every scorer already holds',
      /def platform_authored_answer/.test(typesSrc));
check('…and covers all three ways a gate replaces the answer',
      /grounding/.test(typesSrc) && /honesty/.test(typesSrc) && /guardrail/.test(typesSrc));
check('…and deliberately excludes a REDACT (still the model\'s words, values masked)',
      /REDACT is deliberately NOT included/.test(typesSrc));
// The PROPERTY, not the call site. This used to grep for
// `run.platform_authored_answer()` in each scorer — which pinned an implementation and
// broke the moment the rule moved into the assurance envelope, where it is enforced ONCE
// (`Assessor.run` + `subject_is_model_output`) instead of being remembered at every site.
// Re-litigating an invariant per call site is how one site eventually forgets, so the
// gate now asserts that the rule is REACHED, wherever it lives.
const asrcSrc = readFileSync(join(here, '..', 'src', 'trusted_data_agent', 'agent',
                                  'assurance', 'contract.py'), 'utf8');
const rcSrc = readFileSync(join(here, '..', 'src', 'trusted_data_agent', 'agent',
                                'assurance', 'run_context.py'), 'utf8');
check('the envelope enforces it for every assessor whose subject is the model',
      /subject_is_model_output and ctx\.platform_authored/.test(asrcSrc));
check('…fed from the ONE declaration on the record',
      /platform_authored_answer/.test(rcSrc));
for (const [name, src] of [['numeric_fidelity', relSrc],
                           ['context_utilization', relSrc]]) {
    check(`${name} still asks the shared rule directly (not yet on the substrate)`,
          (src.match(/run\.platform_authored_answer\(\)/g) || []).length >= 1);
}
check('…and grounded_overlap reaches it through the envelope instead',
      /subject_is_model_output = True/.test(readFileSync(
          join(here, '..', 'src', 'trusted_data_agent', 'agent', 'assurance',
               'assessors', 'answer_support.py'), 'utf8')));
check('…and no scorer keeps a private copy of the rule',
      !/_g\.get\("decision"\) in \("block", "annotate"\)/.test(detSrc)
      && !/get\("decision"\) in \("block", "annotate"\)/.test(relSrc));

// ── 12 · the Guide separates what DECIDES from what GRADES ──────────────────
// A mechanism's dimension list held both — the judges the grounding gate escalates
// to in-path (`groundedness` returned 0✓/3✗ and withheld an answer) and the seven
// scorers that only grade it — under ONE "measured by" heading. A label ambiguous
// about direction, over a list ambiguous about role: an operator concluded the
// measurements open and close the gates.
const qdSrc2 = readFileSync(qdPath, 'utf8');
check('in-path judges are DECLARED in the registry, never inferred from a name',
      /POINT_JUDGES: Dict\[str, Tuple\[str, \.\.\.\]\]/.test(apSrc));
check('…and the declaration must be a subset of what the mechanism owns',
      /def judge_partition_conflicts/.test(apSrc));
{   // The declaration is reachable: every declared judge is owned by its mechanism.
    const pjM = apSrc.match(/POINT_JUDGES: Dict\[str, Tuple\[str, \.\.\.\]\] = \{([\s\S]*?)\n\}/);
    check('POINT_JUDGES block found', !!pjM);
    const pdM = apSrc.match(/POINT_DIMENSIONS[\s\S]*?=\s*\{([\s\S]*?)\n\}/);
    const owned = {};
    for (const e of pdM[1].matchAll(/"([\w]+)":\s*\(([^)]*)\)/g)) {
        owned[e[1]] = [...e[2].matchAll(/"([\w]+)"/g)].map((m) => m[1]);
    }
    let declared = 0, unreachable = 0;
    for (const e of pjM[1].matchAll(/"([\w]+)":\s*\(([^)]*)\)/g)) {
        for (const j of [...e[2].matchAll(/"([\w]+)"/g)].map((m) => m[1])) {
            declared++;
            if (!(owned[e[1]] || []).includes(j)) unreachable++;
        }
    }
    check('at least one in-path judge is declared', declared >= 1);
    check('every declared judge is owned by its mechanism (no unreachable declaration)',
          unreachable === 0);
}
check('the mechanism payload serves its in-path judges', /"judges": list\(POINT_JUDGES/.test(apSrc));
check('…and the Guide registers them', /ENTRY_JUDGES\[e\.k\] = e\.judges\.slice\(\)/.test(qdSrc2));
check('the Guide separates escalates-to from graded-by',
      /chipRow\('escalates to', judges[,)]/.test(qdSrc2)
      && /chipRow\('graded by', graded[,)]/.test(qdSrc2));
check('…and a judge is EXCLUDED from the graded-by list (never both)',
      /moduleSharers\(k\) : \(RELATED\[k\] \|\| \[\]\)\)\s*\.filter\(\(d\) => judges\.indexOf\(d\) < 0\)/.test(qdSrc2));
check('the REVERSE direction carries the same distinction',
      /chipRow\('decides for', decidesFor[,)]/.test(qdSrc2)
      && /chipRow\('grades', gradesFor[,)]/.test(qdSrc2));
check('…so the two directions cannot contradict (one relation, one split)',
      /decidesFor = back\.filter\(\(m\) => \(ENTRY_JUDGES\[m\] \|\| \[\]\)\.indexOf\(k\) >= 0\)/.test(qdSrc2));
{   // Asserted on the RENDERED output, not the source: a grep over the file also reads
    // comments, and the comment recording why the label was retired tripped it.
    const w3 = { addEventListener() {}, removeEventListener() {} };
    const d3 = {
        readyState: 'complete', addEventListener() {}, querySelectorAll() { return []; },
        getElementById() { return null; }, querySelector() { return null; },
        body: { appendChild() {} }, head: { appendChild() {} }, documentElement: {},
        createElement() {
            return { style: {}, setAttribute() {}, appendChild() {},
                     classList: { add() {}, remove() {} } };
        },
    };
    globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
    // eslint-disable-next-line no-new-func
    new Function('window', 'document', qdSrc2)(w3, d3);
    const QD3 = w3.QualityDimensions;
    QD3.registerEntries([{ k: 'm_dec', desc: 'x', station: 'grounded',
                           dims: ['grounded_overlap', 'groundedness'],
                           judges: ['groundedness'] }]);
    const fwd = QD3.relatedChips('m_dec');
    // ── 13 · the acted dot says MECHANISM, not "gate" ───────────────────────────
// `acted` is set by nine mechanisms and only four are gates — the rest are a tool
// DENIED by Agent Authority, a quarantined document, a sensitivity ceiling, a
// firewall label, a review parking, an integrity mismatch. Every surface called the
// mark "a gate acted", which narrows a correct computation and sends a reader looking
// for a gate that never fired.
{
    // For each line that closes a reading with a truthy `acted`, walk BACK to the
    // nearest `_mk("<key>"` — the reading it belongs to. A forward regex over the
    // block does not survive these multi-line calls (it silently matched nothing).
    const lines = apSrc.split('\n');
    const acting = new Set();
    lines.forEach((ln, i) => {
        if (!/,\s*True\)\s*$/.test(ln) && !/,\s*acted\)\s*$/.test(ln)) return;
        for (let j = i; j >= 0 && j > i - 25; j--) {
            const m = lines[j].match(/_mk\(\s*"([a-z_]+)"/);
            if (m) { acting.add(m[1]); return; }
        }
    });
    const gates = [...acting].filter((k) => /_gate$|_guard$/.test(k));
    check(`mechanisms that can act were found (${acting.size})`, acting.size >= 5);
    check(`more than gates can act — the noun must be the wider one `
          + `(${acting.size} act, ${gates.length} are gates)`,
          acting.size > gates.length);
}
// Comments are prose ABOUT the code and routinely quote a retired phrase to record why
// it was retired — grepping them fails the very file that documents the fix. Strip them
// so the assertion reads the CODE. (Regex-level, so a `//` inside a string literal is
// over-stripped; acceptable here, and it errs toward missing a hit, never inventing one.)
const _code = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*(\/\/|\*).*$/gm, '');
for (const [name, src] of [['dial', _code(dialSrc)], ['ring', _code(ringSrc)],
                           ['trustCircle', _code(readFileSync(join(here, '..', 'static', 'js',
                                'handlers', 'trustCircle.js'), 'utf8'))],
                           ['grammar', _code(readFileSync(join(here, '..', 'static', 'js',
                                'handlers', 'trustGrammar.js'), 'utf8'))]]) {
    check(`${name} does not call the acted mark "a gate acted"`,
          !/a gate ACTED|a gate acted/i.test(src));
}
check('the shared aria vocabulary says mechanism',
      /'a mechanism acted'/.test(readFileSync(join(here, '..', 'static', 'js', 'handlers',
                                                   'trustGrammar.js'), 'utf8')));

check('the ambiguous "measured by" label is retired from the rendered Guide',
          !/measured by/.test(fwd));
    check('…the decisive judge renders under escalates-to', /escalates to/.test(fwd));
    check('…the scorer renders under graded-by', /graded by/.test(fwd));
    check('…and the judge is NOT also listed as merely grading it',
          fwd.indexOf('data-dim="groundedness"') < fwd.indexOf('graded by'));
    check('the reverse reads "decides for" for a judge',
          /decides for/.test(QD3.relatedChips('groundedness')));
    check('…and "grades" for a plain scorer',
          /grades/.test(QD3.relatedChips('grounded_overlap'))
          && !/decides for/.test(QD3.relatedChips('grounded_overlap')));

    // ── a GUARANTEE is upheld by controls and graded by measurements ─────────
    // A station holds TWO populations, exactly as the dial's station panel shows them.
    // Its entry listed the MECHANISMS under "graded by" — saying a control grades a
    // guarantee, which inverts the one distinction this vocabulary exists to keep — and
    // left the station's real measurements off its own entry entirely.
    QD3.registerEntries([{ k: 'station_grounded', label: 'GROUNDED', desc: 'g', cls: 'station',
                           controls: ['m_dec'], dims: ['grounded_overlap'] }]);
    const st = QD3.relatedChips('station_grounded');
    check('a station names the controls that UPHOLD it', /upheld by/.test(st));
    // …but only CONTROLS get that verb. A station's mechanisms are not all controls —
    // five of the eight have none — and "Evaluation upholds JUDGED" inverts the
    // direction exactly as "graded by" would: a measure reports on a guarantee.
    QD3.registerEntries([
        // A distinct control, NOT the one asserted above — reusing `m_dec` would add a
        // second entry to its reverse links and break an assertion two blocks up.
        { k: 'm_ctrl2', desc: 'c', cls: 'control' },
        { k: 'm_meas', desc: 'm', cls: 'measure' },
        { k: 'm_bound', desc: 'b', cls: 'boundary' },
        { k: 'm_att', desc: 'a', cls: 'attestation' },
        { k: 'station_mixed', label: 'MIXED', desc: 'x', cls: 'station',
          controls: ['m_ctrl2', 'm_meas', 'm_bound', 'm_att'] },
    ]);
    const mixed = QD3.relatedChips('station_mixed');
    // A measure EVIDENCES a guarantee: it observes a fact and puts it on the record; it
    // neither acts (upheld by) nor limits (bounded by). "Reported by" read as "who reported
    // the guarantee", which is why the verb changed.
    check('a measure is EVIDENCED BY, never upheld by', /evidenced by/.test(mixed) && !/reported by/.test(mixed));
    check('a boundary is BOUNDED BY', /bounded by/.test(mixed));
    check('an attestation is ATTESTED BY', /attested by/.test(mixed));
    check('…and the control keeps upheld by', /upheld by/.test(mixed));
    check('each verb groups its own mechanisms, one row each',
          (mixed.match(/qd-rel-lead/g) || []).length === 4);
    check('…and the measurements that GRADE it', /graded by/.test(st));
    check('…as two separate rows, never one', /upheld by/.test(st) && /graded by/.test(st)
          && st.indexOf('upheld by') < st.indexOf('graded by'));
    check('…with the mechanism no longer listed as grading the guarantee',
          st.indexOf('data-dim="m_dec"') < st.indexOf('graded by'));
    // Both populations are reachable — a row you cannot step into is a dead end.
    check('every chip in both rows links', (st.match(/qd-rel-chip" data-dim=/g) || []).length >= 2);
    // The reverse: a control UPHOLDS its guarantee; it does not grade it.
    const mech = QD3.relatedChips('m_dec');
    check('a control reads "upholds" toward its station', /upholds/.test(mech));
    check('…and never "grades" it', !/grades[\s\S]*station_grounded/.test(mech));
    // The registry is additive here like everywhere else.
    check('controlsOf is served', QD3.controlsOf('station_grounded').join() === 'm_dec');
    check('upholdsOf is the reverse of it', QD3.upholdsOf('m_dec').join() === 'station_grounded');

    // ── `counts` is the opposite direction from `graded by` ──────────────────
    // Evaluation PRODUCES the residue; it is not assessed by it ("Never — it grades, it
    // never acts"). Labelling its output "graded by" would invert the causality the
    // escalates-to/graded-by split exists to keep.
    QD3.registerEntries([{ k: 'evaluation', desc: 'e', cls: 'measure',
                           counts: ['cost', 'latency', 'tasks'] }]);
    const ev = QD3.relatedChips('evaluation');
    check('the residue renders under "counts"', /counts/.test(ev));
    check('…never under "graded by"', !/graded by/.test(ev));
    check('…and every one of them links',
          (ev.match(/qd-rel-chip" data-dim=/g) || []).length === 3);
    check('countsOf is served', QD3.countsOf('evaluation').join() === 'cost,latency,tasks');

    // ── a judge is marked INSIDE the row ─────────────────────────────────────
    // An entry header saying "LLM judge" does not help someone scanning twenty-nine
    // chips for the four a model opined on. EXECUTED, because a grep for the tag's CSS
    // class survives the tag being deleted from the row — which a first draft did.
    QD3.registerEntries([{ k: 'st_mix', label: 'MIX', desc: 'x', cls: 'station',
                           dims: ['groundedness', 'cost'] }]);
    const jrow = QD3.relatedChips('st_mix');
    check('a judge chip carries the ·judge tag', /Groundedness[^<]*·judge|·judge/.test(jrow));
    const perChip = [...jrow.matchAll(/data-dim="([^"]+)"[^>]*>([\s\S]*?)<\/button>/g)]
        .map(([, k, body]) => [k, /·judge/.test(body)]);
    check('…exactly on the judges, and not on the deterministic checks',
          JSON.stringify(perChip.sort()) === JSON.stringify([['cost', false], ['groundedness', true]]));
    // PROP-002: dashes are the absence family on every trust surface, so the judge mark
    // may never be a dashed chip — it would mean two things at once.
    check('…and the mark is not a dashed border',
          !/qd-rel-chip[^}]*border[^}]*dashed/.test(qdSrc2));

    // A registered mechanism with NO relations must say so — silence is only honest
    // when it is spoken (Rule 3.2: an absent slot must not be mistakable for a clean one).
    QD3.registerEntries([{ k: 'agent_integrity', desc: 'i', cls: 'control' }]);
    const bare = QD3.relatedChips('agent_integrity');
    check('a relation-less mechanism explains its own silence', /qd-row-note/.test(bare));
    check('…rather than rendering nothing', bare.trim().length > 0);
}
// ── which judges a GATE can consult ─────────────────────────────────────────
// Five judges are OWNED by GROUNDED and GUARDED, but owning a guarantee means "this
// judge measures whether it held", not "this judge runs inside its gate". Only the two
// a mechanism DECLARES (`POINT_JUDGES`) can decide anything. Both directions were on the
// entries already (`escalates to` / `decides for`) — but answering "which of the nine?"
// meant opening nine of them.
{
    const src = readFileSync(join(here, '..', 'static', 'js', 'handlers', 'qualityDimensions.js'), 'utf8');
    const body = src.slice(src.indexOf('function decidesInPath('),
                           src.indexOf('\n    }', src.indexOf('function decidesInPath(')) + 6);
    // eslint-disable-next-line no-new-func
    const decides = new Function('ENTRY_JUDGES', `${body}\nreturn decidesInPath;`)(
        { grounding_gate: ['groundedness', 'scope_faithfulness'] });

    check('a declared escalation judge can decide', decides('groundedness') === true);
    check('…both of them', decides('scope_faithfulness') === true);
    check('a judge owned by the same station but not declared cannot',
          decides('answer_groundedness') === false && decides('source_consistency') === false);
    check('…nor one owned by GUARDED, whose gate is deterministic',
          decides('content_safety') === false);
    check('…nor a residue judge', decides('answer_correctness') === false);
    // Derived from the served declaration, never a second hand-kept list.
    check('the set is derived from POINT_JUDGES, not restated',
          /for \(const m in ENTRY_JUDGES\)/.test(body));
    check('…so a judge no mechanism declares reports only',
          new Function('ENTRY_JUDGES', `${body}\nreturn decidesInPath;`)({})('groundedness') === false);

    // WHEN a judge runs, derived from two independent facts — neither assumed.
    const rbody = src.slice(src.indexOf('function judgeRuns('),
                            src.indexOf('\n    }', src.indexOf('function judgeRuns(')) + 6);
    const J = new Set(['groundedness', 'scope_faithfulness', 'content_safety', 'answer_correctness']);
    const mk = (dim) => new Function('JUDGE_DIMS', 'DIM', 'ENTRY_JUDGES',
        `${body}${rbody}\nreturn judgeRuns;`)(J, dim,
        { grounding_gate: ['groundedness', 'scope_faithfulness'] });
    const runs = mk(Object.fromEntries([...J].map((k) => [k, 1])));
    check('a gate-consulted judge that is also scored reads BOTH', runs('groundedness') === 'both');
    check('…and one only scored reads after', runs('content_safety') === 'after');
    check('a deterministic check is not a judge at all', runs('completed') === null);
    // The empty bucket must still be expressible, or it is mis-filed the day it exists.
    check('a judge wired to a gate but never scored reads in-flight only',
          mk({ content_safety: 1 })('groundedness') === 'inflight');
    check('…derived from the scored registry, not assumed', /after = !!DIM\[k\]/.test(src));

    // Marked in the LIST as the EXCEPTION: every judge is scored after the turn, so
    // only the in-flight ones carry a qualifier.
    check('after-the-turn is the unqualified norm', /const RUNS_LABEL = \{ both: 'also in-flight'[^}]*after: '' \}/.test(src));
    check('…so the header qualifies only the exception',
          /LLM judge\$\{RUNS_LABEL\[runs\] \? ' · ' \+ RUNS_LABEL\[runs\] : ''\}/.test(src));
    check('…the chip rows tag it beside ·judge', /·judge\$\{decidesInPath\(d\) \? ' ·in-flight' : ''\}/.test(src));
    check('…the shared tooltip too', /·judge\$\{decidesInPath\(k\) \? ' ·in-flight' : ''\}/.test(src));
    check('…and the list can be FILTERED by it', /pill\('role', r, RUNS_FACET\[r\]/.test(src));
    check('the facet narrows to judges by construction',
          /_fRole\.size \|\| \(JUDGE_DIMS\.has\(k\) && _fRole\.has\(judgeRuns\(k\)\)\)/.test(src));
    check('…is offered only where judges are in scope (honest absence)',
          /judgesIn\.length \? \[/.test(src));
    // The pill helper already returns '' for a zero count — the empty bucket self-gates.
    check('…and an empty bucket renders no pill', /const pill = \(kind, v, text, count, on, dot\) => count/.test(src));
    check('…and Clear clears it', /_fEngine\.clear\(\); _fRole\.clear\(\)/.test(src));
}


// ── §8 · the same-assessment badge ──────────────────────────────────────────────
// `graded by` says a measurement grades this property; it does NOT say the two are the
// SAME computation, which is exactly what the assurance substrate buys. The badge marks
// that pair, at BOTH ends, and nowhere else.
{
    const i = qdSrc.indexOf('    function chipRow(');
    let d = 0, j = -1;
    for (let n = qdSrc.indexOf('{', i); n < qdSrc.length; n++) {
        if (qdSrc[n] === '{') d++;
        else if (qdSrc[n] === '}' && --d === 0) { j = n + 1; break; }
    }
    check('chipRow was extracted', i > 0 && j > i);
    // Collaborators stubbed — this is about which rows the badge lands on.
    const chipRow = new Function('esc', 'label', 'JUDGE_DIMS', 'decidesInPath',
        'ENTRY_CLASS', 'sharedModule', 'appliesToGuideCls', 'leadHtml', 'kindCount', 'ENTRY_EXCLUDED',
        qdSrc.slice(i, j) + '\nreturn chipRow;')(
        (x) => String(x ?? ''), (x) => 'L:' + x, new Set(), () => false,
        { property_p: 'property', dim_a: 'measure', mech: 'control' },
        (a, b) => {
            const fam = ['property_p', 'dim_a', 'mech_sib'];   // all three read module m1
            return (fam.includes(a) && fam.includes(b) && a !== b) ? 'm1' : null;
        }, () => true,   // class scope: everything applies in this fixture
        // the lead word with its definition on hover — stubbed to the bare lead here
        (lead, text) => `<span class="qd-rel-lead">${text || lead}</span>`,
        // the kind-and-count tag beside the lead — not under test here
        () => '',
        // the mechanisms recorded as off a turn's path — none in this fixture
        new Set());

    // S and M are the two ends of ONE link (operator, 2026-10-30): the S stands wherever the
    // pair shares a module, at BOTH ends — except a mechanism's `properties` row, where the
    // fold pairs each property with its module side by side.
    const onProp = chipRow('graded by', ['dim_a'], 'property_p');
    const onPeer = chipRow('graded by', ['dim_a'], 'mech_sib');
    check('the PROPERTY end carries the badge', /qd-rel-same/.test(onProp));
    check('the MEASUREMENT end carries it too', /qd-rel-same/.test(chipRow('grades', ['property_p'], 'dim_a')));
    check('a MECHANISM graded-by row carries it too (both directions)', /qd-rel-same/.test(onPeer));
    check('…and any relation row naming a sharer carries it',
          /qd-rel-same/.test(chipRow('upholds', ['dim_a'], 'mech_sib')));
    check('…but never a mechanism\'s properties row (its fold pairs each property with its module)',
          !/qd-rel-same/.test(chipRow('properties', ['property_p'], 'mech_sib')));
    check('an unrelated pair is never badged',
          !/qd-rel-same/.test(chipRow('grades', ['other'], 'dim_a')));
    // THE ASSERTION THAT WAS MISSING once. Each end was checked in isolation, so a rule that
    // badged one direction and not the other passed: Grounding Overlap said "same assessment
    // as the Grounding gate" and the gate's entry said nothing back. The property is the
    // RELATION, so it has to be driven from both sides of the same pair.
    for (const [a, b] of [['property_p', 'dim_a'], ['dim_a', 'mech_sib'],
                          ['property_p', 'mech_sib']]) {
        const fwd = /qd-rel-same/.test(chipRow('graded by', [b], a));
        const rev = /qd-rel-same/.test(chipRow('graded by', [a], b));
        check(`the badge is SYMMETRIC for ${a} ↔ ${b} (a one-way link is a dead end)`, fwd && rev);
    }
    check('the S says a click shows the module', /Click the S to show the module/.test(onPeer));
    // A module carries `data-dim` because it ROUTES — and the shared tooltip is built
    // from that key, so hovering "Data scope adherence" described "Answer scope". An
    // element must describe what it IS and name where it goes as a separate fact.
    {
        const i = qdSrc.indexOf("if (el.classList && el.classList.contains('qd-mod'))");
        // Bounded by BRACE DEPTH, not by a character count. A 900-char window measured
        // the branch's LENGTH rather than its content, so adding a line to the tooltip
        // pushed the destination note outside it and failed an assertion whose property
        // held perfectly — the same distance-bounded brittleness this suite has recorded
        // twice before.
        const branch = (() => {
            if (i < 0) return '';
            let d = 0, started = false;
            for (let j = i; j < qdSrc.length; j++) {
                const c = qdSrc[j];
                if (c === '{') { d++; started = true; }
                else if (c === '}') { d--; if (started && d === 0) return qdSrc.slice(i, j + 1); }
            }
            return qdSrc.slice(i);
        })();
        check('the tooltip has a module branch', i > 0);
        check('…titled from the MODULE, not the destination it routes to',
              /qd-tt-title[^`]*MODULE_LABEL\[mk\]/.test(branch));
        check('…and the destination named separately, as a route',
              /qd-tt-note[^`]*Opens <b>\$\{esc\(label\(k\)\)\}/.test(branch));
        // Two tooltip systems on one hover render two boxes.
        check('the M badge carries no competing u-tip',
              !/class="qd-mark u-tip"/.test(qdSrc));
    }
    check('the badge is a LETTER, not a coloured dot', />S<\/span>/.test(onPeer));
    check('…named for a screen reader', /aria-label="Same assessment"/.test(onPeer));
    check('…explained through the platform tooltip, never a native title',
          /u-tip[^>]*data-tip="Same assessment/.test(onPeer));
    check('no native title attribute anywhere in the Guide', !/\stitle="/.test(qdSrc));
// One tooltip at a time: the S sits INSIDE its chip, so hovering it fired the chip's entry card
// AND the S's own tooltip. The entry card yields to a nested mark with its own tooltip.
{
    const own = new Function('return ' + qdSrc.slice(qdSrc.indexOf('(e, el) => {', qdSrc.indexOf('const ownTipInside')),
        qdSrc.indexOf('};', qdSrc.indexOf('const ownTipInside')) + 1))();
    const node = (cls, parent) => ({ cls, parent, closest(sel) { let n = this; while (n) { if (sel === '.u-tip' ? /\bu-tip\b/.test(n.cls) : false) return n; n = n.parent; } return null; },
                                     contains(o) { let n = o; while (n) { if (n === this) return true; n = n.parent; } return false; } });
    const chip = node('qd-rel-chip'), name = node('', chip), sBadge = node('qd-rel-same u-tip', chip);
    check('hovering the S inside a chip: the chip\'s entry card yields (one tooltip, not two)', own({ target: sBadge }, chip) === true);
    check('…hovering the chip\'s name: the entry card shows', own({ target: name }, chip) === false);
    check('…the handler hides the card and returns when a nested mark has its own tooltip',
          /if \(ownTipInside\(e, el\)\) \{ hideTip\(\); return; \}/.test(qdSrc));
}
    {
        const li = qdSrc.indexOf('class="qd-legend"');
        const seg = qdSrc.slice(li, qdSrc.indexOf('</div>', qdSrc.indexOf('qd-modal-list', li)));
        check('the legend states the badge',
              li > 0 && /qd-rel-same/.test(seg) && /Same assessment/.test(seg));
    }
    check('the badge is dark-with-white-letter in both themes',
          /\.qd-rel-same[^{]*\{[\s\S]{0,800}color:#fff/.test(qdSrc));
}

// ── §9 · modules are marked AS modules, and both ends name the same one ─────────
// Modules were rendered as bare text — indistinguishable from an entry a reader could
// open — and on a measurement or a control they were not rendered at all, so the S badge
// asserted a shared assessment the entry never named. Only the tooltip carried it.
{
    check('a module carries the M badge', /aria-label="Module"[^>]*>M</.test(qdSrc)
          || /class="qd-mark[^"]*"[\s\S]{0,200}>M</.test(qdSrc));
    check('…and the M shares the S badge\'s form (one badge family, two letters)',
          /\.qd-rel-same,\.qd-mark\{/.test(qdSrc));
    check('the legend keys BOTH letters',
          /qd-legend[\s\S]{0,1200}qd-mark[\s\S]{0,200}Module/.test(qdSrc)
          && /qd-legend[\s\S]{0,1400}qd-rel-same[\s\S]{0,200}Same assessment/.test(qdSrc));

    // The arrow must LEAD somewhere. A module has no entry of its own, so it routes to
    // the property it assesses — verified 1:1 for all nine modules.
    check('a routed module opens the PROPERTY it assesses',
          /class="qd-mod qd-mod-route" data-dim="\$\{esc\(prop\)\}/.test(qdSrc));
    check('…and one with no destination is a plain span, not a button',
          /: `<span class="qd-mod" data-mod=/.test(qdSrc));
    // The arrow was honest while modules and siblings shared a row. Once they moved into
    // separate rows it sat BETWEEN two chips and read as "points at the next one" — a
    // chain that does not exist. The route survives as the button; direction is carried
    // by the hover link, which names the real target.
    // The RENDERED glyph, not the character: an arrow inside prose (a "Config → Trust"
    // navigation path, a `dimension → stage` comment) is legitimate and must stay.
    check('NO arrow glyph is rendered — it read as a chain across chips',
          !/>\s*→\s*</.test(qdSrc) && !/qd-mod-arrow|qd-sib-arrow/.test(qdSrc));
    check('…and nothing still promises one in words',
          !/the arrow opens|arrow says/.test(qdSrc));
    // On the property itself the module is already home: a self-route is a dead end
    // wearing a link's clothes.
    check('the property lanes row names the module WITHOUT an arrow',
          /moduleName\(l\.assessor, l\.label, null\)/.test(qdSrc));

    // The other half of the operator's finding: the module has to appear on BOTH sides.
    // The lead renders through `leadHtml` (the term plus its definition on hover), so the
    // row is asserted by the call that produces it, not by the markup it used to inline.
    check('a non-property entry gets its own `reads` row',
          /leadHtml\('reads'\)/.test(qdSrc) || /qd-rel-lead">reads</.test(qdSrc));
    check('…and a property does NOT (its lanes row already names them — Rule 3.0)',
          /if \(ENTRY_CLASS\[k\] === 'property'\) return '';/.test(qdSrc));

    // sibling → module, visible rather than tooltip-only
    check('the S badge carries WHICH module it means', /class="qd-rel-same[^`]*data-mod=/.test(qdSrc));
    check('…without an arrow, which read as a chain once the rows split',
          !/qd-sib-arrow/.test(qdSrc));
    check('hovering either end lights the other', /function wireModuleLinks/.test(qdSrc)
          && /qd-linked/.test(qdSrc));
    check('…wired as delegation, so it survives a re-render',
          /wireModuleLinks\(list\)/.test(qdSrc));
}

console.log(fail ? `\n${pass} passed, ${fail} FAILED`
                 : `\nALL PASS — ${pass} assertions (the scorecard counts by the partition).`);
process.exit(fail ? 1 : 0);
