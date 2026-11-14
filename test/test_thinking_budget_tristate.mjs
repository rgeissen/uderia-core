/**
 * The thinking budget is a TRI-STATE: not set · disabled · dynamic.
 *
 * THE DEFECT: the field rendered `?? 0` and saved `parseInt(value) || 0`, so a
 * field nobody touched stamped `thinking_budget: 0` on EVERY save — silently
 * disabling thinking for every Google model — and the UI could not express "not
 * set" at all, which is the state the backend already models (`is not None`).
 *
 * THE TRAP this gate exists for: `update_llm_configuration` shallow-merges
 * (`configuration.update(updates)`), so OMITTING the key on a save leaves a
 * previously-stored 0 in place and clearing the field silently does nothing.
 * The cleared state must therefore go over the wire as an explicit `null`.
 *
 * Executes the SHIPPED populate expression and the SHIPPED save block — no copy
 * of either, so a change to the real code fails here rather than drifting.
 *
 * Run: node test/test_thinking_budget_tristate.mjs
 */
import fs from 'fs';

let pass = 0;
const failures = [];
const check = (name, got, want) => {
    const g = JSON.stringify(got), w = JSON.stringify(want);
    if (g === w) { pass++; return; }
    failures.push(`${name}\n      got=${g} want=${w}`);
};

const SRC = fs.readFileSync(new URL('../static/js/handlers/configurationHandler.js', import.meta.url), 'utf8');

// ── 1 · populate: extract the shipped `value="..."` expression and run it ────
const valueExpr = SRC.match(/id="llm-modal-thinking-budget" value="\$\{([^}]*)\}"/);
check('the populate expression is present', !!valueExpr, true);
const populate = new Function('config', `return (${valueExpr[1]});`);

check('a config that never set it renders EMPTY (was 0)', populate({}), '');
check('an explicit null renders EMPTY, so a cleared value round-trips', populate({ thinking_budget: null }), '');
check('a stored 0 still renders 0', populate({ thinking_budget: 0 }), 0);
check('a stored -1 still renders -1', populate({ thinking_budget: -1 }), -1);
check('a stored 1024 still renders 1024', populate({ thinking_budget: 1024 }), 1024);
check('no config at all renders EMPTY', populate(undefined), '');

// the placeholder tells the user what empty MEANS
check('empty is labelled, not blank', /placeholder="model default"/.test(SRC), true);
check('the hint explains the empty state', /Leave empty for the model's own default/.test(SRC), true);

// ── 2 · save: extract the shipped Google block and run it ───────────────────
const saveBlock = SRC.match(/if \(provider === 'Google'\) \{([\s\S]*?)\n        \}/);
check('the save block is present', !!saveBlock, true);
const save = new Function('provider', 'modal', 'configData', `
    ${saveBlock[0]}
    return configData;
`);
const modalWith = (v) => ({ querySelector: () => (v === undefined ? null : { value: v }) });

// A raising save must report a NAMED failure, never abort the run — a gate that
// dies without saying what broke is half a gate.
const saved = (v) => {
    try { return save('Google', modalWith(v), {}); }
    catch (e) { return { threw: `${e.name}: ${e.message}` }; }
};
check('an untouched (empty) field saves null, NOT 0', saved(''), { thinking_budget: null });
check('whitespace only is still "not set"', saved('   '), { thinking_budget: null });
check('an explicit 0 is honoured', saved('0'), { thinking_budget: 0 });
check('-1 is honoured', saved('-1'), { thinking_budget: -1 });
check('1024 is honoured', saved('1024'), { thinking_budget: 1024 });
check('a non-numeric entry falls back to "not set", never 0', saved('abc'), { thinking_budget: null });
check('a missing input element is "not set"', saved(undefined), { thinking_budget: null });
let nonGoogle;
try { nonGoogle = save('OpenAI', modalWith('0'), {}); } catch (e) { nonGoogle = { threw: e.message }; }
check('a non-Google provider writes nothing', nonGoogle, {});

// ── 3 · the merge trap: the cleared state MUST be an explicit null ──────────
check('the cleared value is sent, never omitted (update_llm_configuration merges)',
      Object.prototype.hasOwnProperty.call(saved(''), 'thinking_budget'), true);
check('the retired `|| 0` stamp is gone', /parseInt\(thinkingInput\.value\) \|\| 0/.test(SRC), false);

// ── 4 · round-trip: what is saved renders back as what was chosen ───────────
for (const entered of ['', '0', '-1', '1024']) {
    const stored = saved(entered).thinking_budget;
    const shown = populate({ thinking_budget: stored });
    check(`round-trip ${JSON.stringify(entered)} -> stored ${JSON.stringify(stored)} -> shown`,
          String(shown), entered.trim());
}

if (failures.length) {
    console.log(`\n  ${failures.length} FAILED:`);
    for (const f of failures) console.log('    FAIL: ' + f);
    console.log(`\n${pass} passed, ${failures.length} failed`);
    process.exit(1);
}
console.log(`ALL PASS — ${pass} assertions (thinking budget: not set / disabled / dynamic).`);
