/**
 * KG inspector double-click collapse gate — executes the SHIPPED `_collapseSurvivors`
 * (the pure survival rule behind "double-click an expanded node again to revert") out of
 * components/builtin/knowledge_graph/renderer.js. No server, no browser, no d3.
 *
 * Properties:
 *   1. a plain collapse removes exactly what the expansion introduced (nodes + links)
 *   2. a node this expansion introduced SURVIVES while another active expansion still
 *      claims it as a neighbour — collapsing A must not make B's neighbourhood incomplete
 *   3. a link this expansion introduced survives when it touches another active centre
 *   4. pre-existing nodes are never removed, whatever the ledger says
 *   5. structural: the double-click toggles (expanded → collapse, else expand+record),
 *      ids are minted monotonically, joins are keyed, removal is synchronous
 *
 * Run: node test/test_kg_expand_collapse.mjs
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SRC = fs.readFileSync(path.join(ROOT, 'components/builtin/knowledge_graph/renderer.js'), 'utf8');
let passed = 0, failed = 0; const fails = [];
const check = (name, ok, detail = '') => { if (ok) passed++; else { failed++; fails.push(`${name}${detail ? ' — ' + detail : ''}`); } };

const start = SRC.indexOf('function _collapseSurvivors(');
check('_collapseSurvivors exists', start > 0);
const fnSrc = SRC.slice(start, SRC.indexOf('\n}\n', start) + 2);
const survivors = new Function(`${fnSrc}; return _collapseSurvivors;`)();

// fixture: base graph n0..n2 ; A=n0 expanded (introduced n3,n4 + links); B=n1 expanded
const N = (id, eid) => ({ id, entity_id: eid, name: eid });
const L = (uid, s, t) => ({ _uid: uid, source: s, target: t, type: 'r' });
function fixture() {
  const n0 = N(0, 'e0'), n1 = N(1, 'e1'), n2 = N(2, 'e2'), n3 = N(3, 'e3'), n4 = N(4, 'e4');
  const nodes = [n0, n1, n2, n3, n4];
  const l0 = L('g0', n0, n1);           // pre-existing
  const l1 = L('g1', n0, n3), l2 = L('g2', n0, n4), l3 = L('g3', n2, n3);   // introduced by A
  const links = [l0, l1, l2, l3];
  const A = { centre: n0, nodeIds: new Set([3, 4]), linkUids: new Set(['g1', 'g2', 'g3']), claimed: new Set(['e1', 'e3', 'e4']) };
  return { n0, n1, n2, n3, n4, l0, l1, l2, l3, nodes, links, A };
}
// 1. plain collapse
{
  const f = fixture();
  const r = survivors(f.A, new Map(), f.nodes, f.links);
  check('plain: introduced nodes removed', [...r.goneNodes].map(n => n.id).sort().join() === '3,4');
  check('plain: introduced links removed, pre-existing link kept', [...r.goneLinks].map(l => l._uid).sort().join() === 'g1,g2,g3');
  check('plain: pre-existing nodes never removed', !r.goneNodes.has(f.n0) && !r.goneNodes.has(f.n1) && !r.goneNodes.has(f.n2));
}
// 2. shared neighbour survives
{
  const f = fixture();
  const B = { centre: f.n1, nodeIds: new Set(), linkUids: new Set(), claimed: new Set(['e0', 'e3']) };   // B found e3 already present
  const r = survivors(f.A, new Map([[1, B]]), f.nodes, f.links);
  check('shared: node B still claims survives', !r.goneNodes.has(f.n3), 'n3 removed');
  check('shared: node nobody else claims goes', r.goneNodes.has(f.n4));
  check('shared: links of the surviving node stay unless A introduced them off-centre', !r.goneLinks.has(f.l0) && r.goneLinks.has(f.l2));
  // l1 (n0→n3) and l3 (n2→n3): introduced by A, neither endpoint is B's centre → removed (revert)
  check('shared: A-introduced links not touching an active centre are reverted', r.goneLinks.has(f.l1) && r.goneLinks.has(f.l3));
}
// 3. link touching another active centre survives
{
  const f = fixture();
  const C = { centre: f.n2, nodeIds: new Set(), linkUids: new Set(), claimed: new Set(['e3']) };   // C's centre is n2; l3 = n2→n3
  const r = survivors(f.A, new Map([[2, C]]), f.nodes, f.links);
  check('centre-touching link survives', !r.goneLinks.has(f.l3) && !r.goneNodes.has(f.n3));
  check('other introduced material still reverts', r.goneNodes.has(f.n4) && r.goneLinks.has(f.l2) && r.goneLinks.has(f.l1));
}
// 4. an expansion that introduced nothing removes nothing
{
  const f = fixture();
  const E = { centre: f.n2, nodeIds: new Set(), linkUids: new Set(), claimed: new Set(['e3']) };
  const r = survivors(E, new Map(), f.nodes, f.links);
  check('empty expansion: nothing removed', r.goneNodes.size === 0 && r.goneLinks.size === 0);
}
// 5. structural
{
  const code = SRC.replace(/\/\/[^\n]*/g, '').replace(/\/\*[\s\S]*?\*\//g, '');
  const i = code.indexOf('function _doDoubleClick('); const body = code.slice(i, i + 1200);
  check('double-click toggles on the ledger', /_expansions\.has\(d\.id\)/.test(body) && /_collapseExpansion\(d\)/.test(body));
  check('expand records in the ledger (third arg true)', /_revealComplete\(d\.entity_id,[\s\S]*?\}, true\)/.test(body));
  check('cascade: nested expansions collapse first', /if \(exp\.nodeIds\.has\(other\.centre\.id\)\) _collapseExpansion\(other\.centre\)/.test(code));
  check('node ids minted from a monotonic counter, not nodes.length', /id: _nextNodeId\+\+/.test(code) && !/let nextId = preCount/.test(code));
  check('node join is keyed', /\.data\(nodes, n => n\.id\)/.test(code));
  check('link join is keyed', /\.data\(links, l => l\._uid\)/.test(code));
  const j = code.indexOf('function _removeElements('); const rb = code.slice(j, j + 1600);
  check('exiting nodes removed synchronously (no fade-then-remove)', /\.exit\(\)\.interrupt\(\)\.remove\(\)/.test(rb) && !/transition\(\)\.duration\(\d+\)\.style\('opacity', 0\)\.remove\(\)/.test(rb));
  check('expanded nodes wear a mark', /kg-expand-mark/.test(code));
}
console.log(failed ? `FAIL — ${failed} of ${passed + failed}\n  ${fails.join('\n  ')}` : `ALL PASS — ${passed} assertions`);
process.exit(failed ? 1 : 0);
