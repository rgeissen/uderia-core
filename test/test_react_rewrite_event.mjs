// The ReAct-lane rewrite event (2026-09-19) — a deterministic rewrite the MCP tool wrapper applied
// (TOP placement · reserved alias · text-stored date · fan-out join) is a STEP of the turn on the
// Conversation / Focus-with-tools / Coordinate-expert lane, exactly as the Optimize lane's
// `System Self-Correction` is: titled on the live stream AND the notification channel, rendered
// as a card that carries the EXECUTED statement (the tool result's preview is bounded, so on a
// long result the rewrite was invisible in the historical Live Status), filed under tool
// execution, and narrated by the KG theatre as the statement that actually ran. Executes the
// shipped functions where they can be extracted; pins the dispatch sites where they cannot.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createReducer, eventsFromTurn, explainStatement } from '../static/js/handlers/kgTheatre.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (p) => fs.readFileSync(path.join(ROOT, p), 'utf8');
let pass = 0, fail = 0;
const check = (label, cond, detail = '') => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}${detail ? ' — ' + detail : ''}`); } };
const extractFn = (src, name) => {
    const i = src.indexOf(`function ${name}(`); if (i < 0) throw new Error(`no ${name}`);
    let depth = 0, j = src.indexOf('{', i);
    for (; j < src.length; j++) { if (src[j] === '{') depth++; else if (src[j] === '}') { depth--; if (!depth) break; } }
    return src.slice(i, j + 1);
};
const EV = read('static/js/eventHandlers.js'), NF = read('static/js/notifications.js'), UI = read('static/js/ui.js');
const RD = read('components/builtin/knowledge_graph/renderer.js');
const P = { tool_name: 'base_readQuery', kind: 'fanout', label: 'fan-out join → aggregate-then-join', original_sql: 'SELECT a FROM t1 JOIN t2 ON t1.k = t2.k', executed_sql: 'SELECT a FROM (SELECT k FROM t1 GROUP BY k) AS t1 JOIN (SELECT k FROM t2 GROUP BY k) AS t2 ON t1.k = t2.k', summary: 'aggregated each; joined the aggregates', changes: ['aggregated t1 per (k)', 'aggregated t2 per (k)', 'joined the two aggregates on k'], grain: 'one row per (k)', session_id: 's1' };

console.log('[1] the step title — live stream and the notification channel, executed');
{
    const getBrandedAgentName = () => 'Agent';
    // the live stream routes every conversation_* event to getLlmOnlyTitle — the harmonized owner
    const title = new Function('getBrandedAgentName', `${extractFn(EV, 'getLlmOnlyTitle')}; return getLlmOnlyTitle;`)(getBrandedAgentName);
    check('eventHandlers routes conversation_* titles to getLlmOnlyTitle', /return getLlmOnlyTitle\(eventType, payload\)/.test(extractFn(EV, '_getConversationAgentStepTitle')));
    check('eventHandlers titles the event as a System Self-Correction naming the kind', title('conversation_tool_rewrite', P) === 'System Self-Correction: fan-out join → aggregate-then-join');
    check('…and never leaks the wire token when the label is absent (falls back to the kind, then a phrase)',
          title('conversation_tool_rewrite', { kind: 'text_date' }) === 'System Self-Correction: text_date' && title('conversation_tool_rewrite', {}) === 'System Self-Correction: statement rewritten');
    const ntitle = new Function('getBrandedAgentName', `${extractFn(NF, '_getConversationAgentStepTitle')}; return _getConversationAgentStepTitle;`)(getBrandedAgentName);
    check('the notification-channel twin titles it the same way (one vocabulary, two channels)', ntitle('conversation_tool_rewrite', P) === title('conversation_tool_rewrite', P));
    check('the tool-completed title is unchanged (no regression on the sibling event)', /Tool Completed: base_readQuery/.test(title('conversation_tool_completed', { tool_name: 'base_readQuery', success: true })));
}

console.log('[2] every dispatch site carries the type');
{
    const live = EV.split("eventData.type === 'conversation_agent_start' ||")[1].split(') {')[0];
    check('live stream: the conversation-event list includes the rewrite', /conversation_tool_rewrite/.test(live));
    const bridge = EV.split('// KG Live Animation: dispatch tool invoked/completed events')[1].split('_tryKGAnimate(')[0];
    check('live stream: the KG animation bridge forwards it', /conversation_tool_rewrite/.test(bridge));
    const nfCases = NF.split("case 'conversation_agent_start':")[1].split('const payload = data.payload || {};')[0];
    check('notification channel: the case list includes it', /case 'conversation_tool_rewrite':/.test(nfCases));
    const evSet = RD.split('const _THEATRE_EVENTS = new Set([')[1].split(']);')[0];
    check("the theatre's allow-list admits it (a mounted theatre would otherwise treat it as noise)", /conversation_tool_rewrite/.test(evSet));
    const reload = UI.split("case 'conversation_tool_rewrite': {")[1] || '';
    check('ui.js renders a dedicated card for it', reload.length > 0);
}

console.log('[3] the card — executed SQL first, original kept, escaped, filed as tool execution, styled as a workaround');
{
    const card = UI.split("case 'conversation_tool_rewrite': {")[1].split("case 'conversation_agent_complete': {")[0];
    check('the executed statement is the open block; the original is kept behind a closed one',
          card.indexOf("'Executed SQL (what actually ran)', details.executed_sql, true") < card.indexOf("'Original statement (as the model wrote it)', details.original_sql, false"));
    check('every model-supplied string is escaped before it reaches innerHTML',
          /escapeHtml\(String\(sql\)\)/.test(card) && /escapeHtml\(String\(details\.summary\)\)/.test(card) && /escapeHtml\(String\(c\)\)/.test(card) && /escapeHtml\(String\(details\.grain\)\)/.test(card));
    check('the card states the cost: zero LLM rounds, applied before the statement ran', /0 — applied deterministically before the statement ran/.test(card));
    const filt = new Function(`${extractFn(UI, 'categoryByType')}; ${extractFn(UI, 'getFilterCategory')}; return getFilterCategory;`)();
    check('filed under tool execution (the Optimize workaround family) by TYPE authority — the same on the live source and on a plan reload',
          filt('interactive', 'conversation_tool_rewrite') === 'tool-execution' && filt('conversation_agent', 'conversation_tool_rewrite') === 'tool-execution'
          && filt('conversation_agent', 'conversation_llm_step') === 'agent' && filt('interactive', 'conversation_llm_step') === 'agent');
    const render = UI.split('function _renderConversationAgentStep(')[1].split('function _renderStandardStep(')[0];
    check('the step element wears the workaround class (the same look as the Optimize System Self-Correction)',
          /if \(type === 'conversation_tool_rewrite'\) stepEl\.classList\.add\('workaround'\)/.test(render));
}

console.log('[4] the KG theatre narrates the statement that actually ran');
{
    const R = createReducer();
    R.apply('conversation_tool_invoked', { tool_name: 'base_readQuery', arguments: { sql: P.original_sql } });
    const ops = R.apply('conversation_tool_rewrite', P);
    const st = R.state.statements[0];
    check('the statement under test becomes the EXECUTED one; the original is kept on the record',
          R.state.statements.length === 1 && st.sql === P.executed_sql && st.original_sql === P.original_sql && st.rewritten && st.rewritten.kind === 'fanout', JSON.stringify(st.rewritten));
    check('the roles are re-read from the executed statement', st.roles && st.roles.tables && st.roles.tables.length >= 1, JSON.stringify(st.roles && st.roles.tables));
    check('the statement op is re-emitted (marked rewritten) and an APPLIED consequence names the rewrite, no LLM round',
          ops.some(o => o.op === 'statement' && o.rewritten) && ops.some(o => o.op === 'applied' && o.consequence.fact === 'rewrite')
          && R.state.consequences.some(c => c.kind === 'applied' && c.fact === 'rewrite' && /no LLM round spent/.test(c.sentence)), JSON.stringify(ops.map(o => o.op)));
    check('every op carries its beat (a beat-less op escapes a hold)', ops.every(o => o.beat), JSON.stringify(ops.map(o => o.beat)));
    R.apply('conversation_tool_completed', { tool_name: 'base_readQuery', success: true, result_preview: '{"status":"success"}', result_rows: 5 });
    check('the result attaches to the rewritten statement, which counts as executed', st.result && st.result.ok && st.result.rows === 5);
    const ex = explainStatement(R.state, st, {});
    check('the explanation says Uderia ran a statement in place of the agent’s', /in place of the agent/.test(ex.does) && /fan-out join/.test(ex.does), ex.does);
    const R2 = createReducer();
    R2.apply('conversation_tool_invoked', { tool_name: 'base_readQuery', arguments: { sql: P.original_sql } });
    const none = R2.apply('conversation_tool_rewrite', { ...P, tool_name: 'other_tool' });
    check('a rewrite for a tool with no pending statement changes nothing', none.length === 0 && R2.state.statements[0].sql === P.original_sql);
    check('a TDA_ component call is never a statement, rewritten or not', createReducer().apply('conversation_tool_rewrite', { ...P, tool_name: 'TDA_Dashboard' }).length === 0);
    const turn = eventsFromTurn({ conversation_agent_events: [
        { type: 'conversation_tool_invoked', payload: { tool_name: 'base_readQuery', arguments: { sql: P.original_sql } } },
        { type: 'conversation_tool_completed', payload: { tool_name: 'base_readQuery', success: true, result_rows: 5 } },
        { type: 'conversation_tool_rewrite', payload: P }] });
    // the agent emits the rewrite AFTER the completion, live and persisted (conversation_agent.py);
    // the first version of this gate drove an order the product never produces
    check('a persisted turn replays in the order the agent emits: invoked, completed, rewrite', turn.map(e => e[0]).join(',') === 'conversation_tool_invoked,conversation_tool_completed,conversation_tool_rewrite');
    const R4 = createReducer(); for (const [t, p] of turn) R4.apply(t, p);
    const s4 = R4.state.statements[0];
    check('in that order the statement is still the EXECUTED one, with its result kept', R4.state.statements.length === 1 && s4.sql === P.executed_sql && s4.original_sql === P.original_sql && s4.rewritten && s4.result && s4.result.rows === 5, JSON.stringify(s4 && s4.rewritten));
    check('the agent source really emits completed before rewrite', (() => { const a = fs.readFileSync(new URL('../src/trusted_data_agent/agent/conversation_agent.py', import.meta.url), 'utf8'); const i = a.indexOf('_emit_event("conversation_tool_completed"'), j = a.indexOf('_emit_event("conversation_tool_rewrite"'); return i > 0 && j > i; })());
}

console.log(`\n${fail ? 'FAILED' : 'ALL PASS'} — ${pass + fail} assertions (${pass} passed, ${fail} failed)`);
process.exit(fail ? 1 : 0);
