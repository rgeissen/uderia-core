// PROP-008 increment C2a — the flow canvas side of an action node (§6).
//
// The SHIPPED renderer is imported under the DOM stub and its pure helpers executed:
//   §P  the palette offers the Action node only while OBDA-MODEL · OBDA-ACT is on, never by keyboard
//   §S  the OBDA-ACT switch as /api/v1/obda/planes reports it (only an explicit act:true ⇒ on)
//   §I  the invocation parser: an object with an object `values`, `$name` kept as text
//   §G  the picker offers only GRANTED actions (the rule the save check and run enforce)
//   §W  the words: what an unattended act does, by its posture
//   §C  wiring: a fresh node's data, the agent field, the save shape, the type maps
//
// Run: node test/test_flow_actions_ui.mjs
import './_uderia_bus.mjs';
import { pathToFileURL } from 'node:url';
import { readFileSync } from 'node:fs';
import { installDom } from './dom_stub.mjs';

const ROOT = new URL('..', import.meta.url).pathname;
let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log(`  PASS  ${name}`); }
  else { fail++; console.log(`  FAIL  ${name}${detail !== undefined ? ' — ' + JSON.stringify(detail) : ''}`); }
}

installDom();
globalThis.fetch = async () => ({ ok: false, status: 404, json: async () => ({}) });
const PATH = `${ROOT}components/builtin/flow/renderer.js`;
const SRC = readFileSync(PATH, 'utf8');
const R = await import(pathToFileURL(PATH));

console.log('[§P] the palette');
const types = (o, a) => R._addableNodes(o, a).map(n => n.type);
check('OBDA-MODEL · OBDA-ACT off: no Action node', !types(false, false).includes('task_action'));
check('OBDA-MODEL · OBDA-ACT off, Optimize default: still none', !types(true, false).includes('task_action'));
check('ACT on: the Action node is offered', types(false, true).includes('task_action'));
check('the default argument is off (a caller that forgets the flag never offers it)',
  !R._addableNodes(false).map(n => n.type).includes('task_action'));
check('the Optimize-only Prompt node keeps its own rule', !types(false, true).includes('task_prompt') && types(true, true).includes('task_prompt'));
const act = R.ADDABLE_NODES.find(n => n.type === 'task_action');
check('it carries no keyboard shortcut (a key can never add a node the palette hides)', act && act.key == null);
check('its title says the posture decides', act && /posture decides/.test(act.title));
check('ACT on changes nothing else in the palette',
  JSON.stringify(types(true, true).filter(t => t !== 'task_action')) === JSON.stringify(types(true, false)));

console.log('[§S] the switch');
check('act on ⇒ on', R._actOnFromPlanes({ status: 'success', planes: { read: false, model: true, act: true } }) === true);
check('act off ⇒ off', R._actOnFromPlanes({ planes: { read: true, model: true, act: false } }) === false);
check('no answer / refusal ⇒ off', R._actOnFromPlanes(null) === false && R._actOnFromPlanes(undefined) === false
  && R._actOnFromPlanes({ status: 'error' }) === false);
check('a truthy non-boolean is not "on"', R._actOnFromPlanes({ planes: { act: 'yes' } }) === false);

console.log('[§I] the invocation');
const ok = R._parseInvocation('{"key": "$cid", "values": {"Phone": "$phone"}}');
check('a {key, values} object parses, $name kept as text', ok.value && ok.value.key === '$cid' && ok.value.values.Phone === '$phone', ok);
check('invalid JSON names the problem', /Invalid JSON/.test(R._parseInvocation('{').error || ''));
check('an array is refused', /must be an object/.test(R._parseInvocation('[]').error || ''));
check('null is refused', /must be an object/.test(R._parseInvocation('null').error || ''));
check('a non-object `values` is refused', /"values" must be an object/.test(R._parseInvocation('{"values": [1]}').error || ''));
check('an empty text is an empty invocation (the run names what is missing)', JSON.stringify(R._parseInvocation('').value) === '{}');

console.log('[§G] the picker');
const rows = [
  { action: 'set_tier', granted: true, posture: 'enforce', trust: 'trusted', entity: 'Customer', key: 'CustomerID', writable: ['LoyaltyTier'] },
  { action: 'close_ticket', granted: false, posture: 'enforce', trust: 'trusted' },
  { action: 'note', granted: true, posture: 'review', trust: 'changed' },
];
const g = R._grantedActions(rows);
check('only granted actions are offered', g.map(a => a.name).join(',') === 'set_tier,note', g);
check('their posture, trust, key and writable attributes travel', g[0].posture === 'enforce' && g[0].key === 'CustomerID'
  && g[0].writable[0] === 'LoyaltyTier' && g[1].trust === 'changed');
check('an absent or malformed list offers nothing', R._grantedActions(undefined).length === 0 && R._grantedActions([null]).length === 0);

console.log('[§W] the words');
check('enforce: writes when it runs', R._postureWords('enforce') === 'writes when it runs');
check('review: parks, nothing written until approved', /parks .* nothing is written until you approve/.test(R._postureWords('review')));
check('observe: records, nothing written', /nothing is written/.test(R._postureWords('observe')));
check('an unknown posture never invents a behaviour', R._postureWords('weird') === 'its posture decides');

console.log('[§C] wiring');
const d = R._defaultNodeData('task_action');
check('a fresh node starts with an empty action and an empty invocation', d.action === '' && d.invocation && d.invocation.key === '' && JSON.stringify(d.invocation.values) === '{}');
check('the agent field is offered on an action node', /const agentField = \(type === 'task_llm' \|\| type === 'judge' \|\| type === 'task_action'(?: \|\| type === 'file_output')?\)/.test(SRC));
check('save writes {action, invocation, profile_id, output_as} through the parser',
  /if \(type === 'task_action'\) \{\s*const inv = _parseInvocation\(actInv\);[\s\S]{0,200}updates\.data = \{ action: \(actName \|\| ''\)\.trim\(\), invocation: inv\.value, profile_id: profile, output_as: outputAs \}/.test(SRC));
check('the picker reads the GRANTED list for the node\'s agent (or the flow default)',
  /const _actAgent = profile \|\| defaultProfileId \|\| ''/.test(SRC) && /\/api\/v1\/actions\?profile_id=\$\{encodeURIComponent\(_actAgent\)\}/.test(SRC)
  && /setActChoices\(_grantedActions\(d && d\.actions\)\)/.test(SRC));
check('every palette call passes the probed switch', (SRC.match(/_addableNodes\(_defaultIsOptim, actOn(?:, filesOn)?\)/g) || []).length === 3
  && !/_addableNodes\(_defaultIsOptim\)/.test(SRC));
check('the probe asks /obda/planes and reads the switch through _actOnFromPlanes', /fetch\('\/api\/v1\/obda\/planes'/.test(SRC) && /setActOn\(_actOnFromPlanes\(d\)\)/.test(SRC)
  && !/fetch\('\/api\/v1\/actions', \{ headers/.test(SRC));
check('the node has a colour, a size, a renderer and a label', /task_action:\s+'#e11d48'/.test(SRC)
  && /task_action:\s+\{ width: 200, height: 72 \}/.test(SRC) && /task_action:\s+StandardNode/.test(SRC) && /task_action: 'Action'/.test(SRC));
check('an action node produces output and can be run on its own', /type === 'task_action' \|\| type === 'task_function'/.test(SRC)
  && /node\.type === 'task_action' \|\|/.test(SRC));
check('an empty grant list says how to fix it, never a blank select', /Grant one from the Actions panel/.test(SRC));
check('the error colour is a theme token', /color: 'var\(--trust-danger, #f87171\)'[^\n]*actInvErr|actInvErr && h\('div', \{ role: 'alert', style: \{ color: 'var\(--trust-danger/.test(SRC));

console.log(`\n${fail ? 'FAILED' : 'ALL PASS'} — ${pass + fail} assertions (${pass} passed, ${fail} failed)`);
process.exit(fail ? 1 : 0);
