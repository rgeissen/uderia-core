/**
 * The Assurance Guide's ONE anatomy — the committed gate.
 *
 *   node test/test_guide_anatomy.mjs
 *
 * WHY THIS EXISTS. The Guide rendered seven layouts (one per entry kind), its lead words
 * arrived undefined, its three text segments were paragraphs, and the rows ran in the
 * order the registry's relations were added (the judge before the module that checks).
 * The anatomy fixes order, grouping, weight and definition WITHOUT dropping a fact. A
 * reorder is exactly the kind of change a later edit undoes by accident, so the order
 * is asserted here, by executing the shipped renderer on a synthetic vocabulary.
 *
 * HOW IT WORKS. qualityDimensions.js runs under a stub DOM with its internals exposed
 * (`rowHtml`, `facetBar`, `glossaryHtml`); synthetic entries are registered through the
 * same `registerEntries` the vocabulary endpoint feeds. No server, no network.
 *
 * Contract asserted here:
 *   1. a mechanism's rows run in the order the gate WORKS:
 *      upholds · how it decides · properties · fires when · (then/also/ignores) · escalates to ·
 *      you can change · fixed · why · ‖ graded by · threshold
 *   2. a guarantee opens with "how it reads": on a turn · on the ring · its checks · why,
 *      ahead of its members
 *   3. every relation row names the KIND and COUNT of what follows, derived from the row
 *   4. the structured facts render as rows (numbered triggers; then / also / ignores; a
 *      `none` row; setting → effect; fixed); a prose payload still renders, as one row
 *   5. the description is the note alone; the wire key is an id mark, present but off
 *      the reading line; the "can" line renders the fates
 *   6. the facet bar folds to its group names with ONE group open; the glossary defines
 *      every lead and the kinds
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
let src = fs.readFileSync(path.join(HERE, '..', 'static', 'js', 'handlers', 'qualityDimensions.js'), 'utf8');
src = src.replace('window.QualityDimensions = {',
    'window.__qd = { rowHtml, facetBar, glossaryHtml, setShared: (m) => { SHARED_MODULES = m; }, setCls: (c) => { _guideCls = c; } }; window.QualityDimensions = {');

class El {
    constructor(t) { this.tag = t; this.children = []; this.attrs = {}; this.style = {}; this.dataset = {}; this.innerHTML = ''; this.textContent = ''; this.id = '';
        this.classList = { add() {}, remove() {}, toggle() {}, contains() { return false; } }; }
    appendChild(c) { this.children.push(c); return c; }
    querySelector() { return null; } querySelectorAll() { return []; } addEventListener() {}
    setAttribute(k, v) { this.attrs[k] = v; } getAttribute(k) { return this.attrs[k]; }
    get offsetHeight() { return 0; } replaceWith() {}
}
const head = new El('head'), body = new El('body');
const document = { getElementById: () => null, createElement: (t) => new El(t), head, body,
                   querySelector: () => null, querySelectorAll: () => [], addEventListener() {}, readyState: 'complete' };
const window = { addEventListener() {}, removeEventListener() {} };
// eslint-disable-next-line no-new-func
new Function('window', 'document', 'localStorage', 'fetch', 'CSS', src)(
    window, document, { getItem: () => null }, () => Promise.reject(new Error('no net')), { escape: (s) => s });
const QD = window.QualityDimensions, I = window.__qd;

let pass = 0, fail = 0;
const check = (label, cond) => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}`); } };
const section = (t) => console.log(`\n── ${t}`);
const leads = (html) => [...html.matchAll(/qd-rel-lead[^>]*>([^<]*)</g)].map((m) => m[1].replace(/&#39;/g, "'"));
const kinds = (html) => [...html.matchAll(/qd-kindn[^>]*>([^<]*)</g)].map((m) => m[1]);
const before = (arr, a, b) => arr.indexOf(a) >= 0 && arr.indexOf(b) >= 0 && arr.indexOf(a) < arr.indexOf(b);

// ── a synthetic vocabulary, in the shape the endpoint serves ─────────────────
QD.registerStages({ order: ['compose', 'deliver'], labels: { compose: 'Compose', deliver: 'Deliver' },
                    descriptions: {}, dimensions: { grounded_overlap: 'compose', scope_adherence: 'compose', safety: 'deliver' } });
QD.registerEntries([
    { k: 'gate_x', label: 'Gate X', desc: 'Judges whether the answer stayed inside its scope.', long: 'Judges whether the answer stayed inside its scope.',
      cls: 'control', station: 'grounded', measured: 'composite', derivation: 'A trigger first. The judge only on flagged turns.',
      how: ['a trigger first', 'the judge only on flagged turns'],
      dims: ['grounded_overlap', 'scope_adherence', 'groundedness'], judges: ['groundedness'],
      fates: ['stopped', 'noted'],
      fires: { when: ['one unsupported number', 'a quoted run the sources do not carry'],
               then: 'the flagged pieces are searched for again', ignores: 'the answer’s own structure' },
      tunable: [['mode', 'what happens on a hit'], ['minNumericFidelity', 'shifts the numeric concern']],
      fixed: [['trigger sensitivity', 'not configurable']],
      why: 'The trigger is tuned for recall.',
      lanes: [{ lane: 'IN_FLIGHT', assessor: 'corpus_support', label: 'Corpus support' }],
      properties: ['property_answer_support', 'property_answer_scope', 'property_owned_only'],
      consults: ['property_answer_support', 'property_answer_scope'] },
    // Properties, as the endpoint serves them: their live lanes. `owned_only` is the gate's
    // but not consulted in flight (like `answer_scope` under Trusted scope); `answer_scope`
    // is consulted with nothing bound — the fold must say so, not drop the row.
    { k: 'property_answer_support', label: 'Answer support', cls: 'property', property: 'answer_support',
      lanes: [{ lane: 'IN_FLIGHT', assessor: 'corpus_support', label: 'Corpus support', mode: 'decides' },
              { lane: 'POST_HOC', assessor: 'corpus_support', label: 'Corpus support', mode: 'grades' }] },
    { k: 'property_answer_scope', label: 'Answer scope', cls: 'property', property: 'answer_scope',
      lanes: [{ lane: 'IN_FLIGHT', assessor: null, label: null, mode: null, note: 'nothing is bound to this lane' }] },
    { k: 'property_owned_only', label: 'Owned only', cls: 'property', property: 'owned_only',
      lanes: [{ lane: 'POST_HOC', assessor: 'late_mod', label: 'Late module', mode: 'grades' }] },
    { k: 'seal_x', label: 'Seal X', desc: 'Whether the turn was sealed.', long: 'Whether the turn was sealed.',
      cls: 'attestation', station: 'proven', measured: 'unmeasured', derivation: 'Read from the chain.',
      dims: [], judges: [], fates: [], fires: { none: 'it always runs' }, fixed: [['sealing', 'unconditional']] },
    { k: 'legacy_x', label: 'Legacy X', desc: 'An older payload.', long: 'An older payload.',
      cls: 'control', station: 'guarded', dims: [], judges: [], fates: ['changed'],
      fires: 'A prose trigger.', tunable: 'The mode.' },
    { k: 'bound_x', label: 'Bound X', desc: 'A limit.', long: 'A limit.', cls: 'boundary', station: 'grounded', dims: [], judges: [], fates: [] },
    { k: 'meas_x', label: 'Meas X', desc: 'A fact.', long: 'A fact.', cls: 'measure', station: 'grounded', dims: [], judges: [], fates: [] },
    // A window aggregate: no module (no `reads`), nothing grades it (no `dims`), but it
    // WEIGHTS named checks — a third relation, served as `weights`.
    { k: 'agg_x', label: 'Agg X', desc: 'A standing over recent turns.', long: 'A standing over recent turns.',
      cls: 'measure', station: 'trusted', how: ['a blend over recent turns', 'the trust-critical checks carry the configured weight'],
      dims: [], judges: [], fates: [], weights: ['scope_adherence', 'grounded_overlap'],
      fires: { when: ['a sustained at-risk standing'], then: 'it can notify' },
      tunable: [['breaker mode', 'off · notify · freeze']] },
    { k: 'station_grounded', label: 'GROUNDED', desc: 'That the answer stays inside what the agent has.', long: 'That the answer stays inside what the agent has.',
      cls: 'station', q: 'Does it stay on its corpus?', controls: ['gate_x', 'bound_x', 'meas_x'], dims: ['grounded_overlap', 'scope_adherence'],
      why: 'Its colour is the worst of its members.',
      reads: { answer: { where: 'Live Status dial', rows: [['reading', 'worst of its members'], ['scale', 'green \u00b7 amber \u00b7 red']] },
               agent: { where: 'Trust Circle \u00b7 recent turns', rows: [['configuration \u00b7 could it act?', 'enforce \u2192 green'], ['execution \u00b7 what did it do?', 'as text \u00b7 never a colour'], ['several agents', 'the weakest decides'], ['open finding', 'amber or red until decided']] },
               checks: '2 \u00b7 colour JUDGED, not this station' } },
]);
const BASE_SHARED = { corpus_support: ['gate_x', 'grounded_overlap', 'property_answer_support'], late_mod: ['gate_x', 'scope_adherence', 'property_owned_only'] };
I.setShared(BASE_SHARED);

// ── 1 · a mechanism's rows run in the order the gate works ───────────────────
section('1 · rows in working order');
const gate = I.rowHtml('gate_x', null);
const L = leads(gate);
const ORDER = ['upholds', 'how it decides', 'properties', 'fires when', 'then', 'ignores', 'escalates to', 'you can change', 'fixed', 'why', 'graded by'];
check(`the gate's leads: ${L.join(' · ')}`, ORDER.every((x) => L.includes(x)));
for (let i = 1; i < ORDER.length; i++) check(`${ORDER[i - 1]} before ${ORDER[i]}`, before(L, ORDER[i - 1], ORDER[i]));
check('properties come before escalates to (what it assesses, before the judge it feeds)', before(L, 'properties', 'escalates to'));
// What the gate is ABOUT (its properties) is in the turn group; the CODE that checks them
// is its own fold, between that group and "after the turn" (it listed modules under
// "reads", skipping the property — the operator asked which is which, 2026-10-30).
{
    const inTurn = gate.slice(gate.indexOf('qd-rel-grp-h">in the turn<'), gate.indexOf('modules during the turn'));
    check('the properties row lists every property the gate assesses, each opening its own entry',
          ['property_answer_support', 'property_answer_scope', 'property_owned_only'].every((p) => inTurn.includes(`data-dim="${p}"`)));
    check('…and no module is named in the turn group', !/qd-mod\b|qd-mod /.test(inTurn) && !inTurn.includes('Corpus support'));
    const iF = gate.indexOf('modules during the turn'), iA = gate.indexOf('qd-rel-grp-h">after the turn<');
    check('the modules fold sits after the turn group and before "after the turn"', iF > gate.indexOf('qd-rel-grp-h">in the turn<') && iF < iA);
    check('…folded, like "after the turn", with its count', /<details class="qd-rel-grp qd-rel-fold"><summary class="qd-rel-grp-h">modules during the turn<span class="qd-fold-n"> · 2<\/span>/.test(gate));
    const fold = gate.slice(iF, gate.indexOf('</details>', iF));
    check('each fold row pairs a consulted property with its in-flight module (the M mark routes to the property)',
          /qd-pair-k u-tip" data-tip="can act on the answer">Answer support<\/span><button type="button" class="qd-mod qd-mod-route" data-dim="property_answer_support"[^>]*>Corpus support<span class="qd-mark"/.test(fold));
    check('a consulted property with nothing bound in flight says so — the row never vanishes',
          /qd-pair-k">Answer scope<\/span><em class="qd-mod-none">nothing is bound to this lane<\/em>/.test(fold));
    check('a property the gate owns but does not consult in flight is not in the fold', !fold.includes('Owned only') && !fold.includes('Late module'));
    const after = gate.slice(gate.indexOf('qd-rel-grp-h">after the turn<'));
    check('…its module is in "after the turn" instead, beside its property, above graded by (used after the turn, by the measure)',
          /qd-pair-k u-tip" data-tip="reports only">Owned only<\/span><button type="button" class="qd-mod qd-mod-route" data-dim="property_owned_only"\s+data-mod="late_mod"/.test(after)
          && after.indexOf('Owned only') < after.indexOf('>graded by<'));
    const prow = (gate.match(/>properties<[\s\S]*?<\/div>/) || [''])[0];
    check('the row reads PROPERTIES and the chips — no kind label repeating the lead (it read "PROPERTIES · PROPERTIES · 3")',
          /qd-rel-lead u-tip" data-tip="What this mechanism assesses[^"]*">properties</.test(gate) && !/qd-kindn/.test(prow));
    check('no S badge on a property chip (the gate\'s module IS its property\'s module — the badge would say nothing)', prow.includes('data-dim="property_answer_support"') && !/qd-shared|>S</.test(prow));
    check('no "reads" lead remains on a mechanism', !L.includes('reads'));
    const seal = I.rowHtml('seal_x', null);
    check('a mechanism without consulted properties has no fold', !seal.includes('modules during the turn'));
    // S and M are ONE link: every S in an entry must have an M for the same module in that
    // entry, or pointing at it lights nothing (the gate's M's had moved into a fold and the
    // property's S's had been removed, 2026-10-30).
    const sMods = (h) => [...h.matchAll(/class="qd-rel-same[^"]*"[^>]*data-mod="([^"]+)"/g)].map((m) => m[1]);
    const mMods = (h) => [...h.matchAll(/class="qd-mod[^"]*"\s+(?:data-dim="[^"]*"\s+)?data-mod="([^"]+)"/g)].map((m) => m[1]);
    for (const k of ['gate_x', 'grounded_overlap', 'property_answer_support']) {
        const h = I.rowHtml(k, null), ms = new Set(mMods(h)), ss = sMods(h);
        check(`${k}: every S has its M in the same entry (S ${ss.length}, M ${ms.size})`, ss.length > 0 && ss.every((m) => ms.has(m)));
    }
}
{
    // A PROPERTY keeps "after the turn" open (what grades it is half its entry), and its links
    // carry the S: its mechanism and its measures use its module, named by the M above.
    QD.registerEntries([{ k: 'property_answer_support', dims: ['grounded_overlap'] }]);
    const pe = I.rowHtml('property_answer_support', null);
    check('a property\'s "after the turn" is an open group, never a fold',
          pe.includes('qd-rel-grp-h">after the turn<') && !/qd-rel-fold"><summary class="qd-rel-grp-h">after the turn/.test(pe));
    check('…and its links to the gate and the measure carry the S (the other end of its M)',
          /data-dim="gate_x"[\s\S]*?>S<\/span>/.test(pe) && /data-dim="grounded_overlap"[\s\S]*?>S<\/span>/.test(pe));
    check('the gate still marks the measure that shares its module (there the badge separates shared from independent)',
          /data-dim="grounded_overlap"[\s\S]*?>S<\/span>/.test(gate));
}
check('how it decides renders as numbered steps, one line each', /how it decides<[\s\S]*<ol class="qd-steps"><li>a trigger first<\/li><li>the judge only on flagged turns<\/li><\/ol>/.test(gate));
check('the two time groups are present, in order',
      gate.indexOf('qd-rel-grp-h">in the turn<') > 0 && gate.indexOf('qd-rel-grp-h">in the turn<') < gate.indexOf('qd-rel-grp-h">after the turn<'));
check('the judge is excluded from graded by', (gate.match(/data-dim="groundedness"/g) || []).length === 1);
// A pass line belongs to a SCORED measure. A mechanism's settings are its own `you can
// change` / `fixed` rows; giving it the measure's "a yes/no check — nothing to configure"
// filed a row about nothing after the turn under "after the turn".
check('a mechanism carries no threshold row (its settings are its own rows)', !L.includes('threshold'));
{
    const m = I.rowHtml('grounded_overlap', null);
    check('a scored measure keeps its threshold, in its after-the-turn group',
          /qd-rel-grp-h">after the turn<[\s\S]*qd-rel-lead[^>]*>threshold</.test(m));
    const lone = I.rowHtml('legacy_x', null);
    check('a mechanism with nothing after the turn has no after-the-turn group',
          !/after the turn/.test(lone) && !/qd-rel-lead[^>]*>threshold</.test(lone));
    check('…and its lone group keeps the lead column (wrapped in a headerless group)',
          /<div class="qd-rel-grp"><div class="qd-row-rel/.test(lone) && !/qd-rel-grp-h/.test(lone));
}
// The after-the-turn group FOLDS on a mechanism or a guarantee (its measures live at
// JUDGED), one line with the counts; it stays open where it is the entry's whole content.
{
    // Opened for one agent, the row reads "graded by · N of M for this class"; the fold's
    // summary must still say "graded by" (it fell back to "7 linked", prod 2026-10-30).
    QD.registerEntries([{ k: 'gate_scoped', label: 'Gate S', cls: 'control', station: 'grounded', how: ['a trigger first'], dims: ['grounded_overlap', 'safety'], judges: [] },
                        { k: 'grounded_overlap', applies_to: ['rag_focused'] }, { k: 'safety', applies_to: ['tool_enabled'] }]);
    I.setShared(Object.assign({}, BASE_SHARED, { scoped_mod: ['gate_scoped', 'grounded_overlap', 'safety'] }));
    I.setCls('rag_focused');
    const scoped = I.rowHtml('gate_scoped', null);
    I.setCls(null);
    I.setShared(BASE_SHARED);
    check('opened for one agent, the summary still reads "graded by", with the class count — never "N linked"',
          /qd-fold-n"> · graded by 1 of 2<\/span>/.test(scoped) && !/linked</.test(scoped));
}
{
    // A gate's "graded by" names only the measures that USE ITS MODULE — read from the module
    // data, never the filing list. The filing list attaches every measure ABOUT a guarantee
    // to some gate on it; the Grounding gate looked responsible for measures it never
    // touches (operator, 2026-10-30). Display only: the filing list is not changed.
    QD.registerEntries([{ k: 'gate_filed', label: 'Gate F', cls: 'control', station: 'grounded', how: ['a check'],
                          dims: ['numeric_fidelity', 'citations'], judges: [] },
                        { k: 'gate_other', label: 'Gate O', cls: 'control', station: 'grounded', how: ['a check'],
                          dims: [], judges: [] }]);
    I.setShared(Object.assign({}, BASE_SHARED, { cit_mod: ['gate_filed', 'citations'], rel_mod: ['gate_other', 'retrieval_relevance'] }));
    const gf = I.rowHtml('gate_filed', null), go = I.rowHtml('gate_other', null);
    const gradedOf = (h) => [...((h.match(/>graded by[^<]*<[\s\S]*?<\/div>/) || [''])[0]).matchAll(/data-dim="([^"]+)"/g)].map((m) => m[1]);
    check('a measure FILED under a gate without using its module is not on the gate', JSON.stringify(gradedOf(gf)) === '["citations"]');
    check('…and the one that uses its module is, with its S', /data-dim="citations"[\s\S]*?>S<\/span>/.test(gf));
    check('a measure using a gate\'s module is on that gate even when filed elsewhere (no data moved)',
          JSON.stringify(gradedOf(go)) === '["retrieval_relevance"]');
    const nf = I.rowHtml('numeric_fidelity', null), cit = I.rowHtml('citations', null), rr = I.rowHtml('retrieval_relevance', null);
    check('the link is never one-way: the unrelated measure does not name the gate it was filed under',
          !nf.includes('data-dim="gate_filed"'));
    check('…the sharing measure names its gate back, both when filed there and when filed elsewhere',
          cit.includes('data-dim="gate_filed"') && rr.includes('data-dim="gate_other"'));
    // A gate whose filed measures use none of its code shows no "graded by" — and its tag
    // agrees ("Not scored"), never "One dimension" over an empty section.
    QD.registerEntries([{ k: 'gate_lonely', label: 'Gate L', cls: 'control', station: 'bounded', how: ['a check'],
                          dims: ['numeric_fidelity'], judges: [], measured: 'atomic' }]);
    const gl = I.rowHtml('gate_lonely', null);
    check('a gate none of whose filed measures uses its code shows no graded-by row, and its tag says so',
          !/>graded by</.test(gl) && />Not scored</.test(gl) && !/>One dimension</.test(gl));
    check('…while a gate with one module-sharing measure reads "One dimension"', />One dimension</.test(gf));
    I.setShared(BASE_SHARED);
}
check('a control folds its after-the-turn group to one line with the counts',
      /<details class="qd-rel-grp qd-rel-fold"><summary class="qd-rel-grp-h">after the turn<span class="qd-fold-n"> \u00b7 graded by 2<\/span><\/summary>/.test(gate)
      && !/<details[^>]*open/.test(gate));
check('a measure keeps its after-the-turn group open (it is the entry)',
      /<div class="qd-rel-grp"><div class="qd-rel-grp-h">after the turn<\/div>/.test(I.rowHtml('grounded_overlap', null))
      && !/qd-rel-fold/.test(I.rowHtml('grounded_overlap', null)));
// A GUARANTEE is grouped by MEMBERSHIP, not by time: the members (what its colour is the
// worst of) in one group, the measures graded about it folded apart, naming where they colour.
{
    const g = I.rowHtml('station_grounded', null);
    check('a guarantee has no time groups — membership is its cut',
          !/qd-rel-grp-h">in the turn</.test(g) && !/qd-rel-grp-h">after the turn</.test(g));
    check('…its members group states the colour rule and the count — no glyph (an orange arc read as a loading spinner)',
          /<div class="qd-rel-grp qd-mem"><div class="qd-rel-grp-h u-tip" data-tip="A member is what a station[^"]*">members \u00b7 3<span class="qd-mem-rule"> \u2014 the worst of these colours GROUNDED<\/span>/.test(g));
    check('…and the non-members fold apart, naming JUDGED as where they colour',
          /<details class="qd-rel-grp qd-rel-fold"><summary class="qd-rel-grp-h">not members \u00b7 graded by 2<span class="qd-fold-n"> \u2014 JUDGED\u2019s members: they colour JUDGED, not GROUNDED<\/span><\/summary>/.test(g)
          && !/<details[^>]*open/.test(g));
    const mem = g.slice(g.indexOf('qd-mem"'), g.indexOf('not members'));
    check('every member row is inside the members group, and no graded-by row is',
          ['upheld by', 'bounded by', 'evidenced by'].every((v) => leads(mem).includes(v)) && !leads(mem).includes('graded by'));
    check('…the members group holds exactly the station\u2019s members',
          JSON.stringify([...mem.matchAll(/qd-rel-chip[^"]*" data-dim="([^"]+)"/g)].map((m) => m[1]).sort())
          === JSON.stringify(['bound_x', 'gate_x', 'meas_x']));
    check('the colour rule carries the same "members · 3" as the group it means, on the same line (no glyph to wrap)',
          /<dd>worst of its members \u00b7 3<\/dd>/.test(g) && !/<svg/.test(g.slice(g.indexOf('worst of its members'), g.indexOf('worst of its members') + 80)));
}
// JUDGED's members are the measures it counts, shelved by moment: the same group, the same
// mark, its own rule ("worst grade of its measures") — one partition across every station.
{
    QD.registerEntries([{ k: 'station_scored', label: 'JUDGED', desc: 'Graded.', long: 'Graded.', cls: 'station',
        q: 'How did its answers grade?', controls: [], dims: [], counts: ['stage_compose', 'stage_deliver'],
        reads: { answer: { where: 'Live Status dial', rows: [['reading', 'worst grade of its measures']] } } }]);
    const j = I.rowHtml('station_scored', null);
    check('JUDGED has a members group: its measures, the worst grade of which colours it',
          /<div class="qd-rel-grp qd-mem">[\s\S]*?members \u00b7 3<span class="qd-mem-rule"> \u2014 the worst grade of these colours JUDGED<\/span>/.test(j));
    check('…holding the moments, noted as the measures they shelve',
          /qd-mem"[\s\S]*?counts<[\s\S]*?<span class="qd-lead-sub">3 measures, by moment<\/span>/.test(j)
          && /data-dim="stage_compose"/.test(j) && /data-dim="stage_deliver"/.test(j));
    check('…and its colour rule carries the same count, no glyph',
          /<dd>worst grade of its measures \u00b7 3<\/dd>/.test(j));
    check('…with no "not members" fold (every measure is its member)', !/not members/.test(j));
}

// ── 2 · a guarantee opens with how it reads ──────────────────────────────────
section('2 · a guarantee reads at both altitudes');
const st = I.rowHtml('station_grounded', null);
const SL = leads(st);
check('how it reads group leads the station', st.indexOf('qd-rel-grp-h">how it reads<') > 0
      && st.indexOf('qd-rel-grp-h">how it reads<') < st.indexOf('qd-mem"'));
check('for one answer · for an agent · its measures · why, in that order',
      before(SL, 'for one answer', 'for an agent') && before(SL, 'for an agent', 'its measures') && before(SL, 'its measures', 'why'));
check('…ahead of the members', before(SL, 'why', 'upheld by') && before(SL, 'upheld by', 'bounded by'));
check('a measure member is EVIDENCED BY, last among the members, noted as taken after the turn',
      before(SL, 'bounded by', 'evidenced by') && st.indexOf('qd-mem"') >= 0 && st.indexOf('qd-mem"') < st.indexOf('evidenced by')
      && /evidenced by<[\s\S]*?<span class="qd-lead-sub">taken after the turn<\/span>/.test(st)
      && st.indexOf('evidenced by') < st.indexOf('not members'));
check('the kind tags carry their definition on hover', /qd-kindn u-tip" data-tip="Taken after the answer; it cannot act/.test(st) && /qd-tag-kind u-tip" data-tip="/.test(st));
check('the question takes the can slot', /qd-can-lead">asks<[\s\S]*Does it stay on its corpus\?/.test(st));
check('each block carries its surface as a sub-label under the lead', /qd-lead-sub">Live Status dial</.test(st) && /qd-lead-sub">Trust Circle/.test(st));
check('the agent block is a label \u2192 phrase table led by the two readings', /<dl class="qd-sub"><dt>configuration \u00b7 could it act\?<\/dt><dd>enforce \u2192 green<\/dd><dt>execution \u00b7 what did it do\?<\/dt>/.test(st));
check('…with the two ring rules as rows', /<dt>several agents<\/dt>/.test(st) && /<dt>open finding<\/dt>/.test(st));
check('a three-string payload still renders as three rows', (() => { QD.registerEntries([{ k: 'station_old', label: 'OLD', desc: 'o', long: 'o', cls: 'station', q: 'q?', reads: { turn: 'T.', ring: 'R.', checks: 'C.' } }]); const o = I.rowHtml('station_old', null); return /for one answer<[\s\S]*T\./.test(o) && /for an agent<[\s\S]*R\./.test(o); })());

// ── 3 · every relation row names the kind and count ──────────────────────────
section('3 · kind and count beside the lead');
const K = kinds(st);
check(`station kinds: ${K.join(' | ')}`, K.includes('control') && K.includes('boundary') && K.includes('measure') && K.includes('measures · 2'));
const GK = kinds(gate);
check(`gate kinds: ${GK.join(' | ')}`, GK.includes('guarantee') && !GK.some((x) => /^propert/.test(x)) && GK.includes('judge') && GK.includes('measures · 2'));
{
    // A measure still names its module — the code that computes it (only a mechanism moved
    // its modules into the fold): one module, the singular, no count.
    const MK = kinds(I.rowHtml('grounded_overlap', null));
    check('a single item carries the singular and no count', MK.includes('module') && !MK.some((x) => /module ·/.test(x)));
    check('…and the measure keeps its "reads" row', leads(I.rowHtml('grounded_overlap', null)).includes('reads'));
}
check('`grades` carries no noun (it lists a mix)', !/qd-kindn">thing/.test(I.rowHtml('grounded_overlap', null)));

// ── 4 · structured facts render as rows ──────────────────────────────────────
section('4 · structured facts');
check('triggers render as a numbered list under "any one of"', /qd-fact-pre">any one of<[\s\S]*<ol class="qd-trig"><li>one unsupported number<\/li><li>/.test(gate));
check('then and ignores are rows of their own', L.includes('then') && L.includes('ignores') && /ignores<[\s\S]*own structure/.test(gate));
check('settings render as setting → effect', /<dt><code>mode<\/code><\/dt><dd>what happens on a hit<\/dd>/.test(gate));
check('fixed has its own row, with the reason', /qd-rel-lead[^>]*>fixed<[\s\S]*trigger sensitivity[\s\S]*not configurable/.test(gate));
check('why folds behind a details element', /<details class="qd-why"><summary>[\s\S]*<\/summary><p>The trigger is tuned for recall\./.test(gate));
const seal = I.rowHtml('seal_x', null);
check('a point that does not fire says never, with what it does instead', /qd-fact-never">never<\/span> it always runs/.test(seal));
check('…and its "how" row reads "how it reads" (not a control)', leads(seal).includes('how it reads') && !leads(seal).includes('how it decides'));
const legacy = I.rowHtml('legacy_x', null);
check('a prose payload still renders both facts, one row each', /fires when<[\s\S]*A prose trigger\./.test(legacy) && /you can change<[\s\S]*The mode\./.test(legacy));

// ── 5 · the description, the can line, the id mark ───────────────────────────
section('5 · description · can · id');
check('the description is the note alone', /qd-row-desc">Judges whether the answer stayed inside its scope\.<\/div>/.test(gate) && !/spans several/.test(gate));
check('the can line renders the fates, the stop pill solid',
      /qd-pill qd-pill-stop[^>]*>stops the answer</.test(gate) && /qd-pill[^>]*>adds a note</.test(gate));
check('a reporting point reads "reports only"', /qd-pill-none[^>]*>reports only</.test(seal));
check('the wire key is an id mark that copies, with the key kept in the DOM',
      /<button type="button" class="qd-id u-tip" data-qd-copy="gate_x"/.test(gate) && /qd-row-key">gate_x</.test(gate));
check('…and the bare key is not printed on the reading line', !/<span class="qd-row-key">gate_x<\/span>\s*<span class="qd-tag">/.test(gate));
check('the title line carries the kind tag', /qd-tag qd-tag-kind[^>]*>Control</.test(gate) && /qd-tag qd-tag-kind[^>]*>Guarantee</.test(st));
check('every lead carries its definition on hover', (gate.match(/qd-rel-lead u-tip" data-tip="/g) || []).length >= 9);

// ── 6 · the facet bar folds; the glossary defines leads and kinds ────────────
section('6 · facets and glossary');
const fb = I.facetBar(['gate_x', 'seal_x', 'station_grounded', 'grounded_overlap']);
check('the bar leads with the group names', /qd-facet-lead">Filter</.test(fb) && /data-qd-facet="group" data-qd-facet-v="class"/.test(fb));
check('exactly one group is open', (fb.match(/qd-facet-group open/g) || []).length === 1);
check('the open group is Kind by default, with its pills', /qd-facet-open">\s*<span class="qd-facet-lead">Kind</.test(fb) && /data-qd-facet="class"/.test(fb));
check('the other groups’ pills are folded', !/data-qd-facet="measured"/.test(fb));
const gl = I.glossaryHtml();
for (const w of ['upholds', 'escalates to', 'graded by', 'reads', 'weights', 'weighs on', 'fires when', 'ignores', 'fixed', 'why', 'for an agent'])
    check(`glossary defines "${w}"`, new RegExp(`<dt>${w}</dt><dd>[^<]{10,}`).test(gl));
check('the glossary defines the kinds, mechanism first', /The kinds[\s\S]*<dt>mechanism<\/dt><dd>The umbrella word/.test(gl));
check('…and the ring’s two readings', /On the ring[\s\S]*<dt>configuration<\/dt><dd>Could it act\?/.test(gl) && /<dt>execution<\/dt><dd>What did it do\?/.test(gl));
// The served Terms: absent until the vocabulary lands (no invented section), present after.
check('no Terms section before the vocabulary serves them', !/Terms used on the rows/.test(gl));
QD.registerTerms([{ term: 'window', def: 'The recent turns the ring reads for an agent.' }, { term: 'judge', def: 'A check an LLM panel returns.' }]);
const gl2 = I.glossaryHtml();
check('the Terms section renders each served term with its definition',
      /Terms used on the rows[\s\S]*<dt>window<\/dt><dd>The recent turns the ring reads for an agent\.<\/dd>[\s\S]*<dt>judge<\/dt>/.test(gl2));

// ── 7 · the checks a window aggregate weights: one relation, named at both ends ──
// `weights` sits where `reads` sits (what the reading is made of, before what it does with
// it); the check answers `weighs on`. Neither borrows `graded by` (they feed it, they do
// not grade it) nor `reads` (there is no module).
section('7 · weights');
const agg = I.rowHtml('agg_x', null);
const AL = leads(agg);
check(`the aggregate's leads: ${AL.join(' · ')}`, ['how it reads', 'weights', 'fires when', 'then', 'you can change'].every((x) => AL.includes(x)));
check('weights comes after how it reads and before fires when', before(AL, 'how it reads', 'weights') && before(AL, 'weights', 'fires when'));
check('the row names its kind and count', kinds(agg).includes('measures · 2'));
check('each weighted check is a chip that opens the measure', /qd-rel-chip[^"]*" data-dim="scope_adherence"/.test(agg) && /qd-rel-chip[^"]*" data-dim="grounded_overlap"/.test(agg));
check('no reads row and no graded by row on the aggregate', !AL.includes('reads') && !AL.includes('graded by'));
const sa = I.rowHtml('scope_adherence', null);
check('the check says `weighs on` back, with the aggregate as the chip', leads(sa).includes('weighs on') && /qd-rel-chip[^"]*" data-dim="agg_x"/.test(sa));
check('…after what it grades', before(leads(sa), 'grades', 'weighs on'));
check('a check nothing weights has no such row', !leads(I.rowHtml('safety', null)).includes('weighs on'));

console.log(fail ? `\nFAILED — ${pass} passed, ${fail} failed` : `\nALL PASS — ${pass} assertions (the Guide's one anatomy).`);
process.exit(fail ? 1 : 0);
