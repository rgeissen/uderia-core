// PROP-006 phase 2 — a multi-server agent's Live Status tool steps name the MCP server they went
// to, live AND after a reload: the Optimize lane's result `mcp_source` and the ReAct lane's event
// `source` render through ONE function (`mcpSourceKv`); a single-server step renders nothing new.
// Executes the shipped function; pins every card that must call it.
//
// Run: node test/test_mcp_source_ui.mjs
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { installDom, El } from './dom_stub.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const UI = fs.readFileSync(path.join(ROOT, 'static/js/ui.js'), 'utf8');
let pass = 0, fail = 0;
const check = (label, cond, detail = '') => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}${detail ? ' — ' + detail : ''}`); } };
const extractFn = (src, name) => {
    const i = src.indexOf(`function ${name}(`); if (i < 0) throw new Error(`no ${name}`);
    let depth = 0, j = src.indexOf('{', i);
    for (; j < src.length; j++) { if (src[j] === '{') depth++; else if (src[j] === '}') { depth--; if (!depth) break; } }
    return src.slice(i, j + 1);
};
const escapeHtml = new Function(`${extractFn(UI, 'escapeHtml')}; return escapeHtml;`)();
const _chipSrc = `const _MCP_GENERIC_WORDS = new Set(['mcp', 'server', 'servers', 'srv', 'the']); ${extractFn(UI, 'mcpServerShortLabel')} ${extractFn(UI, '_ensureMcpChipCss')} ${extractFn(UI, 'mcpServerChip')} ${extractFn(UI, 'mcpCategoryLabel')} ${extractFn(UI, 'mcpCategoryServer')} ${extractFn(UI, 'mcpGroupCategories')}`;
const { shortLabel, chip, catServer, group, catLabel } = new Function('escapeHtml', `${_chipSrc}; return { shortLabel: mcpServerShortLabel, chip: mcpServerChip, catServer: mcpCategoryServer, group: mcpGroupCategories, catLabel: mcpCategoryLabel };`)(escapeHtml);
const kv = new Function('escapeHtml', 'mcpServerChip', `${extractFn(UI, 'mcpSourceKv')}; return mcpSourceKv;`)(escapeHtml, chip);

console.log('[1] the renderer, executed');
check('no source → nothing (every single-server step)', kv(undefined) === '' && kv(null) === '' && kv({}) === '' && kv('x') === '');
const q = kv({ server_id: 'srvB', server: 'CRM', alias: 'crm', reason: 'qualified' });
check('a qualified call: the server name and the reason with its short name',
      q.includes('>Server<') && q.includes('CRM') && q.includes('the model called this server') && q.includes('crm__'), q);
const p = kv({ server_id: 'srvA', server: 'Warehouse', alias: null, reason: 'primary' });
check('a plain call: the first server, said as such', p.includes('Warehouse') && p.includes('the first server'), p);
check('the server id rides the cell (a key for tests and the lineage)', q.includes('data-mcp-server="srvB"'));
const x = kv({ server_id: 's"1', server: '<img src=x onerror=1>', alias: '<b>', reason: 'qualified' });
check('every stamped value is escaped', !x.includes('<img') && !x.includes('<b>') && !x.includes('"s"1"'), x);
check('an unnamed server falls back to its id', kv({ server_id: 'srv9' }).includes('srv9'));

console.log('[2] every card that shows a tool step calls it');
const calls = (UI.match(/\$\{mcpSourceKv\(details\.source\)\}/g) || []).length;
check('the ReAct cards — live completed update, invoked, reloaded completed — each render the source', calls === 3, String(calls));
const react = UI.slice(UI.indexOf("case 'conversation_tool_invoked': {"), UI.indexOf("case 'conversation_tool_rewrite': {"));
check('…the invoked and completed step renderers are among them', (react.match(/mcpSourceKv\(details\.source\)/g) || []).length === 2);
const live = UI.slice(UI.indexOf("if (type === 'conversation_tool_completed') {"), UI.indexOf("// Mark previous active step as completed and stop icon animations"));
check('…and the live update of the executing step', live.includes('mcpSourceKv(details.source)'));
installDom();
const append = new Function('mcpSourceKv', `${extractFn(UI, 'appendMcpSource')}; return appendMcpSource;`)(kv);
const SRC = { server_id: 'srvB', server: 'CRM', alias: 'crm', reason: 'qualified' };
{
    const el = new El('div');
    append(el, 'Tool Execution Result', { status: 'success', results: [], mcp_source: SRC });
    const row = el.children[0];
    check('an Optimize tool result step gains ONE server row carrying the rendered cells',
          el.children.length === 1 && row && String(row.innerHTML || '').includes('CRM') && String(row.className).includes('status-kv-grid'),
          JSON.stringify({ n: el.children.length, html: row && row.innerHTML }));
    const e2 = new El('div'); append(e2, 'Tool Execution Error', { status: 'error', mcp_source: SRC });
    check('…an error step too', e2.children.length === 1);
    const e3 = new El('div'); append(e3, 'Tool Execution Intent', { mcp_source: SRC });
    check('…never any other step', e3.children.length === 0);
    const e4 = new El('div'); append(e4, 'Tool Execution Result', { status: 'success', results: [] });
    check('…nor a result that names no server (every single-server step)', e4.children.length === 0);
    const e5 = new El('div'); append(e5, 'Tool Execution Result', null); append(null, 'Tool Execution Result', { mcp_source: SRC });
    check('…and a missing result or element never throws', e5.children.length === 0);
}
const std = extractFn(UI, '_renderStandardStep');
check('_renderStandardStep calls it on every step (live and the reloaded trace share this renderer)',
      std.includes('appendMcpSource(stepEl, step, details);'));
const hist = UI.slice(UI.indexOf('step: `Tool Execution Result`'), UI.indexOf('step: `Tool Execution Result`') + 400);
check('the reloaded Optimize trace hands the persisted result to that renderer', hist.includes('details: traceEntry.result') && hist.includes('_renderStandardStep'));

console.log('[2b] the server chip, executed');
{
    const A = { server_id: 'srvA', name: 'Teradata MCP - fitness_manager', alias: null, state: 'classified' };
    const B = { server_id: 'srvB', name: 'Teradata MCP - nibrs_source_agent', alias: 'nibrs', state: 'classified' };
    const C = { server_id: 'srvC', name: 'Lake', alias: 'lake', state: 'not_classified', reason: 'classified in full mode, the agent uses light' };
    check('an additional server is labelled by its alias', shortLabel(B, [A, B]) === 'nibrs');
    check('the first server by the words its peers do not share', shortLabel(A, [A, B]) === 'fitness_manager', shortLabel(A, [A, B]));
    check('…without peers, by the part after the last " - "', shortLabel(A) === 'fitness_manager');
    check('…a long name is cut', shortLabel({ server_id: 'x', name: 'A very long server name without separators' }).length <= 18);
    const cb = chip(B, [A, B]);
    check('the chip carries the server id, its label and the platform tooltip (full name + role)',
          cb.includes('data-mcp-chip="srvB"') && cb.includes('>nibrs<') && cb.includes('u-tip') && cb.includes('Teradata MCP - nibrs_source_agent') && cb.includes('nibrs__name'), cb);
    check('…a monochrome glyph (currentColor), no native title', cb.includes('stroke="currentColor"') && !/\stitle=/.test(cb));
    const cc = chip(C, [A, C]);
    check('a server not classified here is drawn dashed and says so, with the reason',
          cc.includes('is-unclassified') && cc.includes('Not classified on this instance') && cc.includes('full mode'), cc);
    check('no server → no chip', chip(null) === '' && chip({}) === '');
    check('every value in the chip is escaped', !chip({ server_id: 'a"b', name: '<script>', alias: '<x>' }).includes('<script>'));
    const INFO = { servers: [A, B], categoryServers: { 'Base': 'srvA', 'nibrs: Base': 'srvB' } };
    const t1 = catServer('nibrs: Base', INFO);
    check('an additional server\'s category: its server, the alias prefix gives way to the chip', t1 && t1.server === B && t1.label === 'Base', JSON.stringify(t1));
    const t0 = catServer('Base', INFO);
    check('a first-server category: its server, its label unchanged', t0 && t0.server === A && t0.label === 'Base');
    check('an injected category (System Tools) belongs to no server', catServer('System Tools', INFO) === null);
    check('a single-server agent (no info) → nothing', catServer('Base', null) === null);
    const INFO2 = { servers: [A, B], categoryServers: { 'All Tools': 'srvA', 'nibrs: All Tools': 'srvB' } };
    check('a light-classified generic category reads "MCP Tools" — the chip says which server', catServer('All Tools', INFO2).label === 'MCP Tools' && catServer('nibrs: All Tools', INFO2).label === 'MCP Tools');
    check('the Server row now leads with the chip', kv({ server_id: 'srvB', server: 'X', alias: 'nibrs', reason: 'qualified' }).includes('data-mcp-chip="srvB"'));
}

console.log('[2c] the panel groups tabs by server, one chip per group');
{
    const A = { server_id: 'srvA', name: 'Teradata MCP - fitness_manager', alias: null, state: 'classified' };
    const B = { server_id: 'srvB', name: 'Teradata MCP - nibrs_source_agent', alias: 'nibrs', state: 'classified' };
    const C = { server_id: 'srvC', name: 'Lake', alias: 'lake', state: 'not_classified' };
    const INFO = { servers: [A, B, C], categoryServers: { 'Base': 'srvA', 'Quality': 'srvA', 'nibrs: Base': 'srvB', 'nibrs: Quality': 'srvB' } };
    const g = group(['nibrs: Base', 'Component Tools', 'Base', 'nibrs: Quality', 'Quality', 'System Tools'], INFO);
    check('categories of no server come first, in their order', g[0].category === 'Component Tools' && g[1].category === 'System Tools', JSON.stringify(g.slice(0, 2)));
    check('then each server\'s group, in the agent\'s order (the first server first)',
          g.slice(2).map(x => x.category).join('|') === 'Base|Quality|nibrs: Base|nibrs: Quality|__unclassified__srvC', g.map(x => x.category).join('|'));
    check('ONE group start per server (where the chip is drawn)', g.filter(x => x.groupStart).map(x => x.server.server_id).join(',') === 'srvA,srvB,srvC');
    check('an additional server\'s labels lose the alias prefix', g.find(x => x.category === 'nibrs: Base').label === 'Base');
    const u = g.find(x => x.unclassified);
    check('a server not classified here is a group of its own, never omitted', u && u.server === C && u.label === 'not classified', JSON.stringify(u));
    const single = group(['Base', 'System Tools'], null);
    check('a single-server agent: categories unchanged, no groups', single.map(x => x.category).join('|') === 'Base|System Tools' && !single.some(x => x.groupStart || x.server));
    const gen = group(['All Tools', 'System Tools'], null);
    check('…its generic category shows as "MCP Tools" (the key is kept for lookups), the injected ones as stored',
          gen[0].category === 'All Tools' && gen[0].label === 'MCP Tools' && gen[1].label === 'System Tools', JSON.stringify(gen));
    check('the generic keys of every type map; any other category reads as stored; a non-string passes through',
          catLabel('All Prompts') === 'MCP Prompts' && catLabel('All Resources') === 'MCP Resources' && catLabel('Quality') === 'Quality' && catLabel(undefined) === undefined);
    check('the stored key is never renamed by the label (display only)', !/generic\[category\] = |category = generic/.test(extractFn(UI, 'mcpCategoryLabel')));
    check('a server with no categories and classified draws nothing', !group(['Base'], { servers: [A, { ...B, state: 'classified' }], categoryServers: { Base: 'srvA' } }).some(x => x.server && x.server.server_id === 'srvB'));
}
{
    const CAP = fs.readFileSync(path.join(ROOT, 'static/js/handlers/capabilitiesManagement.js'), 'utf8');
    const rp = extractFn(CAP, 'renderResourcePanel');
    check('the panel reads the servers only for tools and prompts', rp.includes("(type === 'tools' || type === 'prompts') ? state.resourceData.mcpServers : null"));
    check('…orders its tabs with mcpGroupCategories', rp.includes('UI.mcpGroupCategories(Object.keys(data), _srvInfo)'));
    check('…draws the shared chip once, at a group start', rp.includes('if (entry.groupStart && entry.server && UI.mcpServerChip)') && rp.includes("group.className = 'mcp-group'"));
    check('…labels each tab as TEXT (never as markup): fillCategoryTab sets textContent and appends the figure as an element',
          rp.includes('UI.fillCategoryTab(categoryTab, entry.label, entry.unclassified ? null : data[category]);')
          && /tab\.textContent = label;/.test(fs.readFileSync(path.join(ROOT, 'static/js/handlers/resourceBadge.js'), 'utf8'))
          && !/innerHTML/.test(fs.readFileSync(path.join(ROOT, 'static/js/handlers/resourceBadge.js'), 'utf8')));
    const EH = fs.readFileSync(path.join(ROOT, 'static/js/eventHandlers.js'), 'utf8');
    check('the two plain loaders (capabilitiesManagement, eventHandlers) label their tabs through mcpCategoryLabel too',
          (CAP.match(/UI\.fillCategoryTab\(categoryTab, UI\.mcpCategoryLabel\(category\), data\[category\]\);/g) || []).length === 1
          && (EH.match(/UI\.fillCategoryTab\(categoryTab, UI\.mcpCategoryLabel\(category\), data\[category\]\);/g) || []).length === 1);
    check('no tab anywhere shows the raw category key', !/categoryTab\.textContent = category;/.test(CAP) && !/categoryTab\.textContent = category;/.test(EH) && !/categoryTab\.textContent = category;/.test(UI));
    check('…and gives a not-classified server a note instead of an empty list', rp.includes('if (entry.unclassified)') && rp.includes('is not classified on this instance'));
    const MAIN = fs.readFileSync(path.join(ROOT, 'static/js/main.js'), 'utf8');
    check('main.js stores the servers from the profile resources endpoint', MAIN.includes('panelServers = Array.isArray(data.servers) && data.servers.length') && MAIN.includes('state.resourceData.mcpServers = panelServers;'));
    check('…and clears them for every other load (let panelServers = null)', MAIN.includes('let panelServers = null;'));
}

console.log('[2d] every touched file parses as the ES module the browser loads');
{
    const { spawnSync } = await import('node:child_process');
    for (const f of ['static/js/ui.js', 'static/js/main.js', 'static/js/handlers/capabilitiesManagement.js']) {
        const r = spawnSync(process.execPath, ['--input-type=module', '--check'], { input: fs.readFileSync(path.join(ROOT, f)) });
        check(`${f} is a valid ES module (a plain --check misses a duplicate declaration)`, r.status === 0, String(r.stderr).split('\n').slice(0, 3).join(' '));
    }
}

console.log('[3] theme');
const fn = extractFn(UI, 'mcpSourceKv') + extractFn(UI, 'mcpServerChip') + extractFn(UI, '_ensureMcpChipCss');
check('theme variables only — no Tailwind colour class, no bare colour', !/text-(gray|slate|amber|emerald|rose|red|green|blue)-\d/.test(fn) && !/#[0-9a-fA-F]{3,6}\b/.test(fn) && fn.includes('var(--text-muted)'));

console.log(`\n${fail ? 'FAILED' : 'ALL PASS'} — ${pass} passed, ${fail} failed  (${pass + fail} assertions) — MCP server named on tool steps`);
process.exit(fail ? 1 : 0);
