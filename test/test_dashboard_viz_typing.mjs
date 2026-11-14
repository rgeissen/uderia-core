#!/usr/bin/env node
// Dashboard auto-viz typing + ordering gate.
//
// No browser, no G2Plot: the Auto-Viz Intelligence block is extracted verbatim from
// components/builtin/dashboard/renderer.js and executed, so the gate runs exactly the
// code the shipped renderer runs.  The scenario that opened this gate (prod session
// 661402ae, turn 2): a Teradata DD-MON-YYYY date column ("31-OCT-2023", space-padded)
// was typed NUMERIC — parseFloat("31-OCT-2023") is 31 — and a "trend" line chart plotted
// the day-of-month digits on a 0..32 axis in lexical (DESC) row order.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const src = fs.readFileSync(path.join(ROOT, 'components/builtin/dashboard/renderer.js'), 'utf8');

const A = src.indexOf('// ── Auto-Viz Intelligence Engine');
const B = src.indexOf('/** Returns true when the UI is in light mode');
if (A < 0 || B < 0 || B <= A) { console.error('renderer.js: anchors not found'); process.exit(1); }
function extractFn(name) {
  const at = src.indexOf(`function ${name}(`);
  if (at < 0) throw new Error(`function ${name} not found`);
  let i = src.indexOf('{', at), depth = 0;
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}' && --depth === 0) return src.slice(at, i + 1);
  }
  throw new Error(`unbalanced ${name}`);
}
const block = src.slice(A, B).replace('export const _vizInternals', 'const _vizInternals');
const V = new Function(`
  const _chartTextColor = () => '#fff'; const _chartAxisColor = () => '#ccc';
  ${block}
  ${extractFn('_buildG2PlotSpec')}
  return _vizInternals;`)();

let pass = 0, fail = 0;
function check(name, cond, detail = '') {
  if (cond) { pass++; console.log(`  ✓ ${name}`); }
  else { fail++; console.log(`  ✗ ${name}${detail ? ' — ' + detail : ''}`); }
}
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

console.log('[1] column typing — dates');
const prodIncdate = ['31-OCT-2023           ', '31-OCT-2023', '30-OCT-2023', '29-SEP-2023', '28-SEP-2023'];
check('DD-MON-YYYY (space-padded CHAR) is temporal, not numeric', V.detectColType(prodIncdate) === 'temporal');
check('DD-MON-YY', V.detectColType(['31-OCT-23', '1-jan-24']) === 'temporal');
check('ISO date + datetime', V.detectColType(['2023-10-31', '2023-10-31T08:15:00Z', '2023-11-01 09:00']) === 'temporal');
check('YYYY-MM', V.detectColType(['2023-10', '2023-11']) === 'temporal');
check('YYYY/MM/DD', V.detectColType(['2023/10/31', '2023/11/1']) === 'temporal');
check('MON DD, YYYY', V.detectColType(['Oct 31, 2023', 'Nov 1 2023']) === 'temporal');
check('MON YYYY / MONTH YYYY', V.detectColType(['Oct 2023', 'November 2023', 'DEC-2023']) === 'temporal');
check('MM/YYYY', V.detectColType(['10/2023', '11/2023']) === 'temporal');
check('slash dates', V.detectColType(['10/31/2023', '11/01/2023']) === 'temporal');
check('dotted European dates', V.detectColType(['31.10.2023', '01.11.2023']) === 'temporal');
check('quarters, three spellings', V.detectColType(['2023Q4', 'Q1 2024', 'Q2-2024']) === 'temporal');
check('halves + ISO weeks', V.detectColType(['H1 2023', 'H2 2023']) === 'temporal' && V.detectColType(['2023-W44', '2023W45']) === 'temporal');
check('bare month names', V.detectColType(['January', 'Feb', 'MAR']) === 'temporal');
check('plausible years are temporal', V.detectColType(['2021', '2022', '2023']) === 'temporal');
check('4-digit COUNTS are numeric, not years', V.detectColType(['1234', '5678', '9012']) === 'numeric');
check('a date column with one stray string is categorical', V.detectColType(['31-OCT-2023', 'unknown']) === 'categorical');
check('YYYYMMDD is not a date form (a count)', V.detectColType(['20231031', '20231101']) === 'numeric');

console.log('[2] column typing — numbers');
check('whole-string numbers with currency / commas / percent / accounting / unicode minus',
  V.detectColType(['1,234.50', '$12', '(5)', '−3', '12%', '1e3', '.5']) === 'numeric');
check('a K/M suffix is display text, not a number', V.detectColType(['12K', '3M']) === 'categorical');
check('absent values never vote (None/null/NaN/blank)', V.detectColType(['None', '5', 'null', '', '  ', 'NaN', '7']) === 'numeric');
check('an all-absent column is categorical', V.detectColType(['None', 'null', '']) === 'categorical');
check('mixed text is categorical', V.detectColType(['Theft', 'Burglary']) === 'categorical');

console.log('[3] parseNum');
check('the regression: parseNum("31-OCT-2023") is 0, not 31', V.parseNum('31-OCT-2023') === 0);
check('accounting negative', V.parseNum('(1,234)') === -1234);
check('unicode minus', V.parseNum('−5') === -5);
check('currency + thousands', V.parseNum('$1,234.5') === 1234.5);
check('non-number → 0', V.parseNum('abc') === 0 && V.parseNum('') === 0);

console.log('[4] parseDateValue');
const U = (y, m, d) => Date.UTC(y, m, d);
check('31-OCT-2023', V.parseDateValue('31-OCT-2023') === U(2023, 9, 31));
check('padded 31-OCT-2023', V.parseDateValue('31-OCT-2023           ') === U(2023, 9, 31));
check('two-digit year', V.parseDateValue('31-OCT-23') === U(2023, 9, 31));
check('Oct 31, 2023', V.parseDateValue('Oct 31, 2023') === U(2023, 9, 31));
check('ISO datetime', V.parseDateValue('2023-10-31T08:15:00Z') === Date.UTC(2023, 9, 31, 8, 15, 0));
check('2023Q4 → 1 Oct', V.parseDateValue('2023Q4') === U(2023, 9, 1));
check('slash: day-first when proven by the column', V.slashOrder(['13/01/2024', '02/01/2024']) === 'DMY'
  && V.parseDateValue('13/01/2024', 'DMY') === U(2024, 0, 13));
check('slash: month-first by default', V.slashOrder(['01/02/2024']) === 'MDY'
  && V.parseDateValue('01/02/2024', 'MDY') === U(2024, 0, 2));
check('unparseable → NaN', Number.isNaN(V.parseDateValue('yesterday')));

console.log('[5] chronological ordering');
const rowsDesc = [
  { INCDATE: '31-OCT-2023', n: 5 }, { INCDATE: '31-JAN-2023', n: 2 }, { INCDATE: '30-SEP-2023', n: 4 },
  { INCDATE: '28-FEB-2023', n: 3 }, { INCDATE: '01-JAN-2023', n: 1 },
];
const sorted = V.sortChronological(rowsDesc.map(r => ({ ...r })), 'INCDATE');
check('lexical-DESC rows come out in date order', eq(sorted.map(r => r.n), [1, 2, 3, 4, 5]));
const withBad = V.sortChronological([{ d: 'x', n: 9 }, { d: '02-JAN-2023', n: 2 }, { d: 'y', n: 8 }, { d: '01-JAN-2023', n: 1 }], 'd');
check('unparseable values go last, in their original order', eq(withBad.map(r => r.n), [1, 2, 9, 8]));
const ties = V.sortChronological([{ d: '01-JAN-2023', n: 1 }, { d: '01-JAN-2023', n: 2 }], 'd');
check('the sort is stable on equal dates', eq(ties.map(r => r.n), [1, 2]));

console.log('[6] the prod turn, end to end through _buildG2PlotSpec');
const cols = ['INCDATE', 'INCIDENT_COUNT'];
const rows = [['31-OCT-2023           ', '3'], ['31-AUG-2023           ', '12'], ['30-SEP-2023           ', '9'], ['28-SEP-2023           ', '7']];
const types = cols.map((_, i) => V.detectColType(rows.map(r => r[i])));
check('types = [temporal, numeric]', eq(types, ['temporal', 'numeric']));
check('auto-selects a Line for time + measure', V.selectChartType(cols, types, rows, null) === 'Line');
const spec = V.buildG2PlotSpec('Line', cols, types, rows);
check('the line runs on the date column', spec.type === 'Line' && spec.options.xField === 'INCDATE' && spec.options.yField === 'INCIDENT_COUNT');
check('points are chronological, padding stripped in the parse', eq(spec.options.data.map(d => d.INCIDENT_COUNT), [12, 7, 9, 3]));
check('the measure is a number, not "3"', typeof spec.options.data[0].INCIDENT_COUNT === 'number');
const column = V.buildG2PlotSpec('Column', ['Offense', 'n'], ['categorical', 'numeric'], [['A', '1'], ['B', '3']]);
check('categorical charts still sort by value descending', eq(column.options.data.map(d => d.n), [3, 1]));
check('a line on a categorical x keeps row order (no date sort)',
  eq(V.buildG2PlotSpec('Line', ['k', 'n'], ['categorical', 'numeric'], [['b', '2'], ['a', '1']]).options.data.map(d => d.n), [2, 1]));

console.log('[7] chart-type hints are word-bounded');
check('"airline" is not a line chart request', V.extractVizHint('revenue by airline') === null);
check('"timeline" / "deadline" are not either', V.extractVizHint('project timeline') === null && V.extractVizHint('the deadline') === null);
check('a real trend request still hints Line', V.extractVizHint('incident trends over the last year') === 'Line');
check('an explicit chart_type still hints', V.extractVizHint('line') === 'Line' && V.extractVizHint('pie') === 'Pie' && V.extractVizHint('bar') === 'Bar');
check('single row + measure → KPI', V.selectChartType(['a', 'n'], ['categorical', 'numeric'], [['x', '1']], null) === 'KPI');


console.log('[7] the measure among several numeric columns (session 42eb166c turn 2)');
{
  const cols = ['month_number', 'incident_month', 'robbery_count'];
  const rows = [['1','JAN','14573'],['2','FEB','12512'],['3','MAR','13615'],['4','APR','13854'],['5','MAY','15490'],['6','JUN','15246'],
                ['7','JUL','16569'],['8','AUG','15944'],['9','SEP','15433'],['10','OCT','15699'],['11','NOV','14981'],['12','DEC','15043']];
  const types = cols.map((_, i) => V.detectColType(rows.map(r => r[i])));
  check('the prod shape types as numeric · temporal · numeric', eq(types, ['numeric', 'temporal', 'numeric']), JSON.stringify(types));
  check('the measure is robbery_count, never the month_number ordinal', V.measureIndex(cols, types, rows) === 2);
  const spec = V.buildG2PlotSpec(V.selectChartType(cols, types, rows, null), cols, types, rows);
  check('…and the line plots robbery_count over incident_month', spec.options.yField === 'robbery_count' && spec.options.xField === 'incident_month', JSON.stringify({ x: spec.options.xField, y: spec.options.yField }));
  check('a consecutive integer run is key-like whatever its name (a rank column called "pos2")',
    V.measureIndex(['pos2', 'region', 'sales'], ['numeric', 'categorical', 'numeric'], [['1','A','9'],['2','B','3'],['3','C','5']]) === 2);
  check('an id column is skipped by its name even when not consecutive',
    V.measureIndex(['customer_id', 'name', 'revenue'], ['numeric', 'categorical', 'numeric'], [['17','A','9'],['4','B','3'],['99','C','5']]) === 2);
  check('a measure listed FIRST still wins (key-likeness, not position, decides)',
    V.measureIndex(['revenue', 'year'], ['numeric', 'temporal'], [['9','2021'],['3','2022']]) === 0);
  check('every numeric key-like → the last one (a SELECT lists its measure last)',
    V.measureIndex(['id', 'rank'], ['numeric', 'numeric'], [['1','1'],['2','2'],['3','3']]) === 1);
  check('no numeric column → -1', V.measureIndex(['a'], ['categorical'], [['x']]) === -1);
  check('a single value column that merely counts up is still the measure when it is the only numeric', V.measureIndex(['label', 'n'], ['categorical', 'numeric'], [['a','1'],['b','2'],['c','3']]) === 1);
}
{
  const src2 = src;
  check('no chart/KPI path picks "the first numeric column" any more — every one asks _measureIndex', !/findIndex\(t => t === 'numeric'\)/.test(src2) && (src2.match(/_measureIndex\(columns, types, rows\)/g) || []).length >= 4);
}
console.log(`\n${fail === 0 ? 'ALL PASS' : 'FAILURES'} — ${pass + fail} assertions (${pass} passed, ${fail} failed)`);
process.exit(fail ? 1 : 0);
