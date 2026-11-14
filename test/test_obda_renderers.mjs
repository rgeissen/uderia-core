#!/usr/bin/env node
/**
 * OBDA-READ / OBDA-MODEL — the conversation faces beside OBDA-ACT: the split panels, their
 * sections, the Workspace sections, and the wiring every split panel needs (the aside under
 * #chat-canvas-split, the panel map, the renderer registry, the session-switch close).
 *
 * Mirrors test_action_renderer.mjs §P/§W for the two new planes. No server, no network.
 */
import { readFileSync } from 'node:fs';

let pass = 0, fail = 0;
const check = (label, cond, detail = '') => { if (cond) { pass++; console.log(`  ✓ ${label}`); } else { fail++; console.log(`  ✗ ${label}${detail ? ' — ' + detail : ''}`); } };

const readSrc = readFileSync('components/builtin/obda_read/renderer.js', 'utf8');
const modelSrc = readFileSync('components/builtin/obda_model/renderer.js', 'utf8');

// the pure section builders, lifted out of each module (no DOM needed)
function lift(src, names) {
  const body = src
    .replace(/^export (async )?function/gm, '$1function')
    .replace(/^\/\/ ── install once[\s\S]*$/m, '')          // the DOM-time installer
    .replace(/^if \(typeof document !== 'undefined'[\s\S]*$/m, '');
  return new Function('document', 'window', 'localStorage', 'fetch', body + `\nreturn { ${names.join(', ')} };`)(undefined, {}, undefined, undefined);
}

console.log('[§R] the OBDA-READ panel');
{
  const R = lift(readSrc, ['panelHtml', 'owedWord', 'trustWord']);
  const html = R.panelHtml({
    products: [
      { product: 'support_load', version: '1', validated: true, signable: true, trust: { state: 'trusted' }, measure_names: 'ticket_count', member_entities: 'Tickets, Customers' },
      { product: 'fitness_sales', version: '2', validated: false, trust: { state: 'unsigned' } },
      { product: 'archive', version: '1', validated: true, signable: true, trust: { state: 'changed' } },
    ],
    reads: [{ product: 'support_load', version: '1', row_count: 32, trust_state: 'trusted', turn: 3 }, { product: 'archive', rejected: true, turn: 4 }],
    surface: ['support_load'], focus: 'support_load',
  });
  const order = [...html.matchAll(/data-obr-product="([^"]+)"/g)].map(m => m[1]);
  check('two sections in order: the products, then this conversation\'s governed reads',
    html.indexOf('Data products') < html.indexOf('Governed reads in this conversation'));
  check('on-surface products sort first', order[0] === 'support_load', order.join(','));
  check('a product reads its trust and its surface standing in words',
    /signed · on this agent’s surface/.test(html) && /not signed · not on this agent’s surface/.test(html));
  check('the focused product is marked', /class="obr-item is-focus/.test(html));
  check('every row owes exactly one act, as the Designer offers it',
    R.owedWord({ validated: false }) === 'Validate' && R.owedWord({ validated: true, signable: true, trust: { state: 'unsigned' } }) === 'Sign'
    && R.owedWord({ validated: true, signable: true, trust: { state: 'changed' } }) === 'Re-sign' && R.owedWord({ validated: true, trust: { state: 'trusted' } }) === 'Open');
  check('a governed read names rows and the trust at consumption; a rejected one says so',
    /32 rows · signed at consumption · turn 3/.test(html) && /rejected by the contract/.test(html));
  check('defining a product is offered where the products are listed; owed (accented) when there is none',
    /class="obr-define">Define a product/.test(html) && /obr-define is-owed/.test(R.panelHtml({ products: [] })));
  check('empty sections say so in words', /No data product on this agent’s knowledge graph/.test(R.panelHtml({})) && /No governed read in this conversation yet/.test(R.panelHtml({})));
  check('a note replaces the empty words when the catalog could not be read', /could not be read/.test(R.panelHtml({ note: 'The data products could not be read.' })));
  check('the trust words are the capsule\'s', R.trustWord({ state: 'changed' }) === 'changed since signed' && R.trustWord({ state: 'unavailable' }) === 'trust unavailable');
  check('no emoji in the renderer', !/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(readSrc));
  check('the panel re-reads when the Products Designer announces a change', /uderia:products-changed/.test(readSrc)
    && /uderia:products-changed/.test(readFileSync('static/js/handlers/knowledgeGraphPanelHandler.js', 'utf8')));
}

console.log('[§M] the OBDA-MODEL panel');
{
  const M = lift(modelSrc, ['panelHtml', 'owedWord', 'stageWords']);
  const html = M.panelHtml({
    concepts: [
      { node: { name: 'fitness_db.Lead', status: 'draft', entity_type: 'entity', decision: { rule: 'independent_lifecycle' } }, trust: '' },
      { node: { name: 'Customers.loyalty_tier', status: 'published', entity_type: 'attribute' }, trust: 'changed' },
      { node: { name: 'Customers.region', status: 'published', entity_type: 'attribute' }, trust: 'trusted' },
    ],
    proposals: [{ name: 'Lead', drafted: true, summary: 'Proposed entity Lead — independent lifecycle.' }],
    focus: 'fitness_db.Lead', kgName: 'Fitness',
  });
  check('two sections in order: the extension, then this conversation\'s proposals',
    html.indexOf('Your extension · Fitness') < html.indexOf('Proposals in this conversation'));
  check('a concept reads its kind, its stage and the rule that decided it', /entity · draft · independent lifecycle/.test(html));
  check('a published concept names the trust act still owed', /published · changed/.test(html) && /published · signed/.test(html));
  check('the focused concept is marked', /class="obm-item is-focus/.test(html));
  check('every row owes exactly one act on the road',
    M.owedWord({ status: 'draft' }) === 'Land it' && M.owedWord({ status: 'deployed' }) === 'Publish'
    && M.owedWord({ status: 'published' }, 'changed') === 'Sign the extension' && M.owedWord({ status: 'published' }, 'trusted') === 'Open');
  check('a draft row opens its graph with drafts, a published one the model\'s own view',
    /data-obm-published="0"/.test(html) && /data-obm-published="1"/.test(html));
  check('a proposal routes to its row in the answer, and says when it drafted', /data-obm-proposal="0"/.test(html) && /drafted/.test(html));
  check('Govern is offered where the extension is listed; owed (accented) while something is unpublished or unsigned',
    /obm-govern is-owed">Open Govern/.test(html) && !/is-owed/.test(M.panelHtml({ concepts: [{ node: { name: 'x', status: 'published' }, trust: 'trusted' }] })));
  check('empty sections say so in words', /Nothing added to this agent’s model yet/.test(M.panelHtml({})) && /No proposal in this conversation yet/.test(M.panelHtml({})));
  check('no emoji in the renderer', !/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(modelSrc));
  check('the panel re-reads when the authored layer changes', /uderia:kg-authored-changed/.test(modelSrc));
}

console.log('[§W] wiring');
{
  const idx = readFileSync('templates/index.html', 'utf8');
  for (const [id, name] of [['obda-read', 'OBDA-READ'], ['obda-model', 'OBDA-MODEL']]) {
    check(`the ${name} panel aside exists under the split row, carries the component name and a named close`,
      new RegExp(`<aside id="${id}-split-panel" class="sp-panel"`).test(idx)
      && new RegExp(`id="${id}-split-title" class="sp-title">${name}<`).test(idx)
      && new RegExp(`id="${id}-split-close"[^>]*aria-label="Close the ${name} panel"`).test(idx));
  }
  const ch = readFileSync('static/js/handlers/componentHandler.js', 'utf8');
  check('both panels take the manifest accent (the panel map)', /obda_read:\s+'obda-read-split-panel'/.test(ch) && /obda_model:\s+'obda-model-split-panel'/.test(ch));
  const reg = readFileSync('static/js/componentRenderers.js', 'utf8');
  check('the registry imports both renderers (which install their listeners at startup)',
    /obda_read: 'renderObdaRead'/.test(reg) && /obda_model: 'renderObdaModel'/.test(reg) && /export const renderersReady = Promise\.allSettled/.test(reg));
  const sm = readFileSync('static/js/handlers/sessionManagement.js', 'utf8');
  check('a session switch closes both', /closeObdaReadPanel\(\)/.test(sm) && /closeObdaModelPanel\(\)/.test(sm));
  const ws = readFileSync('static/js/handlers/workspacePanelHandler.js', 'utf8');
  const entry = (k) => { const at = ws.indexOf(`\n    ${k}: {`); return at < 0 ? '' : ws.slice(at, ws.indexOf('\n    },\n', at)); };
  const r = entry('obda_read'), m = entry('obda_model');
  check('the Workspace lists the agent\'s data products and opens the OBDA-READ panel on one',
    /\/api\/v1\/data-products\?profile_id=/.test(r) && /openObdaReadPanel\(\{ profile_id: profileId, focus: it\.product \}\)/.test(r)
    && /createTitle: 'Define a product'/.test(r) && /noDelete: true/.test(r));
  check('the Workspace lists the person\'s concepts and opens the OBDA-MODEL panel on one',
    /workspaceConcepts\(profileId\)/.test(m) && /openObdaModelPanel\(\{ profile_id: profileId, focus: it\.name \}\)/.test(m)
    && /createTitle: 'Open Govern on this agent’s graph'/.test(m) && /noDelete: true/.test(m));
  check('the Workspace sections are alphabetical by the name they show (the three OBDA planes sit together by name)',
    /\.sort\(\(a, b\) => String\(a\.display_name \|\| a\.component_id \|\| ''\)\.localeCompare\(String\(b\.display_name \|\| b\.component_id \|\| ''\)\)\)/.test(ws)
    && !/\n        order: \d+,/.test(ws));
  check('the graph\'s drafts trailer steps aside where the OBDA-MODEL section lists the same concepts',
    /typeof _enabledWorkspaceIds !== 'undefined' && _enabledWorkspaceIds\.has\('obda_model'\)\) return null;/.test(ws) && /_enabledWorkspaceIds = new Set\(enabled\.map/.test(ws));
  check('the Products Designer and the graph opener are reachable from a renderer module',
    /window\.openKgProductsDialog = /.test(readFileSync('static/js/handlers/knowledgeGraphPanelHandler.js', 'utf8'))
    && /window\.openKnowledgeGraphFromWorkspace = /.test(ws));
  // the split-panel structural gate covers resize + focus ring for any aside under the row; the
  // per-agent catalogs are the backend's (test_obda_components §6)
}

console.log(`\n${fail ? 'FAIL' : 'ALL PASS'} — ${pass + fail} assertions (${pass} passed, ${fail} failed)`);
process.exit(fail ? 1 : 0);
