#!/usr/bin/env node
/**
 * The Canvas manages no database connections of its own — it points to the one manager.
 *
 * Components → Canvas carried a second full connection manager (list, form, test, save,
 * delete) over the same Database Connection store that Components → Connectors → Database
 * Connection manages. It is replaced by a pointer, and the canvas's connection picker gains
 * a "Manage connections…" entry to the same place (`window.openPlatformConnector`).
 */
import { readFileSync } from 'node:fs';
import { installDom } from './dom_stub.mjs';

let pass = 0, fail = 0;
const ok = (label, cond, detail = '') => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}${detail ? ' — ' + detail : ''}`); } };

const { body, El } = installDom();
const clicked = [];
const btn = id => { const b = new El('button', { id }); b.click = () => clicked.push(id); body.appendChild(b); return b; };
btn('view-switch-components'); btn('platform-tab-mcp-servers');
globalThis.fetch = async () => ({ ok: true, json: async () => ({ servers: [], settings: [] }) });

// the deep link — the shipped script, executed as the browser runs it
new Function(readFileSync('static/js/handlers/platformConnectorHandler.js', 'utf8'))();
ok('[1] a deep link to a connector\'s detail is published', typeof window.openPlatformConnector === 'function');
window.openPlatformConnector('uderia-database');
ok('[1] it switches to Components, then its Connectors tab', clicked.join() === 'view-switch-components,platform-tab-mcp-servers', clicked.join());
const src = readFileSync('static/js/handlers/platformConnectorHandler.js', 'utf8');
ok('[1] the requested connector is selected once the panel has loaded',
   /if \(_pconnuPendingSelect\) \{ const id = _pconnuPendingSelect; _pconnuPendingSelect = null; _selectServer\(id\); \}/.test(src));
ok('[1] the per-profile assignment of connectors is unchanged (every enabled connector is listed)',
   src.includes('const enabledServers = (serversData.servers || []).filter(s => s.enabled);'));

// the Canvas component detail
const ch = readFileSync('static/js/handlers/componentHandler.js', 'utf8');
ok('[2] the duplicate form and list are gone', !ch.includes('CREDENTIAL_SCHEMA') && !ch.includes('_renderConnectionForm')
   && !ch.includes('_renderConnectionList'));
ok('[2] the Canvas tab never reads or writes connections itself', !ch.includes('/api/v1/canvas/connections'));
ok('[2] …it points to the one manager', ch.includes("window.openPlatformConnector('uderia-database')"));

// the canvas's own connection picker
const cv = readFileSync('components/builtin/canvas/renderer.js', 'utf8');
const build = cv.slice(cv.indexOf('function _buildConnectionItems('), cv.indexOf('function _updateConnTrigger('));
ok('[3] the picker builder appends a manage entry that opens the same manager',
   build.length > 0 && /manage\.addEventListener\('click'[\s\S]*openPlatformConnector\('uderia-database'\)/.test(build)
   && /dropdown\.appendChild\(manage\)/.test(build) && build.includes('Manage connections…') && build.includes('Add a database connection…'));
const rr = readFileSync('src/trusted_data_agent/api/rest_routes.py', 'utf8');
ok('[3] the execute error names where connections are managed (no dead "Credentials tab")',
   !rr.includes('Open the Credentials tab') && rr.includes('Components → Connectors → Database Connection'));

// ── [4] a menu opened from a sub-toolbar is never clipped by it
const sp = readFileSync('static/css/split-panels.css', 'utf8');
const rule = sp.match(/\.sp-panel:has\(\[data-sp-subheader\] \[class\*="dropdown--open"\]\) \[data-sp-subheader\] \{([^}]*)\}/);
ok('[4] while a sub-toolbar menu is open, the toolbar is unclipped and revealed',
   !!rule && /overflow:\s*visible/.test(rule[1]) && /max-height:\s*none/.test(rule[1]) && /pointer-events:\s*auto/.test(rule[1]), rule && rule[1]);
ok('[4] …and the rule comes after the collapse it overrides', !!rule && sp.indexOf(rule[0]) > sp.indexOf('max-height: 0;'));
const openClasses = [...new Set([...cv.matchAll(/classList\.(?:toggle|add)\('([\w-]+--open)'\)/g)].map(m => m[1]))]
    .filter(c => /dropdown|menu|popover/.test(c) && c !== 'canvas-split--open');
ok('[4] every canvas toolbar menu opens with a class the rule matches',
   openClasses.length >= 2 && openClasses.every(c => c.includes('dropdown--open')), openClasses.join(', '));

console.log(`\n${fail ? 'FAILED' : 'ALL PASS'} — ${pass} passed, ${fail} failed  (${pass + fail} assertions)`);
process.exit(fail ? 1 : 0);
