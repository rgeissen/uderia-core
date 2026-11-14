/**
 * KG inspector "Zoom to fit" gate — executes the SHIPPED `_fitTransform` out of
 * components/builtin/knowledge_graph/renderer.js (no server, no browser, no network).
 *
 * The defect this pins: the button used to apply a fixed scale(0.8) about the SVG
 * centre and never looked at where the nodes were, so it only "fit" when the force
 * layout happened to be centred and compact. Properties asserted:
 *   1. off-centre nodes are framed (every node's projected point lands inside the frame)
 *   2. nodes hidden by the type filter (display:none) are excluded from the box
 *   3. the scale never leaves the zoom's OWN scaleExtent (read from the zoom, not typed)
 *   4. no positioned node → identity (never a guess)
 *   5. every caller routes through the one fit — no second hardcoded transform survives
 *
 * Run: node test/test_kg_fit_view.mjs
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SRC = fs.readFileSync(path.join(ROOT, 'components/builtin/knowledge_graph/renderer.js'), 'utf8');

let passed = 0, failed = 0; const fails = [];
const check = (name, ok, detail = '') => { if (ok) passed++; else { failed++; fails.push(`${name}${detail ? ' — ' + detail : ''}`); } };

// ── extract the pure function verbatim ──
const start = SRC.indexOf('function _fitTransform()');
check('_fitTransform exists in the shipped module', start > 0);
const end = SRC.indexOf('\n}\n', start);
const fnSrc = SRC.slice(start, end + 2);

// minimal d3.zoomIdentity with d3-zoom's exact transform algebra
class T { constructor(k, x, y) { this.k = k; this.x = x; this.y = y; }
  scale(k) { return k === 1 ? this : new T(this.k * k, this.x, this.y); }
  translate(x, y) { return (x === 0 && y === 0) ? this : new T(this.k, this.x + this.k * x, this.y + this.k * y); }
  apply(p) { return [p[0] * this.k + this.x, p[1] * this.k + this.y]; } }
const d3 = { zoomIdentity: new T(1, 0, 0) };

function build(state) {
  let _graphState = state;
  const fn = new Function('d3', '_graphState', `${fnSrc}; return _fitTransform();`);
  return fn(d3, _graphState);
}
const W = 900, H = 500;
const zoom = { scaleExtent: () => [0.1, 4] };
const groupsOf = (nodes, hiddenIds = new Set()) => ({
  each(cb) { for (const n of nodes) cb.call({ style: { display: hiddenIds.has(n.id) ? 'none' : '' } }, n); },
});

// 1. off-centre sprawl (the live shape: 88 nodes spread far past the frame, off to one side)
{
  const nodes = []; for (let i = 0; i < 60; i++) nodes.push({ id: 'n' + i, x: 1500 + (i % 6) * 300, y: -900 + Math.floor(i / 6) * 350 });
  const t = build({ nodes, nodeGroups: groupsOf(nodes), width: W, height: H, zoom });
  const inside = nodes.filter(n => { const [x, y] = t.apply([n.x, n.y]); return x >= 0 && x <= W && y >= 0 && y <= H; }).length;
  check('off-centre sprawl: every node projects inside the frame', inside === nodes.length, `${inside}/${nodes.length}`);
  check('off-centre sprawl: not the old fixed scale', Math.abs(t.k - 0.8) > 1e-6, `k=${t.k}`);
  const [cx, cy] = t.apply([1500 + 2.5 * 300, -900 + 4.5 * 350]);
  check('off-centre sprawl: the box is centred in the frame', Math.abs(cx - W / 2) < 1 && Math.abs(cy - H / 2) < 1, `centre→(${cx.toFixed(1)},${cy.toFixed(1)})`);
}
// 2. hidden nodes excluded
{
  const vis = [{ id: 'a', x: 100, y: 100 }, { id: 'b', x: 300, y: 250 }];
  const far = { id: 'h', x: 9000, y: 9000 };
  const nodes = [...vis, far];
  const tAll = build({ nodes, nodeGroups: groupsOf(nodes), width: W, height: H, zoom });
  const tHid = build({ nodes, nodeGroups: groupsOf(nodes, new Set(['h'])), width: W, height: H, zoom });
  check('hidden node excluded: the fit is tighter without it', tHid.k > tAll.k * 3, `k all=${tAll.k.toFixed(3)} hidden=${tHid.k.toFixed(3)}`);
  const insideVis = vis.every(n => { const [x, y] = tHid.apply([n.x, n.y]); return x >= 0 && x <= W && y >= 0 && y <= H; });
  check('hidden node excluded: the visible ones are still framed', insideVis);
}
// 3. clamp to the zoom's own extent (both ends), never a typed floor
{
  const nodes = []; for (let i = 0; i < 40; i++) nodes.push({ id: 'n' + i, x: (i % 8) * 4000, y: Math.floor(i / 8) * 4000 });
  const t = build({ nodes, nodeGroups: groupsOf(nodes), width: W, height: H, zoom });
  check('huge spread clamps to the extent floor', Math.abs(t.k - 0.1) < 1e-9, `k=${t.k}`);
  const t2 = build({ nodes, nodeGroups: groupsOf(nodes), width: W, height: H, zoom: { scaleExtent: () => [0.02, 4] } });
  check('floor is READ from the zoom (a looser extent fits further)', t2.k < 0.1 && t2.k > 0.02, `k=${t2.k}`);
  const tiny = [{ id: 'a', x: 400, y: 200 }, { id: 'b', x: 420, y: 210 }];
  const t3 = build({ nodes: tiny, nodeGroups: groupsOf(tiny), width: W, height: H, zoom });
  check('a tiny graph is never blown up past the magnification ceiling', t3.k <= 1.5 + 1e-9, `k=${t3.k}`);
}
// 4. nothing positioned → identity
{
  const nodes = [{ id: 'a' }, { id: 'b', x: NaN, y: 3 }];
  const t = build({ nodes, nodeGroups: groupsOf(nodes), width: W, height: H, zoom });
  check('no positioned node → identity, never a guess', t.k === 1 && t.x === 0 && t.y === 0);
}
// 5. one fit, every caller — no second hardcoded transform survives
{
  const code = SRC.replace(/\/\/[^\n]*/g, '').replace(/\/\*[\s\S]*?\*\//g, '');
  const hardcoded = (code.match(/zoomIdentity\.translate\(width \/ 2, height \/ 2\)\.scale\(0\.8\)/g) || []).length;
  check('no hardcoded scale(0.8) fit survives', hardcoded === 0, `${hardcoded} found`);
  for (const caller of ['export function kgFitView()', 'toolbar._onZoomFit = () => {', 'function _zoomToFitAll()']) {
    const i = code.indexOf(caller); const body = code.slice(i, i + 400);
    check(`${caller.split('(')[0].replace(/.*\s/, '')} routes through _fitVisibleNodes`, i > 0 && /_fitVisibleNodes\(/.test(body));
  }
}

console.log(failed ? `FAIL — ${failed} of ${passed + failed}\n  ${fails.join('\n  ')}` : `ALL PASS — ${passed} assertions`);
process.exit(failed ? 1 : 0);
