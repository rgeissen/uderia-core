/**
 * PROP-008 increment A2 (UX half) — the proposal reaches every surface it is owed on.
 *
 *   §1  the theatre: a propose_* tool result is a DRAFT op — live (ReAct completion preview,
 *       Optimize tool_result details) and replay (eventsFromTurn) produce the same op; a
 *       proposal that was not drafted is still narrated; the receipt counts drafts
 *   §2  ui.js: the proposal row's Draft act — ONE delegated click, the SAME handler path
 *       through POST /v1/knowledge-graph/propose, the row replaced in place by the
 *       handler's own re-render; a refusal leaves the act usable and says why
 *   §3  the Workspace: the drafts listed under each graph as an uncounted trailer, silent
 *       when nothing is drafted
 *   §4  the Govern dialog: a draft's four-step road, drafted marked as the one state it can
 *       hold today, the later steps drawn as the road and never as buttons
 *   §5  the inspector: an authored node is dashed and its tooltip says so
 *
 * The shipped modules are EXECUTED (kgTheatre by import; ui.js / the Workspace / the Govern
 * dialog extracted under the DOM stub) — no re-implementation of the thing under test.
 *
 * Run: node test/test_kg_act_ux.mjs
 */
import './_uderia_bus.mjs';
import { readFileSync } from 'node:fs';
import { createReducer, eventsFromTurn } from '../static/js/handlers/kgTheatre.js';
import { installDom, El } from './dom_stub.mjs';
const realTimeout = globalThis.setTimeout;   // installDom() replaces setTimeout with a no-op; the flush waits use the real one

let PASS = 0, FAIL = 0;
const check = (name, cond, detail = '') => { if (cond) { PASS++; console.log(`  ✓ ${name}`); } else { FAIL++; console.log(`  ✗ ${name}${detail ? ' — ' + detail : ''}`); } };

const ENR = { enrichments: [{ kg_id: 'kg1', kg_name: 'fitness', question_terms: ['customers'],
    entity_names: ['fitness_db.Customers', 'fitness_db.Sales'],
    entity_facts: { 'fitness_db.Customers': { type: 'entity', deployed: 'fitness_db.Customers' }, 'fitness_db.Sales': { type: 'entity', deployed: 'fitness_db.Sales' } },
    attribute_facts: { 'fitness_db.Customers': { CustomerID: {}, email: {} } },
    entity_relationships: [{ source: 'fitness_db.Sales', target: 'fitness_db.Customers', type: 'foreign_key', from_columns: ['CustomerID'], to_columns: ['CustomerID'] }],
    seeds: [{ name: 'fitness_db.Customers', channel: 'identifier', score: 100 }] }] };

const SPEC_ENTITY = { action: 'propose_entity', entity: 'Lead', relates_to: 'fitness_db.Customers', attributes: ['lead_id', 'status'], key: ['lead_id'],
    verdict: { decision: 'entity', confidence: 0.9, reasons: ['declared'], provisional: true, findings: [], must: false, rule: 'declared' }, drafted: true, node_id: 77, note: 'Drafted' };
const SPEC_ATTR_PROPOSED = { action: 'propose_attribute', entity: 'fitness_db.Customers', attribute: 'loyalty_tier',
    verdict: { decision: 'value_domain', confidence: 0.85, reasons: ['closed set of 3 values declared'], provisional: true, findings: [], must: false, rule: 'closed_enumeration' }, drafted: false, node_id: null, note: '' };

console.log('[§1] the theatre: a propose_* result is a draft op, live and on replay');
{
  const r = createReducer();
  r.apply('user_query', { query: 'Which leads are open?' });
  r.apply('kg_enrichment', ENR);
  // ReAct lane: the completion carries the tool's CONTENT (spec + metadata) as a preview string
  const n0 = r.state.ops.length;
  r.apply('conversation_tool_invoked', { tool_name: 'TDA_KnowledgeGraph', arguments: { action: 'propose_entity', entity_name: 'Lead' } });
  r.apply('conversation_tool_completed', { tool_name: 'TDA_KnowledgeGraph', success: true, result_preview: JSON.stringify({ status: 'success', spec: SPEC_ENTITY, metadata: {} }) });
  const drafts = r.state.ops.slice(n0).filter(o => o.op === 'draft');
  check('a ReAct propose_entity completion yields ONE draft op', drafts.length === 1, JSON.stringify(r.state.ops.slice(n0).map(o => o.op)));
  const c = drafts[0] && drafts[0].consequence;
  check('…naming the entity, its parent, the rule and that it was drafted', c && c.what === 'entity' && c.name === 'Lead' && c.parent === 'fitness_db.Customers' && c.drafted === true && c.rule === 'declared', JSON.stringify(c));
  check('…in a sentence a person reads', c && /Drafted entity Lead on Customers on the authored layer/.test(c.sentence) && /provisional/.test(c.sentence), c && c.sentence);
  check('a TDA_* tool is never a statement (the draft is not an executed statement)', r.state.statements.length === 0);
  // Optimize lane: tool_result carries the result dict
  const n1 = r.state.ops.length;
  r.apply('tool_result', { tool_name: 'TDA_KnowledgeGraph', details: { type: 'component', spec: SPEC_ATTR_PROPOSED, metadata: {} } });
  const d2 = r.state.ops.slice(n1).filter(o => o.op === 'draft');
  check('an Optimize propose_attribute result yields a draft op too', d2.length === 1);
  check('…a proposal that was NOT drafted is still narrated, as proposed', d2[0] && d2[0].consequence.drafted === false && /Proposed attribute loyalty_tier on Customers/.test(d2[0].consequence.sentence) && /value_domain/.test(d2[0].consequence.sentence), d2[0] && d2[0].consequence.sentence);
  // other KG tool results are not drafts; other tools are untouched
  const n2 = r.state.ops.length;
  r.apply('tool_result', { tool_name: 'TDA_KnowledgeGraph', details: { spec: { action: 'query', entities: [] } } });
  r.apply('tool_result', { tool_name: 'base_readQuery', details: { status: 'success', result_rows: 3, spec: { action: 'propose_entity' } } });
  check('a KG query result and a data tool are not drafts', r.state.ops.slice(n2).every(o => o.op !== 'draft'));
  // replay: the persisted turn produces the same op through eventsFromTurn
  const turn = { user_query: 'Which leads are open?', kg_enrichment_event: ENR,
    conversation_agent_events: [{ type: 'conversation_tool_invoked', payload: { tool_name: 'TDA_KnowledgeGraph', arguments: {} } },
                                { type: 'conversation_tool_completed', payload: { tool_name: 'TDA_KnowledgeGraph', success: true, result_preview: JSON.stringify({ spec: SPEC_ENTITY }) } }] };
  const r2 = createReducer();
  for (const [t, p] of eventsFromTurn(turn)) r2.apply(t, p);
  const rd = r2.state.ops.filter(o => o.op === 'draft');
  check('replay from the persisted turn yields the same draft op', rd.length === 1 && rd[0].consequence.sentence === c.sentence);
  // the receipt counts it
  r2.end({});
  const src = readFileSync('static/js/handlers/kgTheatre.js', 'utf8');
  check('the Execute summary counts drafts (the receipt line)', /drafted\b/.test(src.match(/execute: S\.statements\.length[\s\S]{0,900}/)[0]));
  check('the stage draws a draft dashed and labels it', /ui\.drafts\.get\(e\.name\)/.test(src) && /stroke-dasharray', draftKind \? '6 4'/.test(src) && /draftKind\.toUpperCase\(\)/.test(src));
  check('the drafts map is reset with the other per-turn sets, at both sites', (src.match(/ui\.drafts = new Map\(\)/g) || []).length === 2);
}

let _pending = 1; function done2() { _pending--; }
console.log('[§2] ui.js: the proposal row\'s Draft act');
// awaited: the page bus dispatches to the document of the moment, so the next block's stub must not
// replace it while this block's promise chain is still running
await (async () => {
  const ui = readFileSync('static/js/ui.js', 'utf8');
  const start = ui.indexOf('function _wireProposalActs(');
  const end = ui.indexOf("if (typeof document !== 'undefined' && !window.__pinActBound) {");
  check('the act is wired beside the pin act (one site, live and reloaded)', start > 0 && end > start && /_wirePinActs\(messageContent\);\n\s+_wireProposalActs\(messageContent\);/.test(ui));
  const block = ui.slice(start, end);
  // execute the block under a DOM stub with a fake fetch
  const calls = [];
  const banners = [];
  let response = { ok: true, d: { html: `<div class="kg-proposal" data-kg-proposal="entity"><div class="kg-proposal-state">Drafted 'fitness_db.Lead' on the authored layer (decision dec_1).</div></div>`, drafted: true, note: 'Drafted.' } };
  const fetch = (url, opts) => {
    if (!opts || !opts.body) return Promise.resolve({ ok: true, json: () => Promise.resolve({ nodes: [] }) });   // the authored read
    calls.push({ url, body: JSON.parse(opts.body) }); const r = response; return Promise.resolve({ ok: r.ok, json: () => Promise.resolve(r.d) }); };
  const events = [];
  const listeners = {};
  const document = { createElement: (t) => new El(t), addEventListener: (k, f) => { listeners[k] = f; }, dispatchEvent: (e) => { events.push(e.type); } };
  globalThis.document = document;   // the page bus (UderiaBus) dispatches to the document of the moment
  const window = { showAppBanner: (m, k) => banners.push([m, k]) };
  const localStorage = { getItem: () => 'tok' };
  const CustomEvent = function (type, init) { this.type = type; this.detail = init && init.detail; };
  const fn = new Function('document', 'window', 'fetch', 'localStorage', 'CustomEvent', 'console', block + '\nreturn { _wireProposalActs, _onProposalAct };');
  const P = fn(document, window, fetch, localStorage, CustomEvent, { error: () => {} });
  check('a document-level delegated click is bound once', typeof listeners.click === 'function' && window.__proposalActBound === true);
  // a row with the act
  const row = new El('div'); row.className = 'kg-proposal'; row.setAttribute('data-kg-proposal', 'entity');
  const foot = new El('div'); foot.className = 'kg-proposal-foot';
  const btn = new El('button', { 'data-kg-id': 'kg1', 'data-kg-args': JSON.stringify({ action: 'propose_entity', entity_name: 'Lead', relates_to: 'fitness_db.Customers', attributes: ['lead_id'], key: ['lead_id'] }) }); btn.className = 'ans-act kg-proposal-draft';
  btn.textContent = 'Draft it on the authored layer';
  foot.appendChild(btn); row.appendChild(foot);
  const host = new El('div'); host.appendChild(row);
  P._wireProposalActs(host);
  check('wiring marks the act once (idempotent)', btn.dataset.wired === '1' && (P._wireProposalActs(host), btn.dataset.wired === '1'));
  return_: {
    const p = P._onProposalAct(btn);
    check('the click POSTs the row\'s own args with draft=true and the graph id', calls.length === 1 && calls[0].url === '/api/v1/knowledge-graph/propose' && calls[0].body.draft === true && calls[0].body.kg_id === 'kg1' && calls[0].body.action === 'propose_entity' && calls[0].body.entity_name === 'Lead', JSON.stringify(calls[0]));
    check('…while in flight the act is disabled and says so', btn.disabled === true && btn.textContent === 'Drafting…');
    await await_(p).then(() => {
      check('the row is replaced IN PLACE by the handler\'s re-render (no button on a drafted row)', host.children.length === 1 && host.children[0] !== row && host.children[0].className === 'kg-proposal' && !host.children[0].querySelector('.kg-proposal-draft'));
      check('…a banner names the outcome and the authored layer changed', banners.length === 1 && banners[0][1] === 'success' && events.includes('uderia:component-changed'), JSON.stringify({ events, banners }));
      // a refusal leaves the act usable and says why
      response = { ok: false, d: { message: 'propose_entity refused: this agent has no model-authoring grant' } };
      const row2 = new El('div'); row2.className = 'kg-proposal'; const f2 = new El('div'); const b2 = new El('button', { 'data-kg-id': 'kg1', 'data-kg-args': JSON.stringify({ action: 'propose_attribute', entity: 'E', attribute_name: 'a' }) }); b2.className = 'kg-proposal-draft'; b2.textContent = 'Draft it';
      f2.appendChild(b2); row2.appendChild(f2); const h2 = new El('div'); h2.appendChild(row2);
      return await_(P._onProposalAct(b2)).then(() => {
        check('a refusal re-enables the act, restores its words and shows the reason', b2.disabled === false && b2.textContent === 'Draft it' && banners[banners.length - 1][0].includes('no model-authoring grant') && banners[banners.length - 1][1] === 'warning', JSON.stringify(banners));
        check('…and the row stays (nothing was drafted)', h2.children[0] === row2);
        const b3 = new El('button', { 'data-kg-args': '{"action":"propose_entity"}' }); b3.className = 'kg-proposal-draft';
        const before = calls.length; P._onProposalAct(b3);
        check('an act without a graph id never fires a request', calls.length === before);
        done2();
      });
    });
  }
})();
console.log('[§2c] a stored row shows the state NOW: a node already drafted offers no Draft it');
await (async () => {
  const ui = readFileSync('static/js/ui.js', 'utf8');
  const block = ui.slice(ui.indexOf('function _wireProposalActs('), ui.indexOf("if (typeof document !== 'undefined' && !window.__pinActBound) {"));
  const reads = [];
  const authored = { nodes: [
    { name: 'Policing Core.Lead', status: 'published', attributes: [{ name: 'Policing Core.Lead.lead_id', status: 'published' }] },
    { name: 'Policing Core.Lead.closure_reason', entity_type: 'attribute', status: 'draft' }] };
  const fetch = (url) => { reads.push(url); return Promise.resolve({ ok: true, json: () => Promise.resolve(authored) }); };
  const listeners = {};
  const document = { createElement: (t) => new El(t), addEventListener: (k, f) => { listeners[k] = f; }, dispatchEvent: () => {} };
  globalThis.document = document;
  const fn = new Function('document', 'window', 'fetch', 'localStorage', 'CustomEvent', 'console',
    block + '\nreturn { _wireProposalActs, _reconcileProposalRows };');
  const P = fn(document, {}, fetch, { getItem: () => 't' }, function () {}, { error: () => {} });
  const mk = (args, node) => {
    const row = new El('div', node ? { 'data-kg-node': node } : {}); row.className = 'kg-proposal';
    const st = new El('div'); st.className = 'kg-proposal-state'; st.textContent = 'Not drafted.'; row.appendChild(st);
    const foot = new El('div'); foot.className = 'kg-proposal-foot';
    const b = new El('button', { 'data-kg-id': 'kgP', 'data-kg-args': JSON.stringify(args) }); b.className = 'kg-proposal-draft';
    foot.appendChild(b); row.appendChild(foot); return row;
  };
  // the turn on production: stored before the server named its node (no data-kg-node)
  const stale = mk({ action: 'propose_attribute', entity: 'Policing Core.Lead', attribute_name: 'closure_reason' });
  const fresh = mk({ action: 'propose_attribute', entity: 'Policing Core.Lead', attribute_name: 'due_reason' }, 'Policing Core.Lead.due_reason');
  const entity = mk({ action: 'propose_entity', entity_name: 'Lead', relates_to: 'Policing Core.CaseObject' });
  const host = new El('div'); host.appendChild(stale); host.appendChild(fresh); host.appendChild(entity);
  await P._reconcileProposalRows(host);
  check('the authored layer is read once per graph, not once per row', reads.length === 1 && reads[0].includes('kg_id=kgP'), reads);
  check('a row whose attribute was drafted since (Draft it, then a reload) says so and offers nothing',
    !stale.querySelector('.kg-proposal-draft') && stale.querySelector('.kg-proposal-state').textContent === 'Drafted on the authored layer — not yet published.',
    stale.querySelector('.kg-proposal-state').textContent);
  check('…an entity row resolves its node by the handler\'s own rule (the parent\'s root) and reads its stage',
    !entity.querySelector('.kg-proposal-draft') && entity.querySelector('.kg-proposal-state').textContent.includes('published on the model'),
    entity.querySelector('.kg-proposal-state').textContent);
  check('a row whose node does not exist keeps its Draft it', !!fresh.querySelector('.kg-proposal-draft'));
  check('…and the page re-reads whenever the authored layer changes', typeof listeners['uderia:component-changed'] === 'function');
})();

function await_(p) { return p && typeof p.then === 'function' ? p : Promise.resolve(); }

console.log('[§3] the Workspace lists the person\'s own extension under each graph — every stage');
await (async () => {
  const src = readFileSync('static/js/handlers/workspacePanelHandler.js', 'utf8');
  const start = src.indexOf('function _kgDraftRows(');
  const end = src.indexOf('// ── Monochrome icons');
  check('the KG registry entry carries the drafts trailer', /knowledge_graph: \{[\s\S]*?trailer: \(items, profileId\) => _kgDraftRows\(items, profileId\),[\s\S]*?\},\n\s+memory: \{/.test(src));
  const block = src.slice(start, end);
  const opened = [];
  const responses = { kgA: { nodes: [{ name: 'fitness_db.Lead', entity_type: 'entity', status: 'draft', decision: { rule: 'declared' } },
                                     { name: 'fitness_db.Customers.loyalty_tier', entity_type: 'attribute', status: 'draft', decision: { rule: 'closed_enumeration' } }] },
                      kgB: { nodes: [] } };
  const fetch = (url) => { const id = decodeURIComponent(url.split('kg_id=')[1]); return Promise.resolve({ ok: true, json: () => Promise.resolve(responses[id] || { nodes: [] }) }); };
  installDom();
  const document = globalThis.document;
  const vocab = readFileSync('static/js/kgVocabulary.js', 'utf8').replace(/^export /gm, '');
  const openedOpts = [];
  const fn = new Function('document', 'fetch', 'localStorage', '_esc', '_ICON_OPEN', '_openKnowledgeGraph', vocab + block + '\nreturn { _kgDraftRows };');
  const P = fn(document, fetch, { getItem: () => 't' }, (s) => String(s), '<svg/>', (g, o) => { opened.push(g.kg_id); openedOpts.push(o || {}); });
  const wrap = P._kgDraftRows([{ kg_id: 'kgA', kg_name: 'A', profile_id: 'p' }, { kg_id: 'kgB', kg_name: 'B', profile_id: 'p' }], 'p');
  check('a trailer element is returned and hidden until it has rows', wrap && wrap.className.includes('ws-trailer') && wrap.style.display === 'none');
  await new Promise(r => realTimeout(r, 0)); await new Promise(r => realTimeout(r, 0));
  const rows = wrap.querySelectorAll('.ws-row--draft');
  check('one sub-row per draft, across graphs; a graph with none adds nothing', rows.length === 2 && wrap.style.display === '', String(rows.length));
  check('a row names the leaf, the kind, the state and the rule', rows[0].textContent.includes('Lead') && rows[0].textContent.includes('entity') && rows[0].textContent.includes('draft') && rows[0].textContent.includes('declared'));
  check('the drafts are not counted as graphs (a trailer, not an item)', !block.includes('badge') && !block.includes('count'));
  rows[0].querySelector('.ws-open').dispatchEvent({ type: 'click', stopPropagation: () => {} });
  check('opening a draft opens ITS graph', opened.length === 1 && opened[0] === 'kgA');
  const none = P._kgDraftRows([{ kg_id: 'kgB', profile_id: 'p' }], 'p');
  await new Promise(r => realTimeout(r, 0)); await new Promise(r => realTimeout(r, 0));
  check('a graph set with no drafts removes its trailer (silent)', none && !none.isConnected && none.children.length === 0);
  check('…a draft opens WITH the author\'s drafts, centred on it', openedOpts[0].includeDrafts === true && openedOpts[0].focusNames[0] === 'fitness_db.Lead');
  // after publish: the section is the person's EXTENSION, not "drafts" — a published concept stays
  // listed with the act still owed; another extender's concept is not theirs to list
  responses.kgC = { nodes: [{ name: 'core.Lead', entity_type: 'entity', status: 'published', extension_id: 'ext-me', attributes: new Array(9).fill({}) },
                            { name: 'core.Tip', entity_type: 'entity', status: 'draft', extension_id: 'ext-other' }],
                    extensions: [{ extension_id: 'ext-me', mine: true, trust: { state: 'unsigned' } },
                                 { extension_id: 'ext-other', mine: false, trust: { state: 'unsigned' } }] };
  const ext = P._kgDraftRows([{ kg_id: 'kgC', kg_name: 'Core Model', profile_id: 'p' }], 'p');
  await new Promise(r => realTimeout(r, 0)); await new Promise(r => realTimeout(r, 0));
  const xr = ext.querySelectorAll('.ws-row--draft');
  check('the section is named for what it is: the person\'s extension of that graph', /Your extension · Core Model/.test(ext.innerHTML) && !/Drafts on the authored layer/.test(block), ext.innerHTML.slice(0, 200));
  check('a PUBLISHED concept stays listed — in words, its stage, and the act owed', xr.length === 1
    && /1 entity · 9 attributes · published · unsigned/.test(xr[0].textContent), xr[0] && xr[0].textContent);
  check('another extender\'s concept is not listed as theirs', !ext.innerHTML.includes('Tip'));
  xr[0].querySelector('.ws-open').dispatchEvent({ type: 'click', stopPropagation: () => {} });
  check('a published concept opens in the model\'s own view (no drafts), centred on it', openedOpts.at(-1).includeDrafts === false && openedOpts.at(-1).focusNames[0] === 'core.Lead');
  responses.kgC.extensions[0].trust.state = 'trusted';
  const signed = P._kgDraftRows([{ kg_id: 'kgC', kg_name: 'Core Model', profile_id: 'p' }], 'p');
  await new Promise(r => realTimeout(r, 0)); await new Promise(r => realTimeout(r, 0));
  check('…once signed, no act is owed and none is shown', /published$/.test(signed.querySelector('.ws-row-meta').textContent.trim()), signed.querySelector('.ws-row-meta').textContent);
  check('the Workspace reloads when a draft lands (every component\'s change through the one subscriber)', /UderiaBus\.onComponent\('\*'/.test(src) && /_refreshIfShown\(null\)/.test(src.slice(src.indexOf("UderiaBus.onComponent('*'"), src.indexOf("UderiaBus.onComponent('*'") + 400)));
})();

console.log('[§4] the Govern dialog: the draft\'s road');
{
  const src = readFileSync('static/js/handlers/knowledgeGraphPanelHandler.js', 'utf8');
  const start = src.indexOf('const _GOV_STEPS');
  const end = src.indexOf('async function openKgGovernanceDialog(');
  // the graph's counting vocabulary the dialog speaks in (static/js/kgVocabulary.js), as shipped
  const fn = new Function(readFileSync('static/js/kgVocabulary.js', 'utf8').replace(/^export /gm, '') + src.slice(start, end) + '\nreturn { _govDraftsHtml, _GOV_STEPS, _govStepperHtml };');
  const P = fn();
  check('the four steps are the road, drafted first', P._GOV_STEPS.join('>') === 'drafted>validated>deployed>published');
  check('nothing to show when nothing is drafted', P._govDraftsHtml({ nodes: [] }) === '' && P._govDraftsHtml(null) === '');
  const html = P._govDraftsHtml({ nodes: [{ name: 'fitness_db.Lead', entity_type: 'entity', status: 'draft', authored_by: 'u1', decision: { rule: 'declared', evidence: ['declared entity agrees', 'no rows to measure'] } }] });
  check('a draft row names the node, its kind, its rule, its author and its evidence', /<strong>Lead<\/strong>/.test(html) && /entity/.test(html) && />declared</.test(html) && /by u1/.test(html) && /no rows to measure/.test(html));
  check('drafted is the one state marked as now; the later steps are the road, muted', /kg-gov-step--now[^>]*>drafted</.test(html) && (html.match(/class="kg-gov-step kg-gov-step--next u-tip"/g) || []).length === 3);
  check('the later steps are never buttons (Rule 8.5), and their note is the platform tooltip, never a native title', !/<button/.test(html) && /data-tip="not yet/.test(html) && !/ title=/.test(html));
  check('the dialog fetches the authored layer beside lifecycle + conformance, and renders the road', /\[lc, cf, authored\] = await Promise\.all/.test(src) && /knowledge-graph\/authored\?kg_id=/.test(src) && /\$\{_govDraftsHtml\(authored\)\}/.test(src) && /function render\(lc, cf, authored\)/.test(src) && /render\(lc \|\| \{\}, cf \|\| \{\}, authored/.test(src));
  check('a draft is HTML-escaped', !/<script>/.test(P._govDraftsHtml({ nodes: [{ name: 'x.<script>', entity_type: 'entity', status: 'draft' }] })));
  // A3: the Deploy act — offered exactly when a landing is OWED (a draft and the master on)
  const draft = { name: 'fitness_db.Lead', entity_type: 'entity', status: 'draft', authored_by: 'u1' };
  const on = P._govDraftsHtml({ nodes: [draft], act_enabled: true });
  const off = P._govDraftsHtml({ nodes: [draft], act_enabled: false });
  check('with the master on and a draft, the Deploy row is offered: a target, a preview, a two-step land', /id="kg-gov-deploy"/.test(on) && /id="kg-gov-deploy-target"/.test(on) && /kg-gov-deploy-preview/.test(on) && /kg-gov-deploy-land/.test(on));
  check('…and the words say the platform decides where a table lands', /decided by the platform/.test(on) && /platform-owned sibling/.test(on));
  check('the land button starts disabled (a preview comes first), and the row never carries a database field', /class="kg-gov-deploy-land" disabled/.test(on) && !/name="database"|id="kg-gov-deploy-database"/.test(on));
  check('with the master off, no Deploy row — nothing is offered that the REST would refuse', !/id="kg-gov-deploy"/.test(off));
  check('with the master on, the deployed step names its act; validated names the act that earns it (a Preview)', /data-tip="land it from Next, above"/.test(on) && /data-tip="Preview \(Next, above\) validates the draft against the design standard"/.test(on) && !/data-tip="land it/.test(off));
  const deployed = P._govDraftsHtml({ nodes: [{ ...draft, status: 'deployed', deployed_table: 'UDERIA_EXT_fitness_db.Lead', deploy_receipt: { receipt_id: 'mc_1', landing: 'UDERIA_EXT_fitness_db', signed: true, identifier: 'UDERIA_EXT_fitness_db.Lead' } }], act_enabled: true });
  check('a deployed node reads its receipt line: where it landed, the receipt id, sealed', /landed as <code>UDERIA_EXT_fitness_db\.Lead<\/code> in UDERIA_EXT_fitness_db · receipt <code>mc_1<\/code> · sealed/.test(deployed));
  check('…its road marks drafted done, deployed now, published ahead — and validated SKIPPED (nothing validated it; Rule 3.7), with its note', /kg-gov-step--done[^>]*>drafted</.test(deployed) && /kg-gov-step--skipped u-tip[^>]*data-tip="not validated — landed before the design standard validated drafts[^"]*"[^>]*>validated</.test(deployed) && !/kg-gov-step--done[^>]*>validated</.test(deployed) && /kg-gov-step--now[^>]*>deployed</.test(deployed) && /kg-gov-step--next[^>]*>published</.test(deployed));
  const validated = P._govDraftsHtml({ nodes: [{ ...draft, status: 'deployed', validated_at: '2026-10-06T00:00:00Z', standard: 'teradata/1.0' }], act_enabled: true });
  check('…a node that WAS validated reads validated done, naming the standard and the date', /kg-gov-step--done u-tip[^>]*data-tip="validated against the design standard teradata\/1\.0 on 2026-10-06"[^>]*>validated</.test(validated));
  // A4: the readback line — what the warehouse holds of the standard, violations named, an unread dictionary honest
  const held = P._govDraftsHtml({ nodes: [{ ...draft, status: 'deployed', readback: { status: 'conformant', held: 9, violated: 0, unverified: 0, standard: 'teradata/1.0', violations: [] } }], act_enabled: true });
  check('a landed node reads its readback: held count + the standard, in the pass colour', /kg-gov-rb kg-gov-rb--held">readback conformant<\/span> 9 held · standard <code>teradata\/1\.0<\/code>/.test(held));
  const bad = P._govDraftsHtml({ nodes: [{ ...draft, status: 'deployed', readback: { status: 'nonconformant', held: 7, violated: 1, unverified: 1, violations: ['pi_even_distribution: the fullest AMP holds 300% more than the average on k'] } }], act_enabled: true });
  check('…a violated rule is NAMED under the line, in the danger colour; unverified is counted apart', /kg-gov-rb--violated">readback nonconformant<\/span> 7 held · 1 violated · 1 unverified/.test(bad) && /kg-gov-rb-viol">pi_even_distribution: the fullest AMP/.test(bad));
  check('…a node with no readback shows no line (absence is not a pass)', !/class="kg-gov-draft-readback"/.test(deployed));
  const wireA4 = src.slice(src.indexOf('function _wireGovDeploy('), src.indexOf('function handleDelete('));
  check('the deploy words say the design standard on a preview (rules verified on this target, per-table conformance) and the readback after a landing', /design standard <code>/.test(wireA4) && /rules verified on/.test(wireA4) && /readback: \$\{esc\(rb\.summary\)\}/.test(wireA4) && /readback \$\{rbs\}/.test(wireA4));
  const withAttrs = P._govDraftsHtml({ nodes: [{ ...draft, attributes: [{ leaf: 'lead_id' }, { leaf: 'status' }, { name: 'fitness_db.Lead.owner' }] }], act_enabled: true });
  check('an entity row names its attributes inline (they travel with it, never as rows of their own)', /3 attributes: <code>lead_id<\/code> · <code>status<\/code> · <code>owner<\/code>/.test(withAttrs) && (withAttrs.match(/class="kg-gov-draft"/g) || []).length === 1);
  // C3: a landed node may still land on ANOTHER warehouse — offered only for a driver it has not landed on
  const everywhere = P._govDraftsHtml({ nodes: [{ ...draft, status: 'deployed', landed_on: ['postgresql', 'teradata'] }], act_enabled: true });
  check('…and nothing is owed any more: no Deploy row once every draft has landed on every deployable warehouse', !/id="kg-gov-deploy"/.test(everywhere));
  const onTd = P._govDraftsHtml({ nodes: [{ ...draft, status: 'deployed', landed_on: ['teradata'] }], act_enabled: true });
  check('…a node landed on Teradata only is offered "Land on another warehouse", excluding Teradata (C3)', /data-mode="another" data-landed="">/.test(onTd) === false && /data-mode="another"/.test(onTd) && /<div class="kg-gov-deploy-head">Land on another warehouse<\/div>/.test(onTd));
  // Act II on production: closure_reason drafted beside a Lead landed on Teradata — the owed row
  // hid every Teradata connection ("No … connection yet"): the C3 exclusion belongs to "another"
  const owedBeside = P._govDraftsHtml({ nodes: [{ ...draft, status: 'published', landed_on: ['teradata'] }, { ...draft, name: 'fitness_db.Lead.closure_reason', entity_type: 'attribute' }], act_enabled: true, default_target: { id: 'dbconn_op', why: "the connection this graph's tables were landed through" } });
  check('a draft beside a node landed on Teradata is offered every warehouse — the landed-driver exclusion is for "another" only', /data-mode="owed" data-landed=""/.test(owedBeside) && /Land <code>closure_reason<\/code> on the warehouse/.test(owedBeside));
  check('…the server\'s default target (action_types.default_connection) rides on the row, with its why', /data-default="dbconn_op"/.test(owedBeside) && /data-default-why="the connection this graph&#39;s tables were landed through"/.test(owedBeside));
  check('…and the wiring preselects it among several, saying why', /items\.some\(c => c\.id === box\.dataset\.default\)/.test(src) && /Preselected: /.test(src));
  // a Preview IS the validation act — Act II on production: the server stamped closure_reason
  // validated, the dialog still drew it DRAFTED until a reload. ONE road drawing serves both
  const redrawn = P._govStepperHtml({ status: 'validated', validated_at: '2026-10-13T12:18:34Z', standard: 'teradata/1.0' }, { owed: true, actOn: true });
  check('the road drawn after a preview: drafted done, VALIDATED where it stands, naming the standard and the day',
    /kg-gov-step--done" role="listitem">drafted</.test(redrawn) && /kg-gov-step--now u-tip" role="listitem" data-tip="validated against the design standard teradata\/1\.0 on 2026-10-13">validated</.test(redrawn));
  const wireD = src.slice(src.indexOf('function _wireGovDeploy('), src.indexOf('function handleDelete('));
  check('…a preview redraws the road of every node it validated, in place (never a reload that clears the preview)',
    /info\.validated/.test(wireD) && /steps\.outerHTML = _govStepperHtml\(/.test(wireD) && /const stepper = \(n\) => _govStepperHtml\(n, \{ owed, actOn, publishOwed \}\)/.test(src)
    && /data-owed="\$\{owed \? 1 : 0\}"/.test(src));
  const offAgain = P._govDraftsHtml({ nodes: [{ ...draft, status: 'deployed', landed_on: ['teradata'] }], act_enabled: false });
  check('…never with the master off', !/id="kg-gov-deploy"/.test(offAgain));
  const twoWarehouses = P._govDraftsHtml({ nodes: [{ ...draft, status: 'deployed', deploy_receipt: { receipt_id: 'mc_1', landing: 'fitness_db', signed: true, identifier: 'fitness_db.Lead' },
    realisations: { teradata: { table: 'fitness_db.Lead', receipt: { receipt_id: 'mc_1', signed: true } }, postgresql: { table: 'UDERIA_EXT_fitness_db.Lead', readback: { status: 'conformant' }, receipt: { receipt_id: 'mc_2', signed: true } } }, landed_on: ['postgresql', 'teradata'] }], act_enabled: true });
  check('…a node landed on two warehouses names the second one under its receipt line, with its own receipt and readback', /also on PostgreSQL: <code>UDERIA_EXT_fitness_db\.Lead<\/code> · receipt <code>mc_2<\/code> · sealed · readback conformant/.test(twoWarehouses));
  const wire = src.slice(src.indexOf('function _wireGovDeploy('), src.indexOf('function handleDelete('));
  check('the dialog wires the act through the REST face, dry-run first; Land lands only the target a preview showed', /knowledge-graph\/authored\/deploy/.test(wire) && /dry_run: true/.test(wire) && /dry_run: false/.test(wire) && /previewed !== sel\.value/.test(wire) && /_wireGovDeploy\(card, kgId, load\)/.test(src));
  check('the targets are the caller\'s deployable database connections (DEPLOY_DRIVERS), and their absence is said in words', /database-connections/.test(wire) && /DEPLOY_DRIVERS\.includes\(/.test(wire) && /database connection yet/.test(wire) && /box\.remove\(\)/.test(wire));

  // A5 — the Extensions row: one line per extension, its trust reading with the mover NAMED,
  // and only the acts that are OWED (publish = a person's act; sign = after publish)
  const pubNode = { ...draft, status: 'published', published_at: '2026-10-07T00:00:00Z', published_by: 'u1', published_by_label: 'you' };
  const ext = (o) => ({ extension_id: 'ext-u1', mine: true, label: 'your extension', published: 1, unpublished: 1, can_publish: true, unlanded: 0, trust: { state: 'unsigned', drift: [] }, ...o });
  const both = P._govDraftsHtml({ nodes: [pubNode, draft], act_enabled: true, extensions: [ext({}), ext({ extension_id: 'ext-u2', mine: false, label: 'extension by bob', published: 2, unpublished: 0, can_publish: false, trust: { state: 'changed', drift: ['base moved'] } })] });
  check('the caller\'s own extension heads the dialog; each OTHER extension is a row naming whose layer it is', /class="kg-gov-sec-head" data-extension-id="ext-u1"><span class="kg-gov-sec-title">Your extension<\/span>/.test(both) && (both.match(/class="kg-gov-ext"/g) || []).length === 1 && /extension by bob/.test(both) && /Other extensions on this graph \(1\)/.test(both));
  check('…its acts are offered in ONE place, Next — ONE act at a time, the first the road owes; what follows is named, never a second button',
    /<div class="kg-gov-next"><div class="kg-gov-next-head">Next<\/div>/.test(both) && /id="kg-gov-deploy"/.test(both) && !/<button type="button" class="kg-gov-ext-publish"/.test(both)
    && /<span class="kg-gov-ext-next">then publish 1 entity, then sign the extension<\/span>/.test(both));
  check('the reading names the MOVER on a changed extension (never just amber)', /kg-gov-rb--violated kg-gov-ext-trust">changed · base moved</.test(both));
  const landedOnly = P._govDraftsHtml({ nodes: [pubNode, { ...draft, status: 'deployed' }], act_enabled: true, extensions: [ext({})] });
  check('Publish is offered on the caller\'s own extension once nothing is left to land — the Sign it then owes is named, not offered', (landedOnly.match(/<button type="button" class="kg-gov-ext-publish"/g) || []).length === 1 && /data-extension-id="ext-u1">Publish 1 entity</.test(landedOnly)
    && !/<button type="button" class="kg-gov-ext-sign"/.test(landedOnly) && /then sign the extension/.test(landedOnly));   // said in the vocabulary: what is published, not "drafts"
  // the publish-order rule (kgstore.types.awaits_landing, read from the server's `unlanded`):
  // while a node of the extension has not landed, Publish is not the act owed — Land is
  const waiting = P._govDraftsHtml({ nodes: [pubNode, { ...draft, entity_type: 'attribute', name: 'fitness_db.Lead.closure_reason' }], act_enabled: true, extensions: [ext({ unlanded: 1 })] });
  check('an extension with an unlanded node offers NO Publish — it says what is owed first, in words', !/class="kg-gov-ext-publish"/.test(waiting) && /<span class="kg-gov-ext-next">then publish 1 attribute — once it has landed, then sign the extension<\/span>/.test(waiting) && /Land <code>closure_reason<\/code> on the warehouse/.test(waiting));
  check('…the dialog reads the SERVER\'s rule, never a client copy of it', /const unlanded = \(x\.unlanded \|\| 0\) > 0/.test(src) && !/\['draft', 'validated'\]\.includes/.test(src.slice(src.indexOf('const extInfo'), src.indexOf('const signBtn'))));
  check('the Govern dialog is not a "-card" (the global card rule made it translucent and lifted it on hover) and paints a solid surface',
    !/class="kg-gov-card"/.test(src) && /class="kg-gov-dialog"/.test(src) && /class="kg-gov-dialog" style="[^"]*background-color:var\(--bg-primary/.test(src));
  const signOwed = P._govDraftsHtml({ nodes: [pubNode], act_enabled: true, extensions: [ext({ unpublished: 0 })] });
  check('Sign is offered on a published extension that is not signed — the caller\'s own when nothing precedes it, and another extender\'s on its row', (signOwed.match(/<button type="button" class="kg-gov-ext-sign"/g) || []).length === 1 && (both.match(/<button type="button" class="kg-gov-ext-sign"/g) || []).length === 1);
  const signed = P._govDraftsHtml({ nodes: [pubNode], act_enabled: true, extensions: [ext({ unpublished: 0, trust: { state: 'trusted', drift: [] } })] });
  // anchor on rendered BUTTONS — the class names also occur in the dialog's own <style> block
  const acts = (h) => (h.match(/<button type="button" class="kg-gov-ext-(publish|sign)"/g) || []).length;
  check('a signed extension with nothing left to publish offers no act at all (Rule 8.5)', acts(signed) === 0 && /kg-gov-rb--held kg-gov-ext-trust">signed</.test(signed));
  const notApprover = P._govDraftsHtml({ nodes: [draft], act_enabled: true, extensions: [ext({ published: 0, can_publish: false })] });
  check('a caller who may not publish is offered no Publish (the REST would refuse it)', acts(notApprover) === 0);
  const masterOff = P._govDraftsHtml({ nodes: [pubNode, draft], act_enabled: false, extensions: [ext({})] });
  check('with the master off the extension reads, but offers no act (and no Next)', /Your extension/.test(masterOff) && acts(masterOff) === 0 && !/kg-gov-next/.test(masterOff.replace(/<style>[\s\S]*<\/style>/, '')));
  check('a published node\'s road marks published done, naming when and by whom', /kg-gov-step--done u-tip[^>]*data-tip="published on 2026-10-07 by you[^"]*"[^>]*>published</.test(both)
    && !/kg-gov-step--now[^>]*>published</.test(both));
  check('a draft\'s published step names the act that reaches it, a person\'s', /data-tip="published from Next, above, once it has landed — a person&#39;s act, never an agent&#39;s"/.test(both));
  check('no graph with extensions → no Extensions row', !/id="kg-gov-exts"/.test(P._govDraftsHtml({ nodes: [draft], act_enabled: true })));
  const wireP = src.slice(src.indexOf('function _wireGovPublish('), src.indexOf('function _wireGovDeploy('));
  check('the acts go through the REST faces: publish and the extension sign-off', /authored\/publish/.test(wireP) && /trust\/ontology-extension\//.test(wireP) && /_wireGovPublish\(card, kgId, load\)/.test(src));
  check('…and announce the authored layer changed, then re-read the dialog', /UderiaBus\.announce\('knowledge_graph', 'updated', \[kgId\], \{ aspect: 'authored' \}\)/.test(wireP) && /await reload\(\)/.test(wireP));
}

console.log('[§5] the inspector draws an authored node dashed and says so');
{
  const src = readFileSync('components/builtin/knowledge_graph/renderer.js', 'utf8');
  check('a node on the authored layer is stroked dashed', /stroke-dasharray', d => \(\(d\.properties \|\| \{\}\)\.layer === 'authored'\) \? '5 3' : null\)/.test(src));
  check('…and its tooltip names the layer, its state and that it is not yet realised', /authored layer · \$\{st\}/.test(src) && /declared, not yet realised/.test(src));
}

console.log('[§6] the proposal row\'s CSS is the answer surface\'s (tokens only)');
{
  const css = readFileSync('static/css/answer.css', 'utf8');
  const block = css.slice(css.indexOf('.kg-proposal {'));
  check('the row body is styled with theme tokens', /\.kg-proposal-foot \.ans-act/.test(block) && !/#[0-9a-f]{6}(?![^;]*var\()/i.test(block.replace(/var\([^)]*\)/g, '')));
  check('the findings read in the warn tone, the act in the accent', /\.kg-proposal-findings li[^}]*--trust-warn/.test(block) && /\.kg-proposal-foot \.ans-act[^}]*--teradata-orange/.test(block));
}

await new Promise(r => realTimeout(r, 20));
console.log(`\n${FAIL === 0 ? 'ALL PASS' : 'FAILURES'} — ${PASS} assertions, ${FAIL} failures`);
process.exit(FAIL ? 1 : 0);
