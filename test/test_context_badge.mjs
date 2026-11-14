// Gate: the Context rail badge + resource-panel overlay reflect the turn's FULL
// context-window assembly, never a per-call (strategic/tactical) subset.
// Executes the shipped contextPanelHandler.js with its imports stubbed.
import fs from 'node:fs';
let fail = 0, pass = 0;
const check = (name, got, want) => { if (got === want) pass++; else { fail++; console.log(`FAIL ${name}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`); } };

const badge = { textContent: '' };
globalThis.document = { getElementById: (id) => id === 'rail-badge-context' ? badge : null };
globalThis.localStorage = { getItem: () => null };

let src = fs.readFileSync('static/js/handlers/contextPanelHandler.js', 'utf8');
src = src.replace(/^import[^;]+;\s*$/gm, '');
src = `const escapeHtml = s => String(s); const state = {}; const coalescedFetch = async () => ({});\n` + src;
const mod = await import('data:text/javascript;base64,' + Buffer.from(src).toString('base64'));

const snap = (used, available, n, scope) => ({
  budget: { used, available }, context_window_type: { name: 'Balanced' },
  contributions: Array.from({ length: n }, (_, i) => ({ module_id: `m${i}`, used: 1 })),
  ...(scope ? { scope } : {}),
});

// Optimize turn: full assembly, then strategic + tactical per-call subsets.
mod.renderContextWindowSnapshot(snap(40000, 100000, 9));
check('full assembly sets badge', badge.textContent, '40%');
mod.renderContextWindowSnapshot(snap(12000, 100000, 2, 'strategic'));
check('strategic subset leaves badge', badge.textContent, '40%');
mod.renderContextWindowSnapshot(snap(9000, 100000, 3, 'tactical'));
check('tactical subset leaves badge', badge.textContent, '40%');
// Next turn assembles FEWER modules (e.g. an Ideate override) — still the turn's reading.
mod.renderContextWindowSnapshot(snap(20000, 100000, 5));
check('fewer-module next turn replaces', badge.textContent, '20%');
// Reload of a turn persisted before the scope marker.
mod.renderContextWindowSnapshot(snap(5000, 100000, 2), { partial: true });
check('reload partial (unmarked) leaves badge', badge.textContent, '20%');
// Still renders its card for the Live Status trace.
const html = mod.renderContextWindowSnapshot(snap(12000, 100000, 2, 'strategic'));
check('partial still renders its card', /12\.0K \/ 100\.0K tokens \(12\.0%\)/.test(html), true);
mod.resetContextPanelState();
check('reset clears badge', badge.textContent, '');

// Backend marks the per-call snapshots.
const planner = fs.readFileSync('src/trusted_data_agent/agent/planner.py', 'utf8');
const phase = fs.readFileSync('src/trusted_data_agent/agent/phase_executor.py', 'utf8');
check('planner marks strategic scope', /"scope": "strategic"/.test(planner), true);
check('phase executor marks tactical scope', /"scope": "tactical"/.test(phase), true);
const ev = fs.readFileSync('static/js/eventHandlers.js', 'utf8');
check('reload passes partial for strategic', /renderContextWindowSnapshot\(strategicSnapshotEvent, \{ partial: true \}\)/.test(ev), true);

console.log(fail ? `${pass} passed, ${fail} failed` : `ALL PASS — ${pass} assertions`);
process.exit(fail ? 1 : 0);
