#!/usr/bin/env node
/**
 * A graph opened ON a concept lands on it — the OBDA-MODEL panel's Open, a Workspace
 * extension row, "Open in Graph". Before: `focus_names` only seeded the server's sample;
 * the node was in the graph, unmarked, and the panel fit the whole graph. Now the renderer
 * selects it exactly as a click does and zooms to it once, on the open; a re-query keeps
 * the selection without moving the view. The shipped function is EXECUTED under stubs.
 *
 * Run: node test/test_kg_open_focus.mjs
 */
import { readFileSync } from 'node:fs';

let pass = 0, fail = 0;
const ok = (label, cond, detail = '') => { if (cond) pass++; else { fail++; console.log(`  FAIL: ${label}${detail ? ' — ' + detail : ''}`); } };
const R = readFileSync('components/builtin/knowledge_graph/renderer.js', 'utf8');
const extractFn = (src, name) => {
    const i = src.indexOf(`function ${name}(`); if (i < 0) throw new Error(`no ${name}`);
    let depth = 0, j = src.indexOf('{', i);
    for (; j < src.length; j++) { if (src[j] === '{') depth++; else if (src[j] === '}') { depth--; if (!depth) break; } }
    return src.slice(i, j + 1);
};

console.log('[1. the function, executed]');
{
    const calls = { select: [], zoom: [], latch: [] };
    const nodes = [{ id: 1, entity_id: 11, name: 'core.Lead' }, { id: 2, entity_id: 12, name: 'core.Offence' }, { id: 3, entity_id: 13, name: 'other.Lead' }];
    const src = `let _userZoomOverride = true; let _graphState = { nodes, selectNode: n => calls.select.push(n.name) };
        const _kgNameSegments = s => String(s).split('.');
        ${extractFn(R, '_matchNodesToNames')}
        function _centreOnNode(n) { calls.zoom.push([n.name]); calls.latch.push(_userZoomOverride); }
        ${extractFn(R, '_focusOpenedNames')}
        return { run: _focusOpenedNames, unset: () => { _graphState = null; } };`;
    const F = new Function('nodes', 'calls', src)(nodes, calls);
    const got = F.run(['core.Lead'], true);
    ok('an open on a qualified name selects THAT node (the click\'s own selection) and returns it', got && got.name === 'core.Lead' && calls.select.join() === 'core.Lead');
    ok('…and centres the view ON that node, with the "person panned" latch released first (the open is their act)', calls.zoom.length === 1 && calls.zoom[0].join() === 'core.Lead' && calls.latch[0] === false);
    F.run(['Offence'], false);
    ok('a bare leaf name matches by the leaf; a re-query (no zoom) re-selects without moving the view', calls.select.at(-1) === 'core.Offence' && calls.zoom.length === 1);
    ok('a name that matches nothing draws nothing and does not throw', F.run(['Nothing'], true) === null && calls.select.length === 2);
    ok('no names / no graph → null', F.run([], true) === null && F.run(null, true) === null && (F.unset(), F.run(['core.Lead'], true) === null));
}

console.log('[2. the wiring]');
const after = R.slice(R.indexOf("UderiaBus.emit('uderia:kg-rendered'"), R.indexOf('// Drain any animation queued'));
ok('renderKGFull focuses the opened names after the graph is live (after kg:rendered, before the animation drain)',
   /const openedSpec = _activeSpec \|\| \{\};/.test(after) && /const openedNames = Array\.isArray\(openedSpec\.focus_names\)/.test(after) && /setTimeout\(\(\) => _focusOpenedNames\(names, zoomTo\), 220\)/.test(after));
ok('…zoom only on the open (focus_names); a kept seed (applied_focus_names) re-selects without zoom', /zoomTo = !!openedNames/.test(after) && /openedNames \|\| keptNames/.test(after));
ok('…the open happens once: focus_names is cleared on the spec before the timer', /openedSpec\.focus_names = null;/.test(after));
ok('the graph state exposes the single-click selection for it', /selectNode: _doSingleClick/.test(R));
const canon = R.slice(R.indexOf('canon.action = spec.action || canon.action;'), R.indexOf('renderKGFull(renderTarget.id, canon);'));
ok('the canonical open carries the opened names onto the rendered spec, as the open AND the kept seed',
   /canon\.focus_names = spec\.focus_names;/.test(canon) && /canon\.applied_focus_names = spec\.focus_names;/.test(canon));
const reload = extractFn(R, '_reloadKGPanel');
ok('a reload keeps the seed it had (no new zoom)', /focusNames: kept/.test(reload) && /canon\.applied_focus_names = kept/.test(reload) && !/canon\.focus_names = kept/.test(reload));
const centre = extractFn(R, '_centreOnNode');
ok('the centring puts the node itself at the view centre at a reading scale (not its neighbourhood\'s box)',
   /translate\(width \/ 2, height \/ 2\)\.scale\(scale\)\.translate\(-node\.x, -node\.y\)/.test(centre) && /Math\.max\(0\.6, Math\.min\(1\.25/.test(centre));
const WS = readFileSync('static/js/handlers/workspacePanelHandler.js', 'utf8');
ok('the Workspace opener passes focus_names on the spec (the OBDA-MODEL panel\'s Open goes through it)', /focus_names: focus,/.test(WS));
const OBM = readFileSync('components/builtin/obda_model/renderer.js', 'utf8');
ok('the OBDA-MODEL Open names the concept to the opener', /focusNames: \[name\]/.test(OBM) && /window\.openKnowledgeGraphFromWorkspace/.test(OBM));

console.log(`\n${fail ? 'FAILED' : 'ALL PASS'} — ${pass + fail} assertions (${pass} passed, ${fail} failed)`);
process.exit(fail ? 1 : 0);
