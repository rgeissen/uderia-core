#!/usr/bin/env node
/**
 * R-4 route-parity gate — written BEFORE the Anatomy restyle, same inversion as R-3:
 * the surface already ships and already routes; the restyle's danger is LOSING a route,
 * not inventing a reading. Ledger rows: I35 (19 slots — the first hand ledger counted 6),
 * I36 (foundations worst-of — a meaning loss that SHIPPED), I38–I41, R13–R16, R19.
 *
 * R13 (faculty → section-precise editor jump) is flagged §6 highest-risk; I41/R19 (the
 * picker search) exist only because extraction found them — the first ledger missed both.
 *
 * House rule: execute what is executable (the module loads under a stub window;
 * `_postureToken` is extracted and RUN), source-anchor the DOM-bound routes.
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const AN = path.join(HERE, '..', 'static', 'js', 'handlers', 'agentAnatomy.js');

let pass = 0; const failures = [];
function check(name, got, want) {
    if (JSON.stringify(got) === JSON.stringify(want)) { pass++; return; }
    failures.push(`${name}\n      got=${JSON.stringify(got)} want=${JSON.stringify(want)}`);
}
function section(t) { console.log(`\n── ${t} ${'─'.repeat(Math.max(0, 64 - t.length))}`); }

const src = fs.readFileSync(AN, 'utf8');
const win = {};
new Function('window', 'document', src)(win, {
    addEventListener: () => {}, getElementById: () => null,
    querySelector: () => null, querySelectorAll: () => [],
    readyState: 'loading',
});
const AA = win.AgentAnatomy;

// ── 1 · public API ───────────────────────────────────────────────────────────
section('public API — every consumer entry point');
for (const k of ['mount', 'update', 'init', 'open', 'miniInto', 'refreshKpis', 'openAgentPicker']) {
    check(`AgentAnatomy.${k} exists`, typeof AA[k], 'function');
}

// ── 2 · the navigation surface — every kind survives the restyle ─────────────
section('routes — every _navigate kind (R13–R16 + the cross-link triangle)');
for (const kind of ["'tab'", "'view'", "'agents'", "'circle'", "'child'", "'link'", "'seal'", "'editor'"]) {
    check(`kind === ${kind} is dispatched`,
        new RegExp(`kind === ${kind}`).test(src), true);
}
// R13 — highest risk (§6): the faculty jump lands on the exact editor SECTION.
check('R13: the editor jump is section-precise',
    /openProfileForEdit\(_selected, target\)/.test(src), true);
check('…with the fallback to the card\'s own edit button',
    /data-action="edit-profile"/.test(src), true);
// R14/R16 — foundations → posture matrix · constellation pivot + breadcrumb.
check('R14: foundations route to the Trust Posture',
    /nav: \{ label: 'Open the Trust Posture', action: 'agents:posture' \}/.test(src), true);
check('R16: the constellation pivot records the breadcrumb',
    /_pivotFrom = \{ id: from\.id, tag: from\.tag \}/.test(src), true);
check('…and back returns through it', /target === 'back' && _pivotFrom/.test(src), true);
// The agent travels with every jump (context-aware cross-links).
check('Awareness jump pre-filters to the agent',
    /executionDashboard\.focusAgent/.test(src), true);
check('Ontology jump deep-links the agent, with retry',
    /window\.ontologyFocusAgent/.test(src) && /for \(let i = 0; i < 25/.test(src), true);
check('the seal opens the Trust Membrane detail',
    /TrustBadge\.openDetail\(p3\.id, p3\.tag\)/.test(src), true);
check('…with the honest fallback when the suite is absent',
    /_navigate\('agents:posture'\);\s*\/\/ honest fallback/.test(src), true);

// ── 3 · the picker (I41/R19 — extraction-found; the first ledger missed it) ──
section('agent picker — search, pills, keyboard');
check('the picker has a search input', /class="an-pick-search"/.test(src), true);
check('…wired to filter on input', /input\.addEventListener\('input'/.test(src), true);
check('…auto-focused', /an-pick-search/.test(src) && /input\.focus\(\)/.test(src), true);
check('pick-agent opens it from the star', /action === 'pick-agent'/.test(src)
    && /_openStarPicker\(\)/.test(src), true);

// ── 4 · I36 — foundations aggregate worst-of, executed not grepped ───────────
section('foundations — worst-of (the shipped green at 1-of-9 was the meaning loss)');
{
    const m = src.match(/function _postureToken\(c\) \{[\s\S]*?\n    \}/);
    check('_postureToken extracted', !!m, true);
    const tok = new Function(`${m[0]}; return _postureToken;`)();
    check('all enforcing → pass', tok({ enforce: 9, observe: 0, off: 0 }), 'pass');
    // THE ledger row: 1 enforcing of 9 must never read PASS — the strongest member never
    // hides the other eight's exposure (Rule 5.1, the Circle's own policy). Since W1
    // (TRUST_CIRCLE_LEGIBILITY.md) the mixed estate reads `watching` — a posture, not
    // an alarm: the value carries the count, the colour stays out of amber.
    check('1 of 9 enforcing → watching, never pass', tok({ enforce: 1, observe: 8, off: 0 }), 'watching');
    check('1 of 9 enforcing → …and never amber', tok({ enforce: 1, observe: 8, off: 0 }) !== 'warn', true);
    check('enforcers with the rest OFF → still watching (mixed is a posture)',
        tok({ enforce: 3, observe: 0, off: 6 }), 'watching');
    check('all observing → watching', tok({ enforce: 0, observe: 9, off: 0 }), 'watching');
    check('all off → na (dashed, honest absence)', tok({ enforce: 0, observe: 0, off: 9 }), 'na');
    check('empty estate → na', tok({ enforce: 0, observe: 0, off: 0 }), 'na');
}

// ── 5 · honest absence + the mini ────────────────────────────────────────────
section('robustness — absence-tolerant seams');
check('miniInto is fail-silent (catch, no throw to the host)',
    /async function miniInto[\s\S]{0,900}catch/.test(src), true);
check('the seal renders nothing without a seal', /if \(!seal\) return '';/.test(src), true);

// ── 6 · R-4.4 — the ring's colour rule reaches the map ───────────────────────
section('restyle — blueprint map, schematic subject, colour = attention');
const css = fs.readFileSync(path.join(HERE, '..', 'static', 'css', 'trust.css'), 'utf8');
// The arm label is a PAIR: the description carries primary weight, the topic
// beneath it wears blueprint. Neither may take a category/trust colour (I37).
check('the arm description line is primary',
    /\.an-metaphor \{[\s\S]{0,300}?fill: var\(--text-primary\)/.test(css), true);
check('…and the topic beneath it is blueprint, not sage (I37)',
    /\.an-concept \{[^}]*fill: var\(--an-map\)/.test(css), true);
check('…and the orange birth-mark is gone',
    /an-metaphor\.an-accent/.test(css) || /an-accent'/.test(src), false);
check('the subject is schematic: faint fill + hairline stroke',
    /class="an-star-i"/.test(src) && /class="an-star an-star-f"/.test(src), true);
check('…the steel gradient is retired from BOTH stars (main + mini)',
    /anStar/.test(src.replace(/anStarMini|an-star/g, '')), false);
check('…and the mini joined the schematic language',
    /class="an-star-i"[\s\S]{0,400}class="an-star-f"/.test(
        src.slice(src.indexOf('function _miniSvg'))), true);
check('the plus rides the centre picker', /an-subject-plus/.test(src), true);
check('…only when picking is possible', /const plus = model\.pickable/.test(src), true);
check('…and lights accent on hover (actionable channel)',
    /\.an-center:hover \.an-subject-plus[^{]*\{ stroke: var\(--teradata-orange\)/.test(css), true);
check('the legend explains colour MEANING, not category',
    /blueprint<\/b> — the map layer/.test(src) && /reserved for the two foundations/.test(src), true);
check('…and the category legend is gone',
    /how to think|where Agents are born/.test(src), false);
check('the starfield is deterministic literals, not runtime random',
    /const STARFIELD = \[\[/.test(src) && !/Math\.random/.test(src), true);
// The field must stay clear of every plate — re-run the numeric filter the literals
// were generated with; a future edit that moves a plate must re-justify the field.
{
    const FIELD = JSON.parse((src.match(/const STARFIELD = (\[\[[\s\S]*?\]\]);/) || [])[1]);
    const RECTS = [[375, 102, 625, 186], [46, 244, 306, 326], [670, 244, 930, 326],
                   [106, 516, 356, 598], [644, 516, 894, 598], [250, 584, 470, 676],
                   [530, 584, 750, 676], [60, 40, 300, 92], [690, 42, 950, 78],
                   [430, 468, 570, 496]];
    // The constellation slots are read from the SOURCE, never a hand copy: a copy
    // let slot #2 sit inside the Configuration plate for months while this gate
    // stayed green (it only checked the FIELD against the constellation).
    const constSrc = (() => { const t = src.slice(src.indexOf('function _constellation('));
        return t.slice(0, t.indexOf('\n    function ', 1)); })();
    const CONST = JSON.parse((constSrc.match(/const pts = (\[\[[\s\S]*?\]\]);/) || [])[1]).map((p) => p.slice(0, 2));
    check('nine constellation slots parsed from the source', CONST.length, 9);
    // Each slot's FOOTPRINT — the 1.35× star (r≈17) plus the @tag text beneath it
    // (10.5px mono, up to 9 chars → x±32, baseline y+26 → box y+16..y+30) — must
    // clear every plate and stay inside the membrane's inner margin.
    {
        const foot = ([x, y]) => [x - 32, y - 17, x + 32, y + 30];
        const buried = CONST.map((p, i) => {
            const [a, b, c, d] = foot(p);
            const plate = RECTS.findIndex(([ra, rb, rc, rd]) => c > ra && a < rc && d > rb && b < rd);
            const edge = a < 62 || c > 938 || b < 42 || d > 658;
            return plate >= 0 ? `#${i + 1} under plate ${plate}` : (edge ? `#${i + 1} outside the frame` : null);
        }).filter(Boolean);
        check('every constellation slot (star + its tag) clears every label plate and the frame',
            buried.join(', '), '');
        // The polylines that thread the experts are drawn from the same slots.
        const polys = [...constSrc.matchAll(/<polyline points="([^"]+)"/g)].map((m) => m[1]);
        const onSlot = polys.every((pl) => pl.split(' ').every((pt) => CONST.some(([x, y]) => `${x},${y}` === pt)));
        check('…and every constellation line runs slot to slot', onSlot, true);
    }
    let bad = 0;
    for (const [x, y, r] of FIELD) {
        const m = r * 11 + 4;
        if (x - m < 62 || x + m > 938 || y - m < 42 || y + m > 658) bad++;
        else if (RECTS.some(([a, b, c, d]) => x + m > a && x - m < c && y + m > b && y - m < d)) bad++;
        else if (Math.hypot(x - 500, y - 335) < 175 + m) bad++;
        else if (CONST.some(([cx, cy]) => Math.hypot(x - cx, y - cy) < 26 + m)) bad++;
    }
    check('every field star clears every plate, the subject and the constellation', bad, 0);
    check('…and the field is dense enough to read as a universe', FIELD.length >= 25, true);
}


// ── 7 · R-4.5 — agent mode, reviewed explicitly (the §6 least-understood surface) ──
section('agent mode — the guardrail lanes fold worst-of, one rule for both scopes');
{
    const m = src.match(/function _lanesToken\(inMode, outMode\) \{[\s\S]*?\n    \}/);
    check('_lanesToken extracted', !!m, true);
    const lanes = new Function(`${m[0]}; return _lanesToken;`)();
    check('both lanes acting → acting', lanes('enforce', 'review'), 'acting');
    // THE defect: outbound enforce + inbound OFF read green while its own value line
    // said `in off · out enforce` — the colour contradicting its own text.
    check('an acting lane beside an OFF one → partial, never acting',
        lanes('off', 'enforce'), 'partial');
    check('…in either order', lanes('enforce', 'off'), 'partial');
    check('acting beside observing → partial', lanes('observe', 'enforce'), 'partial');
    check('both off → off', lanes('off', 'off'), 'off');
    check('both observing → partial', lanes('observe', 'observe'), 'partial');
    check('one rule, two consumers (agent mode + estate stats), no third copy',
        (src.match(/_lanesToken\(/g) || []).length, 3);
    check('the .some() lane classifier is gone',
        /\[outMode, inMode\]\.some/.test(src), false);
}


// ── 8 · glyphs — texture without a second paint (operator: clean, industrial) ─
section('glyphs — monochrome stroke icons in the blueprint channel');
check('every faculty has an icon', ['memory', 'configuration', 'session', 'components',
    'intelligence'].every((k) => new RegExp(`${k}:\\s+'<`).test(src)), true);
check('…and both foundations', /grounding:\s+'</.test(src) && /guardrails:\s+'</.test(src), true);
// One concept, one icon: the foundations reuse the RING'S grounded/guarded verbatim.
{
    const tc = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'handlers',
        'trustCircle.js'), 'utf8');
    const pick = (source, key) => (source.match(new RegExp(`${key}:\\s+'([^']+)'`)) || [])[1];
    check('grounding icon ≡ the ring\'s grounded icon',
        pick(src, 'grounding'), pick(tc, 'grounded'));
    check('guardrails icon ≡ the ring\'s guarded icon',
        pick(src, 'guardrails'), pick(tc, 'guarded'));
}
check('icons are monochrome stroke (currentColor, industrial)',
    /stroke="currentColor" stroke-width="1.9"/.test(src), true);
check('…never an emoji', /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(
    (src.match(/const FAC_ICONS = \{[\s\S]*?\};/) || [''])[0]), false);
check('the faculty glyph is ACCENT — a door, not a reading (operator call)',
    /\.an-fac-icon \{ color: var\(--teradata-orange/.test(css), true);
check('…SOLID at rest (no alpha on glyphs), strengthened by WEIGHT under the hand',
    !/\.an-fac-icon \{[^}]*opacity/.test(css)
    && /:hover \.an-fac-icon[^{]*\{[^}]*stroke-width: 2\.3/.test(css), true);
check('…state lives in hue and dash, never alpha',
    !/\.an-fac-(quiet|off) \.an-fac-(box|icon) \{[^}]*opacity/.test(css), true);
check('…but an OFF faculty is never dressed as a door',
    /\.an-fac-off \.an-fac-icon \{ color: var\(--text-muted\)/.test(css), true);
check('faculty state stays in the blueprint channel (weight + dash)',
    /\.an-fac-off \.an-fac-box \{[^}]*stroke-dasharray/.test(css)
    && !/\.an-fac-(on|quiet|off)[^{]*\{[^}]*var\(--trust-(pass|warn|danger)\)/.test(css), true);
check('foundation icons take the SAME posture token as their box',
    /an-ftok-\$\{st\}/.test(src) && /\.an-ftok-warn \{ color: var\(--trust-warn\)/.test(css), true);
check('na-class still renders NO marker (structural absence)',
    /f\.state !== 'na-class'/.test(src), true);
check('the bare dot is gone', /an-dot/.test(src) || /\.an-dot\b/.test(css), false);

section('the agent class is SHOWN as the platform\'s IFOC pill — never spelt as its internal type (@PXPROD read "Ideate (llm_only)")');
const cfgFact = (src.split('key: \'configuration\', metaphor:').pop() || '').split('{ k: \'Brain\'')[0];   // the per-agent model's Class fact (the last Configuration faculty built)
check('the Class fact is the badge, flagged as markup', /k: 'Class', html: true,\s*\n?\s*v: _ifocBadge\(p\.profile_type\)/.test(cfgFact), true);
check('…and no fact anywhere prints the raw profile_type beside the label', /\(\$\{p\.profile_type\}\)/.test(src), false);
check('an Ideate agent that senses through tools says so beside the pill', /isIdeate && p\.useMcpTools \? ' <span class="an-copy">with tools<\/span>'/.test(cfgFact), true);
check('the fact renderer honours the markup flag and escapes everything else', /\$\{x\.html \? x\.v : esc\(x\.v\)\}/.test(src), true);
// the badge itself: executed — label, colour, tint and border per class; unknown class degrades, never throws
const badgeFn = new Function('esc', 'cls', src.slice(src.indexOf('function _ifocBadge(type)'), src.indexOf('\n    }\n', src.indexOf('function _ifocBadge(type)')) + 7) + '\nreturn _ifocBadge;')(
    (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
    (t) => ({ llm_only: { label: 'Ideate', color: '#4ade80' }, genie: { label: 'Coordinate', color: '#F15F22' } }[t] || { label: t || 'Agent', color: '#94a3b8' }));
const b = badgeFn('llm_only');
check('badge: Ideate in its colour on a 15% tint with a 1px border, uppercase pill', [/>Ideate</.test(b), /color:#4ade80/.test(b), /rgba\(74, 222, 128, 0\.15\)/.test(b), /border:1px solid #4ade80/.test(b), /rounded-full/.test(b), /uppercase/.test(b), /llm_only/.test(b)], [true, true, true, true, true, true, false]);
check('badge: Coordinate carries the brand orange', /color:#F15F22/.test(badgeFn('genie')), true);
check('badge: an unknown class degrades to a grey pill with the class word, never throws', /color:#94a3b8/.test(badgeFn('mystery')) && />mystery</.test(badgeFn('mystery')), true);


section('faculty reads — ONE rule per faculty, resolved as the runtime resolves it (the Intelligence tile read 1 forever)');
check('the estate star and the KPI tiles read the same _facultyStats', (src.match(/= _facultyStats\(profiles\)/g) || []).length, 2);
check('no surface stringifies a collection entry any more', /repos\.add\(String\(c\)\)/.test(src), false);
check('Faculty Adoption reads the resolved components, repositories and skills',
    /memory: \(p\) => _enabledComponents\(p\)\.includes\('memory'\)/.test(src)
    && /knowledge: \(p\) => _knowledgeRepoIds\(p\)\.length > 0/.test(src)
    && /skills: \(p\) => _appliedSkills\(p\)\.length > 0/.test(src), true);
check('the per-agent skill count falls back to the applied set, not the raw config', /: _appliedSkills\(p\)\.length;/.test(src), true);
check('a tile sub-line clamps instead of truncating mid-word', /class="text-\[11px\] mt-1 an-kpi-sub"/.test(src) && !/mt-1 truncate/.test(src), true);
// executed: the four reads over fixture profiles
const facSrc = src.slice(src.indexOf('    function _enabledComponents(p)'), src.indexOf('\n    }\n', src.indexOf('    function _facultyStats(profiles)')) + 7);
const fac = new Function(facSrc + '\nreturn { _enabledComponents, _appliedSkills, _knowledgeRepoIds, _facultyStats };')();
const fx = [
    { componentsEnabled: ['memory', 'chart'], skillsApplied: ['chart-expert'], knowledgeConfig: { collections: [{ id: 13, reranking: false }, { id: 2 }] } },
    { componentsEnabled: ['chart'], skillsApplied: [], knowledgeConfig: { collections: [{ id: 13 }] } },
    { componentConfig: { memory: { enabled: true }, flow: { enabled: false } }, skillsConfig: { skills: [{ id: 'x', active: true }, { id: 'y', active: false }] }, knowledgeConfig: { collections: [7, '7'] } },
    {},
];
const st = fac._facultyStats(fx);
check('repositories: object entries count by id — two agents on 13 and one on 2 and 7 read 3 distinct', st.repos.size, 3);
check('…and a bare id and its string spelling are one repository', [...st.repos].sort().join(','), '13,2,7');
check('components: the server-resolved set wins; a payload without it falls back to the explicit config', fac._enabledComponents(fx[2]).join(','), 'memory');
check('memory: counted from the resolved set (two agents)', st.memAgents, 2);
check('instruments: distinct enabled components across the estate', [...st.instruments].sort().join(','), 'chart,memory');
check('…and the agents carrying any', st.instrumentedAgents, 3);
check('skills: applied set when resolved, explicit actives otherwise', [...st.skills].sort().join(','), 'chart-expert,x');
check('an empty profile reads as nothing, never throws', fac._enabledComponents(fx[3]).length + fac._appliedSkills(fx[3]).length + fac._knowledgeRepoIds(fx[3]).length, 0);

console.log('');
if (failures.length) {
    console.log(`FAILED — ${pass} passed, ${failures.length} failed\n`);
    failures.forEach((f) => console.log(`  ✗ ${f}`));
    process.exit(1);
}
console.log(`ALL PASS — ${pass} assertions across the R-4 route-parity gate.\n`);
