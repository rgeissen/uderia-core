// Gate — the per-agent MCP binding dialog's MODEL and CHOICE derivations
// (static/js/handlers/agentPackHandler.js: _mcpBindingModel / _mcpBindingChoice).
//
// No browser, no server: the two pure functions are extracted verbatim from the
// shipped module and executed here. The dialog itself only renders these; the
// contract they carry is:
//   * default = ONE selection applied to every agent (explicit map + fallback id),
//   * "assign individually" is OFFERED only when the pack's agents reference
//     DIFFERENT server names,
//   * per-agent mode binds each agent to its own selection and never invents a
//     binding for an empty row,
//   * the "for all" default is the server the resolved agents agree on.
//
// Run: node test/test_pack_mcp_binding_dialog.mjs
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const src = fs.readFileSync(path.join(ROOT, 'static/js/handlers/agentPackHandler.js'), 'utf8');

function extract(name) {
    const start = src.indexOf(`function ${name}(`);
    if (start < 0) throw new Error(`${name} not found`);
    let depth = 0, i = src.indexOf('{', start);
    for (; i < src.length; i++) {
        if (src[i] === '{') depth++;
        else if (src[i] === '}') { depth--; if (depth === 0) break; }
    }
    return src.slice(start, i + 1);
}
const _mcpBindingModel = new Function(`${extract('_mcpBindingModel')}; return _mcpBindingModel;`)();
const _mcpBindingChoice = new Function(`${extract('_mcpBindingChoice')}; return _mcpBindingChoice;`)();

let pass = 0, fail = 0;
const check = (label, cond) => { if (cond) pass++; else { fail++; console.log('  FAIL: ' + label); } };
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

const servers = [{ id: 'srv-nibrs', name: 'NIBRS' }, { id: 'srv-case', name: 'CASE' }, { id: 'srv-x', name: 'X' }];

// ── model: two different names → per-agent option offered, default = majority ──
const twoNames = [
    { tag: 'NIBRS', mcp_server_name: 'Teradata MCP - nibrs_source_agent', resolved_mcp_server_id: 'srv-nibrs' },
    { tag: 'CASEDF', mcp_server_name: 'Teradata MCP - case_source_agent', resolved_mcp_server_id: null },
];
let m = _mcpBindingModel(twoNames, servers);
check('two distinct names', m.distinctNames.length === 2);
check('per-agent option offered when names differ', m.perAgentOption === true);
check('default-for-all = the resolved agent', m.defaultForAll === 'srv-nibrs');
check('unresolved agents named', eq(m.unresolved, ['CASEDF']));
check('one agent unresolved → opens in single mode (nothing to disagree with)', m.startPerAgent === false);

// ── model: agents auto-matched to DIFFERENT servers → opens per-agent, no "all" default ──
m = _mcpBindingModel([
    { tag: 'NIBRS', mcp_server_name: 'Teradata MCP - nibrs_source_agent', resolved_mcp_server_id: 'srv-nibrs' },
    { tag: 'CASEDF', mcp_server_name: 'Teradata MCP - case_source_agent', resolved_mcp_server_id: 'srv-case' },
], servers);
check('different matches → per-agent start', m.startPerAgent === true && m.perAgentOption === true);
check('different matches → no one-for-all default (a bare Continue cannot rebind an agent)', m.defaultForAll === '');
check('same match on two names → single mode with that default', (() => {
    const mm = _mcpBindingModel([
        { tag: 'A', mcp_server_name: 'X', resolved_mcp_server_id: 'srv-x' },
        { tag: 'B', mcp_server_name: 'Y', resolved_mcp_server_id: 'srv-x' },
    ], servers);
    return mm.startPerAgent === false && mm.perAgentOption === true && mm.defaultForAll === 'srv-x';
})());
check('dialog pre-checks the switch from the model', /id="mcp-bind-per-agent"\$\{model\.startPerAgent \? ' checked' : ''\}/.test(src));

// ── model: one shared name → no per-agent option ──
const oneName = [
    { tag: 'A', mcp_server_name: 'Teradata', resolved_mcp_server_id: 'srv-x' },
    { tag: 'B', mcp_server_name: 'teradata', resolved_mcp_server_id: 'srv-x' },
];
m = _mcpBindingModel(oneName, servers);
check('names compared as given (two spellings = two names)', m.distinctNames.length === 2);
m = _mcpBindingModel([
    { tag: 'A', mcp_server_name: 'Teradata', resolved_mcp_server_id: 'srv-x' },
    { tag: 'B', mcp_server_name: 'Teradata', resolved_mcp_server_id: 'srv-x' },
], servers);
check('one shared name → single selector only', m.perAgentOption === false && m.defaultForAll === 'srv-x');
check('no names at all → single selector, no default', (() => {
    const mm = _mcpBindingModel([{ tag: 'A' }, { tag: 'B' }], servers);
    return mm.perAgentOption === false && mm.defaultForAll === '' && eq(mm.unresolved, ['A', 'B']);
})());
check('a resolved id the user does not own is not a default', (() => {
    const mm = _mcpBindingModel([{ tag: 'A', resolved_mcp_server_id: 'srv-foreign' }], servers);
    return mm.defaultForAll === '' && eq(mm.unresolved, ['A']);
})());
check('rows without a tag are dropped', _mcpBindingModel([{ tag: '' }, { tag: 'A' }], servers).rows.length === 1);

// ── choice: single mode binds every agent explicitly ──
let c = _mcpBindingChoice('single', 'srv-nibrs', {}, twoNames);
check('single: explicit map for every agent', eq(c.bindings, { NIBRS: 'srv-nibrs', CASEDF: 'srv-nibrs' }));
check('single: fallback id = the selection', c.mcpServerId === 'srv-nibrs');
c = _mcpBindingChoice('single', '', {}, twoNames);
check('single with "None": nothing bound, no fallback', eq(c.bindings, {}) && c.mcpServerId === null);

// ── choice: per-agent mode ──
c = _mcpBindingChoice('per-agent', '', { NIBRS: 'srv-nibrs', CASEDF: 'srv-case' }, twoNames);
check('per-agent: each agent its own server', eq(c.bindings, { NIBRS: 'srv-nibrs', CASEDF: 'srv-case' }));
check('per-agent: fallback = first bound agent when no "all" selection', c.mcpServerId === 'srv-nibrs');
c = _mcpBindingChoice('per-agent', 'srv-x', { NIBRS: 'srv-nibrs', CASEDF: '' }, twoNames);
check('per-agent: an empty row is left OUT of the map (falls back to pack-level)', eq(c.bindings, { NIBRS: 'srv-nibrs' }));
check('per-agent: fallback = the "all" selection when present', c.mcpServerId === 'srv-x');
c = _mcpBindingChoice('per-agent', '', { NIBRS: 'srv-nibrs', GHOST: 'srv-x' }, twoNames);
check('per-agent: a selection for an unknown tag is ignored', eq(c.bindings, { NIBRS: 'srv-nibrs' }));

// ── choice: a multi-server agent's additional servers (plan stage 2) ──
const multi = [
    { tag: 'MSOPT', mcp_server_name: 'Sales MCP', resolved_mcp_server_id: 'srv-x',
      additional_servers: [{ alias: 'crm', mcp_server_name: 'CRM MCP', resolved_mcp_server_id: 'srv-case' }] },
    { tag: 'ONE', mcp_server_name: 'Sales MCP', resolved_mcp_server_id: 'srv-x' },
];
c = _mcpBindingChoice('single', 'srv-x', {}, multi, { MSOPT: { crm: 'srv-case' } });
check('multi: the agent binds {primary, <alias>}', eq(c.bindings.MSOPT, { primary: 'srv-x', crm: 'srv-case' }));
check('multi: a single-server agent keeps the plain id', c.bindings.ONE === 'srv-x');
check('multi: "for all" never binds an additional server',
    eq(_mcpBindingChoice('single', 'srv-x', {}, multi, {}).bindings.MSOPT, 'srv-x'));
c = _mcpBindingChoice('per-agent', '', { MSOPT: '' }, multi, { MSOPT: { crm: 'srv-case' } });
check('multi: an unbound first server stays out of the row (falls back to pack-level)',
    eq(c.bindings.MSOPT, { crm: 'srv-case' }));
c = _mcpBindingChoice('per-agent', '', { MSOPT: 'srv-x' }, multi, { MSOPT: { crm: '' } });
check('multi: an empty additional selection is left out', c.bindings.MSOPT === 'srv-x');
check('multi: the fallback id stays the first server, never an additional one',
    _mcpBindingChoice('per-agent', '', { MSOPT: 'srv-x' }, multi, { MSOPT: { crm: 'srv-case' } }).mcpServerId === 'srv-x');
check('dialog renders one row per (agent, alias) and passes them to the choice',
    /class="mcp-bind-extra-select" data-tag=/.test(src)
    && /_mcpBindingChoice\(mode\(\), allSel\.value, perAgent, model\.rows, perAliasNow\(\)\)/.test(src));
check('an additional row is pre-filled ONLY from its own match, never the "for all" choice',
    /optionsHtml\(matched \? x\.resolved_mcp_server_id : ''\)/.test(src));

// ── wiring: the three callers send the explicit map ──
const mk = fs.readFileSync(path.join(ROOT, 'static/js/handlers/marketplaceHandler.js'), 'utf8');
check('import flow sends mcp_bindings', /fd\.append\('mcp_bindings'/.test(src));
check('fork flow sends mcp_bindings', /body\.mcp_bindings = mcpBindings/.test(mk));
check('subscribe flow sends mcp_bindings', /mcp_bindings: mcpBindings/.test(mk));
check('dialog exported for the marketplace', /showMcpBindingDialog: _showMcpBindingDialog/.test(src) && /resolveMarketplaceMcpChoice: _resolveMarketplaceMcpChoice/.test(src));
check('legacy single picker kept as the fallback', /function _showMcpServerPicker\(/.test(src));

console.log(fail ? `FAILED — ${fail} of ${pass + fail}` : `ALL PASS — ${pass + fail} assertions`);
process.exit(fail ? 1 : 0);
