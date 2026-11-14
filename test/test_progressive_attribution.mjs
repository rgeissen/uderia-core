#!/usr/bin/env node
/**
 * Progressive-disclosure cost attribution — the gate.
 *
 * A library fetch is a TOOL call, so its cost does not land on the fetch: it lands on
 * the NEXT LLM call, whose prompt now carries the section. The trace showed input
 * jumping 6,397 -> 11,006 with nothing saying why. This asserts the attribution that
 * closes that, and — the operator's imperative — that EVERY code path is covered.
 *
 * The attribution is DERIVED by replaying the ordered event list, not plumbed through
 * the backend. Two reasons, both asserted here:
 *   * live and reload replay the SAME list through the SAME renderers, so it stands a
 *     plan reload by construction rather than via a second persistence path;
 *   * a contextvar written by the component and drained by the emitter would be
 *     invisible across sibling tasks when Optimize runs phases in parallel — the
 *     hazard the governed-SQL registry already hit.
 *
 * No server, no browser: the shipped helpers are extracted and executed, and §D
 * replays a REAL persisted turn from tda_sessions when one is present.
 */

import fs from 'fs';
import path from 'path';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const UI = fs.readFileSync(path.join(ROOT, 'static/js/ui.js'), 'utf8');

let passed = 0;
const failures = [];
function check(name, cond, detail = '') {
    if (cond) { passed++; console.log(`  ok  ${name}`); }
    else { failures.push(name); console.log(`  FAIL  ${name}${detail ? ' — ' + detail : ''}`); }
}

/** Extract a top-level function body by brace matching (never a slice by distance). */
function grab(src, name) {
    // match the FULL name — `function _pdTagHtml` also prefixes `_pdTagHtmlGrid`
    const i = src.indexOf(`function ${name}(`);
    if (i === -1) return null;
    let depth = 0;
    for (let k = src.indexOf('{', i); k < src.length; k++) {
        if (src[k] === '{') depth++;
        else if (src[k] === '}') { depth--; if (!depth) return src.slice(i, k + 1); }
    }
    return null;
}

// ── §A. The helpers exist and are executable ────────────────────────────────
console.log('\n[A] the derivation');
const parts = ['_pdReset', '_pdNoteSection', '_pdTagHtml', '_pdTagHtmlGrid'].map(n => grab(UI, n));
check('all four helpers present', parts.every(Boolean),
      parts.map((p, i) => p ? '' : ['_pdReset','_pdNoteSection','_pdTagHtml','_pdTagHtmlGrid'][i]).filter(Boolean).join(','));

// NO injected helpers. The first version of this gate supplied `esc`, which ui.js does
// NOT have at module scope — so the suite passed while the shipped renderer threw
// "ReferenceError: esc is not defined" on every reload of a turn with a section load.
// A stub that is more generous than reality measures nothing. The module's OWN
// escapeHtml is extracted alongside the helpers instead.
const sandbox = {};
// eslint-disable-next-line no-new-func
new Function(`var _pdPendingSections=[];${grab(UI, 'escapeHtml').replace('export ', '')}
    ${parts.join('\n')}
    this._pdReset=_pdReset; this._pdNoteSection=_pdNoteSection;
    this._pdTagHtml=_pdTagHtml; this._pdTagHtmlGrid=_pdTagHtmlGrid;`).call(sandbox);
const { _pdReset, _pdNoteSection, _pdTagHtml, _pdTagHtmlGrid } = sandbox;

// A helper that throws must be reported BY NAME, not kill the run: a gate that dies
// without saying what broke is half a gate (the mutation that shipped `esc` did exactly
// this — it aborted the suite instead of failing §A2).
function guard(name, fn, fallback = '') {
    try { return fn(); }
    catch (e) { failures.push(`${name} threw`); console.log(`  FAIL  ${name} threw — ${e.message}`); return fallback; }
}

_pdReset();
check('no sections -> no tag (a plain LLM call is untouched)',
      guard('empty tag', () => _pdTagHtml()) === '');

_pdNoteSection({ anchor: 'master-design', chars: 20928 });
const tag1 = guard('tag after one section', () => _pdTagHtml());
check('a loaded section tags the next call', tag1.includes('master-design'));
check('the tag states the weight in tokens', /5,232|5,232 tokens/.test(tag1), tag1.replace(/<[^>]+>/g, ' ').trim());
check('the tag DRAINS — a section is attributed once, not to every later call',
      guard('drain', () => _pdTagHtml()) === '');

_pdNoteSection({ anchor: 'a', chars: 4000 });
_pdNoteSection({ anchor: 'b', chars: 4000 });
const tag2 = guard('tag after two sections', () => _pdTagHtml());
check('two sections in one gap are attributed together', tag2.includes('a') && tag2.includes('b'));
check('and their weights sum', tag2.includes('2,000'), tag2.replace(/<[^>]+>/g, ' ').trim());

_pdNoteSection({ anchor: 'x', chars: 100 });
_pdReset();
check('reset clears pending (no cross-turn leakage)', _pdTagHtml() === '');
check('a malformed section does not throw or invent a weight',
      (() => { try { _pdNoteSection({}); const t = _pdTagHtml(); return t.includes('0 tokens') || t.includes('~0'); }
               catch { return false; } })());


// ── §A2. The helpers must not reach for undefined identifiers ───────────────
console.log('\n[A2] no undefined dependency (the ReferenceError class)');
const MODULE_SCOPE_FNS = new Set(
    [...UI.matchAll(/^(?:export )?function ([a-zA-Z_$][\w$]*)\(/gm)].map(m => m[1]));
const calledByHelpers = new Set();
for (const body of parts) {
    // strip template-literal content first: `color: var(--x)` is CSS, not a call
    const code = body.replace(/`[\s\S]*?`/g, '``');
    for (const m of code.matchAll(/(?:^|[^.\w$])([a-zA-Z_$][\w$]*)\s*\(/g)) calledByHelpers.add(m[1]);
}
const BUILTINS = new Set(['if','for','while','switch','catch','return','function','Number','String',
                          'Math','Object','Array','JSON','parseInt','parseFloat','typeof','reduce','map','join']);
const undefinedDeps = [...calledByHelpers].filter(n =>
    !BUILTINS.has(n) && !MODULE_SCOPE_FNS.has(n) && !n.startsWith('_pd'));
check('every function a helper calls exists at ui.js module scope',
      undefinedDeps.length === 0, undefinedDeps.join(', '));
check('escaping uses the module\'s own escapeHtml',
      parts.join('').includes('escapeHtml'));
const tagEsc = _pdTagHtml.toString();
check('the tag escapes its anchor', tagEsc.includes('escapeHtml'));

// ── §B. Every RENDER path is wired ──────────────────────────────────────────
console.log('\n[B] all render paths');
const renderers = {
    _renderConversationAgentStep: 'ReAct — Ideate(rerouted)/Focus/Conversation, live + reload',
    _renderStandardStep: 'Optimize — planner/phase steps',
    _renderGenieStep: 'Coordinate — the coordinator’s own fetches',
};
for (const [fn, why] of Object.entries(renderers)) {
    const body = grab(UI, fn);
    check(`${fn} records sections (${why})`, !!body && body.includes('_pdNoteSection'));
}
check('_renderConversationAgentStep emits the tag', (grab(UI, '_renderConversationAgentStep') || '').includes('_pdTagHtml('));
check('_renderStandardStep emits the tag', (grab(UI, '_renderStandardStep') || '').includes('_pdTagHtmlGrid('));

// ── §C. Every RESET boundary ────────────────────────────────────────────────
console.log('\n[C] turn boundaries — leakage is worse than omission');
check('reload entry resets (renderHistoricalTrace replays from scratch)',
      /export function renderHistoricalTrace\([^)]*\)\s*\{[\s\S]{0,600}?_pdReset\(\)/.test(UI));
check('ReAct turn start resets', (grab(UI, '_renderConversationAgentStep') || '').includes('_pdReset()'));
check('Optimize/standard resets on execution_start (every engine emits it)',
      (grab(UI, '_renderStandardStep') || '').includes('_pdReset()'));
check('Coordinate resets on its own turn boundary',
      (grab(UI, '_renderGenieStep') || '').includes('_pdReset()'));

// ── §D. Every EMIT path (backend) surfaces the section event ────────────────
console.log('\n[D] all emit paths');
const emitters = {
    'agent/conversation_agent.py': 'ReAct',
    'agent/phase_executor.py': 'Optimize',
    'agent/genie_coordinator.py': 'Coordinate',
};
for (const [rel, why] of Object.entries(emitters)) {
    const src = fs.readFileSync(path.join(ROOT, 'src/trusted_data_agent', rel), 'utf8');
    check(`${rel} surfaces _component_llm_events (${why})`, src.includes('_component_llm_events'));
}
const handler = fs.readFileSync(path.join(ROOT, 'components/builtin/skill_library/handler.py'), 'utf8');
check('the handler emits skill_section_loaded with its size',
      handler.includes('skill_section_loaded') && handler.includes('chars'));

// ── §E. Replay a REAL persisted turn (reload fidelity) ──────────────────────
console.log('\n[E] replay of a real persisted turn');
let replayed = false;
const sessDir = path.join(ROOT, 'tda_sessions');
if (fs.existsSync(sessDir)) {
    outer:
    for (const d of fs.readdirSync(sessDir)) {
        const dir = path.join(sessDir, d);
        if (!fs.statSync(dir).isDirectory()) continue;
        for (const f of fs.readdirSync(dir)) {
            let s;
            try { s = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8')); } catch { continue; }
            const evs = s?.last_turn_data?.workflow_history?.[0]?.conversation_agent_events;
            if (!Array.isArray(evs) || !evs.some(e => (e.payload || {}).type === 'skill_section_loaded')) continue;

            _pdReset();
            const steps = [];
            for (const e of evs) {
                const p = e.payload || {};
                if (p.type === 'skill_section_loaded') _pdNoteSection(p.details || {});
                if (e.type === 'conversation_llm_step') steps.push({ input: p.input_tokens, tag: _pdTagHtml() });
            }
            check('replay: the first LLM step carries no attribution', steps[0] && steps[0].tag === '');
            check('replay: later steps are attributed to the sections that landed',
                  steps.slice(1).every(x => x.tag !== ''));
            check('replay: input grows across attributed steps (the jump being explained)',
                  steps.length > 1 && steps[steps.length - 1].input > steps[0].input,
                  steps.map(x => x.input).join(' -> '));
            replayed = true;
            break outer;
        }
    }
}
if (!replayed) console.log('  SKIPPED — no persisted turn with a section load present (never a silent pass)');

// ── §J. The renderers are EXECUTED, not grepped ─────────────────────────────
// §B only asserts that `_pdNoteSection` APPEARS in each renderer. That is a presence
// check, and presence is not correctness: the `skill_section_loaded` case shipped
// assigning a bare `detailsHtml` — a name borrowed from a different renderer, undefined
// in this one — so every reload of a turn that loaded a section threw ReferenceError and
// the trace died. The same class as the `esc` bug §A2 exists for. A grep cannot see it;
// running the code can.
console.log('\n[J] the case bodies actually run');

function caseBody(fnName, caseName) {
    const fn = grab(UI, fnName);
    if (!fn) return null;
    const i = fn.indexOf(`case '${caseName}':`);
    if (i === -1) return null;
    let depth = 0;
    for (let k = fn.indexOf('{', i); k < fn.length; k++) {
        if (fn[k] === '{') depth++;
        else if (fn[k] === '}') { depth--; if (!depth) return fn.slice(fn.indexOf('{', i) + 1, k); }
    }
    return null;
}

const secBody = caseBody('_renderConversationAgentStep', 'skill_section_loaded');
check('the section-load case was located', !!secBody);
if (secBody) {
    const el = { innerHTML: '' };
    const ran = guard('the section-load case executes', () => {
        // EXACTLY the collaborators the real function has in scope — nothing more. A
        // generous harness is how the `esc` bug passed a green suite.
        // eslint-disable-next-line no-new-func
        new Function('details', 'detailsEl', 'escapeHtml', '_pdNoteSection',
                     secBody.replace(/\bbreak;\s*$/, ''))
            ({ anchor: 'master-design', chars: 20928 }, el,
             (x) => String(x), () => {});
        return el.innerHTML;
    }, null);
    check('…and writes the card into detailsEl.innerHTML',
          typeof ran === 'string' && ran.includes('master-design'),
          String(ran).slice(0, 80));
    check('…reporting the section weight in tokens', String(ran).includes('5,232'));
}

// The whole class, not the instance: this renderer assigns to `detailsEl.innerHTML` in
// every one of its cases. A bare `detailsHtml` is a variable it does not have.
for (const fn of ['_renderConversationAgentStep', '_renderGenieStep']) {
    const b = grab(UI, fn) || '';
    check(`${fn} never assigns a bare detailsHtml (it has no such variable)`,
          !/(?<![.\w])detailsHtml\s*=/.test(b) || /(?:let|const|var)\s+detailsHtml/.test(b));
}

console.log('\n' + '='.repeat(66));
console.log(`RESULT: ${failures.length ? 'FAILURES: ' + JSON.stringify(failures) : 'ALL PASS'}  (${passed} checks)`);
console.log('='.repeat(66));
process.exit(failures.length ? 1 : 0);
