#!/usr/bin/env node
/**
 * The token KPI display: a partial update never wipes what it does not carry.
 *
 * WHY THIS GATE EXISTS
 * Operator screenshot after a 13k-token turn: LAST STMT 242/4 · LAST TURN 0/0 ·
 * SESSION TOTAL 0/0. The chain: session-name generation runs LAST; its
 * `session_name_generation_complete` handler maps STATEMENT fields only into
 * updateTokenDisplay; and the "normal update" branch wrote all six fields with
 * `|| 0` — so the statement-only event zeroed TURN and SESSION TOTAL as the
 * final state of the screen. The contract now: absent means LEAVE ALONE; an
 * event that wants to reset a field sends an explicit 0.
 *
 * This gate EXECUTES the shipped function under a DOM stub — a grep passes the
 * mutation that restores the `|| 0` writes, which is the exact defect.
 *
 * Run: node test/test_token_display_contract.mjs      (no server, no network)
 */
import { readFileSync } from 'fs';

const ui = readFileSync('static/js/ui.js', 'utf8');
let pass = 0, fail = 0;
const check = (name, cond, detail) => {
    if (cond) { pass++; }
    else { fail++; console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ''}`); }
};

/* ------------------------------------------------------------ extract + run */
function sliceFn(marker) {
    const i = ui.indexOf(marker);
    if (i < 0) return null;
    let depth = 0, j = ui.indexOf('{', i);
    for (let k = j; k < ui.length; k++) {
        if (ui[k] === '{') depth++;
        else if (ui[k] === '}') { depth--; if (depth === 0) return ui.slice(i, k + 1); }
    }
    return null;
}
const src = sliceFn('export function updateTokenDisplay(data, isHistorical = false)');
check('the display function is extractable', !!src);
if (!src) { console.log('\nFAIL — function missing'); process.exit(1); }

// DOM stub: the elements the function touches, tracked by id
function freshDom() {
    const els = {};
    const ids = ['token-normal-display', 'token-aws-message', 'turn-token-label',
                 'statement-input-tokens', 'statement-output-tokens',
                 'turn-input-tokens', 'turn-output-tokens',
                 'total-input-tokens', 'total-output-tokens'];
    for (const id of ids) {
        els[id] = { textContent: '', classList: { add() {}, remove() {} } };
    }
    return {
        getElementById: (id) => els[id] || null,
        _els: els,
    };
}

function run(dom, data, isHistorical = false) {
    const state = { currentProvider: 'Google', currentModel: 'gemini-3.1-flash-lite' };
    const console_ = { log() {} };
    const win = { sessionCostAccumulator: 0, turnCostAccumulator: 0 };
    // collaborators the function calls that are irrelevant to the token-field
    // contract under test (cost tooltips / dual-model chips) — stubbed inert,
    // NOT the fields themselves: those come from the real extracted function
    const stubs = { _updateDualModelTooltips: () => {}, updateCostDisplay: () => {},
                    _refreshCostTooltip: () => {}, _recomputeLiveCost: () => {},
                    _setCostDisplays: () => {}, formatCost: (v) => String(v) };
    const fn = new Function('document', 'state', 'console', 'window',
        ...Object.keys(stubs),
        `${src.replace('export function', 'function')}; return updateTokenDisplay;`)(
        dom, state, console_, win, ...Object.values(stubs));
    fn(data, isHistorical);
}

/* [1] a FULL update sets all six */
{
    const dom = freshDom();
    run(dom, { statement_input: 10595, statement_output: 324,
               turn_input: 10595, turn_output: 324,
               total_input: 10595, total_output: 324 });
    check('a full update sets statement', dom._els['statement-input-tokens'].textContent === '10,595');
    check('a full update sets turn', dom._els['turn-input-tokens'].textContent === '10,595');
    check('a full update sets total', dom._els['total-input-tokens'].textContent === '10,595');
}

/* [2] THE SCREENSHOT: a statement-only update must PRESERVE turn/total */
{
    const dom = freshDom();
    run(dom, { statement_input: 10595, statement_output: 324,
               turn_input: 13313, turn_output: 393,
               total_input: 13313, total_output: 393 });
    // the session-name mapping: statement fields + cost only
    run(dom, { statement_input: 242, statement_output: 4, cost_usd: 0.0000258 });
    check('statement updates to the name call', dom._els['statement-input-tokens'].textContent === '242');
    check('LAST TURN is PRESERVED, not wiped to 0',
          dom._els['turn-input-tokens'].textContent === '13,313',
          `got '${dom._els['turn-input-tokens'].textContent}' — the operator's exact screenshot`);
    check('SESSION TOTAL is PRESERVED, not wiped to 0',
          dom._els['total-input-tokens'].textContent === '13,313');
}

/* [3] an explicit 0 still resets (absent ≠ zero) */
{
    const dom = freshDom();
    run(dom, { statement_input: 100, statement_output: 10,
               turn_input: 100, turn_output: 10, total_input: 100, total_output: 10 });
    run(dom, { statement_input: 50, statement_output: 5,
               turn_input: 0, turn_output: 0, total_input: 100, total_output: 10 });
    check('an explicit turn=0 IS written (absent ≠ zero)',
          dom._els['turn-input-tokens'].textContent === '0',
          'an event that wants to reset sends an explicit 0 — that path must survive');
}

/* [4] the all-undefined cost-only skip path is intact */
{
    const dom = freshDom();
    run(dom, { statement_input: 7, statement_output: 3,
               turn_input: 7, turn_output: 3, total_input: 7, total_output: 3 });
    run(dom, { cost_usd: 0.001 });
    check('a cost-only event changes no token field',
          dom._els['statement-input-tokens'].textContent === '7'
          && dom._els['turn-input-tokens'].textContent === '7');
}

/* [5] the terminal-event zero-guard is intact (statement absent, turn present) */
{
    const dom = freshDom();
    run(dom, { statement_input: 9, statement_output: 2,
               turn_input: 9, turn_output: 2, total_input: 9, total_output: 2 });
    run(dom, { turn_input: 20, turn_output: 4, total_input: 20, total_output: 4 });
    check('a terminal event updates turn/total', dom._els['turn-input-tokens'].textContent === '20');
    check('a terminal event preserves the statement', dom._els['statement-input-tokens'].textContent === '9');
}

console.log(fail
    ? `\nFAIL — ${fail} of ${pass + fail} assertions failed.`
    : `\nALL PASS — ${pass} assertions (a partial update never wipes the display).`);
process.exit(fail ? 1 : 0);
