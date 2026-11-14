/**
 * PROP-006 phase 1 — the combine's UX surfaces, EXECUTED (no grep of the renderer):
 *
 *   1. the lineage dialog (figureLineage.js, loaded whole under the DOM stub): a source line
 *      and the combine's inputs (the second hop) render ONLY when the turn's record says
 *      `multi_source`; a single-source figure's dialog is unchanged;
 *   2. the Number Lineage card (ui.js `_computeSourcesRows`, extracted): no rows without
 *      `sources`; one row per source and per combine with names and times;
 *   3. the reload title (eventHandlers.js `_assuranceStepTitle`, extracted) agrees with the
 *      backend `compute_lineage.step_title` rule, which the Python gate pins.
 *
 * No server, no network. Run: node test/test_combine_ux.mjs
 */
import { readFileSync } from 'node:fs';
import { installDom } from './dom_stub.mjs';

let passed = 0, failed = 0;
function check(name, cond, detail) {
    if (cond) { passed++; } else { failed++; console.log(`  FAIL: ${name}${detail !== undefined ? ' — ' + JSON.stringify(detail).slice(0, 300) : ''}`); }
}
function extract(src, header) {
    const i = src.indexOf(header);
    if (i < 0) throw new Error('not found: ' + header);
    let depth = 0, j = src.indexOf('{', i);
    for (let k = j; k < src.length; k++) {
        if (src[k] === '{') depth++;
        else if (src[k] === '}') { depth--; if (depth === 0) return src.slice(i, k + 1); }
    }
    throw new Error('unbalanced: ' + header);
}
const escapeHtml = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// ── fixtures: a two-source turn with a combine, and a single-source turn ──
const MULTI = {
    multi_source: true,
    statements: [
        { n: 1, tool: 'query', statement: 'SELECT id, dept FROM hr.emp', row_count: 40, source_id: 'conn:hr', source_name: 'HR warehouse', ts: '2026-09-28T10:00:00+00:00' },
        { n: 2, tool: 'base_readQuery', statement: 'SELECT emp_id, deals FROM crm.x', row_count: 25, source_id: 'mcp:crm', source_name: 'CRM server', ts: '2026-09-28T10:00:05+00:00' },
        { n: 3, tool: 'compute_sql', statement: 'SELECT s1.dept, SUM(s2.deals) FROM s1 JOIN s2 ON s1.id = s2.emp_id GROUP BY 1', row_count: 4, source_id: 'combine', inputs: [1, 2], origin_sources: ['conn:hr', 'mcp:crm'], ts: '2026-09-28T10:00:06+00:00',
          sample_rows: [{ dept: 'Sales', deals: 12 }] },
    ],
    receipts: [{ op: 'sql', sql: 'SELECT s1.dept, SUM(s2.deals) FROM s1 JOIN s2 ON s1.id = s2.emp_id GROUP BY 1', statements: [1, 2], row_count: 65, result_rows: 4, truncated: false, result_statement: 3, normalizations: [{ left: 's1.id', right: 's2.emp_id', applied: 'trimmed' }], receipt_hash: 'abc123' }],
};
const SINGLE = {
    statements: [{ n: 1, tool: 'base_readQuery', statement: 'SELECT 1', row_count: 3, source_id: 'mcp:only', ts: '2026-09-28T10:00:00+00:00' }],
    receipts: [],
};

// ── 1. the lineage dialog, loaded whole ──
console.log('[1] lineage dialog: source line + second hop, only on a multi-source turn');
installDom();
// eslint-disable-next-line no-new-func
new Function(readFileSync('static/js/handlers/figureLineage.js', 'utf8'))();
const FL = globalThis.window.FigureLineage;
check('the dialog exposes its source/inputs/receipt renderers', !!(FL && FL._sourceLine && FL._inputsHtml && FL._receiptRows));

const s3 = MULTI.statements[2], s1 = MULTI.statements[0];
const line1 = FL._sourceLine(s1, MULTI);
check('a statement names its source by NAME with its read time', line1.includes('HR warehouse') && line1.includes('read'), line1);
check('a combined statement says it was combined by code', FL._sourceLine(s3, MULTI).includes('combined by code'));
const hop = FL._inputsHtml(s3, MULTI);
check('the second hop lists every input with its source', hop.includes('statement 1') && hop.includes('HR warehouse') && hop.includes('statement 2') && hop.includes('CRM server'), hop);
check('the second hop carries each input\'s own statement text', hop.includes('SELECT id, dept FROM hr.emp'));
check('an input not on the record is SAID, never dropped', FL._inputsHtml({ inputs: [1, 99] }, MULTI).includes("not on this turn's record"));
check('a statement with no inputs has no second hop', FL._inputsHtml(s1, MULTI) === '');

check('single-source: no source line (the dialog is unchanged)', FL._sourceLine(SINGLE.statements[0], SINGLE) === '');
check('single-source: no second hop', FL._inputsHtml(SINGLE.statements[0], SINGLE) === '');

const rr = FL._receiptRows(MULTI.receipts[0]);
check('a combine receipt reads as a COMBINE, not an aggregate', rr.includes('COMBINE') && !rr.includes('SQL(') , rr);
check('a combine receipt names rows read, rows produced and the key normalisation', rr.includes('Rows read') && rr.includes('Rows produced') && rr.includes('trimmed'));
check('a combine receipt shows the SQL that ran', rr.includes('fl-sql') && rr.includes('JOIN s2'));
check('a combine receipt escapes its SQL', FL._receiptRows({ op: 'sql', sql: '<b>x</b>', statements: [] }).includes('&lt;b&gt;'));
const agg = FL._receiptRows({ op: 'sum', column: 'miles', row_count: 3, statements: [1], value: 6 });
check('an aggregate receipt renders exactly as before (no COMBINE, no SQL block)', agg.includes('SUM(miles)') && !agg.includes('fl-sql') && !agg.includes('COMBINE'));

// ── 2. the Number Lineage card rows ──
console.log('[2] Number Lineage card: sources + combined results rows');
const uiSrc = readFileSync('static/js/ui.js', 'utf8');
const _trustVal = () => 'style=""';
// eslint-disable-next-line no-new-func
const rowsFn = new Function('escapeHtml', '_trustVal', extract(uiSrc, 'function _computeSourcesRows(src)') + '\nreturn _computeSourcesRows;')(escapeHtml, _trustVal);
check('no sources block → no rows (a single-source card is unchanged)', rowsFn(undefined) === '' && rowsFn(null) === '');
const SRC = {
    sources: [{ source_id: 'conn:hr', name: 'HR warehouse', statements: [1], rows: 40 }, { source_id: 'mcp:crm', name: 'CRM server', statements: [2], rows: 25 }],
    combines: [{ n: 3, row_count: 4, inputs: [{ n: 1, source_name: 'HR warehouse', ts: '2026-09-28T10:00:00+00:00' }, { n: 2, source_name: 'CRM server', ts: '2026-09-28T10:00:05+00:00' }] }],
};
const html = rowsFn(SRC);
check('one Source row per source, named, with its statements and rows', (html.match(/>Source</g) || []).length === 2 && html.includes('HR warehouse') && html.includes('statement 1') && html.includes('40 rows'));
check('a Combined results row names each input and its source', html.includes('Combined results') && html.includes('statement 1 (HR warehouse') && html.includes('statement 2 (CRM server'));
check('the combine row says code joined it, not the model', html.includes('joined by code, not by the model'));
check('an unnamed source falls back to its id, never empty', rowsFn({ sources: [{ source_id: 'mcp:gone', statements: [1], rows: 1 }] }).includes('mcp:gone'));
check('the card escapes source names', rowsFn({ sources: [{ name: '<i>x</i>', statements: [], rows: 0 }] }).includes('&lt;i&gt;'));

// ── 3. the reload title mirrors the backend rule ──
console.log('[3] reload title mirrors compute_lineage.step_title');
const ehSrc = readFileSync('static/js/eventHandlers.js', 'utf8');
// eslint-disable-next-line no-new-func
const title = new Function(extract(ehSrc, 'function _assuranceStepTitle(eventType, payload)') + '\nreturn _assuranceStepTitle;')();
check('no sources → "Number Lineage" (unchanged)', title('compute_lineage', { details: {} }) === 'Number Lineage');
check('a combine → "Number Lineage — Combined Results"', title('compute_lineage', { details: { sources: SRC } }) === 'Number Lineage — Combined Results');
check('several sources, no combine → "Number Lineage — Several Sources"', title('compute_lineage', { details: { sources: { sources: SRC.sources, combines: [] } } }) === 'Number Lineage — Several Sources');
check('a bare payload (reload shape) reads the same', title('compute_lineage', { sources: SRC }) === 'Number Lineage — Combined Results');
// the Python side of the rule, read so the two cannot drift silently
const pySrc = readFileSync('src/trusted_data_agent/agent/compute_lineage.py', 'utf8');
for (const t of ['Number Lineage — Combined Results', 'Number Lineage — Several Sources']) {
    check(`the backend rule emits the same title "${t}"`, pySrc.includes(`"${t}"`));
}

// ── 4. theme: every element these surfaces introduce uses theme variables only ──
console.log('[4] theme-aware: no Tailwind colour class, no bare colour outside a var() fallback');
const TAILWIND_COLOR = /\b(?:text|bg|border|ring|from|to|via)-(?:gray|slate|zinc|neutral|stone|red|rose|amber|yellow|emerald|green|teal|cyan|sky|blue|indigo|violet|purple|pink|orange|lime|fuchsia)-\d{2,3}\b/;
const bareColor = (txt) => txt.replace(/var\([^)]*\)/g, '').match(/#[0-9a-fA-F]{3,8}\b|rgba?\(/);
const flSrc = readFileSync('static/js/handlers/figureLineage.js', 'utf8');
const pieces = {
    'figureLineage sourceLine/inputsHtml/combine receipt': extract(flSrc, 'function sourceLine(st, compute)') + extract(flSrc, 'function inputsHtml(st, compute)') + extract(flSrc, 'function receiptRows(rc)'),
    'ui.js _computeSourcesRows': extract(uiSrc, 'function _computeSourcesRows(src)'),
};
for (const [name, code] of Object.entries(pieces)) {
    check(`${name}: no Tailwind colour class`, !TAILWIND_COLOR.test(code), (code.match(TAILWIND_COLOR) || [])[0]);
    check(`${name}: no bare colour`, !bareColor(code), bareColor(code));
}
const css = readFileSync('static/css/trust.css', 'utf8');
const flCss = css.slice(css.indexOf('/* PROP-006'), css.indexOf('/* ── Chunk-browser'));
check('the dialog\'s new CSS rules exist', flCss.includes('.fl-source') && flCss.includes('.fl-inputs'));
check('the dialog\'s new CSS uses theme variables only', !bareColor(flCss), bareColor(flCss));

console.log(`\n${failed === 0 ? 'ALL PASS' : 'FAILURES'} — ${passed} passed, ${failed} failed (${passed + failed} assertions) — combine UX surfaces executed`);
process.exit(failed === 0 ? 0 : 1);
