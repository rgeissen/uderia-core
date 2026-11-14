/**
 * CI gate: the ANDP constructor modal — offered acts, multi-select, and the notice.
 *
 * Three things here fail SILENTLY, which is why they are executed rather than read:
 *
 *  - After a successful build, leaving "Build Knowledge Graph" active invites a
 *    silent DUPLICATE build (a second graph, no warning). The footer therefore
 *    swaps to Build another / Done and the inputs lock — the form is now a record.
 *  - A build of several products has several outcomes; collapsing them into one
 *    verdict hides a graph that is missing a product, which looks exactly like a
 *    complete one until an agent is asked something it can no longer answer.
 *  - Building into "a new knowledge graph" DEACTIVATES the graph the chosen agent
 *    is currently grounded on. Nothing is deleted, but the agent stops seeing it.
 *    That is the default path, and it is how this instance ended up with two
 *    3,111-entity NIBRS graphs, one quietly retired.
 *
 * The shipped functions are extracted and EXECUTED here (not reimplemented), so a
 * regression in the real file fails rather than a copy of it.
 *
 * Run: node test/test_kg_andp_modal.mjs
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const src = fs.readFileSync(
  path.join(ROOT, 'static/js/handlers/rag/kgAndpSemanticModal.js'), 'utf8');

// extract functions verbatim — no reimplementation
const pick = (name) => {
  const i = src.indexOf(`function ${name}(`);
  if (i < 0) throw new Error(`${name} not found`);
  let d = 0, j = src.indexOf('{', i);
  for (let k = j; k < src.length; k++) {
    if (src[k] === '{') d++;
    else if (src[k] === '}') { d--; if (!d) return src.slice(i, k + 1); }
  }
};

let pass = 0, fail = 0;
const t = (n, c) => { c ? pass++ : (fail++, console.log('  FAIL:', n)); };

// ── element stub ────────────────────────────────────────────────────────────
const els = {};
const mk = (id) => (els[id] = {
  id, disabled: false, value: '', textContent: '', innerHTML: '', style: {},
  _c: new Set(id.match(/again|done|notice/) ? ['hidden'] : []),
  classList: {
    toggle(c, on) { on ? this._o._c.add(c) : this._o._c.delete(c); },
    add(c) { this._o._c.add(c); }, remove(c) { this._o._c.delete(c); },
    contains(c) { return this._o._c.has(c); },
  },
});
for (const id of ['kg-andp-build','kg-andp-cancel','kg-andp-again','kg-andp-done',
                  'kg-andp-profile','kg-andp-connection','kg-andp-name','kg-andp-notice',
                  'kg-andp-backend','kg-andp-columns','kg-andp-physical','kg-andp-knowledge',
                  'kg-andp-nonbusiness','kg-andp-validate','kg-andp-target']) { mk(id); els[id].classList._o = els[id]; }

let checkboxes = [];                       // the product tick-list
const _overlay = {
  querySelector: (s) => {
    if (s === '.kg-andp-prod:checked') return checkboxes.find(c => c.checked) || null;
    return els[s.replace('#', '')] || null;
  },
  querySelectorAll: (s) => {
    if (s === '.kg-andp-prod:checked') return checkboxes.filter(c => c.checked);
    if (s === '.kg-andp-prod') return checkboxes;
    return [];
  },
};
const setProducts = (spec) => {
  checkboxes = spec.map(([value, checked]) =>
    ({ value, checked, disabled: false, dataset: { semantic: `${value}_Semantic` } }));
};

const factory = new Function('_overlay', '_graphs', 'window',
  pick('_setFooter') + '\n' + pick('_setFormEnabled') + '\n' +
  pick('_selectedProducts') + '\n' + pick('_updateNotice') + '\n' +
  pick('_productLines') + '\n' + pick('_esc') +
  '\nreturn { _setFooter, _setFormEnabled, _selectedProducts, _updateNotice, _productLines };');

const GRAPHS = [
  { kg_id: 'g1', kg_name: 'Policing Estate (NIBRS + CASE)', profile_id: 'p1',
    is_active_for_owner: true, total_entities: 3181 },
  { kg_id: 'g2', kg_name: 'Fitness Database KG', profile_id: 'p2',
    is_active_for_owner: true, total_entities: 86 },
];
const api = factory(_overlay, GRAPHS, {});
const { _setFooter, _setFormEnabled, _selectedProducts, _updateNotice, _productLines } = api;
const hidden = (id) => els[id].classList.contains('hidden');

// ── 1. only the act that is OWED is offered ─────────────────────────────────
setProducts([['NIBRS_Source', true]]);
_setFooter('form'); _setFormEnabled(true);
t('form: Build shown', !hidden('kg-andp-build'));
t('form: Cancel shown', !hidden('kg-andp-cancel'));
t('form: Done hidden', hidden('kg-andp-done'));
t('form: Build another hidden', hidden('kg-andp-again'));
t('form: inputs enabled', !els['kg-andp-name'].disabled);
t('form: product ticks enabled', !checkboxes[0].disabled);

_setFooter('done'); _setFormEnabled(false);
t('done: Build HIDDEN (no duplicate build offered)', hidden('kg-andp-build'));
t('done: Cancel hidden', hidden('kg-andp-cancel'));
t('done: Done shown', !hidden('kg-andp-done'));
t('done: Build another shown', !hidden('kg-andp-again'));
t('done: inputs locked', els['kg-andp-name'].disabled && els['kg-andp-columns'].disabled);
t('done: product ticks locked too', checkboxes[0].disabled);

_setFooter('form'); _setFormEnabled(true);
t('back to form: Build shown again', !hidden('kg-andp-build'));
t('back to form: Done hidden again', hidden('kg-andp-done'));
t('back to form: inputs re-enabled', !els['kg-andp-name'].disabled);

// ── 2. multi-select ─────────────────────────────────────────────────────────
setProducts([['NIBRS_Source', true], ['CASE_Source', true], ['OTHER', false]]);
t('only ticked products are submitted',
  JSON.stringify(_selectedProducts()) === '["NIBRS_Source","CASE_Source"]',
  JSON.stringify(_selectedProducts()));
setProducts([['A', false], ['B', false]]);
t('nothing ticked -> nothing submitted', _selectedProducts().length === 0);
t('the payload sends the list', /products,/.test(src));
t('a single product still works (back-compat kept server-side)',
  /const product = products\[0\]/.test(src));
t('submitting with none selected is refused',
  /if \(!products\.length\) return showAppBanner/.test(src));

// ── 3. the notice speaks only when something is not obvious ─────────────────
els['kg-andp-profile'].value = 'p1'; els['kg-andp-target'].value = '';
setProducts([['NIBRS_Source', true]]);
_updateNotice();
t('new graph + profile already grounded -> warns', !hidden('kg-andp-notice'));
t('   names the graph that would be deactivated',
  /Policing Estate/.test(els['kg-andp-notice'].innerHTML));
t('   says deactivated, not deleted',
  /deactivate/i.test(els['kg-andp-notice'].innerHTML)
  && /kept, not deleted/i.test(els['kg-andp-notice'].innerHTML));
t('   offers the safe alternative',
  /Build into/i.test(els['kg-andp-notice'].innerHTML));

els['kg-andp-profile'].value = 'p9';       // a profile with no graph
_updateNotice();
t('nothing surprising -> silent', hidden('kg-andp-notice'));

els['kg-andp-target'].value = 'g1';
_updateNotice();
t('adding to an existing graph -> says so', !hidden('kg-andp-notice'));
t('   names the target and what it holds',
  /Policing Estate/.test(els['kg-andp-notice'].innerHTML)
  && /3181/.test(els['kg-andp-notice'].innerHTML));
t('   promises nothing is removed', /Nothing is removed/i.test(els['kg-andp-notice'].innerHTML));
t('   never calls it a merge', !/merge/i.test(els['kg-andp-notice'].innerHTML));

els['kg-andp-target'].value = '';
els['kg-andp-profile'].value = 'p9';
setProducts([['A', true], ['B', true]]);
_updateNotice();
t('several products -> explains they share one graph', !hidden('kg-andp-notice'));
t('   states the collision guarantee',
  /product-qualified/.test(els['kg-andp-notice'].innerHTML));

// ── 4. per-product outcomes are never averaged into one verdict ─────────────
const partial = _productLines({ products: [
  { product: 'NIBRS_Source', status: 'success', entities_added: 3111 },
  { product: 'CASE_Source', status: 'failed', error: 'no data_product_map' },
]});
t('a built product is named', /NIBRS_Source/.test(partial));
t('a failed product is named', /CASE_Source/.test(partial));
t('the failure reason is shown', /no data_product_map/.test(partial));
t('failure is not styled as success',
  /trust-danger/.test(partial) && /trust-pass/.test(partial));
// NB: the RULE that decides "partial" is backend Python — gated by name in
// test_andp_multiselect.py:t_overall_status. Grepping this file for it once looked
// like coverage and was not: a mutation that rounded partial up to success in
// rest_routes.py passed all 39 assertions here.
t('the UI renders a partial build distinctly from a success',
  /data\.status === 'partial'/.test(src) && /trust-warn/.test(src));
t('a partial build still keeps the graph (not thrown away)',
  /data\.status !== 'success' && data\.status !== 'partial'/.test(src));
t('the banner names the failures', /product\` \+\s*\n?.*failed/.test(src) || /failed\./.test(src));
t('no products array -> falls back to the single product',
  /product:/.test(_productLines({ product: 'X' })));

// ── build-wide reporting: the result card (P3, Aug 2026) ────────────────────
// The card previously served the LAST product's stats/scope as the build's —
// a CASE+NIBRS card read as NIBRS-only, and the scope line understated what the
// grounding gate enforces. These execute the shipped renderers verbatim.
const rfactory = new Function('window',
  pick('_esc') + '\n' + pick('_productLines') + '\n' + pick('_mismatchLines') +
  '\n' + pick('_bridgeLine') + '\n' + pick('_renderResult') +
  '\nreturn { _mismatchLines, _renderResult };');
const { _mismatchLines, _renderResult } = rfactory({});

const TWO = {
  status: 'success', appended: false,
  products: [
    { product: 'CASE_Source', status: 'success', entities_added: 71,
      semantic_database: 'CASE_Source_Semantic',
      scope_databases: ['CASE_Source_Domain'],
      stats: { entities_logical: 15, attributes_logical: 4, type_mismatches: 0,
               glossary_terms: 3 } },
    { product: 'NIBRS_Source', status: 'success', entities_added: 3112,
      semantic_database: 'NIBRS_Source_Semantic',
      scope_databases: ['NIBRS_Source_Domain'],
      stats: { entities_logical: 26, attributes_logical: 1508,
               type_mismatches: 1425, glossary_terms: 5 } },
  ],
  // build-wide, as the backend now serves them:
  scope_databases: ['CASE_Source_Domain', 'NIBRS_Source_Domain'],
  structural: { entities_added: 3183, relationships_added: 4750 },
  stats: { entities_logical: 41, attributes_logical: 1512, type_mismatches: 1425,
           glossary_terms: 8 },
  semantic_database: 'NIBRS_Source_Semantic',
};
const card = _renderResult(TWO);
t('scope line carries BOTH domains',
  /CASE_Source_Domain, NIBRS_Source_Domain/.test(card));
t('no top-level semantic-module row (per-product lines carry it)',
  !/semantic module/.test(card));
t('per-product lines still name each semantic module',
  /CASE_Source_Semantic/.test(card) && /NIBRS_Source_Semantic/.test(card));
t('stats block shows the build-wide sums', /41 entities/.test(card)
  && /1512 attributes/.test(card) && /8 glossary terms/.test(card));
t('divergence warning is attributed to the product that carries it',
  /NIBRS_Source: 1425 columns/.test(card));
t('a clean product is NOT blamed for its neighbour\'s divergence',
  !/CASE_Source: /.test(_mismatchLines(TWO, TWO.stats)));

const ONE = { products: [TWO.products[1]], stats: TWO.products[1].stats };
t('single product: divergence line is unattributed (nothing to tell apart)',
  /^\s*<div/.test(_mismatchLines(ONE, ONE.stats))
  && !/NIBRS_Source: /.test(_mismatchLines(ONE, ONE.stats))
  && /1425 columns/.test(_mismatchLines(ONE, ONE.stats)));
t('legacy response (no per-product stats): aggregate fallback, unattributed',
  /1425 columns/.test(_mismatchLines({ products: [] },
                                     { type_mismatches: 1425 })));
t('zero divergence renders nothing',
  _mismatchLines({ products: [] }, { type_mismatches: 0 }) === ''
  && _mismatchLines(TWO, { ...TWO.stats, type_mismatches: 0 }) !== undefined);


console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
