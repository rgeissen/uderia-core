#!/usr/bin/env node
/**
 * The kg_enrichment step title is built in ONE place, for BOTH paths.
 *
 * WHY THIS GATE EXISTS
 * Reported from a walkthrough with two screenshots of the SAME event: during
 * live execution the step read a bare "Knowledge Graph Enrichment"; after a
 * browser refresh it read "(51 nodes, 62 relationships, 1 data product:
 * support_load)". Two render paths, two title builders, one of them missing —
 * and the missing half was the live one, where the operator actually watches.
 * A title that names WHICH data products the planner was shown is the fact the
 * event exists for (it settled a root-cause investigation twice in one day).
 *
 * The fix is ONE exported builder (ui.js kgEnrichmentTitle) consumed by the
 * reload title functions AND rewritten onto the event at the ONE chokepoint
 * every live render passes through (updateStatusWindow).
 *
 * Run: node test/test_kg_event_title.mjs        (no server, no network)
 */
import { readFileSync } from 'fs';

const ui = readFileSync('static/js/ui.js', 'utf8');
const eh = readFileSync('static/js/eventHandlers.js', 'utf8');

let pass = 0, fail = 0;
const check = (name, cond, detail) => {
    if (cond) { pass++; }
    else { fail++; console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ''}`); }
};

/* ---------------------------------------------------- extract + execute it */
function sliceFn(src, marker) {
    const i = src.indexOf(marker);
    if (i < 0) return null;
    let depth = 0, j = src.indexOf('{', i);
    for (let k = j; k < src.length; k++) {
        if (src[k] === '{') depth++;
        else if (src[k] === '}') { depth--; if (depth === 0) return src.slice(i, k + 1); }
    }
    return null;
}

const src = sliceFn(ui, 'export function kgEnrichmentTitle(payload)');
check('the builder exists and is exported', !!src);
if (!src) { console.log(`\nFAIL — builder missing`); process.exit(1); }
const VOCAB = readFileSync('static/js/kgVocabulary.js', 'utf8').replace(/^export /gm, '');   // the graph's counting vocabulary, as shipped
const title = new Function(`${VOCAB}\n${src.replace('export function', 'function')}; return kgEnrichmentTitle;`)();

/* the exact payload from the operator's screenshot */
check('the reported live case renders enriched',
      title({ enrichments: [{ entity_count: 51, relationship_count: 62,
                              data_products: ['support_load'] }] })
      === 'Knowledge Graph Enrichment (51 nodes, 62 relationships, 1 data product: support_load)');
check('top-level totals win when present',
      title({ total_entities: 9, total_relationships: 4, enrichments: [{ entity_count: 51 }] })
      .startsWith('Knowledge Graph Enrichment (9 nodes, 4 relationships'));
check('no products -> counts only',
      title({ total_entities: 3, total_relationships: 2 })
      === 'Knowledge Graph Enrichment (3 nodes, 2 relationships)');
check('products dedupe across enrichments',
      title({ enrichments: [{ data_products: ['a'] }, { data_products: ['a', 'b'] }] })
      .includes('2 data products: a, b'));
check('overflow past three is summarised',
      title({ enrichments: [{ data_products: ['a', 'b', 'c', 'd'] }] })
      .includes('4 data products: a, b, c +1'));
check('a missing payload never throws', title(undefined).includes('(0 nodes'));

/* ── the second recall channel, on the collapsed line ─────────────────────────
   Seeding matches the words a question used AND what it meant. The title carries
   the second only when it fired: a clause on every turn is furniture. Critically
   it must distinguish a graph whose INDEX IS STILL BUILDING from one whose words
   simply matched — read as a bare absence, the first looks like a finding about
   the question when it is a fact about the deployment. */
const withSeeding = (ss) => title({
    total_entities: 51, total_relationships: 62,
    enrichments: [{ entity_count: 51, relationship_count: 62, data_products: [],
                    semantic_seeding: ss }] });
check('entities reached only by meaning are named on the title',
      withSeeding({ state: 'ready', hits: 4, bridged: [{ name: 'db.Party', score: 0.7 }] })
      === 'Knowledge Graph Enrichment (51 nodes, 62 relationships, 1 by meaning)');
check('two bridges pluralise the count, not the noun',
      withSeeding({ state: 'ready', hits: 4,
                    bridged: [{ name: 'a' }, { name: 'b' }] }).includes('2 by meaning'));
check('semantics that agreed without adding anything stay silent',
      withSeeding({ state: 'ready', hits: 2, bridged: [] })
      === 'Knowledge Graph Enrichment (51 nodes, 62 relationships)');
check('a still-building index says so rather than reading as an empty result',
      withSeeding({ state: 'building', hits: 0, bridged: [] })
      .includes('semantic index warming'));
check('a cold index says the same', withSeeding({ state: 'cold', hits: 0, bridged: [] })
      .includes('semantic index warming'));
check('the channel being off adds no clause',
      withSeeding({ state: 'off', hits: 0, bridged: [] })
      === 'Knowledge Graph Enrichment (51 nodes, 62 relationships)');
check('an event predating the channel renders exactly as before',
      title({ total_entities: 51, total_relationships: 62, enrichments: [{ entity_count: 51 }] })
      === 'Knowledge Graph Enrichment (51 nodes, 62 relationships)');
check('surface-only keeps the meaning clause too',
      title({ surface_only: true,
              enrichments: [{ entity_count: 51, data_products: ['p'],
                              semantic_seeding: { state: 'ready', hits: 1,
                                                  bridged: [{ name: 'x' }] } }] })
      .includes('1 by meaning'));

/* ── the details card ─────────────────────────────────────────────────────────
   Before this the event had NO custom renderer, so it fell through to a raw
   JSON dump of the whole payload (table_columns, join_keys, scope...) — which is
   where a reader stopped reading, and where the seeding fact would have been
   buried. The card must lead with how the question reached the entities. */
const cardSrc = sliceFn(ui, 'function _renderKgEnrichmentDetails(details)');
check('the card renderer exists', !!cardSrc);
const card = new Function(
    `const escapeHtml=(x)=>String(x==null?'':x).replace(/&/g,'&amp;').replace(/</g,'&lt;');
     const _trustChip=(lvl,txt)=>\`<span data-lvl="\${lvl}">\${txt}</span>\`;
     const _trustVal=(lvl)=>\`data-lvl="\${lvl}"\`;
     ${VOCAB}
     ${cardSrc}; return _renderKgEnrichmentDetails;`)();

const cardFor = (ss, extra = {}) => card(Object.assign({
    total_entities: 51, total_relationships: 62,
    enrichments: [Object.assign({ entity_count: 51, relationship_count: 62,
                                  semantic_seeding: ss }, extra)] }, extra.top || {}));

const bridgedCard = cardFor({ state: 'ready', hits: 3,
                              bridged: [{ name: 'db.Party', score: 0.72 }],
                              bridged_dropped: 2 });
check('the card leads with how the question reached the entities',
      bridgedCard.indexOf('Seeded by') < bridgedCard.indexOf('Injected'), bridgedCard);
check('it names the bridged entity and its similarity',
      bridgedCard.includes('db.Party') && bridgedCard.includes('0.72'));
check('it reports hits that never reached the prompt rather than hiding them',
      bridgedCard.includes('2 semantic hits'), bridgedCard);
check('a bridge reads as a pass, not a warning', bridgedCard.includes('data-lvl="pass"'));

check('“ran and found nothing” is not dressed as a failure',
      cardFor({ state: 'ready', hits: 0, bridged: [] }).includes('nothing cleared'));
check('a warming index is a warning, and says why there are no hits',
      (() => { const c = cardFor({ state: 'building', hits: 0, bridged: [] });
               return c.includes('data-lvl="warn"') && c.includes('still building'); })());
check('the channel being off says so plainly',
      cardFor({ state: 'off', hits: 0, bridged: [] }).includes('semantic seeding is off'));
check('an event with no seeding block still renders (and says lexical only)',
      card({ total_entities: 5, total_relationships: 2, enrichments: [{ entity_count: 5 }] })
      .includes('semantic seeding is off'));
check('the card never throws on a malformed payload',
      typeof card(undefined) === 'string' && typeof card({ enrichments: null }) === 'string');
check('a long bridge list is summarised rather than dumped',
      cardFor({ state: 'ready', hits: 9,
                bridged: Array.from({ length: 9 }, (_, i) => ({ name: `n${i}`, score: 0.5 })) })
      .includes('+3 more'));
check('entity names are escaped',
      cardFor({ state: 'ready', hits: 1, bridged: [{ name: '<img src=x>', score: 0.5 }] })
      .includes('&lt;img'));

/* WHICH graph enriched the turn (Sep 2026). The event carried the owner profile but not
   the graph's own identity — a consumer's pack copy sits beside the publisher's and the
   two were indistinguishable on the card. The row is rendered from `kg_name` and OMITTED
   (never invented) on an event that predates the stamp. */
const named = cardFor({ state: 'ready', hits: 0, bridged: [] }, { kg_name: 'NIBRS Source Ontology', kg_id: 'kg-1' });
check('the card names the graph that enriched the turn',
      named.includes('>Graph<') && named.includes('NIBRS Source Ontology'), named);
check('the Graph row carries the counts beside the name',
      named.includes('51 nodes') && named.includes('62 relationships'));
check('the Graph row leads the card (identity before mechanism)',
      named.indexOf('>Graph<') < named.indexOf('Seeded by'));
const unnamed = cardFor({ state: 'ready', hits: 0, bridged: [] });
check('an event predating the stamp renders NO Graph row (nothing invented)',
      !unnamed.includes('>Graph<'), unnamed);
check('the graph name is escaped',
      cardFor({ state: 'ready', hits: 0, bridged: [] }, { kg_name: '<b>x</b>' }).includes('&lt;b'));
check('two graphs on one turn are both named',
      card({ enrichments: [{ kg_name: 'A', entity_count: 1 }, { kg_name: 'B', entity_count: 1 }] })
      .includes('A, B'));

/* Model B — WHICH VERSION the agent grounded on (Tier 4.3 §1a). Three honest readings and
   an absence: a release (pass), a deprecated release (warn), release-only requested but no
   release exists so the draft was read (warn — the owner asked for something they did not
   get), and an older event with no grounding stamp (no row — nothing invented). */
const g = (grounding) => cardFor({ state: 'ready', hits: 0, bridged: [] }, { grounding });
const rel = g({ mode: 'released', kg_id: 'kg-1::released', version_id: 'kgv-1', label: '1.2.0', lifecycle: 'published' });
check('a released-mode turn names the release it grounded on',
      rel.includes('Grounded on') && rel.includes('released 1.2.0') && rel.includes('data-lvl="pass"'), rel);
const dep = g({ mode: 'released', kg_id: 'kg-1::released', version_id: 'kgv-1', label: '1.0.0', lifecycle: 'deprecated' });
check('a DEPRECATED release reads as a warning and says so',
      dep.includes('data-lvl="warn"') && dep.includes('deprecated'), dep);
const fb = g({ mode: 'released', fallback: 'live', reason: 'no release yet' });
check('release-only requested with no release = a warning naming the fallback',
      fb.includes('data-lvl="warn"') && fb.includes('live draft') && fb.includes('no release yet'), fb);
check('a live-mode turn says live draft, quietly (no chip)',
      g({ mode: 'live' }).includes('live draft') && !g({ mode: 'live' }).includes('data-lvl="warn"'));
check('an event with no grounding stamp renders NO Grounded-on row',
      !cardFor({ state: 'ready', hits: 0, bridged: [] }).includes('Grounded on'));
check('the release label is escaped',
      g({ mode: 'released', kg_id: 'x', label: '<i>' }).includes('&lt;i'));

/* Every render path must carry it — a branch missing from one engine's renderer
   flattens the fact for that engine alone, which is exactly how the title bug this
   gate was written for happened. Asserted per NAMED path: counting occurrences let a
   removed dispatch pass, because the function's own DEFINITION counts as one. */
for (const fnName of ['_renderGenieStep', '_renderConversationAgentStep', '_renderStandardStep']) {
    const body = sliceFn(ui, `function ${fnName}(eventData`);
    check(`${fnName} dispatches kg_enrichment to the card`,
          !!body && body.includes('_renderKgEnrichmentDetails(details)'),
          body ? 'no dispatch in this path' : 'path not found');
}

/* ANALYSED vs INJECTED — the operator's second finding: under an exclusive
   surface the counts describe the subgraph that was ANALYSED for scope, while
   the prompt received the contract ALONE. The old title read "51 nodes" over
   a turn that injected none of them — claiming an injection that did not
   happen, on a transparency surface. */
const so = title({ surface_only: true,
                   enrichments: [{ entity_count: 51, relationship_count: 62,
                                   data_products: ['support_load'] }] });
check('surface-only leads with what was INJECTED',
      so.includes('surface-only — injected 1 data product contract: support_load'), so);
check('surface-only says the schemas were withheld', so.includes('schemas withheld'));
check('the analysed count survives, labelled as ANALYSED',
      so.includes('51 nodes analysed for scope'),
      'hiding the analysis would be the opposite lie — the scope derivation is real work');
check('surface-only never renders the plain counts-lead form',
      !so.startsWith('Knowledge Graph Enrichment (51 nodes'));
check('the flag also reads from the enrichment entry',
      title({ enrichments: [{ surface_only: true, entity_count: 5,
                              data_products: ['p'] }] }).includes('surface-only'));
check('an unconfined turn is byte-identical to before',
      title({ enrichments: [{ entity_count: 51, relationship_count: 62,
                              data_products: ['support_load'] }] })
      === 'Knowledge Graph Enrichment (51 nodes, 62 relationships, 1 data product: support_load)');

/* ------------------------------------------------- single source, both paths */
const templateCount = (ui.match(/Knowledge Graph Enrichment \(\$\{/g) || []).length
                    + (eh.match(/Knowledge Graph Enrichment \(\$\{/g) || []).length;
check('the title template exists exactly ONCE across both files', templateCount === 1,
      `found ${templateCount} — a second copy is the live/reload split reborn`);

const upd = sliceFn(ui, 'export function updateStatusWindow(eventData');
check('the LIVE chokepoint rewrites the title', !!upd
      && /type === 'kg_enrichment'/.test(upd) && /kgEnrichmentTitle\(/.test(upd),
      'every live render path passes through updateStatusWindow — patching the three '
      + 'renderers separately is how one of them ends up missed');
check('the live rewrite cannot break a render', !!upd
      && upd.indexOf('catch') > -1 && upd.indexOf('catch') < upd.indexOf('const { step'));

check('both reload title functions delegate to the ONE builder',
      (eh.match(/UI\.kgEnrichmentTitle\(/g) || []).length === 2,
      'the two title functions in eventHandlers previously carried their own copies');
check('no private product-suffix helper survives',
      !ui.includes('_kgProductSuffix') && !eh.includes('_kgProductSuffix'),
      'a dead copy is the seed of the next drift');

console.log(fail
    ? `\nFAIL — ${fail} of ${pass + fail} assertions failed.`
    : `\nALL PASS — ${pass} assertions (one kg_enrichment title + its card, live and reload).`);
process.exit(fail ? 1 : 0);
