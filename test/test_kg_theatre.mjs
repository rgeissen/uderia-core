#!/usr/bin/env node
/**
 * KG execution theatre — the pure core, executed under Node (no DOM, no server).
 *
 * The theatre's contract (docs/Architecture/KG_INSPECTOR_ARCHITECTURE.md, "Execution theatre"):
 *   • ONE reducer feeds live and reload — the persisted turn replays the same ops.
 *   • Consequences are read from the trace: applied / prevented / wasted, never asserted.
 *   • Relevance decides the skin: worst first, the current stage while a beat runs.
 *   • No two rendered labels overlap (the label layer is a pure placer; asserted here).
 *   • Statement roles are read from the SQL the agent wrote, table-qualified.
 *
 * Run: node test/test_kg_theatre.mjs
 */
import { sqlRoles, buildModel, createReducer, placeLabels, eventsFromTurn, skinSentence, BEATS, BEAT_EXPLAIN, LEGEND, explainBeat, explainStatement, entityCount, correspondenceCount } from '../static/js/handlers/kgTheatre.js';

import fs from 'node:fs';
let pass = 0, fail = 0;
const check = (label, cond, detail = '') => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}${detail ? ' — ' + detail : ''}`); } };

// ── a faithful core-model enrichment (the shape handler._theatre_facts stamps) ──
const ENR = {
    enrichments: [{
        kg_id: 'kg-core', kg_name: 'Policing Core Model', entity_count: 8, relationship_count: 5,
        entity_names: ['Policing Core.Incident', 'Policing Core.Offence', 'Policing Core.Victim', 'Policing Core.Agency', 'Policing Core.CaseObject', 'Policing Core.ChainOfCustody',
            'Policing Core.Offence.offence_code', 'Policing Core.Incident.incident_date_text', 'Policing Core.Incident.ori', 'Policing Core.Incident.incident_number'],
        question_terms: ['robberies', 'robbery', 'month', 'reported'],
        seeds: [
            { name: 'Policing Core.Offence.offence_code', type: 'attribute', score: 125, reasons: ['decoded value', 'owning entity'], terms: ['robbery'], channel: 'lexical' },
            { name: 'Policing Core.Incident.incident_date_text', type: 'attribute', score: 70, reasons: ['identifier word'], terms: ['month'], channel: 'semantic' },
            { name: 'Policing Core.Incident', type: 'entity', score: 40, reasons: ['identifier prefix'], terms: ['reported'], channel: 'lexical' },
        ],
        provenance: { 'Policing Core.Offence': 'seed', 'Policing Core.Incident': 'seed', 'Policing Core.Victim': 'fk_chain', 'Policing Core.Agency': 'fk_chain', 'Policing Core.CaseObject': 'joinable', 'Policing Core.ChainOfCustody': 'joinable' },
        entity_facts: {
            'Policing Core.Incident': { type: 'entity', deployed: 'Policing_Core.v_Incident', area: 'crime', key_unique: true, rows: 6124701 },
            'Policing Core.Offence': { type: 'entity', deployed: 'Policing_Core.v_Offence', area: 'crime', key_unique: true },
            'Policing Core.Victim': { type: 'entity', deployed: 'Policing_Core.v_Victim', area: 'crime' },
            'Policing Core.Agency': { type: 'entity', deployed: 'Policing_Core.v_Agency', area: 'crime' },
            'Policing Core.CaseObject': { type: 'entity', deployed: 'Policing_Core.v_CaseObject', area: 'evidence' },
            'Policing Core.ChainOfCustody': { type: 'entity', deployed: 'Policing_Core.v_ChainOfCustody', area: 'evidence' },
        },
        attribute_facts: {
            'Policing Core.Offence': { ori: { type: 'CHAR(9)', semantic: 'identifier' }, incident_number: { type: 'VARCHAR(12)', semantic: 'identifier' }, offence_seq: { type: 'BYTEINT', semantic: 'identifier' }, offence_code: { type: 'VARCHAR(3)', semantic: 'code', coded: true }, offence_type: { type: 'VARCHAR(512)', decoded_from: 'offence_code' } },
            'Policing Core.Incident': { ori: { type: 'CHAR(9)', semantic: 'identifier' }, incident_number: { type: 'VARCHAR(12)', semantic: 'identifier' }, incident_date_text: { type: 'CHAR(11)', semantic: 'date', shape: 'DD-MMM-YYYY' } },
            'Policing Core.ChainOfCustody': { object_id: { type: 'VARCHAR(64)', semantic: 'identifier' } },
        },
        entity_keys: { 'Policing Core.Incident': ['ori', 'incident_number'], 'Policing Core.Offence': ['ori', 'incident_number', 'offence_seq'], 'Policing Core.Agency': ['ori'], 'Policing Core.ChainOfCustody': ['object_id'] },
        entity_foreign_keys: { 'Policing Core.Offence': ['ori', 'incident_number'], 'Policing Core.Victim': ['ori', 'incident_number'], 'Policing Core.Incident': ['ori'] },
        entity_relationships: [
            { source: 'Policing Core.Incident', target: 'Policing Core.Offence', type: 'foreign_key', cardinality: '1:N', name: 'Incident__Offence', from_columns: ['ori', 'incident_number'], to_columns: ['ori', 'incident_number'] },
            { source: 'Policing Core.Incident', target: 'Policing Core.Victim', type: 'foreign_key', cardinality: '1:N', name: 'Incident__Victim', from_columns: ['ori', 'incident_number'], to_columns: ['ori', 'incident_number'] },
            { source: 'Policing Core.Agency', target: 'Policing Core.Incident', type: 'foreign_key', cardinality: '1:N', name: 'Agency__Incident', from_columns: ['ori'], to_columns: ['ori'] },
            { source: 'Policing Core.CaseObject', target: 'Policing Core.ChainOfCustody', type: 'foreign_key', cardinality: '1:N', from_columns: ['object_id'], to_columns: ['object_id'] },
            { source: 'Policing Core.Offence', target: 'Policing Core.CaseObject', type: 'relates_to', mechanism: 'type_correspondence' },
        ],
        subject_areas: { v_incident: 'crime', v_offence: 'crime', v_caseobject: 'evidence', v_chainofcustody: 'evidence' },
        table_columns: { 'Policing_Core.v_Incident': ['ori', 'incident_number', 'incident_date_text', 'state_code'] },
        omitted_columns: { 'Policing Core.Incident': 14, 'Policing Core.Offence': 11 },
        scope_databases: ['Policing_Core'], data_products: [], semantic_seeding: { state: 'ready', hits: 3, bridged: [{ name: 'Policing Core.Incident.incident_date_text', score: 0.61 }] },
    }],
    total_entities: 10, total_relationships: 5, session_id: 's1',
};
const SQL_OK = "SELECT SUBSTR(i.incident_date_text, 4, 3) AS month, COUNT(*) FROM Policing_Core.v_Offence o JOIN Policing_Core.v_Incident i ON o.ori = i.ori AND o.incident_number = i.incident_number WHERE o.offence_code = '120' GROUP BY SUBSTR(i.incident_date_text, 4, 3)";
const SQL_WRONG_KEY = "SELECT COUNT(*) FROM Policing_Core.v_Offence o JOIN Policing_Core.v_Incident i ON o.ori = i.ori WHERE o.offence_code = '120'";
const SQL_CROSS = "SELECT COUNT(*) FROM Policing_Core.v_Incident i JOIN Policing_Core.v_ChainOfCustody c ON i.incident_number = c.object_id WHERE 1=1";

console.log('[1] statement roles');
{
    const r = sqlRoles(SQL_OK);
    check('tables resolved to leaves', r.tables.includes('v_offence') && r.tables.includes('v_incident'));
    check('aliases resolve', r.aliases.o === 'v_offence' && r.aliases.i === 'v_incident');
    check('both key pairs read as joins', r.joins.length === 2 && r.joins.every(j => j.a.table !== j.b.table));
    check('filter read with its literal', r.filters.some(f => f.table === 'v_offence' && f.col === 'offence_code' && f.literal === "'120'" && f.op === '='));
    check('group by reaches the inner column of SUBSTR', r.groupBy.some(g => g.table === 'v_incident' && g.col === 'incident_date_text'));
    check('measure read', r.measures.some(m => m.fn === 'COUNT'));
    check('role precedence: key > measure > filter > dimension', r.roles['v_offence.ori'] === 'key' && r.roles['v_offence.offence_code'] === 'filter' && r.roles['v_incident.incident_date_text'] === 'dimension');
    check('empty / non-string sql is silent, never a throw', !sqlRoles(null).ok && !sqlRoles(42).ok && sqlRoles('').tables.length === 0);
    const c = sqlRoles('SELECT 1 FROM a CROSS JOIN b');
    check('a CROSS JOIN reads as an unkeyed join', c.joins.some(j => j.unkeyed));
}

console.log('[2] the model is read from the event\'s own facts');
{
    const m = buildModel(ENR);
    check('entities carry deployed identifiers and areas', m.entities['Policing Core.Offence'].deployed === 'Policing_Core.v_Offence' && m.entities['Policing Core.Offence'].area === 'crime');
    check('deployed leaf resolves back to the entity', m.byLeaf['v_offence'] === 'Policing Core.Offence' && m.byLeaf['offence'] === 'Policing Core.Offence');
    check('declared keys on the entity', m.entities['Policing Core.Offence'].keys.join(',') === 'ori,incident_number,offence_seq');
    check('relationships split from correspondences', m.rels.length === 4 && m.bridges.length === 1 && m.bridges[0].type === 'relates_to');
    check('cardinality and key columns travel', m.rels[0].cardinality === '1:N' && m.rels[0].fromCols.join(',') === 'ori,incident_number');
    check('attributes carry facts (coded, date shape)', m.entities['Policing Core.Offence'].attrs.offence_code.coded === true && m.entities['Policing Core.Incident'].attrs.incident_date_text.shape === 'DD-MMM-YYYY');
    check('the seeds\' area leads the column order', m.areas[0] === 'crime');
    check('an empty event yields an empty model, not a throw', Object.keys(buildModel({}).entities).length === 0 && Object.keys(buildModel(null).entities).length === 0);
}

console.log('[3] the reducer — live stages then execution');
{
    const R = createReducer();
    R.apply('user_query', { query: 'How many robberies were reported per month?' });
    let ops = R.apply('kg_stage', { stage: 'understand', terms: ['robberies', 'robbery', 'month'], ts: 1 });
    check('understand → question op, beat set', ops.some(o => o.op === 'question') && R.state.beat === 'understand');
    ops = R.apply('kg_stage', { stage: 'seed', count: 3, seeds: ENR.enrichments[0].seeds, ts: 2 });
    check('seed → seeds op with a plain-language sentence', ops.some(o => o.op === 'seeds') && /Seeded .*offence_code/.test(R.state.stageSentence));
    ops = R.apply('kg_stage', { stage: 'expand', entities: 10, relationships: 5, provenance_counts: { seed: 2, fk_chain: 2 }, ts: 3 });
    check('expand names the declared-key reach', /2 reached through declared keys/.test(R.state.stageSentence));
    ops = R.apply('kg_stage', { stage: 'inject', entities: 10, relationships: 5, omitted_columns: { a: 14, b: 11 }, ts: 4 });
    check('inject counts the folded attributes', /25 attributes folded/.test(R.state.stageSentence));
    ops = R.apply('kg_enrichment', ENR);
    check('the enrichment builds the model and does NOT re-synthesize stages already seen', ops.some(o => o.op === 'model') && !ops.some(o => o.synthetic) && R.state.synthetic === false);
    check('beats seen in order', R.state.beatsSeen.join(',') === 'understand,seed,expand,inject');
    // the statement that follows the declared key
    ops = R.apply('tool_intent', { tool_name: 'base_readQuery', arguments: { sql: SQL_OK } });
    const applied = R.state.consequences.filter(c => c.kind === 'applied');
    check('a statement moves the beat to execute', R.state.beat === 'execute');
    check('the declared key is recognised as APPLIED, naming both entities', applied.some(a => a.fact === 'declared_key' && /Joined Offence to Incident on their declared key/.test(a.sentence)));
    check('the quoted code comparison is APPLIED', applied.some(a => a.fact === 'code_as_string'));
    check('the text-date grouping is APPLIED with its shape', applied.some(a => a.fact === 'date_shape' && a.shape === 'DD-MMM-YYYY'));
    check('the skin while executing is the latest applied fact', /declared key|code|text/.test(skinSentence(R.state).text) && skinSentence(R.state).state === 'live');
    ops = R.apply('tool_result', { tool_name: 'base_readQuery', details: { status: 'success', result_rows: 12 } });
    check('a success result is executed, not prevented', ops.some(o => o.op === 'executed' && o.ok && o.rows === 12));
    R.apply('grounding_gate', { details: { decision: 'pass', out_of_scope_dbs: [], scope_databases: ['Policing_Core'], ran: true } });
    check('a clean verdict says in scope', R.state.beat === 'verify' && /Stayed inside/.test(R.state.stageSentence));
    const rec = R.end({ durationMs: 12800 });
    check('the receipt names the grain and the declared keys', R.state.receipt.sentence.includes('grain of Offence') && R.state.receipt.sentence.includes('declared keys') && R.state.receipt.state === 'pass');
    check('the receipt counts seeds used from touched entities', R.state.receipt.seeds === 3 && R.state.receipt.seedsUsed >= 1 && R.state.receipt.wasted === false);
    check('facts are words, deduplicated', R.state.receipt.facts.includes('declared key') && R.state.receipt.facts.includes('code as string') && R.state.receipt.facts.includes('date shape'));
    check('the ops ledger is append-only and ends with the receipt', rec.length === 1 && R.state.ops[R.state.ops.length - 1].op === 'receipt');
}

console.log('[4] the reducer — the enrichment alone reconstructs the four stages (no live notices)');
{
    const R = createReducer();
    const ops = R.apply('kg_enrichment', ENR);
    check('all four stages synthesized in order', ['question', 'seeds', 'expand', 'inject'].every(o => ops.some(x => x.op === o && x.synthetic)) && R.state.beatsSeen.join(',') === 'understand,seed,expand,inject');
    check('marked as reconstructed (never claimed live)', R.state.synthetic === true);
    check('every op carries the beat it belongs to (a beat-less op escapes a hold)', ops.every(o => BEATS.includes(o.beat)), JSON.stringify(ops.map(o => o.op + ':' + o.beat)));
    check('Understand is emitted before the model op, which belongs to Seed', ops.findIndex(o => o.op === 'question') < ops.findIndex(o => o.op === 'model') && ops.find(o => o.op === 'model').beat === 'seed' && ops.findIndex(o => o.op === 'model') < ops.findIndex(o => o.op === 'seeds'), JSON.stringify(ops.map(o => o.op + ':' + o.beat)));
}

console.log('[5] prevented — the wrong key, the cross-area join, the ReAct preview');
{
    const R = createReducer();
    R.apply('kg_enrichment', ENR);
    R.apply('tool_intent', { tool_name: 'base_readQuery', arguments: { sql: SQL_WRONG_KEY } });
    const st = R.state.statements[0];
    check('a join covering only part of the declared key is SUSPECT, not applied', st.suspect.some(s => s.kind === 'wrong_key' && s.declared.join(',') === 'ori,incident_number') && !st.applied.some(a => a.fact === 'declared_key'));
    const ops = R.apply('tool_result', { tool_name: 'base_readQuery', details: { status: 'error', preflight: true, preflight_join: true, error: 'join key', violations: [{ tables: ['v_offence', 'v_incident'], key: ['ori', 'incident_number'], used: ['ori'], missing: ['incident_number'] }] } });
    const prevented = R.state.consequences.find(c => c.kind === 'prevented');
    check('the refusal is a PREVENTED consequence naming the declared key', ops.some(o => o.op === 'prevented') && prevented && prevented.check === 'join_key' && /declared key is ori, incident_number/.test(prevented.sentence));
    check('relevance: prevented beats everything on the skin', skinSentence(R.state).state === 'danger' && /Stopped a join/.test(skinSentence(R.state).text));
    const rec = R.end();
    check('the receipt is danger when the only statement was stopped', R.state.receipt.state === 'danger' && R.state.receipt.prevented === 1);

    const R2 = createReducer();
    R2.apply('kg_enrichment', ENR);
    R2.apply('tool_intent', { tool_name: 'base_readQuery', arguments: { sql: SQL_CROSS } });
    check('a join across subject areas is suspect as cross_area with the correspondence found', R2.state.statements[0].suspect.some(s => s.kind === 'cross_area'));
    R2.apply('tool_result', { tool_name: 'base_readQuery', details: { status: 'error', preflight: true, preflight_cross_domain: true, error: 'x', violations: [{ tables: ['v_incident', 'v_chainofcustody'], areas: ['crime', 'evidence'] }] } });
    check('the cross-domain refusal reads as a subject-area stop', R2.state.consequences.some(c => c.kind === 'prevented' && c.check === 'cross_domain'));

    // ReAct lane: the refusal arrives as a preview string
    const R3 = createReducer();
    R3.apply('kg_enrichment', ENR);
    R3.apply('conversation_tool_invoked', { tool_name: 'base_readQuery', arguments: { sql: SQL_WRONG_KEY } });
    const o3 = R3.apply('conversation_tool_completed', { tool_name: 'base_readQuery', success: false, result_preview: "[Preflight:join] JOIN v_offence ⋈ v_incident uses (ori) — not the declared key (ori, incident_number). Rewrite: ON …" });
    check('the ReAct preview is read as a join-key stop', o3.some(o => o.op === 'prevented') && R3.state.consequences.some(c => c.check === 'join_key'));
    const o4 = createReducer();
    o4.apply('kg_enrichment', ENR);
    o4.apply('conversation_tool_invoked', { tool_name: 'base_readQuery', arguments: { sql: SQL_OK } });
    const okOps = o4.apply('conversation_tool_completed', { tool_name: 'base_readQuery', success: true, result_preview: '{"status":"success"}', result_rows: 12 });
    check('a clean ReAct completion is executed', okOps.some(o => o.op === 'executed' && o.ok));
}

console.log('[6] wasted — seeds offered, none touched');
{
    const R = createReducer();
    R.apply('kg_enrichment', ENR);
    R.apply('tool_intent', { tool_name: 'base_readQuery', arguments: { sql: 'SELECT COUNT(*) FROM airlines_mvm.awb a' } });
    R.apply('tool_result', { tool_name: 'base_readQuery', details: { status: 'success' } });
    R.end();
    check('the receipt reads WASTED and the skin says so', R.state.receipt.wasted === true && /used none of them/.test(R.state.receipt.sentence) && R.state.receipt.state === 'warn');
}

console.log('[7] verdict + honesty relevance');
{
    const R = createReducer();
    R.apply('kg_enrichment', ENR);
    R.apply('tool_intent', { tool_name: 'base_readQuery', arguments: { sql: SQL_OK } });
    R.apply('tool_result', { tool_name: 'base_readQuery', details: { status: 'success' } });
    R.apply('grounding_gate', { details: { decision: 'annotate', out_of_scope_dbs: ['finance_ledger'], scope_databases: ['Policing_Core'], ran: true } });
    check('an out-of-scope reference is a WARN skin naming the source', skinSentence(R.state).state === 'warn' && /finance_ledger/.test(skinSentence(R.state).text));
    R.apply('grounding_gate', { details: { decision: 'block', out_of_scope_dbs: ['finance_ledger'], ran: true } });
    check('a block is DANGER on the skin', skinSentence(R.state).state === 'danger');
    const H = createReducer();
    H.apply('kg_enrichment', ENR);
    H.apply('honesty', { details: { acted: true, decision: 'block', kind: 'empty_relation' } });
    check('an honesty verdict is a WARN skin in plain words', skinSentence(H.state).state === 'warn' && /empty result/.test(skinSentence(H.state).text));
    const N = createReducer();
    N.apply('honesty', { details: { acted: false, ran: false } });
    check('a non-acting honesty stamp is silent', !N.state.honesty);
}

console.log('[8] reload replay — the persisted turn yields the same events');
{
    const turn = {
        user_query: 'How many robberies were reported per month?', kg_enrichment_event: ENR,
        execution_trace: [
            { action: { tool_name: 'TDA_SystemLog', arguments: { message: 'phase' } }, result: {} },
            { action: { tool_name: 'base_readQuery', arguments: { sql: SQL_OK } }, result: { status: 'success', result_rows: 12 } },
        ],
        grounding: { decision: 'pass', out_of_scope_dbs: [], ran: true }, honesty: { acted: false },
    };
    const ev = eventsFromTurn(turn);
    check('system-log entries are not statements', !ev.some(([t, p]) => t === 'tool_intent' && /TDA_SystemLog/.test(p.tool_name)));
    check('order: query → enrichment → intent → result → grounding', ev.map(e => e[0]).join(',') === 'user_query,kg_enrichment,tool_intent,tool_result,grounding_gate,honesty');
    const R = createReducer(); for (const [t, p] of ev) R.apply(t, p); R.end();
    check('the replay reaches the same receipt as the live turn', R.state.receipt.state === 'pass' && R.state.consequences.filter(c => c.kind === 'applied').length === 3);
    const react = { kg_enrichment_event: ENR, conversation_agent_events: [{ type: 'conversation_tool_invoked', payload: { tool_name: 'base_readQuery', arguments: { sql: SQL_OK } } }, { type: 'conversation_tool_completed', payload: { tool_name: 'base_readQuery', success: true, result_preview: 'ok' } }] };
    const ev2 = eventsFromTurn(react);
    check('the ReAct trace replays through the same two event types', ev2.map(e => e[0]).join(',') === 'kg_enrichment,conversation_tool_invoked,conversation_tool_completed');
    check('an empty turn yields no events, never a throw', eventsFromTurn(null).length === 0 && eventsFromTurn({}).length === 0);
}

console.log('[9] the label layer — no two labels overlap, priority wins, folded when nothing fits');
{
    const occupied = [{ x: 0, y: 0, w: 100, h: 40 }];
    const labels = [
        { id: 'a', w: 60, h: 14, candidates: [{ x: 10, y: 10 }, { x: 120, y: 10 }], priority: 1 },
        { id: 'b', w: 60, h: 14, candidates: [{ x: 120, y: 10 }, { x: 120, y: 30 }], priority: 5 },
        { id: 'c', w: 60, h: 14, candidates: [{ x: 120, y: 10 }], priority: 0 },
    ];
    const p = placeLabels(labels, occupied, { x: 0, y: 0, w: 400, h: 300 });
    check('the box is respected (a skips its first candidate)', p.a && p.a.x === 120 && p.a.y === 10 || (p.a && p.a.y !== 10) || p.a === null);
    check('priority takes the shared slot first', p.b && p.b.x === 120 && p.b.y === 10);
    check('a label with no free candidate folds (null), never overlaps', p.c === null);
    const rects = Object.entries(p).filter(([, v]) => v).map(([k, v]) => ({ ...v, w: 60, h: 14 }));
    const overlap = (r, t) => !(r.x + r.w <= t.x || t.x + t.w <= r.x || r.y + r.h <= t.y || t.y + t.h <= r.y);
    let any = false; for (let i = 0; i < rects.length; i++) for (let j = i + 1; j < rects.length; j++) if (overlap(rects[i], rects[j])) any = true;
    check('no two placed labels overlap each other', !any);
    check('bounds are honoured', placeLabels([{ id: 'x', w: 50, h: 10, candidates: [{ x: 390, y: 10 }] }], [], { x: 0, y: 0, w: 400, h: 100 }).x === null);
}

console.log('[10] beat vocabulary is the seven-beat story');
check('seven beats, in the story order', BEATS.join(',') === 'understand,seed,expand,inject,execute,verify,receipt');

console.log('[11] the idle frame is the default; a reload ARMS, a primer turn is never a story');
{
  // The renderer is the app-side seam (it loads d3, so it is asserted structurally); the theatre's
  // own arm/replay behaviour is executed in the DOM gate (test_kg_theatre_dom.mjs).
  const fs = await import('node:fs');
  const R = fs.readFileSync('components/builtin/knowledge_graph/renderer.js', 'utf8');
  const E = fs.readFileSync('static/js/eventHandlers.js', 'utf8');
  const T = fs.readFileSync('static/js/handlers/kgTheatre.js', 'utf8');
  const replay = R.slice(R.indexOf('export function replayKGTheatre('), R.indexOf('function _syncTheatreToggle('));
  check('a reloaded turn is ARMED, never auto-played', /t\.arm\(events\)/.test(replay) && !/replayFrom\(/.test(replay));
  check('a session-primer turn is not offered on reload', /turnData\.is_session_primer\) return false/.test(replay));
  const consume = R.slice(R.indexOf('function _theatreConsume('), R.indexOf('function _theatreEnd('));
  check('a live primer turn never reaches the stage', /payload\.is_session_primer\)[^\n]*_theatreSkipTurn = true/.test(consume) && /if \(_theatreSkipTurn\) return/.test(consume));
  check('the primer skip closes with the turn', /_theatreSkipTurn\) \{ _theatreSkipTurn = false; return; \}/.test(R.slice(R.indexOf('function _theatreEnd('))));
  check('a live turn start tells the theatre it is live (the armed story is dropped)', /_theatre\.reset\(\{ wholeGraph: _wholeGraph\(\), live: true \}\)/.test(consume));
  const toggle = R.slice(R.indexOf('function _ensureTheatreControls('), R.indexOf('_syncTheatreToggle();\n}', R.indexOf('function _ensureTheatreControls(')));
  check('toggling the theatre on arms the last turn rather than playing it', /t\.arm\(_theatreTurn\.slice\(\)\)/.test(toggle) && !/replayFrom/.test(toggle));
  check('the submit payload carries the primer flag to the stage', /_tryKGAnimate\('user_query', \{ query:[^}]*is_session_primer: !!body\.is_session_primer/.test(E));
  const reset = T.slice(T.indexOf('reset(next) {'), T.indexOf('setDepth, get depth()'));
  check('the theatre drops the armed source on a live reset only', /if \(next && next\.live\) _replaySource = null;/.test(reset));
  check('arm() is the reload verb: a source and an idle stage', /arm\(events\) \{ _replaySource = \(events \|\| \[\]\)\.slice\(\); api\.reset\(\); \}/.test(T));
  check('Replay reads the turn on stage before an armed source', /const src = \(ui\.events && ui\.events\.length\) \? ui\.events\.slice\(\) : _replaySource;/.test(T));
  check('the armed source is declared above every reader (the TDZ trap)', T.indexOf('let _replaySource = null;') < T.indexOf('function refreshChrome()'));
}
console.log('[12] every beat is explained: what it does, what it did (only once performed), what it is doing now');
{
  check('every beat has a constant explanation', BEATS.every(b => typeof BEAT_EXPLAIN[b] === 'string' && BEAT_EXPLAIN[b].length > 40));
  const r = createReducer();
  r.apply('user_query', { query: 'How many robberies were reported per month in 2023?' });
  r.apply('kg_stage', { stage: 'understand', terms: ENR.enrichments[0].question_terms });
  r.apply('kg_stage', { stage: 'seed', seeds: ENR.enrichments[0].seeds });
  r.apply('kg_stage', { stage: 'expand', entities: 10, relationships: 5, provenance_counts: { seed: 2, fk_chain: 2, joinable: 2 } });
  r.apply('kg_stage', { stage: 'inject', entities: 10, relationships: 5, omitted_columns: { a: 14, b: 11 } });
  r.apply('kg_enrichment', ENR);
  r.apply('tool_intent', { tool_name: 'base_readQuery', arguments: { sql: "SELECT COUNT(*) FROM Policing_Core.v_Offence o JOIN Policing_Core.v_Incident i ON o.ori = i.ori AND o.incident_number = i.incident_number WHERE o.offence_code = '120'" } });
  r.apply('tool_result', { tool_name: 'base_readQuery', details: { status: 'success', result_rows: 12 } });
  const S = r.state;
  // the facts of a beat are told only once the STAGE performed it: a queued beat says so and nothing else
  const q = explainBeat(S, 'seed', { known: true, queued: true });
  check('a queued beat carries no facts yet', q.did === '' && /Up next/.test(q.status), JSON.stringify(q));
  const d = explainBeat(S, 'seed', { known: true, done: true });
  check('a performed Seed names its seeds and their channels', /3 seeds reached/.test(d.did) && /offence_code/.test(d.did) && /1 by meaning/.test(d.did) && d.status === 'Performed.', JSON.stringify(d));
  check('a held beat says how to continue', /Held here/.test(explainBeat(S, 'seed', { known: true, done: true, held: true }).status));
  check('a previewed beat says how to hold it, and how to return', /Previewing/.test(explainBeat(S, 'seed', { known: true, done: true, preview: true }).status));
  check('the beat on stage says so', explainBeat(S, 'execute', { known: true, done: true, current: true }).status === 'On stage now.');
  check('a beat after the hold reads ahead, with its facts', /After the hold/.test(explainBeat(S, 'execute', { known: true, ahead: true }).status) && /1 statement ran/.test(explainBeat(S, 'execute', { known: true, ahead: true }).did));
  const ex = explainBeat(S, 'execute', { known: true, done: true });
  check('a performed Execute counts statements and the facts applied', /1 statement ran/.test(ex.did) && /declared key/.test(ex.did), ex.did);
  // Verify that never happened, after the receipt: DID NOT RUN, with its reason — never "waiting"
  r.end({});
  const v = explainBeat(S, 'verify', { known: false, absent: true });
  check('an absent Verify after the receipt says it did not run, and why', /Did not run this turn/.test(v.status) && /grounding gate/.test(v.status) && v.did === '', JSON.stringify(v));
  check('a beat the turn has not reached reads waiting', /Waiting/.test(explainBeat(S, 'verify', { known: false, pending: true }).status));
  const rc = explainBeat(S, 'receipt', { known: true, done: true });
  check('the receipt explains the verdict and the seeds used', /seeds used/.test(rc.did), rc.did);
  // a reload's first four beats are reconstructed, and the explanation says so
  const r2 = createReducer(); r2.apply('user_query', { query: 'q' }); r2.apply('kg_enrichment', ENR);
  check('a reconstructed beat is named as such', /Reconstructed from the enrichment/.test(explainBeat(r2.state, 'seed', { known: true, done: true }).did));
  const st = explainStatement(S, S.statements[0], { held: true });
  check('a statement is explained: what it is, what it did, how to continue', /Statement 1/.test(st.does) && /v_offence|v_incident/i.test(st.does) && st.did.length > 10 && /Held here/.test(st.status), JSON.stringify(st));
  check('explainBeat never throws on an empty state', (() => { try { for (const b of BEATS) explainBeat(createReducer().state, b, {}); return true; } catch (e) { return false; } })());
}
console.log('[13] the key names every mark the stage can draw');
{
  const items = LEGEND.flatMap(g => g.items);
  check('five groups, every item labelled', LEGEND.length === 5 && items.every(i => i.sw && i.label && i.label.length > 8));
  const rels = new Set(items.filter(i => i.sw === 'rel').map(i => i.v));
  check('every relationship state the stage paints has a row', ['known', 'lit', 'pass', 'danger', 'bridge'].every(v => rels.has(v)));
  const boxes = new Set(items.filter(i => i.sw === 'box').map(i => i.v));
  check('every entity state has a row', ['dim', 'known', 'lit', 'solid', 'background', 'cooling'].every(v => boxes.has(v)));
  const dots = new Set(items.filter(i => i.sw === 'dot').map(i => i.v));
  check('every trail state has a row', ['pending', 'queued', 'active', 'done', 'danger'].every(v => dots.has(v)) && items.some(i => i.sw === 'bar'));
  check('the marks are keyed', ['perimeter', 'ghost', 'barrier', 'receipt'].every(sw => items.some(i => i.sw === sw)));
  check('the row icons are keyed', ['seed', 'key', 'filter', 'dimension', 'measure', 'absent'].every(v => items.some(i => i.sw === 'icon' && i.v === v)));
}

console.log('[14] the real turn (prod ce559107, turn 2): the reader, the grain, the wording');
{
  const fs = await import('node:fs');
  const turn = JSON.parse(fs.readFileSync(new URL('./fixtures/kg_theatre_ce559107_turn2.json', import.meta.url)));
  const r = createReducer();
  for (const [t, p] of eventsFromTurn(turn)) r.apply(t, p);
  r.end({ durationMs: 22770 });
  const S = r.state, st = S.statements[0];
  check('one data statement (the dashboard call is not one)', S.statements.length === 1, S.statements.length);
  check('a GROUP BY on a select alias is followed to its column',
    st.roles.groupBy.some(g => g.table === 'v_incident' && g.col === 'incident_date_text' && g.via === 'month_text'), JSON.stringify(st.roles.groupBy));
  check('a filter on a function of a column is read (the year filter)',
    st.roles.filters.some(f => f.fn === 'SUBSTR' && f.col === 'incident_date_text' && f.literal === "'2023'"), JSON.stringify(st.roles.filters));
  check('the bare column is placed on its table through the graph', st.roles.roles['v_incident.incident_date_text'] === 'dimension', JSON.stringify(st.roles.roles));
  const facts = S.consequences.filter(c => c.kind === 'applied').map(c => c.fact);
  check('the date shape is credited', facts.includes('date_shape'), facts.join(','));
  check('the declared key and the quoted code are still credited', facts.includes('declared_key') && facts.includes('code_as_string'), facts.join(','));
  check('COUNT(*) over Incident ⋈ Offence answers at the grain of Offence (the 1:N child)', st.grain === 'Policing Core.Offence', st.grain);
  check('the receipt names that grain', /grain of Offence/.test(S.receipt.sentence), S.receipt.sentence);
  check('the date seed is counted as used (3 of 15)', S.receipt.seedsUsed === 3, S.receipt.seedsUsed);
  // a bare column BOTH tables carry (ori) is never placed by the graph resolver — it would be a guess
  const r2 = createReducer();
  r2.apply('kg_enrichment', turn.kg_enrichment_event);
  r2.apply('conversation_tool_invoked', { tool_name: 'base_readQuery', arguments: { sql: 'SELECT ori, COUNT(*) FROM Policing_Core.v_Incident I JOIN Policing_Core.v_Offence O ON I.ori = O.ori AND I.incident_number = O.incident_number GROUP BY ori' } });
  const g2 = r2.state.statements[0].roles.groupBy;
  check('the graph resolver never guesses between two tables carrying the column', g2.length === 1 && g2[0].table === null, JSON.stringify(g2));
  const ex = explainBeat(S, 'expand', { done: true }).did;
  check('the reach counts the nodes in scope (kgVocabulary: an entity is a business concept): no raw tag, no attribute count inside it', /nodes in scope \(\d+ seeded/.test(ex) && !/\bcolumn\b/.test(ex) && !/attributes of tables/.test(ex), ex);
}
{
  // a bare column two tables both carry is never guessed
  const r = sqlRoles('SELECT ori, COUNT(*) FROM a.t1 JOIN a.t2 ON t1.k = t2.k GROUP BY ori', () => null);
  check('an unplaceable bare column stays unplaced', r.groupBy.length === 1 && r.groupBy[0].table === null, JSON.stringify(r.groupBy));
  const one = sqlRoles('SELECT SUBSTR(d, 1, 3) AS m FROM a.t1 GROUP BY m');
  check('with one table a bare column belongs to it', one.groupBy[0] && one.groupBy[0].table === 't1' && one.groupBy[0].col === 'd', JSON.stringify(one.groupBy));
  const nj = sqlRoles('SELECT x FROM a.t1 JOIN a.t2 ON t1.k = t2.k');
  check('a table with no alias before JOIN does not swallow the join', nj.tables.join(',') === 't1,t2' && nj.joins.length === 1, JSON.stringify(nj.tables));
  check('keywords and functions are never columns', !one.projected.some(p => ['substr', 'as', 'm'].includes(p.col)), JSON.stringify(one.projected));
}

{
  // a physical table another entity DEPLOYS as folds into that entity (session 34f5d1b6, turn 3)
  const ev = { enrichments: [{
    entity_names: ['P.Incident', 'P.Offence', 'Policing_Core.v_Incident', 'Policing_Core.v_Offence'],
    entity_facts: { 'P.Incident': { type: 'entity', deployed: 'Policing_Core.v_Incident', area: 'crime' },
                    'P.Offence': { type: 'entity', deployed: 'Policing_Core.v_Offence', area: 'crime' },
                    'Policing_Core.v_Incident': { type: 'table' }, 'Policing_Core.v_Offence': { type: 'table' } },
    entity_keys: { 'P.Incident': ['ori', 'incident_number'], 'P.Offence': ['ori', 'incident_number', 'offence_seq'] },
    entity_relationships: [{ source: 'P.Incident', target: 'P.Offence', type: 'foreign_key', cardinality: '1:N', from_columns: ['ori', 'incident_number'], to_columns: ['ori', 'incident_number'] }],
    seeds: [{ name: 'Policing_Core.v_Incident', type: 'table', score: 0, reasons: [], terms: [], channel: 'carry_over' }],
  }] };
  const m = buildModel(ev);
  check('a physical twin is not drawn as a second box', Object.keys(m.entities).sort().join(',') === 'P.Incident,P.Offence', Object.keys(m.entities).join(','));
  check("the twin's seed lands on its logical entity", m.seeds[0] && m.seeds[0].name === 'P.Incident', JSON.stringify(m.seeds));
  const r = createReducer();
  r.apply('kg_enrichment', ev);
  r.apply('conversation_tool_invoked', { tool_name: 'base_readQuery', arguments: { sql: 'SELECT COUNT(*) FROM Policing_Core.v_Offence O JOIN Policing_Core.v_Incident I ON O.ori = I.ori AND O.incident_number = I.incident_number' } });
  check('the statement on v_Incident/v_Offence lands on the declared join', r.state.consequences.some(c => c.fact === 'declared_key'), JSON.stringify(r.state.consequences));
}
console.log('[15] the real turn (local f622f5ef, turn 2): counts, twins, the rewrite as persisted, the honesty act');
{
  const turn = JSON.parse(fs.readFileSync(new URL('./fixtures/kg_theatre_f622f5ef_turn2.json', import.meta.url), 'utf8'));
  const ev = eventsFromTurn(turn);
  check('the persisted order is invoked, completed, rewrite (what the agent really emits)', ev.map(e => e[0]).filter(t => /conversation_tool/.test(t)).join(',') === 'conversation_tool_invoked,conversation_tool_completed,conversation_tool_rewrite');
  const R = createReducer(); for (const [t, p] of ev) R.apply(t, p); R.end();
  const S = R.state, m = S.model;
  check('15 nodes and ONE correspondence (its two edges are one; the bridge node is no entity)', entityCount(m) === 15 && correspondenceCount(m) === 1, `${entityCount(m)}/${correspondenceCount(m)}`);
  check('a physical column folds with its table: 13 seeds, none under a deployed name', m.seeds.length === 13 && !m.seeds.some(s => /^Policing_Core\./.test(s.name)), m.seeds.map(s => s.name).join(' '));
  const co = m.seeds.find(s => s.name === 'Policing Core.CaseObject');
  check('the folded seed keeps every word that reached either copy', co && co.terms.includes('case') && co.terms.includes('evidence'), JSON.stringify(co));
  const ex = explainBeat(S, 'expand', { done: true }).did;
  check('Expand: 13 relationships, 1 correspondence, 15 nodes (6 seeded, 9 through declared keys)', /13 relationships and 1 correspondence followed; 15 nodes in scope \(6 seeded, 9 through declared keys\)/.test(ex), ex);
  check('Inject: the same counts', /15 nodes, 13 relationships and 1 correspondence written/.test(explainBeat(S, 'inject', { done: true }).did) && /12 attributes folded/.test(explainBeat(S, 'inject', { done: true }).did));
  const st = S.statements[0];
  check('a rewrite that arrives AFTER the result still lands on its statement', S.statements.length === 1 && st.rewritten && st.rewritten.kind === 'reserved_alias' && /"count"/.test(st.sql) && !/"count"/.test(st.original_sql), JSON.stringify(st.rewritten));
  check('the result stays on the rewritten statement', st.result && st.result.ok && st.result.rows === 23, JSON.stringify(st.result));
  check('the rewrite is an applied consequence and the explanation says who ran what', S.consequences.some(c => c.fact === 'rewrite') && /in place of the agent/.test(explainStatement(S, st, {}).does));
  check('GROUP BY 1 reads the first select item as the dimension', st.roles.roles['v_caseobject.primary_type'] === 'dimension', JSON.stringify(st.roles.roles));
  check('the honesty act is told in its own words, naming what is not covered', S.honesty && /does not cover what was asked \(267\)/.test(S.honesty.sentence), S.honesty && S.honesty.sentence);
  check('the receipt carries the act: never a pass beside a warning', S.receipt.state === 'warn' && S.receipt.altered === true && /does not cover/.test(S.receipt.sentence) && skinSentence(S).state === 'warn', JSON.stringify(S.receipt));
  check('Verify says the answer was annotated and by which check', /annotated: 4 figures/.test(explainBeat(S, 'verify', { done: true }).did) && /does not cover/.test(explainBeat(S, 'verify', { done: true }).did));
  // every kind the honesty gate can emit has its own sentence (read from the gate's source)
  const gateSrc = fs.readFileSync(new URL('../src/trusted_data_agent/agent/honesty/gate.py', import.meta.url), 'utf8');
  const kinds = [...new Set([...gateSrc.matchAll(/kind\s*=\s*"([a-z_]+)"/g)].map(x => x[1]))];
  const generic = kinds.filter(k => { const r = createReducer(); r.apply('honesty', { acted: true, kind: k, decision: 'flag' }); return r.state.honesty.sentence === 'The honesty check altered the answer.'; });
  check('no honesty kind the gate emits falls back to the generic sentence', kinds.length >= 8 && generic.length === 0, generic.join(','));
  // the order the first gate drove (rewrite before the result) still works
  const R2 = createReducer();
  R2.apply('conversation_tool_invoked', { tool_name: 'base_readQuery', arguments: { sql: 'SELECT a AS count FROM t' } });
  R2.apply('conversation_tool_rewrite', { tool_name: 'base_readQuery', kind: 'reserved_alias', label: 'reserved word as alias', original_sql: 'SELECT a AS count FROM t', executed_sql: 'SELECT a AS "count" FROM t' });
  R2.apply('conversation_tool_completed', { tool_name: 'base_readQuery', success: true, result_rows: 2 });
  check('rewrite before the result: one statement, rewritten, result attached', R2.state.statements.length === 1 && R2.state.statements[0].rewritten && R2.state.statements[0].result.rows === 2);
  const R3 = createReducer();
  R3.apply('conversation_tool_invoked', { tool_name: 'base_readQuery', arguments: { sql: 'SELECT 1 FROM t' } });
  R3.apply('conversation_tool_completed', { tool_name: 'base_readQuery', success: true, result_rows: 1 });
  R3.apply('conversation_tool_rewrite', { tool_name: 'base_readQuery', kind: 'x', executed_sql: 'SELECT 2 FROM t' });
  R3.apply('conversation_tool_rewrite', { tool_name: 'base_readQuery', kind: 'x', executed_sql: 'SELECT 3 FROM t' });
  check('a statement is rewritten once: a second rewrite with no statement left changes nothing', R3.state.statements[0].sql === 'SELECT 2 FROM t');
}
console.log(`\n${fail === 0 ? 'ALL PASS' : 'FAILURES'} — ${pass} assertions, ${fail} failures`);
process.exit(fail ? 1 : 0);
