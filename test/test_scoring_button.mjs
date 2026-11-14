// The scorecard's ONE scoring button (Oct 2026).
//
// Every answer is already scored by code after delivery, so the act worth offering on a
// scored answer is the AI judges' review. Before, a separate judges switch did nothing
// until a second "Score" button was pressed — two controls for one act. Now one button
// whose state decides the act:
//   unscored → Score this answer      (code checks, no tokens)
//   scored   → Ask the AI judges      (uses tokens)
//   judged   → Ask the AI judges again
// This gate executes the SHIPPED state table and checks the wiring around it.
import { readFileSync } from 'node:fs';

let pass = 0, fail = 0;
const check = (name, cond, detail = '') => {
    if (cond) { pass++; } else { fail++; console.log(`  FAIL  ${name}${detail ? ' — ' + detail : ''}`); }
};
const src = readFileSync(new URL('../static/js/ui.js', import.meta.url), 'utf8');

// 1. One button, no separate judges switch.
check('no separate judges switch remains', !src.includes('trajectory-eval-judges') && !src.includes('judgesBtn'));
check('the scoring button exists once', (src.match(/id="trajectory-eval-score"/g) || []).length === 1);

// 2. The shipped state table, executed.
const start = src.indexOf('const _SCORE_STATES = {');
const end = src.indexOf('};', start);
check('the state table is present', start > 0 && end > start);
const table = start > 0 ? Function(`return ${src.slice(start + 'const _SCORE_STATES = '.length, end + 1)};`)() : {};
check('three states: unscored, scored, judged', JSON.stringify(Object.keys(table)) === '["unscored","scored","judged"]', Object.keys(table).join(','));
check('an unscored answer is scored by code only — never charged tokens', table.unscored?.judges === false);
check('…and its tooltip says no tokens', /no tokens/.test(table.unscored?.tip || ''), table.unscored?.tip);
check('a scored answer offers the judges', table.scored?.judges === true && /judges/i.test(table.scored?.label || ''));
check('a judged answer offers them again', table.judged?.judges === true && /again/.test(table.judged?.label || ''));
for (const k of ['scored', 'judged'])
    check(`${k}: the tooltip names the cost`, /uses tokens/.test(table[k]?.tip || ''), table[k]?.tip);
check('the icon follows the act: target to score, scales to judge',
      table.unscored?.icon === 'target' && table.scored?.icon === 'scales' && table.judged?.icon === 'scales');
for (const [k, st] of Object.entries(table))
    check(`${k}: no station jargon in the words a user reads`, !/GROUNDED|GUARDED|JUDGED/.test(`${st.label} ${st.tip}`));

// 3. Wiring: the click asks the endpoint for judges exactly when the state says so;
//    the stored score decides the starting state; the label is set for screen readers.
check('the click reads the mode, not a switch', /const judges = _SCORE_STATES\[_scoreMode\]\.judges;/.test(src));
check('…and sends it as include_judges', /include_judges: judges/.test(src));
check('a stored score with judge verdicts starts as judged, otherwise scored',
      /_scoreState\(\(d\.has_judges \|\| _hasJudgeVerdicts\(verdicts\)\) \? 'judged' : 'scored'\)/.test(src));
check('after scoring, judges that could not be reached leave it scored, not judged',
      /_scoreState\(_hasJudgeVerdicts\(data\.verdicts\) \? 'judged' : 'scored'\)/.test(src));
check('a failed scoring restores the previous state', /_scoreState\(priorMode\)/.test(src));
check('the accessible label follows the state', /btn\.setAttribute\('aria-label', st\.label\)/.test(src));

console.log(fail ? `RESULT: FAILURES  (${pass} passed, ${fail} failed)` : `ALL PASS — ${pass} assertions (one scoring button, its act decided by the answer's state).`);
process.exit(fail ? 1 : 0);
