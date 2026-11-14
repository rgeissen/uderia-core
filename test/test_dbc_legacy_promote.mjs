/**
 * CI gate: a legacy database connection can be promoted from the UI.
 *
 * The gap this closes: a `canvas_conn_*` connection lives in the OLD Canvas credential
 * storage, so it has no central record for Edit and Delete to act on — correctly, they
 * are omitted. The store could always promote one (`migrate_legacy`, exposed at
 * POST /v1/database-connections/migrate-legacy) and NOTHING IN THE UI EVER CALLED IT.
 * The row was read-only, with no way out and nothing on screen saying a way out existed.
 * The capability was present; only the affordance was missing.
 *
 * Executed against the shipped renderer under a DOM stub, because every failure here is
 * silent: a button that renders but is never wired, a promote that succeeds but never
 * refreshes the list, or a count reported as 1 when the endpoint promoted three.
 *
 * Run: node test/test_dbc_legacy_promote.mjs
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const SRC = path.join(ROOT, 'static/js/handlers/platformConnectorHandler.js');
const src = fs.readFileSync(SRC, 'utf8');

let pass = 0, fail = 0;
const t = (n, c, d = '') => { c ? pass++ : (fail++, console.log('  FAIL:', n, d ? `— ${d}` : '')); };

// ── extract the shipped functions verbatim ──────────────────────────────────
const pick = (name) => {
  const i = src.indexOf(`function ${name}(`);
  if (i < 0) throw new Error(`${name} not found`);
  const start = src.lastIndexOf('async', i) === i - 6 ? i - 6 : i;
  let d = 0;
  for (let k = src.indexOf('{', i); k < src.length; k++) {
    if (src[k] === '{') d++;
    else if (src[k] === '}') { d--; if (!d) return src.slice(start, k + 1); }
  }
};

// ── stubs ───────────────────────────────────────────────────────────────────
let fetched = [];        // every request the code makes
let reloaded = 0;        // did the list refresh after promoting?
let banners = [];
let migratedCount = 3;   // the endpoint promotes ALL of the user's legacy rows
let failNext = false;

const mkBtn = () => ({ textContent: 'Promote', disabled: false, dataset: {} });

const env = {
  fetch: async (url, opts) => {
    fetched.push({ url, method: (opts && opts.method) || 'GET' });
    if (failNext) return { ok: false, status: 500, json: async () => ({ message: 'boom' }) };
    return { ok: true, status: 200, json: async () => ({ status: 'success', migrated: migratedCount }) };
  },
  _pconnuHeaders: () => ({}),
  _dbcLoadList: async () => { reloaded++; },
  window: { showAppBanner: (msg, kind) => banners.push({ msg, kind }) },
};

const promote = new Function('fetch', '_pconnuHeaders', '_dbcLoadList', 'window',
  pick('_dbcPromoteLegacy') + '\nreturn _dbcPromoteLegacy;'
)(env.fetch, env._pconnuHeaders, env._dbcLoadList, env.window);

// ── 1. the affordance exists, and only where it belongs ─────────────────────
t('legacy rows render a Promote button', /pmcp-dbc-promote/.test(src));
t('it is wired, not just rendered',
  /querySelectorAll\('\.pmcp-dbc-promote'\)[\s\S]{0,120}addEventListener/.test(src));
t('it explains itself on hover', /title="Promote into the central store/.test(src));
t('it says the original is kept', /Non-destructive/.test(src));

// Edit/Delete must still be withheld from legacy rows — promoting is the way in,
// and offering Edit on a record that does not exist centrally would simply fail.
// Bound the slice from the START marker forward — an earlier `.join('')` exists in
// this file, and searching for it globally produced an end index BEFORE the start,
// so the block was empty and both assertions below tested nothing while "failing"
// for the wrong reason. A bound is not an assertion.
const rowStart = src.indexOf('const legacy = c.source');
const rowBlock = src.slice(rowStart, src.indexOf(".join('')", rowStart));
t('the row block was actually captured', rowBlock.length > 200 && rowBlock.length < 4000,
  `len ${rowBlock.length}`);
t('legacy rows still withhold Edit and Delete',
  /legacy\s*\?/.test(rowBlock) && /pmcp-dbc-edit/.test(rowBlock) && /pmcp-dbc-del/.test(rowBlock));
t('promote is on the legacy branch, edit on the other',
  rowBlock.indexOf('pmcp-dbc-promote') > -1
  && rowBlock.indexOf('pmcp-dbc-promote') < rowBlock.indexOf('pmcp-dbc-edit'),
  `promote@${rowBlock.indexOf('pmcp-dbc-promote')} edit@${rowBlock.indexOf('pmcp-dbc-edit')}`);

// ── 2. it calls the right endpoint, the right way ───────────────────────────
await promote(mkBtn());
t('POSTs to the migrate endpoint', fetched.length === 1
  && fetched[0].url === '/api/v1/database-connections/migrate-legacy'
  && fetched[0].method === 'POST', JSON.stringify(fetched));

// ── 3. it reports what ACTUALLY happened ────────────────────────────────────
t('reports the endpoint\'s real count, not "1"',
  /Promoted 3 legacy connections/.test(banners[0].msg), banners[0].msg);
t('and reports success', banners[0].kind === 'success');

banners = []; fetched = []; migratedCount = 1;
await promote(mkBtn());
t('singular when one was promoted',
  /Promoted 1 legacy connection\b/.test(banners[0].msg) && !/connections/.test(banners[0].msg),
  banners[0].msg);

banners = []; fetched = []; migratedCount = 0;
await promote(mkBtn());
t('zero promoted is NOT reported as success', banners[0].kind !== 'success', banners[0].kind);
t('   and says so plainly', /Nothing to promote/.test(banners[0].msg), banners[0].msg);

// ── 4. the list refreshes, or the user sees no change and clicks again ──────
reloaded = 0; migratedCount = 2; banners = [];
await promote(mkBtn());
t('refreshes the list on success', reloaded === 1, String(reloaded));

// ── 5. failure leaves the button usable ─────────────────────────────────────
failNext = true; reloaded = 0; banners = [];
const btn = mkBtn();
await promote(btn);
t('a failed promote re-enables the button', btn.disabled === false);
t('   restores its label', btn.textContent === 'Promote', btn.textContent);
t('   does not claim the list changed', reloaded === 0);
t('   surfaces the reason', banners.length === 1 && /boom/.test(banners[0].msg), JSON.stringify(banners));
failNext = false;

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
