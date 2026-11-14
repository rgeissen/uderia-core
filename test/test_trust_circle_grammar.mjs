/**
 * Trust Circle communication contract — the committed gate.
 *
 *   node test/test_trust_circle_grammar.mjs
 *
 * WHY THIS EXISTS. The station grammar is a cross-cutting convention, and an
 * unwritten convention has already failed twice in this codebase: five hand-rolled
 * copies of the posture-mode ladder (one drifted and silently downgraded a stored
 * `review` to `observe`), and a hand-rolled `.some()` aggregation that reported the
 * best member as the state of a whole estate (1 acting agent of 10 rendered green).
 * A doc explains the contract; this test enforces it. A future contributor who adds
 * a ninth station by hand fails a gate instead of a code review.
 *
 * HOW IT WORKS. The real `_circleStationsFor()` is extracted verbatim from
 * agentPerformanceHandler.js and executed with the module state stubbed, so the test
 * exercises shipped code rather than a reimplementation. Fixtures are synthetic and
 * self-contained: no server, no network, no live payloads — this must run in CI.
 *
 * Contract asserted here (see docs/Architecture/TRUST_STATUS_COMMUNICATION.md):
 *   1. every station renders BOTH attributes as `configuration · execution`
 *   2. the four hover slots appear in fixed order, and Evidence is never omitted
 *   3. the Why slot always leads with the attention word, and names the driver
 *   4. colour is attention priority = worst-of(design, execution)
 *   5. aggregation across agents is worst-of, never best-of
 *   6. no station ever renders a bare scope-size count
 *   7. absent data reads as an explicit `—`, never as a clean value
 */
import fs from 'fs';
import path from 'path';
import { execSync } from 'node:child_process';
import { fileURLToPath } from 'url';

// The measurement process model, read from its ONE declaration (eval/stages.py) so this
// gate can never drift from what the product actually serves to the admin page.
const STAGE_MODEL = JSON.parse(execSync(
    `python3 -c "import sys,json;sys.path.insert(0,'src');`
    + `from trusted_data_agent.eval import stages as s;print(json.dumps(s.vocabulary_payload()))"`
).toString());

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.join(HERE, '..', 'static', 'js', 'handlers', 'agentPerformanceHandler.js');

const lines = fs.readFileSync(SRC, 'utf8').split('\n');
const start = lines.findIndex((l) => l.includes('function _cfgMode(cfg, dflt)'));
const retIdx = lines.findIndex((l) => l.includes(
    'return [signed, grounded, guarded, bounded, scored, trusted, reviewed, proven]'));
if (start < 0 || retIdx < 0) {
    console.error('FAILED — could not locate _circleStationsFor() in agentPerformanceHandler.js.\n'
        + 'If the function or its return statement was renamed, update this extractor.');
    process.exit(1);
}
const body = lines.slice(start, retIdx + 2).join('\n');

// TRUST_STATE_LABEL is a module-level const the ring references (the human labels for the
// behavioral composite states). Extract it VERBATIM from source rather than stubbing it, so a
// rename fails loudly here and the gate never runs against a drifted copy.
const tslStart = lines.findIndex((l) => l.includes('const TRUST_STATE_LABEL = {'));
if (tslStart < 0) {
    console.error('FAILED — could not locate `const TRUST_STATE_LABEL = {` in agentPerformanceHandler.js.');
    process.exit(1);
}
const tslRel = lines.slice(tslStart).findIndex((l) => l.trim() === '};');
const tslBlock = lines.slice(tslStart, tslStart + tslRel + 1).join('\n');

// The health floors the derivation explainer grades its dimension chips on. Extracted
// verbatim for the same reason as TRUST_STATE_LABEL: a stub would let the gate pass while
// the shipped bands used different numbers than the roster bars two sections up.
// HEALTHY/REVIEW are now `let` (adopted from the admin-configured `quality_bands`);
// LOW_SAMPLE stays `const`. Extracted verbatim either way — a stub would let the gate
// pass while the shipped defaults differed.
const healthLines = lines.filter((l) => /^\s*(?:const|let) (HEALTHY|REVIEW|LOW_SAMPLE) = [0-9.]+;/.test(l));
if (healthLines.length !== 3) {
    console.error('FAILED — could not locate the HEALTHY/REVIEW/LOW_SAMPLE constants in '
        + `agentPerformanceHandler.js (found ${healthLines.length} of 3).`);
    process.exit(1);
}
// Extracted verbatim, not stubbed: these floors decide the chip bands and the tentative
// marker, and a stub would let the gate pass while the shipped numbers differed.
const healthBlock = healthLines.join('\n');

const STATION_KEYS = ['signed', 'grounded', 'guarded', 'bounded', 'scored', 'trusted', 'reviewed', 'proven'];
const SLOTS = ['Configuration', 'Execution', 'Why', 'Evidence'];
// The assembler's honest-absence line — legitimately repeatable across slots.
const _NONE_TXT = 'none in this window';
// `watching` (W1) is the absence family's posture word — a chosen setting is never an alarm.
const ATTN_WORDS = ['quiet', 'worth a look', 'needs you now', 'not turned on', 'watching'];

// TC-51 — the gate exercises the REAL dimension vocabulary. qualityDimensions.js is a
// plain script; loaded here with minimal DOM stubs and installed as `window.QualityDimensions`
// so `_dimLabel` runs its primary path (QD.label), not the title-case fallback. Without this
// the gate ran the fallback exclusively — and its own assertion accepted TWO names for one
// dimension (`/Grounded Overlap|Grounding Overlap/`), the precise defect the contract forbids.
const QD_SRC = path.join(HERE, '..', 'static', 'js', 'handlers', 'qualityDimensions.js');
const _qdWin = { addEventListener() {}, removeEventListener() {} };
const _qdDoc = {
    readyState: 'complete', addEventListener() {}, querySelectorAll() { return []; },
    getElementById() { return null; }, body: { appendChild() {} }, head: { appendChild() {} },
    documentElement: {},
    createElement() { return { style: {}, setAttribute() {}, appendChild() {}, classList: { add() {}, remove() {} } }; },
};
try {
    new Function('window', 'document', fs.readFileSync(QD_SRC, 'utf8'))(_qdWin, _qdDoc);
} catch (e) {
    console.error(`FAILED — qualityDimensions.js did not load under Node: ${e.message}`);
    process.exit(1);
}
if (!_qdWin.QualityDimensions || typeof _qdWin.QualityDimensions.label !== 'function') {
    console.error('FAILED — qualityDimensions.js loaded but exposed no QualityDimensions.label.');
    process.exit(1);
}

// PROP-002 — the ONE trust vocabulary. Loaded for real (like QD) so every consumer
// the gate executes takes its PRIMARY path (reading TrustGrammar), not the
// absence-tolerant fallback — and so the canonical words the gate asserts against
// are the shipped ones, never a stub.
const TG_SRC = path.join(HERE, '..', 'static', 'js', 'handlers', 'trustGrammar.js');
try {
    new Function('window', 'document', fs.readFileSync(TG_SRC, 'utf8'))(_qdWin, _qdDoc);
} catch (e) {
    console.error(`FAILED — trustGrammar.js did not load under Node: ${e.message}`);
    process.exit(1);
}
if (!globalThis.TrustGrammar || !globalThis.TrustGrammar.ATTN_WORD) {
    console.error('FAILED — trustGrammar.js loaded but exposed no TrustGrammar.ATTN_WORD on globalThis.');
    process.exit(1);
}
const TG = globalThis.TrustGrammar;

globalThis.window = { QualityDimensions: _qdWin.QualityDimensions, TrustGrammar: TG };
const QD = _qdWin.QualityDimensions;

function build(state) {
    return new Function('STATE', `
        const { profilesRaw, model, trustAgentsFull, trustBehavior,
                inboxBadgeCount, inboxDerivedCount, inboxDerivedDanger, inboxDerivedTop,
                inboxByTag, inboxUnattributed, circleLens, actActivity } = STATE;
        const _profilesRaw = profilesRaw, _model = model,
              _trustAgentsFull = trustAgentsFull, _trustBehavior = trustBehavior,
              _actActivity = actActivity || null,
              _inboxBadgeCount = inboxBadgeCount || 0, _inboxDerivedCount = inboxDerivedCount || 0,
              _inboxDerivedDanger = inboxDerivedDanger || 0, _inboxDerivedTop = inboxDerivedTop || '',
              _inboxByTag = inboxByTag || {}, _inboxUnattributed = inboxUnattributed || 0;
        // The trust-trip lens — module state declared OUTSIDE the extraction span,
        // stubbed here so fixtures can exercise both lenses (TRUST_TRIP_RESET.md).
        const _circleLens = circleLens === 'trip' ? 'trip' : 'total';
        ${tslBlock}
        ${healthBlock}
        ${body}
        return _circleStationsFor;
    `)(state);
}

// A profile with an explicit posture. `scope: null` simulates a backend that does
// not compute authorityScope at all (unknown authority must never read as "off").
const P = (tag, gMode, oMode, scope = { restricted: false, autonomous: true, constraints: [] }) => ({
    tag, id: 'p-' + tag, profile_type: 'rag_focused',
    groundingConfig: { mode: gMode },
    guardrailConfig: { outbound: { mode: oMode }, inbound: { mode: oMode } },
    honestyConfig: { mode: 'observe' },
    ...(scope ? { authorityScope: scope } : {}),
});
const T = (tag, state) => ({ tag, state, ingredients: [], blocking: [] });

function stations(profiles, trust, opts = {}) {
    const byAgent = opts.byAgent || {};
    const agents = profiles.map((p) => ({
        tag: p.tag, key: p.tag, type: p.profile_type, online: byAgent[p.tag] || null, isSelf: true,
    }));
    const fn = build({
        profilesRaw: profiles,
        model: { dashboard: opts.dashboard || {}, agents },
        trustAgentsFull: trust,
        trustBehavior: opts.trustBehavior || null,
        inboxBadgeCount: opts.inboxBadgeCount,
        inboxDerivedCount: opts.inboxDerivedCount,
        inboxDerivedDanger: opts.inboxDerivedDanger,
        inboxDerivedTop: opts.inboxDerivedTop,
        inboxByTag: opts.inboxByTag,
        inboxUnattributed: opts.inboxUnattributed,
        circleLens: opts.lens,
    });
    const target = opts.agent ? agents.find((a) => a.tag === opts.agent) : null;
    const out = {};
    for (const s of fn(target)) out[s.key] = s;
    return out;
}

let pass = 0; const failures = [];
function check(name, got, want) {
    if (got === want) { pass++; return; }
    failures.push(`${name}\n      got=${JSON.stringify(got)} want=${JSON.stringify(want)}`);
}
// A Guide stub FAITHFUL to what the ring may use: it exposes `openGuide` (the
// capability the panel asks about) and throws a NAMED error on anything else, so a
// ring that starts looking dimensions up itself (Rule 3b.7) fails with the reason
// rather than a bare TypeError. A permissive stub would let exactly that through.
const guideStub = () => ({
    QualityDimensions: new Proxy({ openGuide() {} }, {
        get(t, prop) {
            if (prop in t) return t[prop];
            throw new Error(`Rule 3b.7 — the ring must not use QualityDimensions.${String(prop)}: `
                + 'it routes to the Guide, it never looks a dimension up itself.');
        },
    }),
});

function section(title) { console.log(`\n── ${title} ${'─'.repeat(Math.max(0, 70 - title.length))}`); }
function report(title) {
    const before = failures.length;
    return () => console.log(`   ${failures.length === before ? 'ok' : 'FAIL'}  ${title}`);
}

// ── 1–3 · the grammar invariants, asserted on EVERY station in EVERY scenario ──
function assertGrammar(label, st) {
    for (const key of STATION_KEYS) {
        const s = st[key];
        if (!s) { failures.push(`${label}: station "${key}" missing entirely`); continue; }
        const facts = s.facts || [];
        const slots = facts.map((f) => f && f.k);
        check(`${label}/${key}: four slots in fixed order`,
            slots.length === 4 && SLOTS.every((w, i) => slots[i] === w), true);
        // Rows must stay STRUCTURED. A pre-formatted string leaves the renderer nothing
        // to lay out, which is how the panel became four lines of identical grey prose.
        check(`${label}/${key}: rows are structured, not pre-formatted strings`,
            facts.every((f) => f && typeof f === 'object' && 'v' in f && 'd' in f && 't' in f), true);
        // Evidence is mandatory. Making it structural is what stopped REVIEWED from
        // reporting "every layer quiet" while a danger-severity item sat in the inbox.
        check(`${label}/${key}: Evidence row carries content`,
            !!String((facts[3] || {}).d || '').trim(), true);
        // The two attributes are the anchor: each reading row always shows a value,
        // even when that value is an honest dash.
        check(`${label}/${key}: both reading rows have a value`,
            !!(facts[0] || {}).v && !!(facts[1] || {}).v, true);
        check(`${label}/${key}: reading vs reasoning are typed`,
            [facts[0], facts[1]].every((f) => f.t === 'read')
            && [facts[2], facts[3]].every((f) => f.t === 'reason'), true);
        check(`${label}/${key}: value is a config·execution pair`,
            / · /.test(String(s.value)), true);
        check(`${label}/${key}: Why states the attention word`,
            ATTN_WORDS.some((w) => String((facts[2] || {}).v || '').startsWith(w)), true);
        // A composite colour is only honest because the drill-down names its driver —
        // in the PANEL'S vocabulary (#64): the attribute is labelled "Configuration",
        // so the internal token `design` must never leak into the slot.
        // Operator-settled form: the driver sits in BRACKETS after the attention
        // word — `needs you now (execution)` — and the explanation prose is set off
        // with an em dash, so anchor and explanation cannot run together as one
        // phrase.
        if (s.driver) {
            const word = { design: 'configuration', execution: 'execution' }[s.driver] || s.driver;
            check(`${label}/${key}: Why names the driver when one exists`,
                new RegExp(`\\(${word}\\)$`).test(String((facts[2] || {}).v || '')), true);
            check(`${label}/${key}: the internal token never leaks (#64)`,
                /\(design\)$/.test(String((facts[2] || {}).v || '')), false);
            check(`${label}/${key}: the Why explanation is set off with a dash`,
                !String((facts[2] || {}).d || '').trim()
                || String((facts[2] || {}).d || '').startsWith('— '), true);
        }
        // No row may restate the headline — that redundancy read as the same sentence
        // twice, adjacent, and was the panel's most visible defect.
        const head = String(s.headline || '').replace(/[.\s]+$/, '').toLowerCase();
        if (head.length > 25) {
            check(`${label}/${key}: no row restates the headline`,
                facts.some((f) => String(f.d || '').toLowerCase().includes(head.slice(0, 40))), false);
        }
        check(`${label}/${key}: colour is worst-of(design, execution)`,
            s.state, expectedAttention(s.design.state, s.execution.state));
        // W4 — `annotate` and `review` are STORAGE tokens; a reader sees the editor's
        // words (watch + disclose · enforce + keep for review). Swept across every slot.
        const allText = [s.value, s.headline, ...facts.map((f) => `${f.v} ${f.d}`)].join(' ');
        check(`${label}/${key}: no bare storage token reaches a reading (W4)`,
            /\bannotate\b|\breview mode\b|\bin review\b(?! posture)/.test(allText), false);
        // Absent data must be an explicit dash, never a value that reads as clean.
        if (!s.design.text) check(`${label}/${key}: absent design renders —`, s.value.split(' · ')[0], '—');
        if (!s.execution.text) check(`${label}/${key}: absent execution renders —`, s.value.split(' · ')[1], '—');
    }
}
// Attention first, posture second, absence last (W1): a real attention reading on
// either side decides worst-of; otherwise a `watching` side IS the reading; else na.
const RANK = { pass: 1, warn: 2, danger: 3 };
function expectedAttention(a, b) {
    const live = [a, b].filter((s) => s && RANK[s]);
    if (live.length) return live.reduce((w, s) => (RANK[s] > RANK[w] ? s : w), 'pass');
    return (a === 'watching' || b === 'watching') ? 'watching' : 'na';
}

section('grammar invariants across representative scenarios');
{
    const scenarios = {
        'focused/unsigned+observe': stations([P('A', 'observe', 'observe')], [T('A', 'unsigned')], { agent: 'A' }),
        'focused/signed+enforce': stations([P('A', 'enforce', 'enforce',
            { restricted: true, autonomous: false, constraints: ['deny_writes'] })], [T('A', 'trusted')], { agent: 'A' }),
        'focused/no-authority-scope': stations([P('A', 'observe', 'observe', null)], [T('A', 'deployment_unattested')], { agent: 'A' }),
        'focused/with-eval-data': stations([P('A', 'review', 'review')], [T('A', 'unsigned')], {
            agent: 'A',
            dashboard: { enabled: true, by_agent: { A: { turns: 4, failures: 3, dimensions: { groundedness: 0.75, grounded_overlap: 0.25 }, dimension_counts: { groundedness: 4, grounded_overlap: 4 } } } },
            byAgent: { A: { turns: 4, failures: 3, dimensions: { groundedness: 0.75, grounded_overlap: 0.25 }, dimension_counts: { groundedness: 4, grounded_overlap: 4 } } },
            trustBehavior: { enabled: true, breaker_mode: 'off', agents: { A: { behavior: { turns: 4, weighted_pass_rate: 0.24, trend: [1, 0, 0, 0] }, composite: { state: 'provisional', reason: 'insufficient evidence', integrity: 'unsigned' } } } },
            inboxBadgeCount: 0, inboxDerivedCount: 1, inboxDerivedDanger: 1, inboxDerivedTop: 'danger: possible multi-turn attack',
        }),
        'estate/mixed': stations(
            [P('A', 'enforce', 'enforce'), P('B', 'observe', 'observe'), P('C', 'observe', 'observe')],
            [T('A', 'trusted'), T('B', 'unsigned'), T('C', 'changed')],
            { dashboard: { enabled: true, total_spans: 10, spans_with_failure: 4, by_agent: { A: { turns: 10, failures: 4, tag: 'A' } } } }),
        'empty/no-profiles': stations([], []),
    };
    for (const [label, st] of Object.entries(scenarios)) assertGrammar(label, st);
    report('grammar holds in every scenario')();
}

// ── 3b · the derivation shares its figure's subject ──────────────────────────
section('derivation subject — window and exec describe what the figure counts');
{
    // Shipped defect: at estate scope GROUNDED's execution figure counted AGENTS
    // (from the by_agent cells) while its derivation read the FOCUSED variables —
    // so the explainer said "no scored turns in this window" directly under
    // "4 of 5 scored agents clean". Two slots, one station, disagreeing about
    // whether anything was measured at all (§7.8, one panel deep).
    const est = stations(
        [P('A', 'enforce', 'enforce'), P('B', 'observe', 'observe')],
        [T('A', 'trusted'), T('B', 'unsigned')],
        { dashboard: { enabled: true, total_spans: 8, spans_with_failure: 2, by_agent: {
            A: { turns: 5, failures: 1, tag: 'A', dimensions: { grounded_overlap: 0.8 }, dimension_counts: { grounded_overlap: 5 } },
            B: { turns: 3, failures: 1, tag: 'B', dimensions: { grounded_overlap: 1 }, dimension_counts: { grounded_overlap: 3 } },
        } } });
    for (const k of ['grounded', 'guarded']) {
        const d = est[k].derivation || {};
        const execText = est[k].execution.text || '';
        if (execText) {
            check(`estate ${k}: the window counts agents, like the figure`,
                /scored agent/.test(d.window || ''), true);
            check(`estate ${k}: the window never claims turn-emptiness under an agent figure`,
                /no scored turns/.test(d.window || ''), false);
            check(`estate ${k}: exec narrative counts agents too`,
                /AGENTS at estate scope/.test(d.exec || ''), true);
        }
    }
    // Focused scope keeps the turn subject — the fix must not have widened it.
    const foc = stations([P('A', 'enforce', 'enforce')], [T('A', 'trusted')], {
        agent: 'A',
        dashboard: { enabled: true, by_agent: { A: { turns: 4, failures: 1, tag: 'A' } } },
        byAgent: { A: { turns: 4, failures: 1, dimensions: { grounded_overlap: 0.75 }, dimension_counts: { grounded_overlap: 4 } } },
    });
    check('focused grounded: the window still counts turns',
        /scored turn/.test((foc.grounded.derivation || {}).window || ''), true);
}

// The conduct score is a BLEND — pass rate recency-weighted + extra weight on the
// trust-critical dimensions — so it can legitimately sit 20 points from the card's raw
// pass rate. Unexplained, that distance was reported as a bug; the tooltip must name its
// components whenever the payload carries them.
section('trust mark — a blended figure names its components');
{
    const src2 = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'handlers',
        'agentPerformanceHandler.js'), 'utf8');
    check('the tooltip derives the blend from the payload',
        /beh\.weighted_pass_rate != null[\s\S]{0,120}beh\.trust_dim_rate != null/.test(src2), true);
    check('…naming both components',
        /recency-weighted[\s\S]{0,80}trust-critical checks/.test(src2), true);
    // The rule is "never hardcode the VALUE" — the first draft of this row banned the
    // token and failed on the one line that reads the weight FROM THE SERVER CONFIG,
    // which is exactly the right pattern. Every mention must be a config read.
    check('…and every blend-weight mention reads server config',
        (src2.match(/trust_dim_weight/g) || []).every === undefined ? true
            : (src2.match(/trust_dim_weight/g) || []).length
              === (src2.match(/_trustBehavior\.config\.trust_dim_weight/g) || []).length, true);
    check('…with no literal 0\.4 or 40% near the blend text',
        /trust-critical checks[^`]{0,60}(0\.4|40%)/.test(src2), false);
}

// TRUSTED's execution VALUE is the measurement, not the band. "Watch" in the value slot
// was a state word wearing a value's clothes (Rule 3.5 family): it duplicated its own
// detail ("Watch Watch — …") and left the UNBLENDED pass rate as the only figure. The
// conduct score is the measurement; the standing is its band, named as qualifier.
section('TRUSTED — the value is the conduct score, the standing its qualifier');
{
    const foc = stations([P('A', 'observe', 'observe')], [T('A', 'trusted')], {
        agent: 'A',
        trustBehavior: { enabled: true, breaker_mode: 'off', self_uuid: 'u1', agents: { A: {
            behavior: { turns: 38, weighted_pass_rate: 0.49, trust_dim_rate: 1.0,
                        score: 0.7033, trend: [1, 0, 1, 1] },
            composite: { state: 'watch', reason: 'below the proven floor', integrity: 'trusted' },
        } } },
    });
    const t = foc.trusted;
    check('the execution value is the score', t.execution.text, '70%');
    check('…never the bare state word', t.execution.text === 'Watch', false);
    check('the standing is named in the qualifier', /standing Watch/.test(t.execution.detail), true);
    check('…with the conduct figure and window', /conduct 70% over 38 turns/.test(t.execution.detail), true);
    check('…and the blend components', /overall 49% blended with trust-critical 100%/.test(t.execution.detail), true);
    check('no word appears twice in a row',
        /\b(\w+) \1\b/.test(`${t.execution.text} ${t.execution.detail}`), false);
    // Configuration stays a SETTING — the standing must never migrate into it: design
    // says the watching is on; execution says what the watching found.
    check('configuration remains the switch', t.design.text, 'on');
    check('…and never carries the standing', /Watch/i.test(t.design.text), false);
    // A payload without the score still renders honestly (older backend).
    const old = stations([P('A', 'observe', 'observe')], [T('A', 'trusted')], {
        agent: 'A',
        trustBehavior: { enabled: true, breaker_mode: 'off', self_uuid: 'u1', agents: { A: {
            behavior: { turns: 5, trend: [1, 1] },
            composite: { state: 'watch', reason: 'x', integrity: 'trusted' },
        } } },
    });
    check('an older payload falls back to the standing label', old.trusted.execution.text, 'Watch');
}

// ── 4 · attention priority ───────────────────────────────────────────────────
section('attention priority — colour is triage, not a dimension');
{
    // `na` on one side neither masks a real reading nor invents attention of its own.
    const armedNoEvidence = stations([P('A', 'enforce', 'enforce')], [T('A', 'unsigned')], {
        agent: 'A', dashboard: { enabled: true },
    });
    check('armed gate, no turns yet -> quiet (execution na does not drag)', armedNoEvidence.grounded.state, 'pass');
    check('signing has no config dial -> design side renders —', armedNoEvidence.signed.value.split(' · ')[0], '—');
    check('BOUNDED execution is honestly absent -> —', armedNoEvidence.bounded.value.split(' · ')[1], '—');

    // Execution must be able to overrule a perfect configuration. This is the false
    // comfort the attention model exists to remove.
    // `completed` is a RESIDUE dim (JUDGED's own slice of the partition) — the
    // grounding failure alone must NOT raise JUDGED (Rule 3b.6).
    const badExec = { turns: 4, failures: 3,
        dimensions: { grounded_overlap: 0.25, completed: 0.25 },
        dimension_counts: { grounded_overlap: 4, completed: 4 } };
    const drift = stations([P('A', 'review', 'review')], [T('A', 'unsigned')], {
        agent: 'A', dashboard: { enabled: true, by_agent: { A: badExec } }, byAgent: { A: badExec },
    });
    // Execution overruling a perfect configuration — retargeted to JUDGED, which is
    // where a measurement failure now lands. The rule (execution CAN overrule config)
    // is unchanged; only the station carrying the measurement moved.
    check('configured to withhold but execution failing -> needs you now', drift.scored.state, 'danger');
    check('and the driver is named as execution', drift.scored.driver, 'execution');
    check('SCORED below the 50% floor on ITS OWN checks -> needs you now', drift.scored.state, 'danger');

    // A design shortfall with clean execution must attribute to design.
    const unscoped = stations([P('A', 'observe', 'observe')], [T('A', 'unsigned')], { agent: 'A' });
    check('unscoped authority with no execution data -> driver is design', unscoped.bounded.driver, 'design');
    report('attention priority behaves')();
}

// ── 5 · aggregation is worst-of, never best-of ───────────────────────────────
section('estate aggregation — worst-of');
{
    const mk = (modes) => modes.map((m, i) => P('A' + i, m, m));
    const tr = (modes) => modes.map((m, i) => T('A' + i, 'unsigned'));
    const st = (modes) => stations(mk(modes), tr(modes));
    const ten = (one) => [one, ...Array(9).fill('observe')];
    // W1 — a chosen posture is a READING, never an alarm: a mixed estate reads
    // `watching` (the value carries `1/10`), never green (the strongest member must
    // not hide the others' exposure) and never amber (nobody is being asked to look).
    check('1 enforce of 10 -> watching (NOT green)', st(ten('enforce')).grounded.design.state, 'watching');
    check('1 enforce of 10 -> …and never amber', st(ten('enforce')).grounded.design.state !== 'warn', true);
    check('1 review of 10 -> watching', st(ten('review')).grounded.design.state, 'watching');
    check('all enforce -> quiet', st(Array(10).fill('enforce')).grounded.design.state, 'pass');
    check('all observe -> watching', st(Array(5).fill('observe')).grounded.design.state, 'watching');
    check('all annotate -> watching (disclosure stops nothing)', st(Array(5).fill('annotate')).grounded.design.state, 'watching');
    check('all off -> not turned on', st(Array(5).fill('off')).grounded.design.state, 'na');
    check('off + observe -> watching', st(['off', 'observe']).grounded.design.state, 'watching');
    check('guarded mirrors grounded', st(ten('enforce')).guarded.design.state, 'watching');
    // The value slot speaks the reader's words, never the storage token (W4).
    const annot = stations([P('A', 'annotate', 'review')], [T('A', 'unsigned')], { agent: 'A' });
    check('W4 an annotate agent reads "watch + disclose" in the value', annot.grounded.design.text, 'watch + disclose');
    check('W4 a review agent reads "enforce + keep for review" in the value', annot.guarded.design.text, 'enforce + keep for review');
    check('W4 the estate census speaks the same words',
        /2 watch \+ disclose/.test(st(['annotate', 'annotate', 'enforce']).grounded.design.detail), true);
    check('W1 the focused ring colours a watching agent watching, never amber', annot.grounded.state, 'watching');
    check('W1 …and the Why slot names the posture as the driver', annot.grounded.facts[2].v, 'watching (configuration)');

    const R = { restricted: true, autonomous: false, constraints: ['deny_writes'] };
    check('one unrestricted of three -> worth a look',
        stations([P('A', 'observe', 'observe', R), P('B', 'observe', 'observe', R), P('C', 'observe', 'observe')],
            [T('A', 'unsigned'), T('B', 'unsigned'), T('C', 'unsigned')]).bounded.design.state, 'warn');
    check('unknown authority scope -> worth a look, never "not turned on"',
        stations([P('A', 'observe', 'observe', null)], [T('A', 'unsigned')], { agent: 'A' }).bounded.design.state, 'warn');
    check('unresolved scope is stated in the panel',
        stations([P('A','observe','observe',null)],[T('A','unsigned')],{agent:'A'})
            .bounded.facts.some((f) => /could not be resolved/.test(f.d || '')), true);
    report('aggregation is worst-of')();
}

// ── estate EXECUTION — worst-of over per-agent bands (drill-down consistency) ─
// Shipped defect: GROUNDED/GUARDED banded the PROPORTION of clean agents and JUDGED
// pooled turns across the estate — so one agent whose own focused ring read danger
// hid behind a healthy average (9/10 clean = 90% → green; 6 failing turns among 100
// pooled = 94% → green). "Quiet" at estate level did not mean "no agent needs you".
// The invariant pinned here: the estate station never reads better than the worst
// colour a drill-down can reach, because both altitudes run the SAME banding.
section('estate execution — a red agent is never hidden by a healthy average');
{
    const tags = ['A0', 'A1', 'A2', 'A3', 'A4', 'A5', 'A6', 'A7', 'A8', 'J'];
    const profs = tags.map((t) => P(t, 'observe', 'observe'));
    const trust = tags.map((t) => T(t, 'unsigned'));
    const cells = {};
    tags.forEach((t) => {
        cells[t] = { turns: 10, failures: 0, tag: t,
            dimensions: { grounded_overlap: 1, safety: 1, completed: 1 },
            dimension_counts: { grounded_overlap: 10, safety: 10, completed: 10 } };
    });
    cells.J = { turns: 10, failures: 6, tag: 'J',
        dimensions: { grounded_overlap: 0.4, safety: 1, completed: 0.4 },
        dimension_counts: { grounded_overlap: 10, safety: 10, completed: 10 } };
    const est = stations(profs, trust, {
        dashboard: { enabled: true, total_spans: 100, spans_with_failure: 6, by_agent: cells } });
    // RETARGETED to JUDGED by the lane cut: the worst-of rollup rule is unchanged and
    // still load-bearing — it just governs the station that carries measurement now.
    // The fixture is the same one that produced the defect the rule exists for (1 red
    // agent of 10 read green under a proportional rollup).
    check('1 danger agent of 10 → estate JUDGED reads danger (was 90% → green)',
        est.scored.execution.state, 'danger');
    check('…the magnitude survives in the value (Rule 5.2)',
        est.scored.execution.text, '9/10');
    check('…and Evidence names the agent behind the colour',
        /@J/.test(String((est.scored.facts[3] || {}).d || '')), true);
    check('…and its Why speaks in worst-of vocabulary, not the pooled floor',
        /weakest scored agent/.test(String((est.scored.facts[2] || {}).d || '')), true);
    // A guarantee station is no longer coloured by measurements AT ALL — the complement
    // of the rule above, and the one that proves the two lanes are actually separate.
    check('GROUNDED is not raised by a measurement failure, at estate scope either',
        est.grounded.execution.state !== 'danger', true);
    check('GUARDED likewise stays out of attention on a measurement-only failure',
        est.guarded.execution.state !== 'danger', true);

    // ── THE LANE CUT: one failure lights ONE station — and it is JUDGED ──────
    // An agent failing ONLY a grounding check. Measurement is a lane, so the failure
    // is counted at JUDGED and nowhere else; GROUNDED reports what its MECHANISMS did
    // and is not coloured by a measurement at all.
    //
    // This fixture has now pinned three different rules and the history is the point:
    //   union (pre-Aug 2026)  both stations red — one cause, "2 need you now"
    //   guarantee partition   GROUNDED red, JUDGED quiet
    //   lane cut (here)       JUDGED red, GROUNDED reads its own mechanisms
    // Exclusivity is the invariant that survived all three, and it is what separates
    // this model from the union: exactly one station is raised by one root cause.
    const kCells = {};
    tags.forEach((t) => {
        kCells[t] = { turns: 10, failures: 0, tag: t,
            dimensions: { grounded_overlap: 1, safety: 1, completed: 1 },
            dimension_counts: { grounded_overlap: 10, safety: 10, completed: 10 } };
    });
    kCells.J = { turns: 10, failures: 6, tag: 'J',
        dimensions: { grounded_overlap: 0.4, safety: 1, completed: 1 },
        dimension_counts: { grounded_overlap: 10, safety: 10, completed: 10 },
        turn_failed_dims: [['grounded_overlap'], ['grounded_overlap'], ['grounded_overlap'],
                           ['grounded_overlap'], ['grounded_overlap'], ['grounded_overlap'],
                           [], [], [], []] };
    const kEst = stations(profs, trust, {
        dashboard: { enabled: true, total_spans: 100, spans_with_failure: 6, by_agent: kCells } });
    check('lane cut: a grounding-only failure turns JUDGED red…',
        kEst.scored.execution.state, 'danger');
    check('lane cut: …and does NOT colour GROUNDED — a measurement is not a mechanism',
        kEst.grounded.execution.state !== 'danger', true);
    const kFoc = stations(profs, trust, { agent: 'J',
        dashboard: { enabled: true, by_agent: kCells }, byAgent: kCells });
    check('lane cut: focused too — red JUDGED beside a quiet GROUNDED is legitimate',
        kFoc.scored.execution.state === 'danger'
        && kFoc.grounded.execution.state !== 'danger', true);
    // EXCLUSIVITY — the invariant that outlived all three models. Exactly ONE station
    // is raised by one root cause, or the routing summary lies about how many problems
    // there are (the union's defect, in the fixture that produced it).
    check('lane cut: exactly one station is raised by one root cause',
        ['grounded', 'guarded', 'bounded', 'scored', 'proven']
            .filter((k) => kFoc[k].execution.state === 'danger').length, 1);
    // A guarantee station must NAME where its measurements went — a bare `—` where a
    // band used to be is reach loss: the fact still exists but is further away, and an
    // inventory passes while the surface gets worse.
    check('lane cut: GROUNDED names that its checks are counted at JUDGED',
        /counted at JUDGED/.test(String(kFoc.grounded.execution.detail || '')), true);
    check('lane cut: …and renders `—` rather than collapsing the attribute (Rule 1.2)',
        kFoc.grounded.value.endsWith('· —'), true);
    // W3 — a guarantee station carries ONE graded line for its checks (counted at
    // JUDGED, where every chip lives), never the chip grid that coloured nothing here.
    check('lane cut: …and its derivation carries the graded line, not the chip grid',
        !!((kFoc.grounded.derivation || {}).graded) && ((kFoc.grounded.derivation || {}).dims || []).length === 0, true);
    check('lane cut: …and the graded line counts the checks about this guarantee',
        ((kFoc.grounded.derivation || {}).graded || {}).of > 0, true);
    check('lane cut: JUDGED carries every measured check, guarantee-labelled included',
        ((kFoc.scored.derivation || {}).dims || [])
            .some((x) => x.key === 'grounded_overlap'), true);
    check('lane cut: JUDGED\'s note states the rule rather than the retired ownership',
        /Measurement is a\s+LANE/.test(String((kFoc.scored.derivation || {}).note || ''))
        || /where MEASUREMENT is counted/.test(String((kFoc.scored.derivation || {}).note || '')), true);
    check('lane cut: no station claims to own a subset of the checks',
        ['grounded', 'guarded', 'bounded', 'scored', 'proven'].every((k) =>
            !/this station owns/.test(JSON.stringify(kFoc[k]))), true);

    // Drill-down consistency: the estate colour IS the worst focused colour.
    const focJ = stations(profs, trust, { agent: 'J',
        dashboard: { enabled: true, by_agent: cells }, byAgent: cells });
    check('the estate colour equals the worst drill-down colour',
        est.scored.execution.state, focJ.scored.execution.state);

    // Amber propagates as amber — worst-of, not any-shortfall-is-red.
    const soft = { ...cells, J: { ...cells.J, failures: 2,
        dimensions: { grounded_overlap: 0.7, safety: 1 },
        dimension_counts: { grounded_overlap: 10, safety: 10 } } };
    check('an amber agent turns the estate amber, never red',
        stations(profs, trust, { dashboard: { enabled: true, by_agent: soft } })
            .scored.execution.state, 'warn');

    // An agent with NOTHING measured on a station's keys is excluded — never
    // counted clean (the old rollup counted every cell, measured or not).
    const two = ['A0', 'A1'].map((t) => P(t, 'observe', 'observe'));
    const twoTrust = ['A0', 'A1'].map((t) => T(t, 'unsigned'));
    const partial = stations(two, twoTrust, { dashboard: { enabled: true, by_agent: {
        A0: { turns: 5, failures: 0, tag: 'A0',
              dimensions: { grounded_overlap: 1 }, dimension_counts: { grounded_overlap: 5 } },
        A1: { turns: 5, failures: 0, tag: 'A1',
              dimensions: { safety: 1 }, dimension_counts: { safety: 5 } },
    } } });
    check('an agent unmeasured on the scored checks is excluded, not counted clean',
        partial.scored.execution.text, '2/2');
    report('estate execution is worst-of, consistent with drill-down')();
}

// ── the trust-trip lens — two lenses, one record (TRUST_TRIP_RESET.md) ───────
// The speedometer model: the odometer (whole record) never resets; the trip cell
// is the SAME aggregator's output over the current leg, selected read-time by the
// lens. Pinned here: W1 (total lens byte-identical, trip cell or not), W3 (every
// trip-scoped reading names its boundary; an empty leg says so), W5 (TRUSTED's
// composite never re-windows), W6 (estate = each agent since its OWN reset,
// disclosed), W7 (the previous-leg comparison renders).
section('the trust-trip lens — disclosed, and lens-invariant where it must be');
{
    const mkCell = (over = {}) => ({ turns: 10, failures: 4, tag: 'A',
        dimensions: { grounded_overlap: 0.6, safety: 1, completed: 0.6 },
        dimension_counts: { grounded_overlap: 10, safety: 10, completed: 10 }, ...over });
    const cellWithTrip = mkCell({ trip: { turns: 3, failures: 0, tag: 'A',
        dimensions: { grounded_overlap: 1, safety: 1, completed: 1 },
        dimension_counts: { grounded_overlap: 3, safety: 3, completed: 3 },
        since: '2026-07-30T10:00:00+00:00', leg_n: 2, note: 'after the fix',
        prev: { turns: 8, failures: 5,
                dimensions: { grounded_overlap: 0.375, completed: 0.375 },
                dimension_counts: { grounded_overlap: 8, completed: 8 } } } });
    const base = (lens, cell) => stations([P('A', 'observe', 'observe')], [T('A', 'unsigned')], {
        agent: 'A', lens,
        dashboard: { enabled: true, by_agent: { A: cell } }, byAgent: { A: cell } });

    // W1 — the odometer is inviolable: the whole-record lens with a trip cell
    // present renders byte-identically to a world with no marker at all.
    check('the whole-record lens ignores the trip cell entirely (W1)',
        JSON.stringify(base('total', cellWithTrip).grounded),
        JSON.stringify(base('total', mkCell()).grounded));

    const t = base('trip', cellWithTrip);
    assertGrammar('trip/focused', t);   // the FULL grammar contract holds under the lens
    check('the trip lens reads the LEG figures', t.scored.execution.text, '100%');
    check('…and the reading names its boundary (W3)',
        /this leg \(since /.test(t.scored.facts[1].d), true);
    check('…and the derivation window names the reset',
        /since the trip reset on /.test((t.scored.derivation || {}).window || ''), true);
    check('W7: the previous leg is compared in the derivation',
        /previous leg read 3\/8 clean/.test((t.scored.derivation || {}).exec || ''), true);
    check('JUDGED reads the leg too', t.scored.execution.text, '100%');
    check('…with its own previous-leg comparison',
        /previous leg read 3\/8 clean/.test((t.scored.derivation || {}).exec || ''), true);
    check('PROVEN counts the leg\'s sealed verdicts', t.proven.execution.text, '3 sealed');

    // An EMPTY leg is empty — never clean, never "no execution data".
    const empty = base('trip', mkCell({ trip: { turns: 0, failures: 0,
        since: '2026-07-30T10:00:00+00:00', leg_n: 1 } }));
    check('an empty leg says so', /the leg is empty, not clean/.test(empty.scored.facts[1].d), true);
    check('…and shows no figure', empty.scored.execution.text, '');

    // W5 — TRUSTED's composite never re-windows; the leg is one LABELLED
    // informational line beside the canonical reading, never in place of it.
    const tb = { enabled: true, breaker_mode: 'off', self_uuid: '', agents: { A: {
        behavior: { turns: 10, score: 0.7, weighted_pass_rate: 0.7, trust_dim_rate: 0.7,
                    trend: [1, 1, 0] },
        composite: { state: 'watch', reason: 'r', integrity: 'unsigned' } } } };
    const tbOpts = (lens) => ({ agent: 'A', lens, trustBehavior: tb,
        dashboard: { enabled: true, by_agent: { A: cellWithTrip } },
        byAgent: { A: cellWithTrip } });
    const wTrip = stations([P('A', 'observe', 'observe')], [T('A', 'unsigned')], tbOpts('trip'));
    const wTotal = stations([P('A', 'observe', 'observe')], [T('A', 'unsigned')], tbOpts('total'));
    check('TRUSTED\'s execution reading is lens-invariant (W5)',
        JSON.stringify(wTrip.trusted.execution), JSON.stringify(wTotal.trusted.execution));
    check('…with the leg as a labelled informational line only',
        /the badge itself always reads the whole record/.test(wTrip.trusted.facts[3].d), true);

    // W6 — estate under the trip lens: each agent since its OWN reset where one
    // exists; the mix of windows is disclosed, never silently aggregated.
    const est = stations([P('A', 'observe', 'observe'), P('B', 'observe', 'observe')],
        [T('A', 'unsigned'), T('B', 'unsigned')], { lens: 'trip',
            dashboard: { enabled: true, by_agent: {
                A: cellWithTrip,
                B: { turns: 5, failures: 0, tag: 'B', dimensions: { grounded_overlap: 1 },
                     dimension_counts: { grounded_overlap: 5 } },
            } } });
    assertGrammar('trip/estate', est);
    check('estate trip: the mixed windows are disclosed',
        /1 of 2 have an open leg/.test((est.scored.derivation || {}).window || ''), true);
    check('…and the unmarked agent keeps its whole record in the rollup',
        est.scored.execution.text, '2/2');

    // An unmarked FOCUSED agent under the trip lens reads its whole record —
    // nothing is trip-scoped, so nothing carries a leg disclosure.
    check('an unmarked focused agent under the trip lens = its whole record',
        JSON.stringify(base('trip', mkCell()).grounded),
        JSON.stringify(base('total', mkCell()).grounded));
    report('the trip lens — two lenses, one record')();
}

// ── integrity mapping ────────────────────────────────────────────────────────
section('integrity states — all four wire strings + the unknown case');
{
    const p = [P('A', 'observe', 'observe')];
    const s = (state) => stations(p, [T('A', state)], { agent: 'A' }).signed.execution.state;
    check('unsigned, practice not in use -> na', s('unsigned'), 'na');
    check('trusted -> pass', s('trusted'), 'pass');
    check('changed -> danger', s('changed'), 'danger');
    // Publisher-signed but not attested HERE is genuinely partial: dashed would imply
    // nothing is left to do, which is false — a local attestation is outstanding.
    check('deployment_unattested -> warn (partial, not dashed)', s('deployment_unattested'), 'warn');
    check('unknown wire string -> na, no crash', s('a_state_added_later'), 'na');
    check('one changed dominates all-trusted (worst-of)',
        stations([P('A', 'observe', 'observe'), P('B', 'observe', 'observe')],
            [T('A', 'trusted'), T('B', 'changed')]).signed.execution.state, 'danger');
    report('integrity mapping is complete')();
}

// ── unsigned · deliberate absence vs outstanding work ────────────────────────
section('unsigned — "not turned on" only while signing is not in use');
{
    // The four levels conflate two kinds of absence. `na` must mean nothing is owed;
    // an unsigned agent in a deployment that DOES sign has work outstanding, and
    // reading it as "not turned on" tells the operator there is nothing to do.
    const two = [P('A', 'observe', 'observe'), P('B', 'observe', 'observe')];

    const virgin = stations(two, [T('A', 'unsigned'), T('B', 'unsigned')], { agent: 'A' });
    check('nothing ever attested -> na (practice not in use, nothing owed)',
        virgin.signed.execution.state, 'na');
    check('and the panel says signing is not in use',
        virgin.signed.facts.some((f) => /not in use|nothing is owed/.test(f.d || '')), true);

    const inUse = stations(two, [T('A', 'unsigned'), T('B', 'trusted')], { agent: 'A' });
    check('another agent signed -> warn (outstanding)', inUse.signed.execution.state, 'warn');
    check('and the panel says sign-off is outstanding',
        inUse.signed.facts.some((f) => /outstanding/.test(f.d || '')), true);

    // Any attestation at all means the practice is in use — including the two states
    // that are not a clean pass.
    check('a `changed` peer also counts as practice-in-use',
        stations(two, [T('A', 'unsigned'), T('B', 'changed')], { agent: 'A' }).signed.execution.state, 'warn');
    check('a `deployment_unattested` peer also counts as practice-in-use',
        stations(two, [T('A', 'unsigned'), T('B', 'deployment_unattested')], { agent: 'A' }).signed.execution.state, 'warn');

    // The practice signal is deployment-wide, never the focused scope — otherwise a
    // focused unsigned agent could never read as outstanding.
    check('a signed agent is unaffected by the rule',
        stations(two, [T('A', 'trusted'), T('B', 'unsigned')], { agent: 'A' }).signed.execution.state, 'pass');
    check('unknown wire string stays na even when the practice is in use',
        stations(two, [T('A', 'a_state_added_later'), T('B', 'trusted')], { agent: 'A' }).signed.execution.state, 'na');

    // Estate consequence: a wholly unsigned estate stays dashed; a mixed one is amber.
    check('all-unsigned estate -> na',
        stations(two, [T('A', 'unsigned'), T('B', 'unsigned')]).signed.state, 'na');
    check('mixed estate -> warn',
        stations(two, [T('A', 'unsigned'), T('B', 'trusted')]).signed.state, 'warn');
    report('unsigned distinguishes deliberate from outstanding')();
}

// ── 6 · no scope-size constants ──────────────────────────────────────────────
section('no station may render a bare scope-size count');
{
    // Three stations once rendered `profiles.length` / `states.length`, which is
    // always "1" for a focused agent — a constant that reads like a measurement.
    const st = stations([P('A', 'observe', 'observe')], [T('A', 'unsigned')], { agent: 'A' });
    let offenders = 0;
    for (const key of STATION_KEYS) {
        const sides = String(st[key].value).split(' · ');
        if (sides.some((v) => v.trim() === '1')) { offenders++; failures.push(`${key} renders a bare "1"`); }
    }
    check('no bare scope-size side in any station', offenders, 0);

    // Prefixing the count with a word disguises it but does not fix it: REVIEWED read
    // "1 can park" for a focused agent, which is 1/1 wearing a noun. Any leading integer
    // equal to the focused scope size is the same defect, however it is dressed.
    // The fixture must ACTIVATE every configuration side, or the rule is asserted
    // against stations that never produced a count. An all-`observe` agent leaves
    // REVIEWED at "none park" and the check passes vacuously — which is exactly how
    // "1 can park" shipped. Both postures are swept.
    let dressed = 0;
    [st,
     stations([P('A', 'review', 'review')], [T('A', 'trusted')], { agent: 'A' }),
     stations([P('A', 'enforce', 'enforce')], [T('A', 'changed')], { agent: 'A' }),
    ].forEach((fx, i) => {
        for (const key of STATION_KEYS) {
            String(fx[key].value).split(' · ').forEach((v) => {
                const m = /^\s*(\d+)\b/.exec(v);
                // A ratio (`1/4`) is a real reading; a leading bare count is not.
                if (m && m[1] === '1' && !/^\s*\d+\s*\//.test(v)) {
                    dressed++;
                    failures.push(`${key} (fixture ${i}) renders a dressed scope-size count: "${v.trim()}"`);
                }
            });
        }
    });
    check('no scope-size count dressed up with a word', dressed, 0);

    // Estate scope is where a count IS a proportion — it must survive the rule above.
    const es = stations([P('A', 'review', 'review'), P('B', 'observe', 'observe')],
        [T('A', 'unsigned'), T('B', 'unsigned')], {});
    check('estate scope keeps its real counts', /can park/.test(es.reviewed.value), true);
    report('no scope-size constants')();
}

// ── 6a · a qualifier must add meaning, never echo a wire string ──────────────
// The Execution qualifier under SIGNED rendered the raw enum `deployment_unattested`:
// a machine token in a human slot, saying nothing the value had not already said.
section('qualifiers are written for a person, not copied from the wire');
{
    const WIRE = ['deployment_unattested', 'trusted', 'changed', 'unsigned'];
    let leaks = 0;
    WIRE.forEach((state) => {
        const st = stations([P('A', 'observe', 'observe')], [T('A', state)], { agent: 'A' });
        st.signed.facts.forEach((f) => {
            // `unsigned`/`changed` are ordinary English and legitimate in prose; the
            // give-away is the snake_case token, which no sentence would contain.
            if (/[a-z]+_[a-z_]+/.test(String(f.d || ''))) {
                leaks++; failures.push(`SIGNED(${state}) qualifier leaks a wire token: "${f.d}"`);
            }
        });
        check(`${state}: the qualifier says what it means`,
            String(st.signed.facts[1].d || '').length > 12, true);
    });
    check('no snake_case wire token reaches a hover slot', leaks, 0);

    // The rule was written for SIGNED and enforced on SIGNED only — so GROUNDED printed
    // `grounded_overlap` at a user, in two slots, while the gate reported clean. Scorer
    // keys reach the ring through the eval payload, so the sweep must cover every
    // station that renders one.
    const withDims = stations([{ ...P('UDERIA', 'review', 'review'), tag: 'UDERIA' }],
        [T('UDERIA', 'trusted')], { agent: 'UDERIA', byAgent: { UDERIA: {
            turns: 4,
            dimensions: { grounded_overlap: 0.25, groundedness: 0.75, no_tool_loops: 0.5 },
            dimension_counts: { grounded_overlap: 4, groundedness: 4, no_tool_loops: 4 },
            dimension_series: { grounded_overlap: [1, 0, 0, 0], groundedness: [1, 0, 1, 1] } } } });
    let keyLeaks = 0;
    STATION_KEYS.forEach((key) => {
        withDims[key].facts.forEach((f) => {
            [f.v, f.d].forEach((txt) => {
                if (/\b[a-z]+_[a-z_]+\b/.test(String(txt || ''))) {
                    keyLeaks++;
                    failures.push(`${key}: raw scorer key in a slot — "${txt}"`);
                }
            });
        });
    });
    check('no station prints a raw scorer key', keyLeaks, 0);
    check('dimension names come from the platform label source',
        new RegExp(QD.label('grounded_overlap')).test(withDims.scored.facts[1].d), true);
    check('and no "(s)" placeholder reaches a polished surface',
        /\(s\)/.test(withDims.scored.facts[3].d), false);
    report('qualifiers add meaning, never echo the wire')();
}

// ── 6b · no slot may restate another slot on the same panel ──────────────────
// The wire-token fix above was first written as a paraphrase of the headline — the
// same "adds nothing" defect in different clothes, and structurally invisible to a
// rule that only looked for snake_case. Each slot has a distinct job (value = what it
// is, headline = so what, qualifier = what was recorded); saying one thing three times
// makes a panel look full while carrying one fact.
section('slots must not restate each other');
{
    const STOP = new Set(['this', 'that', 'with', 'from', 'have', 'been', 'were', 'they',
                          'them', 'then', 'than', 'what', 'when', 'which', 'their', 'there',
                          'here', 'into', 'only', 'once', 'every', 'will', 'does', 'each']);
    const words = (s) => String(s || '').toLowerCase().replace(/[^a-z\s]/g, ' ')
        .split(/\s+/).filter((w) => w.length > 3 && !STOP.has(w));
    /** Fraction of the qualifier's content words already present in the headline. */
    const echo = (q, h) => {
        const qw = words(q);
        if (qw.length < 4) return 0;             // too short to judge; not an echo
        const hw = new Set(words(h));
        return qw.filter((w) => hw.has(w)).length / qw.length;
    };
    const FIXTURES = [
        ['unsigned', stations([P('A', 'observe', 'observe')], [T('A', 'unsigned')], { agent: 'A' })],
        ['trusted', stations([P('A', 'review', 'review')], [T('A', 'trusted')], { agent: 'A' })],
        ['changed', stations([P('A', 'enforce', 'enforce')], [T('A', 'changed')], { agent: 'A' })],
        ['unattested', stations([P('A', 'review', 'review')], [T('A', 'deployment_unattested')], { agent: 'A' })],
        ['estate', stations([P('A', 'review', 'review'), P('B', 'observe', 'observe')],
                            [T('A', 'trusted'), T('B', 'unsigned')], {})],
    ];
    let echoes = 0;
    FIXTURES.forEach(([name, st]) => {
        STATION_KEYS.forEach((key) => {
            const s = st[key];
            // Slots 0/1 are Configuration/Execution qualifiers; 2/3 are Why/Evidence,
            // which legitimately restate the rule and are exempt.
            [0, 1].forEach((i) => {
                const r = echo(s.facts[i].d, s.headline);
                if (r > 0.7) {
                    echoes++;
                    failures.push(`${key} (${name}) slot ${i} restates the headline (${Math.round(r * 100)}% overlap)`
                        + `\n      qualifier: "${s.facts[i].d}"\n      headline:  "${s.headline}"`);
                }
            });
        });
    });
    check('no qualifier restates its own headline', echoes, 0);

    // Slot-vs-SLOT, not just slot-vs-headline. The first version of this rule checked
    // only against the headline, and GROUNDED shipped with Execution and Evidence
    // carrying byte-identical text — two slots, one fact, invisible to the gate that
    // was supposed to prevent exactly that. A rule enforced on one axis is enforced on
    // one axis only.
    let twins = 0;
    const byAgent = { UDERIA: { turns: 4, dimensions: { grounded_overlap: 0.25, groundedness: 0.75 },
                                dimension_counts: { grounded_overlap: 4, groundedness: 4 },
                                dimension_series: { grounded_overlap: [1, 0, 0, 0], groundedness: [1, 0, 1, 1] } } };
    const withData = stations([{ ...P('UDERIA', 'review', 'review'), tag: 'UDERIA' }],
                              [T('UDERIA', 'trusted')], { agent: 'UDERIA', byAgent });
    [...FIXTURES.map(([, s]) => s), withData].forEach((st) => {
        STATION_KEYS.forEach((key) => {
            const ds = st[key].facts.map((f) => String(f.d || '').trim()).filter((d) => d && d !== _NONE_TXT);
            const seen = new Set();
            ds.forEach((d) => {
                if (seen.has(d)) { twins++; failures.push(`${key}: two slots carry identical text — "${d}"`); }
                seen.add(d);
            });
        });
    });
    check('no two slots on a panel carry the same text', twins, 0);

    // And the fix must be real, not a reword: Evidence names the INSTANCES.
    check('Evidence names the instances behind a shortfall',
        /of the last 4 scored turns/.test(withData.grounded.facts[3].d), true);
    check('and distinguishes a corroborated failure from a single dissenting check',
        /more than one check/.test(withData.grounded.facts[3].d)
        && new RegExp(QD.label('grounded_overlap') + ' only').test(withData.grounded.facts[3].d), true);
    report('slots carry distinct facts')();
}

// ── 6b · actions are derived from state, and only where they can be performed ──
section('station actions must follow the state, not the station');
{
    // A station once carried exactly ONE hardcoded action however many the state
    // warranted: SIGNED offered "open the posture matrix" — a surface that cannot sign —
    // while the guided bottom-up flow existed with no caller. Actions therefore come from
    // the reading, and never appear where they could not be carried out.
    const withTB = (fn) => {
        const had = typeof globalThis.window !== 'undefined' ? globalThis.window : undefined;
        globalThis.window = { TrustBadge: { signAgentGuided() {}, verifyAgent() {}, attestDeployment() {} },
                              QualityDimensions: QD };   // keep the real labels in play (TC-51)
        try { return fn(); } finally {
            if (had === undefined) delete globalThis.window; else globalThis.window = had;
        }
    };
    const TP = (tag, state) => ({ tag, state, ingredients: [], blocking: [], profile_id: 'p-' + tag });
    const acts = (state, opts = {}) => withTB(() => (stations(
        [P('A', 'observe', 'observe')], [TP('A', state)], { agent: 'A', ...opts }
    ).signed.actions || []).map((a) => a.action));

    check('unsigned offers the act that is owed', acts('unsigned').join(','), 'sign-agent');
    // `unsigned` is the PORTABLE half's word. When the backend also says the deployment
    // half owes its own act, offer it — it is the one act a non-signer OWNER can take,
    // and a consumer whose pack agent read `unsigned` was offered only the act they
    // were not permitted to perform (found live, 2026-09-07).
    const TUNS = (dep) => ({ tag: 'A', state: 'unsigned', ingredients: [], blocking: [],
                             profile_id: 'p-A', portable_state: 'unsigned', deployment: dep });
    const unsActs = (dep) => withTB(() => (stations([P('A', 'observe', 'observe')], [TUNS(dep)],
                                                    { agent: 'A' }).signed.actions || []).map((a) => a.action));
    check('unsigned whose deployment is not attested also offers the attest act',
        unsActs({ state: 'unsigned', required: true }).join(','), 'sign-agent,attest-deployment');
    check('...and when the deployment drifted', unsActs({ state: 'changed', required: true }).join(','),
        'sign-agent,attest-deployment');
    check('...but not when nothing deployment-bound is required',
        unsActs({ state: 'unsigned', required: false }).join(','), 'sign-agent');
    check('...nor when the deployment half is already attested',
        unsActs({ state: 'trusted', required: true }).join(','), 'sign-agent');
    check('sign stays the primary act on unsigned',
        withTB(() => (stations([P('A', 'observe', 'observe')], [TUNS({ state: 'unsigned', required: true })],
            { agent: 'A' }).signed.actions || []).filter((a) => a.primary).map((a) => a.action).join(',')),
        'sign-agent');
    check('changed offers re-sign AND verify (two needs, not one)',
        acts('changed').join(','), 'sign-agent,verify-agent');
    check('trusted offers verify, never a redundant sign',
        acts('trusted').join(','), 'verify-agent');
    check('a primary act is marked exactly once', withTB(() => (stations(
        [P('A', 'observe', 'observe')], [TP('A', 'changed')], { agent: 'A' }
    ).signed.actions || []).filter((a) => a.primary).length), 1);

    // `changed` has TWO faces (the two signable halves), and each owes a DIFFERENT
    // act. The shipped defect this pins: a DEPLOYMENT drift (the model moved on this
    // instance while the portable signature stayed intact) offered "Re-sign this
    // agent" — an act that cannot move the state, experienced as "re-signing doesn't
    // work". The owed act there is re-attesting the deployment (Rule 8.5).
    const TDEP = (portable, drift) => ({ tag: 'A', state: 'changed', ingredients: [],
        blocking: [], profile_id: 'p-A', portable_state: portable,
        deployment: { state: 'changed', required: true,
                      drift: drift || ['model changed: Google/g → OpenRouter/m'] } });
    const depSt = withTB(() => stations([P('A', 'observe', 'observe')], [TDEP('trusted')], { agent: 'A' }).signed);
    check('deployment-face changed offers the act that is owed — re-attest, not re-sign',
        (depSt.actions || []).map((a) => a.action).join(','), 'attest-deployment,verify-agent');
    check('…marked primary', (depSt.actions || []).some((a) => a.action === 'attest-deployment' && a.primary), true);
    check('…and the panel names WHICH half moved',
        /THIS deployment moved after it was attested/.test(depSt.facts[1].d), true);
    check('…with the drift named verbatim',
        /model changed: Google\/g → OpenRouter\/m/.test(depSt.facts[1].d), true);
    check('…and Evidence names the clearing act',
        /re-attestation of this deployment is the act that clears it/.test(depSt.facts[3].d), true);
    check('…and the headline says the portable signature is intact',
        /portable signature is intact/.test(depSt.headline), true);
    check('both-halves drift offers both acts, re-sign first (bottom-up)',
        withTB(() => (stations([P('A', 'observe', 'observe')], [TDEP('changed')], { agent: 'A' })
            .signed.actions || []).map((a) => a.action).join(',')),
        'sign-agent,attest-deployment,verify-agent');
    check('a breakdown-less `changed` (older backend) keeps the generic reading',
        withTB(() => stations([P('A', 'observe', 'observe')], [TP('A', 'changed')], { agent: 'A' })
            .signed.facts[1].d.includes('moved since sign-off')), true);

    // Estate scope has no single agent to act on; and without the trust package (the
    // removable Ontology Suite) the buttons must simply not exist.
    check('estate scope offers no per-agent act', withTB(() => (stations(
        [P('A', 'observe', 'observe'), P('B', 'observe', 'observe')],
        [TP('A', 'unsigned'), TP('B', 'unsigned')]).signed.actions || []).length), 0);
    check('no trust package -> no buttons (absence-tolerance)',
        (stations([P('A', 'observe', 'observe')], [TP('A', 'unsigned')],
            { agent: 'A' }).signed.actions || []).length, 0);

    // A blocker gets a ROUTE to its own surface. Signing refuses until the blocking
    // ingredient is signable and the refusal says what to do — but offered nowhere to do
    // it. Only routable kinds are offered (never a button that fails on click), capped so
    // a multi-blocker agent is not a wall of buttons.
    const blocked = (blocking) => withTB(() => (stations(
        [P('A', 'observe', 'observe')],
        [{ tag: 'A', state: 'unsigned', ingredients: [], profile_id: 'p-A', blocking }],
        { agent: 'A' }).signed.actions || []).map((a) => a.action));

    check('a knowledge blocker gets a route to its own surface',
        blocked([{ kind: 'knowledge', artifact_id: '2', label: 'Uderia Documentation' }])
            .includes('open-ingredient:knowledge:2'), true);
    check('the route button carries the artefact NAME, not the id', withTB(() => (stations(
        [P('A', 'observe', 'observe')],
        [{ tag: 'A', state: 'unsigned', ingredients: [], profile_id: 'p-A',
           blocking: [{ kind: 'knowledge', artifact_id: '2', label: 'Uderia Documentation' }] }],
        { agent: 'A' }).signed.actions || []).some((a) => a.label === 'Open Uderia Documentation')), true);
    check('a planner blocker is routable too',
        blocked([{ kind: 'planner', artifact_id: '1' }]).includes('open-ingredient:planner:1'), true);
    // #31 — a skill has a sign-off surface (the TrustBadge popover), so a blocking
    // skill is routable now; this assertion previously pinned its unroutability,
    // which was only correct while no route existed.
    check('#31 a skill blocker gets a route to its trust surface',
        blocked([{ kind: 'skill', artifact_id: 'chart-expert' }])
            .includes('open-ingredient:skill:chart-expert'), true);
    check('an unroutable kind gets NO button (never a dead click)',
        blocked([{ kind: 'extension', artifact_id: 'pdf' }])
            .some((a) => a.indexOf('open-ingredient') === 0), false);
    check('blocker routes are capped at two',
        blocked([{ kind: 'knowledge', artifact_id: '2' }, { kind: 'planner', artifact_id: '1' },
                 { kind: 'planner', artifact_id: '3' }])
            .filter((a) => a.indexOf('open-ingredient') === 0).length, 2);
    check('no blockers -> no route buttons', blocked([])
        .some((a) => a.indexOf('open-ingredient') === 0), false);

    // Navigation is never removed by adding acts — the old route stays available.
    check('the navigation cta survives alongside the acts',
        withTB(() => stations([P('A', 'observe', 'observe')], [TP('A', 'unsigned')],
            { agent: 'A' }).signed.cta.action), 'posture-matrix');

    // Every station still exposes an actions array, so the panel never branches on shape.
    const st = withTB(() => stations([P('A', 'observe', 'observe')], [TP('A', 'unsigned')], { agent: 'A' }));
    let bad = 0;
    for (const k of STATION_KEYS) if (!Array.isArray(st[k].actions)) { bad++; failures.push(`${k} has no actions array`); }
    check('every station exposes an actions array', bad, 0);
    report('state-derived actions')();
}

// ── 6c · a blocker must be named, not just identified ────────────────────────
section('SIGNED — a blocker names the artefact when the backend supplies one');
{
    // "knowledge repository #2" was shown at the exact moment it asked you to sign it.
    // A knowledge signature is EARNED (bound to a scan report), so an id is not judgeable.
    const withLabel = stations([P('A', 'observe', 'observe')], [{
        tag: 'A', state: 'unsigned', ingredients: [],
        blocking: [{ kind: 'knowledge', artifact_id: '2', label: 'Uderia Documentation' }],
    }], { agent: 'A' });
    const ev = withLabel.signed.facts.find((f) => f.k === 'Evidence');
    check('the human name appears in Evidence', /Uderia Documentation/.test(ev.d), true);
    check('the bare id no longer stands in for the name', /#2/.test(ev.d), false);

    // An unlabelled blocker still identifies itself — the id is the fallback, never blank.
    const noLabel = stations([P('A', 'observe', 'observe')], [{
        tag: 'A', state: 'unsigned', ingredients: [],
        blocking: [{ kind: 'planner', artifact_id: '7' }],
    }], { agent: 'A' });
    const ev2 = noLabel.signed.facts.find((f) => f.k === 'Evidence');
    check('no label -> falls back to the id, never an empty name', /planner repository #7/.test(ev2.d), true);
    report('blockers are named')();
}

// ── REVIEWED · the derived-item honesty rule ─────────────────────────────────
section('REVIEWED — a zero count must never hide a derived observation');
{
    const st = stations([P('A', 'observe', 'observe')], [T('A', 'unsigned')], {
        agent: 'A', inboxBadgeCount: 0, inboxDerivedCount: 1, inboxDerivedDanger: 1,
        inboxDerivedTop: 'danger: possible multi-turn attack',
        inboxByTag: { A: { open: 0, derived: 1, derivedDanger: 1,
                           top: 'danger: possible multi-turn attack' } },
    });
    check('badge count stays 0 (derived carry no disposition)', /0 open/.test(st.reviewed.value), true);
    check('but the derived observation is surfaced in Evidence',
        /derived observation/.test(st.reviewed.facts[3].d), true);
    check('and it is named, not merely counted',
        /multi-turn attack/.test(st.reviewed.facts[3].d), true);
    // Focused scope is binary ("can park" / "cannot park"); estate scope counts.
    check('configuration says whether anything CAN park',
        /cannot park|can park|none park/.test(st.reviewed.value), true);
    report('REVIEWED cannot hide derived work')();
}

// ── Both attributes of a station must describe the SAME subject ──────────────
// REVIEWED paired a per-agent configuration with an estate-wide execution, so a
// focused agent with nothing waiting reported the estate's queue as its own.
{
    const st = stations([P('A', 'observe', 'observe'), P('B', 'observe', 'observe')],
        [T('A', 'unsigned'), T('B', 'unsigned')], {
            agent: 'A', inboxBadgeCount: 3,
            inboxByTag: { B: { open: 3, derived: 0, derivedDanger: 0, top: '' } },
        });
    check('a focused agent reports ITS OWN queue, not the estate\'s',
        /0 open/.test(st.reviewed.value), true);
    check('and the estate figure is still named, so the zero is not misread',
        /3 open estate-wide/.test(st.reviewed.facts[1].d), true);
    // The guarantee is that the headline is about THIS AGENT and never presents the
    // estate's queue as its own. (These fixture agents are both `observe`, so since #60
    // the headline is the inert one — which satisfies the same guarantee more honestly.
    // Asserted as the invariant rather than as a literal phrase, so the two fixes cannot
    // fight over one sentence.)
    check('the headline never reports the estate figure as this agent\'s',
        /3 item/.test(st.reviewed.headline), false);
    check('…and is scoped to this agent', /this agent/.test(st.reviewed.headline), true);
    // The "waiting on your decision" phrasing still applies where work CAN be parked.
    {
        const armed = Object.assign(P('A', 'observe', 'observe'),
            { groundingConfig: { mode: 'review' } });
        const w = stations([armed], [T('A', 'unsigned')], {
            agent: 'A', inboxBadgeCount: 2,
            inboxByTag: { A: { open: 2, derived: 0, derivedDanger: 0, top: '' } },
        });
        check('an armed agent with real work says whose decision is outstanding',
            /waiting on your decision/.test(w.reviewed.headline), true);
    }
    // Estate scope keeps the estate figure — the subject there IS the estate.
    const es = stations([P('A', 'observe', 'observe'), P('B', 'observe', 'observe')],
        [T('A', 'unsigned'), T('B', 'unsigned')], {
            inboxBadgeCount: 3,
            inboxByTag: { B: { open: 3, derived: 0, derivedDanger: 0, top: '' } },
        });
    check('estate scope reports the estate figure', /3 open/.test(es.reviewed.value), true);
    // Items belonging to no agent must be named when focused, never silently dropped.
    const un = stations([P('A', 'observe', 'observe')], [T('A', 'unsigned')], {
        agent: 'A', inboxBadgeCount: 2, inboxUnattributed: 2, inboxByTag: {},
    });
    check('estate-level items are named, not dropped',
        /attributed to no agent/.test(un.reviewed.facts[1].d), true);
    report('REVIEWED — configuration and execution share one subject')();
}

// ── SIGNED — the owed act differs per state, and so must the button ──────────
// `deployment_unattested` is already publisher-signed: offering "Sign this agent"
// put the panel's most prominent action on an act that cannot move the state.
{
    const acts = (state) => {
        const st = stations([P('A', 'observe', 'observe')], [T('A', state)], { agent: 'A' });
        return (st.signed.actions || []).map((a) => a.action);
    };
    // Actions are gated on the trust package being present (window.TrustBadge), which
    // is absent under Node — so assert the SHAPE of the derivation, not live buttons.
    const src = fs.readFileSync(SRC, 'utf8');
    check('an unattested deployment is offered the attest act',
        /deployment_unattested'\)[\s\S]{0,200}attest-deployment/.test(src), true);
    check('and NOT the sign act, which cannot move that state',
        /Attest this deployment[\s\S]{0,80}action: 'sign-agent'/.test(src), false);
    check('the attest act is routed like the other trust acts',
        /action === 'attest-deployment'/.test(src), true);
    check('and it is carried out through ONE shared implementation',
        /TB\.attestDeployment\(/.test(src), true);
    check('never offering a button the trust package cannot honour',
        /deployment_unattested' && !TB\.attestDeployment/.test(src), true);
    check('no action is offered without a focused agent record', acts('unsigned').length, 0);
    report('SIGNED — the offered act follows the state')();
}

// ── SIGNED — integrity carries a SHAPE, because colour is spent on attention ──
// Two states at the same attention level are the same colour by design; without a
// second channel `unsigned` and `deployment_unattested` were indistinguishable.
{
    const src = fs.readFileSync(SRC, 'utf8');
    check('the seal is borrowed from the Trust Membrane, not reinvented',
        /TB\.META && TB\.META\[state\]/.test(src), true);
    check('the glyph is supplied per integrity state', /glyph: _integrityGlyph\(/.test(src), true);
    check('estate scope supplies none — no single state can stand for many',
        /_integrityGlyph\(one && integFor\.length/.test(src), true);
    const st = stations([P('A', 'observe', 'observe')], [T('A', 'unsigned')], { agent: 'A' });
    check('absent trust package degrades to the station default', st.signed.glyph, undefined);
    // The renderer must fall back, never blank the icon.
    const rsrc = fs.readFileSync(
        path.join(HERE, '..', 'static', 'js', 'handlers', 'trustCircle.js'), 'utf8');
    check('the renderer restores the station icon when no glyph is supplied',
        /if \(!want\) \{ host\.innerHTML = ICONS\[key\]/.test(rsrc), true);
    check('and only SIGNED is expected to supply one (documented, not enforced)',
        /Deliberately SIGNED-only/.test(rsrc), true);
    report('SIGNED — shape carries integrity, colour carries attention')();
}

// ── prose must not contradict state ─────────────────────────────────────────
// Three separate `deployment_unattested` defects shipped as prose disagreeing with
// the state it described, none caught by structural assertions. This is that class.
{
    const CONTRADICTIONS = {
        deployment_unattested: [/no baseline exists/i, /not signed yet/i, /never been attested/i],
        trusted: [/no baseline/i, /outstanding/i, /not signed/i],
        changed: [/no baseline exists/i],
    };
    Object.entries(CONTRADICTIONS).forEach(([state, pats]) => {
        const st = stations([P('A', 'observe', 'observe')], [T('A', state)], { agent: 'A' });
        const prose = [st.signed.headline, st.signed.facts.map((f) => `${f.v} ${f.d}`).join(' ')].join(' ');
        pats.forEach((p) => check(`${state}: prose does not claim ${p}`, p.test(prose), false));
    });
    report('SIGNED — prose agrees with the state it describes')();
}

// ── act outcomes are worded from the landed state ───────────────────────────
// The guided sign once reported "content-bound and offline-verifiable"
// unconditionally while landing on `deployment_unattested` — an unfinished job
// reported as finished. The outcome must be read from `signAgentGuided`'s
// returned state, and the outcome for the half-done state must point at the act
// that is owed (Rule 8.5 applied to outcome lines, not just buttons). And the
// outcome must survive its own refresh: `load()` re-mounts the ring and drops
// the pin, so the act flow re-pins the station and rewrites the fresh panel —
// which requires the renderer to export a programmatic `pin`.
{
    const hsrc = fs.readFileSync(SRC, 'utf8');
    const rsrc = fs.readFileSync(
        path.join(HERE, '..', 'static', 'js', 'handlers', 'trustCircle.js'), 'utf8');
    check('the sign outcome is selected by the landed state',
        /_SIGN_OUTCOME\[r\.state\]/.test(hsrc), true);
    check('no unconditional success line remains on the sign path',
        /say\('Signed — content-bound and offline-verifiable\.'\)/.test(hsrc), false);
    const m = hsrc.match(/deployment_unattested:\s*'([^']+(?:'[^']*)*)'/);
    check('the half-done outcome names the act that is owed (attest)',
        !!m && /attest/i.test(m[1]), true);
    check('the act flow restores the station after the reload (_repinAndSay)',
        /_repinAndSay\(stationKey \|\| 'signed'/.test(hsrc), true);
    check('the renderer exports a programmatic pin for that restore',
        /window\.TrustCircle = \{[^}]*\bpin\b/.test(rsrc), true);
    // The live-event path: a trust act on another surface must re-read the facts,
    // not merely animate — pulsing over stale readings was defect #20.
    check('a trust event triggers a debounced trust re-fetch',
        /_circleTrustT = setTimeout\(_refreshCircleTrust/.test(hsrc), true);
    // Extract the function BODY rather than a character window — a char window silently
    // becomes a weaker assertion every time the body grows (it already had to be widened
    // once, which is the tell).
    const _refreshBody = (() => {
        const i = hsrc.indexOf('async function _refreshCircleTrust()');
        if (i < 0) return '';
        const j = hsrc.indexOf('\n    function ', i);
        return hsrc.slice(i, j < 0 ? hsrc.length : j);
    })();
    check('_refreshCircleTrust exists', _refreshBody.length > 0, true);
    check('the refresh repaints in place (update preserves the pin)',
        /TrustCircle\.update\(_buildCircleModel\(\)\)/.test(_refreshBody), true);
    check('…and never re-mounts (a mount would drop the pin)',
        /TrustCircle\.mount\(/.test(_refreshBody), false);
    // REVIEWED reads _inboxByTag, which only renderAssuranceInbox() computes. Repainting
    // the ring without recomputing it raced another listener and left a disposed review
    // reading "1 open".
    // Match the CALL, not the name: the body's own comment mentions
    // `renderAssuranceInbox()`, so a bare-name regex matched the prose and stayed green
    // when the call itself was deleted — found by mutation-testing this very assertion.
    // Sep 2026: the recompute moved behind the ONE post-disposition seam (`refreshInbox`
    // — which itself awaits `renderAssuranceInbox()` and then repaints every reader),
    // so the property here is that the seam is AWAITED before the ring is rebuilt.
    check('the refresh recomputes the inbox (through the one seam) before rebuilding the model',
        /await refreshInbox\(\)[\s\S]*TrustCircle\.update\(_buildCircleModel\(\)\)/.test(_refreshBody), true);
    report('trust acts — outcome wording, panel survival, live re-fetch')();
}

// ── release is never offered for an inbound refusal (security, #51) ─────────
// Release injects the withheld original into subsequent turns' context. For an
// INBOUND item the original is the user's request — for a parked prompt injection,
// the attacker's payload — so releasing it would feed the model the very text the
// guard refused, labelled owner-approved. Four layers must hold; this asserts the
// two that live in this repo's JS + the presence of the backend allow-list.
{
    const hsrc = fs.readFileSync(SRC, 'utf8');
    check('the inbox mirrors the releasable-source allow-list',
        /_RELEASABLE_SOURCES\s*=\s*new Set\(\['grounding',\s*'guardrail_outbound'\]\)/.test(hsrc), true);
    check('inbound is NOT releasable in that mirror',
        /_RELEASABLE_SOURCES\s*=\s*new Set\(\[[^\]]*guardrail_inbound/.test(hsrc), false);
    check('the release button is gated on the allow-list, not rendered unconditionally',
        /_RELEASABLE_SOURCES\.has\(r\.source \|\| ''\)\s*\?[\s\S]{0,400}review-release/.test(hsrc), true);
    const rq = fs.readFileSync(
        path.join(HERE, '..', 'src', 'trusted_data_agent', 'core', 'review_queue.py'), 'utf8');
    check('the store declares the allow-list (single source of truth)',
        /RELEASABLE_SOURCES\s*=\s*frozenset\(\{"grounding",\s*"guardrail_outbound"\}\)/.test(rq), true);
    check('the context-feeding read path filters on it',
        /released_originals_for_session[\s\S]{0,1400}if is_releasable_source\(r\[1\]\)/.test(rq), true);
    const rr = fs.readFileSync(
        path.join(HERE, '..', 'src', 'trusted_data_agent', 'api', 'rest_routes.py'), 'utf8');
    check('the endpoint refuses release for a non-releasable source',
        /release and not review_queue\.is_releasable_source\(item\.get\("source"\)\)/.test(rr), true);
    report('review release — inbound payloads can never re-enter the context')();
}

// ── I3 · one declared aggregation policy at EVERY station (#59, Rule 5.1) ────
// TRUSTED hand-rolled `has('proven') → pass`, a `.some()` in disguise: 2 of 9 agents
// proven rendered the estate GREEN. That is verbatim the defect #6 fixed, on the one
// station never swept — so this asserts the OUTCOME (worst-of holds) and the STRUCTURE
// (no station computes its own cross-agent state), because a policy that must be
// remembered per call site will be forgotten again.
{
    const mixed = [P('A', 'observe', 'observe'), P('B', 'observe', 'observe'),
                   P('C', 'observe', 'observe')];
    const tb = (m) => ({ enabled: true, breaker_mode: 'off', agents: m });
    const cell = (s) => ({ composite: { state: s }, behavior: { turns: 5 } });

    // One proven among provisionals must NOT render green.
    let st = stations(mixed, [], { trustBehavior: tb({
        A: cell('proven'), B: cell('provisional'), C: cell('provisional') }) });
    check('TRUSTED: one proven among provisionals is not green', st.trusted.state, 'warn');
    check('…and the value still carries the magnitude (Rule 5.2)',
        /1\/3/.test(st.trusted.value), true);

    st = stations(mixed, [], { trustBehavior: tb({
        A: cell('proven'), B: cell('trusted'), C: cell('proven') }) });
    check('TRUSTED: all proven/trusted is green', st.trusted.state, 'pass');

    st = stations(mixed, [], { trustBehavior: tb({
        A: cell('proven'), B: cell('at_risk'), C: cell('proven') }) });
    check('TRUSTED: one at_risk dominates (danger)', st.trusted.state, 'danger');

    st = stations(mixed, [], { trustBehavior: tb({
        A: cell('proven'), B: cell('watch'), C: cell('proven') }) });
    check('TRUSTED: one watch pulls the estate to warn', st.trusted.state, 'warn');

    // An estate where NOTHING has earned standing yet must not read "not turned on"
    // while the feature is on — `provisional` is a gap in the record, not a disabled
    // control. Without this fixture the provisional→warn mapping is unobservable
    // (every mixed case resolves to warn regardless), which a mutation test proved.
    st = stations(mixed, [], { trustBehavior: tb({
        A: cell('provisional'), B: cell('provisional'), C: cell('provisional') }) });
    check('TRUSTED: an all-provisional estate is warn, never "not turned on"',
        st.trusted.state, 'warn');

    // Focused scope must be unchanged by worst-of (a population of one).
    st = stations([P('A', 'observe', 'observe')], [], {
        agent: 'A', trustBehavior: tb({ A: cell('proven') }) });
    check('TRUSTED: a focused proven agent stays green', st.trusted.state, 'pass');

    // TC-10/TC-53: a focused at-risk agent must render the HUMAN label everywhere, never the
    // raw composite wire token. The existing snake-sweep never produced a focused `at_risk`
    // composite, so `on · at_risk` in the value slot went unobserved.
    st = stations([P('A', 'observe', 'observe')], [], {
        agent: 'A', trustBehavior: tb({ A: cell('at_risk') }) });
    check('TRUSTED: a focused at_risk agent is danger', st.trusted.state, 'danger');
    check('TRUSTED: focused at_risk never leaks the wire token anywhere in the station',
        /at_risk/.test(JSON.stringify(st.trusted)), false);
    check('TRUSTED: focused at_risk value reads the human label "At risk"',
        /At risk/.test(st.trusted.value || ''), true);

    // Structural: cross-agent state comes from the ONE policy, never a local `.some()`.
    const src = fs.readFileSync(SRC, 'utf8');
    const fn = src.slice(src.indexOf('function _circleStationsFor(agent)'),
                         src.indexOf('function _buildCircleModel()'));
    check('no station hand-rolls a best-of over agent states',
        /states\.includes\(|has\('proven'\)\s*\|\|/.test(fn), false);
    check('TRUSTED routes its cross-agent state through _postureAggregate',
        /_postureAggregate\(states\.map/.test(fn), true);
    report('I3 — one aggregation policy, worst-of, at every station')();
}

// ── #60 · REVIEWED must not read green when it is structurally inert ────────
// With no gate in review mode nothing can EVER be parked, so `0 open` is a tautology
// rather than a measurement — and a green "nothing is waiting" reports the absence of
// work as if it were the absence of risk. Unacknowledged incidents need no review-mode
// gate, so a non-zero count must stay real signal even then.
{
    const noPark = [P('A', 'observe', 'observe'), P('B', 'observe', 'observe')];
    const canPark = [Object.assign(P('A', 'observe', 'observe'),
        { groundingConfig: { mode: 'review' } })];

    let st = stations(noPark, [], {});
    check('REVIEWED: inert estate is "not turned on", never quiet', st.reviewed.state, 'na');
    check('…and says so rather than "nothing is waiting"',
        /can ever be parked|never hand/i.test(st.reviewed.headline), true);
    check('…and its execution qualifier admits the zero is structural',
        /structural, not measured/i.test(st.reviewed.facts[1].d), true);
    // The figure and the estate attribution must SURVIVE the fix — a reader still needs
    // to know where the work lives (Rule 3.1 / the per-agent-vs-estate split of #11).
    check('…while still reporting the figure itself', /0 open/.test(st.reviewed.value), true);

    st = stations(noPark, [], { agent: 'A' });
    check('REVIEWED: inert focused agent is "not turned on"', st.reviewed.state, 'na');

    // An armed gate with an empty queue IS a real quiet reading — the fix must not
    // flatten every zero to `na`.
    st = stations(canPark, [], { agent: 'A' });
    check('REVIEWED: armed gate + empty queue stays quiet', st.reviewed.state, 'pass');
    check('…and still reports the measured zero', /0 open/.test(st.reviewed.value), true);

    // Incidents do not require a review-mode gate: signal survives an inert config.
    st = stations(noPark, [], { inboxBadgeCount: 2 });
    check('REVIEWED: unacknowledged incidents surface even with no parker',
        st.reviewed.state, 'warn');
    report('#60 — an inert REVIEWED reports absence of capability, not absence of risk')();
}

// ── I1 · a gate that ACTED is visible in its own station (#50 #53 #36) ──────
// The scorers structurally cannot report these: an inbound refusal is never scored
// (a blocked turn is kept away from the judges), an outbound redaction is scored
// AFTER redaction (clean by construction), and a cleared escalation leaves no failing
// dimension at all. So the station read "clean · none in this window" on a day the
// guard refused an attack. The gate block on the per-agent cell is the missing half.
{
    const armed = Object.assign(P('A', 'review', 'review'),
        { groundingConfig: { mode: 'review' }, guardrailConfig: { outbound: { mode: 'review' }, inbound: { mode: 'review' } } });
    const online = (gate, dims) => ({
        turns: 11, failures: 0, dimensions: dims || { safety: 1, output_sanity: 1,
            groundedness: 1, grounded_overlap: 1 },
        dimension_counts: { safety: 11 }, gate,
    });

    // #50 + #53 — the guard acted; GUARDED must say so.
    let st = stations([armed], [T('A', 'trusted')], {
        agent: 'A', byAgent: { A: online({ refused: 1, redacted: 1, parked: 2, acted: 2 }) } });
    check('#50 GUARDED names the inbound refusal', /1 refused/.test(st.guarded.facts[1].d), true);
    check('#53 …and the outbound redaction', /1 redacted/.test(st.guarded.facts[1].d), true);
    check('…and Evidence is no longer "none in this window"',
        /none in this window/.test(st.guarded.facts[3].d), false);
    check('…it names the acts instead', /refused|redacted/.test(st.guarded.facts[3].d), true);

    // #36 — a cleared escalation is the strongest evidence a gate works.
    st = stations([armed], [T('A', 'trusted')], {
        agent: 'A', byAgent: { A: online({ escalated: 1, cleared: 1, acted: 0, parked: 0 }) } });
    check('#36 GROUNDED reports the cleared escalation',
        /checked and cleared/.test(st.grounded.facts[1].d), true);
    check('…and Evidence stops claiming nothing happened',
        /none in this window/.test(st.grounded.facts[3].d), false);

    // A withheld answer must be named, not merely counted.
    st = stations([armed], [T('A', 'trusted')], {
        agent: 'A', byAgent: { A: online({ blocked: 1, parked: 1, acted: 1 }) } });
    check('GROUNDED names a withheld answer', /1 withheld/.test(st.grounded.facts[1].d), true);
    // `parked` is a gate-WIDE count, so no station may report it: live proof showed
    // GROUNDED claiming "1 parked for review" for a park the inbound GUARDRAIL did.
    // Each station reports its own acts; the park's home station is REVIEWED.
    st = stations([armed], [T('A', 'trusted')], {
        agent: 'A', byAgent: { A: online({ refused: 1, parked: 1, acted: 1 }) } });
    check('GROUNDED never claims a park another lane performed',
        /parked/.test(st.grounded.facts[1].d), false);
    check('…and GUARDED does not either', /parked/.test(st.guarded.facts[1].d), false);
    check('…while GUARDED still names its own act', /1 refused/.test(st.guarded.facts[1].d), true);

    // Absence: no gate block ⇒ byte-identical to before (masters off must not change
    // a single reading, which is what makes this safe to ship on).
    const noGate = stations([armed], [T('A', 'trusted')], {
        agent: 'A', byAgent: { A: online(null) } });
    check('no gate record ⇒ the qualifier names no gate act',
        /refused|redacted|withheld|cleared/.test(noGate.guarded.facts[1].d), false);
    // GUARDED no longer carries a rate — its execution is its guards' own record —
    // so with no gate act it names where its checks are counted instead of a figure.
    check('…and with no act it names where its checks are counted',
        /counted at JUDGED/.test(noGate.guarded.facts[1].d), true);
    check('…and Evidence falls back to the honest absence',
        noGate.guarded.facts[3].d, 'none in this window');
    // A zeroed gate block must read like absence too — never "0 refused".
    const zeroGate = stations([armed], [T('A', 'trusted')], {
        agent: 'A', byAgent: { A: online({ refused: 0, redacted: 0, parked: 0 }) } });
    check('a zeroed gate block reports nothing rather than zeros',
        /refused|redacted|parked/.test(zeroGate.guarded.facts[1].d), false);
    report('I1 — the gate\'s outcome is visible in its own station')();
}

// ── I4 · Rule 3.4 — the execution qualifier expands its anchor ──────────────
// Only SCORED ever did this, and it is the only rate station nobody misread. The two
// that dropped the UNIT are exactly the two that misled this product's author — twice
// in one sitting ("9/11 · Grounding Overlap 9/11 passed" read as "1 turn HAD an
// overlap", the exact inverse). A ratio without its unit is not a shorter sentence,
// it is an ambiguous one.
{
    const armed = Object.assign(P('A', 'review', 'review'),
        { groundingConfig: { mode: 'review' } });
    const withDims = {
        turns: 11, failures: 2,
        dimensions: { safety: 1, output_sanity: 1, grounded_overlap: 0.8181, completed: 0.8181 },
        dimension_counts: { safety: 11, grounded_overlap: 11, completed: 11 },
    };
    const st = stations([armed], [T('A', 'trusted')], { agent: 'A', byAgent: { A: withDims } });

    // The rate-bearing focused stations must each name their unit. GROUNDED and
    // GUARDED left this set with the lane cut (their execution is their mechanisms'
    // acts, not a rate); the rule is unchanged where a rate still renders.
    ['scored'].forEach((k) => {
        const d = st[k].facts[1].d;
        check(`${k}: the qualifier names the unit being counted`, /\bturns?\b/.test(d), true);
        check(`${k}: …and expands the anchor in words ("N of M")`, /\d+ of \d+ /.test(d), true);
    });

    // It must not merely restate the anchor's digits as its whole content (Rule 3.0).
    const anchor = st.scored.facts[1].v;                      // e.g. "9/11"
    check('JUDGED: the qualifier is not a restatement of the anchor',
        st.scored.facts[1].d.trim() === anchor, false);
    // A dimension may QUALIFY the predicate, never stand in for the unit.
    check('JUDGED: a failing dimension is named as a shortfall, not as the unit',
        /shortfall:/.test(st.scored.facts[1].d), true);
    // The complement: a guarantee station names where its checks went, and offers the
    // dimension rows so the reading is one click away rather than gone.
    check('GROUNDED: names that its checks are counted at JUDGED',
        /counted at JUDGED/.test(st.grounded.facts[1].d), true);
    check('GROUNDED: …and its derivation carries the graded line (W3)',
        (((st.grounded.derivation || {}).graded || {}).of || 0) > 0, true);

    // Estate: the qualifier must expand the ANCHOR, never its complement — `2/3`
    // beside "1 of 3 show shortfalls" put two numerators for one population on a line.
    // 2 clean of 3, 1 short — deliberately asymmetric, so the anchor's numerator (2)
    // and the complement's (1) DIFFER. A 1-of-2 fixture cannot tell them apart, which
    // is how the first version of this assertion passed against the defect it targets.
    const es = stations([P('A', 'observe', 'observe'), P('B', 'observe', 'observe'),
                         P('C', 'observe', 'observe')],
        [T('A', 'unsigned'), T('B', 'unsigned'), T('C', 'unsigned')], {
            dashboard: { by_agent: { A: { dimensions: { grounded_overlap: 0.5 } },
                                     B: { dimensions: { grounded_overlap: 1 } },
                                     C: { dimensions: { grounded_overlap: 1 } } } } });
    const ed = es.scored.facts[1];
    // FORMAT RULE (operator-settled): per-TURN rates render as % (focused scope);
    // per-AGENT ratios keep x/y — agents are enumerable and acted on individually,
    // so the estate execution here stays a fraction.
    check('estate JUDGED: an agent ratio stays x/y',
        /^\d+\/\d+$/.test(String(es.scored.execution.text)), true);
    check('…never a percentage', /%/.test(String(es.scored.execution.text)), false);
    check('…and names the unit', /scored agents?/.test(ed.d), true);

    // #49 — no "(s)" placeholder may reach a polished surface. #16 fixed one site;
    // this asserts the whole station model, so the sweep cannot be half-done again.
    const scan = (obj) => [obj.value, obj.headline, ...obj.facts.map((f) => `${f.v} ${f.d}`)].join(' ');
    const inboxy = stations([armed], [T('A', 'trusted')], {
        agent: 'A', inboxBadgeCount: 3, inboxDerivedCount: 2, inboxUnattributed: 2,
        inboxByTag: { A: { open: 1, derived: 2, derivedDanger: 1, top: 'danger: x' } },
        byAgent: { A: withDims } });
    [st, es, inboxy].forEach((set, i) => {
        Object.entries(set).forEach(([k, s2]) => {
            check(`${['focused', 'estate', 'inbox'][i]}/${k}: no "(s)" pluralization placeholder`,
                /\(s\)/.test(scan(s2)), false);
        });
    });

    // #61 — the singular focused phrasing must not leak into estate scope.
    const many = stations(
        [P('A', 'observe', 'observe'), P('B', 'observe', 'observe'), P('C', 'observe', 'observe')],
        [T('A', 'trusted'), T('B', 'unsigned'), T('C', 'unsigned')], {});
    check('#61 estate SIGNED does not say "this one"',
        /this one/.test(many.signed.facts[2].d), false);
    check('…it counts the unsigned agents instead',
        /2 agents here are not signed yet/.test(many.signed.facts[2].d), true);
    const focused = stations([P('A', 'observe', 'observe'), P('B', 'observe', 'observe')],
        [T('A', 'unsigned'), T('B', 'trusted')], { agent: 'A' });
    check('…while a focused agent still says "this one"',
        /this one is not signed yet/.test(focused.signed.facts[2].d), true);
    report('I4 — Rule 3.4: the qualifier expands the anchor, with its unit')();
}

// ── #17 · the route must lead where the DRIVER can be investigated ──────────
// Rule 8.5 says the offered ACT must be the act that is owed; the same logic governs
// the offered ROUTE. A station amber because its execution fell short sent you to the
// posture matrix — a configuration surface that only re-tells you what the
// Configuration row already said.
//
// Scoped to the stations whose execution is TURN-BASED. SIGNED is deliberately exempt:
// its "execution" is an artefact's integrity state, not a rate over turns, and the
// posture matrix's integrity column IS where that is compared — plus its real remedy
// is its state-derived actions (sign / attest / verify), not a route.
{
    const CONFIG_SURFACES = ['posture-matrix', 'posture-tools'];
    // Stations that still carry a per-TURN execution figure. GROUNDED and GUARDED
    // left this list with the lane cut — their execution is now their own mechanisms'
    // acts, not a measurement rate — so the rate grammar (Rule 3.4 unit/anchor, the
    // driver, the route reorder) is exercised where a rate actually renders.
    const TURN_BASED = ['scored', 'trusted'];
    const armed = Object.assign(P('A', 'review', 'review'),
        { groundingConfig: { mode: 'review' } });
    // Execution far below the floor, configuration acting ⇒ driver is execution.
    const failing = {
        turns: 10, failures: 8,
        dimensions: { safety: 0.2, output_sanity: 0.2, groundedness: 0.2,
                      grounded_overlap: 0.2, content_safety: 0.2 },
        dimension_counts: { safety: 10, grounded_overlap: 10 },
    };
    const st = stations([armed], [T('A', 'trusted')], {
        agent: 'A', byAgent: { A: failing },
        trustBehavior: { enabled: true, breaker_mode: 'off',
                         agents: { A: { composite: { state: 'at_risk' }, behavior: { turns: 10 } } } },
    });
    TURN_BASED.forEach((k) => {
        if (st[k].driver !== 'execution') return;   // only the case the rule is about
        check(`${k}: an execution-driven station does not route to a config surface`,
            CONFIG_SURFACES.includes(st[k].cta && st[k].cta.action), false);
        check(`${k}: …it routes somewhere the turns can be inspected`,
            st[k].cta && st[k].cta.action, 'roster');
    });
    // At least one station must actually exercise the case, or the loop above is
    // vacuous — the hollow-assertion failure mode this suite keeps rediscovering.
    check('#17 the fixture produces at least one execution-driven station',
        TURN_BASED.some((k) => st[k].driver === 'execution'), true);

    // A DESIGN-driven station keeps its configuration route: the posture matrix is
    // exactly right when the shortfall is how the control is set.
    const observing = stations([P('B', 'observe', 'observe')], [T('B', 'trusted')], {
        agent: 'B', byAgent: { B: { turns: 10, failures: 0,
            dimensions: { safety: 1, grounded_overlap: 1 }, dimension_counts: { safety: 10 } } } });
    check('#17 a design-driven GROUNDED still routes to the posture matrix',
        observing.grounded.driver === 'design'
        && observing.grounded.cta.action === 'posture-matrix', true);

    // Rule 4b.4 — the driver REORDERS the routes; it never deletes one. Swapping the
    // CTA outright would have removed the configuration route from every
    // execution-driven station, trading one navigation defect for another.
    TURN_BASED.forEach((k) => {
        if (!st[k].cta2) return;
        check(`${k}: the alternate route survives the reorder`,
            st[k].cta.action !== st[k].cta2.action, true);
    });
    // JUDGED's own route pair survives the reorder (Rule 4b.4 — the driver REORDERS
    // routes, it never deletes one). The station-specific posture-matrix assertion it
    // replaces belonged to GROUNDED, which no longer has an execution driver.
    check('#17 an execution-driven JUDGED still reaches both its routes',
        st.scored.driver === 'execution' && !!st.scored.cta
        && (!st.scored.cta2 || st.scored.cta.action !== st.scored.cta2.action), true);
    check('#17 a design-driven GROUNDED still reaches the runs',
        observing.grounded.cta2 && observing.grounded.cta2.action, 'roster');
    // The renderer must actually draw both, or the model's second route is decorative.
    const rsrc = fs.readFileSync(
        path.join(HERE, '..', 'static', 'js', 'handlers', 'trustCircle.js'), 'utf8');
    check('#17 the renderer draws both routes',
        /\[data\.cta, data\.cta2\]\.filter/.test(rsrc), true);
    report('#17 — the route follows the driver, and no route is lost')();
}

// ── #52 · the Assurance Inbox must not print wire tokens or repeat itself ───
// Rule 3.0 bans snake_case reaching a slot, and the gate swept the RING — the inbox
// renderer was never covered, so a parked injection read "prompt-injection detected
// (2 patterns) · prompt_injection": the category once as prose and once as a raw token.
{
    const hsrc = fs.readFileSync(SRC, 'utf8');
    check('#52 the inbox names categories from the published vocabulary',
        /window\.TrustVocab && window\.TrustVocab\.category/.test(hsrc), true);
    check('#52 …and does not hand-roll a second category map',
        /const\s+catLabel\s*=\s*\{/.test(hsrc), false);
    check('#52 the row no longer renders raw categories',
        /r\.categories\.map\(esc\)\.join/.test(hsrc), false);
    // ui.js owns the ONE map and publishes it, because the inbox is a plain script that
    // cannot import a module synchronously.
    const usrc = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'ui.js'), 'utf8');
    check('#52 ui.js declares the single source',
        /export const TRUST_CATEGORY_LABEL = \{/.test(usrc), true);
    check('#52 …and publishes it for plain scripts',
        /window\.TrustVocab = Object\.assign/.test(usrc), true);
    check('#52 …and consumes its own map rather than a literal',
        /const catLabel = TRUST_CATEGORY_LABEL;/.test(usrc), true);
    // The backend reasons feed the same row: no "(s)" may reach it.
    const psrc = fs.readFileSync(path.join(HERE, '..', 'src', 'trusted_data_agent',
        'agent', 'guardrails', 'policy.py'), 'utf8');
    check('#52 guardrail reasons pluralise properly',
        /\(s\)\)"/.test(psrc), false);
    check('#52 …via one helper', /def _n\(count: int, noun: str/.test(psrc), true);
    report('#52 — the inbox speaks the product vocabulary, once')();
}

// ── #44 · the runs ledger reads fields that actually EXIST ──────────────────
// The row's gate note is rendered from `r.gate.*`, whose keys come from
// `eval/gate_outcome.py`. A renderer referencing a key the producer never emits fails
// silently and forever — which is how "turn 1 · deterministic" survived on a turn two
// judges had ruled on. This pins the renderer to the produced vocabulary.
{
    const hsrc = fs.readFileSync(SRC, 'utf8');
    const py = fs.readFileSync(path.join(HERE, '..', 'src', 'trusted_data_agent',
        'eval', 'gate_outcome.py'), 'utf8');
    const produced = new Set(
        [...py.matchAll(/out\["eval\.gate\.(\w+)"\]/g)].map((m) => m[1]));
    check('#44 gate_outcome.py produces a non-empty attribute set', produced.size > 0, true);
    // Every `r.gate.<key>` the ledger reads must be one the producer emits.
    const read = [...hsrc.matchAll(/\br\.gate\.(\w+)\b/g)].map((m) => m[1]);
    const phantom = [...new Set(read)].filter((k) => !produced.has(k));
    check('#44 the ledger reads no phantom gate field', phantom.join(',') || 'none', 'none');
    // …and the gate note is actually wired into the row.
    // Assert the CALL, not the NAME: a bare-name regex also matches the function's own
    // DEFINITION, so deleting the call leaves the gate green. Third recurrence of this
    // shape in this suite (a comment, a docstring, and now a definition) — when pinning
    // a wiring, always match the interpolation that renders it.
    check('#44 the row renders the gate note', /\$\{_runGateNote\(r\)\}/.test(hsrc), true);
    check('#44 …and names the deciding dimensions', /\$\{_runFailedDims\(r\)\}/.test(hsrc), true);
    check('#44 …and no longer calls an adjudicated turn "deterministic" outright',
        /r\.judged \|\| \(r\.gate && r\.gate\.escalated\)/.test(hsrc), true);
    report('#44 — the ledger describes the turn it is explaining')();
}

// ── #24 · a mode is only a protection while its deployment MASTER is on ──────
// With a master off both runtimes short-circuit at their first line, yet
// GROUNDED/GUARDED rendered `observe · 7/7` and claimed measurement — the exact
// falsehood that let a sign-off sail through a deployment the operator believed
// was gated. SCORED has always read its own master from the dashboard payload
// (`dash.enabled`); its siblings simply never got the same treatment.
section('#24 — the master switch reaches the ring');
{
    const OFF = { grounding_gate_enabled: false, guardrails_enabled: false };
    const ON = { grounding_gate_enabled: true, guardrails_enabled: true };
    const dims = { grounded_overlap: 0.75, content_safety: 1.0 };
    const online = { turns: 4, dimensions: dims, dimension_counts: { grounded_overlap: 4, content_safety: 4 } };
    const dash = (masters) => ({
        enabled: true,
        ...(masters ? { deployment_masters: masters } : {}),
        by_agent: { A: { turns: 4, dimensions: dims, dimension_counts: { grounded_overlap: 4, content_safety: 4 } } },
    });

    // 1 · master off + an ACTING mode configured: intent contradicted → warn,
    //     the value is the deployment truth, and the qualifier names the cause.
    const armed = stations([P('A', 'enforce', 'review')], [T('A', 'trusted')],
        { agent: 'A', dashboard: dash(OFF), byAgent: { A: online } });
    for (const key of ['grounded', 'guarded']) {
        const v = armed[key].design;
        check(`#24 ${key}: an acting mode under an off master reads warn`, v.state, 'warn');
        check(`#24 ${key}: …and the value is the deployment truth, not the profile's`, v.text, 'off');
        check(`#24 ${key}: …and the qualifier names the cause`,
            /master switch is off/.test(v.detail || ''), true);
    }

    // 2 · master off + only DEFAULT modes: genuinely not-turned-on → na, never alarm.
    const idle = stations([P('A', 'observe', 'observe')], [T('A', 'trusted')],
        { agent: 'A', dashboard: dash(OFF), byAgent: { A: online } });
    check('#24 grounded: default modes under an off master read na', idle.grounded.design.state, 'na');
    check('#24 grounded: …with the master named', idle.grounded.design.detail, 'master switch off');
    check('#24 guarded: default modes under an off master read na', idle.guarded.design.state, 'na');

    // 3 · REVIEWED — a review-mode gate cannot park while its master is off, and the
    //     inert reason must be named ("no gate is in review mode" would be false).
    const rev = stations([P('A', 'review', 'observe')], [T('A', 'trusted')],
        { agent: 'A', dashboard: dash(OFF) });
    check('#24 reviewed: a muted review gate cannot park', rev.reviewed.design.text, 'cannot park');
    check('#24 reviewed: …and the cause is the master, not the mode',
        /master switch is off/.test(rev.reviewed.design.detail), true);
    check('#24 reviewed: …never the false "no gate is in review mode"',
        /no gate is in review mode/.test(rev.reviewed.design.detail), false);
    const revOn = stations([P('A', 'review', 'observe')], [T('A', 'trusted')],
        { agent: 'A', dashboard: dash(ON) });
    check('#24 reviewed: the same config parks once the master is on',
        revOn.reviewed.design.text, 'can park');

    // 4 · REVIEWED — a granted signed action in REVIEW posture parks writes for a person, so
    //     an agent whose gates are all observe still CAN park (the station read "cannot park"
    //     on an agent that had parked five writes that day). The server says which actions
    //     park (`actionsParking`); the station names them and never re-derives it.
    const actOnly = { ...P('A', 'observe', 'observe'), actionsParking: ['close_lead'] };
    const revAct = stations([actOnly], [T('A', 'trusted')], { agent: 'A', dashboard: dash(ON) });
    check('#69 reviewed: a review-posture action alone means the agent can park',
        revAct.reviewed.design.text, 'can park');
    check('#69 reviewed: …the configuration reads pass, not na', revAct.reviewed.design.state, 'pass');
    check('#69 reviewed: …and the detail names the parking action',
        /close_lead/.test(revAct.reviewed.design.detail || ''), true);
    const revNone = stations([P('A', 'observe', 'observe')], [T('A', 'trusted')], { agent: 'A', dashboard: dash(ON) });
    check('#69 reviewed: without it the agent still cannot park', revNone.reviewed.design.text, 'cannot park');

    // 4 · absence tolerance — an older backend omits the block entirely; null means
    //     UNKNOWN and must degrade to the per-profile reading, never a claimed "off".
    const absent = stations([P('A', 'enforce', 'enforce')], [T('A', 'trusted')],
        { agent: 'A', dashboard: dash(null), byAgent: { A: online } });
    const present = stations([P('A', 'enforce', 'enforce')], [T('A', 'trusted')],
        { agent: 'A', dashboard: dash(ON), byAgent: { A: online } });
    for (const key of ['grounded', 'guarded', 'reviewed']) {
        check(`#24 ${key}: masters absent ≡ masters on (regression)`,
            JSON.stringify(absent[key]), JSON.stringify(present[key]));
    }

    // 5 · estate scope under an off master: the armed count is named, and worst-of
    //     still governs (one armed agent must not silence the reading).
    const estate = stations([P('A', 'enforce', 'observe'), P('B', 'observe', 'observe')],
        [T('A', 'trusted'), T('B', 'trusted')], { dashboard: dash(OFF) });
    check('#24 estate grounded: value is the deployment truth', estate.grounded.design.text, 'off');
    check('#24 estate grounded: the armed count is named',
        /1 configured to act — the master switch is off/.test(estate.grounded.design.detail), true);
    check('#24 estate grounded: configured-but-inert reads warn', estate.grounded.design.state, 'warn');

    // 6 · the ONE source — the payload key the ring reads is produced by the shared
    //     backend helper, on the SAME endpoint the ring already loads.
    const rsrc = fs.readFileSync(path.join(HERE, '..', 'src', 'trusted_data_agent',
        'api', 'rest_routes.py'), 'utf8');
    check('#24 backend: one helper defines the masters',
        (rsrc.match(/def _deployment_masters\(/g) || []).length, 1);
    check('#24 backend: the dashboard payload carries it',
        rsrc.includes('"deployment_masters": _deployment_masters()'), true);
    check('#24 backend: the audit pack reuses it, never a second dict',
        rsrc.includes('{**_deployment_masters(), "trust_behavior": cfg}'), true);
    check('#24 backend: no other inline masters dict survives',
        (rsrc.match(/"grounding_gate_enabled": bool\(getattr/g) || []).length, 1);

    report('#24 — a reported mode is a mode that is actually running')();
}

// ── #62 · a figure carries the denominator it was measured on ────────────────
// `_worstDimRate` returned only min(rate), so the stations projected it onto
// `online.turns`: a judge dimension sampled ONCE and failed rendered `0/18` — while
// `_dimShortfall`, on the same panel, correctly said `0/1`. Live-observed on @UDERIA
// (groundedness 0.0 · n=1 · 18 turns). The value must never contradict its own
// qualifier, and seventeen unjudged turns must never be claimed unclean.
section('#62 — the execution figure and its own denominator');
{
    const mk = (dims, counts, turns = 4) => stations(
        [P('A', 'enforce', 'enforce')], [T('A', 'trusted')],
        { agent: 'A', dashboard: { enabled: true, by_agent: { A: { turns } } },
          byAgent: { A: { turns, dimensions: dims, dimension_counts: counts } } });

    // 1 · a sampled judge dim fails: the figure is ITS k/n, not rate × turns.
    const sampled = mk({ grounded_overlap: 0.75, groundedness: 0.0 },
                       { grounded_overlap: 4, groundedness: 1 });
    check('#62 JUDGED: the value is the worst dim over its own sample',
        sampled.scored.value.endsWith(' · 0%')
        && /^0\/1 — /.test(sampled.scored.execution.detail), true);
    check('#62 JUDGED: …never projected onto unjudged turns',
        /^0\/4 — /.test(sampled.scored.execution.detail), false);
    check('#62 JUDGED: the unit says the sample is partial',
        /0 of 1 scored turn/.test(sampled.scored.execution.detail), true);
    check('#62 JUDGED: the qualifier agrees with the value',
        /Groundedness \(RAG\) 0\/1 passed/.test(sampled.scored.execution.detail), true);

    // 2 · the worst dim measured on every turn: reading unchanged (regression).
    const full = mk({ grounded_overlap: 0.25, groundedness: 1.0 },
                    { grounded_overlap: 4, groundedness: 1 });
    check('#62 JUDGED: a full-sample worst dim keeps the turn denominator',
        full.scored.value.endsWith(' · 25%')
        && /^1\/4 — /.test(full.scored.execution.detail), true);
    check('#62 JUDGED: …and the plain unit', /1 of 4 turns/.test(full.scored.execution.detail), true);

    // 3 · older backend without dimension_counts: fall back to turns — the exact
    //     pre-fix reading, never a crash, never an invented sample size.
    const noCounts = mk({ grounded_overlap: 0.75 }, undefined);
    check('#62 JUDGED: absent counts degrade to the turn denominator',
        noCounts.scored.value.endsWith(' · 75%')
        && /^3\/4 — /.test(noCounts.scored.execution.detail), true);

    // 4 · a tie prefers the larger sample — more evidence decides the same rate.
    const tie = mk({ grounded_overlap: 0.5, groundedness: 0.5 },
                   { grounded_overlap: 4, groundedness: 1 });
    check('#62 JUDGED: equal rates resolve to the better-evidenced dim',
        tie.scored.value.endsWith(' · 50%')
        && /^2\/4 — /.test(tie.scored.execution.detail), true);

    // 5 · the same arithmetic on the safety checks. They used to render at GUARDED;
    //     after the lane cut every measurement renders here, and the rule is the rule.
    const guard = mk({ content_safety: 0.0, safety: 1.0, output_sanity: 1.0 },
                     { content_safety: 1, safety: 4, output_sanity: 4 });
    check('#62 JUDGED (safety dims): same denominator rule',
        guard.scored.value.endsWith(' · 0%')
        && /^0\/1 — /.test(guard.scored.execution.detail), true);
    check('#62 JUDGED (safety dims): …with the partial-sample unit',
        /0 of 1 scored turn/.test(guard.scored.execution.detail), true);

    // 6 · #62b — with the per-turn record present, the figure is the UNION over the
    //     stable turn denominator: "x of N turns clean", where a turn is clean when
    //     no check that RAN on it failed. This is the full-window reading the
    //     fallback above cannot compute (the operator's own question: "why doesn't
    //     y show the full amount of turns?").
    const mkU = (turn_failed_dims, dims, counts, turns = 4) => stations(
        [P('A', 'enforce', 'enforce')], [T('A', 'trusted')],
        { agent: 'A', dashboard: { enabled: true, by_agent: { A: { turns } } },
          byAgent: { A: { turns, dimensions: dims, dimension_counts: counts, turn_failed_dims } } });

    // The live @UDERIA shape in miniature: the judge failed on ONE turn, the overlap
    // check on ANOTHER — the union is 2 failed of 4, never 0/1 and never 0/4.
    const union = mkU([['groundedness'], [], [], ['grounded_overlap']],
                      { grounded_overlap: 0.75, groundedness: 0.0 },
                      { grounded_overlap: 4, groundedness: 1 });
    check('#62b JUDGED: the union over the full window',
        union.scored.value.endsWith(' · 50%')
        && /^2\/4 — /.test(union.scored.execution.detail), true);
    check('#62b JUDGED: …with the stable turn unit',
        /2 of 4 turns clean on every check measured/.test(union.scored.execution.detail), true);
    check('#62b JUDGED: an abstaining judge never counts against a turn',
        /^0\/4 — /.test(union.scored.execution.detail), false);
    // A turn failing a check OUTSIDE this station's family does not dirty it here.
    // (producer-faithful: a dim that failed on a turn ALWAYS has an aggregate too —
    // the #66 vacuous-clean guard rejects a family with no measured dim at all)
    const other = mkU([['content_safety'], [], [], []],
                      { grounded_overlap: 1.0, content_safety: 0.75 },
                      { grounded_overlap: 4, content_safety: 4 });
    // Under the lane cut every measurement counts at JUDGED, so a safety failure and
    // a grounding failure land in the SAME union — which is the point: they are both
    // measurements. What must not leak is the reverse direction, asserted above: a
    // measurement never colours a guarantee station.
    check('#62b JUDGED: a safety failure counts here, with everything else measured',
        other.scored.value.endsWith(' · 75%')
        && /^3\/4 — /.test(other.scored.execution.detail), true);
    check('#62b: …and it still does not colour GUARDED',
        other.guarded.execution.state !== 'danger', true);
    // A capped window is disclosed, never passed off as the whole.
    const capped = mkU([[], [], ['groundedness']], { groundedness: 0.5 }, { groundedness: 2 }, 9);
    check('#62b JUDGED: a trailing window names itself',
        /the trailing 3 of 9 turns/.test(capped.scored.execution.detail), true);

    // 7 · #63 — the corroboration reading must come from the aligned record. The
    //     per-dim series do NOT align by index: zipping a 1-entry judge series with a
    //     4-entry overlap series compared different turns and could invent a
    //     corroborated failure that never happened.
    const misB = stations([P('A', 'enforce', 'enforce')], [T('A', 'trusted')],
        { agent: 'A', dashboard: { enabled: true, by_agent: { A: { turns: 4 } } },
          byAgent: { A: { turns: 4,
              dimensions: { grounded_overlap: 0.75, groundedness: 0.0 },
              dimension_counts: { grounded_overlap: 4, groundedness: 1 },
              dimension_series: { grounded_overlap: [0, 1, 1, 1], groundedness: [0] } } } });
    check('#63 grounded: a sampled series is never zipped in at index 0',
        /fell short on more than one check/.test(misB.grounded.facts[3].d), false);
    check('#63 grounded: the aligned dims still report their instances',
        /1 on Grounding Overlap only/.test(misB.grounded.facts[3].d), true);
    // With the aligned record, a REAL corroborated turn is still named.
    const corr = stations([P('A', 'enforce', 'enforce')], [T('A', 'trusted')],
        { agent: 'A', dashboard: { enabled: true, by_agent: { A: { turns: 4 } } },
          byAgent: { A: { turns: 4,
              dimensions: { grounded_overlap: 0.75, groundedness: 0.0 },
              dimension_counts: { grounded_overlap: 4, groundedness: 1 },
              turn_failed_dims: [['grounded_overlap', 'groundedness'], [], [], []] } } });
    check('#63 grounded: a genuine corroborated turn is reported',
        /1 turn fell short on more than one check/.test(corr.grounded.facts[3].d), true);

    report('#62 — no figure without its own denominator')();
}

// ── #26/#27/#32 · SIGNED — one regime for Why AND Evidence, and no false walls ─
// #26: Why said "nothing is owed" (from signingInUse) while Evidence directly below
// said "sign-off blocked by …" (from blockers) — two derivations, never reconciled.
// #27: "blocked by" was false in every regime — the guided flow SIGNS unsigned
// ingredients first; only a validator refusal at sign time actually blocks.
// #32: the stored caveat named exactly what other stations were amber about
// ("every answer gate is in observe mode") and nothing joined the dots.
section('#26/#27/#32 — SIGNED speaks one truth');
{
    const blocking = [{ kind: 'knowledge', artifact_id: '6', label: 'Uderia Documentation' }];
    // Regime A: nothing signed anywhere — signing is NOT in use.
    const idle = stations([P('A', 'observe', 'observe')],
        [{ tag: 'A', state: 'unsigned', ingredients: [], blocking }], { agent: 'A' });
    const sIdle = idle.signed;
    check('#26 idle: Evidence agrees with "nothing is owed"',
        /nothing is owed — a first sign-off would begin with/.test(sIdle.facts[3].d), true);
    check('#26 idle: Why still says nothing is owed',
        /nothing is owed/.test(sIdle.facts[2].d), true);
    check('#27 idle: the false wall is gone', /blocked by/.test(sIdle.facts[3].d), false);
    check('#27 idle: the blocker is still NAMED',
        /Uderia Documentation/.test(sIdle.facts[3].d), true);

    // Regime B: signing IS in use (another agent is signed) — the same blocker now
    // reads as the order of operations, never a wall.
    const inUse = stations([P('A', 'observe', 'observe'), P('B', 'observe', 'observe')],
        [{ tag: 'A', state: 'unsigned', ingredients: [], blocking },
         T('B', 'trusted')], { agent: 'A' });
    check('#27 in-use: sign-off describes the order of operations',
        /sign-off will first sign/.test(inUse.signed.facts[3].d), true);
    check('#26 in-use: no "nothing is owed" while work is outstanding',
        /nothing is owed/.test(inUse.signed.facts[3].d), false);
    check('#27 the word "blocked" never renders on SIGNED',
        inUse.signed.facts.some((f) => /blocked/.test(String(f.d || f.v))), false);

    // #32: a partial signature's caveat reaches Evidence, joined to the stations it
    // explains — keyed on the machine-readable status, never prose-matching.
    const cavAgent = { tag: 'A', state: 'trusted', ingredients: [], blocking: [],
        caveat: { band: 'partial', status: 'observe_only',
                  note: 'every answer gate is in observe mode' },
        ingredient_caveats: [{ kind: 'knowledge', artifact_id: '7', label: 'Docs',
                               band: 'partial', status: 'integrity-only',
                               note: 'content not fully scanned for injection' }] };
    const cav = stations([P('A', 'observe', 'observe')], [cavAgent], { agent: 'A' });
    check('#32 the agent\'s own caveat reaches Evidence',
        /signed with a caveat — every answer gate is in observe mode/.test(cav.signed.facts[3].d), true);
    check('#32 …joined to the stations it explains',
        /the reading behind GROUNDED and GUARDED/.test(cav.signed.facts[3].d), true);
    check('#32 an ingredient caveat is named with its artefact',
        /“Docs” signed with a caveat — content not fully scanned/.test(cav.signed.facts[3].d), true);
    // A non-gate caveat must NOT claim the gate cross-reference.
    const cav2 = stations([P('A', 'observe', 'observe')],
        [{ ...cavAgent, caveat: { band: 'partial', status: 'integrity-only', note: 'x' },
           ingredient_caveats: null }], { agent: 'A' });
    check('#32 a non-gate caveat gets no gate cross-reference',
        /the reading behind GROUNDED/.test(cav2.signed.facts[3].d), false);

    // #31: skills and ontologies are routable blockers now (the actions themselves are
    // gated on the TrustBadge package, absent under Node — so the ROUTE is pinned at
    // the source: the set and the branch that serves it).
    const hsrc = fs.readFileSync(SRC, 'utf8');
    check('#31 the routable set includes skill and ontology',
        /const ROUTABLE = \{ knowledge: 1, planner: 1, skill: 1, ontology: 1 \};/.test(hsrc), true);
    check('#31 …and the ingredient opener serves them',
        /kind === 'skill' \|\| kind === 'ontology'/.test(hsrc), true);
    check('#31 …via the artefact trust surface', /TB\.openArtifactTrust\(kind, id,/.test(hsrc), true);

    // #28/#29: disclosures in the act flow (module code outside the extracted body —
    // pinned on the executed strings and their ORDER: the pre-act facts must be read
    // BEFORE the act that changes them).
    check('#28 signing announces the two-step journey up front',
        hsrc.includes("'Signing — step 1 of 2 (this deployment then attests its own half)…'"), true);
    const iFirst = hsrc.indexOf('const _firstSign = !(_trustAgentsFull || []).some');
    const iAct = hsrc.indexOf('await TB.signAgentGuided(rec.profile_id');
    check('#29 the pre-act estate facts are captured before the act',
        0 <= iFirst && iFirst < iAct, true);
    check('#29 the first signature discloses the estate re-grade',
        hsrc.includes('now read as outstanding work rather than not turned on'), true);
    check('#29 …appended to the outcome the signer actually reads',
        hsrc.includes('_base + _signCaveatNote(r.caveats) + _estateNote'), true);

    // #25: the profile editor's posture cards tell the truth about an off master.
    const csrc = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'handlers',
        'configurationHandler.js'), 'utf8');
    check('#25 the editor applies the master banners', /_applyMasterBanners\(modal\);/.test(csrc), true);
    check('#25 unknown masters render nothing (older backend)',
        csrc.includes('if (!masters) return;'), true);
    // (These two were greps pinning the FIRST implementation — `radio.closest`
    // and the old chip wording — and broke when the master-alignment assessment
    // widened coverage + renamed the state to "deactivated". The properties are
    // now EXECUTED by test_master_alignment.mjs; here we pin only the anchors.)
    check('#25 the card is found from its own control, not a heading',
        /const card = ctl && ctl\.closest\('details'\)/.test(csrc), true);
    check('#25 the collapsed state is honest too (chip beside the heading)',
        csrc.includes("chip.textContent = 'deactivated — master off';"), true);
    check('#25 banners are idempotent across re-opens',
        csrc.includes('if (oldB) oldB.remove();'), true);

    report('#26/#27/#28/#29/#31/#32/#25 — the SIGNED cluster speaks one truth')();
}

// ── solo-sweep findings #64–#66/#68 ──────────────────────────────────────────
// #64 is asserted inside the grammar invariants (the internal `design` token never
// leaks — every fixture, every station). The rest here.
section('#64–#66/#68 — solo class-sweep findings');
{
    // #66 — a family with NO measured dim must not read vacuously clean: the
    // producer-aligned record exists, but nothing of this family ever ran.
    const vac = stations([P('A', 'enforce', 'enforce')], [T('A', 'trusted')],
        { agent: 'A', dashboard: { enabled: true, by_agent: { A: { turns: 3 } } },
          byAgent: { A: { turns: 3, dimensions: { completed: 1.0 },
                          dimension_counts: { completed: 3 },
                          turn_failed_dims: [[], [], []] } } });
    check('#66 grounded: no family dim ever measured ⇒ no vacuous 100%',
        vac.grounded.value.endsWith(' · —'), true);
    check('#66 guarded: same', vac.guarded.value.endsWith(' · —'), true);

    const hsrc = fs.readFileSync(SRC, 'utf8');
    // #65 — TRUSTED-off offers the act that is owed (the admin card) where the
    // caller can perform it, and the prose names the route that actually exists.
    check('#65 TRUSTED-off routes an admin to the evaluation card',
        hsrc.includes('cta: _masterCta || { label: \'Open the agent roster\', action: \'roster\' },'), true);
    check('#65 …and the prose names the real route (the Trust panel)',
        hsrc.includes('Administration → App Config → Trust → the TRUSTED station'), true);
    check('#65 the wrong routes are gone',
        hsrc.includes("evidence: 'enable it under Configuration → Operations")
        || hsrc.includes('Administration → App Config → Operations → Agent Evaluation'), false);
    // The admin-eval act drives the TRUST sub-tab (the station-partitioned panel),
    // and that panel actually exists with its per-station slider hosts — the route
    // and its destination are asserted together so neither can move alone.
    check('#65 the admin-eval act opens the Trust sub-tab',
        /admin-eval[\s\S]{0,900}data-config-tab="trust"/.test(hsrc), true);
    const idxSrc = fs.readFileSync(path.join(HERE, '..', 'templates', 'index.html'), 'utf8');
    check('#65 the Trust sub-tab exists in the admin nav',
        /data-config-tab="trust"/.test(idxSrc) && /id="config-panel-trust"/.test(idxSrc), true);
    // The Trust panel hosts one slider block per STAGE of the turn, in turn order —
    // sliders tune MEASUREMENTS, and a measurement's home is its stage. Three station
    // hosts held 7/2/9 of the 18 tunable thresholds; five stage hosts hold 3/2/6/4/3.
    const _stageHosts = [...idxSrc.matchAll(/data-thresh-stage="([a-z_]+)"/g)].map((m) => m[1]);
    for (const st of ['plan', 'retrieve', 'act', 'compose', 'the_run']) {
        check(`#65 the Trust panel hosts the ${st} sliders`,
            _stageHosts.includes(st), true);
    }
    check('#65 no station-keyed slider host survives',
        /data-thresh-station=/.test(idxSrc), false);
    check('#65 every host is a declared stage',
        _stageHosts.every((st) => STAGE_MODEL.order.includes(st)), true);
    // …and they are ordered as the TURN runs, not as they happened to be placed.
    check('#65 the hosts render in turn order',
        _stageHosts.join(','),
        STAGE_MODEL.order.filter((st) => _stageHosts.includes(st)).join(','));

    // A THRESHOLD TUNES A MEASUREMENT, and measurement is counted in ONE place — so
    // every threshold lives in ONE section, grouped by moment, in turn order.
    //
    // The panel used the STATION as the organising unit for four different kinds of
    // setting and fitted only one of them: five stations held a single dropdown each in
    // their own bordered card, beside one holding eighteen sliders, with the panel's
    // Save button stranded inside PROVEN. It is now cut by WHAT YOU CONFIGURE —
    // Scoring · Quality bands · Enforcement · What reaches you — in setup order.
    {
        const panel = idxSrc.slice(idxSrc.indexOf('id="config-panel-trust"'));
        const panelEnd = panel.indexOf('id="config-panel-', 10);
        const p = panelEnd > -1 ? panel.slice(0, panelEnd) : panel;
        const heads = [...p.matchAll(/<h3[^>]*>([^<]+)</g)].map((m) => m[1].trim());
        // PROP-003 adds a fifth block, and its placement CHANGED once the panel became
        // an accordion. It was last and below the Save row, which made it read as a
        // separate panel rather than the fifth thing you configure. It now sits in the
        // sequence — what does the assessing that Quality bands and Enforcement both
        // consume — and above the shared Save with the rest.
        check('#65 the panel is cut by what you configure, in setup order',
            heads.join(' · '),
            'Scoring · Quality bands · Enforcement · Assessment modules · What reaches you');
        check('#65 the binding studio is IN the sequence, not stranded after the save row',
            p.indexOf('assurance-bindings-host') < p.indexOf('save-agent-eval-btn'), true);
        // FLIPPED, as the note said it should be once built. One save model in one column:
        // a change here is CHOSEN on confirm and applied by the panel's Save, like every
        // other control above it. Each is still its own request and its own audit event on
        // apply — staging changed when they are written, never how.
        const bindSrc = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'handlers', 'assuranceBindings.js'), 'utf8');
        check('#65 …and its changes are STAGED, not applied on confirm',
            /_staged\.set\(/.test(bindSrc) && /function _apply\(prop, lane, key, act\) \{\s*_stage\(/.test(bindSrc), true);
        check('#65 …applied by the panel Save, which owns them',
            /AssuranceBindings[\s\S]{0,400}applyStaged\(\)/.test(
                fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'adminManager.js'), 'utf8')), true);
        check('#65 the per-station cards are gone',
            /class="rounded-lg p-4"/.test(p), false);
        // Every threshold host in ONE section — the bands section — and nowhere else.
        const bandsAt = p.indexOf('>Quality bands<');
        const enfAt = p.indexOf('>Enforcement<');
        const hostIdx = [...p.matchAll(/data-thresh-stage="([a-z_]+)"/g)].map((m) => m.index);
        check('#65 every threshold host lives in the Quality bands section',
            hostIdx.length === _stageHosts.length
            && hostIdx.every((x) => x > bandsAt && x < enfAt), true);
        check('#65 …and the section names where measurement is counted',
            /counted at <strong>JUDGED<\/strong>/.test(p), true);
        check('#65 …and that the grouping is by moment',
            /<strong>moment<\/strong> of the turn/.test(p), true);
        // The presets belong with the bands they preset, not floating elsewhere.
        const presetAt = p.indexOf("applyPreset('strict')");
        check('#65 the preset bar sits with the bands',
            presetAt > bandsAt && presetAt < enfAt, true);
        // The panel-level Save is a panel action, not a station's.
        const saveAt = p.indexOf('id="save-agent-eval-btn"');
        check('#65 Save is at the panel foot, after every section',
            saveAt > p.indexOf('>What reaches you<'), true);
        // Reporting is ONE setting eight times — a grid of rows, not eight cards.
        check('#65 the eight reporting levels render as one grid',
            /class="trust-report-grid"/.test(p)
            && (p.match(/class="trust-report-row"/g) || []).length === 8, true);
    }
    // The slider grouping derives from the PARTITION (stationOfThreshold), never a
    // hand-copied station list — the seam that lets dimension→station assignment
    // become dynamic later with no admin-page rework.
    const thrSrc = fs.readFileSync(
        path.join(HERE, '..', 'static', 'js', 'handlers', 'thresholdSliders.js'), 'utf8');
    check('#65 sliders group by the partition seam',
        /stageOfThreshold/.test(thrSrc) && /data-thresh-stage/.test(thrSrc), true);

    // ── the roster reads the SAME process model ────────────────────────────────
    // Detail bars, the runs-ledger verdict chips, the per-run band strip and the compare
    // heatmap all group/order by `_dimStageOf` → the SERVED model — never through a
    // second hand-rolled taxonomy (the defect class the one-source table exists for).
    check('roster: one stage resolver, reading the SERVED model',
        /_dimStageOf\(d\)[\s\S]{0,300}stageOf/.test(hsrc), true);
    check('roster: it declares no taxonomy of its own',
        /const\s+STAGE_DIMENSIONS/.test(hsrc) || /const\s+TIER\s*=/.test(hsrc), false);
    check('roster: detail bars group by stage',
        /_stageGroupsOf\(Object\.keys\(dims\)\)/.test(hsrc), true);
    check('roster: the group header chip is NOT sourced from a station reading',
        /_stageHeadChip[\s\S]{0,1400}_turnCleanRate/.test(hsrc), true);
    check('roster: …and says so — a stage has no ring segment to agree with',
        /NOT a station reading/.test(hsrc), true);
    check('roster: run verdict chips group by stage',
        /const stGroups = \{\};\s*\n\s*verdicts\.forEach\(\(v\) => \{ const st = _dimStageOf/.test(hsrc), true);
    check('roster: the band strip carries separators, stage-ordered',
        /ap-strip-sep/.test(hsrc) && /function dimStrip[\s\S]*?_dimStageOf/.test(hsrc), true);
    check('roster: the compare heatmap orders columns in TURN order',
        /\.\.\.dimSet\]\.sort[\s\S]{0,200}_stageIdxOf/.test(hsrc), true);
    // The retired station route is replaced by an in-place explanation, not dropped —
    // a stage has no Guide entry to route to (see the inventory ledger).
    check('roster: the stage header explains itself in place',
        /_stageDescOf\(st\)/.test(hsrc), true);
    check('roster: per-check Guide routes survive untouched',
        /data-dim="\$\{/.test(hsrc), true);
    // #68 — singular grammar on the SCORED headline.
    check('#68 one scored turn reads singular',
        hsrc.includes("'The 1 scored turn came through clean on every check measured.'"), true);
    report('#64–#66/#68 — sweep findings closed')();
}

// ── gate hardening (deep-assessment TC-50..57 + TC-8) ───────────────────────
section('gate hardening — one vocabulary, real labels, renderer surfaces, one subject');
{
    const done = report('TC-50/51/52/53/54/55/57 + TC-8 — former blind spots now measured');
    const hsrc = fs.readFileSync(SRC, 'utf8');
    const RSRC_PATH = path.join(HERE, '..', 'static', 'js', 'handlers', 'trustCircle.js');
    const rsrc = fs.readFileSync(RSRC_PATH, 'utf8');

    // TC-52 — the extractor's anchors must be UNIQUE, or a stray comment containing the
    // anchor string would silently mis-slice the extraction (first-occurrence match).
    for (const [label, a] of [
        ['handler function anchor', 'function _cfgMode(cfg, dflt)'],
        ['handler return anchor', 'return [signed, grounded, guarded, bounded, scored, trusted, reviewed, proven]'],
        ['TRUST_STATE_LABEL anchor', 'const TRUST_STATE_LABEL = {'],
    ]) {
        check(`TC-52 ${label} appears exactly once in source`, hsrc.split(a).length - 1, 1);
    }

    // TC-57 — the renderer's own STATIONS registry is a SECOND 8-station list; a key typo
    // there degraded silently to the `{state:'na'}` fallback. Assert it equals STATION_KEYS
    // in canonical order.
    const stIdx = rsrc.indexOf('const STATIONS = ');
    const stBlock = rsrc.slice(stIdx, rsrc.indexOf('];', stIdx));
    const rKeys = [...stBlock.matchAll(/key:\s*'([a-z]+)'/g)].map((m) => m[1]);
    check('TC-57 renderer STATIONS keys === STATION_KEYS, in order',
        rKeys.join(','), STATION_KEYS.join(','));

    // TC-50 — ONE attention vocabulary, and since PROP-002 its canonical home is
    // trustGrammar.js. The handler and the renderer READ it (absence-tolerant
    // fallback literals gate-pinned EQUAL to the canonical), and the legend + the
    // routing summary's hardcoded phrases must agree with it — the "five
    // hand-rolled ladders" class, on the words themselves.
    const parseMap = (m) => { const o = {}; [...m.matchAll(/(\w+):\s*'([^']*)'/g)].forEach((x) => { o[x[1]] = x[2]; }); return o; };
    const gsrc = fs.readFileSync(TG_SRC, 'utf8');
    const canon = parseMap((gsrc.match(/const ATTN_WORD = \{([^}]*)\}/) || [, ''])[1]);
    check('TC-50 the canonical map is extractable from trustGrammar.js',
        Object.keys(canon).length, 4);
    check('TC-50 the loaded module serves the same words as its source literal',
        JSON.stringify(TG.ATTN_WORD), JSON.stringify(canon));
    // Consumers: primary path reads TrustGrammar; the fallback literal is pinned
    // equal to the canonical so absence can never change a word.
    // The literal is the LAST `{…}` of the declaration (after `||` or the ternary's `:`).
    const hAttn = parseMap((hsrc.match(/const _ATTN_WORD = [^;]*(?:\|\||:)\s*\{([^}]*)\}/) || [, ''])[1]);
    const rChip = parseMap((rsrc.match(/const stateWord = [^;]*(?:\|\||:)\s*\{([^}]*)\}/) || [, ''])[1]);
    check('TC-50 handler reads TrustGrammar first',
        /const _ATTN_WORD = \([^)]*TrustGrammar/.test(hsrc), true);
    check('TC-50 renderer reads TrustGrammar first',
        /const stateWord = \(globalThis\.TrustGrammar/.test(rsrc), true);
    for (const k of ['pass', 'warn', 'danger', 'na']) {
        check(`TC-50 '${k}' fallback agrees canonical↔handler`, hAttn[k], canon[k]);
        check(`TC-50 '${k}' fallback agrees canonical↔renderer`, rChip[k], canon[k]);
        check(`TC-50 legend carries the '${k}' word verbatim`,
            rsrc.includes(`tc-leg-${k}"></span>${canon[k]}`), true);
    }
    check('TC-50 routing summary hardcodes the danger word', rsrc.includes(`${canon.danger}</strong>`), true);
    check('TC-50 routing summary hardcodes the warn word', rsrc.includes(`${canon.warn} — `), true);
    // PROP-002 — the legend names ALL rendered states (Rule 3b.7): the absence
    // family's other two members were rendered by the band/dial/mini and absent
    // from every legend.
    check('TC-50 legend carries the pending word',
        rsrc.includes(`tc-leg-pending"></span>${TG.ABSENCE_WORD.pending}`), true);
    check('TC-50 legend carries the unknown word',
        rsrc.includes(`tc-leg-unknown"></span>${TG.ABSENCE_WORD.unknown}`), true);
    check('W1 legend carries the watching word, and says it stops nothing',
        rsrc.includes(`tc-leg-watching"></span>${TG.ABSENCE_WORD.watching}`) && /stops nothing/.test(rsrc), true);
    check('W1 handler + renderer fallbacks carry the watching word',
        hAttn.watching === TG.ABSENCE_WORD.watching && rChip.watching === TG.ABSENCE_WORD.watching, true);

    // TC-50b (P3) — the ring still EXPORTS the vocabulary (older consumers read
    // TrustCircle.stateWord), and there is exactly ONE fallback literal.
    check('TC-50b the attention vocabulary is exported on TrustCircle',
        /window\.TrustCircle = \{[^}]*\bstateWord\b/s.test(rsrc), true);
    check('TC-50b the attention-word LOOKUP is exported too',
        /window\.TrustCircle = \{[^}]*\battentionWord\b/s.test(rsrc), true);
    check('TC-50b stateWord is module-scope, not trapped inside a render function',
        /^\s{4}const stateWord = \(globalThis\.TrustGrammar/m.test(rsrc), true);
    check('TC-50b exactly one fallback literal (no second hand-rolled copy)',
        (rsrc.match(/const stateWord = \(globalThis/g) || []).length, 1);
    // Execute the REAL declaration + lookup with TrustGrammar loaded — the
    // primary path, not the fallback. An unknown state must yield '' — a wrong
    // triage word is worse than none.
    const attnFn = new Function(`${rsrc.match(/const stateWord = \(globalThis[\s\S]*?\};/)[0]}
        ${rsrc.match(/function attentionWord\(state\) \{[^}]*\}/)[0]}
        return attentionWord;`)();
    for (const k of ['pass', 'warn', 'danger', 'na']) {
        check(`TC-50b exported lookup returns the '${k}' word`, attnFn(k), canon[k]);
    }
    check('TC-50b an unknown state yields no word rather than a guess', attnFn('bogus'), '');

    // TC-51 — dimension names come from the ONE QD vocabulary (the gate loads the real
    // qualityDimensions.js; see the harness header). A key must have exactly one name.
    // A floor, not an exact count: the vocabulary GROWS with every scorer (58 → 59 with
    // cache_efficiency), and this pin exists to prove the REAL module loaded, not to
    // re-count the registry (test_assurance_points pins registry ↔ JS equality).
    check('TC-51 QD is genuinely loaded (≥58 dimensions)', Object.keys(QD.DIM || {}).length >= 58, true);
    const dimFix = stations([P('A', 'enforce', 'enforce')], [], {
        agent: 'A',
        byAgent: { A: { turns: 4, dimensions: { grounded_overlap: 0.5 }, dimension_counts: { grounded_overlap: 4 } } },
        dashboard: { enabled: true },
    });
    const dimTxt = JSON.stringify(dimFix.grounded);
    check('TC-51 the real label renders (Grounding Overlap)', /Grounding Overlap/.test(dimTxt), true);
    check('TC-51 the fallback name never appears beside it', /Grounded Overlap/.test(dimTxt), false);

    // TC-53 — PROVEN at exactly ONE sealed verdict: `1 sealed`, never a bare `1`
    // (a dressed constant, Rule 1.3).
    const oneSealed = stations([P('A', 'observe', 'observe')], [], {
        agent: 'A', byAgent: { A: { turns: 1 } }, dashboard: { enabled: true } });
    check('TC-53 PROVEN(1) value carries its unit', oneSealed.proven.value.includes('1 sealed'), true);
    check('TC-53 PROVEN(1) is not a bare count', /(^|· )1( ·|$)/.test(oneSealed.proven.value), false);

    // TC-54 (Rule 5.4) — an estate amber NAMES the laggards; the rule was stated but had
    // zero assertions (only the master-off variant was gated).
    const lagSt = stations([P('A', 'enforce', 'enforce'), P('B', 'observe', 'observe'),
                            P('C', 'observe', 'observe')], []);
    const lagEv = (lagSt.grounded.facts[3] || {}).d || '';
    check('TC-54 estate GROUNDED names who is not acting', /not acting: /.test(lagEv), true);
    check('TC-54 the laggard list names @B', /@B/.test(lagEv), true);
    check('TC-54 the acting agent is not in the laggard list', /not acting: [^·]*@A/.test(lagEv), false);

    // TC-54 (Rule 3.3) — no doctrine in any hover slot. The banned phrases are the ones
    // the contract removed; a station carrying them again would be marketing, not a reading.
    const DOCTRINE = [/graded in code first/i, /51 dimensions/i, /receipts export from every capsule/i];
    const doctrineScenarios = {
        'estate-mixed': lagSt,
        'focused-dims': dimFix,
        'focused-one': oneSealed,
    };
    for (const [label, st] of Object.entries(doctrineScenarios)) {
        for (const key of STATION_KEYS) {
            const txt = JSON.stringify((st[key] || {}).facts || []);
            for (const rx of DOCTRINE) {
                if (rx.test(txt)) failures.push(`TC-54 doctrine in ${label}/${key} hover: ${rx}`);
            }
        }
    }
    check('TC-54 doctrine sweep ran over every slot of every station', true, true);

    // TC-54 (pin lifecycle, Rules 4b.1/4b.4) — `stationPanel` is a pure string function:
    // actions render ONLY when pinned; pinning never deletes a navigation route.
    // Extract from `derivationPanel` (which `stationPanel` calls) through the end of
    // stationPanel, and inject `_derivOpen` as a parameter so the explainer's open/closed
    // states are both drivable from the gate.
    const spIdx = rsrc.indexOf('function derivationPanel(d)');
    const spEnd = rsrc.indexOf('\n    // ── rendering states onto the ring', spIdx);
    check('TC-54 stationPanel extracted', spIdx > 0 && spEnd > spIdx, true);
    const escFn = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    // `window` is injected because stationPanel asks whether the Assurance Guide is
    // loaded before offering its route (Rule 8.5). Without the stub this harness threw
    // on a mutation instead of reporting one — a crashing gate still fails CI, but it
    // names nothing, and a gate that cannot say WHAT broke is half a gate.
    const mkPanel = (derivOpen, win) => new Function('esc', '_derivOpen', 'window',
        `${rsrc.slice(spIdx, spEnd)}\nreturn stationPanel;`)(escFn, derivOpen, win || guideStub());
    const stationPanel = mkPanel(null);
    const spData = {
        state: 'warn', headline: 'H.',
        facts: [{ k: 'Configuration', v: 'observe', d: '' }],
        actions: [{ label: 'Sign this agent', action: 'sign-agent', primary: true }],
        cta: { label: 'Open the roster', action: 'roster' },
    };
    const unpinned = stationPanel({ label: 'Signed', sub: 's' }, spData, false);
    const pinned = stationPanel({ label: 'Signed', sub: 's' }, spData, true);
    check('TC-54 unpinned hover offers NO action buttons (hover is a reading)',
        unpinned.includes('data-tc-act'), false);
    check('TC-54 unpinned hover hints at the pin affordance',
        /Click the station to keep this open/.test(unpinned), true);
    check('TC-54 pinned panel renders the action', /data-tc-act="sign-agent"/.test(pinned), true);
    check('TC-54 pinning preserves the navigation route (Rule 4b.4)',
        /data-tc-act="roster"/.test(pinned), true);
    check('TC-54 pinned panel offers the close control', /data-tc-unpin/.test(pinned), true);
    check('TC-54 the chip word comes from the shared vocabulary', pinned.includes(hAttn.warn), true);

    // ── TC-100 — honesty is a scored dimension, and GROUNDED counts it ──────────
    // Honesty was a stage in the Trust Journey strip but not one of the dimensions, so
    // the one failure mode nothing else catches — a confident false negative, every data
    // query failed yet the answer reports "there are none" — had no station reporting
    // whether it held. It belongs to GROUNDED because that station's guarantee is the
    // answer's relationship to its evidence, and over-claiming an ABSENCE is that
    // relationship failing at the opposite polarity.
    check('TC-100 honest_absence is in the dimension vocabulary',
        !!(QD.DIM || {}).honest_absence, true);
    check('TC-100 …with a human label, never the raw key',
        QD.label('honest_absence'), 'Honest Absence');
    check('TC-100 …and a full explanation in the guide',
        ((QD.LONG || {}).honest_absence || '').length > 200, true);
    // Boolean by nature: an admin threshold would have to answer "what fraction of
    // confident false negatives is acceptable", which has no honest number.
    const qdSrc100 = fs.readFileSync(QD_SRC, 'utf8');
    check('TC-100 it is not tunable — a yes/no check takes no threshold',
        /honest_absence:\s*\{ key:/.test(qdSrc100), false);
    check('TC-100 …and is not a judge (it is deterministic code)',
        QD.JUDGE_DIMS.has('honest_absence'), false);
    // The station must actually count it, or the strip's station tag would claim an
    // affiliation the ring does not honour — a claim on one surface the other denies.
    // The ring's station key sets live in ONE mirror (`STATION_DIMS`, the partition);
    // the per-station `const gKeys` is a reference into it, so extract the mirror.
    const hsrc100 = fs.readFileSync(SRC, 'utf8');
    const sdSrc = (hsrc100.split('const STATION_DIMS = {')[1] || '').split('};')[0];
    check('TC-100 the STATION_DIMS partition mirror exists', sdSrc.length > 0, true);
    const gKeysSrc = (sdSrc.split('grounded: [')[1] || '').split(']')[0];
    check('TC-100 GROUNDED counts honest_absence', /'honest_absence'/.test(gKeysSrc), true);
    // Every key ANY station names must exist in the vocabulary — a typo would silently
    // drop a dimension from that station's reading with nothing to notice it.
    for (const m of sdSrc.matchAll(/'(\w+)'/g)) {
        if (!QD.DIM[m[1]]) failures.push(`TC-100 STATION_DIMS names unknown dimension "${m[1]}"`);
    }
    check('TC-100 every dimension the partition names exists', true, true);
    // Rule 3b.6 — the partition is DISJOINT: no dimension in two stations' sets.
    // (Registry↔mirror EQUALITY is asserted in test_assurance_points.py §12.)
    {
        const seen = new Map();
        for (const sm of sdSrc.matchAll(/(\w+):\s*\[([^\]]*)\]/g)) {
            for (const dm of sm[2].matchAll(/'(\w+)'/g)) {
                if (seen.has(dm[1])) {
                    failures.push(`3b.6 dimension "${dm[1]}" appears in both ${seen.get(dm[1])} and ${sm[1]}`);
                }
                seen.set(dm[1], sm[1]);
            }
        }
        check('3b.6 the station partition is pairwise disjoint', true, true);
    }
    // Applicability mirrors the runtime gate: Ideate and Focus issue no raw SQL, so the
    // check is structurally inapplicable there rather than merely quiet.
    check('TC-100 the applicability predicate is exported',
        typeof QD.appliesToClass, 'function');
    check('TC-100 it applies to the tool classes',
        QD.appliesToClass('honest_absence', 'tool_enabled')
        && QD.appliesToClass('honest_absence', 'conversation_with_tools'), true);
    check('TC-100 …and structurally not to Ideate or Focus (no raw SQL)',
        QD.appliesToClass('honest_absence', 'llm_only')
        || QD.appliesToClass('honest_absence', 'rag_focused'), false);

    // ── the Guide's partition home + engine tags (operator-mandated consistency) ──
    // Every measurement has exactly ONE station home: owned dims at their owning
    // mechanism's station, everything else at JUDGED (the residue) — no measurement
    // is ever unassigned. Engine tags mirror the scorers' applicability. Simulate the
    // vocabulary registration the live Guide receives from /v1/assurance/vocabulary.
    QD.registerEntries([
        { k: 'grounding_gate', label: 'Grounding gate', desc: 'd',
          dims: ['groundedness', 'grounded_overlap', 'citations'], cls: 'control',
          station: 'grounded', applies_to: [] },
        { k: 'tool_enforcement', label: 'Tool enforcement', desc: 'd',
          dims: ['tool_success_rate'], cls: 'control', station: 'bounded',
          applies_to: ['tool_enabled', 'conversation_with_tools', 'genie'] },
        { k: 'station_grounded', label: 'GROUNDED', desc: 'd',
          dims: ['grounding_gate'], cls: 'station', station: 'grounded' },
    ]);
    check('guide: an owned dimension is homed at its mechanism\'s station',
        QD.stationOf('groundedness'), 'grounded');
    check('guide: a residue dimension is homed at JUDGED', QD.stationOf('completed'), 'scored');
    check('guide: a mechanism is homed at its own station',
        QD.stationOf('grounding_gate'), 'grounded');
    check('guide: a station entry has no home — it IS one',
        QD.stationOf('station_grounded'), null);
    for (const k of Object.keys(QD.DIM)) {
        if (QD.stationOf(k) === null && !String(k).startsWith('station_')) {
            failures.push(`guide: measurement "${k}" has no station home`);
        }
    }
    check('guide: no measurement is left unassigned to a station', true, true);
    check('guide: a Focus-only dimension tags Focus alone',
        JSON.stringify(QD.enginesFor('groundedness')), JSON.stringify(['rag_focused']));
    check('guide: an unrestricted dimension tags every IFOC engine',
        QD.enginesFor('completed').length, 4);
    check('guide: conversation_with_tools folds into the Ideate tag',
        QD.enginesFor('scope_adherence').includes('llm_only'), true);
    check('guide: a flow dimension tags flow alone',
        JSON.stringify(QD.enginesFor('flow_cost')), JSON.stringify(['flow']));
    // The admin Trust page's grouping seam: a threshold key resolves to the STAGE of the
    // turn its dimension measures. It used to resolve to the station that OWNED the
    // dimension — which said a slider belonged to a guarantee, true only while stations
    // owned dimensions. Sliders tune MEASUREMENTS, so they group the way measurements do.
    //
    // Driven with the served model registered, exactly as the admin page has it.
    QD.registerStages(STAGE_MODEL);
    check('admin: a grounding threshold groups at COMPOSE',
        QD.stageOfThreshold('min_grounded'), 'compose');
    check('admin: a tool threshold groups at ACT',
        QD.stageOfThreshold('min_tool_success_rate'), 'act');
    check('admin: a cost threshold groups at THE RUN',
        QD.stageOfThreshold('max_cost_usd'), 'the_run');
    check('admin: a retrieval threshold groups at RETRIEVE',
        QD.stageOfThreshold('min_retrieval_relevance'), 'retrieve');
    check('admin: a flag threshold resolves too (citations → COMPOSE)',
        QD.stageOfThreshold('require_citations'), 'compose');
    check('admin: an unknown threshold key resolves to empty, never a guess',
        QD.stageOfThreshold('max_nonexistent'), '');
    // The sliders must SPREAD across the stages — three station hosts held 7/2/9 of the
    // 18, which is what made the old grouping read as three arbitrary piles.
    check('admin: the tunable thresholds span at least four stages',
        new Set(['min_grounded', 'min_tool_success_rate', 'max_cost_usd',
                 'min_retrieval_relevance', 'max_steps']
            .map((k) => QD.stageOfThreshold(k)).filter(Boolean)).size >= 4, true);


    // ── Rule 3.0c — a disagreement between a coarse check and its refining judge ──
    // GROUNDED read danger on 14/16 live turns because `grounded_overlap` (lexical,
    // median 0.27) systematically disagreed with `groundedness` (the judge that refines
    // it, median 1.00). Neither weighting the judge nor demoting the pre-signal is
    // honest: one hides which measure spoke and makes the reading depend on a SAMPLED
    // judge, the other silences a check that runs on every turn. The disagreement is
    // reported instead — disputed turns leave the rate alone and hold the station at
    // "worth a look", named in the qualifier.
    {
        const gk = ['grounded_overlap', 'groundedness'];
        const cell = (fails, disputed) => ({
            turns: fails.length, failures: 0, tag: 'A',
            dimensions: { grounded_overlap: 0.27, groundedness: 1.0 },
            dimension_counts: { grounded_overlap: fails.length, groundedness: fails.length },
            turn_failed_dims: fails, turn_disputed_dims: disputed,
        });
        // 4 turns, every one flagged by the coarse check and CLEARED by the judge.
        const allDisputed = cell([['grounded_overlap'], ['grounded_overlap'],
                                  ['grounded_overlap'], ['grounded_overlap']],
                                 [['grounded_overlap'], ['grounded_overlap'],
                                  ['grounded_overlap'], ['grounded_overlap']]);
        const d = stations([P('A', 'observe', 'observe')], [T('A', 'trusted')], {
            agent: 'A', dashboard: { enabled: true, by_agent: { A: allDisputed } },
            byAgent: { A: allDisputed } });
        check('3.0c disputed turns do not read as failures', d.scored.execution.text, '100%');
        check('3.0c ...but the station is held at worth a look, never green',
            d.scored.execution.state, 'warn');
        check('3.0c ...and the qualifier NAMES the disagreement',
            /cleared it by the judge|cleared by the judge that refines it/.test(
                d.scored.execution.detail), true);
        // The GUARANTEE the pair is about still explains the disagreement in its own
        // derivation — the reader who opens GROUNDED asking "why aren't these red
        // here?" gets the answer at the station they asked from (reach, not just fact).
        check('3.0c ...and the derivation explains why it is reported, not resolved',
            /reported rather than resolved/.test(
                JSON.stringify(d.grounded.derivation || {})), true);

        // A CORROBORATED failure — both measures agree — must still read danger.
        const corr = cell([['grounded_overlap', 'groundedness'], [], [], []], [[], [], [], []]);
        const c = stations([P('A', 'observe', 'observe')], [T('A', 'trusted')], {
            agent: 'A', dashboard: { enabled: true, by_agent: { A: corr } }, byAgent: { A: corr } });
        check('3.0c a corroborated failure is NOT softened',
            c.scored.execution.text, '75%');
        check('3.0c ...and carries no disagreement note',
            /cleared by the judge/.test(c.scored.execution.detail), false);

        // An older backend emits no `turn_disputed_dims` — the pre-rule reading, unchanged.
        const legacy = { turns: 4, failures: 0, tag: 'A',
            dimensions: { grounded_overlap: 0.27 }, dimension_counts: { grounded_overlap: 4 },
            turn_failed_dims: [['grounded_overlap'], [], [], []] };
        const l = stations([P('A', 'observe', 'observe')], [T('A', 'trusted')], {
            agent: 'A', dashboard: { enabled: true, by_agent: { A: legacy } }, byAgent: { A: legacy } });
        check('3.0c an older payload keeps the pre-rule reading', l.scored.execution.text, '75%');
        void gk;
    }

    // ── TC-99 — the explainer survives a passing pointer, and the ring does not move ─
    // Two independent defects produced one visible symptom: pin a station, open "?", then
    // move the pointer across the ring, and the panel flickered.
    //
    // (a) GEOMETRY. `.tc-root` is a two-column grid whose row height is the taller of
    // (ring, panel). Under `align-items: center` the ring is centred in that row, so every
    // panel-height change TRANSLATES the ring: a peek shrank the panel, the ring slid up,
    // the pointer fell off the node, `mouseleave` restored the taller pinned panel, the
    // ring slid back under the pointer, `mouseenter` fired — a loop with no stable state.
    // Panel CONTENT must never feed back into pointer TARGETING.
    const cssSrc = fs.readFileSync(path.join(HERE, '..', 'static', 'css', 'trust.css'), 'utf8');
    const rootRule = (cssSrc.split('.tc-root {')[1] || '').split('}')[0];
    check('TC-99 the ring is anchored, not centred against the panel',
        /align-items:\s*start/.test(rootRule), true);
    check('TC-99 …and specifically not centred (the flicker geometry)',
        /align-items:\s*center/.test(rootRule), false);

    // (b) PRECEDENCE. Even with stable geometry, a pointer merely crossing the ring must
    // not tear down a derivation the reader deliberately opened. Extracted verbatim and
    // driven as a truth table — the predicate decides whether a reader's deeper read
    // survives, so every branch is asserted, not just the headline one.
    const psIdx = rsrc.indexOf('    function peekSuppressed(');
    const psEnd = rsrc.indexOf('\n    function renderPanel(', psIdx);
    check('TC-99 peekSuppressed extracted', psIdx > 0 && psEnd > psIdx, true);
    const peekSuppressed = new Function(`${rsrc.slice(psIdx, psEnd)}\nreturn peekSuppressed;`)();
    check('TC-99 a pointer peek elsewhere does NOT discard an open explainer',
        peekSuppressed('signed', true, 'grounded', 'grounded'), true);
    // The three ways this could over-reach — each would break a working interaction:
    check('TC-99 with the explainer closed, a normal peek still works',
        peekSuppressed('signed', true, 'grounded', null), false);
    check('TC-99 keyboard focus still peeks — Tab IS the navigation',
        peekSuppressed('signed', false, 'grounded', 'grounded'), false);
    check('TC-99 mouseleave still falls back to the pinned station',
        peekSuppressed(undefined, true, 'grounded', 'grounded'), false);
    check('TC-99 re-hovering the pinned station itself is not suppressed',
        peekSuppressed('grounded', true, 'grounded', 'grounded'), false);
    check('TC-99 with nothing pinned every peek renders',
        peekSuppressed('signed', true, null, null), false);
    // The pointer flag has to actually reach the predicate: `mouseenter` passes it,
    // `focus` must not — otherwise the keyboard branch above is unreachable in practice.
    const _lineWith = (needle) => (rsrc.split('\n').find((l) => l.includes(needle)) || '');
    const enterLine = _lineWith("addEventListener('mouseenter'");
    const focusLine = _lineWith("addEventListener('focus'");
    check('TC-99 mouseenter marks the peek as pointer-driven',
        /renderPanel\(key,\s*true\)/.test(enterLine), true);
    check('TC-99 focus does not — else the keyboard branch is unreachable in practice',
        /renderPanel\(key\)/.test(focusLine) && !/renderPanel\(key,/.test(focusLine), true);

    // ── the derivation explainer ("?") — Rule 3b ────────────────────────────────
    // The station's method must be explainable WITHOUT crowding the reading, the
    // explanation must not restate a slot, and where quality dimensions contribute they
    // must be reachable — a figure derived from named dimensions that cannot be inspected
    // is exactly the opacity this feature exists to remove.
    // Drive the REAL _dimRows (inside the extracted body) for a sampled judge dimension.
    const _dimRows_probe = (n = 1) => {
        const st = stations([P('A', 'enforce', 'enforce')], [], {
            agent: 'A', dashboard: { enabled: true },
            byAgent: { A: { turns: 18, dimensions: { groundedness: 0 }, dimension_counts: { groundedness: n } } },
        });
        return (st.scored.derivation.dims || []).find((d) => d.key === 'groundedness') || {};
    };
    const dstations = stations([P('A', 'enforce', 'enforce')], [T('A', 'trusted')], {
        agent: 'A', dashboard: { enabled: true },
        byAgent: { A: { turns: 18, failures: 5,
                        dimensions: { groundedness: 0.5, grounded_overlap: 0.66, safety: 1 },
                        dimension_counts: { groundedness: 18, grounded_overlap: 18, safety: 18 } } },
    });
    // 3b.1 — EVERY station carries a derivation. A station that cannot explain its own
    // figure is a number with no provenance, which is what the ring exists to refuse.
    for (const key of STATION_KEYS) {
        const d = (dstations[key] || {}).derivation;
        check(`3b.1 ${key} carries a derivation`, !!d, true);
        check(`3b.1 ${key} names the population it measured over`, !!(d && d.window), true);
        // W6 — a configuration / execution row renders only when it carries a LIVE
        // value (a figure, an instance, a named agent); the method itself lives in the
        // Assurance Guide's station entry, which the explainer routes to.
        const live = (t) => /\d|@\w|: [a-z]/.test(String(t || ''));
        check(`3b.1 ${key} configuration row is live or absent (W6)`, !d.config || live(d.config), true);
        check(`3b.1 ${key} execution row is live or absent (W6)`, !d.exec || live(d.exec), true);
    }
    // 3b.2 — a station with NO quality-dimension input must SAY so, or the explainer
    // implies the ring is a view onto the Quality card. Two of the eight have none;
    // BOUNDED and PROVEN joined the dimension-fed stations when the partition
    // (Rule 3b.6) re-homed the tool-conduct and record-soundness checks to them.
    for (const key of ['signed', 'reviewed']) {
        const d = dstations[key].derivation;
        check(`3b.2 ${key} has no contributing dimensions`, (d.dims || []).length, 0);
        check(`3b.2 ${key} says so explicitly rather than leaving it unsaid`,
            /no quality dimension/i.test(d.note || ''), true);
    }
    // …and the stations that LABEL measurements carry a real slice, every key known.
    // The exact membership is deliberately NOT pinned here: `test_assurance_points`
    // asserts ring ≡ registry in BOTH directions, and a second copy of that fact in this
    // gate is the defect the symmetry work exists to remove — it was a hardcoded `4` that
    // failed when A18 correctly dropped two mislabelled dimensions, reporting a
    // deliberate improvement as a regression.
    for (const key of ['bounded', 'proven']) {
        const d = dstations[key].derivation;
        check(`3b.2 ${key} carries a non-empty label slice (as the graded line — W3)`,
            !!(d.graded && d.graded.total > 0), true);
        check(`3b.2 ${key} carries no chip grid of its own (W3)`, (d.dims || []).length, 0);
    }
    // 3b.3 — a station WITH dimension input names them, and every one is a real key the
    // Assurance Guide can explain (a chip that opens an empty guide is worse than none).
    for (const key of ['grounded', 'guarded']) {
        const g = dstations[key].derivation.graded || {};
        check(`3b.3 ${key} counts its contributing dimensions in the graded line`, g.total > 0, true);
        check(`3b.3 ${key} carries no chip grid of its own (W3)`, (dstations[key].derivation.dims || []).length, 0);
    }
    // …and JUDGED, where every chip lives, names them from the one vocabulary.
    {
        const dims = dstations.scored.derivation.dims || [];
        check('3b.3 JUDGED names the contributing dimensions', dims.length > 0, true);
        check('3b.3 JUDGED dimensions are all known to QualityDimensions', dims.every((x) => !!QD.DIM[x.key]), true);
        check('3b.3 JUDGED labels come from the ONE vocabulary', dims.every((x) => x.label === QD.label(x.key)), true);
    }
    // 3b.4 — the live band travels with the chip, and a dimension this class never
    // produces is marked inapplicable rather than silently dropped or shown as a pass.
    const gd = dstations.scored.derivation.dims;   // W3 — the chips live at JUDGED
    // Defensive lookups: a regression that DROPS a dimension must report a clean
    // assertion failure, not crash the gate on a dereference (a crash diagnoses nothing
    // and hides every check after it).
    const gGrounded = gd.find((x) => x.key === 'groundedness') || {};
    check('3b.4 a measured dimension carries its live band', gGrounded.band, 'review');
    check('3b.4 …and its score', gGrounded.score, 0.5);
    // W3 — on a guarantee station the checks its class never produced are COUNTED in
    // the graded line ("N not produced by this class"), never dropped and never a pass.
    const gg = dstations.grounded.derivation.graded || {};
    check('3b.4 unmeasured dimensions are counted, not dropped', gg.total > gg.of, true);
    check('3b.4 …and never counted as clean', gg.clean <= gg.of, true);
    check('3b.4 …with the measured ones carrying their live band at JUDGED',
        (gd.find((x) => x.key === 'grounded_overlap') || {}).band, 'review');
    // 3b.7 (Rule 3.5) — a SAMPLED dimension carries its denominator. `Groundedness (RAG)
    // 0%` on one judged turn reads identically to 0% on eighteen without it — and the
    // tentative one then looks like a catastrophe. This is the defect that prompted the
    // rule; it must not come back.
    const sampled = _dimRows_probe();
    check('3b.7 a sampled dimension is marked partial', sampled.partial, true);
    check('3b.7 …and tentative at n<=2', sampled.tentative, true);
    check('3b.7 …and carries n', sampled.n, 1);
    check('3b.7 …and its denominator', sampled.total, 18);
    const fullDim = _dimRows_probe(18);
    check('3b.7 a fully-measured dimension is NOT partial', fullDim.partial, false);
    check('3b.7 …and not tentative', fullDim.tentative, false);
    const chipOpen = mkPanel('grounded')({ key: 'grounded', label: 'Grounded', sub: 's' }, {
        state: 'warn', headline: 'H.', facts: [{ k: 'Configuration', v: 'x', d: '' }],
        derivation: { window: 'w', config: 'c', exec: 'e', note: '',
                      dims: [{ key: 'groundedness', label: 'Groundedness (RAG)', applicable: true,
                               band: 'danger', score: 0, n: 1, total: 18,
                               partial: true, tentative: true }] },
    }, true);
    // The denominator is still mandatory — at n<=2 the FRACTION carries it (0/1), above
    // that the n= badge does. Assert the requirement, not one particular rendering.
    check('3b.7 the rendered chip shows the denominator', /0\/1|n=1(?!\d)/.test(chipOpen), true);
    check('3b.7 …flagged tentative by shape', /tc-dim-danger tentative/.test(chipOpen), true);
    check('3b.7 …and the tooltip states the full ratio',
        /decided on 1 of 18 scored turn/.test(chipOpen), true);

    // 3b.8 — EVERY visual channel a surface renders must appear in its legend. The bar's
    // 45%-opacity dimming carried real meaning ("the evidence is thin") for months while
    // being explained nowhere: a signal nobody can read is worse than no signal, because
    // the viewer knows something was said and cannot tell what. Assert the channels and
    // their legend entries together, so adding one without the other fails here.
    const barIdx = hsrc.indexOf('function bar(key, value, threshold, n, total, series)');
    const barBody = hsrc.slice(barIdx, hsrc.indexOf('\n    function ', barIdx + 10));
    const legendIdx = hsrc.indexOf('LIVE PASS RATE BY DIMENSION');
    const legendBlock = hsrc.slice(legendIdx, legendIdx + 2000);
    check('3b.8 the bar renders a dimming channel', /fillOpacity/.test(barBody), true);
    // Assert the VISIBLE legend text, not merely the word somewhere in the region: the
    // first version of this check was satisfied by the badge's TOOLTIP, so deleting the
    // legend entry still passed — a gate that reports confidence it has not earned.
    // The legend is the row after the section header; the tooltip is a data-tip attribute.
    const legendVisible = legendBlock.replace(/data-tip="[^"]*"/g, '');
    check('3b.8 …and the visible legend states it', /dimmed/.test(legendVisible), true);
    check('3b.8 the bar renders an n= badge', /n=\$\{n\}/.test(barBody), true);
    check('3b.8 …and the legend states both sample states',
        /fewer turns than scored/.test(legendVisible) && /fraction/.test(legendVisible), true);
    check('3b.8 …without spending an attention colour on confidence',
        /#f59e0b/.test(legendVisible.slice(legendVisible.indexOf('fewer turns'))), false);
    check('3b.8 the legend states the judge marker', /model-judged/.test(legendBlock), true);
    check('3b.8 the legend states the three bands',
        /Healthy/.test(legendBlock) && /Review/.test(legendBlock) && /Danger/.test(legendBlock), true);

    // 3b.9 — CONFIDENCE IS NOT ATTENTION, on BOTH surfaces. Colour belongs to the rate's
    // band (Rule 4.1); a thin sample is marked by shape — the fraction form, the dimmed
    // bar, the dashed chip. Asserted on the roster AND the ring together, because fixing
    // one surface and discovering the other later is how this pair drifted before.
    check('3b.9 the roster never colours the n= badge amber',
        /n=\$\{n\}<\/span>/.test(barBody) && /#f59e0b/.test(barBody), false);
    check('3b.9 the roster renders a FRACTION at n<=2, not a percentage',
        /tentative && n != null && value != null[\s\S]{0,80}\$\{n\}`/.test(barBody), true);
    check('3b.9 …and drops the now-redundant n= badge there (Rule 3.0)',
        /\(!partial \|\| tentative\)/.test(barBody), true);
    check('3b.9 the roster still dims the bar', /fillOpacity = tentative \? 0\.45/.test(barBody), true);
    check('3b.9 the ring chip never colours its n= badge',
        /tc-dim-n tentative/.test(rsrc), false);
    check('3b.9 the ring marks tentative by SHAPE (dashed chip)',
        /\.tc-dim\.tentative \{ border-style: dashed/.test(
            fs.readFileSync(path.join(HERE, '..', 'static', 'css', 'trust.css'), 'utf8')), true);
    // Behavioural: a tentative chip renders `0/1`, a healthy sampled one keeps `n=`.
    const tentChip = mkPanel('grounded')({ key: 'grounded', label: 'G', sub: 's' }, {
        state: 'warn', headline: 'H.', facts: [{ k: 'Configuration', v: 'x', d: '' }],
        derivation: { window: 'w', config: 'c', exec: 'e', note: '', dims: [
            { key: 'groundedness', label: 'Groundedness (RAG)', applicable: true, band: 'danger',
              score: 0, n: 1, total: 18, partial: true, tentative: true },
            { key: 'grounded_overlap', label: 'Grounding Overlap', applicable: true, band: 'review',
              score: 0.72, n: 11, total: 18, partial: true, tentative: false }] },
    }, true);
    check('3b.9 a tentative chip shows the fraction 0/1', /0\/1/.test(tentChip), true);
    check('3b.9 …and NOT a percentage', /Groundedness \(RAG\)[\s\S]{0,60}0%/.test(tentChip), false);
    check('3b.9 …and carries the dashed shape', /tc-dim-danger tentative/.test(tentChip), true);
    check('3b.9 …and does not also print n=1 (no restatement)',
        /n=1(?!\d)/.test(tentChip), false);
    check('3b.9 a merely-partial chip keeps the percentage', /72%/.test(tentChip), true);
    check('3b.9 …and keeps its grey n= badge', /tc-dim-n">n=11/.test(tentChip), true);
    // The legend must show the form the surface actually renders.
    check('3b.9 the legend shows the fraction example', /0\/1<\/span>/.test(legendVisible), true);
    check('3b.9 …and no longer advertises an amber n=1', /n=1<\/span> \u22642/.test(legendVisible), false);

    // 3b.5 — Rule 3.0 holds across the new zone: the explainer must not restate a slot.
    for (const key of STATION_KEYS) {
        const st = dstations[key], d = st.derivation;
        const slots = (st.facts || []).map((f) => String((f && f.d) || '').toLowerCase()).filter(Boolean);
        for (const [name, text] of [['config', d.config], ['exec', d.exec], ['note', d.note]]) {
            const t = String(text || '').toLowerCase();
            if (t && slots.some((sl) => sl.length > 24 && (sl === t || t.includes(sl)))) {
                failures.push(`3b.5 ${key}: derivation.${name} restates a hover slot verbatim`);
            }
        }
    }
    check('3b.5 no derivation restates a hover slot', true, true);
    // 3b.6 — the RENDERED explainer: closed by default (the reading comes first), the "?"
    // only where a derivation exists, and dimensions rendered as real routable buttons.
    const dData = {
        state: 'warn', headline: 'H.',
        facts: [{ k: 'Configuration', v: 'enforce', d: '' }],
        cta: { label: 'Open the roster', action: 'roster' },
        derivation: { window: '18 scored turns in this window', config: 'C-text.', exec: 'E-text.',
                      dims: [{ key: 'groundedness', label: QD.label('groundedness'),
                               applicable: true, band: 'review', score: 0.5, n: 18 }],
                      note: 'N-text.' },
    };
    const stObj = { key: 'grounded', label: 'Grounded', sub: 's' };
    const closed = mkPanel(null)(stObj, dData, true);
    const open = mkPanel('grounded')(stObj, dData, true);
    check('3b.6 the "?" control renders when a derivation exists',
        /data-tc-deriv/.test(closed), true);
    check('3b.6 the explainer is CLOSED by default (the reading comes first)',
        /class="tc-deriv"/.test(closed), false);
    check('3b.6 …and its control reports collapsed to assistive tech',
        /aria-expanded="false"/.test(closed), true);
    check('3b.6 opening it renders the layer', /class="tc-deriv"/.test(open), true);
    check('3b.6 …and reports expanded', /aria-expanded="true"/.test(open), true);
    check('3b.6 the layer names the population', /18 scored turns/.test(open), true);
    check('3b.6 a contributing dimension is a real routable button',
        /data-tc-act="explain-dim:groundedness"/.test(open), true);
    check('3b.6 …labelled from the one vocabulary', open.includes(QD.label('groundedness')), true);
    check('3b.6 a station without a derivation shows no "?"',
        /data-tc-deriv/.test(mkPanel(null)(stObj, { state: 'na', headline: 'H.', facts: [] }, true)), false);
    check('3b.6 the explainer never renders on an unpinned peek (Rule 4b.5)',
        /class="tc-deriv"/.test(mkPanel('grounded')(stObj, dData, false)), false);
    check('3b.6 …but the peek hint makes it discoverable',
        /derived/.test(mkPanel(null)(stObj, dData, false)), true);
    // 3b.7 — the explainer routes to the ONE glossary; it must not grow a second one.
    // 3b.7 — the property is that the ring never DEFINES a dimension: no second
    // glossary, no label/description lookup, no rendering of an entry. It routes.
    // The blunt form ("never mentions QualityDimensions") also banned asking whether
    // the Guide is even available — a capability check defines nothing, and without it
    // the panel would offer a button that cannot open anything, which Rule 8.5 bans in
    // the opposite direction. Asserted as the property, with every lookup named.
    check('3b.7 the ring routes to the guide', /explain-dim:/.test(rsrc), true);
    for (const lookup of ['openGuide(', '.label(', '.desc(', 'LONG[', 'DIM['])
        check(`3b.7 …and never looks a dimension up itself (${lookup})`,
            rsrc.includes('QualityDimensions' + (lookup.startsWith('.') ? lookup : '.' + lookup))
            || rsrc.includes('QD' + (lookup.startsWith('.') ? lookup : '.' + lookup)), false);
    check('3b.7 …its only use of the Guide module is asking whether it exists',
        (rsrc.match(/QualityDimensions/g) || []).length <= 2, true);
    check('3b.7 the host resolves explain-dim through QualityDimensions.openGuide',
        /explain-dim:[\s\S]{0,900}QD\.openGuide/.test(hsrc), true);

    // ── the JUDGED label on the `scored` wire key + containment ─────────────────
    // The label went judged→scored (Aug: 42 of 51 dims are deterministic; "Judged"
    // collided with the literal judges) and BACK (operator decision): once TRUSTED's
    // value became a conduct SCORE, "Scored" carried two meanings on one ring, and the
    // score/score collision is the one readers actually hit. The station renders
    // VERDICTS — its subtitle always said so. The WIRE KEY never moves: spans, payloads
    // and Guide keys are persisted, so `scored` stays and only the surface reads Judged.
    check('rename: the wire key is `scored`', STATION_KEYS.includes('scored'), true);
    check('rename: no `judged` wire key exists', STATION_KEYS.includes('judged'), false);
    check('rename: the renderer labels it Judged',
        /key:\s*'scored',\s*label:\s*'Judged'/.test(rsrc), true);
    check('rename: no station is still labelled Scored', /label:\s*'Scored'/.test(rsrc), false);
    check('rename: the renderer keys its icon by the wire key', /^\s*scored:\s*'</m.test(rsrc), true);
    // The station's own rendered text must carry the new name, never the old one.
    for (const key of STATION_KEYS) {
        const blob = JSON.stringify(dstations[key] || {});
        if (/\bSCORED\b/.test(blob)) {
            failures.push(`rename: station ${key} still says "SCORED" in its rendered text`);
        }
    }
    check('rename: no station text says "SCORED"', true, true);
    // The SUBTITLE names the PARTITION residue, so the ring states this station's
    // narrowed subject before anyone opens a panel — and it must not drift from the
    // derivation two slots below. Its earlier "every check, one verdict" claimed the
    // union Rule 3b.6 has since replaced; a subtitle promising something the note does
    // not teach would be doctrine (Rule 3.3) rather than a reading.
    // Parse the renderer's own station registry — labels and subtitles, verbatim.
    const STATIONS_META = {};
    for (const m of rsrc.matchAll(/\{ key: '(\w+)',\s*label: '([^']+)',\s*sub: '([^']+)'(?:,\s*q: '([^']+)')? \}/g)) {
        STATIONS_META[m[1]] = { label: m[2], sub: m[3], q: m[4] || '' };
    }
    check('rename: the station registry was parsed',
        Object.keys(STATIONS_META).length, STATION_KEYS.length);
    const scoredSub = (STATIONS_META.scored || {}).sub || '';
    check('rename: SCORED has a subtitle', scoredSub.length > 0, true);
    check('rename: the subtitle names the residue subject, not the replaced union',
        /execution quality/i.test(scoredSub) && !/every check/i.test(scoredSub), true);
    check('rename: the subtitle does not resurrect judging',
        /judg/i.test(scoredSub), false);
    // Register check — the siblings are lowercase prose, not textspeak or abbreviations.
    for (const [k, m] of Object.entries(STATIONS_META)) {
        if (/\d|[A-Z]{2,}/.test(m.sub || '')) {
            failures.push(`register: station ${k} subtitle "${m.sub}" is not plain prose`);
        }
    }
    check('register: every station subtitle is plain prose', true, true);

    // THE PARTITION (Rule 3b.6, replacing the union/containment model): every station
    // owns a mutually exclusive dimension set, JUDGED holds the residue, and TRUSTED is
    // the sole aggregate. Stated on BOTH sides so neither can drift into implying the
    // other model — a relationship visible from only one end reads as a coincidence.
    const scoredNote = dstations.scored.derivation.note || '';
    check('lane cut: JUDGED declares that it counts ALL measurement',
        /where MEASUREMENT is counted — all of it/i.test(scoredNote), true);
    // W6 — the history of the definition lives in the casebook; the note states the rule.
    check('lane cut: JUDGED no longer defines itself by exclusion',
        /whatever no other station claimed|residue|leftover/i.test(scoredNote), false);
    check('lane cut: JUDGED note is three sentences, not a paragraph',
        (scoredNote.match(/\. /g) || []).length <= 2 && scoredNote.length < 520, true);
    check('lane cut: JUDGED names what the other stations report instead',
        /what their MECHANISMS did/.test(scoredNote), true);
    check('lane cut: JUDGED keeps the guarantee LABEL alive for grouping',
        /the guarantee it is\s+ABOUT/.test(scoredNote), true);
    check('lane cut: JUDGED states the judge cap and why',
        /judges stay advisory/.test(scoredNote), true);
    check('lane cut: JUDGED names TRUSTED as the single aggregate',
        /TRUSTED remains the single aggregate/.test(scoredNote), true);
    check('partition: JUDGED never claims the replaced union model',
        /union over ALL|can never read better/i.test(scoredNote), false);
    for (const key of ['grounded', 'guarded', 'bounded']) {
        const n = dstations[key].derivation.note || '';
        // A guarantee station must ANSWER the question its own dimension rows provoke:
        // if these checks are about me, why am I not coloured by them?
        check(`lane cut: ${key} states that its checks are counted at JUDGED`,
            /counted at JUDGED/.test(n), true);
        // W3 — one sentence: the cut is carried by the lane chip and the graded row
        // now, so the note states the rule and stops. The rest lives in the Guide.
        check(`lane cut: ${key} says the checks do not colour it`, /do not colour this station/.test(n), true);
        check(`lane cut: ${key} states the time cut`, /acts during the turn/.test(n) && /grades it afterwards/.test(n), true);
        check(`lane cut: ${key} note is short — the shared sentence plus at most its own two`,
            (n.match(/\. /g) || []).length <= 3 && n.length < 700, true);
        check(`lane cut: ${key} no longer claims to OWN its dimensions`,
            /OWNS the dimensions/.test(n), false);
        check(`lane cut: ${key} no longer claims to be a subset of JUDGED`,
            /SUBSET of JUDGED|can never read better/.test(n), false);
    }
    // TRUSTED's own explainer must claim the aggregate WITHOUT a double count.
    // (Source-checked: this fixture runs with behavioral trust off, whose derivation
    // legitimately describes the off state instead.)
    check('partition: TRUSTED declares itself the one aggregate',
        /ONE aggregate on the ring/.test(hsrc), true);
    check('partition: …and that its blend counts no dimension twice',
        /PARTITION the evidence, so no dimension is counted twice/.test(hsrc), true);

    // ── 3b.10 — the Guide's "can I change this?" claim, for EVERY dimension ──────
    // Two different things look alike in the Guide: WHICH dimensions a station watches
    // (fixed in code) and a dimension's PASS LINE (configurable — but only for
    // deterministic ones). A wrong claim here sends an operator to a slider that does
    // not exist, or tells them a real control is unavailable. Ground truth is the
    // SCORERS themselves, so the Guide cannot drift from what the code actually reads.
    const qdSrc = fs.readFileSync(QD_SRC, 'utf8');
    const tuneBlock = qdSrc.split('const TUNING = {')[1].split('\n    };')[0];
    const tuneMap = {};
    for (const m of tuneBlock.matchAll(/(\w+):\s*\{ key: '(\w+)'/g)) tuneMap[m[1]] = m[2];
    // Parse the Python scorers for `dimension = "x"` + `_cuts(case, "key")` + kind.
    const scorerDir = path.join(HERE, '..', 'src', 'trusted_data_agent', 'eval', 'scorers');
    const truth = {};
    for (const f of fs.readdirSync(scorerDir).filter((x) => x.endsWith('.py'))) {
        for (const blk of fs.readFileSync(path.join(scorerDir, f), 'utf8').split('\nclass ')) {
            const dm = blk.match(/dimension = "(\w+)"/);
            if (!dm) continue;
            const cm = blk.match(/_cuts\(case, "(\w+)"\)/);
            const km = blk.match(/kind = ScoreKind\.(\w+)/);
            truth[dm[1]] = { key: cm ? cm[1] : null, judge: km ? km[1] === 'JUDGE' : null };
        }
    }
    check('3b.10 the scorers were parsed', Object.keys(truth).length > 20, true);
    let tuneBad = 0;
    for (const [dim, t] of Object.entries(truth)) {
        if (!QD.DIM[dim]) continue;                       // not surfaced in the Guide
        const listed = tuneMap[dim], isJudge = QD.JUDGE_DIMS.has(dim);
        if (t.key && !listed) {
            tuneBad++; failures.push(`3b.10 ${dim}: scorer reads '${t.key}' but the Guide offers no control`);
        }
        if (listed && t.key && listed !== t.key) {
            tuneBad++; failures.push(`3b.10 ${dim}: Guide says '${listed}', scorer reads '${t.key}'`);
        }
        if (listed && isJudge) {
            tuneBad++; failures.push(`3b.10 ${dim}: claimed tunable AND judge-backed`);
        }
        if (t.judge && !isJudge) {
            tuneBad++; failures.push(`3b.10 ${dim}: scorer kind=JUDGE but not in JUDGE_DIMS`);
        }
    }
    check('3b.10 every dimension\'s tuning claim matches its scorer', tuneBad, 0);

    // 3b.11 — the THREE kinds must be told apart correctly. "Pass/fail is structural"
    // claims a verdict exists; for a scorer that returns `passed=None` on every path
    // there is no verdict at all, only a number. Asserting a decision where there is
    // only a measurement is the same over-claim class as reporting absence-of-failure
    // as success — so the informational set is derived from the scorers, not asserted.
    const infoSet = new Set(
        [...qdSrc.split('const INFORMATIONAL = new Set([')[1].split('])')[0]
            .matchAll(/'(\w+)'/g)].map((m) => m[1]));
    const scorerBodies = {};
    for (const f of fs.readdirSync(scorerDir).filter((x) => x.endsWith('.py'))) {
        for (const blk of fs.readFileSync(path.join(scorerDir, f), 'utf8').split('\nclass ')) {
            const dm = blk.match(/dimension = "(\w+)"/);
            if (dm) scorerBodies[dm[1]] = blk.split('\n\nclass')[0];
        }
    }
    let kindBad = 0;
    for (const [dim, body] of Object.entries(scorerBodies)) {
        if (!QD.DIM[dim] || tuneMap[dim] || QD.JUDGE_DIMS.has(dim)) continue;  // only the structural ones
        // Does any return path produce a boolean verdict?
        const boolish = /passed=\s*(?!None)[\w(]+|_verdict\(\s*(?!None)[\w(]+|_ok\(|_fail\(/.test(body);
        if (!boolish && !infoSet.has(dim)) {
            kindBad++;
            failures.push(`3b.11 ${dim}: never returns a verdict, but the Guide calls it a yes/no check`);
        }
        if (boolish && infoSet.has(dim)) {
            kindBad++;
            failures.push(`3b.11 ${dim}: returns a real verdict, but the Guide calls it informational`);
        }
    }
    check('3b.11 binary vs informational matches the scorers', kindBad, 0);
    check('3b.11 the informational set is non-empty (the distinction is live)',
        infoSet.size > 0, true);
    check('3b.11 no dimension is both tunable and informational',
        [...infoSet].some((d) => tuneMap[d]), false);
    // And every dimension a STATION watches must get one of the three honest answers.
    const stationDims = new Set();
    for (const key of STATION_KEYS) {
        for (const d of ((dstations[key].derivation || {}).dims || [])) stationDims.add(d.key);
    }
    for (const k of ['groundedness', 'grounded_overlap', 'scope_adherence', 'safety', 'output_sanity']) {
        const answerable = QD.JUDGE_DIMS.has(k) || !!tuneMap[k] || !!QD.DIM[k];
        check(`3b.10 ${k} has a definite tuning answer`, answerable, true);
    }
    check('3b.10 the station dimension set is non-empty', stationDims.size > 0, true);

    // ── 3b.12 — ONE source for the band floors ──────────────────────────────────
    // Where green ends and amber begins was defined in FOUR places (the roster's
    // HEALTHY/REVIEW, the ring's `_execBand`, the KPI charts' `healthColor`, and an
    // admin-editable copy in TRUST_BEHAVIOR). Four definitions of two numbers is how a
    // rate reads amber on the roster and green on the chart beside it.
    const kpiSrc = fs.readFileSync(
        path.join(HERE, '..', 'static', 'js', 'handlers', 'agentKpiCharts.js'), 'utf8');
    // No module may re-derive a floor from a literal in a banding expression.
    check('3b.12 the ring bands by the shared floors, not literals',
        /rate >= HEALTHY \? 'pass' : rate >= REVIEW \? 'warn'/.test(hsrc), true);
    check('3b.12 …and no literal floor survives in _execBand',
        /rate >= 0\.8 \? 'pass'/.test(hsrc), false);
    // The PROPERTY is "bands by the injected floors" — colour-agnostic, since healthColor
    // now returns --trust-* tokens (TC-91b), not literals. Pinning the hex here pinned the
    // implementation, not the rule.
    check('3b.12 the KPI charts band by injected floors',
        /pr >= _healthyFloor[\s\S]{0,60}pr >= _warnFloor/.test(kpiSrc), true);
    check('3b.12 …and no literal floor survives in healthColor',
        /pr >= 0\.8 \?/.test(kpiSrc), false);
    check('3b.12 the charts expose a setter for the one source',
        /setBands/.test(kpiSrc) && /agentKpiCharts\.setBands/.test(hsrc), true);
    // The configured value is ADOPTED before anything bands a rate.
    check('3b.12 floors are applied before the model is built',
        hsrc.indexOf('_applyQualityBands(dashboard)') < hsrc.indexOf('_model = buildModel('), true);
    // A malformed or inverted config must NOT repaint the estate.
    const applyFn = hsrc.slice(hsrc.indexOf('function _applyQualityBands'),
                               hsrc.indexOf('\n    }', hsrc.indexOf('function _applyQualityBands')));
    check('3b.12 an inverted band (warn >= healthy) is rejected', /w >= h/.test(applyFn), true);
    check('3b.12 an out-of-range band is rejected', /h > 0 && h <= 1/.test(applyFn), true);
    // Byte-identical baseline: the defaults ARE the historical numbers.
    check('3b.12 default green floor unchanged', /let HEALTHY = 0\.8;/.test(hsrc), true);
    check('3b.12 default amber floor unchanged', /let REVIEW = 0\.5;/.test(hsrc), true);
    // Semantics stay OUT of the settings page: the mode ladder and worst-of are claims
    // about what the system DOES, not judgements about where a bar sits. Configurable
    // worst-of would permit best-of — the shipped false-green this grammar exists to stop.
    check('3b.12 the mode ladder is not configurable',
        /_POSTURE_STATE[\s\S]{0,400}quality_bands/.test(hsrc), false);
    check('3b.12 the hand-rolled ladder is gone — the one map is read instead',
        !/_modeLadderState/.test(hsrc) && /_POSTURE_STATE = \(_TG && _TG\.POSTURE_STATE\)/.test(hsrc), true);
    check('3b.12 worst-of aggregation is not configurable',
        /_postureAggregate[\s\S]{0,400}quality_bands/.test(hsrc), false);

    // ── 3b.14 — the Guide reports the LIVE bands, not the shipped defaults ─────
    // It used to print "unset by default" for every metric: a claim about what SHIPS,
    // rendered where a reader looks for what IS. On a deployment whose admin had tuned
    // every slider that line was false in the worst direction — it said nothing was
    // banded while everything was. Same over-claim family as Rule 3.5 (a figure must
    // carry its own denominator): a constant must never wear a live reading's clothes.
    const TS_SRC = path.join(HERE, '..', 'static', 'js', 'handlers', 'thresholdSliders.js');
    const tsSrc = fs.readFileSync(TS_SRC, 'utf8');

    // (a) ONE source for label / unit / direction. Two copies could disagree on `dir`,
    // and a band printed backwards ("Healthy <= 75%" on a higher-is-better metric) is
    // worse than no band at all — it reads as a fact.
    const specKeys = new Set(Object.keys(QD.THRESHOLD_SPEC || {}));
    const rangeKeys = new Set([...(tsSrc.split('const RANGES = [')[1] || '').split('\n    ];')[0]
        .matchAll(/key: '(\w+)'/g)].map((m) => m[1]));
    check('3b.14 the slider metric table was parsed', rangeKeys.size >= 15, true);
    check('3b.14 every slider metric has a canonical spec',
        [...rangeKeys].filter((k) => !specKeys.has(k)).join('|'), '');
    check('3b.14 the canonical spec invents no metric the slider lacks',
        [...specKeys].filter((k) => !rangeKeys.has(k)).join('|'), '');
    check('3b.14 the slider keeps no second copy of label/unit/dir',
        /const RANGES = \[[\s\S]*?\n    \];/.exec(tsSrc)[0].match(/label:|unit:|dir:/), null);
    check('3b.14 the slider formats through the canonical formatter',
        /QD\.fmtThreshold\(spec\.key, v\)/.test(tsSrc), true);

    // (b) direction is honoured in BOTH senses. Asserting only one would pass a
    // hardcoded operator.
    check('3b.14 lower-is-better reads Healthy <= / Danger >',
        QD.fmtBand('max_cost_usd', { healthy: 0.3, danger: 0.6 }), 'Healthy ≤ $0.30 · Danger > $0.60');
    check('3b.14 higher-is-better reads Healthy >= / Danger <',
        QD.fmtBand('min_grounded', { healthy: 0.75, danger: 0.5 }), 'Healthy ≥ 75% · Danger < 50%');
    check('3b.14 a half-set band prints only the cut that exists',
        QD.fmtBand('max_tokens', { danger: 25000 }), 'Danger > 25,000');
    check('3b.14 an absent band prints nothing, never a zero',
        QD.fmtBand('max_tokens', null), '');

    // (c) every tunable dimension can actually be rendered — a TUNING key with no spec
    // would print a directionless band.
    const tuneEntries = {};
    for (const m of (qdSrc.split('const TUNING = {')[1] || '').split('\n    };')[0]
        .matchAll(/(\w+):\s*\{ key: '(\w+)'(, flag: true)?/g)) {
        tuneEntries[m[1]] = { key: m[2], flag: !!m[3] };
    }
    check('3b.14 the tuning map was parsed', Object.keys(tuneEntries).length >= 15, true);
    check('3b.14 every banded tuning key has a spec',
        Object.values(tuneEntries).filter((t) => !t.flag && !specKeys.has(t.key))
            .map((t) => t.key).join('|'), '');

    // (d) the rendered line itself. Extracted VERBATIM from the module (the gate never
    // reimplements a renderer) with its closure injected, so what CI grades is what ships.
    const tlSrc = qdSrc.slice(qdSrc.indexOf('    function tuningLine(k) {'),
        qdSrc.indexOf('\n    const JUDGE_DIMS = new Set('));
    check('3b.14 tuningLine extracted', tlSrc.includes('Bands now'), true);
    // classOf is a real dependency of the line: a GUARANTEE and a MOMENT have no pass
    // line, so the renderer must ask what kind of entry it was handed. The stub mirrors
    // the module's own mapping shape (registered class, else a measurement).
    // ENTRY_CLASS is the registered-class map the module keeps; a scored dimension has no
    // entry there, which is how the line tells a measurement from a registered mechanism.
    const _clsStub = { station_grounded: 'station', stage_plan: 'stage', grounding_gate: 'control' };
    const mkTune = (thresh, failed, fallback) => new Function(
        'JUDGE_DIMS', 'TUNING', 'INFORMATIONAL', 'esc', 'fmtBand', 'ADMIN_PATH',
        '_thresh', '_threshFailed', '_threshFallback', 'classOf', 'ENTRY_CLASS',
        `${tlSrc}\nreturn tuningLine;`)(
            QD.JUDGE_DIMS, tuneEntries,
            new Set(['efficiency']), QD.esc, QD.fmtBand, 'ADMIN', thresh, failed, fallback,
            (k) => _clsStub[k] || 'dimension', _clsStub);

    const live = mkTune({ max_cost_usd: { healthy: 0.3, danger: 0.6 } }, false, {});
    // A GROUPING is not measured. Seen rendered on every moment entry: "A yes/no check —
    // it either holds or it does not. No score, and nothing to configure." — a true
    // sentence about a pass/fail DIMENSION, said about a point in the turn, which has no
    // pass line to describe. The renderer must say nothing rather than borrow the words.
    check('3b.14 a moment renders no tuning line at all',
        live('stage_plan'), '');
    check('3b.14 …and neither does a guarantee',
        live('station_grounded'), '');
    // Nor a MECHANISM: its settings are its own `you can change` / `fixed` rows. The
    // measure's sentence put a row about nothing after the turn under "after the turn",
    // and told the grounding gate — which has a mode — that it had "nothing to configure".
    check('3b.14 …nor a mechanism (its settings are its own rows)',
        live('grounding_gate'), '');
    check('3b.14 …while a measurement still reports its band',
        /Bands now:/.test(live('cost')), true);
    // The comparators arrive HTML-ESCAPED (`&gt;`), which is the correct output: the
    // line is escaped, never raw-injected. Asserting the escaped form pins both facts.
    check('3b.14 a configured metric reports its live band',
        /Bands now:.*Healthy ≤ \$0\.30 · Danger &gt; \$0\.60/.test(live('cost')), true);
    check('3b.14 …and never claims a shipped default',
        /unset by default|by default/.test(live('cost')), false);
    check('3b.14 an unconfigured metric says so plainly',
        /Bands now:.*not set.*measured, but not banded/.test(live('tokens')), true);
    // A metric the SCORER still bands must not be reported as unbanded — the same
    // over-claim, mirrored: silence about a real gate is as misleading as a false one.
    const withFb = mkTune({}, false, { min_grounded: { danger: 0.5 } });
    check('3b.14 an unset metric the code still bands names the fallback',
        /not set.*falls back to.*Danger &lt; 50%/.test(withFb('grounded_overlap')), true);
    // Pending and failed states must assert NO number.
    const pending = mkTune(null, false, null), broken = mkTune(null, true, null);
    check('3b.14 while the bands are loading the line states no figure',
        /Bands now|not set|≤|≥|&gt;|&lt;/.test(pending('cost')), false);
    check('3b.14 an unreadable band says so instead of guessing',
        /Could not read the live bands/.test(broken('cost'))
        && !/≤|≥|&gt;|&lt;/.test(broken('cost')), true);
    // A boolean expectation has no band — reporting one would invent a scale.
    check('3b.14 a flag reports on/off, never a band',
        /Currently:.*not required/.test(mkTune({}, false, {})('citations')), true);

    // (e) the code fallback has ONE source. Inlining the number in the scorer again
    // would let the Guide promise a band the scorer does not apply.
    const detSrc = fs.readFileSync(path.join(HERE, '..', 'src', 'trusted_data_agent',
        'eval', 'scorers', 'deterministic.py'), 'utf8');
    check('3b.14 the scorer fallback is a named map',
        /CODE_FALLBACK_CUTS[^=]*=\s*\{[\s\S]*?"min_grounded":\s*\{"danger":\s*0\.5\}/.test(detSrc), true);
    check('3b.14 …and is not re-inlined at the call site',
        /gate = 0\.5/.test(detSrc), false);
    check('3b.14 the Guide reads the fallback from the server, not a JS copy',
        /dflt:/.test(qdSrc), false);

    // (f) the live read must be reachable by the Guide's whole audience. Behind the
    // admin gate it would render "could not read" for every non-admin — a silent
    // downgrade to the state this section exists to remove.
    const rsrcTune = fs.readFileSync(path.join(HERE, '..', 'src', 'trusted_data_agent',
        'api', 'rest_routes.py'), 'utf8');
    const epIdx = rsrcTune.indexOf('/v1/evals/scoring-thresholds');
    check('3b.14 the live-bands endpoint exists', epIdx > 0, true);
    // The decorators up to the handler — a fixed character window broke when an @operation
    // descriptor (api/operations.py) was stacked under the route.
    const epDecor = rsrcTune.slice(epIdx, rsrcTune.indexOf('async def', epIdx));
    check('3b.14 …is authenticated but not admin-only',
        /@require_auth/.test(epDecor) && !/@require_admin|admin_required/.test(epDecor), true);
    check('3b.14 …and a saved change drops the cached bands',
        /invalidateThresholds\(\)/.test(fs.readFileSync(
            path.join(HERE, '..', 'static', 'js', 'adminManager.js'), 'utf8')), true);

    // ── 8.2b — the practice rule is GENERAL, not a SIGNED special case ──────────
    // An opt-in protection may only read `na` where nothing is owed (8.1), decided by a
    // practice signal that fails toward `warn`. BOUNDED read amber unconditionally for
    // months — the one station that could never go green.
    //
    // BOUNDED's signal is PER-AGENT (reachability), not the deployment-wide form 8.2
    // mandates for SIGNED. 8.2 exists to stop a CIRCULAR signal; reachability is
    // independent of scoping, so the coarse form was simply wrong — it flagged a Focus
    // agent no coordinator lists for an exposure it does not have.
    const U = (tag, restricted, reach) => ({
        ...P(tag, 'observe', 'observe',
            restricted ? { restricted: true, autonomous: false, constraints: ['deny writes'] }
                       : { restricted: false, autonomous: true, constraints: [] }),
        ...(reach === undefined ? {} : { runsUnattended: reach }),
    });
    const bnd = (profiles, focus) => stations(profiles, [], {
        agent: focus, dashboard: { enabled: true } }).bounded;

    check('8.2b unscoped + nothing can drive it unattended → na (dashed, nothing owed)',
        bnd([U('A', false, false)], 'A').state, 'na');
    check('8.2b unscoped + reachable unattended → warn (outstanding)',
        bnd([U('A', false, true)], 'A').state, 'warn');
    check('8.2b a scoped agent is green either way',
        bnd([U('A', true, false)], 'A').state === 'pass' &&
        bnd([U('A', true, true)], 'A').state === 'pass', true);
    // Rule 8.1: an ABSENT signal must NOT read na — a dash may never hide real exposure.
    check('8.2b an absent reachability signal fails toward warn, never na',
        bnd([U('A', false, undefined)], 'A').state, 'warn');

    // The whole point of going per-agent: two unscoped agents, only one reachable.
    // The coarse signal reported BOTH amber; only the reachable one is outstanding.
    const mixed = [U('A', false, true), U('B', false, false)];
    check('8.2b per-agent — the reachable one is amber', bnd(mixed, 'A').state, 'warn');
    check('8.2b per-agent — the unreachable one is dashed', bnd(mixed, 'B').state, 'na');
    check('8.2b estate aggregates worst-of over the SAME per-agent rule',
        bnd(mixed, null).state, 'warn');
    check('8.2b an estate whose unscoped agents are all unreachable is not amber',
        bnd([U('A', false, false), U('B', false, false)], null).state, 'na');

    // Rule 8.3: the dashed reading names WHY nothing is owed — and the amber must not
    // borrow that excuse (an over-claim of safety is the failure mode this catches).
    const bWhy = (ps, f) => (bnd(ps, f).facts || []).map((x) => (x && x.d) || '').join(' ');
    check('8.2b the dashed reading explains that nothing can drive it unattended',
        /no coordinator, schedule or flow/.test(bWhy([U('A', false, false)], 'A')), true);
    check('8.2b …and the amber reading does not claim that',
        /no coordinator, schedule or flow/.test(bWhy([U('A', false, true)], 'A')), false);
    // Rule 4.x — the estate headline must not read "all agents narrow their reach" when
    // some simply are not reachable. That would launder absence into a success claim.
    const bHead = (ps) => ((bnd(ps, null).facts || [])[0] || {}).d || bnd(ps, null).headline || '';
    check('8.2b an unreachable-unscoped estate is not reported as fully scoped',
        /All \d+ agents narrow their own reach/.test(
            JSON.stringify(bnd([U('A', false, false), U('B', false, false)], null))), false);

    // Pin the source shape: per-agent read, and the estate-wide field is GONE (a stale
    // dashboard field silently overriding the per-agent rule is exactly the regression
    // this section exists to prevent).
    check('8.2b BOUNDED reads a PER-AGENT reachability signal',
        /p\.runsUnattended !== false/.test(hsrc), true);
    check('8.2b the superseded deployment-wide field is not read',
        /autonomy_in_use/.test(hsrc), false);

    // TC-55 — the mini glyph: one segment per state, classed by that state (the roster,
    // capsule and chat-header consumers all render THIS output; it had zero assertions).
    const glIdx = rsrc.indexOf('function glyph(states, opts = {})');
    const glEnd = rsrc.indexOf('\n    function glyphInto', glIdx);
    check('TC-55 glyph extracted', glIdx > 0 && glEnd > glIdx, true);
    const glyph = new Function('esc', `${rsrc.slice(glIdx, glEnd)}\nreturn glyph;`)(escFn);
    const g = glyph(['pass', 'warn', 'danger', 'na', 'pass', 'warn', 'danger', 'na'], { size: 20 });
    check('TC-55 glyph renders one segment per station', (g.match(/tc-mini-seg/g) || []).length, 8);
    for (const st of ['pass', 'warn', 'danger', 'na']) {
        check(`TC-55 glyph carries the '${st}' state class`, g.includes(`tc-mini-${st}`), true);
    }
    check('TC-55 an empty state list degrades to 8 na segments',
        (glyph(null, {}).match(/tc-mini-na/g) || []).length, 8);

    // TC-55 — the routing summary (rendered tally): `na` is a chosen posture and must be
    // EXCLUDED from the attention counts (Rule 5.3 on the rendered surface, not the model).
    const asIdx = rsrc.indexOf('function _attentionSummary()');
    const asEnd = rsrc.indexOf('\n    // ── the side panel', asIdx);
    check('TC-55 _attentionSummary extracted', asIdx > 0 && asEnd > asIdx, true);
    // The summary now names stations from the ONE label registry (the SCORED/JUDGED
    // split on one screen came from it using the wire key) — so the probe injects the
    // renderer's REAL STATIONS array, extracted verbatim, not a stub the gate invents.
    // W5 — the declaration now READS trustGrammar.STATIONS with a literal fallback;
    // the slice spans the whole declaration (to its closing `];`).
    const regIdx = rsrc.indexOf('const STATIONS = ');
    const regEnd = rsrc.indexOf('];', regIdx) + 2;
    check('TC-55 STATIONS registry extracted', regIdx > 0 && regEnd > regIdx, true);
    const mkSummary = (stationsArr) => new Function('esc', '_model',
        `${rsrc.slice(regIdx, regEnd)}\n${rsrc.slice(asIdx, asEnd)}\nreturn _attentionSummary();`)(
        escFn, { stations: stationsArr });
    const quietSum = mkSummary([{ key: 'signed', state: 'pass' }, { key: 'bounded', state: 'na' }]);
    check('TC-55 all-quiet (incl. na) reads clear — na never counts', /All stations quiet/.test(quietSum), true);
    // W1 / Rule 3.7 — a watching station is never summarised as quiet.
    const watchSum = mkSummary([{ key: 'signed', state: 'pass' }, { key: 'grounded', state: 'watching' },
                                { key: 'guarded', state: 'watching' }]);
    check('W1 a watching ring is not "all quiet"', /All stations quiet/.test(watchSum), false);
    check('W1 …it names the watching stations and says they stop nothing',
        /2 watching — GROUNDED, GUARDED \(recording, not stopping\)/.test(watchSum), true);
    const watchMix = mkSummary([{ key: 'grounded', state: 'watching' }, { key: 'scored', state: 'danger' }]);
    check('W1 …and the watching line rides the attention summary too',
        /1 needs you now/.test(watchMix) && /1 watching — GROUNDED/.test(watchMix), true);
    const mixSum = mkSummary([
        { key: 'signed', state: 'na' }, { key: 'grounded', state: 'warn' },
        { key: 'scored', state: 'danger' }, { key: 'proven', state: 'pass' }]);
    check('TC-55 summary counts exactly the one danger', /1 needs you now/.test(mixSum), true);
    check('TC-55 the summary names stations by LABEL, never the wire key',
        /JUDGED/.test(mixSum) && !/SCORED/.test(mixSum), true);
    check('TC-55 summary counts exactly the one warn', /1 worth a look/.test(mixSum), true);
    check('TC-55 summary names the danger station', /JUDGED/.test(mixSum), true);
    // V2 — the tally is three rows (attention · quiet · absent). The na station is never an
    // attention NAME (Rule 5.3: a chosen posture is not an alarm) — it is named in the absent row.
    const attnRow = (mixSum.match(/<div class="tc-group">[\s\S]*?<\/div>/) || [''])[0];
    check('TC-55 the na station is never named in the attention row', /SIGNED/.test(attnRow), false);
    check('V2 …but it is named, honestly, in the absent row', /<b>absent<\/b>[\s\S]*SIGNED/.test(mixSum), true);
    // rename: the summary must render the LABEL (Judged), never the wire key. The
    // previous form of this row asserted the OPPOSITE — "derived from the key, so it
    // must say SCORED" — which pinned the exact defect the operator's screenshot then
    // exposed: SCORED in the summary beside a JUDGED plate. A gate row can enshrine a
    // bug as faithfully as it prevents one.
    check('rename: the routing summary renders the label, not the wire key',
        /JUDGED/.test(mkSummary([{ key: 'scored', state: 'danger' }])), true);

    // TC-8 — both attributes of an estate reading describe ONE subject. A system-scope
    // dashboard (per-user-keyed cells + foreign users' cells) must produce estate stations
    // BYTE-IDENTICAL to the same self cells seen in my-scope shape — foreign cells change
    // nothing on the caller's ring.
    const selfCells = {
        A: { turns: 6, failures: 1, tag: 'A', dimensions: { scope_adherence: 0.8 }, dimension_counts: { scope_adherence: 5 } },
        B: { turns: 3, failures: 0, tag: 'B' },
    };
    const twoProfiles = [P('A', 'enforce', 'enforce'), P('B', 'observe', 'observe')];
    const myScope = stations(twoProfiles, [], {
        dashboard: { enabled: true, by_agent: selfCells,
                     total_spans: 9, spans_with_failure: 1 } });
    const sysScope = stations(twoProfiles, [], {
        dashboard: { enabled: true, self_uuid: 'me',
                     by_agent: {
                         'A::me': { ...selfCells.A, user_uuid: 'me' },
                         'B::me': { ...selfCells.B, user_uuid: 'me' },
                         'A::other': { turns: 40, failures: 39, tag: 'A', user_uuid: 'other' },
                         'X::other': { turns: 11, failures: 11, tag: 'X', user_uuid: 'other' },
                     },
                     total_spans: 60, spans_with_failure: 51 } });
    for (const key of STATION_KEYS) {
        check(`TC-8 ${key}: estate reading is subject-invariant (foreign cells change nothing)`,
            JSON.stringify(sysScope[key]), JSON.stringify(myScope[key]));
    }

    done();
}

// ── §9 · the form rules (R-1) ────────────────────────────────────────────────
// Promoted from a prototype no CI read. Three are enforced against shipped code;
// the rest are PRESENCE-KEYED — inert until the construct appears, failing the
// moment it appears without conforming. A forward rule with no gate is a rule that
// will be broken, and the gate not yet being able to run is not a reason to omit it.
{
    section('§9 form rules — how a reading is drawn (R-1)');
    const done = report('§9 form rules (R-1) — promoted from the prototype');

    const CSS = fs.readFileSync(path.join(HERE, '..', 'static', 'css', 'trust.css'), 'utf8');
    const SURFACES = ['trustJourney', 'answerCapsule', 'trustCircle', 'agentPerformanceHandler',
                      'agentAnatomy', 'trustBadge', 'qualityDimensions', 'agentKpiCharts']
        .map((n) => [n, fs.readFileSync(
            path.join(HERE, '..', 'static', 'js', 'handlers', `${n}.js`), 'utf8')]);

    // Rule 9.4 — weight before colour. The four states must be pairwise distinct on a
    // NON-colour property, so attention survives a colourblind reader and reads before
    // the hue does. Extracted from the real rules; a state block that vanishes fails.
    // Extract every state block in ONE pass. The first attempt built a RegExp from a
    // template string and mis-escaped it, so it matched nothing and reported `null` for
    // all four — a checker that silently sees nothing is the exact failure mode this
    // gate exists to prevent, which is why the extractability of each block is asserted
    // separately from the property it then checks.
    const STATE_BLOCKS = {};
    for (const m of CSS.matchAll(/\.tj-stage\.tj-([a-z]+)\s*\{([^}]*)\}/g)) {
        if (!(m[1] in STATE_BLOCKS)) STATE_BLOCKS[m[1]] = m[2];
    }
    const stateBlock = (cls) => (cls in STATE_BLOCKS ? STATE_BLOCKS[cls] : null);
    // The non-colour signature: everything that is NOT a hue. `background` counts only
    // for its FILL SHAPE (a gradient is a half-fill, a flat value is not), never its hue.
    const signature = (body) => {
        if (body == null) return null;
        const bw = (body.match(/border-width:\s*([^;]+)/) || [, '1.5px'])[1].trim();
        const bs = (body.match(/border-style:\s*([^;]+)/) || [, 'solid'])[1].trim();
        const grad = /background:\s*linear-gradient/.test(body);
        const noFill = !/background:/.test(body);
        return `w=${bw} s=${bs} fill=${grad ? 'half' : (noFill ? 'none' : 'full')}`;
    };
    const STATES = ['pass', 'warn', 'danger', 'na', 'watching'];
    const sigs = {};
    for (const st of STATES) {
        const body = stateBlock(st);
        check(`TC-90 the '${st}' state block is extractable from trust.css`, body != null, true);
        sigs[st] = signature(body);
    }
    for (let i = 0; i < STATES.length; i += 1) {
        for (let j = i + 1; j < STATES.length; j += 1) {
            const a = STATES[i], b = STATES[j];
            check(`TC-90 '${a}' vs '${b}' differ without hue (${sigs[a]} ≠ ${sigs[b]})`,
                sigs[a] !== sigs[b] && sigs[a] != null, true);
        }
    }

    // Rule 9.5 — three colour channels. A hardcoded literal renders as a reading, is
    // invisible to the token system, and breaks in light mode. Currently zero across
    // every surface, so this is a regression gate rather than an aspiration.
    const TW = /\b(?:text|bg|border|from|to|via)-(?:red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|slate|gray|zinc|neutral|stone)-\d{2,3}\b/g;
    for (const [name, src] of SURFACES) {
        const hits = [...new Set(src.match(TW) || [])];
        check(`TC-91 ${name}.js uses no hardcoded colour utility${hits.length ? ` (${hits.slice(0, 4).join(', ')})` : ''}`,
            hits.length, 0);
    }
    // TC-91b — the OTHER half of Rule 9.5 the contract states but the gate never measured:
    // a trust-STATE colour as a bare hex literal. The rule targets the specific `--trust-*`
    // VALUES (all three shade-families, both themes) — not "any #hex" — because that is what
    // renders the wrong shade in light mode, and it cleanly excludes IFOC identity palettes,
    // chart-series colours, SVG/anchor ID refs and `&#NNNN;` HTML entities (none of which use
    // these values). Allowed only inside a `var(--…, #hex)` fallback (the tokenized form) or on
    // a line marked `hex-palette` (a categorical/identity palette value, e.g. a work-mix task
    // colour where `#10b981` means "reasoning", not "pass"). Block comments are stripped first
    // (prose about a hex is not a rendered hex — a lesson this file already carries).
    const STATE_HEX = /#(?:10b981|34d399|059669|f59e0b|fbbf24|b45309|ef4444|f87171|dc2626)(?:[0-9a-fA-F]{2})?\b/gi;
    for (const [name, src] of SURFACES) {
        const noBlock = src.replace(/\/\*[\s\S]*?\*\//g, '');   // prose is not a rendering
        const bad = [];
        noBlock.split(/\n/).forEach((ln, i) => {
            if (/hex-palette/.test(ln)) return;                 // a marked identity/series palette
            const code = ln.replace(/\/\/.*$/, '')              // strip line comments
                           .replace(/var\(--[a-z-]+,\s*#[0-9a-fA-F]{3,8}\)/gi, '');  // allow token fallbacks
            const m = code.match(STATE_HEX);
            if (m) bad.push(`${i + 1}:${m[0]}`);
        });
        check(`TC-91b ${name}.js uses no bare trust-state hex${bad.length ? ` (${bad.slice(0, 4).join(', ')})` : ''}`,
            bad.length, 0);
    }
    // …and the three channels are the only ones trust.css defines.
    const channels = [...new Set([...CSS.matchAll(/--trust-([a-z-]+)\s*:/g)].map((m) => m[1]))];
    // The four attention states, their fills, and the four SIGNED seal shapes — the one
    // sanctioned second channel (Rule 8.4: integrity BORROWS TrustBadge's vocabulary
    // rather than inventing a per-station one). Anything else is a fourth meaning nobody
    // defined, so this is an allow-list and never a widened pattern.
    const CHANNEL_TOKENS = new Set([
        'pass', 'warn', 'danger', 'na',
        'pass-bg', 'warn-bg', 'danger-bg', 'na-bg',
        'seal-trusted', 'seal-changed', 'seal-unsigned', 'seal-unattested',
    ]);
    const stray = channels.filter((c) => !CHANNEL_TOKENS.has(c));
    check(`TC-91 trust.css defines no colour outside the three channels${stray.length ? ` (${stray.join(', ')})` : ''}`,
        stray.length, 0);

    // Rule 9.7 — hover must not resize. The readings region must not stretch its row:
    // a pointer crossing the dial reflowed the panel, moving the element out from under
    // the pointer, which fed back into a screen-wide flicker.
    const tcRoot = (CSS.match(/\.tc-root\s*\{([^}]*)\}/) || [, ''])[1];
    check('TC-93 .tc-root is extractable (the fix lives there)', tcRoot.length > 0, true);
    check('TC-93 the readings row does not stretch to its tallest child',
        /align-items:\s*(start|flex-start)/.test(tcRoot), true);
    check('TC-93 …and specifically is NOT centred (the shape that flickered)',
        /align-items:\s*center/.test(tcRoot), false);

    // ── presence-keyed rules ──────────────────────────────────────────────────
    // Rule 9.1 — pending is not absence. No `pending` state exists yet, so the fold
    // would happen by default and silently. This bites the moment one appears.
    // Rule 9.1 now has a real subject: the dial renders `pending` for a turn whose
    // score has not arrived. It keys on whichever class family exists, because the rule
    // is about the STATE, not about one renderer's naming.
    const pendingCss = (CSS.match(/\.(?:tj-stage\.tj|ad-seg)-pending\s*\{([^}]*)\}/) || [, null])[1];
    if (pendingCss != null) {
        check('TC-94 pending reuses no --trust-na token',
            /--trust-na/.test(pendingCss), false);
        // …and it must not merely be `na` wearing a different name.
        const naBody = (CSS.match(/\.ad-seg-na\s*\{([^}]*)\}/) || [, ''])[1];
        check('TC-94 pending is visually distinct from na',
            pendingCss.replace(/\s+/g, '') !== naBody.replace(/\s+/g, ''), true);
        check('TC-94 pending carries its own dash pattern (in-flight, not absent)',
            /stroke-dasharray/.test(pendingCss), true);
    } else {
        check('TC-94 (inert) no pending state yet — Rule 9.1 gate armed', true, true);
    }

    // Rule 9.2 — a plus may only appear where a picker exists. A mark must not promise
    // what its surface cannot do.
    for (const [name, src] of SURFACES) {
        const hasPlus = /class="[^"]*\b(?:tc|an)-plus\b/.test(src) || /\bPLUS_GLYPH\b/.test(src);
        if (!hasPlus) continue;
        const hasPicker = /pickable|an-pick-row|openPicker/.test(src);
        check(`TC-95 ${name}.js renders the plus and therefore exposes a picker`,
            hasPicker, true);
    }
    check('TC-95 (inert if unused) Rule 9.2 gate armed', true, true);

    // Rule 9.3 — ticks declare their meaning in ONE place per surface.
    for (const [name, src] of SURFACES) {
        if (!/\b(?:tc|an|ad)-tick\b/.test(src)) continue;
        check(`TC-96 ${name}.js declares what its ticks mean`,
            /tick[^\n]{0,80}(mean|marks|indicates)/i.test(src), true);
    }
    check('TC-96 (inert if unused) Rule 9.3 gate armed', true, true);

    // Rule 9.6 — locate, never filter. A spine click scrolls to its target; it must not
    // hide a sibling. `data-scroll` is the shipped spine route.
    for (const [name, src] of SURFACES) {
        if (!/data-scroll/.test(src)) continue;
        const handler = (src.match(/data-scroll[\s\S]{0,900}?scrollIntoView/) || [null])[0];
        check(`TC-92 ${name}.js's spine route scrolls to its target`, handler != null, true);
        check(`TC-92 ${name}.js's spine route hides nothing`,
            /display\s*=\s*['"]none|\.remove\(\)|hidden\s*=\s*true/.test(handler || ''), false);
    }

    done();
}

// ── PROP-002 · one ring grammar — the four channels, six states, one vocabulary ──
{
    section('PROP-002 — one ring grammar (four channels · six states · one vocabulary)');
    const done = report('PROP-002 — the unified grammar holds on every surface');
    const CSS = fs.readFileSync(path.join(HERE, '..', 'static', 'css', 'trust.css'), 'utf8');
    const hsrc = fs.readFileSync(SRC, 'utf8');
    const rsrc = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'handlers', 'trustCircle.js'), 'utf8');
    const dsrc = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'handlers', 'assuranceDial.js'), 'utf8');
    const usrc = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'ui.js'), 'utf8');
    const qsrc = fs.readFileSync(QD_SRC, 'utf8');
    const jsrc = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'handlers', 'trustJourney.js'), 'utf8');
    const nsrc = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'notifications.js'), 'utf8');

    // P2-1 — the module itself: six states, both rank tables, the word lookup.
    check('P2-1 seven states, exactly', TG.STATES.join(','), 'pass,warn,danger,na,pending,unknown,watching');
    check('P2-1 word() covers watching', TG.word('watching'), 'watching');
    check('P2-1 watching ranks between na and pass', TG.ATTN_RANK.na < TG.ATTN_RANK.watching
        && TG.ATTN_RANK.watching < TG.ATTN_RANK.pass, true);
    check('P2-1 ATTN_RANK orders na below pass', TG.ATTN_RANK.na < TG.ATTN_RANK.pass, true);
    check('P2-1 TURN_RANK: unknown outranks pass, pending sits between',
        TG.TURN_RANK.unknown > TG.TURN_RANK.pass && TG.TURN_RANK.pending > TG.TURN_RANK.pass
        && TG.TURN_RANK.pending < TG.TURN_RANK.unknown && TG.TURN_RANK.danger > TG.TURN_RANK.warn, true);
    check('P2-1 word() covers the absence family', TG.word('pending'), 'not in yet');
    check('P2-1 word() for unknown', TG.word('unknown'), 'not recorded');
    check('P2-1 word() yields no guess for a bogus state', TG.word('bogus'), '');
    check('P2-1 bandColor aliases bands onto the trust tokens',
        /--trust-pass/.test(TG.bandColor('healthy')) && /--trust-warn/.test(TG.bandColor('review'))
        && /--trust-danger/.test(TG.bandColor('danger')), true);
    check('P2-1 severity words are the attention vocabulary',
        TG.SEVERITY_WORD.danger === TG.ATTN_WORD.danger && TG.SEVERITY_WORD.warning === TG.ATTN_WORD.warn, true);

    // P2-2 — the dial reads the shared TURN_RANK (fallback pinned equal) and speaks
    // ONE pending word ("still coming in" and the dead "not recorded" fallback are gone).
    check('P2-2 dial RANK reads TrustGrammar.TURN_RANK',
        /const RANK = \(window\.TrustGrammar && window\.TrustGrammar\.TURN_RANK\)/.test(dsrc), true);
    const dialRankFb = (dsrc.match(/TURN_RANK\)\s*\|\|\s*\{([^}]*)\}/) || [, ''])[1];
    const fbPairs = Object.fromEntries([...dialRankFb.matchAll(/(\w+):\s*(\d+)/g)].map((m) => [m[1], +m[2]]));
    check('P2-2 dial fallback rank equals the canonical TURN_RANK',
        JSON.stringify(fbPairs), JSON.stringify(TG.TURN_RANK));
    check('P2-2 "still coming in" is retired', /still coming in/.test(dsrc), false);
    check('P2-2 the routing line says "not in yet"', /\$\{n\('pending'\)\} not in yet/.test(dsrc), true);
    check('P2-2 word() resolves the absence family via TrustGrammar first',
        /TG && TG\.word/.test(dsrc) || /TrustGrammar[\s\S]{0,80}\.word/.test(dsrc), true);
    // The tick grammar reaches assistive tech: I and II are different facts.
    check('P2-2 aria distinguishes I from II (TICK_ARIA)', /TICK_ARIA/.test(dsrc), true);
    check('P2-2 the collapsed ", has detail" for both marks is gone',
        /const detail = d\.marks \? ', has detail' : ''/.test(dsrc), false);

    // P2-3 (amended by the dot consolidation) — ONE mark family: DOTS. The acted
    // engraving is two muted state-coloured dots; the owed act is the single accent
    // dot. Ticks are retired ENTIRELY — the engraved ticks were visually identical
    // to the ring's retired owed double-tick, so the old grammar looked alive
    // beside the new one (the operator confusion that forced the amendment).
    check('P2-3 the ring renders acted DOTS (ticks retired)', /tc-acted-dot/.test(rsrc), true);
    check('P2-3 no tick mark remains on the ring', /tc-acted-tick/.test(rsrc), false);
    check('P2-3 …nor in the CSS', /tc-acted-tick/.test(CSS), false);
    check('P2-3 the ring renders the owed dot', /tc-owed-dot/.test(rsrc), true);
    check('P2-3 the owed double-tick is gone from the renderer', /tc-owed-tick/.test(rsrc), false);
    check('P2-3 …and from the CSS', /tc-owed-tick/.test(CSS), false);
    check('P2-3 acted dots toggle on the MODEL fact d.acted',
        /tc-acted-dot'\)\.forEach[\s\S]{0,120}d\.acted/.test(rsrc), true);
    check('P2-3 the owed dot toggles on d.owed',
        /tc-owed-dot'\)\.forEach[\s\S]{0,120}d\.owed/.test(rsrc), true);
    const owedDotCss = (CSS.match(/\.tc-owed-dot\s*\{([^}]*)\}/) || [, ''])[1];
    check('P2-3 the owed dot rides the ACCENT channel, never a reading token',
        /--teradata-orange/.test(owedDotCss) && !/--trust-/.test(owedDotCss), true);
    // The legend must EXPLAIN both mark channels — asserted as the property, and as
    // AGREEMENT with the one vocabulary rather than a literal. This pinned the exact
    // string 'a gate acted', so correcting a noun that misdescribed the mark (nine
    // mechanisms set `acted`; only four are gates) broke the assertion while the
    // property held — and it could not have caught the legend and the aria-label
    // drifting apart, which is the failure that actually matters here.
    check('P2-3 the legend explains the marks channel',
        /tc-legend-marks/.test(rsrc) && /an act is owed/.test(rsrc), true);
    check('P2-3 the acted legend reads the ONE vocabulary (no second copy of the word)',
        /\$\{_actedWord\(\)\}/.test(rsrc) && /TG\.TICK_ARIA\[2\]/.test(rsrc), true);
    check('P2-3 …and that vocabulary describes the mark as a MECHANISM, not a gate',
        /'a mechanism acted'/.test(fs.readFileSync(TG_SRC, 'utf8')) && !/a gate acted/i.test(fs.readFileSync(TG_SRC, 'utf8')), true);
    // The assembler carries both facts; a call site cannot invent them.
    check('P2-3 _assemble carries acted', /acted: !!o\.acted/.test(hsrc), true);
    check('P2-3 _assemble carries owed', /owed: !!o\.owed/.test(hsrc), true);

    // P2-3b — fixture: a focused agent whose inbox attribution says a grounding
    // gate acted gets the II ticks on GROUNDED; the estate never inherits them.
    const actedFix = stations([P('A', 'enforce', 'enforce')], [], {
        agent: 'A', dashboard: { enabled: true },
        byAgent: { A: { turns: 3 } },
        inboxByTag: { A: { open: 0, derived: 1, derivedDanger: 0, top: '',
                           stations: { grounded: 1 } } },
    });
    check('P2-3b focused GROUNDED carries acted from the inbox attribution',
        actedFix.grounded.acted, true);
    check('P2-3b GUARDED (no observation) stays untacked', !!actedFix.guarded.acted, false);
    const estateFix = stations([P('A', 'enforce', 'enforce')], [], {
        inboxByTag: { A: { open: 0, derived: 1, derivedDanger: 0, top: '',
                           stations: { grounded: 1 } } },
    });
    check('P2-3b the estate ring never inherits one agent\'s acted ticks',
        !!estateFix.grounded.acted, false);

    // P2-4 — the six states render EVERYWHERE they can be fed. The mini glyph is
    // handed dial states (chat header / capsule): a state without a CSS class
    // rendered as an INVISIBLE segment (fact loss, Rule 9.8).
    check('P2-4 mini glyph has a pending class', /\.tc-mini-pending/.test(CSS), true);
    check('P2-4 mini glyph has an unknown class', /\.tc-mini-unknown/.test(CSS), true);
    const miniPending = (CSS.match(/\.tc-mini-pending\s*\{([^}]*)\}/) || [, ''])[1];
    check('P2-4 mini pending is not --trust-na in disguise (Rule 9.1)',
        /--trust-na/.test(miniPending), false);
    check('P2-4 ring band has a pending state',
        /data-tc-state="pending"[^{]*\.tc-bandseg/.test(CSS), true);
    check('P2-4 panel chip has pending + unknown states',
        /\.tc-state-pending/.test(CSS) && /\.tc-state-unknown/.test(CSS), true);
    // W1 — `watching` renders on every surface a state can be fed to, in the QUIET colour
    // (nothing needs you) and HOLLOW (nothing protected): never amber, never a fill, never
    // dashed — dashed is the absence family's, and a dashed watching box read as off.
    for (const sel of ['\\.tc-mini-watching', 'data-tc-state="watching"\\][^{]*\\.tc-bandseg',
                       '\\.tc-state-watching', '\\.tc-leg-watching', '\\.tj-stage\\.tj-watching',
                       '\\.an-tok-watching', '\\.an-chip-watching', '\\.trust-chip\\.tc-watching']) {
        const block = (CSS.match(new RegExp(`${sel}\\s*\\{([^}]*)\\}`)) || [, null])[1];
        check(`W1 watching is drawn by ${sel.replace(/\\\\/g, '')}`, block != null, true);
        check(`W1 …and ${sel.replace(/\\\\/g, '')} is quiet green, never amber or red`,
            block != null && /--trust-pass/.test(block) && !/--trust-(warn|danger)/.test(block), true);
        check(`W1 …and ${sel.replace(/\\\\/g, '')} is hollow, never dashed`,
            block != null && !/dasharray|dashed/.test(block) && !/trust-pass-bg/.test(block), true);
    }
    // The node box and band are THINNER than pass — the non-colour difference (Rule 9.4).
    const w = (sel) => +((CSS.match(new RegExp(`${sel}[^{]*\\{[^}]*stroke-width:\\s*([0-9.]+)`)) || [, '0'])[1]);
    check('W1 the watching band is thinner than the pass band', w('data-tc-state="watching"\\][^{]*\\.tc-bandseg') < w('data-tc-state="pass"\\][^{]*\\.tc-bandseg'), true);
    check('W1 the mini watching segment is thinner than a pass segment', w('\\.tc-mini-watching') < 2.6, true);

    // P2-5 — ONE colour vocabulary on the Quality surfaces: band colours are the
    // trust tokens, never a private theme-invariant hex (Rule 9.5 reaching the
    // two files the TC-91 sweep never covered).
    check('P2-5 ui.js chips read TrustGrammar.bandColor',
        /window\.TrustGrammar && window\.TrustGrammar\.bandColor/.test(usrc), true);
    check('P2-5 ui.js has no hardcoded band-colour map left',
        /BAND_COLOR = \{ healthy: '#/.test(usrc), false);
    check('P2-5 the handler ledger BAND_C uses the tokens',
        /BAND_C = \{\s*\n?\s*healthy: 'var\(--trust-pass/.test(hsrc), true);
    check('P2-5 qualityDimensions BAND uses the tokens',
        /healthy:\s*\{ c: 'var\(--trust-pass/.test(qsrc), true);
    // The judged marker rides the ·judge text tag; dashes belong to the absence
    // family (a dashed GREEN chip contradicted the shape grammar).
    check('P2-5 no judge-dash ternary left in ui.js',
        /kind === 'judge' \? 'dashed'/.test(usrc), false);
    check('P2-5 no judge-dash ternary left in the handler',
        /kind === 'judge' \? 'dashed'/.test(hsrc), false);
    check('P2-5 the ·judge text tag remains on both surfaces',
        /·judge/.test(usrc) && /·judge/.test(hsrc), true);

    // P2-6 — the inbox speaks the shared vocabulary: wire severities never reach
    // a chip (Rule 3.0 applied to the one surface that still printed them), and
    // event→station attribution has ONE mapping (bucket tally ∧ live pulse).
    check('P2-6 _sevWord reads TrustGrammar.SEVERITY_WORD', /SEVERITY_WORD/.test(hsrc), true);
    check('P2-6 the severity chip renders the word, not the wire string',
        /_sevChip[\s\S]{0,220}_sevWord\(sev\)/.test(hsrc), true);
    check('P2-6 one event→station mapping', (hsrc.match(/function _stationOfEvent\(/g) || []).length, 1);
    check('P2-6 the live pulse routes through it',
        /const station = _stationOfEvent\(t\)/.test(hsrc), true);
    check('P2-6 the bucket tally routes through it',
        /_stationOfEvent\(d\.event_type\)/.test(hsrc), true);
    check('P2-6 the badge trusts the server\'s pending count',
        /reviews\.pending === 'number'/.test(hsrc), true);
    // P2-6 — the completeness invariant: a DERIVED observation must be ack-able, or a
    // danger item nobody can disposition is permanently unresolvable residue. This
    // grepped two function names, so unifying the two ack paths into one batch executor
    // broke it while the property held perfectly. Asserted on the rendered row instead:
    // a derived item offers an ack keyed by (session, turn, event_type) — the sealed
    // trace is never rewritten, the acknowledgment is a disposition ABOUT it.
    check('P2-6 derived observations are ack-able (completeness invariant)',
        /data-agent-action="inbox-ack-batch"/.test(hsrc)
        && /session_id: x\.session_id/.test(hsrc) && /event_type: x\.event_type/.test(hsrc), true);

    // P2-6b — the owed dot is ACTIONABLE at estate scope (context-sensitive
    // support): the agents owing an act are NAMED (Rule 5.4) and offered as
    // focus routes that land the ring on the agent with the station pinned —
    // where the state-derived act (Rule 8.5) is offered. Worst first: `changed`
    // (a detected discrepancy) outranks outstanding work.
    const owedEstate = stations(
        [P('A', 'observe', 'observe'), P('B', 'observe', 'observe'), P('C', 'observe', 'observe')],
        [T('A', 'unsigned'), T('B', 'changed'), T('C', 'trusted')]);
    const sActs = owedEstate.signed.actions || [];
    check('P2-6b estate SIGNED offers focus routes onto the owing agents',
        sActs.length >= 2, true);
    check('P2-6b worst first — changed outranks unsigned',
        (sActs[0] || {}).action, 'focus-agent:B');
    check('P2-6b the leading route is primary', !!(sActs[0] || {}).primary, true);
    check('P2-6b the route label names the act that is owed (Rule 8.5)',
        /changed after sign-off/.test((sActs[0] || {}).label || ''), true);
    check('P2-6b the unsigned agent routes too',
        sActs.some((a) => a.action === 'focus-agent:A' && /sign-off owed/.test(a.label)), true);
    const sEv = (owedEstate.signed.facts[3] || {}).d || '';
    check('P2-6b Evidence names the owing agents', /owed by /.test(sEv) && /@B/.test(sEv) && /@A/.test(sEv), true);
    check('P2-6b …with the act each owes', /changed after sign-off/.test(sEv) && /sign-off owed/.test(sEv), true);
    // A deployment that never signs owes nothing — no routes, no naming (Rule 8.2).
    const virginEstate = stations([P('A', 'observe', 'observe')], [T('A', 'unsigned')]);
    check('P2-6b signing not in use → no focus routes', (virginEstate.signed.actions || []).length, 0);
    // The dispatcher lands the route with the SAME station pinned.
    check('P2-6b the dispatcher handles focus-agent and re-pins the station',
        /action\.startsWith\('focus-agent:'\)/.test(hsrc)
        && /TrustCircle\.pin\)\s*window\.TrustCircle\.pin\(key\)/.test(hsrc), true);
    // REVIEWED at estate scope names WHERE the open work lives.
    const revEstate = stations([P('A', 'review', 'observe'), P('B', 'observe', 'observe')], [], {
        inboxBadgeCount: 3,
        inboxByTag: { A: { open: 2, derived: 0, derivedDanger: 0, top: '' },
                      B: { open: 1, derived: 0, derivedDanger: 0, top: '' } },
    });
    const rEv = (revEstate.reviewed.facts[3] || {}).d || '';
    check('P2-6b REVIEWED estate evidence names the holders',
        /open on /.test(rEv) && /@A \(2\)/.test(rEv) && /@B \(1\)/.test(rEv), true);

    // P2-9 — the estate Execution qualifier EXPANDS its anchor (Rule 3.4): it may
    // not open by restating the anchor's own digits (`1/3` beside `1/3 — …` read
    // as "1/3 1/3") and may not close with the COMPLEMENT numerator ("2 agents
    // short" — a second numerator for one population; Evidence names those agents
    // instead, in plain words: "below the healthy floor on …").
    const dupFix = stations(
        [P('A', 'enforce', 'enforce'), P('B', 'observe', 'observe'), P('C', 'observe', 'observe')], [], {
            dashboard: { enabled: true, by_agent: {
                A: { turns: 4, failures: 0, tag: 'A',
                     dimensions: { grounded_overlap: 1.0, output_sanity: 1.0 },
                     dimension_counts: { grounded_overlap: 4, output_sanity: 4 } },
                B: { turns: 3, failures: 3, tag: 'B',
                     dimensions: { grounded_overlap: 0.2, output_sanity: 0.3 },
                     dimension_counts: { grounded_overlap: 3, output_sanity: 3 } },
                C: { turns: 2, failures: 2, tag: 'C',
                     dimensions: { grounded_overlap: 0.4, output_sanity: 0.4 },
                     dimension_counts: { grounded_overlap: 2, output_sanity: 2 } },
            }, total_spans: 9, spans_with_failure: 5 } });
    let p29Rates = 0;
    for (const key of STATION_KEYS) {
        const ex = (dupFix[key] || {}).facts && dupFix[key].facts[1];
        if (!ex || !ex.v || !/^\d+\/\d+$/.test(ex.v)) continue;
        p29Rates += 1;
        check(`P2-9 ${key}: qualifier does not restate the anchor's digits`,
            String(ex.d || '').startsWith(ex.v), false);
        check(`P2-9 ${key}: no complement-numerator tail ("N agents short")`,
            /\d+ agents? short\b/.test(ex.d || ''), false);
    }
    // ≥1 after the lane cut: GROUNDED and GUARDED no longer render an estate agent
    // ratio (their execution is their mechanisms' acts), so JUDGED is the station
    // whose anchor this rule now governs. The floor still guards the real risk — a
    // fixture that produced NO rate anchor would vacuously pass the loop above.
    check('P2-9 the fixture actually produced estate rate anchors', p29Rates >= 1, true);
    check('P2-9 no "N agents short" template survives anywhere in the handler',
        /, 'agent'\)\} short`/.test(hsrc), false);
    // The estate shortfall wording now lives on the station that carries the rate;
    // GROUNDED/GUARDED no longer produce one, so the de-jargoned form is asserted
    // where it renders rather than where it used to.
    check('P2-9 the shortfall is explained where it is named (below the healthy floor)',
        /below the healthy floor on/.test(hsrc), true);

    // P2-10 — the severity CONSTRUCT + per-station reporting + the colour fold
    // (PROP-002b). One level, one behaviour; assignments filter presentation,
    // never creation; danger is the floor.
    check('P2-10 the construct carries the danger floor',
        TG.SEVERITY_CONSTRUCT && TG.SEVERITY_CONSTRUCT.danger.floor === true, true);
    check('P2-10 info never badges and never folds',
        TG.SEVERITY_CONSTRUCT.info.badge === false && TG.SEVERITY_CONSTRUCT.info.fold === false, true);
    check('P2-10 the construct speaks the attention vocabulary',
        TG.SEVERITY_CONSTRUCT.danger.word === TG.ATTN_WORD.danger
        && TG.SEVERITY_CONSTRUCT.warning.word === TG.ATTN_WORD.warn, true);
    check('P2-10 danger always surfaces — even on a danger-only station',
        TG.severitySurfaces('danger', 'guarded', { guarded: 'danger' }), true);
    check('P2-10 warning is filtered on a danger-only station',
        TG.severitySurfaces('warning', 'guarded', { guarded: 'danger' }), false);
    check('P2-10 the default level passes warning',
        TG.severitySurfaces('warning', 'guarded', {}), true);
    check('P2-10 info surfaces only where a station reports everything',
        TG.severitySurfaces('info', 'proven', { proven: 'all' }) === true
        && TG.severitySurfaces('info', 'proven', {}) === false, true);
    // The fold — an OPEN danger item on a healthy-rate station raises it to red,
    // leads with the inbox route (Rule 4b.8), keeps the station route (4b.4),
    // names the driver and the count; the measurement is never rewritten.
    const foldCell = { turns: 5, dimensions: { grounded_overlap: 1.0 },
                       dimension_counts: { grounded_overlap: 5 } };
    const foldFix = stations([P('A', 'enforce', 'enforce')], [], {
        agent: 'A', dashboard: { enabled: true }, byAgent: { A: foldCell },
        inboxByTag: { A: { open: 1, derived: 1, derivedDanger: 1, top: '',
                           stations: { grounded: 1 },
                           openItems: { grounded: { state: 'danger', count: 2 } } } },
    });
    check('P2-10 an open danger item raises a healthy station to danger',
        foldFix.grounded.state, 'danger');
    check('P2-10 the Why slot names the driver (open items)',
        /open items/.test((foldFix.grounded.facts[2] || {}).v || ''), true);
    check('P2-10 the leading route is the inbox', (foldFix.grounded.cta || {}).action, 'inbox');
    check('P2-10 the station route survives beneath it (Rule 4b.4)', !!foldFix.grounded.cta2, true);
    check('P2-10 Evidence names the open count',
        /2 open inbox items/.test((foldFix.grounded.facts[3] || {}).d || ''), true);
    const noFold = stations([P('A', 'enforce', 'enforce')], [], {
        agent: 'A', dashboard: { enabled: true }, byAgent: { A: foldCell } });
    check('P2-10 no open items → the measurement stands (byte-identical baseline)',
        noFold.grounded.state, 'pass');
    // Inbox surfaces: station chips, the severity view filter (construct words),
    // the severity dot beside the word chip, and shortfall→station routing.
    check('P2-10 inbox rows wear their station', /_rowStLabel/.test(hsrc), true);
    check('P2-10 severity view filter speaks the construct words',
        /inbox-sev-filter/.test(hsrc) && /needs you now \(/.test(hsrc), true);
    // Behavioural, not a grep for `_sevDot(e.severity)`: that pinned ONE rendering of
    // severity, so the inbox redesign broke it while the property (severity legible
    // without hue) was what actually needed protecting — and the first draft of that
    // redesign violated the property while a source grep would have been satisfied by
    // any surviving call. The property is asserted where the row is executed, below.
    check('P2-10 shortfall events route to their NAMED station',
        /station_shortfall_/.test(hsrc), true);
    // P2-10b — the inbox's agent lens + bulk dispositions (operator-requested).
    check('P2-10b agent filter pills draw from the ONE IFOC map',
        /inbox-agent-filter/.test(hsrc) && /_agentIfocColor/.test(hsrc), true);
    // Behavioural: this grepped two exact call expressions, so routing the tag through
    // the shared chip broke it while the property — a row tag wears its IFOC colour —
    // held better than before. Both row kinds must build their tag with the ONE chip.
    check('P2-10b both row kinds render the platform tag chip',
        /const tagHtml = _agentTagChip\(/.test(hsrc)
        && /const rTagHtml = _agentTagChip\(/.test(hsrc), true);
    {   // …whose colour resolves from the IFOC single source. Extract the resolver and
        // read its BODY, so a comment growing between the name and the call cannot
        // decide whether this passes (a distance-bounded regex nearly did).
        const i = hsrc.indexOf('function _agentIfocColor(');
        const body = hsrc.slice(i, hsrc.indexOf('\n    }', i))
            .replace(/\/\/.*$/gm, '');
        check('P2-10b …whose colour resolves through the IFOC palette',
            /ifoc\(/.test(body), true);
        check('P2-10b …and not from a per-profile colour field that could disagree',
            /\.color\b/.test(body.replace(/ifoc\([^)]*\)\.color/g, '')), false);
    }
    check('P2-10b view filters never mutate the model (attribution reads unfiltered)',
        /pendingReviewsAll\.forEach/.test(hsrc), true);
    check('P2-10b bulk disposition acts on the SHOWN set only',
        /_shownReviewIds/.test(hsrc) && /review-approve-all/.test(hsrc)
        && /review-uphold-all/.test(hsrc), true);
    check('P2-10b bulk RELEASE is impossible by construction',
        /review-release-all/.test(hsrc), false);

    // P2-7 — the live banner TONE honours severity (every trust banner was amber).
    check('P2-7 notifications maps severity → tone',
        /ten\.severity === 'danger' \? 'error'/.test(nsrc), true);

    // P2-8 — the strip's rank agrees with ATTN_RANK ordering (na below pass).
    const stripRank = (jsrc.match(/const rank = \{([^}]*)\}/) || [, ''])[1];
    const sr = Object.fromEntries([...stripRank.matchAll(/(\w+):\s*(\d+)/g)].map((m) => [m[1], +m[2]]));
    check('P2-8 strip rank: na sits below pass (no longer tied)', sr.na < sr.pass, true);
    check('P2-8 strip declares itself the legacy fallback', /LEGACY FALLBACK ONLY/.test(jsrc), true);

    done();
}

// ── Rule 9.4 · shape carries the state family — colour is never the SOLE carrier ──
// The operator could not tell a review band from a danger band in the runs ledger.
// The colour was correct; the CHANNEL was not. In the light theme --trust-warn
// (#b45309) and --trust-danger (#dc2626) sit ΔE 31.9 / 26° of hue apart (dark: 69.0 /
// 43°), and under deuteranopia they collapse to ΔE 5.5 — the same colour. Retuning the
// token cannot fix that (the best candidate moves CVD separation 11.7 → 13), which is
// why trust.css mandates a SHAPE channel "on EVERY surface". Two marks in the ledger
// were solid squares in all four states with only the fill colour differing.
//
// Asserted as a PROPERTY, not a grep: neutralise every colour and the four states must
// still be distinguishable. A future mark that carries state by hue alone fails here.
{
    const done = report('Rule 9.4 — the state family is legible without hue');
    const hsrc = fs.readFileSync(SRC, 'utf8');

    // Extract the shipped mark builder and run it for all four bands.
    const mIdx = hsrc.indexOf('function bandMark(');
    check('the shared band mark is extracted', mIdx > 0, true);
    const mEnd = hsrc.indexOf('\n    }', mIdx) + '\n    }'.length;
    // eslint-disable-next-line no-new-func
    const bandMark = new Function('BAND_C', `${hsrc.slice(mIdx, mEnd)}\nreturn bandMark;`)(
        { healthy: 'GREEN', review: 'AMBER', danger: 'RED', na: 'GREY' });

    const marks = ['healthy', 'review', 'danger', 'na'].map((b) => bandMark(b));
    // Strip every colour token, then the markup must STILL differ per state.
    const decolour = (h) => h.replace(/--sq:[^";]*/g, '--sq:X')
                             .replace(/GREEN|AMBER|RED|GREY/g, 'X');
    const bare = marks.map(decolour);
    check('every state is distinguishable with colour removed',
        new Set(bare).size, 4);
    // ONE implementation: the station header mark must be the same builder, not a
    // second private square (it was an inline 7px solid one).
    check('the station header mark uses the shared builder',
        /const dot = worst !== 'na' \? bandMark\(worst\) : ''/.test(hsrc), true);
    check('…and no private inline square survives beside it',
        /width:7px;height:7px;border-radius:2px;background:\$\{BAND_C/.test(hsrc), false);

    // The class names alone are just labels — the visual difference lives in the CSS,
    // so each state must declare a shape property, and they must differ from each other.
    const shape = (b) => (hsrc.match(new RegExp(`\\.ap-sq-${b}\\{([^}]*)\\}`)) || [, ''])[1];
    for (const b of ['healthy', 'review', 'danger', 'na']) {
        check(`.ap-sq-${b} declares its own fill or border`,
            /background|border/.test(shape(b)), true);
    }
    const shapes = ['healthy', 'review', 'danger', 'na']
        .map((b) => shape(b).replace(/var\(--[a-z-]+\)/g, 'C'));
    check('…and no two states share one shape once colour is neutralised',
        new Set(shapes).size, 4);
    // The three that carry a FINDING must each be legible at 9px: solid / half / ring.
    check('pass is a solid fill', /background:var\(--sq\)/.test(shape('healthy')), true);
    check('warn is a half fill', /linear-gradient/.test(shape('review')), true);
    check('danger is outlined and emphasized', /border:2px/.test(shape('danger')), true);
    check('na is the sparse-dash absence family', /dashed/.test(shape('na')), true);

    // ── the Assurance Inbox is a TRIAGE queue ───────────────────────────────
    // It was one run-on sentence per row in one weight, four identical findings
    // listed flat, and two labelled filter rows — so the two danger items were
    // pushed off the first screen by repetition of a warning. A queue is read DOWN
    // the titles first; everything else must be quieter and beneath them.
    {
        const grab = (n) => { const i = hsrc.indexOf('function ' + n + '('); return hsrc.slice(i, hsrc.indexOf('\n    }', i) + 6); };
        // eslint-disable-next-line no-new-func
        const F = new Function([
            'const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",\'"\':"&quot;"}[c]));',
            // The REAL chip path: the lens's own helper, delegating to the ONE builder
            // published by ui.js. Stubbing the chip would let the gate pass a lens that
            // hand-rolled its markup again — the exact regression it exists to catch.
            'const _profilesRaw = [{ tag: "UDERIA", profile_type: "rag_focused" },'
            + ' { tag: "RESTO", profile_type: "tool_enabled" }];',
            'const ifoc = (t) => ({ color: "#3b82f6", label: "Focus" });',
            'const _agentIfocColor = () => "#3b82f6";',
            hsrc.slice(hsrc.indexOf('    function _agentTagChip('),
                       hsrc.indexOf('\n    }', hsrc.indexOf('    function _agentTagChip(')) + 6),
            'const _stationOfEvent = (t) => t.startsWith("station_shortfall_") ? t.slice(18) : "guarded";',
            'const relTime = () => "2h ago";',
            hsrc.slice(hsrc.indexOf('const _INBOX_TITLE'), hsrc.indexOf('function _inboxRow(')),
            grab('_stationLabel'), grab('_sevWord'), grab('_inboxSignature'), grab('_groupInbox'), grab('_inboxRow'),
            'return { row: _inboxRow, group: _groupInbox, title: _inboxTitle, detail: _inboxDetail, TITLES: _INBOX_TITLE };',
        ].join('\n'))();

        const dup = (h) => ({ id: 'e' + h, source: 'stored', severity: 'warning',
            event_type: 'retrieval_defuse', profile_tag: 'UDERIA', created_at: `2026-08-03T${h}:00:00Z`,
            summary: "Retrieval firewall defused a chunk from 'Docs' (pattern(s): ignore_previous) — kept, labelled untrusted data",
            payload: { document_id: 'd1', labels: ['ignore_previous'] } });

        // Every event type the backend can raise has a declared title — an operator
        // must never meet a row headed by a wire key.
        for (const k of ['grounding_block', 'guardrail_outbound_redact', 'honesty_rewrite',
                         'retrieval_defuse', 'trust_breaker', 'session_sentinel',
                         'judge_condemnation', 'memory_quarantine', 'live_redteam_failure']) {
            check(`inbox: '${k}' has a declared title`, !!F.TITLES[k], true);
            check(`inbox: '${k}' title is prose, not a wire key`, /_/.test(F.TITLES[k]), false);
        }
        // The parameterised family is BUILT, not listed — five copies of one rule.
        check('inbox: a station shortfall titles itself from its station',
            F.title({ event_type: 'station_shortfall_grounded' }), 'GROUNDED execution below the danger floor');
        // An unknown type degrades to something readable, never to nothing.
        check('inbox: an unknown type still gets a readable title',
            F.title({ event_type: 'some_new_thing' }), 'Some new thing');

        // Rule 3.0 — no slot restates another. The detail must not repeat the title,
        // nor the agent tag that has its own slot in the meta line.
        const d = F.detail(dup('19'));
        check('inbox: the detail drops the title\'s own words', /^Retrieval firewall/.test(d), false);
        check('inbox: …and keeps what the title does not carry', /Docs/.test(d), true);
        check('inbox: the detail drops a redundant agent lead-in',
            F.detail({ profile_tag: 'RESTO', event_type: 'station_shortfall_grounded',
                       summary: 'GROUNDED execution below the danger floor for @RESTO — worst check at 0%' }),
            'worst check at 0%');

        // Recurrence collapses: four identical findings are four facts and one problem.
        const items = [{ id: 's1', source: 'derived', severity: 'danger',
                         event_type: 'station_shortfall_grounded', profile_tag: 'RESTO',
                         summary: 'GROUNDED execution below the danger floor' },
                       dup('19'), dup('18'), dup('17'), dup('16')];
        const groups = F.group(items);
        check('inbox: identical findings collapse to one row', groups.length, 2);
        const rowHtml = groups.map(F.row).join('');
        check('inbox: …carrying the occurrence count', /×4/.test(rowHtml), true);
        check('inbox: …and every occurrence remains reachable',
            (rowHtml.match(/class="ai-occ-row"/g) || []).length, 4);
        // A decided item never folds into an open group — that would hide an act.
        check('inbox: a decided item stays its own row',
            F.group([dup('19'), { ...dup('18'), disposition_label: 'quarantined' }]).length, 2);
        // Different agents are different problems.
        check('inbox: the same finding on two agents does not merge',
            F.group([dup('19'), { ...dup('18'), profile_tag: 'OTHER' }]).length, 2);

        // Anatomy: one title, then quieter detail and meta — asserted structurally so a
        // later edit cannot flatten it back into one run-on line.
        const one = F.row([dup('19')]);
        for (const cls of ['ai-title', 'ai-detail', 'ai-meta', 'ai-actions'])
            check(`inbox: the row has its ${cls}`, one.includes(cls), true);
        check('inbox: severity rides the row edge, not a competing chip',
            /class="ai-row ai-sev-warning"/.test(one), true);
        // P2-10 (the property the old grep stood in for): severity must be readable
        // without hue. The edge differs in WEIGHT per level AND the row states the
        // severity WORD from the one vocabulary.
        for (const [sev, word] of [['danger', 'needs you now'], ['warning', 'worth a look'],
                                   ['info', 'for the record']]) {
            const r = F.row([{ ...dup('19'), severity: sev }]);
            check(`inbox: a ${sev} row states its severity in words`, r.includes(word), true);
        }
        const edge = (sev) => (hsrc.match(new RegExp(`\\.ai-sev-${sev}\\{([^}]*)\\}`)) || [, ''])[1];
        check('inbox: the three severities differ in border WEIGHT, not only colour',
            new Set(['danger', 'warning', 'info'].map((x) => (edge(x).match(/border-left-width:[^;]*/) || [''])[0])).size, 3);
        // Actions must survive keyboard-only use: quiet by opacity, never display:none.
        // Anchor on the RULE, not a substring: `.ai-occ-act .ai-btn{` also contains
        // '.ai-btn{' and silently inverted this slice when the occurrence rows landed.
        const css = hsrc.slice(hsrc.indexOf('.ai-actions{'), hsrc.indexOf('\n        .ai-btn{'));
        check('inbox: actions are dimmed, never removed from the tree',
            /opacity/.test(css) && !/display:\s*none/.test(css), true);
        check('inbox: …and reappear on focus, not only on hover',
            /focus-within/.test(hsrc), true);

        // ── collapsing must not cost precision ───────────────────────────────
        // A collapsed row acts on EVERY occurrence it collapsed; expanding gives each
        // occurrence its own actions. Grouping that could only be acted on wholesale
        // would trade precision for tidiness — not a trade a triage queue may make.
        const g3 = F.row([dup('19'), dup('18'), dup('17')]);
        const parse = (h) => [...h.matchAll(/data-batch="([^"]*)"/g)]
            .map((m) => JSON.parse(m[1].replace(/&quot;/g, '"')));
        const bar = g3.slice(g3.indexOf('class="ai-actions"'));
        const occ = g3.slice(g3.indexOf('ai-occ-row'), g3.indexOf('class="ai-actions"'));
        check('inbox: the collapsed row acts on every occurrence',
            parse(bar).every((b) => b.length === 3), true);
        check('inbox: …and says so on the button', /Ack all 3/.test(bar), true);
        check('inbox: every occurrence is individually actionable',
            (g3.match(/class="ai-occ-row"/g) || []).length, 3);
        check('inbox: …each acting on itself alone',
            parse(occ).every((b) => b.length === 1), true);
        check('inbox: …covering all three, none skipped',
            new Set(parse(occ).map((b) => b[0].id)).size, 3);
        // One executor for one and for many — a group-only path is the branch that
        // drifts, and the single case is the better-exercised of the two.
        check('inbox: one batch action serves both', /inbox-ack-batch/.test(hsrc), true);
        check('inbox: …with no surviving single-item action',
            /inbox-ack-derived|'inbox-ack'/.test(hsrc), false);
        // A settled occurrence offers no action and says why.
        const mixed = F.row([dup('19'), { ...dup('18'), acknowledged: true }]);
        check('inbox: a settled occurrence is not re-offered',
            parse(mixed.slice(mixed.indexOf('class="ai-actions"'))).every((b) => b.length === 1), true);

        // The row-removal selector that made every button look dead: the POST fired,
        // the server recorded it, and nothing moved because the handler still looked
        // for the OLD row's utility classes. Refreshing from the source cannot rot the
        // same way — and a removed row would leave every badge and count stale anyway.
        check('inbox: actions refresh from the source, never remove a row by its classes',
            /closest\('div\.flex\.items-start'\)/.test(hsrc.replace(/^\s*\*.*$/gm, '')), false);
        check('inbox: …through one refresh path', /async function refreshInbox\(/.test(hsrc), true);

        // ── every station opens its own Guide entry, at either scope ─────────
        // A station panel could route to the runs and the posture matrix but not to the
        // GUARANTEE's own definition — the one surface that says what it upholds and
        // what grades it. The route is a property of BEING a station, so it is declared
        // ONCE in the renderer rather than eight times in the model.
        const tcSrc2 = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'handlers',
                                                 'trustCircle.js'), 'utf8');
        // EXECUTED, not grepped. The first draft of these assertions searched the
        // source for the route string — which survives `if (false)` and survives the
        // guard being neutered, so both mutations passed. A rendered-output assertion
        // is the only one that can tell "declared" from "reachable".
        // The SAME slice the TC-54 extractor uses — from `derivationPanel`, which
        // stationPanel calls, so its collaborators travel with it rather than being
        // stubbed (a stubbed collaborator is a gate testing its own stub).
        const spI = tcSrc2.indexOf('function derivationPanel(d)');
        const spE = tcSrc2.indexOf('\n    // ── rendering states onto the ring', spI);
        check('guide route: stationPanel extracted', spI > 0 && spE > spI, true);
        const escG = (x) => String(x == null ? '' : x).replace(/[&<>"']/g, (c) =>
            ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
        const panelWith = (qd) => {
            const prev = globalThis.window;
            globalThis.window = qd ? guideStub() : {};
            try {
                // eslint-disable-next-line no-new-func
                const fn = new Function('esc', '_derivOpen', 'window',
                    `${tcSrc2.slice(spI, spE)}\nreturn stationPanel;`)(escG, null, globalThis.window);
                return fn({ key: 'grounded', label: 'Grounded', sub: 's' },
                          { state: 'warn', headline: 'H.', facts: [],
                            cta: { label: 'Open the roster', action: 'roster' } }, true);
            } finally { globalThis.window = prev; }
        };
        const withGuide = panelWith(true);
        check('guide route: a pinned station RENDERS the Guide button',
            /data-tc-act="explain-dim:station_grounded"/.test(withGuide), true);
        check('guide route: …labelled for where it goes',
            /Open in the Assurance Guide/.test(withGuide), true);
        check('guide route: …beside the station\'s existing routes, not instead of them',
            /data-tc-act="roster"/.test(withGuide), true);
        // Rule 8.5 — no button that cannot open anything.
        check('guide route: absent Guide ⇒ no button at all',
            /explain-dim:/.test(panelWith(false)), false);
        check('guide route: …while the real routes survive',
            /data-tc-act="roster"/.test(panelWith(false)), true);
        // One declaration, and it names the STATION — identical at either scope.
        check('guide route: …declared once, not per station',
            (tcSrc2.match(/Open in the Assurance Guide/g) || []).length, 1);
        check('guide route: the route names the station, never an agent',
            !/explain-dim:station_[^"]*(_circleAgent|agent)/.test(withGuide), true);
        // The handler opens the Guide on whatever key it is handed — station keys too.
        check('guide route: the handler opens the Guide on the key it is given',
            /QD\.openGuide\(dimKey,/.test(hsrc), true);

        // ── ONE palette, ONE tag chip ────────────────────────────────────────
        // profileColors.js declares itself the single source and records that the map
        // "historically drifted across ~5 handlers"; ui.js exports the ONE chip builder.
        // This lens carried a third copy of the palette, and the first attempt at a
        // conform tag added a fourth copy of the chip — the drift, reproduced while
        // fixing it. Neither may come back.
        const pcSrc = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'profileColors.js'), 'utf8');
        const uiSrc2 = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'ui.js'), 'utf8');
        const codeOnly = hsrc.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*(\/\/|\*).*$/gm, '');
        // Precision matters: a single hex may legitimately reappear as a chart series
        // colour. What may never reappear is the PALETTE — the four together. So the
        // assertion is "no expression in this lens pairs three or more of them", which
        // catches a copy keyed by type, by label, or by anything else, while leaving an
        // unrelated `#3b82f6` in a series list alone. A blunter ban would be worked
        // around rather than obeyed.
        const IFOC_HEX = ['#4ade80', '#3b82f6', '#9333ea', '#F15F22'];
        for (const hex of IFOC_HEX)
            check(`palette: ${hex} is declared in profileColors.js`, pcSrc.includes(hex), true);
        const paletteCopies = codeOnly.split('\n')
            .filter((ln) => IFOC_HEX.filter((h) => ln.includes(h)).length >= 3);
        check('palette: the Agents lens declares no copy of it (found: '
              + (paletteCopies[0] || 'none').trim().slice(0, 60) + ')',
            paletteCopies.length, 0);
        check('palette: the lens resolves through the published single source',
            /window\.ProfileColors/.test(hsrc), true);
        // The chip is built by the one builder, never re-created locally.
        check('tag chip: ui.js owns the builder', /export function renderProfileTag\(/.test(uiSrc2), true);
        check('tag chip: …published for IIFE handlers',
            /window\.renderProfileTag = renderProfileTag/.test(uiSrc2), true);
        check('tag chip: …and the lens delegates to it',
            /window\.renderProfileTag/.test(hsrc), true);
        check('tag chip: …with no local copy of the chip markup',
            /profile-tag profile-tag--/.test(codeOnly), false);
        check('tag chip: …and no local re-styling of its shared tokens',
            /--profile-tag-(bg|border|text|radius)/.test(codeOnly), false);
        // An absent builder degrades to the plain tag, never to a styled lookalike.
        check('tag chip: the fallback is degraded, not a reproduction',
            /`<span>@\$\{esc\(t\)\}<\/span>`/.test(hsrc), true);

        // ── Rule 8.5 · no row is a dead end ──────────────────────────────────
        // An acknowledged `needs you now` row offered NOTHING — "urgent, and there is
        // nothing to do". `Ack` disposes of the NOTICE, never of the condition: a
        // station shortfall persists and re-derives after every ack, so what is owed
        // there is to go look at the agent. Every row now offers the place it is worked,
        // routed by what the row IS, disposed or not.
        const barOf = (h) => h.slice(h.indexOf('class="ai-actions"'));
        const labels = (h) => [...barOf(h).matchAll(/<button[^>]*?>([^<]*)</g)].map((m) => m[1].trim());

        const settledDanger = F.row([{ id: 'a', source: 'derived', severity: 'danger',
            acknowledged: true, event_type: 'station_shortfall_grounded', profile_tag: 'RESTO',
            summary: 'GROUNDED execution below the danger floor' }]);
        check('inbox: an acknowledged danger row is not a dead end',
            labels(settledDanger).length > 0, true);
        check('inbox: …it routes to the agent, where a shortfall is worked',
            /Open @RESTO/.test(barOf(settledDanger)), true);
        check('inbox: …and does NOT re-offer an ack it already has',
            /inbox-ack-batch/.test(barOf(settledDanger)), false);

        const turnScoped = F.row([{ id: 'b', source: 'derived', severity: 'danger',
            event_type: 'grounding_block', profile_tag: 'UDERIA',
            session_id: 's-1234', turn_id: 3, summary: 'Answer withheld' }]);
        check('inbox: a turn-scoped finding routes to that turn',
            /data-ap-open-session="s-1234"/.test(turnScoped) && /data-ap-turn="3"/.test(turnScoped), true);
        check('inbox: …using the route the rest of the lens already uses',
            /data-ap-open-session/.test(hsrc), true);

        // EVERY row, whatever its severity or disposition.
        for (const sev of ['danger', 'warning', 'info']) {
            for (const acked of [false, true]) {
                const r = F.row([{ id: 'x', source: 'stored', severity: sev, acknowledged: acked,
                    event_type: 'retrieval_defuse', profile_tag: 'UDERIA', summary: 'x', payload: {} }]);
                check(`inbox: a ${sev}${acked ? ' acknowledged' : ''} row still offers its route`,
                    labels(r).length > 0, true);
            }
        }
    }

    // Rule 9.5 — a mark and its own LEGEND must read the same vocabulary. The pass/fail
    // sparkline filled with raw hex (#10b981 / #ef4444) while the legend three blocks
    // below used var(--trust-pass) / var(--trust-danger); in the light theme those
    // resolve to #059669 / #dc2626, so the key and the marks it explains rendered in
    // different colours. Same drift class as the acted-dot legend/aria split.
    const spark = (hsrc.match(/function sparkBars\([\s\S]*?\n    \}/) || [''])[0];
    check('the sparkline is extracted', spark.length > 0, true);
    check('…and fills from the trust tokens, never raw hex',
        /var\(--trust-pass/.test(spark) && /var\(--trust-danger/.test(spark), true);
    check('…so the legend and the bars it explains agree',
        /var\(--trust-pass/.test(hsrc) && !/fill="\$\{v \? '#/.test(hsrc), true);
    // The bars already satisfy Rule 9.4 WITHOUT the shape work: pass is full height,
    // fail is half. Pinned so a later tidy-up cannot flatten them to one height and
    // leave colour as the sole carrier.
    check('…and the state stays legible without hue (two heights)',
        /height="\$\{v \? h - 4 : h \* 0\.5\}"/.test(spark), true);

    done();
}


// ── the judge channel: WHO measured it, held apart from WHAT it found ────────
// A run row is ~24 identical squares, two of which are a MODEL'S OPINION and the rest
// a rule's measurement. Nothing said which, nothing said a judge had run at all, and
// the ruling was readable only after expanding the row. Fill carries the state and
// colour carries attention, so the kind rides the glyph's own shape.
{
    const done = report('the judge channel — a model opined, and what it ruled');
    const hsrc = fs.readFileSync(SRC, 'utf8');
    const grab = (n) => { const i = hsrc.indexOf('function ' + n + '('); return hsrc.slice(i, hsrc.indexOf('\n    }', i) + 6); };

    // ── the mark ────────────────────────────────────────────────────────────
    const mIdx = hsrc.indexOf('function bandMark(');
    // eslint-disable-next-line no-new-func
    const bandMark = new Function('BAND_C', `${hsrc.slice(mIdx, hsrc.indexOf('\n    }', mIdx) + 6)}\nreturn bandMark;`)(
        { healthy: 'GREEN', review: 'AMBER', danger: 'RED', na: 'GREY' });
    const decolour = (h) => h.replace(/--sq:[^";]*/g, '--sq:X').replace(/GREEN|AMBER|RED|GREY/g, 'X');
    for (const b of ['healthy', 'review', 'danger', 'na']) {
        check(`a judge's ${b} mark differs from a rule's, with colour removed`,
            decolour(bandMark(b, '', true)) !== decolour(bandMark(b, '', false)), true);
    }
    // The judge shape must COMPOSE with the state, never overwrite it: all four state
    // treatments must survive the judge variant.
    const states = ['healthy', 'review', 'danger', 'na'].map((b) => decolour(bandMark(b, '', true)));
    check('…and the four states stay distinguishable on a judge mark', new Set(states).size, 4);

    const jshape = (hsrc.match(/\.ap-sq-j\{([^}]*)\}/) || [, ''])[1];
    check('the judge glyph declares its own shape', jshape.length > 0, true);
    // NOT the dot channel: a dot means "a mechanism acted" on the ring (Rule 9.3).
    check('…and it is not a circle — the dot channel is not reused',
        /border-radius:\s*50%/.test(jshape), false);
    check('…and it carries no fill of its own, so the state still shows',
        /background|border(?!-radius)/.test(jshape), false);

    // ── which marks are judges: per-mark exactness ──────────────────────────
    // Both mutations must fail here: dropping the mark, and applying it to everything.
    const stripSrc = grab('dimStrip');
    // The SHIPPED panel block, whole: `markBandOf` (worst-of the panel) and
    // `_clearedNote` (which names a split) are one unit — stubbing either would let a
    // broken panel rule through the gate.
    const _pStart = hsrc.indexOf('const _panelVotes =');
    const _pEnd = hsrc.indexOf('// dimension band-strip');
    // eslint-disable-next-line no-new-func
    const _panel = new Function('isJudgeVerdict', 'bandOfVerdict',
        `${hsrc.slice(_pStart, _pEnd)}\nreturn { markBandOf, _clearedNote, _panelSplit, _panelFails };`)(
        (v) => (v && v.kind) ? v.kind === 'judge' : false,
        (v) => (v.band || (v.passed === true ? 'healthy' : v.passed === false ? 'danger' : 'na')));

    // eslint-disable-next-line no-new-func
    const dimStrip = new Function(
        'bandMark', 'bandOfVerdict', 'dimAttrs', 'isJudgeVerdict', 'isInPathJudge', '_clearedNote', 'markBandOf', '_dimStageOf', '_stageIdxOf', '_stageLabelOf', '_stageDescOf', '_stageKeysInOrder',
        `${stripSrc}\nreturn dimStrip;`)(
        bandMark,
        (v) => (v.band || (v.passed === true ? 'healthy' : v.passed === false ? 'danger' : 'na')),
        (k, note) => `data-dim="${k}"${note ? ` data-dim-note="${note}"` : ''}`,
        // the SHIPPED predicate, not a stub
        new Function('QD', `${hsrc.slice(hsrc.indexOf('const isJudgeDim ='), hsrc.indexOf('\n', hsrc.indexOf('const isJudgeVerdict =')))}\nreturn isJudgeVerdict;`)({ JUDGE_DIMS: new Set(['groundedness']) }),
        // the SHIPPED provenance predicate too — a stub would let the gate pass a
        // resolver that marked every judge, or none, as in-path.
        new Function(`${hsrc.slice(hsrc.indexOf('const isInPathJudge ='), hsrc.indexOf(';', hsrc.indexOf('run.gate.judge_family ===')) + 1)}\nreturn isInPathJudge;`)(),
        _panel._clearedNote,
        _panel.markBandOf,
        // The stage seam, stubbed against the REAL served model so the strip's column
        // order is exercised as the product computes it.
        (d) => STAGE_MODEL.dimensions[d] || '',
        (d) => { const i = STAGE_MODEL.order.indexOf(STAGE_MODEL.dimensions[d]);
                 return i === -1 ? STAGE_MODEL.order.length : i; },
        (st) => String(STAGE_MODEL.labels[st] || st).toUpperCase(),
        (st) => STAGE_MODEL.descriptions[st] || '',
        (groups) => STAGE_MODEL.order.concat(['']).filter((st) => groups[st] && groups[st].length));

    const vs = [
        { dimension: 'grounded_overlap', kind: 'metric', band: 'danger', explanation: '46% of claims supported' },
        { dimension: 'groundedness', kind: 'judge', band: 'healthy', explanation: '3 of 3 judges upheld' },
        { dimension: 'retrieval_relevance', kind: 'metric', band: 'review' },
    ];
    const html = dimStrip(vs);
    const marks = html.match(/<span class="ap-sq[^>]*>/g) || [];
    check('every verdict gets a mark', marks.length, 3);
    const jm = marks.filter((m) => /ap-sq-j/.test(m));
    check('exactly the judge is marked as one', jm.length, 1);
    check('…and it is the judge dimension', /data-dim="groundedness"/.test(jm[0]), true);
    check('…carrying the judge state, not a state of its own', /ap-sq-healthy/.test(jm[0]), true);
    // The ruling rides the mark's tooltip — "what did it find", not only "what is it".
    check('a mark carries the verdict’s runtime explanation',
        /data-dim="groundedness"[^>]*data-dim-note="3 of 3 judges upheld"/.test(html), true);
    // A span persisted before `kind` existed still resolves through JUDGE_DIMS.
    const legacy = dimStrip([{ dimension: 'groundedness', band: 'danger' }]);
    check('…and a span with no kind falls back to the pinned judge set',
        /ap-sq-j/.test(legacy), true);

    // ── that a judge RAN, and its ruling, without expanding ─────────────────
    // eslint-disable-next-line no-new-func
    const _actedTag = new Function('esc', 'escAttr',
        `${grab('_actedTag')}\nreturn _actedTag;`)((x) => String(x), (x) => String(x));

    // eslint-disable-next-line no-new-func
    const _judgeTag = new Function('isJudgeVerdict', 'bandOfVerdict', 'dimLabel', 'esc', 'escAttr', '_actedTag',
        `${grab('_judgeTag')}\nreturn _judgeTag;`)(
        (v) => v.kind === 'judge',
        (v) => v.band || 'na',
        (k) => k.replace(/_/g, ' '),
        (x) => String(x), (x) => String(x), _actedTag);

    check('no judge, no chip — nothing is claimed', _judgeTag({ verdicts: [{ dimension: 'cost', kind: 'metric' }] }), '');
    const t1 = _judgeTag({ verdicts: [
        { dimension: 'groundedness', kind: 'judge', band: 'healthy' },
        { dimension: 'content_safety', kind: 'judge', band: 'healthy' },
        { dimension: 'answer_correctness', kind: 'judge', band: 'danger' },
    ] });
    check('a judged run says so', /judge/.test(t1), true);
    // ONE fact per chip. `gate-judged 2/3` fused two: a gate consults exactly ONE judge
    // and the tally is the PANEL's, so the label read as "the gate's judge said 2 of 3".
    // Name the FINDING, not the ratio: the denominator ("judges that RULED") is a
    // concept the reader cannot see, and it was asked about three times.
    check('…states the finding, not a ratio', /1 judge flagged this/.test(t1), true);
    check('…with no bare fraction on the chip',
        /\d+\/\d+/.test((/>([^<]+)<\/span>$/.exec(t1) || [, ''])[1]), false);
    // The summary must count what the reader can COUNT below it: "2 of the 3 judges
    // that ruled" set a hidden denominator against a list of FOUR named judges.
    check('…and the composition, in the list’s own terms, is one hover away',
        /3 judges ran on this turn: 2 upheld the answer, 1 flagged it\./.test(t1), true);
    check('…with no fraction to decode', /\d+ of the \d+/.test(t1), false);
    check('…and the vocabulary anchored where the confusion lives',
        /A judge is one check; the models on its panel vote inside it\./.test(t1), true);

    // Silent on the NORM: a chip on almost every row is weight without information, and
    // the strip's diamonds already say a judge ran and what it ruled.

    // …but its own text must then explain why it IS here (Rule 3.2): an escalated turn
    // can be unanimous, and "3 of 3 upheld" beside unanimous rows with no chip is the
    // ambiguity that rule bans.
    // A gate CONSULTING a judge is its own fact, so it is its own chip — and only where
    // nothing else already carries it (Rule 3.0).
    const t1b = _judgeTag({ gate: { escalated: true, cleared: true, judge_votes: '3/3' }, verdicts: [
        { dimension: 'groundedness', kind: 'judge', band: 'healthy' },
    ] });
    check('an escalated turn nothing acted on says the gate asked', /gate asked a judge/.test(t1b), true);
    check('…and how it ended', /cleared/.test(t1b), true);
    // The gate's fraction is its judge's PANEL — labelled as models, so it can never
    // be read as a second count of judges.
    check('…carrying the gate judge’s own panel, labelled as models',
        /3\/3 models/.test(t1b), true);
    const t1c = _judgeTag({ gate: { escalated: true, grounding: 'block' }, verdicts: [
        { dimension: 'groundedness', kind: 'judge', band: 'healthy' },
    ] });
    check('…and never repeats what the act chip already states',
        /gate asked a judge/.test(t1c), false);
    // Presence must always be self-explaining: a unanimous panel earns no chip at all,
    // so a visible tally is always a tally that flagged something (Rule 3.2).
    check('a unanimous panel earns no tally chip', _judgeTag({ verdicts: [
        { dimension: 'groundedness', kind: 'judge', band: 'healthy' },
        { dimension: 'content_safety', kind: 'judge', band: 'healthy' },
    ] }), '');
    check('…and the act chip states the consultation instead',
        /The gate consulted one judge to decide this/.test(
            _actedTag({ gate: { grounding: 'block', escalated: true, judge_votes: '0/1' } })), true);
    // An abstention is not a ruling — a judge that declined upheld nothing.
    const t2 = _judgeTag({ verdicts: [
        { dimension: 'groundedness', kind: 'judge', band: 'healthy' },
        { dimension: 'content_safety', kind: 'judge', band: 'danger' },
        { dimension: 'source_consistency', kind: 'judge', band: 'na' },
    ] });
    check('an abstention is counted apart from a ruling',
        /1 judge flagged this/.test(t2)
        && /3 judges ran on this turn: 1 upheld the answer, 1 flagged it, 1 abstained\./.test(t2), true);
    check('…and its REASON rides its own line, not a repeated count',
        /abstained \(no input to rule on\)/.test(t2)
        && !/1 judge abstained —/.test(t2), true);
    check('…and is named rather than dropped', /abstained/.test(t2), true);
    // A gate escalation produces no dimension at all — the one thing the marks cannot say.
    const t3 = _judgeTag({ verdicts: [], gate: { escalated: true, cleared: true, judge_votes: '3✓/0✗' } });
    check('a gate escalation is reported though it has no mark', /gate asked a judge/.test(t3), true);
    check('…naming its panel’s vote', /3✓\/0✗ models/.test(t3), true);
    // A vote shape the parser does not know must not make the ruling vanish.
    check('…and an unparsed vote string is shown, not dropped',
        /Its panel voted 3✓\/0✗/.test(t3), true);
    // Rule 3.0: the row states its verdict once. A second band-coloured chip beside it
    // reads as a second verdict.
    check('the chip is muted, never a band colour',
        /--trust-(danger|warn|pass)/.test(t1), false);

    // …and the COLLAPSED row actually renders it — the whole point is not expanding.
    check('the collapsed run row renders the judge chip',
        /\$\{dispTag\}\$\{_judgeTag\(r\)\}/.test(hsrc), true);

    // ── what the ruling could DO ───────────────────────────────────────────
    // A diamond is a judge. A judge a gate consulted DURING the turn could withhold the
    // answer and wears the regular diamond; a judge SAMPLED after it only reported, and
    // is drawn smaller. The modifier deliberately rides the COMMON case, because it
    // means "less weight" — so the consequential judge is the one that stands out.
    check('a sampled judge is marked apart from one a gate consulted',
        decolour(bandMark('healthy', '', true, false)) !== decolour(bandMark('healthy', '', true, true)), true);
    check('…both are judges', ['', ''].every((_, i) =>
        /ap-sq-j\b/.test(bandMark('healthy', '', true, !!i))), true);
    // The RULE, stated implementation-agnostically: sampled is smaller than in-path.
    const _scaleOf = (cls) => {
        const m = hsrc.match(new RegExp(`\\.${cls}\\{([^}]*)\\}`));
        return parseFloat(((m || [, ''])[1].match(/scale\(([\d.]+)\)/) || [, NaN])[1]);
    };
    check('a sampled judge is drawn smaller than one a gate consulted',
        _scaleOf('ap-sq-j') < _scaleOf('ap-sq-jp'), true);
    // …and calibrated so the COMMON case looks normal, not degraded: 48 of 52 judge
    // marks are sampled, and shrinking all of them made the strip read as broken.
    check('…with the sampled diamond at the weight of a square, not shrunken',
        _scaleOf('ap-sq-j') >= 0.75, true);
    check('the in-path modifier carries no fill, so the state still shows',
        /background|border(?!-radius)/.test((hsrc.match(/\.ap-sq-jp\{([^}]*)\}/) || [, ''])[1]), false);

    // Resolved per verdict from the gate that consulted it, never per turn.
    const escRun = { gate: { escalated: true, judge_family: 'groundedness' }, verdicts: [
        { dimension: 'grounded_overlap', kind: 'metric', band: 'healthy' },
        { dimension: 'groundedness', kind: 'judge', band: 'danger' },
        { dimension: 'content_safety', kind: 'judge', band: 'healthy' },
    ] };
    const escHtml = dimStrip(escRun.verdicts, escRun);
    const escMarks = escHtml.match(/<span class="ap-sq[^>]*>/g) || [];
    check('exactly the judge the gate consulted is drawn larger',
        escMarks.filter((m) => /ap-sq-jp/.test(m)).length, 1);
    check('…and it is that judge',
        /data-dim="groundedness"/.test(escMarks.find((m) => /ap-sq-jp/.test(m))), true);
    check('every other judge on the turn is drawn as sampled',
        /ap-sq-jp/.test(escMarks.find((m) => /content_safety/.test(m))), false);
    check('no escalation, every judge is sampled',
        (dimStrip(escRun.verdicts, { verdicts: escRun.verdicts }).match(/ap-sq-jp/g) || []).length, 0);

    // ── a judge's colour is worst-of its PANEL ─────────────────────────────
    // The recorded verdict is the panel's consensus, so a dissenting model disappears
    // into a majority. Worst-of is the house rule (5.1), and Rule 3.0c says a
    // disagreement between measures of one property is REPORTED, not weighted away.
    const unanimous = { kind: 'judge', passed: true, votes: [{ verdict: 'pass' }, { verdict: 'pass' }] };
    const split = { kind: 'judge', passed: true, votes: [{ verdict: 'pass' }, { verdict: 'pass' }, { verdict: 'fail' }] };
    check('a unanimous panel is green', _panel.markBandOf(unanimous), 'healthy');
    check('one dissenting vote turns the mark red', _panel.markBandOf(split), 'danger');
    check('…and a consensus failure is red too',
        _panel.markBandOf({ kind: 'judge', passed: false, votes: [{ verdict: 'fail' }] }), 'danger');
    check('an abstention stays in the absence family — not a green',
        _panel.markBandOf({ kind: 'judge', passed: null, votes: [] }), 'na');
    // …and a stray fail vote inside an abstained panel must not manufacture a finding:
    // a judge that declined to rule is an absence, not a red.
    check('…even when a vote inside it says fail',
        _panel.markBandOf({ kind: 'judge', passed: null, votes: [{ verdict: 'fail' }] }), 'na');
    check('a span with no votes keeps its recorded verdict',
        _panel.markBandOf({ kind: 'judge', passed: true, votes: [] }), 'healthy');
    check('a RULE is never repainted by a panel it does not have',
        _panel.markBandOf({ kind: 'metric', passed: true, votes: [{ verdict: 'fail' }] }), 'healthy');
    // A red mark beside a chip reading "upheld", with nothing explaining the
    // difference, is the contradiction this grammar exists to prevent.
    check('a split panel is NAMED on the mark, in models',
        /panel upheld it 2 models to 1/.test(_panel._clearedNote(split, new Set())), true);
    check('…and on the chip', /·split \$\{_panelVotes\(v\)\.length - _panelFails\(v\)\}\/\$\{_panelVotes\(v\)\.length\}/.test(hsrc), true);
    check('a unanimous panel is not called split', _panel._panelSplit(unanimous), false);

    // ── the refining judge disagreed (Rule 3.0c), in BOTH directions ───────
    // The centre dot IS the refining judge's verdict. Only the "cleared" direction was
    // ever drawn, so three of the four readings had a name and the fourth — a green
    // measurement hiding a judge's objection — had none.
    const corr = decolour(bandMark('danger', '', false, false, true));
    check('a corrected measurement is marked apart from an uncorrected failure',
        corr !== decolour(bandMark('danger')), true);
    const cshape = (hsrc.match(/\.ap-sq\.ap-sq-c\{([^}]*)\}/) || [, ''])[1];
    check('…the correction is a CENTRE fill, so the outline still shows',
        /radial-gradient/.test(cshape) && !/border/.test(cshape), true);
    check('…defaulting to the pass token, because "cleared" IS the judge upholding it',
        /--trust-pass/.test(cshape), true);

    const dispRun = { disputed: ['grounded_overlap'], verdicts: [
        { dimension: 'grounded_overlap', kind: 'metric', band: 'danger', explanation: '22% supported' },
        { dimension: 'groundedness', kind: 'judge', band: 'healthy' },
        { dimension: 'retrieval_relevance', kind: 'metric', band: 'danger' },
    ] };
    const dHtml = dimStrip(dispRun.verdicts, dispRun);
    const dMarks = dHtml.match(/<span class="ap-sq[^>]*>/g) || [];
    check('exactly the corrected measurement is marked', dMarks.filter((m) => /ap-sq-c/.test(m)).length, 1);
    check('…and it is that dimension', /data-dim="grounded_overlap"/.test(dMarks.find((m) => /ap-sq-c/.test(m))), true);
    check('…an uncorrected failure beside it is untouched',
        /ap-sq-c/.test(dMarks.find((m) => /retrieval_relevance/.test(m))), false);
    check('…and the measurement keeps the band it measured (never rewritten)',
        /ap-sq-danger/.test(dMarks.find((m) => /ap-sq-c/.test(m))), true);
    check('no disagreement, no centre mark',
        /ap-sq-c/.test(dimStrip(dispRun.verdicts, { verdicts: dispRun.verdicts })), false);
    check('the mark says WHY in words too',
        /cleared by the judge that refines this check/.test(dHtml), true);
    check('the chip names it as well, so the two cannot disagree',
        /wasCleared \? ' ·judge-cleared'/.test(hsrc), true);

    // The MIRROR: the rule passed and the judge that refines it disputed that.
    const flagRun = { judge_flagged: ['grounded_overlap'], verdicts: [
        { dimension: 'grounded_overlap', kind: 'metric', band: 'healthy', explanation: '76% supported' },
        { dimension: 'groundedness', kind: 'judge', band: 'danger' },
    ] };
    const fHtml = dimStrip(flagRun.verdicts, flagRun);
    const fMark = (fHtml.match(/<span class="ap-sq[^>]*data-dim="grounded_overlap"[^>]*>/) || [''])[0];
    check('a disputed PASS is marked too', /ap-sq-c/.test(fMark), true);
    check('…and the measurement still shows what it measured', /ap-sq-healthy/.test(fMark), true);
    check('…with the dot carrying the JUDGE’s verdict, not the rule’s',
        /--dot:var\(--trust-danger/.test(fMark), true);
    check('…while the cleared direction keeps the pass token',
        /--dot:/.test((dHtml.match(/<span class="ap-sq[^>]*ap-sq-c[^>]*>/) || [''])[0]), false);
    check('…said in words as well', /the judge that refines it disputed that/.test(fHtml), true);
    check('the chip names it, symmetric with judge-cleared',
        /wasFlagged \? ' ·judge-flagged'/.test(hsrc), true);
    check('…and so does the row, with its own reason', /_disp\('passed, but disputed'/.test(hsrc), true);
    check('…and it does not fail the turn', /a judge alone does not fail the turn/.test(fHtml), true);

    // ── the kind is marked where a check is NAMED, too ──────────────────────
    // Dash and dot are different strokes: a sparse dash is the absence family on
    // every trust surface (PROP-002 retired the DASHED judge chip for that
    // collision); a dot is the kind channel.
    check('a judge chip is dotted', /border:1px \$\{judged \? 'dotted' : 'solid'\}/.test(hsrc), true);
    check('…and never dashed — the absence family keeps the dash',
        /border:1px \$\{judged \? 'dashed'/.test(hsrc), false);
    const qdsrc = fs.readFileSync(new URL('../static/js/handlers/qualityDimensions.js', import.meta.url), 'utf8');
    check('the Guide’s chip rows are dotted the same way',
        /\.qd-rel-chip\.qd-judge\{border-style:dotted;\}/.test(qdsrc)
        && /qd-rel-chip\$\{JUDGE_DIMS\.has\(d\) \? ' qd-judge' : ''\}/.test(qdsrc), true);
    check('…never dashed', /\.qd-rel-chip\.qd-judge\{border-style:dashed/.test(qdsrc), false);
    check('the chip names a gate-consulted judge in words too, without jargon',
        /inPath \? ' ·judge · gate-consulted' : ' ·judge'/.test(hsrc), true);
    check('…and "in-path" never reaches a user-facing string',
        /['\`][^'\`\n]*\bin-path\b[^'\`\n]*['\`]/.test(
            hsrc.split('\n').filter((l) => !/^\s*(\/\/|\*|\/\*)/.test(l)).join('\n')), false);

    // ── a mechanism ACTED — the intervention axis, on the row ──────────────
    // Orthogonal to the verdict beside it (Rule 3bb): on real data every intervention
    // landed on a turn whose measurements were otherwise clean, so a healthy row can
    // still be one whose answer was withheld. It had no mark at all.

    check('a clean turn is not marked', _actedTag({ gate: { grounding: 'pass', outbound: 'pass' } }), '');
    check('no gate at all is not marked', _actedTag({}), '');
    // The one that would report the absence of a control as the control working.
    check('an ERRORED guard is not an act — it is a guard that did not run',
        _actedTag({ gate: { honesty: 'error', inbound: 'error', outbound: 'error' } }), '');

    const withheld = _actedTag({ gate: { grounding: 'block' } });
    check('a withheld answer is marked', /withheld/.test(withheld), true);
    check('…on the acted-dot channel (Rule 9.3), not a strip mark',
        /ap-acted-dot/.test(withheld) && !/ap-sq/.test(withheld), true);
    check('…and the act is NAMED — a dot cannot say which of five acts happened',
        /withheld/.test(withheld), true);
    const parked = _actedTag({ gate: { grounding: 'block', parked: true } });
    check('an OWED act takes the accent (Rule 9.5), not a band colour',
        /F15F22|teradata-orange/.test(parked), true);
    check('…and says what is owed', /parked/.test(parked), true);
    check('an act with nothing owed does NOT take the accent',
        /F15F22|teradata-orange/.test(withheld), false);
    check('each act has its own words', [
        [{ inbound: 'block' }, /refused/],
        [{ outbound: 'redact' }, /redacted/],
        [{ honesty: 'rewrite' }, /rewritten/],
        [{ grounding: 'annotate' }, /annotated/],
    ].every(([g, re]) => re.test(_actedTag({ gate: g }))), true);
    check('…and one act is never named twice',
        ((/>([^<]+)<\/span>$/.exec(_actedTag({ gate: { grounding: 'block', outbound: 'block' } })) || [, ''])[1]
            .match(/withheld/g) || []).length, 1);

    // Rule 3bb: two axes. The act must not move the verdict, nor the verdict the act.
    check('the acted mark does not decide the row’s band',
        /function runBand\(run\)[\s\S]{0,700}gate/.test(hsrc), false);
    check('the acted chip leads the row — it is the most decision-relevant fact',
        /\$\{_actedTag\(r\)\}\$\{dispTag\}/.test(hsrc), true);
    check('the acted dot is a DOT, and the strip marks are not',
        /\.ap-acted-dot\{[^}]*border-radius:50%/.test(hsrc)
        && !/\.ap-sq\{[^}]*border-radius:50%/.test(hsrc), true);

    // "Which turns were altered" is a different question from "which scored badly",
    // and without a filter the answer is a scan of every row.
    check('the acted turns can be isolated', /data-ap-runfilter="act:/.test(hsrc), true);
    check('…the pill carries its count', /Acted \$\{_actedN\}/.test(hsrc), true);
    // Guarded on the COUNT, not merely mentioned near it: the first version of this
    // assertion matched the key row's own `${_actedN ?` and passed an always-on pill.
    check('…offered only when something acted (honest absence)',
        /\$\{_actedN \? `<span style="width:1px[\s\S]{0,600}data-ap-runfilter="act:/.test(hsrc), true);
    check('…and the filter reads the same predicate the mark does',
        /_runFilter\.act === 'acted'\) runs = runs\.filter\(\(r\) => !!_actedTag\(r\)\)/.test(hsrc), true);
    check('the key names the acted channel, only where one occurred',
        /\$\{_actedN \? _keyRow\('a dot on the row'/.test(hsrc), true);

    // ── column discipline: no chip may move the columns ────────────────────
    // The row is read DOWN its columns — band, marks, cost, latency. A long chip
    // wrapped the timestamp onto two lines and shifted the strip row by row.
    check('the chips are the only flexible member, and they SHRINK',
        /\.ap-run-chips\{flex:1 1 auto;min-width:0/.test(hsrc), true);
    check('…truncating rather than pushing (the full text is in the tooltip)',
        /\.ap-run-chips > \.ap-chip\{[^}]*text-overflow:ellipsis/.test(hsrc), true);
    check('…every fixed member refuses to wrap',
        /\.ap-run-hd > \.ap-run-fixed\{flex:0 0 auto;white-space:nowrap\}/.test(hsrc), true);
    check('…and the strip never shrinks with them',
        /\.ap-run-hd > \.ap-strip\{flex:0 0 auto\}/.test(hsrc), true);
    check('the row marks its fixed members',
        (hsrc.match(/class="ap-run-fixed|ap-dot ap-run-fixed/g) || []).length >= 4, true);
    // This asserted the exact CALL LIST of the chips row, so adding a legitimate chip
    // broke it while the property it stands for — every chip lives in the shrinking
    // slot — held perfectly. Pinned to the PROPERTY instead (the ~20th conversion of
    // this defect class in this repo): the slot must contain nothing but interpolated
    // chips, and no chip builder may render outside it, where it would become a fixed
    // member and push the columns.
    const _chipsSlot = (hsrc.match(/<span class="ap-run-chips">([\s\S]*?)<\/span>/) || [])[1];
    check('the chips slot exists', typeof _chipsSlot === 'string' && _chipsSlot.length > 0, true);
    check('…and holds only interpolated chips (no fixed literal markup)',
        /^(\$\{[^}]+\})+$/.test((_chipsSlot || '').trim()), true);
    const _rowHd = (hsrc.match(/<div class="ap-run-hd"[\s\S]*?<\/div>/) || [''])[0];
    check('…and puts every chip inside the shrinking slot',
        !/(_actedTag\(|_judgeTag\(|_voteTag\(|\$\{dispTag\}|\$\{invTag\})/
            .test(_rowHd.replace(_chipsSlot || '', '')), true);
    // The row carries the SHORT form; the sentence lives where there is room for it.
    check('the acted chip stays short', _actedTag({ gate: { grounding: 'block', parked: true } })
        .match(/>([^<]+)<\/span>$/)[1].length <= 20, true);
    check('…and the full sentence is in its tooltip',
        /data-tip="[^"]*original is parked for review/.test(parked), true);

    // ── the preference axis: a vote is reported, never measured ────────────
    // A user's upvote/downvote is a THIRD axis beside intervention and quality
    // (Rule 3bb). It may be shown next to a verdict; it may never move one, and it
    // may never be carried by colour alone.
    {
        // eslint-disable-next-line no-new-func
        const _vt = new Function('esc', `${grab('_voteTag')}\nreturn _voteTag;`)(
            (x) => String(x));
        const up = _vt({ vote: 'up' }), down = _vt({ vote: 'down' }), none = _vt({});
        check('a rated run carries a vote chip', up.length > 0 && down.length > 0, true);
        check('…an unrated run carries none', none === '', true);
        check('…and an unknown value renders nothing rather than a stray chip',
            _vt({ vote: 'sideways' }) === '', true);
        // Rule 9.4a — the WORD is the channel, so it survives greyscale and CVD.
        check('the vote is carried by a word, not by colour alone',
            />upvoted</.test(up) && />downvoted</.test(down), true);
        check('…in the muted register, because preference is not an attention level',
            /var\(--text-muted\)/.test(up) && !/--trust-(danger|warn|pass)/.test(up), true);
        check('…and says what it is, and is not, in its tooltip',
            /data-tip="[^"]*preference, not a measurement/.test(up), true);
        // R3 — the band is the verdict's, and a vote must not reach it.
        // eslint-disable-next-line no-new-func
        const _rb = new Function(`${grab('runBand')}\nreturn runBand;`)();
        const passing = { passed: true, gating_failures: 0, verdicts: [] };
        check('a downvote does not change the row band',
            _rb({ ...passing, vote: 'down' }) === _rb(passing), true);

        // The roster's preference reading: absent at zero (an honest absence, not a
        // decorative zero) and never folded into the pass rate it sits under.
        // eslint-disable-next-line no-new-func
        const _vn = new Function(`${grab('voteNote')}\nreturn voteNote;`)();
        check('no votes ⇒ the roster line is absent entirely',
            _vn({ turns: 10, upvotes: 0, downvotes: 0 }) === '', true);
        const note = _vn({ turns: 10, upvotes: 3, downvotes: 2, vote_disagreements: 1 });
        check('…and with votes it names both directions',
            /3 up/.test(note) && /2 down/.test(note), true);
        check('…and names the disagreement, which is why the line exists',
            /1 disagrees? with the scorecard/.test(note), true);
        check('…while stating it is not folded into the rate above it',
            /data-tip="[^"]*never folded into the pass rate/.test(note), true);
        check('…and stays silent about disagreements when there are none',
            !/disagree/.test(_vn({ turns: 4, upvotes: 1, downvotes: 0 })), true);
    }

    // ── one mark, one meaning: the verdict dot is retired ──────────────────
    // The row carried a verdict dot AND, once the acted mark landed, an acted dot —
    // two dots side by side meaning different things, which is the misreading Rule 9.3
    // exists to prevent. The verdict is carried by its WORD (in its colour); where
    // there is no room for a word, by the state MARK, which is a square.
    check('no verdict dot survives on any ledger surface', /class="ap-dot/.test(hsrc), false);
    check('…nor its CSS', /\.ap-dot\{/.test(hsrc), false);
    check('the only dot on a run row is the acted one',
        /ap-acted-dot/.test(hsrc) && !/ap-dot/.test(hsrc), true);
    check('…and the verdict keeps its word, in its colour',
        /style="color:\$\{BAND_C\[b\]\}">\$\{b === 'na' \? 'unscored' : b\}/.test(hsrc), true);
    check('the coordination tree uses the shared state mark instead',
        /\$\{bandMark\(b\)\}\$\{roleTag\}/.test(hsrc), true);

    // ── the strip is a FIXED GRID ──────────────────────────────────────────
    // It is read DOWN the ledger — "did this check pass on every turn?" — which only
    // works if a dimension sits in the same column on every row. Omitting a check the
    // turn did not produce made the strips different lengths (20 vs 24 on a real
    // agent) and moved every dimension after it.
    const ALL = ['grounded_overlap', 'groundedness', 'content_safety', 'cost'];
    const partial = [{ dimension: 'grounded_overlap', kind: 'metric', band: 'danger' },
                     { dimension: 'cost', kind: 'metric', band: 'healthy' }];
    const full = ALL.map((d) => ({ dimension: d, kind: d === 'cost' ? 'metric' : 'judge', band: 'healthy' }));
    const nMarks = (h) => (h.match(/<span class="ap-sq/g) || []).length;
    check('a turn missing checks still fills the grid',
        nMarks(dimStrip(partial, {}, ALL)), ALL.length);
    check('…and a complete turn is the same length',
        nMarks(dimStrip(full, {}, ALL)), nMarks(dimStrip(partial, {}, ALL)));
    // Same column, every row — the property that makes vertical reading possible.
    const colOf = (h, d) => (h.match(/<span class="ap-sq[^>]*>/g) || [])
        .findIndex((m) => m.includes(`data-dim="${d}"`));
    check('…and every dimension keeps its column',
        ALL.every((d) => colOf(dimStrip(partial, {}, ALL), d) === colOf(dimStrip(full, {}, ALL), d)), true);
    // A check that did not run is an ABSENCE, and must say so rather than borrow an
    // abstention's words or a pass's colour.
    const missing = (dimStrip(partial, {}, ALL).match(/<span class="ap-sq[^>]*data-dim="groundedness"[^>]*>/) || [''])[0];
    check('a check that did not run renders in the absence family',
        /ap-sq-na/.test(missing), true);
    check('…and says so in words', /did not run on this turn/.test(dimStrip(partial, {}, ALL)), true);
    check('…and keeps its shape, because shape says WHO measures it, not whether it ran',
        /ap-sq-j\b/.test(missing), true);
    // The grid is built from the UNFILTERED runs — a filter must not move the columns.
    check('the grid is computed once, from every run',
        /const _allDims = \(\(\) => \{[\s\S]{0,400}\(data\.runs \|\| \[\]\)\.forEach/.test(hsrc), true);
    check('…and the strip is given it', /dimStrip\(r\.verdicts, r, _allDims\)/.test(hsrc), true);
    // Absent the grid (any other caller), it degrades to the turn's own dimensions.
    check('no grid supplied → the turn’s own checks, as before',
        nMarks(dimStrip(partial, {})), partial.length);

    // ── one word, one population ────────────────────────────────────────────
    // Two things were both called "judges": a JUDGE is one check asked once of the
    // turn; the MODELS are its panel, voting inside it. On one row "1 judge flagged
    // this" counted checks while "(judges 0/1)" counted models — the same word, two
    // populations, a few pixels apart.
    const words = (h) => (/>([^<]+)<\/span>$/.exec(h) || [, ''])[1]
        + ' ' + (/data-tip="([^"]*)"/.exec(h) || [, ''])[1];
    const gateChip = _judgeTag({ gate: { escalated: true, cleared: true, judge_votes: '2/3' }, verdicts: [] });
    check('a model count is labelled MODELS, never judges', /2\/3 models/.test(gateChip), true);
    check('…and never as a bare fraction beside a judge count',
        /judges 2\/3|\(2\/3\)/.test(words(gateChip)), false);
    const actChip = _actedTag({ gate: { grounding: 'block', escalated: true, judge_votes: '0/1' } });
    check('the act chip counts models as models too',
        /models on its panel/.test(words(actChip)), true);
    check('…and no longer calls them judges', /judges 0\/1/.test(words(actChip)), false);
    // The judge tally counts CHECKS and says so where the number is explained.
    // The population is now anchored by a definition rather than a parenthetical gloss,
    // because "judges (checks)" was read as jargon and did not settle the question.
    check('the panel tally anchors its population',
        /A judge is one check; the models on its panel vote inside it/.test(words(t1)), true);

    // The gate NOTE is the third place the same fraction appears — it must count the
    // same population as the other two. It was the one site left calling a judge's
    // panel "judges", so a count of models read as a count of checks.
    // eslint-disable-next-line no-new-func
    const _runGateNote = new Function('_GATE_ACT', 'esc', `${grab('_runGateNote')}\nreturn _runGateNote;`)(
        { grounding_block: 'answer withheld by the grounding gate', inbound_block: 'request refused',
          outbound_block: 'answer withheld', outbound_redact: 'personal data redacted' },
        (x) => String(x));
    const note = (g) => _runGateNote({ gate: g }).replace(/<[^>]*>/g, '');
    check('the gate note counts MODELS, not judges',
        /0 of 1 models upheld it/.test(note({ grounding: 'block', judge_votes: '0/1' })), true);
    check('…and never labels them judges',
        /judges 0\/1|\(judges /.test(note({ grounding: 'block', judge_votes: '0/1' })), false);
    check('a cleared escalation says it too',
        /3 of 3 models upheld it/.test(note({ escalated: true, cleared: true, judge_votes: '3/3' })), true);
    // A vote shape the parser does not know is still the gate's record.
    check('…and an unparsed vote string is shown, not dropped',
        /its panel voted 2✓\/1✗/.test(note({ grounding: 'block', judge_votes: '2✓/1✗' })), true);
    check('no gate act, no note', note({ grounding: 'pass' }), '');

    // ── no two chips may say the same thing, or share a verb ────────────────
    // A row carried `judge-flagged` AND `1 judge flagged this` — the SAME groundedness
    // ruling stated twice, in the same words. And `cleared` meant a withdrawn
    // conviction on one chip and a cleared answer on another. Three facts, two verbs.
    const V = (n, b) => ({ dimension: n, kind: 'judge', band: b });
    const txt = (x) => (/>([^<]+)<\/span>$/.exec(x || '') || [, ''])[1];
    check('the 3.0c marks are named for their EFFECT, not with a shared verb',
        /_disp\('not counted as failed'/.test(hsrc) && /_disp\('passed, but disputed'/.test(hsrc), true);
    check('…so no chip repeats another’s verb',
        /_disp\('judge-cleared'|_disp\('judge-flagged'/.test(hsrc), false);
    // The tally is SILENT where the 3.0c mark already accounts for every flagged judge.
    check('one ruling is not stated twice',
        _judgeTag({ judge_flagged: ['grounded_overlap'],
                    verdicts: [V('groundedness', 'danger'), V('content_safety', 'healthy')] }), '');
    check('…but a second, unexplained flag still speaks',
        /2 judges flagged this/.test(_judgeTag({ judge_flagged: ['grounded_overlap'],
            verdicts: [V('groundedness', 'danger'), V('content_safety', 'danger')] })), true);
    check('…and with no 3.0c mark the tally is unaffected',
        /1 judge flagged this/.test(_judgeTag({
            verdicts: [V('groundedness', 'danger'), V('content_safety', 'healthy')] })), true);
    // A CLEARED pair leaves its judge healthy, so any flag is a different judge —
    // suppressing there would hide a real finding.
    check('a cleared pair never silences an unrelated flag',
        /1 judge flagged this/.test(_judgeTag({ disputed: ['grounded_overlap'],
            verdicts: [V('groundedness', 'healthy'), V('content_safety', 'danger')] })), true);

    // ── the key ─────────────────────────────────────────────────────────────
    // Three channels now ride the strip, so it carries a real legend — and every
    // sample is drawn by the SHIPPED builder, so the key cannot drift from the marks.
    check('every channel is named in the key',
        /fill — what it found/.test(hsrc) && /shape — who measured/.test(hsrc)
        && /size — what it could do/.test(hsrc) && /centre — the refining judge disagreed/.test(hsrc)
        && /a dot on the row/.test(hsrc), true);
    check('…drawn by the shipped mark builder, never hand-rolled',
        /_keyItem\(bandMark\('healthy'\)\)|_keyItem\(bandMark\('healthy'\), /.test(hsrc), true);
    check('…all four states are shown',
        ['healthy', 'review', 'danger', 'na'].every((b) => hsrc.includes(`_keyItem(bandMark('${b}')`)), true);
    check('…the kind row is offered only when a judge is in the pool (honest absence)',
        /_hasJudge \? _keyRow\('shape/.test(hsrc), true);
    check('…the size row states what the ruling could DO',
        /_hasJudge \? _keyRow\('size — what it could do'/.test(hsrc), true);
    check('…and the correction row explains BOTH directions, only where one occurred',
        /\(r\.disputed \|\| \[\]\)\.length \|\| \(r\.judge_flagged \|\| \[\]\)\.length\) \? _keyRow\('centre/.test(hsrc)
        && /green — the rule measured a failure/.test(hsrc)
        && /red — the rule passed and that judge disputed it/.test(hsrc), true);
    // Isolating a channel means holding every OTHER channel constant — the shape row
    // draws both samples in the same state, so the only thing varying is shape.
    check('…the shape row isolates shape by holding the state constant',
        /_keyRow\('shape[\s\S]{0,400}bandMark\('danger'\)[\s\S]{0,200}bandMark\('danger', '', true\)/.test(hsrc), true);
    // …and it draws them in DANGER, because that is where the distinction bites: a
    // failed rule convicts the turn, a flagged judge does not (a model opined).
    check('…and names the consequence, which differs at the same state',
        /a failure here fails the turn/.test(hsrc)
        && /a flagged judge does not fail the turn on its own/.test(hsrc), true);
    // A key you must discover is not a key. Open unless explicitly dismissed.
    check('the key is shown by default',
        /localStorage\.getItem\('tda_strip_key'\) !== '0'/.test(hsrc), true);
    check('…and dismissing it persists', /localStorage\.setItem\('tda_strip_key'/.test(hsrc), true);
    check('…the toggle reads the COMPUTED style, not what it last wrote',
        /getComputedStyle\(box\)\.display === 'none'/.test(hsrc), true);
    check('the key is a toggle, and toggling it does not collapse open rows',
        /data-ap-stripkey/.test(hsrc) && !/data-ap-stripkey[\s\S]{0,400}renderRunsLedger/.test(hsrc), true);

    // ── ONE predicate ───────────────────────────────────────────────────────
    // Two marks describing one measurement must not disagree about its kind.
    const kindTests = (hsrc.match(/kind === 'judge'/g) || []).length;
    check('the judge predicate is declared once', kindTests, 1);
    check('…and the chip tag reads it', /const judged = isJudgeVerdict\(v\)/.test(hsrc), true);
    check('…as does the heatmap column header', /isJudgeDim\(d\) \? esc\(' ·judge'\)/.test(hsrc), true);

    done();
}

// ── the judge step names its layer, on both surfaces ────────────────────────
// One event type serves the scoring panel AND a gate's in-path escalation, and only
// one of them could change what the user received. The distinction must survive a
// reload too, or it lasts only as long as the live stream.
{
    const done = report('the judge step names its layer');
    const esrc = fs.readFileSync(new URL('../static/js/eventHandlers.js', import.meta.url), 'utf8');
    const usrc = fs.readFileSync(new URL('../static/js/ui.js', import.meta.url), 'utf8');

    // Every profile branch must resolve the title from the layer — one that hardcodes
    // it flattens the distinction for that engine only, which is worse than not having it.
    const titleFns = ['getToolEnabledTitle', 'getLlmOnlyTitle', 'getRagFocusedTitle', 'getGenieTitle'];
    for (const fn of titleFns) {
        const i = esrc.indexOf(`function ${fn}(`);
        const body = esrc.slice(i, esrc.indexOf('\n}', i) + 2);
        // eslint-disable-next-line no-new-func
        const f = new Function(`${body}\nreturn ${fn};`)();
        check(`${fn}: an in-flight judge is named as one`,
            f('judge_evaluation_complete', { details: { layer: 'in_flight' } }), 'Gate Consulted a Judge');
        check(`${fn}: a sampled one keeps the norm`,
            f('judge_evaluation_complete', { details: { layer: 'post_hoc' } }), 'Agent Quality Judged');
        // An older event carries no layer — it must read as the norm, never claim a
        // certainty the record does not have.
        check(`${fn}: a legacy event claims nothing`,
            f('judge_evaluation_complete', { details: {} }), 'Agent Quality Judged');
    }

    // The card states it in words, and says what it MEANS — a layer name alone would
    // be a wire token on a user-facing surface.
    const i = usrc.indexOf('function _renderJudgeEvalCompleteDetails');
    const body = usrc.slice(i, usrc.indexOf('\n}', usrc.indexOf('</div>\n    `;', i)) + 2);
    // eslint-disable-next-line no-new-func
    const card = new Function('_trustChip', '_trustVal', 'escapeHtml', `${body}\nreturn _renderJudgeEvalCompleteDetails;`)(
        (b, t) => t, (b) => `data-b="${b}"`, (x) => String(x));
    const row = (h) => (h.match(/status-kv-key">When<\/div>\s*<div[^>]*>([^<]*)</) || [, ''])[1].trim();
    const base = { input_tokens: 10, output_tokens: 5, cost_usd: 0.0001, judge_models: ['m'] };
    check('the card names the in-flight layer',
        /during the turn/.test(row(card({ ...base, layer: 'in_flight' }))), true);
    check('…and says what it could do', /withhold the answer/.test(row(card({ ...base, layer: 'in_flight' }))), true);
    check('the card names the post-hoc layer',
        /after the turn/.test(row(card({ ...base, layer: 'post_hoc' }))), true);
    check('…and says it changed nothing', /changed nothing/.test(row(card({ ...base, layer: 'post_hoc' }))), true);
    check('a legacy event gets no row rather than a guess', row(card(base)), '');
    check('neither surface leaks the wire token',
        /in_flight|post_hoc/.test(row(card({ ...base, layer: 'in_flight' }))
            + row(card({ ...base, layer: 'post_hoc' }))), false);

    // A panel that SPENT but recorded no ruling. The card used to omit the Verdicts
    // row entirely, so it announced "LLM judge panel · N models" over a real bill with
    // nothing behind it — a spend wearing a judgement's clothes (production session
    // 4a2169f3: $0.019407 billed, zero verdicts, and the card said only that a panel
    // ran). Absence is a finding: the card must state it, and the headline must not
    // claim a ruling the record cannot show.
    const spent = card({ ...base, layer: 'post_hoc' });
    check('a billed panel with no ruling says so', /no ruling recorded/.test(spent), true);
    check('…and does not present the spend as a judgement',
        /LLM judge panel — no ruling/.test(spent), true);
    check('…while still showing that the tokens were spent',
        /tokens above were still spent/.test(spent), true);
    // …and the opposite: a real ruling reads as one, with no absence note.
    const ruled = card({ ...base, layer: 'post_hoc',
        verdicts: [{ dimension: 'groundedness', passed: true, votes: [{ model: 'm', verdict: 'pass' }] }] });
    check('a card with a ruling claims one', /LLM judge panel(?! — no ruling)/.test(ruled), true);
    check('…and carries no absence note', /no ruling recorded/.test(ruled), false);
    check('…and shows the tally', /1 pass/.test(ruled), true);
    done();
}

// ── the lane cut's own invariants ────────────────────────────────────────────
// The properties `_execAtJudged` and the PROVEN branch encode. Asserted on the RENDERED
// station rather than by grepping the helper, so a caller that stops using it fails too.
section('the lane cut — a guarantee station reports its mechanisms, not measurements');
{
    const cell = { turns: 8, failures: 4, tag: 'A',
        dimensions: { grounded_overlap: 0.5, safety: 0.5, provenance_integrity: 1,
                      must_not_call: 1, completed: 1 },
        dimension_counts: { grounded_overlap: 8, safety: 8, provenance_integrity: 8,
                            must_not_call: 8, completed: 8 },
        turn_failed_dims: [['grounded_overlap'], ['safety'], ['grounded_overlap'],
                           ['safety'], [], [], [], []],
        gate: { acted: 2, blocked: 1, cleared: 1, refused: 1, redacted: 1 },
        spans_scored: 8 };
    const st = stations([P('A', 'enforce', 'enforce')], [T('A', 'trusted')],
        { agent: 'A', dashboard: { enabled: true, by_agent: { A: cell } }, byAgent: { A: cell } });

    // 1 · every guarantee station renders `—` for execution, and NAMES the destination.
    //     `—` alone would be reach loss: the fact still exists but is further away.
    for (const k of ['grounded', 'guarded', 'bounded']) {
        check(`${k}: execution renders — rather than collapsing (Rule 1.2)`,
            st[k].value.endsWith('· —'), true);
        check(`${k}: …and names where its checks are counted`,
            /counted at JUDGED/.test(st[k].execution.detail || ''), true);
        check(`${k}: …with an honest absence state, never a vacuous pass`,
            st[k].execution.state, 'na');
    }
    // 2 · a station WITH acts reports them; one without says so rather than inventing.
    check('GROUNDED reports its own gate acts as execution',
        /withheld|cleared/.test(st.grounded.execution.detail || ''), true);
    check('GUARDED reports its own guard acts as execution',
        /refused|redacted/.test(st.guarded.execution.detail || ''), true);
    check('BOUNDED has no acts to report and does not invent one',
        /withheld|cleared|refused|redacted/.test(st.bounded.execution.detail || ''), false);
    // 3 · PROVEN keeps a real reading: its mechanism's success condition IS its act.
    //     A seal is produced or it is not — unlike a gate, where acting ≠ passing.
    check('PROVEN keeps its receipt count as the value',
        /\d+ sealed/.test(String(st.proven.execution.text || '')), true);
    check('PROVEN is no longer coloured by its record-soundness checks',
        st.proven.execution.state, 'pass');
    check('PROVEN still names those checks and where they are counted',
        /counted at JUDGED/.test(st.proven.execution.detail || ''), true);
    // 4 · JUDGED carries everything — across every guarantee's label.
    const jd = (st.scored.derivation || {}).dims || [];
    check('JUDGED counts checks from every guarantee label',
        ['grounded_overlap', 'safety', 'provenance_integrity', 'must_not_call']
            .every((k) => jd.some((x) => x.key === k)), true);
    check('…and it is the only station whose colour a measurement can move',
        ['grounded', 'guarded', 'bounded', 'proven']
            .every((k) => st[k].execution.state === 'na' || st[k].execution.state === 'pass'), true);
    report('a guarantee reports acts; JUDGED reports measurement')();
}

// ── W1 · a chosen posture is a reading, not an alarm ─────────────────────────
// TRUST_CIRCLE_LEGIBILITY.md W1. The three mode→colour maps (ring, posture matrix,
// anatomy) disagreed about `observe`; now ONE map (trustGrammar.POSTURE_STATE) is
// read everywhere, and a chosen non-protecting posture is `watching` — absence
// family, never amber (Rule 4.4 extended), never green (Rule 5.1 kept).
section('W1 — posture is a reading: one map, one fold, one aggregate, one chip');
{
    const done = report('W1 — the posture reading');
    const hsrc = fs.readFileSync(SRC, 'utf8');
    // The map itself, mirrored from core/posture_modes.py (pinned byte-equal there).
    check('W1 POSTURE_STATE: protecting reads pass', TG.postureState('enforce') === 'pass' && TG.postureState('review') === 'pass', true);
    check('W1 POSTURE_STATE: watching reads watching', TG.postureState('observe') === 'watching' && TG.postureState('annotate') === 'watching', true);
    check('W1 POSTURE_STATE: off and garbage read na', TG.postureState('off') === 'na' && TG.postureState('bogus') === 'na', true);
    check('W4 POSTURE_LABEL speaks the editor\'s words', TG.postureLabel('annotate'), 'watch + disclose');
    check('W4 POSTURE_LABEL: review is enforce + keep for review', TG.postureLabel('review'), 'enforce + keep for review');
    check('W4 POSTURE_LABEL: an unknown token stays visible, never renamed', TG.postureLabel('bogus'), 'bogus');

    // The fold — attention first, posture second, absence last. Executed as the
    // shipped `_attention` (extracted verbatim), driven as a truth table.
    const aIdx = hsrc.indexOf('function _attention(');
    const aEnd = hsrc.indexOf('\n    }', aIdx) + '\n    }'.length;
    const attention = new Function(`${hsrc.slice(aIdx, aEnd)}\nreturn _attention;`)();
    const TABLE = [
        [['watching', 'na'], 'watching'], [['na', 'watching'], 'watching'],
        [['watching', 'pass'], 'pass'], [['watching', 'warn'], 'warn'], [['watching', 'danger'], 'danger'],
        [['pass', 'na'], 'pass'], [['na', 'na'], 'na'], [['warn', 'pass'], 'warn'], [['danger', 'warn'], 'danger'],
        [['watching', 'watching'], 'watching'], [[undefined, 'watching'], 'watching'],
    ];
    for (const [[a, b], want] of TABLE) check(`W1 _attention(${a}, ${b}) = ${want}`, attention(a, b), want);
    check('W1 the handler fold equals the grammar\'s', TABLE.every(([[a, b], w]) => TG.attention(a, b) === w), true);

    // The aggregate — worst-of with the posture fallback, executed.
    const gIdx = hsrc.indexOf('function _postureAggregate(');
    const gEnd = hsrc.indexOf('\n    }', gIdx) + '\n    }'.length;
    const agg = new Function(`${hsrc.slice(gIdx, gEnd)}\nreturn _postureAggregate;`)();
    const AGG = [
        [['pass', 'watching'], 'watching'], [['watching', 'watching'], 'watching'], [['pass', 'pass'], 'pass'],
        [['na', 'na'], 'na'], [['pass', 'na'], 'watching'], [['watching', 'warn'], 'warn'],
        [['pass', 'danger'], 'danger'], [[], 'na'], [['na', 'watching'], 'watching'],
    ];
    for (const [st, want] of AGG) check(`W1 _postureAggregate(${st.join(',') || 'empty'}) = ${want}`, agg(st), want);
    check('W1 the handler aggregate equals the grammar\'s', AGG.every(([st, w]) => TG.postureAggregate(st) === w), true);

    // The posture matrix chip reads the SAME map and the SAME words (it used to render
    // observe neutral while the ring read amber — two surfaces, one fact, two answers).
    const cIdx = hsrc.indexOf('function _modeChip(');
    const cEnd = hsrc.indexOf('\n    }', cIdx) + '\n    }'.length;
    const chip = new Function('_postureState', '_postureLabel', 'esc',
        `${hsrc.slice(cIdx, cEnd)}\nreturn _modeChip;`)(TG.postureState, TG.postureLabel, (x) => String(x));
    check('W1 matrix: observe is watching (dashed), not neutral', /tc-watching/.test(chip('observe')), true);
    check('W1 matrix: annotate is watching, not amber', /tc-watching/.test(chip('annotate')) && !/tc-warn/.test(chip('annotate')), true);
    check('W1 matrix: enforce and review are pass', /tc-pass/.test(chip('enforce')) && /tc-pass/.test(chip('review')), true);
    check('W4 matrix: the chip prints the reader\'s word', />watch \+ disclose</.test(chip('annotate')), true);
    check('W4 matrix: …never the storage token', />annotate</.test(chip('annotate')) || />review</.test(chip('review')), false);
    check('W1 matrix: an off master still reads inert', /off \(master\)/.test(chip('enforce', true)), true);

    // The served effective posture reaches the ring (the fixture never set `posture`
    // before — the primary path of `_gateMode` was unexercised).
    const inherited = { ...P('A', undefined, undefined), groundingConfig: {}, guardrailConfig: {},
        posture: { grounding: { mode: 'review', source: 'agent' }, outbound: { mode: 'annotate', source: 'agent' },
                   inbound: { mode: 'annotate', source: 'agent' }, honesty: { mode: 'observe', source: 'default' },
                   corpus: { mode: 'review', source: 'grounding' } } };
    const eff = stations([inherited], [T('A', 'unsigned')], { agent: 'A' });
    check('W1 the served effective mode drives the ring (inherited review → pass)', eff.grounded.design.state, 'pass');
    check('W4 …and is printed in the reader\'s words', eff.grounded.design.text, 'enforce + keep for review');
    check('W1 inherited annotate on the guard → watching', eff.guarded.design.state, 'watching');
    done();
}

// ── W5 · one station registry, and each station leads with its question ───────
// TRUST_CIRCLE_LEGIBILITY.md W5. Seven label registries and four subtitle copies held
// the eight stations and drifted (JUDGED carried two subtitles; `scored → JUDGED` was
// hand-written at eight sites). ONE table now: assurance_points.STATION_TABLE,
// mirrored byte-equal in trustGrammar.STATIONS (pinned by test_assurance_points);
// every surface reads it.
section('W5 — one station registry; a station leads with the question it answers');
{
    const done = report('W5 — the station registry');
    const rsrc = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'handlers', 'trustCircle.js'), 'utf8');
    const dsrc = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'handlers', 'assuranceDial.js'), 'utf8');
    const usrc = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'ui.js'), 'utf8');
    const qsrc = fs.readFileSync(QD_SRC, 'utf8');
    const hsrc = fs.readFileSync(SRC, 'utf8');
    const spIdx = rsrc.indexOf('function derivationPanel(d)');
    const spEnd = rsrc.indexOf('\n    // ── rendering states onto the ring', spIdx);
    const escFn = (x) => String(x == null ? '' : x).replace(/[&<>"']/g, (c) =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const mkPanel = (derivOpen, win) => new Function('esc', '_derivOpen', 'window',
        `${rsrc.slice(spIdx, spEnd)}\nreturn stationPanel;`)(escFn, derivOpen, win || guideStub());
    const rows = TG.STATIONS;
    check('W5 the grammar carries the eight stations, in order', rows.map((r) => r.key).join(','), STATION_KEYS.join(','));
    for (const r of rows) {
        check(`W5 ${r.key} has a question ending in ?`, /\?$/.test(r.q || ''), true);
        check(`W5 ${r.key} question is plain prose (no digits, no shouting)`, /\d|[A-Z]{2,}/.test(r.q), false);
        check(`W5 ${r.key} has a character line`, !!(r.sub && r.sub.length), true);
    }
    check('W5 scored is labelled Judged in the registry', TG.stationLabel('scored'), 'JUDGED');
    check('W5 an unknown key stays visible', TG.stationLabel('bogus'), 'BOGUS');
    // The renderer's fallback literal equals the canonical, row for row.
    const STATIONS_META = {};
    for (const m of rsrc.matchAll(/\{ key: '(\w+)',\s*label: '([^']+)',\s*sub: '([^']+)'(?:,\s*q: '([^']+)')? \}/g)) {
        STATIONS_META[m[1]] = { label: m[2], sub: m[3], q: m[4] || '' };
    }
    const metaKeys = Object.keys(STATIONS_META);
    check('W5 the ring\'s fallback literal parsed all eight rows (with q)', metaKeys.length, 8);
    for (const r of rows) {
        const m = STATIONS_META[r.key] || {};
        check(`W5 ring fallback ${r.key} equals the registry`,
            m.label === r.label && m.sub === r.sub && m.q === r.q, true);
    }
    check('W5 the ring reads trustGrammar.STATIONS first',
        /const STATIONS = \(globalThis\.TrustGrammar && Array\.isArray\(globalThis\.TrustGrammar\.STATIONS\)/.test(rsrc), true);
    // The dial, the studio and the Live Status chip read the same registry.
    check('W5 the dial derives ORDER from the registry', /const ORDER = _TGS \? _TGS\.map\(\(r\) => r\.key\)/.test(dsrc), true);
    check('W5 the dial derives LABEL from the registry', /const LABEL = _TGS \? Object\.fromEntries/.test(dsrc), true);
    check('W5 the dial prints the station label through one rule', /stationLabel\(r\.station\)/.test(dsrc), true);
    const bsrc = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'handlers', 'assuranceBindings.js'), 'utf8');
    check('W5 the studio derives its stations from the registry',
        /const STATIONS = _TGS \? _TGS\.map\(\(r\) => \[r\.key, String\(r\.label\)\.toUpperCase\(\), r\.q\]\)/.test(bsrc), true);
    check('W5 the Live Status chip reads TrustGrammar.stationLabel', /TrustGrammar\.stationLabel\(st\)/.test(usrc), true);
    check('W5 governed_data is attributed to GROUNDED on the Live Status card (was scored)',
        /governed_data: 'grounded'/.test(usrc), true);
    // No hand-written `scored → JUDGED` survives outside the registry and its pinned fallbacks.
    for (const [name, src] of [['ui.js', usrc], ['agentPerformanceHandler.js', hsrc], ['qualityDimensions.js', qsrc]]) {
        check(`W5 no 'JUDGED' literal left in ${name}`, /'JUDGED'/.test(src), false);
    }
    check('W5 the handler labels inbox rows through the registry', /_stationLabel\(_rowSt\)/.test(hsrc) && /_stationLabel\(st\)/.test(hsrc), true);
    // The admin reporting grid (hand-written HTML) agrees with the registry, name and character.
    const idx = fs.readFileSync(path.join(HERE, '..', 'templates', 'index.html'), 'utf8');
    for (const r of rows) {
        const re = new RegExp(`trust-report-name">${r.label.toUpperCase()}</span>\\s*<span class="trust-report-sub">([^<]*)</span>`);
        const m = idx.match(re);
        check(`W5 admin grid names ${r.key}`, !!m, true);
        check(`W5 admin grid ${r.key} subtitle equals the registry`, m ? m[1] : null, r.sub);
    }
    // The panel leads with the question, in both the peek and the pinned form.
    const stQ = { key: 'grounded', label: 'Grounded', sub: 'truth · faithfulness', q: 'Does it stay on its corpus?' };
    const dQ = { state: 'watching', facts: [], headline: 'Drift is recorded.', derivation: null };
    check('W5 the peek leads with the question', /class="tc-q">Does it stay on its corpus\?</.test(mkPanel(null)(stQ, dQ, false)), true);
    check('W5 the pinned panel leads with the question', /class="tc-q">Does it stay on its corpus\?</.test(mkPanel('grounded')(stQ, dQ, true)), true);
    check('W5 …above the consequence headline',
        (() => { const h = mkPanel('grounded')(stQ, dQ, true); return h.indexOf('tc-q') < h.indexOf('tc-headline') && h.indexOf('tc-q') > h.indexOf('tc-eyebrow'); })(), true);
    check('W5 the node aria-label carries the question', /aria-label="\$\{esc\(st\.label\)\} — \$\{esc\(st\.q \|\| st\.sub\)\}"/.test(rsrc), true);
    check('W5 the registry is exported from TrustCircle', /STATIONS \};\s*\}\)\(\);\s*$/.test(rsrc), true);
    done();
}

// ── W2 · the verdict line — one sentence from the stations' own values ────────
// TRUST_CIRCLE_LEGIBILITY.md W2. The glance layer: a clause per station, derived ONLY
// from that station's value/state (Rule 3b.5), attention clauses first, no doctrine
// (3.3), every figure traceable to a slot (3.5), never "clean" from nothing (3.7).
section('W2 — the verdict line is built from the stations and leads the panel');
{
    const done = report('W2 — the verdict');
    const hsrc = fs.readFileSync(SRC, 'utf8');
    const rsrc = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'handlers', 'trustCircle.js'), 'utf8');
    const vIdx = hsrc.indexOf('function _verdictLine(');
    const vEnd = hsrc.indexOf('\n    }', vIdx) + '\n    }'.length;
    check('W2 the builder is extractable', vIdx > 0 && vEnd > vIdx, true);
    // The builder reads the attention words and the posture maps from its closure;
    // inject the grammar's own so the extraction runs the shipped primary path.
    const verdict = new Function('_ATTN_WORD', '_postureState', '_POSTURE_LABEL',
        `${hsrc.slice(vIdx, vEnd)}\nreturn _verdictLine;`)(TG.ATTN_WORD, TG.postureState, TG.POSTURE_LABEL);
    const traceable = (v, sts) => v.clauses.every((c) => {
        const s = sts[c.key];
        const nums = (c.text.match(/\d+/g) || []);
        const pool = `${s.value} ${(s.facts || []).map((f) => `${f.v} ${f.d}`).join(' ')}`;
        return nums.every((n) => pool.includes(n));
    });

    // Focused: watching agent, unsigned, no turns.
    const quiet = stations([P('A', 'annotate', 'observe')], [T('A', 'unsigned')], { agent: 'A', dashboard: { enabled: true } });
    const vq = verdict(Object.values(quiet), { label: '@A', kind: 'agent' });
    check('W2 the lead is the subject', vq.lead, '@A');
    check('W2 watching gates read as ONE clause that says they stop nothing',
        /watches drift and unsafe content, stops neither/.test(vq.text), true);
    check('W2 the verdict is three rows at most, with their keys', vq.lines.length <= 3 && vq.lines.every((l) => ['needs you', 'posture', 'record'].includes(l.k)), true);
    check('W2 no invented terminology (the platform says grounding gate and guardrails)', /drift gate/.test(vq.text), false);
    check('W2 no stored posture label in the rows', /watch \+ disclose|enforce \+ keep/.test(vq.text), false);
    check('W2 an unsigned agent is said plainly', /not signed/.test(vq.text), true);
    check('W2 Rule 3.7 — no turns is never clean', /no turns graded yet/.test(vq.text) && !/clean/.test(vq.text), true);
    check('W2 no storage token in the verdict (W4)', /\bannotate\b|\breview\b/.test(vq.text), false);
    check('W2 every figure traces to a station slot (3.5)', traceable(vq, quiet), true);
    check('W2 no doctrine in the verdict (3.3)', /graded in code|receipts export|dimensions/.test(vq.text), false);

    // Focused: a failing JUDGED leads, enforce stops drift, graded turns are counted.
    const bad = { turns: 4, failures: 3, dimensions: { grounded_overlap: 0.25, completed: 0.25 },
                  dimension_counts: { grounded_overlap: 4, completed: 4 } };
    const hot = stations([P('A', 'enforce', 'review', { restricted: true, autonomous: false, constraints: ['deny_writes'] })],
        [T('A', 'trusted')], { agent: 'A', dashboard: { enabled: true, by_agent: { A: bad } }, byAgent: { A: bad } });
    const vh = verdict(Object.values(hot), { label: '@A', kind: 'agent' });
    check('W2 the danger clause leads', vh.clauses[0].state, 'danger');
    check('W2 …and it is the graded-turns clause', /of 4 turns graded clean/.test(vh.clauses[0].text), true);
    check('W2 enforce reads as stopping drift and unsafe content', /stops drift and unsafe content/.test(vh.text), true);
    const rev = stations([P('A', 'review', 'review')], [T('A', 'trusted')], { agent: 'A' });
    check('W2 keep-for-review on both gates reads as one suffix',
        /stops drift and unsafe content, keeping originals for review/.test(verdict(Object.values(rev), { label: '@A', kind: 'agent' }).text), true);
    check('W2 the needs-you row leads and carries the worst state', vh.lines[0].k === 'needs you' && vh.lines[0].state === 'danger', true);
    check('W2 the open-items driver names the inbox item', (() => {
        const oi = stations([P('A', 'annotate', 'observe')], [T('A', 'trusted')], { agent: 'A',
            inboxByTag: { A: { open: 1, derived: 0, derivedDanger: 0, top: '', stations: {}, openItems: { grounded: { state: 'warn', count: 1 } } } } });
        return /grounding gate: 1 open inbox item/.test(verdict(Object.values(oi), { label: '@A', kind: 'agent' }).text); })(), true);
    check('W2 a scoped agent reads as acting within its scope', /acts only within its scope/.test(vh.text), true);
    check('W2 signed and attested', /signed and attested/.test(vh.text), true);
    check('W2 every figure traces to a station slot (3.5)', traceable(vh, hot), true);

    // Mixed gates (grounding watching, guardrails enforcing) read as two short clauses.
    const mixed = stations([P('A', 'observe', 'enforce')], [T('A', 'trusted')], { agent: 'A' });
    check('W2 mixed gates read as two clauses', /watches drift · stops unsafe content/.test(verdict(Object.values(mixed), { label: '@A', kind: 'agent' }).text), true);
    // JUDGED's figure is read wherever the detail carries it ("N/M" or "N of M turns").
    const alt = stations([P('A', 'enforce', 'enforce')], [T('A', 'trusted')], { agent: 'A', dashboard: { enabled: true } });
    alt.scored.execution.text = '71%'; alt.scored.execution.detail = '5 of 7 turns clean on every check measured';
    check('W2 JUDGED ratio read from an "N of M turns" detail', /5 of 7 turns graded clean/.test(verdict(Object.values(alt), { label: '@A', kind: 'agent' }).text), true);

    // Estate form.
    const est = stations([P('A', 'enforce', 'enforce'), P('B', 'observe', 'observe'), P('C', 'observe', 'observe')],
        [T('A', 'trusted'), T('B', 'unsigned'), T('C', 'changed')], { dashboard: { enabled: true } });
    const ve = verdict(Object.values(est), { label: 'Agent estate', kind: 'estate' });
    check('W2 estate: signed count', /1 of 3 signed/.test(ve.text), true);
    check('W2 estate: drift-stopping count', /1 of 3 stops drift/.test(ve.text), true);
    const est2 = stations([P('A', 'enforce', 'enforce'), P('B', 'enforce', 'observe'), P('C', 'observe', 'observe')],
        [T('A', 'trusted'), T('B', 'unsigned'), T('C', 'changed')], { dashboard: { enabled: true } });
    check('W2 estate: plural agreement (2 of 3 stop drift)', /2 of 3 stop drift/.test(verdict(Object.values(est2), { label: 'Agent estate', kind: 'estate' }).text), true);
    check('W2 estate: no graded agents is never clean', /no agents graded yet/.test(ve.text), true);
    check('W2 estate: every figure traces (3.5)', traceable(ve, est), true);

    // The ring leads with it, the doctrine is folded, and the roster tooltip reuses it.
    check('W2 the model carries the verdict', /verdict: _verdictLine\(stations, center\)/.test(hsrc), true);
    // V2 (Oct 2026) — the pane reads like the Anatomy pane: eyebrow · headline · ONE italic
    // sentence (the verdict) · copy · the three reading groups · the folded full key · acts.
    const dp = rsrc.slice(rsrc.indexOf('function defaultPanel()'), rsrc.indexOf('/** The derivation explainer'));
    check('V2 the default panel is eyebrow → headline → verdict, before any copy or legend',
        (() => { const i = (re) => dp.search(re); return i(/tc-eyebrow/) < i(/tc-headline/) && i(/tc-headline/) < i(/_verdictProse\(\)/)
                 && i(/_verdictProse\(\)/) < i(/tc-copy/) && i(/tc-copy/) < i(/_attentionSummary\(\)/) && i(/_attentionSummary\(\)/) < i(/tc-howto/); })(), true);
    check('V2 the verdict is ONE sentence whose lead carries the attention class, the body none',
        /<p class="tc-verdict-prose"><span class="tc-v-lead tc-v-\$\{lead\.st\}">/.test(rsrc) && !/class="tc-vrows"/.test(rsrc), true);
    check('V2 the pane never restates the tally in a banner (Rule 3.0)', /tc-attn/.test(dp), false);
    check('V2 the full key is folded inside the How-to disclosure',
        (() => { const h = dp.indexOf('<details class="tc-howto">'); const e = dp.indexOf('</details>', h);
                 return h > 0 && dp.indexOf('tc-leg-pending', h) < e && dp.indexOf('tc-legend-marks', h) < e; })(), true);
    check('V2 the live-event line survives, once, in the footer', (dp.match(/id="tc-lastevent-text"/g) || []).length === 1 && /tc-foot/.test(dp), true);
    // The headline names the ring's stations in the ring's order — the words after "born" are
    // STATIONS labels, so the sentence cannot drift from the registry (the operator caught
    // "grounded" missing from it).
    const hl = (dp.match(/<h4 class="tc-headline">([^<]*)/) || [, ''])[1];
    const hlWords = hl.split('·').map((w) => w.trim().toLowerCase()).filter(Boolean).map((w) => w.replace(/\s*—.*$/, ''));
    const ringOrder = TG.STATIONS.map((s) => s.label.toLowerCase());
    check('V2 the headline opens with born', hlWords[0], 'born');
    check('V2 …and every word after it is a station label, in ring order',
        (() => { const ws = hlWords.slice(1); const idx = ws.map((w) => ringOrder.indexOf(w));
                 return ws.length >= 4 && idx.every((i) => i >= 0) && idx.every((i, k) => k === 0 || i > idx[k - 1]); })(), true);
    check('V2 …including grounded and guarded, the two gates', hlWords.includes('grounded') && hlWords.includes('guarded'), true);
    // The verdict lead reads from the same counts the groups are named from.
    const vpIdx = rsrc.indexOf('function _verdictProse()'); const vpEnd = rsrc.indexOf('\n    // ── the side panel', vpIdx);
    const acIdx = rsrc.indexOf('function _attentionCounts()'); const acEnd = rsrc.indexOf('\n    // ── the verdict (W2)', acIdx);
    const escP = (x) => String(x == null ? '' : x).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
    const mkProse = (model) => new Function('esc', '_model', `${rsrc.slice(acIdx, acEnd)}\n${rsrc.slice(vpIdx, vpEnd)}\nreturn _verdictProse();`)(escP, model);
    const pHot = mkProse({ stations: [{ key: 'scored', state: 'danger' }, { key: 'bounded', state: 'warn' }],
                           verdict: { lines: [{ k: 'needs you', text: '2 of 4 turns graded clean' }, { k: 'record', text: '9 receipts sealed' }] } });
    check('V2 a danger station leads the sentence in red, counting the amber findings the sentence lists too',
        /tc-v-danger">1 station needs you, 1 worth a look:<\/span> 2 of 4 turns graded clean\. 9 receipts sealed\./.test(pHot), true);
    check('V2 danger alone reads without an amber count', /1 station needs you:<\/span>/.test(mkProse({ stations: [{ key: 'scored', state: 'danger' }], verdict: { lines: [] } })), true);
    const pWatch = mkProse({ stations: [{ key: 'grounded', state: 'watching' }, { key: 'signed', state: 'pass' }],
                             verdict: { lines: [{ k: 'posture', text: 'watches drift, stops neither' }] }, closures: { count: 3, last: 'a parked write approved' } });
    check('V2 Rule 3.7 — a watching ring is "nothing needs you", never "all quiet"',
        /tc-v-pass">Nothing needs you right now\.<\/span>/.test(pWatch) && !/All stations quiet/.test(pWatch), true);
    check('V2 the loop-closed count is a sentence of the verdict', /The loop has closed 3× — last, a parked write approved\./.test(pWatch), true);
    check('V2 two warn stations read plural', /2 stations worth a look:/.test(mkProse({ stations: [{ key: 'a', state: 'warn' }, { key: 'b', state: 'warn' }], verdict: { lines: [] } })), true);
    check('W2 the roster tooltip is the verdict text', /data-tip="\$\{esc\(verdict \|\|/.test(hsrc), true);
    done();
}

// ── W3 / W6 · the lane cut in the layout; one explanation layer ───────────────
// TRUST_CIRCLE_LEGIBILITY.md W3 + W6. A guarantee station shows its lane in the
// eyebrow and ONE graded line for its checks (which opens JUDGED); the chip grid lives
// at JUDGED. The explainer keeps only rows with a live value; the method is in the
// Guide, and the explainer says so when nothing live is left.
section('W3/W6 — the lane in the eyebrow, the graded line, live-only explainer rows');
{
    const done = report('W3/W6 — lane cut in the layout');
    const hsrc = fs.readFileSync(SRC, 'utf8');
    const rsrc = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'handlers', 'trustCircle.js'), 'utf8');
    const spIdx = rsrc.indexOf('function derivationPanel(d)');
    const spEnd = rsrc.indexOf('\n    // ── rendering states onto the ring', spIdx);
    const escFn = (x) => String(x == null ? '' : x).replace(/[&<>"']/g, (c) =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const mkPanel = (derivOpen, win) => new Function('esc', '_derivOpen', 'window',
        `${rsrc.slice(spIdx, spEnd)}\nreturn stationPanel;`)(escFn, derivOpen, win || guideStub());
    // The registry carries the lane, and every station has one.
    for (const r of TG.STATIONS) check(`W3 ${r.key} has a lane`, !!(r.lane && r.lane.length), true);
    check('W3 in-flight stations say they can change the answer',
        ['grounded', 'guarded', 'bounded'].every((k) => /in flight · can change the answer/.test(TG.stationRow(k).lane)), true);
    check('W3 JUDGED says it grades only', /after the turn · grades only/.test(TG.stationRow('scored').lane), true);
    // The eyebrow renders the lane in both forms.
    const stL = { key: 'grounded', label: 'Grounded', sub: 's', q: 'Q?', lane: 'in flight · can change the answer' };
    const dL = { state: 'watching', facts: [], headline: 'H.',
                 derivation: { window: '4 scored turns in this window', config: '', exec: '',
                               graded: { clean: 3, of: 4, total: 6, disputed: 1 }, dims: [], note: 'n' } };
    const peek = mkPanel(null)(stL, dL, false), pinned = mkPanel('grounded')(stL, dL, true);
    check('W3 the peek eyebrow carries the lane', /class="tc-lane">in flight · can change the answer</.test(peek), true);
    check('W3 the pinned eyebrow carries the lane', /class="tc-lane">in flight · can change the answer</.test(pinned), true);
    // The graded row: figure + denominator + the route to JUDGED; the method row when
    // nothing live is left.
    check('W3 the graded row names clean of measured', /Graded at JUDGED[\s\S]{0,80}3 of 4 checks measured clean/.test(pinned), true);
    check('W3 …and the checks this class never produces', /2 not produced by this class/.test(pinned), true);
    check('W3 …and the disputed count', /1 disputed/.test(pinned), true);
    check('W3 …and routes to JUDGED', /data-tc-act="pin:scored"/.test(pinned), true);
    check('W6 the method row appears when no live row is left', /Method[\s\S]{0,140}Assurance Guide/.test(pinned), true);
    check('W3 the shared JUDGED note is one sentence in source',
        (() => { const i = hsrc.indexOf('const _JUDGED_NOTE ='); const t = hsrc.slice(i, hsrc.indexOf(';\n', i));
                 return (t.match(/\. /g) || []).length === 0 && t.length < 400; })(), true);
    const dLive = { ...dL, derivation: { ...dL.derivation, exec: 'Execution is this station\'s OWN acts: 1 withheld.' } };
    check('W6 …and not when a live row is present', /tc-dk">Method</.test(mkPanel('grounded')(stL, dLive, true)), false);
    const dNone = { ...dL, derivation: { ...dL.derivation, graded: { clean: 0, of: 0, total: 6, disputed: 0 } } };
    check('W3 no measured check reads as absence, never as clean (3.7)',
        /no check measured in this window/.test(mkPanel('grounded')(stL, dNone, true)), true);
    // The host routes `pin:` to the ring.
    check('W3 the host routes pin: to TrustCircle.pin', /action\.indexOf\('pin:'\) === 0[\s\S]{0,200}window\.TrustCircle\.pin\(stKey\)/.test(hsrc), true);
    // Live-only rule, executed on the real stations: a station with nothing live has
    // both rows empty, and the JUDGED grid still carries every label slice.
    const cell = { turns: 8, failures: 4, dimensions: { grounded_overlap: 0.5, safety: 0.5, provenance_integrity: 1, must_not_call: 1, completed: 1 },
                   dimension_counts: { grounded_overlap: 8, safety: 8, provenance_integrity: 8, must_not_call: 8, completed: 8 },
                   turn_failed_dims: [['grounded_overlap'], ['safety'], [], [], [], [], [], []], spans_scored: 8 };
    const st = stations([P('A', 'enforce', 'enforce')], [T('A', 'trusted')],
        { agent: 'A', dashboard: { enabled: true, by_agent: { A: cell } }, byAgent: { A: cell } });
    const judgedKeys = new Set((st.scored.derivation.dims || []).map((x) => x.key));
    for (const k of ['grounded', 'guarded', 'bounded', 'proven']) {
        const d = st[k].derivation;
        check(`W3 ${k} carries a graded line and no chips`, !!d.graded && (d.dims || []).length === 0, true);
        check(`W3 ${k} graded line counts a label slice JUDGED also lists`, d.graded.total > 0, true);
    }
    check('W3 JUDGED lists every guarantee-labelled check',
        ['grounded_overlap', 'safety', 'provenance_integrity', 'must_not_call'].every((k) => judgedKeys.has(k)), true);
    check('W6 SIGNED keeps no doctrine rows', !st.signed.derivation.config && !st.signed.derivation.exec, true);
    check('W6 GROUNDED keeps its live execution row when the gate acted',
        (() => { const c2 = { ...cell, gate: { acted: 1, blocked: 1, cleared: 0 } };
                 const s2 = stations([P('A', 'enforce', 'enforce')], [T('A', 'trusted')],
                     { agent: 'A', dashboard: { enabled: true, by_agent: { A: c2 } }, byAgent: { A: c2 } });
                 return /withheld/.test(s2.grounded.derivation.exec || ''); })(), true);
    done();
}

// ── W7 · the contract's core names a live gate section for every rule ─────────
// TRUST_CIRCLE_LEGIBILITY.md W7: the contract is ten rules, each naming the section here
// that enforces it; the history lives in the casebook. A rule whose named section does
// not exist is a rule stated but unmeasured — the contract's own §0 lesson, structural.
section('W7 — every core rule names a gate section that exists');
{
    const done = report('W7 — the core is gated');
    const self = fs.readFileSync(fileURLToPath(import.meta.url), 'utf8');
    const titles = new Set([...self.matchAll(/section\('((?:[^'\\]|\\.)*)'\)/g)].map((m) => m[1].replace(/\\'/g, "'")));
    const core = fs.readFileSync(path.join(HERE, '..', 'docs', 'Architecture', 'TRUST_STATUS_COMMUNICATION.md'), 'utf8');
    const rules = [...core.matchAll(/^\*\*(\d+) · [^\n]*\n([\s\S]*?)\*Gate:\* ([^\n]*)/gm)];
    check('W7 the core states exactly ten rules', rules.length, 10);
    for (const r of rules) {
        const named = [...r[3].matchAll(/`([^`]+)`/g)].map((m) => m[1]);
        check(`W7 rule ${r[1]} names at least one gate section`, named.length > 0, true);
        for (const n of named) check(`W7 rule ${r[1]}: section "${n.slice(0, 48)}" exists in this gate`, titles.has(n), true);
    }
    check('W7 the casebook exists and says it is not the contract',
        /This is the casebook, not the contract/.test(fs.readFileSync(path.join(HERE, '..', 'docs', 'Architecture', 'TRUST_STATUS_COMMUNICATION_CASEBOOK.md'), 'utf8')), true);
    check('W7 the core is one page, not a changelog', core.split('\n').length < 200, true);
    done();
}

// ── class scope — the Guide marks what does not apply to the opened class ───────
// Follow-up to W5: GROUNDED read six mechanisms on the dial beside nine in the Guide.
// The dial now lists out-of-class mechanisms flagged (test_assurance_dial); the Guide,
// opened for an agent, marks the same chips the same way and counts them apart.
section('class scope — Guide chips mark what is not part of the opened class');
{
    const done = report('class scope — the Guide side');
    const qsrc = fs.readFileSync(QD_SRC, 'utf8');
    const dsrc = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'handlers', 'assuranceDial.js'), 'utf8');
    check('the Guide has ONE applicability predicate against the opened class', /function appliesToGuideCls\(k\)/.test(qsrc), true);
    check('…deciding by EXACT membership of the served applies_to, like the registry and the scorers',
        /return raw\.includes\(_guideCls\);/.test(qsrc) && !/_guideCls === 'conversation_with_tools' \? 'llm_only'/.test(qsrc), true);
    check('a chip outside the class carries the mark', /qd-rel-offclass/.test(qsrc), true);
    check('…and says so in words', /·not this class/.test(qsrc), true);
    check('the row lead counts what applies', /of \$\{keys\.length\} for this class/.test(qsrc), true);
    check('the dial hands the Guide the class on every row', /data-dim-context-cls="\$\{esc\(_profileClass\)\}"/.test(dsrc), true);
    const css = fs.readFileSync(path.join(HERE, '..', 'static', 'css', 'trust.css'), 'utf8');
    check('the mark is drawn dashed and dimmed, never in an attention colour',
        /\.qd-rel-chip\.qd-rel-offclass \{ opacity: \.55; border-style: dashed; \}/.test(css), true);
    done();
}

// ── one station, one look — the answer's dial and the Trust Circle draw a station the
// same way: the same icon (one registry) and the same colour token per state. The operator
// read the two surfaces as two different instruments; this section is what keeps them one.
section('one station, one look — icons and state colours agree between the dial and the ring');
{
    const done = report('dial ≡ ring: icons and colours');
    const gsrc = fs.readFileSync(TG_SRC, 'utf8');
    const rsrc = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'handlers', 'trustCircle.js'), 'utf8');
    const dsrc = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'handlers', 'assuranceDial.js'), 'utf8');
    const icons = (src, head) => { const i = src.indexOf(head); const j = src.indexOf('\n    };', i); return Object.fromEntries([...src.slice(i, j).matchAll(/^\s*(\w+):\s*'([^']*)'/gm)].map((m) => [m[1], m[2]])); };
    const G = icons(gsrc, 'const STATION_ICONS = {'), R = icons(rsrc, '        || {'), D = icons(dsrc, '        || {');
    check('the grammar carries an icon per station', Object.keys(G).sort().join(','), STATION_KEYS.slice().sort().join(','));
    check('the ring reads the registry first', /const ICONS = \(globalThis\.TrustGrammar && globalThis\.TrustGrammar\.STATION_ICONS\)/.test(rsrc), true);
    check('the dial reads the registry first', /\.STATION_ICONS\n\s*\|\| \{/.test(dsrc), true);
    check('the ring fallback equals the registry', JSON.stringify(R), JSON.stringify(G));
    check('the dial fallback equals the registry', JSON.stringify(D), JSON.stringify(G));
    check('the loaded module serves the same icons', JSON.stringify(TG.STATION_ICONS), JSON.stringify(G));
    // One colour token per state on both instruments: the dial's row dot and segment, the
    // ring's band. `na` is dashed on both; `watching` is a ring-altitude state (a turn has
    // no posture) and is never drawn by the dial.
    const CSS = fs.readFileSync(path.join(HERE, '..', 'static', 'css', 'trust.css'), 'utf8');
    const tok = (re) => ((CSS.match(re) || [, ''])[1].match(/--trust-[a-z]+/) || [''])[0];
    for (const st of ['pass', 'warn', 'danger']) {
        const ring = tok(new RegExp(`data-tc-state="${st}"\\]\\s*\\.tc-bandseg\\s*\\{([^}]*)\\}`));
        const dot = tok(new RegExp(`\\.ad-gate-${st}\\s+\\.ad-gate-dot\\s*\\{([^}]*)\\}`));
        const seg = tok(new RegExp(`\\.ad-seg-${st}\\s*\\{([^}]*)\\}`));
        check(`${st}: the ring band, the dial dot and the dial segment share one token (${ring})`, ring && ring === dot && ring === seg, true);
    }
    check('na: dashed on the ring and on the dial', /data-tc-state="na"[^{]*\.tc-bandseg[^{]*\{[^}]*dasharray/.test(CSS) && /\.ad-seg-na\s*\{[^}]*dasharray/.test(CSS), true);
    check('the dial never draws a posture (no watching rule on its surfaces)', /\.ad-(gate|seg|chip)-watching/.test(CSS), false);
    done();
}

// ── the suite cannot silently shrink ─────────────────────────────────────────
// A whole section (the Rule 3.0c correction marks, 9 assertions) was once removed by an
// over-wide block replacement, and the suite still reported ALL PASS — a gate that can
// lose coverage without failing is not a gate. Raise the floor whenever it grows.
{
    const done = report('coverage floor');
    check('the suite has not shrunk', pass >= 2040, true);
    done();
}

// ── summary ──────────────────────────────────────────────────────────────────
console.log('');
if (failures.length) {
    console.log(`FAILED — ${pass} passed, ${failures.length} failed\n`);
    failures.slice(0, 80).forEach((f) => console.log(`  ✗ ${f}`));
    if (failures.length > 80) console.log(`  … and ${failures.length - 80} more`);
    console.log('');
    process.exit(1);
}
console.log(`ALL PASS — ${pass} assertions across the Trust Circle communication contract.`);
console.log('Contract: docs/Architecture/TRUST_STATUS_COMMUNICATION.md\n');
