#!/usr/bin/env node
/**
 * A menu opened from a split panel's sub-toolbar is never clipped by it.
 *
 * The auto-hiding sub-toolbar collapses by clipping (overflow: hidden), even when pinned. The
 * canvas connection picker, History and Sources open below that row, so they opened invisibly
 * and a tap landed on the editor (a connection could not be picked). While any
 * `*dropdown--open` menu inside it is open, the sub-toolbar stays revealed and unclipped.
 */
import { readFileSync } from 'node:fs';
let pass = 0, fail = 0;
const ok = (label, cond, detail = '') => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}${detail ? ' — ' + detail : ''}`); } };
const sp = readFileSync('static/css/split-panels.css', 'utf8');
const rule = sp.match(/\.sp-panel:has\(\[data-sp-subheader\] \[class\*="dropdown--open"\]\) \[data-sp-subheader\] \{([^}]*)\}/);
ok('while a sub-toolbar menu is open, the toolbar is unclipped and revealed',
   !!rule && /overflow:\s*visible/.test(rule[1]) && /max-height:\s*none/.test(rule[1]) && /pointer-events:\s*auto/.test(rule[1]), rule && rule[1]);
ok('the rule comes after the collapse it overrides', !!rule && sp.indexOf(rule[0]) > sp.indexOf('max-height: 0;'));
const cv = readFileSync('components/builtin/canvas/renderer.js', 'utf8');
const openClasses = [...new Set([...cv.matchAll(/classList\.(?:toggle|add)\('([\w-]+--open)'\)/g)].map(m => m[1]))]
    .filter(c => /dropdown|menu|popover/.test(c) && c !== 'canvas-split--open');
ok('every canvas toolbar menu opens with a class the rule matches',
   openClasses.length >= 2 && openClasses.every(c => c.includes('dropdown--open')), openClasses.join(', '));
// The flow toolbar is the same auto-hiding sub-toolbar. Its menus open as fixed body portals
// (the Options menu once sat in it as an absolute child and was clipped behind the canvas).
const fl = readFileSync('components/builtin/flow/renderer.js', 'utf8');
const tbStart = fl.indexOf("className: 'flow-toolbar', 'data-sp-subheader'");
const tbEnd = fl.indexOf("className: 'flow-main'", tbStart);
const toolbar = tbStart > 0 && tbEnd > tbStart ? fl.slice(tbStart, tbEnd) : '';
ok('the flow toolbar block is found', toolbar.length > 0);
ok('no flow toolbar menu is an absolute child of the clipping toolbar',
   !/position:\s*'absolute'/.test(toolbar));
const optMenu = toolbar.slice(toolbar.indexOf("className: 'flow-options-menu'") - 200, toolbar.indexOf("className: 'flow-options-menu'") + 400);
ok('the flow Options menu is a fixed body portal',
   /createPortal\(h\('div', \{\s*className: 'flow-options-menu'/.test(optMenu) && /position: 'fixed'/.test(optMenu)
   && /\), document\.body\)/.test(toolbar));
ok('a click inside the portalled Options menu does not close it',
   /closest\?\.\('\.flow-options-menu'\)/.test(fl));
console.log(`\n${fail ? 'FAILED' : 'ALL PASS'} — ${pass} passed, ${fail} failed  (${pass + fail} assertions)`);
process.exit(fail ? 1 : 0);
