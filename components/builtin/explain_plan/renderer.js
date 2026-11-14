/**
 * EXPLAIN Plan renderer — draws a parsed Teradata plan (spec from the TDA_ExplainPlan handler)
 * as a graph of steps: source tables at the top, steps below in execution order, the steps of
 * a parallel group side by side inside a frame, an edge for every table or spool a step reads.
 * A step a finding names carries the finding's colour; selecting a step shows its full text.
 *
 * The graph knows the width it is drawn into: a row longer than the width wraps (a parallel
 * group of eighteen steps becomes several rows inside one frame), a node narrows before it
 * wraps, and when the source tables would not fit one row they are named inside the steps
 * that read them instead of as a lane of their own. Zoom changes how much fits, not the text
 * size alone: at 50 % the layout gets twice the width, so a large plan can be seen whole.
 *
 * In the side panel and in a dashboard tile the plan is split into segments — Overview (the
 * findings, where the time goes, the comparison with the previous plan), Graph, Steps (a
 * sortable table of every step) and Statement — so no part of a large plan pushes another
 * off screen. In the answer it is a preview card. Theme variables only.
 */

const NODE_W = 236, NODE_MIN_W = 168, NODE_MAX_W = 360, NODE_H = 46, NODE_H_T = 60, SRC_H = 28;
const GAP_X = 22, GAP_Y = 30, WRAP_GAP = 10, GROUP_HEAD = 16, PAD = 12;
const SEV_RANK = { danger: 0, warn: 1, info: 2 };
const NON_WORK = ['LOCK', 'END TRANSACTION', 'PARALLEL'];
const ZOOMS = [0.4, 0.5, 0.67, 0.8, 1, 1.25];

function _esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function _css() {
  if (document.getElementById('xplan-css')) return;
  const st = document.createElement('style');
  st.id = 'xplan-css';
  st.textContent = `
  .xplan { font-size: 12px; color: var(--text-primary); }
  .xplan-head { display:flex; flex-wrap:wrap; gap:6px 12px; align-items:baseline; color: var(--text-muted); margin-bottom:6px; }
  /* In the answer: the graph as a PREVIEW (neutral — the colours and their key belong to the
     panel) and the footer every component card shares to open the side panel. */
  .xplan-card { border:1px solid var(--border-secondary, var(--border-primary)); border-radius:8px; overflow:hidden; background: var(--card-bg); }
  .xplan-card-preview { cursor:pointer; }
  .xplan-card .sp-open-row { border-top:1px solid var(--border-secondary, var(--border-primary)); }
  .xplan-card-preview { max-height: 320px; overflow:hidden; position:relative; padding:6px; }
  .xplan-card-preview svg { display:block; width:100%; height:auto; max-height:308px; }
  .xplan-card-preview.is-cut::after { content:''; position:absolute; left:0; right:0; bottom:0; height:40px; pointer-events:none;
    background: linear-gradient(to bottom, transparent, var(--card-bg)); }
  .xplan-card .xplan-node, .xplan-card .xplan-node * { cursor:pointer; }
  .xplan-head strong { color: var(--text-primary); font-weight:600; }

  /* segments */
  .xplan.seg { display:flex; flex-direction:column; min-height:0; }
  .xplan-tabs { display:flex; gap:2px; border-bottom:1px solid var(--border-secondary, var(--border-primary)); margin:0 0 8px; flex:0 0 auto; overflow-x:auto; }
  .xplan-tabs .ind-tab { font-size:12px; padding:6px 10px; gap:6px; }
  .xplan-tabs .ind-tab:focus-visible { outline: 2px solid var(--teradata-orange, #F15F22); outline-offset:-2px; }
  .xplan-tab-n { font-size:10px; padding:0 5px; border-radius:8px; border:1px solid var(--border-primary); color: var(--text-muted); }
  .xplan-tab-n.danger { border-color: var(--trust-danger, #dc2626); color: var(--trust-danger, #dc2626); }
  .xplan-tab-n.warn { border-color: var(--trust-warn, #d97706); color: var(--trust-warn, #d97706); }
  .xplan-pane { min-height:0; }
  .xplan-pane[hidden] { display:none !important; }
  .xplan-pane.scroll { overflow:auto; }
  .xplan-pane-graph { display:flex; flex-direction:column; min-height:0; }

  /* overview */
  .xplan-stats { display:flex; flex-wrap:wrap; gap:6px; margin:0 0 10px; }
  .xplan-stat { display:inline-flex; flex-direction:column; padding:5px 10px; border:1px solid var(--border-secondary, var(--border-primary)); border-radius:6px; min-width:64px; }
  .xplan-stat b { font-size:15px; font-weight:600; color: var(--text-primary); line-height:1.2; }
  .xplan-stat span { font-size:10px; color: var(--text-muted); text-transform:uppercase; letter-spacing:.04em; }
  .xplan-stat.danger b { color: var(--trust-danger, #dc2626); }
  .xplan-stat.warn b { color: var(--trust-warn, #d97706); }
  .xplan-sec { font-size:10px; font-weight:600; letter-spacing:.06em; text-transform:uppercase; color: var(--text-muted); margin:12px 0 6px; }
  .xplan-find { list-style:none; margin:0 0 8px; padding:0; display:flex; flex-direction:column; gap:5px; }
  .xplan-find li { display:flex; gap:8px; align-items:flex-start; line-height:1.45; }
  .xplan-find li button.xplan-find-btn { all:unset; cursor:pointer; color: var(--text-primary); }
  .xplan-find li button.xplan-find-btn:hover { text-decoration: underline; text-decoration-color: var(--text-muted); }
  .xplan-find li button:focus-visible { outline: 2px solid var(--teradata-orange, #F15F22); outline-offset: 2px; }
  .xplan-find .xplan-note-txt { color: var(--text-muted); }
  .xplan-find .xplan-find-what { color: var(--text-primary); font-weight:500; }
  .xplan-find .xplan-find-why { display:block; color: var(--text-muted); margin-top:2px; }
  .xplan-sev { flex:0 0 auto; margin-top:4px; width:8px; height:8px; border-radius:2px; box-sizing:border-box; }
  .xplan-sev.danger { border:2px solid var(--trust-danger, #dc2626); }
  .xplan-sev.warn { background: linear-gradient(90deg, var(--trust-warn, #d97706) 50%, transparent 50%); border:1px solid var(--trust-warn, #d97706); }
  .xplan-sev.info { background: var(--text-muted); opacity:.55; }
  .xplan-chips { display:inline-flex; flex-wrap:wrap; gap:3px; margin-left:4px; vertical-align:middle; }
  .xplan-chip { all:unset; cursor:pointer; font-size:10px; line-height:1; padding:3px 6px; border-radius:4px; border:1px solid var(--border-primary); color: var(--text-muted); font-variant-numeric: tabular-nums; }
  .xplan-chip:hover { color: var(--text-primary); border-color: var(--text-muted); }
  .xplan-chip:focus-visible { outline: 2px solid var(--teradata-orange, #F15F22); outline-offset: 1px; }
  .xplan-hot { display:grid; grid-template-columns: minmax(0,auto) 1fr auto; gap:5px 10px; align-items:center; }
  .xplan-hot .xplan-hot-bar { height:6px; border-radius:3px; background: color-mix(in srgb, var(--text-muted) 20%, transparent); overflow:hidden; }
  .xplan-hot .xplan-hot-bar i { display:block; height:100%; background: var(--teradata-orange, #F15F22); opacity:.75; }
  .xplan-hot .xplan-hot-v { color: var(--text-muted); font-variant-numeric: tabular-nums; font-size:11px; }
  .xplan-bk { color: var(--text-muted); margin:10px 0 6px; line-height:1.4; }

  /* graph */
  .xplan-gtools { display:flex; flex-wrap:wrap; align-items:center; gap:6px 10px; margin:0 0 6px; flex:0 0 auto; }
  .xplan-seg { display:inline-flex; border:1px solid var(--border-primary); border-radius:6px; overflow:hidden; }
  .xplan-seg button { all:unset; cursor:pointer; padding:3px 8px; font-size:11px; color: var(--text-muted); display:inline-flex; align-items:center; gap:4px; }
  .xplan-seg button + button { border-left:1px solid var(--border-primary); }
  .xplan-seg button:hover { color: var(--text-primary); }
  .xplan-seg button.on { color: var(--text-primary); background: color-mix(in srgb, var(--teradata-orange, #F15F22) 14%, transparent); }
  .xplan-seg button:focus-visible { outline: 2px solid var(--teradata-orange, #F15F22); outline-offset:-2px; }
  .xplan-seg button[disabled] { opacity:.4; cursor:default; }
  .xplan-seg svg { width:13px; height:13px; }
  .xplan-zoom-v { min-width:38px; justify-content:center; font-variant-numeric: tabular-nums; }
  .xplan-gtools-lbl { font-size:11px; color: var(--text-muted); }
  .xplan-graph { position:relative; overflow:auto; border:1px solid var(--border-secondary, var(--border-primary)); border-radius:6px; background: var(--card-bg); min-height:160px; }
  .xplan.seg { container-type: inline-size; }
  .xplan-gbody { flex:1 1 auto; min-height:0; display:flex; flex-direction:column; }
  .xplan-pane-graph .xplan-graph { flex:1 1 auto; min-height:200px; }
  .xplan-detail-text summary { cursor:pointer; margin-top:4px; font-size:11px; }
  .xplan-detail-text div { margin-top:4px; }
  /* wide (fullscreen, a wide tile): the selected step is described BESIDE the graph, so the
     graph keeps the full height; narrow: under it, compact */
  @container (min-width: 900px) {
    .xplan-gbody { flex-direction:row; gap:10px; }
    .xplan-gbody .xplan-detail { margin-top:0; width:340px; flex:0 0 340px; max-height:none; align-self:stretch; }
  }
  @container (max-width: 520px) {
    .xplan-gtools .lbl { display:none; }
    .xplan-gtools .xplan-link-note { display:none; }
  }
  .xplan-graph svg { display:block; margin:0 auto; }
  .xplan-node rect.box { fill: var(--bg-primary, var(--card-bg)); stroke: var(--border-primary); stroke-width:1; }
  .xplan-node.sev-danger rect.box { stroke: var(--trust-danger, #dc2626); stroke-width:2; }
  .xplan-node.sev-warn rect.box { stroke: var(--trust-warn, #d97706); stroke-width:1.5; }
  .xplan-node.sel rect.box { stroke: var(--teradata-orange, #F15F22); stroke-width:2.5; fill: color-mix(in srgb, var(--teradata-orange, #F15F22) 14%, var(--bg-primary, var(--card-bg))); }
  .xplan-node.rel rect.box { stroke: var(--teradata-orange, #F15F22); stroke-dasharray: 4 2; }
  .xplan-node { cursor:pointer; transition: opacity .15s ease; }
  .xplan-node:focus { outline:none; }
  .xplan-node:focus-visible rect.box { stroke: var(--teradata-orange, #F15F22); stroke-width:2.5; }
  .xplan-node text { fill: var(--text-primary); font-size:11px; }
  .xplan-node text.sub { fill: var(--text-muted); font-size:10px; }
  .xplan-node rect.tbar { fill: var(--teradata-orange, #F15F22); opacity:.55; }
  .xplan-src rect { fill: none; stroke: var(--border-primary); stroke-dasharray: 3 2; }
  .xplan-src text { fill: var(--text-muted); font-size:10px; }
  .xplan-par rect { fill: color-mix(in srgb, var(--text-muted) 5%, transparent); stroke: var(--border-primary); stroke-dasharray: 5 3; }
  .xplan-par text { fill: var(--text-muted); font-size:10px; }
  .xplan-edge { fill:none; stroke: var(--text-muted); stroke-opacity:.55; stroke-width:1.2; transition: stroke-opacity .15s ease; }
  .xplan-graph.has-sel .xplan-edge { stroke-opacity:.12; }
  .xplan-graph.has-sel .xplan-node:not(.sel):not(.rel) { opacity:.45; }
  .xplan-graph.has-sel .xplan-edge.on { stroke: var(--teradata-orange, #F15F22); stroke-opacity:.95; stroke-width:1.8; }
  .xplan-key { display:flex; flex-wrap:wrap; gap:4px 14px; align-items:center; margin:0 0 6px; color: var(--text-muted); font-size:11px; flex:0 0 auto; }
  .xplan-key[hidden], .xplan-detail[hidden] { display:none !important; }
  .xplan-key span { display:inline-flex; align-items:center; gap:5px; }
  .xplan-key-sw { display:inline-block; width:14px; height:9px; border-radius:2px; border:1px solid var(--border-primary); }
  .xplan-key-sw.sev-danger { border:2px solid var(--trust-danger, #dc2626); }
  .xplan-key-sw.sev-warn { border:1.5px solid var(--trust-warn, #d97706); }
  .xplan-key-sw.sel { border:2px solid var(--teradata-orange, #F15F22); background: color-mix(in srgb, var(--teradata-orange, #F15F22) 14%, transparent); }
  .xplan-key-sw.par { border:1px dashed var(--border-primary); }
  .xplan-key-sw.tbar { height:3px; border:0; background: var(--teradata-orange, #F15F22); opacity:.55; }
  .xplan-key-note { opacity:.8; }
  .xplan-detail { flex:0 0 auto; margin-top:6px; padding:8px 10px; border:1px solid var(--border-secondary, var(--border-primary)); border-left:3px solid var(--teradata-orange, #F15F22);
    border-radius:6px; color: var(--text-muted); line-height:1.45; max-height:30%; overflow:auto; background: var(--card-bg); }
  .xplan-detail strong { color: var(--text-primary); }
  .xplan-detail-nav { display:flex; gap:6px; align-items:center; float:right; margin:-2px 0 4px 8px; }

  /* steps */
  .xplan-st-tools { display:flex; flex-wrap:wrap; align-items:center; gap:6px 12px; margin:0 0 8px; }
  .xplan-st-tools label { display:inline-flex; gap:5px; align-items:center; font-size:11px; color: var(--text-muted); cursor:pointer; }
  table.xplan-steps { width:100%; border-collapse:collapse; font-size:11px; }
  table.xplan-steps th { position:sticky; top:0; background: var(--card-bg); text-align:left; font-weight:600; color: var(--text-muted);
    font-size:10px; text-transform:uppercase; letter-spacing:.04em; padding:5px 6px; border-bottom:1px solid var(--border-primary); white-space:nowrap; }
  table.xplan-steps td { padding:5px 6px; border-bottom:1px solid var(--border-secondary, var(--border-primary)); vertical-align:top; color: var(--text-primary); }
  table.xplan-steps td.num { text-align:right; font-variant-numeric: tabular-nums; white-space:nowrap; }
  table.xplan-steps td.mut { color: var(--text-muted); }
  table.xplan-steps tr.bk td { color: var(--text-muted); }
  table.xplan-steps .xplan-st-sub { color: var(--text-muted); font-size:10px; margin-top:1px; overflow-wrap:anywhere; }
  table.xplan-steps td.xplan-st-flow { overflow-wrap:anywhere; }
  .xplan-st-flow-inline { display:none; }
  /* narrow: what a step reads and writes goes under its operation, so the numbers stay on screen */
  @container (max-width: 560px) {
    table.xplan-steps .xplan-st-flow { display:none; }
    .xplan-st-flow-inline { display:block; }
  }
  table.xplan-steps tr.xplan-row { cursor:pointer; }
  table.xplan-steps tr.xplan-row:hover td { background: color-mix(in srgb, var(--text-muted) 8%, transparent); }
  table.xplan-steps button.xplan-row-btn { all:unset; cursor:pointer; font-weight:600; font-variant-numeric: tabular-nums; }
  table.xplan-steps button.xplan-row-btn:focus-visible { outline: 2px solid var(--teradata-orange, #F15F22); outline-offset: 2px; }

  #explain-plan-split-panel { width:0; min-width:0; max-width:760px; flex-shrink:0; display:flex; flex-direction:column; height:100%;
    overflow:hidden; background: var(--card-bg); border-left:1px solid var(--border-primary); transition: width .3s ease, min-width .3s ease; }
  #explain-plan-split-panel.xplan-split--open { width:42%; min-width:380px; }
  .xplan-panel-body { flex:1; min-height:0; overflow:hidden; padding:10px 14px 12px; display:flex; flex-direction:column; }
  .xplan-panel-body > #explain-plan-split-graph { flex:1 1 auto; min-height:0; display:flex; flex-direction:column; }
  .xplan-panel-body > #explain-plan-split-graph > .xplan.seg { flex:1 1 auto; }
  .xplan-panel-body .xplan.seg > .xplan-pane:not([hidden]) { flex:1 1 auto; }
  .xplan-fullscreen #explain-plan-split-panel.xplan-split--open { position:fixed !important; top: var(--xplan-fullscreen-top, 0px);
    left:0 !important; right:0 !important; bottom:0 !important; width:auto !important; max-width:none; z-index:50; }
  #explain-plan-subtoolbar:empty { display:none; }
  .xplan-bar-gap { flex:1 1 auto; }
  .xplan-bar-note { font-size:11px; color: var(--text-muted); }
  .xplan-bar-note.is-error { color: var(--trust-danger, #dc2626); }
  .xplan-cmp { margin:0 0 10px; padding:8px 10px; border:1px solid var(--border-secondary, var(--border-primary)); border-radius:6px; font-size:12px; color: var(--text-muted); }
  .xplan-cmp strong { color: var(--text-primary); }
  .xplan-cmp ul { list-style:none; margin:6px 0 0; padding:0; display:flex; flex-direction:column; gap:3px; }
  .xplan-cmp .gone { color: var(--trust-pass, #059669); }
  .xplan-cmp .new { color: var(--trust-danger, #dc2626); }
  .xplan-cmp .new-warn { color: var(--trust-warn, #d97706); }
  .xplan-link-note { color: var(--text-muted); font-size:11px; }
  .xplan-sql pre { margin:0; white-space:pre-wrap; font-size:12px; line-height:1.5; color: var(--text-primary); padding:10px;
    border:1px solid var(--border-secondary, var(--border-primary)); border-radius:6px; background: var(--bg-primary, var(--card-bg)); }
  .xplan.fill { height:100%; display:flex; flex-direction:column; }
  .xplan.fill > .xplan-pane:not([hidden]) { flex:1 1 auto; }
  `;
  document.head.appendChild(st);
}

/** Rows of the graph: sources first, then each top-level step (a parallel group = one row of
 *  its member steps). With `maxWidth` the layout fits that width: nodes narrow first, then a
 *  row longer than the columns that fit wraps (a parallel group's rows share one frame), and
 *  the source tables move into the steps that read them when they would not fit one row
 *  (`tables: 'auto' | 'lane' | 'steps'`). Without `maxWidth` every row is one row. Pure —
 *  exported for the gate. */
export function layoutPlan(spec, opts = {}) {
  const steps = (spec.steps || []).filter(s => !['LOCK', 'END TRANSACTION'].includes(s.op));
  const maxW = opts.maxWidth > 0 ? opts.maxWidth : 0;
  let nodeW = maxW ? Math.max(NODE_MIN_W, Math.min(NODE_W, Math.floor(maxW - 2 * PAD))) : NODE_W;
  const cols = maxW ? Math.max(1, Math.floor((maxW - 2 * PAD + GAP_X) / (nodeW + GAP_X))) : Infinity;
  const sources = spec.sources || [];
  const tablesInSteps = opts.tables === 'steps' || (opts.tables !== 'lane' && sources.length > cols);
  const stepH = tablesInSteps ? NODE_H_T : NODE_H;
  const rows = [];
  if (sources.length && !tablesInSteps) rows.push(sources.map(t => ({ id: `table:${t}`, kind: 'source', label: t })));
  for (const s of steps) {
    if (s.op === 'PARALLEL') { rows.push([]); continue; }
    if (s.parallel_group) {
      const last = rows[rows.length - 1];
      if (last && last.group === s.parallel_group) { last.push({ id: s.id, kind: 'step', step: s }); continue; }
      const row = [{ id: s.id, kind: 'step', step: s }]; row.group = s.parallel_group;
      if (last && last.length === 0) rows[rows.length - 1] = row; else rows.push(row);
      continue;
    }
    rows.push([{ id: s.id, kind: 'step', step: s }]);
  }
  const kept = rows.filter(r => r.length);
  const usedCols = Math.max(1, ...kept.map(r => Math.min(r.length, cols)));
  // spare width goes into the nodes (more of each step's words) up to a readable maximum
  if (maxW) nodeW = Math.max(nodeW, Math.min(NODE_MAX_W, Math.floor((maxW - 2 * PAD - (usedCols - 1) * GAP_X) / usedCols)));
  const width = PAD * 2 + usedCols * nodeW + (usedCols - 1) * GAP_X;
  const pos = {}, frames = [];
  let y = PAD;
  for (const r of kept) {
    const h = r[0].kind === 'source' ? SRC_H : stepH;
    const framed = !!r.group;
    if (framed) y += GROUP_HEAD;
    const top = y;
    const per = cols === Infinity ? r.length : cols;
    let minX = Infinity, maxX = -Infinity;
    for (let i = 0; i < r.length; i += per) {
      const chunk = r.slice(i, i + per);
      const rowW = chunk.length * nodeW + (chunk.length - 1) * GAP_X;
      let x = (width - rowW) / 2;
      for (const n of chunk) {
        pos[n.id] = { x, y, w: nodeW, h, node: n };
        minX = Math.min(minX, x); maxX = Math.max(maxX, x + nodeW);
        x += nodeW + GAP_X;
      }
      y += h + WRAP_GAP;
    }
    y -= WRAP_GAP;
    if (framed) {
      frames.push({ x: minX - 6, y: top - GROUP_HEAD, w: maxX - minX + 12, h: y - top + GROUP_HEAD + 6,
                    group: r.group, ids: r.map(n => n.id) });
    }
    y += GAP_Y;
  }
  return { pos, width, height: y - GAP_Y + PAD, rows: kept, frames, cols, nodeW, tablesInSteps };
}

/** The worst finding severity per step id. Pure — exported for the gate. */
export function stepSeverity(findings) {
  const out = {};
  for (const f of findings || []) {
    for (const id of f.steps || []) {
      if (!(id in out) || SEV_RANK[f.severity] < SEV_RANK[out[id]]) out[id] = f.severity;
    }
  }
  return out;
}

/** The zoom that shows the whole plan in a viewport (layout reflows at each zoom), or the
 *  smallest zoom when none does. Pure — exported for the gate. */
export function fitAllZoom(spec, width, height, tables = 'auto') {
  if (!(width > 0) || !(height > 0)) return 1;
  for (const z of [...ZOOMS].filter(z => z <= 1).reverse()) {
    const L = layoutPlan(spec, { maxWidth: width / z, tables });
    if (L.height * z <= height && L.width * z <= width + 1) return z;
  }
  return ZOOMS[0];
}

function _stepLines(s) {
  const head = `${s.id} · ${s.op}${s.scope ? ' · ' + s.scope : ''}`;
  const bits = [];
  if (s.join_method) bits.push(s.join_method);
  if (s.output) bits.push(`→ ${s.output}${s.distribution && s.distribution !== 'local' ? ' (' + s.distribution + ')' : ''}`);
  if (s.est_rows != null) bits.push(`${Number(s.est_rows).toLocaleString('en-US')} row${s.est_rows === 1 ? '' : 's'}${s.confidence ? ', ' + s.confidence + ' confidence' : ''}`);
  return [head, bits.join(' · ')];
}

function _tablesOf(s) { return (s.inputs || []).filter(i => !String(i).startsWith('Spool ')); }

function _clip(text, max) { return text.length > max ? text.slice(0, Math.max(1, max - 1)) + '…' : text; }

function _parLabel(ids) {
  if (!ids.length) return 'in parallel';
  return `in parallel · steps ${ids.length > 2 ? ids[0] + '–' + ids[ids.length - 1] : ids.join(', ')}`;
}

/** The graph as SVG markup, and the layout it was drawn from. */
function _svg(spec, sev, resultSteps, { preview = false, maxWidth = 0, tables = 'auto', zoom = 1 } = {}) {
  const L = layoutPlan(spec, { maxWidth, tables });
  const dw = Math.round(L.width * zoom), dh = Math.round(L.height * zoom);
  const parts = [`<svg width="${dw}" height="${dh}" viewBox="0 0 ${L.width} ${L.height}" role="img" aria-label="Query plan graph"${preview ? ' aria-hidden="true" preserveAspectRatio="xMidYMin meet"' : ''}>`];
  for (const f of L.frames) {
    parts.push(`<g class="xplan-par"><rect x="${f.x}" y="${f.y}" width="${f.w}" height="${f.h}" rx="8"/>`
      + `<text x="${f.x + 8}" y="${f.y + 11}">${_esc(_parLabel(f.ids))}</text></g>`);
  }
  for (const e of spec.edges || []) {
    const a = L.pos[e.from], b = L.pos[e.to];
    if (!a || !b) continue;
    const x1 = a.x + a.w / 2, y1 = a.y + a.h, x2 = b.x + b.w / 2, y2 = b.y, my = (y1 + y2) / 2;
    const d = `M${x1},${y1} C${x1},${my} ${x2},${my} ${x2},${y2}`;
    parts.push(preview ? `<path class="xplan-edge" d="${d}"/>`
      : `<g class="xplan-edge-g"><title>${_esc(e.via)}</title><path class="xplan-edge" data-from="${_esc(e.from)}" data-to="${_esc(e.to)}" d="${d}"/></g>`);
  }
  const maxSec = Math.max(0, ...(spec.steps || []).map(s => Number(s.est_seconds) || 0));
  const c1 = Math.floor((L.nodeW - 16) / 6.1), c2 = Math.floor((L.nodeW - 16) / 5.2);
  for (const id of Object.keys(L.pos)) {
    const p = L.pos[id], n = p.node;
    if (n.kind === 'source') {
      parts.push(`<g class="xplan-src"><rect x="${p.x}" y="${p.y}" width="${p.w}" height="${p.h}" rx="4"/><text x="${p.x + 8}" y="${p.y + 18}">${_esc(_clip(n.label, c2 - 8))}</text></g>`);
      continue;
    }
    const s = n.step, [l1, l2] = _stepLines(s);
    const cls = ['xplan-node', sev[s.id] && sev[s.id] !== 'info' ? `sev-${sev[s.id]}` : '', resultSteps.includes(s.id) ? 'result' : ''].join(' ');
    const tabs = _tablesOf(s);
    const label = `Step ${l1}. ${l2}${tabs.length ? '. Reads ' + tabs.join(', ') : ''}${sev[s.id] ? '. Finding: ' + sev[s.id] : ''}`;
    const share = maxSec > 0 && s.est_seconds > 0 ? s.est_seconds / maxSec : 0;
    // the opening tag differs (a preview node is not a tab stop); the box and its lines are
    // the same — the parentheses keep them on BOTH branches
    parts.push((preview ? `<g class="${cls}" data-step="${_esc(s.id)}">`
      : `<g class="${cls}" data-step="${_esc(s.id)}" tabindex="0" role="button" aria-label="${_esc(label)}">`)
      + `<rect class="box" x="${p.x}" y="${p.y}" width="${p.w}" height="${p.h}" rx="5"/>`
      + `<text x="${p.x + 8}" y="${p.y + 18}">${_esc(_clip(l1, c1))}</text>`
      + `<text class="sub" x="${p.x + 8}" y="${p.y + 34}">${_esc(_clip(l2, c2))}</text>`
      + (L.tablesInSteps && tabs.length ? `<text class="sub" x="${p.x + 8}" y="${p.y + 50}">${_esc(_clip('reads ' + tabs.join(', '), c2))}</text>` : '')
      + (!preview && share > 0 ? `<rect class="tbar" x="${p.x + 1}" y="${p.y + p.h - 4}" width="${Math.max(2, (p.w - 2) * share).toFixed(1)}" height="3" rx="1.5"/>` : '')
      + `</g>`);
  }
  parts.push('</svg>');
  return { html: parts.join(''), L };
}

function _detail(s) {
  const facts = [];
  if (s.inputs && s.inputs.length) facts.push(`reads ${s.inputs.map(_esc).join(' and ')}`);
  if (s.access && s.access.length) facts.push(`by way of ${s.access.map(_esc).join(' / ')}`);
  if (s.join_method) facts.push(_esc(s.join_method));
  if (s.output) facts.push(`into ${_esc(s.output)}${s.distribution && s.distribution !== 'local' ? ' (' + _esc(s.distribution) + ')' : ''}`);
  if (s.est_rows != null) facts.push(`${Number(s.est_rows).toLocaleString('en-US')} rows estimated${s.confidence ? ' with ' + _esc(s.confidence) + ' confidence' : ''}`);
  if (s.est_seconds != null) facts.push(`estimated ${s.est_seconds} s`);
  return `<strong>Step ${_esc(s.id)} · ${_esc(s.op)}</strong>${facts.length ? ' — ' + facts.join(', ') : ''}`
    + `<details class="xplan-detail-text"><summary>The step in Teradata's words</summary><div>${_esc(s.text)}</div></details>`;
}

/** The identity of a plan, so a card can tell whether the panel shows ITS plan. */
function _planKey(s) {
  return JSON.stringify([s.origin || 'agent', s.statement, s.sql, (s.steps || []).map(x => x.text)]);
}
/** A short id for the plan (the open control carries it in an attribute). */
function _planId(s) {
  const t = _planKey(s);
  let h = 5381;
  for (let i = 0; i < t.length; i++) h = ((h * 33) ^ t.charCodeAt(i)) >>> 0;
  return 'p' + h.toString(16) + '-' + t.length.toString(16);
}
const _cardSpecs = new Map();   // plan id -> spec, so the control opens exactly that plan

/** True when the plan panel is open and shows this plan. */
export function isPlanExpanded(spec) {
  const panel = document.getElementById('explain-plan-split-panel');
  if (!panel || !panel.classList.contains('xplan-split--open')) return false;
  const cur = _history[_shownIndex()];
  return !!cur && _planKey(cur) === _planKey(spec);
}

/** The panel reports which plan it shows (or that it is closed); every plan card's
 *  shared open control follows — the same rule as every other component card. */
function _syncPlanCards() {
  const panel = document.getElementById('explain-plan-split-panel');
  const open = !!(panel && panel.classList.contains('xplan-split--open'));
  const cur = open ? _history[_shownIndex()] : null;
  try { window.PanelOpenControl && window.PanelOpenControl.shown('explain_plan', cur ? _planId(cur) : null); } catch (_) {}
}
if (typeof window !== 'undefined') {
  // Registered through the queue: this renderer may load before panelOpenControl.js.
  (window.__panelOpenQueue = window.__panelOpenQueue || []).push(['explain_plan', {
    open(id) { const spec = _cardSpecs.get(id); if (spec) openExplainPlanPanel(spec); },
    close() { closeExplainPlanPanel(); },
  }]);
  if (window.PanelOpenControl && window.PanelOpenControl.drain) window.PanelOpenControl.drain();
}

/** In the answer: the graph only, as a preview, and the shared open control. The control
 *  opens the plan in the side panel (findings, key, the selected step, the statement,
 *  compare, re-explain); while its plan is open there it closes the panel. Clicking the
 *  preview is the same gesture as the control. */
function _renderCard(host, spec) {
  const steps = spec.steps || [];
  const work = steps.filter(s => !['LOCK', 'END TRANSACTION', 'PARALLEL'].includes(s.op));
  const n = (spec.findings || []).length;
  const name = `query plan, ${work.length} step${work.length === 1 ? '' : 's'}${n ? `, ${n} finding${n === 1 ? '' : 's'}` : ''},`;
  const id = _planId(spec);
  _cardSpecs.set(id, spec);
  // the preview is laid out for about three columns (the width of an answer), wrapping a long
  // parallel group and folding many source tables into their steps
  const pv0 = _svg(spec, {}, [], { preview: true, maxWidth: 780 });
  const ctl = (typeof window !== 'undefined' && window.PanelOpenControl)
    ? window.PanelOpenControl.html({ component: 'explain_plan', id, accent: '#f97316',
                                     panelId: 'explain-plan-split-panel', name }) : '';
  host.innerHTML = `<div class="xplan xplan-card">`
    + `<div class="xplan-card-preview" aria-hidden="true">${pv0.html}</div>`
    + `${ctl}</div>`;
  const card = host.querySelector('.xplan-card');
  card._xplanSpec = spec;
  const pv = card.querySelector('.xplan-card-preview');
  if (pv && pv0.L.height * Math.min(1, 760 / pv0.L.width) > 308 * 1.05) pv.classList.add('is-cut');
  pv?.addEventListener('click', () => {
    const btn = card.querySelector('.sp-open-ctl');
    if (btn && window.PanelOpenControl) window.PanelOpenControl._onClick(btn);
  });
}

function _pref(key, fallback, allowed) {
  try { const v = localStorage.getItem(key); if (v && (!allowed || allowed.includes(v))) return v; } catch (_) { /* storage optional */ }
  return fallback;
}
function _savePref(key, v) { try { localStorage.setItem(key, v); } catch (_) { /* storage optional */ } }

const KIND_LABEL = {
  product_join: 'product join', no_confidence: 'estimate without statistics', duplicated: 'Spool duplicated to all AMPs',
  redistributed: 'Rows redistributed between AMPs', full_scan: 'All-rows scan (no index used)', hot_step: 'One step carries most of the time',
};

const _stepKey = id => String(id).split('.').map(n => String(Number(n) || 0).padStart(5, '0')).join('.');

/** Findings as list items, worst first: findings of one KIND folded into one line — the
 *  finding named once, its explanation once, a chip per step in execution order (four
 *  identical product-join sentences, or eighteen "redistributes" lines, say less than one line
 *  that names the steps). A kind found once keeps its own sentence. Severity is kept per
 *  kind. Pure — exported for the gate. */
export function groupFindings(findings) {
  const out = [];
  const byKind = new Map();
  for (const f of findings || []) {
    if (!byKind.has(f.kind)) {
      const g = { severity: f.severity, kind: f.kind, steps: [], texts: [], grouped: true };
      byKind.set(f.kind, g); out.push(g);
    }
    const g = byKind.get(f.kind);
    if (SEV_RANK[f.severity] < SEV_RANK[g.severity]) g.severity = f.severity;
    for (const id of f.steps || []) if (!g.steps.includes(id)) g.steps.push(id);
    g.texts.push(f.text);
  }
  for (const g of out) {
    g.steps.sort((a, b) => (_stepKey(a) < _stepKey(b) ? -1 : _stepKey(a) > _stepKey(b) ? 1 : 0));
    if (g.texts.length === 1) { g.text = g.texts[0]; g.grouped = false; continue; }
    const t = g.texts[0] || '';
    const i = t.indexOf(' — ');
    g.why = i >= 0 ? t.slice(i + 3) : '';
  }
  out.sort((a, b) => SEV_RANK[a.severity] - SEV_RANK[b.severity]);
  return out;
}

const _ICO_MINUS = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" d="M5 12h14"/></svg>';
const _ICO_PLUS = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" d="M12 5v14M5 12h14"/></svg>';
const _ICO_FITW = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M4 12h16M7 9l-3 3 3 3M17 9l3 3-3 3"/></svg>';
const _ICO_FITALL = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>';
const _ICO_PREV = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M15 18l-6-6 6-6"/></svg>';
const _ICO_NEXT = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 6l6 6-6 6"/></svg>';
const _ICO_X = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" d="M6 6l12 12M18 6L6 18"/></svg>';

export function renderExplainPlan(containerId, payload, opts = {}) {
  _css();
  const host = document.getElementById(containerId);
  if (!host) return;
  if (host._xplanRO) { try { host._xplanRO.disconnect(); } catch (_) {} host._xplanRO = null; }
  let p = payload;
  if (typeof p === 'string') { try { p = JSON.parse(p); } catch (_) { p = {}; } }
  const spec = (p && p.spec) ? p.spec : (p || {});
  if (spec.error) { host.innerHTML = `<div class="xplan">${_esc(spec.error)}</div>`; return; }
  const canPanel = !opts.inPanel && !spec._dashboardFill;
  if (canPanel) { _renderCard(host, spec); return; }

  const steps = spec.steps || [];
  const byId = Object.fromEntries(steps.map(s => [s.id, s]));
  const sev = stepSeverity(spec.findings);
  const result = spec.result_steps || [];
  const work = steps.filter(s => !NON_WORK.includes(s.op));
  const order = work.map(s => s.id);
  const findings = (spec.findings || []);
  const who = spec.statement != null ? `Statement ${_esc(spec.statement)}` : 'The canvas statement';
  const nDanger = findings.filter(f => f.severity === 'danger').length;
  const nWarn = findings.filter(f => f.severity === 'warn').length;
  const groups = new Set(work.map(s => s.parallel_group).filter(Boolean));
  const totalSec = spec.total_seconds != null ? Number(spec.total_seconds)
    : work.reduce((a, s) => a + (Number(s.est_seconds) || 0), 0);

  // ── Overview ─────────────────────────────────────────────────────────────────────────
  // the row's summary already says "Plan of statement N · K steps · total est. S s": inside a
  // row those two spans are hidden by the row's one rule ([data-ans-restates]); the cut notice stays
  const head = `<div class="xplan-head"><span data-ans-restates><strong>${who}</strong> · ${work.length} step${work.length === 1 ? '' : 's'}</span>`
    + (spec.total_seconds != null ? `<span data-ans-restates>total estimated ${_esc(spec.total_seconds)} s</span>` : '')
    + (spec.truncated ? `<span>the plan text was cut — later steps are missing</span>` : '')
    + `</div>`;
  const stat = (v, label, cls = '') => `<div class="xplan-stat ${cls}"><b>${_esc(v)}</b><span>${_esc(label)}</span></div>`;
  const stats = `<div class="xplan-stats">`
    + stat(work.length, work.length === 1 ? 'step' : 'steps')
    + (groups.size ? stat(groups.size, groups.size === 1 ? 'parallel group' : 'parallel groups') : '')
    + stat((spec.sources || []).length, (spec.sources || []).length === 1 ? 'table' : 'tables')
    + (spec.total_seconds != null ? stat(`${spec.total_seconds} s`, 'estimated') : '')
    + (nDanger ? stat(nDanger, nDanger === 1 ? 'needs attention' : 'need attention', 'danger') : '')
    + (nWarn ? stat(nWarn, nWarn === 1 ? 'worth a look' : 'worth a look', 'warn') : '')
    + `</div>`;
  const chips = ids => `<span class="xplan-chips">${ids.map(id => `<button type="button" class="xplan-chip" data-step="${_esc(id)}" aria-label="Show step ${_esc(id)}">${_esc(id)}</button>`).join('')}</span>`;
  const items = groupFindings(findings).map(f => {
    if (f.grouped) {
      const what = KIND_LABEL[f.kind] || f.kind.replace(/_/g, ' ');
      return `<li><span class="xplan-sev ${_esc(f.severity)}" aria-hidden="true"></span><span><span class="${f.severity === 'info' ? 'xplan-note-txt' : 'xplan-find-what'}">${_esc(what.charAt(0).toUpperCase() + what.slice(1))} in ${f.steps.length} steps</span>${chips(f.steps)}`
        + `${f.why ? `<span class="xplan-find-why">${_esc(f.why)}</span>` : ''}</span></li>`;
    }
    return `<li><span class="xplan-sev ${_esc(f.severity)}" aria-hidden="true"></span><button type="button" class="xplan-find-btn" data-steps="${_esc((f.steps || []).join(','))}">${_esc(f.text)}</button></li>`;
  });
  const list = findings.length
    ? `<div class="xplan-sec">Findings</div><ul class="xplan-find" aria-label="Findings">${items.join('')}</ul>`
    : `<div class="xplan-head">No product join, no estimate without statistics, no data movement and no full scan in this plan.</div>`;
  const hotSteps = totalSec > 0 ? work.filter(s => Number(s.est_seconds) > 0)
    .sort((a, b) => b.est_seconds - a.est_seconds).slice(0, 5) : [];
  const hot = hotSteps.length > 1 ? `<div class="xplan-sec">Where the time goes</div><div class="xplan-hot">${hotSteps.map(s => {
    const pct = Math.round(100 * s.est_seconds / totalSec);
    return `<button type="button" class="xplan-chip" data-step="${_esc(s.id)}" aria-label="Show step ${_esc(s.id)}">Step ${_esc(s.id)} · ${_esc(s.op)}</button>`
      + `<span class="xplan-hot-bar" aria-hidden="true"><i style="width:${Math.max(2, pct)}%"></i></span>`
      + `<span class="xplan-hot-v">${_esc(s.est_seconds)} s · ${pct}%</span>`;
  }).join('')}</div>` : '';
  // The bookkeeping steps the graph leaves out, named so the step numbering has no silent gap.
  const bk = [];
  const lockSteps = steps.filter(s => s.op === 'LOCK');
  if (lockSteps.length) {
    const ids = lockSteps.map(s => s.id);
    const span = ids.length === 1 ? `Step ${ids[0]}` : (ids.length === Number(ids[ids.length - 1]) - Number(ids[0]) + 1 && ids.every(i => /^\d+$/.test(i)) ? `Steps ${ids[0]}–${ids[ids.length - 1]}` : `Steps ${ids.join(', ')}`);
    const tables = {};
    for (const s of lockSteps) for (const l of (s.locks || [])) (tables[l.mode.toLowerCase()] ||= new Set()).add(l.table);
    const how = Object.entries(tables).map(([m, set]) => `${[...set].join(', ')} for ${m}`).join('; ');
    bk.push(how ? `${span} ${ids.length === 1 ? 'locks' : 'lock'} ${how}` : `${span} ${ids.length === 1 ? 'takes' : 'take'} the table locks`);
  }
  const endTx = steps.filter(s => s.op === 'END TRANSACTION').map(s => s.id);
  if (endTx.length) bk.push(`step ${endTx.join(', ')} ends the transaction`);
  const bookkeeping = bk.length ? `<div class="xplan-bk">${_esc(bk.join(' · '))} — not drawn; they do no work on the data.</div>` : '';

  // ── Graph ────────────────────────────────────────────────────────────────────────────
  // The key: what each mark on THIS graph means — only the marks the plan uses. A mark with
  // no key is a guess.
  const KIND_WORD = { product_join: 'product join', no_confidence: 'estimate without statistics' };
  const keyItems = [];
  for (const sevName of ['danger', 'warn']) {
    const kinds = [...new Set(findings.filter(f => f.severity === sevName).map(f => KIND_WORD[f.kind] || f.kind.replace(/_/g, ' ')))];
    if (kinds.length) keyItems.push(`<span><i class="xplan-key-sw sev-${sevName}" aria-hidden="true"></i>${_esc(kinds.join(', '))}</span>`);
  }
  keyItems.push(`<span><i class="xplan-key-sw sel" aria-hidden="true"></i>selected step</span>`);
  if (groups.size) keyItems.push(`<span><i class="xplan-key-sw par" aria-hidden="true"></i>steps run in parallel</span>`);
  if (work.some(s => Number(s.est_seconds) > 0)) keyItems.push(`<span><i class="xplan-key-sw tbar" aria-hidden="true"></i>share of the estimated time</span>`);
  const keyOpen = _pref('xplan_key', '', ['open', 'closed']) ? _pref('xplan_key', '', ['open', 'closed']) === 'open'
    : (host.clientWidth || 0) >= 560;   // narrow, or still opening: one click away
  const key = `<div class="xplan-key" id="${_esc(containerId)}-key" aria-label="Graph key"${keyOpen ? '' : ' hidden'}>${keyItems.join('')}<span class="xplan-key-note">other findings are in the Overview</span></div>`;
  const gtools = `<div class="xplan-gtools" role="toolbar" aria-label="Graph view">`
    + `<span class="xplan-seg"><button type="button" class="xplan-zoom-out" aria-label="Zoom out — more of the plan at once" title="Zoom out — more of the plan at once">${_ICO_MINUS}</button>`
    + `<button type="button" class="xplan-zoom-v" title="Zoom" aria-live="polite">100%</button>`
    + `<button type="button" class="xplan-zoom-in" aria-label="Zoom in" title="Zoom in">${_ICO_PLUS}</button></span>`
    + `<span class="xplan-seg"><button type="button" class="xplan-fit-w" title="Fit the width of the panel (readable size)" aria-label="Fit width">${_ICO_FITW}<span class="lbl">Fit width</span></button>`
    + `<button type="button" class="xplan-fit-all" title="Show the whole plan at once" aria-label="Whole plan">${_ICO_FITALL}<span class="lbl">Whole plan</span></button></span>`
    + ((spec.sources || []).length ? `<span class="xplan-seg" role="group" aria-label="Where the source tables are drawn">`
      + `<button type="button" class="xplan-tbl" data-tables="lane" title="A row of source tables above the steps, an edge to each step that reads them">Tables in a row</button>`
      + `<button type="button" class="xplan-tbl" data-tables="steps" title="Each step names the tables it reads — fewer edges on a large plan">in steps</button></span>` : '')
    + `<span class="xplan-bar-gap"></span>`
    + `<span class="xplan-seg"><button type="button" class="xplan-key-toggle${keyOpen ? ' on' : ''}" aria-expanded="${keyOpen}" aria-controls="${_esc(containerId)}-key" title="What the marks on the graph mean">Key</button></span>`
    + (opts.graphNote ? `<span class="xplan-link-note">${_esc(opts.graphNote)}</span>` : '')
    + `</div>`;

  // ── Steps ────────────────────────────────────────────────────────────────────────────
  const stepsPane = `<div class="xplan-st-tools"><span class="xplan-seg" role="group" aria-label="Sort the steps">`
    + `<button type="button" class="xplan-sort on" data-sort="order">In order</button>`
    + `<button type="button" class="xplan-sort" data-sort="time">By time</button>`
    + `<button type="button" class="xplan-sort" data-sort="rows">By rows</button></span>`
    + (findings.length ? `<label><input type="checkbox" class="xplan-only-find"> Only steps with a finding</label>` : '')
    + `</div><div class="xplan-steps-host"></div>`;

  const sql = spec.sql ? `<div class="xplan-sql"><pre>${_esc(spec.sql)}</pre></div>` : '';

  const SEGS = ['overview', 'graph', 'steps', ...(spec.sql ? ['statement'] : [])];
  const st = {
    seg: _pref('xplan_segment', 'overview', SEGS), zoom: 1,
    tables: _pref('xplan_tables', 'auto', ['auto', 'lane', 'steps']), sel: null, sort: 'order', onlyFind: false,
  };
  const worst = nDanger ? 'danger' : nWarn ? 'warn' : '';
  const tab = (k, label, n, cls = '') => `<button type="button" class="ind-tab ind-tab--underline xplan-tab" role="tab" data-seg="${k}" id="${_esc(containerId)}-tab-${k}" aria-controls="${_esc(containerId)}-pane-${k}">${label}${n != null && n !== '' ? ` <span class="xplan-tab-n ${cls}">${n}</span>` : ''}</button>`;
  const tabs = `<div class="xplan-tabs" role="tablist" aria-label="EXPLAIN plan">`
    + tab('overview', 'Overview', nDanger + nWarn || '', worst)
    + tab('graph', 'Graph')
    + tab('steps', 'Steps', work.length)
    + (spec.sql ? tab('statement', 'Statement') : '')
    + `</div>`;
  // The first drawing is in the markup (the width the host has now); draw() redraws only when
  // the graph's own width differs — e.g. it was measured while its segment was hidden.
  const w0 = host.clientWidth ? host.clientWidth - 4 : (opts.graphWidth || 0);
  const first = _svg(spec, sev, result, { maxWidth: w0 ? (w0 - 2) / st.zoom : 0, tables: st.tables, zoom: st.zoom });
  const pane = (k, inner, cls = '') => `<div class="xplan-pane ${cls}" data-pane="${k}" role="tabpanel" id="${_esc(containerId)}-pane-${k}" aria-labelledby="${_esc(containerId)}-tab-${k}" hidden>${inner}</div>`;
  host.innerHTML = `<div class="xplan${spec._dashboardFill ? ' fill' : ''}">`
    + tabs
    + pane('overview', `${head}${stats}${opts.prelude || ''}${list}${hot}${bookkeeping}`, 'scroll')
    + pane('graph', `${gtools}${key}<div class="xplan-gbody"><div class="xplan-graph">${first.html}</div><div class="xplan-detail" hidden></div></div>`, 'xplan-pane-graph')
    + pane('steps', stepsPane, 'scroll')
    + (spec.sql ? pane('statement', sql, 'scroll') : '')
    + `</div>`;
  const root = host.querySelector('.xplan');
  root.classList.add('seg');
  const gEl = host.querySelector('.xplan-graph');
  const detail = host.querySelector('.xplan-detail');
  let drawnFor = w0;

  const measure = () => (gEl && gEl.clientWidth) || (host.clientWidth ? host.clientWidth - 4 : 0) || (opts.graphWidth || 0);
  const nodeEl = id => [...gEl.querySelectorAll('.xplan-node')].find(n => n.dataset.step === id);

  const markSelection = (reveal) => {
    const id = st.sel;
    gEl.querySelectorAll('.xplan-node').forEach(n => { n.classList.remove('sel'); n.classList.remove('rel'); });
    gEl.querySelectorAll('.xplan-edge').forEach(e => e.classList.remove('on'));
    gEl.classList.toggle('has-sel', !!id);
    if (!id) return;
    const node = nodeEl(id);
    if (node) {
      node.classList.add('sel');
      if (reveal && node.scrollIntoView) { try { node.scrollIntoView({ block: 'nearest', inline: 'nearest' }); } catch (_) {} }
    }
    gEl.querySelectorAll('.xplan-edge').forEach(e => {
      if (e.dataset.to !== id && e.dataset.from !== id) return;
      e.classList.add('on');
      const other = nodeEl(e.dataset.to === id ? e.dataset.from : e.dataset.to);
      if (other) other.classList.add('rel');
    });
  };

  const draw = (reuse) => {
    if (!gEl) return;
    let L;
    if (reuse) L = reuse.L;
    else {
      const w = measure();
      const out = _svg(spec, sev, result, { maxWidth: w ? (w - 2) / st.zoom : 0, tables: st.tables, zoom: st.zoom });
      gEl.innerHTML = out.html; L = out.L; drawnFor = w;
    }
    gEl.querySelectorAll('.xplan-node').forEach(n => {
      n.addEventListener('click', () => select(n.dataset.step, { from: 'graph' }));
      n.addEventListener('keydown', e => {
        const i = order.indexOf(n.dataset.step);
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(n.dataset.step, { from: 'graph' }); }
        else if ((e.key === 'ArrowDown' || e.key === 'ArrowRight') && i < order.length - 1) { e.preventDefault(); select(order[i + 1], { from: 'graph', focus: true }); }
        else if ((e.key === 'ArrowUp' || e.key === 'ArrowLeft') && i > 0) { e.preventDefault(); select(order[i - 1], { from: 'graph', focus: true }); }
        else if (e.key === 'Escape') { e.preventDefault(); clearSel(); }
      });
    });
    host.querySelectorAll('.xplan-tbl').forEach(b => {
      const on = (b.dataset.tables === 'steps') === L.tablesInSteps;
      b.classList.toggle('on', on); b.setAttribute('aria-pressed', String(on));
    });
    const zv = host.querySelector('.xplan-zoom-v');
    if (zv) zv.textContent = `${Math.round(st.zoom * 100)}%`;
    const zo = host.querySelector('.xplan-zoom-out'), zi = host.querySelector('.xplan-zoom-in');
    if (zo) zo.disabled = st.zoom <= ZOOMS[0];
    if (zi) zi.disabled = st.zoom >= ZOOMS[ZOOMS.length - 1];
    // the key follows the width until the reader chooses: shown when there is room for it
    if (!_pref('xplan_key', '', ['open', 'closed'])) setKey(measure() >= 560);
    markSelection(false);
  };

  function setKey(open) {
    const k = host.querySelector('.xplan-key'), btn = host.querySelector('.xplan-key-toggle');
    if (!k) return;
    k.hidden = !open;
    if (btn) { btn.classList.toggle('on', open); btn.setAttribute('aria-expanded', String(open)); }
  }

  const showDetail = () => {
    const s = byId[st.sel];
    if (!s || !detail) { if (detail) detail.hidden = true; return; }
    const i = order.indexOf(s.id);
    detail.hidden = false;
    detail.innerHTML = `<span class="xplan-detail-nav xplan-seg">`
      + `<button type="button" class="xplan-dprev" aria-label="Previous step"${i <= 0 ? ' disabled' : ''}>${_ICO_PREV}</button>`
      + `<button type="button" class="xplan-dnext" aria-label="Next step"${i < 0 || i >= order.length - 1 ? ' disabled' : ''}>${_ICO_NEXT}</button>`
      + `<button type="button" class="xplan-dclose" aria-label="Clear the selection">${_ICO_X}</button></span>` + _detail(s);
    detail.querySelector('.xplan-dprev')?.addEventListener('click', () => select(order[i - 1], { from: 'graph', focus: true }));
    detail.querySelector('.xplan-dnext')?.addEventListener('click', () => select(order[i + 1], { from: 'graph', focus: true }));
    detail.querySelector('.xplan-dclose')?.addEventListener('click', clearSel);
  };

  const clearSel = () => { st.sel = null; markSelection(false); if (detail) detail.hidden = true; };

  function select(id, { from = 'other', focus = false } = {}) {
    if (!id || !byId[id]) return;
    st.sel = id;
    if (from !== 'graph' && st.seg !== 'graph') setSeg('graph');
    markSelection(true);
    showDetail();
    if (focus) { const n = nodeEl(id); if (n && n.focus) { try { n.focus(); } catch (_) {} } }
    if (typeof opts.onSelectStep === 'function') { try { opts.onSelectStep(byId[id]); } catch (_) { /* a link, never a failure */ } }
  }

  function setSeg(k, save = true) {
    if (!SEGS.includes(k)) k = 'overview';
    st.seg = k;
    if (save) _savePref('xplan_segment', k);
    host.querySelectorAll('.xplan-tab').forEach(t => {
      const on = t.dataset.seg === k;
      t.classList.toggle('active', on);
      t.setAttribute('aria-selected', String(on));
      t.setAttribute('tabindex', on ? '0' : '-1');
    });
    host.querySelectorAll('.xplan-pane').forEach(pn => { pn.hidden = pn.dataset.pane !== k; });
    // the graph is laid out for the width it is SHOWN in — measure again once it is visible
    if (k === 'graph' && Math.abs(measure() - drawnFor) > 12) draw();
  }

  const renderSteps = () => {
    const hostEl = host.querySelector('.xplan-steps-host');
    if (!hostEl) return;
    let rows = steps.filter(s => s.op !== 'PARALLEL');
    if (st.onlyFind) rows = rows.filter(s => sev[s.id]);
    if (st.sort === 'time') rows = [...rows].sort((a, b) => (Number(b.est_seconds) || 0) - (Number(a.est_seconds) || 0));
    else if (st.sort === 'rows') rows = [...rows].sort((a, b) => (Number(b.est_rows) || 0) - (Number(a.est_rows) || 0));
    const body = rows.map(s => {
      const isWork = !NON_WORK.includes(s.op);
      const f = sev[s.id];
      const reads = (s.inputs || []).join(', ');
      const flow = `${_esc(reads || (s.op === 'LOCK' ? (s.locks || []).map(l => l.table).join(', ') : ''))}${s.output ? ` → ${_esc(s.output)}` : ''}${s.distribution && s.distribution !== 'local' ? ` (${_esc(s.distribution)})` : ''}`;
      return `<tr class="${isWork ? 'xplan-row' : 'bk'}" data-step="${_esc(s.id)}">`
        + `<td>${f ? `<span class="xplan-sev ${_esc(f)}" title="${_esc(f)}" style="display:inline-block;margin:0 5px 0 0"></span>` : ''}`
        + (isWork ? `<button type="button" class="xplan-row-btn" data-step="${_esc(s.id)}" aria-label="Show step ${_esc(s.id)} in the graph">${_esc(s.id)}</button>` : _esc(s.id)) + `</td>`
        + `<td>${_esc(s.op)}${s.join_method ? `<div class="xplan-st-sub">${_esc(s.join_method)}</div>` : ''}${flow ? `<div class="xplan-st-sub xplan-st-flow-inline">${flow}</div>` : ''}</td>`
        + `<td class="mut xplan-st-flow">${flow}</td>`
        + `<td class="num">${s.est_rows != null ? Number(s.est_rows).toLocaleString('en-US') : ''}${s.confidence ? `<div class="xplan-st-sub">${_esc(s.confidence)}</div>` : ''}</td>`
        + `<td class="num">${s.est_seconds != null ? `${_esc(s.est_seconds)} s` : ''}</td></tr>`;
    }).join('');
    hostEl.innerHTML = `<table class="xplan-steps"><thead><tr><th>Step</th><th>Operation</th><th class="xplan-st-flow">Reads → into</th><th style="text-align:right">Rows</th><th style="text-align:right">Time</th></tr></thead><tbody>${body || '<tr><td colspan="5" class="mut">No step matches.</td></tr>'}</tbody></table>`;
    hostEl.querySelectorAll('.xplan-row').forEach(r => r.addEventListener('click', () => select(r.dataset.step)));
    hostEl.querySelectorAll('.xplan-row-btn').forEach(b => b.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(b.dataset.step); } }));
  };

  // ── wiring ───────────────────────────────────────────────────────────────────────────
  host.querySelectorAll('.xplan-tab').forEach(t => {
    t.addEventListener('click', () => setSeg(t.dataset.seg));
    t.addEventListener('keydown', e => {
      const i = SEGS.indexOf(t.dataset.seg);
      const to = e.key === 'ArrowRight' ? SEGS[(i + 1) % SEGS.length] : e.key === 'ArrowLeft' ? SEGS[(i - 1 + SEGS.length) % SEGS.length] : null;
      if (!to) return;
      e.preventDefault(); setSeg(to);
      const nt = [...host.querySelectorAll('.xplan-tab')].find(x => x.dataset.seg === to);
      if (nt && nt.focus) { try { nt.focus(); } catch (_) {} }
    });
  });
  host.querySelectorAll('.xplan-find-btn').forEach(b => {
    b.addEventListener('click', () => { const first = (b.dataset.steps || '').split(',')[0]; if (first) select(first); });
  });
  host.querySelectorAll('.xplan-chip').forEach(b => b.addEventListener('click', () => select(b.dataset.step)));
  const zoomTo = z => { st.zoom = z; draw(); markSelection(true); };
  host.querySelector('.xplan-zoom-out')?.addEventListener('click', () => { const i = ZOOMS.indexOf(st.zoom); if (i > 0) zoomTo(ZOOMS[i - 1]); });
  host.querySelector('.xplan-zoom-in')?.addEventListener('click', () => { const i = ZOOMS.indexOf(st.zoom); if (i < ZOOMS.length - 1) zoomTo(ZOOMS[i + 1]); });
  host.querySelector('.xplan-zoom-v')?.addEventListener('click', () => zoomTo(1));
  host.querySelector('.xplan-fit-w')?.addEventListener('click', () => zoomTo(1));
  host.querySelector('.xplan-fit-all')?.addEventListener('click', () => zoomTo(fitAllZoom(spec, measure() - 2, (gEl && gEl.clientHeight) - 2, st.tables)));
  host.querySelectorAll('.xplan-tbl').forEach(b => b.addEventListener('click', () => {
    st.tables = b.dataset.tables; _savePref('xplan_tables', st.tables); draw(); markSelection(true);
  }));
  host.querySelector('.xplan-key-toggle')?.addEventListener('click', () => {
    const k = host.querySelector('.xplan-key');
    if (!k) return;
    setKey(k.hidden);
    _savePref('xplan_key', k.hidden ? 'closed' : 'open');
  });
  host.querySelectorAll('.xplan-sort').forEach(b => b.addEventListener('click', () => {
    st.sort = b.dataset.sort;
    host.querySelectorAll('.xplan-sort').forEach(x => x.classList.toggle('on', x === b));
    renderSteps();
  }));
  host.querySelector('.xplan-only-find')?.addEventListener('change', e => { st.onlyFind = !!e.target.checked; renderSteps(); });

  draw(Math.abs(measure() - w0) <= 12 ? first : null);
  renderSteps();
  setSeg(opts.initialSeg && SEGS.includes(opts.initialSeg) ? opts.initialSeg : st.seg, false);
  // The graph follows the width it is given: a resized panel, fullscreen, a dashboard tile.
  if (gEl && typeof ResizeObserver !== 'undefined') {
    let raf = 0;
    const ro = new ResizeObserver(() => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        if (!gEl.isConnected) { ro.disconnect(); return; }
        const w = measure();
        if (w && Math.abs(w - drawnFor) > 12) draw();
      });
    });
    ro.observe(gEl);
    host._xplanRO = ro;
  }
}


// ─── The EXPLAIN Plan panel — beside the SQL canvas ───────────────────────────────────────
// One panel, fed by the canvas's Explain button and by any plan in the answer ("Open in
// panel"). It keeps the last few plans so a re-explain compares with the one before; selecting
// a step selects the tables it reads in the open SQL canvas.

const HISTORY_MAX = 6;
const _history = [];          // newest last
let _compareOn = true;

const KIND_WORDS = {
  product_join: 'product join', no_confidence: 'estimate without statistics', duplicated: 'spool duplicated to all AMPs',
  redistributed: 'rows redistributed', full_scan: 'all-rows scan', hot_step: 'one step carries most of the time',
};

/** What changed from one plan to the next, by finding KIND (step numbers move between plans,
 *  so a finding is matched by what it is, not where it sits). Pure — exported for the gate. */
export function comparePlans(prev, cur) {
  const count = spec => {
    const m = {};
    for (const f of (spec && spec.findings) || []) m[f.kind] = (m[f.kind] || 0) + 1;
    return m;
  };
  const a = count(prev), b = count(cur);
  const kinds = [...new Set([...Object.keys(a), ...Object.keys(b)])];
  const gone = kinds.filter(k => a[k] && !b[k]);
  const added = kinds.filter(k => !a[k] && b[k]);
  const severity = {};
  for (const f of [...((prev && prev.findings) || []), ...((cur && cur.findings) || [])]) severity[f.kind] = f.severity;
  const work = spec => ((spec && spec.steps) || []).filter(s => !['LOCK', 'END TRANSACTION', 'PARALLEL'].includes(s.op)).length;
  return {
    gone, added, severity,
    steps: [work(prev), work(cur)],
    seconds: [prev && prev.total_seconds != null ? prev.total_seconds : null, cur && cur.total_seconds != null ? cur.total_seconds : null],
  };
}

function _compareHtml(prev, cur) {
  const c = comparePlans(prev, cur);
  const word = k => KIND_WORDS[k] || k.replace(/_/g, ' ');
  const sec = c.seconds[0] != null && c.seconds[1] != null
    ? `estimated time ${_esc(c.seconds[0])} s → <strong>${_esc(c.seconds[1])} s</strong>` : '';
  const items = [
    // a change is coloured by how much the finding matters, not merely by its direction
    ...c.gone.map(k => `<li class="${c.severity[k] === 'info' ? '' : 'gone'}">no longer: ${_esc(word(k))}</li>`),
    ...c.added.map(k => `<li class="${c.severity[k] === 'danger' ? 'new' : c.severity[k] === 'warn' ? 'new-warn' : ''}">new: ${_esc(word(k))}</li>`),
  ];
  const same = !items.length ? '<li>the same findings as before</li>' : '';
  return `<div class="xplan-cmp" aria-label="Compared with the previous plan"><strong>Compared with the previous plan</strong> · `
    + `steps ${c.steps[0]} → <strong>${c.steps[1]}</strong>${sec ? ' · ' + sec : ''}<ul>${items.join('')}${same}</ul></div>`;
}

function _panelCanvasOpen() {
  const p = document.getElementById('canvas-split-panel');
  return !!(p && p.classList.contains('canvas-split--open'));
}

async function _highlightStep(step) {
  if (!_panelCanvasOpen()) return;
  const names = (step.inputs || []).filter(i => !String(i).startsWith('Spool '));
  if (!names.length) return;
  try {
    const m = await import('/api/v1/components/canvas/renderer');
    if (typeof m.highlightInOpenCanvas === 'function') m.highlightInOpenCanvas(names);
  } catch (_) { /* the canvas is optional */ }
}

async function _openStatementInCanvas(spec) {
  try {
    const { renderCanvas } = await import('/api/v1/components/canvas/renderer');
    const tmp = document.createElement('div');
    tmp.id = `xplan-canvas-${Math.random().toString(36).slice(2, 8)}`;
    tmp.style.display = 'none';
    document.body.appendChild(tmp);
    await Promise.resolve(renderCanvas(tmp.id, { component: 'canvas', action: 'open', view: 'canvas',
      title: 'Explained statement', content: spec.sql || '', language: 'sql',
      connection_id: spec.connection_id || null })).finally(() => tmp.remove());
  } catch (e) { console.warn('[explain_plan] open in canvas failed', e); }
}

const _ICON_CANVAS = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"/></svg>';
const _ICON_COMPARE = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M8 7h12m0 0l-4-4m4 4l-4 4M16 17H4m0 0l4 4m-4-4l4-4"/></svg>';
const _ICON_REFRESH = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h5M20 20v-5h-5M5.1 15a7 7 0 0011.9 2.6L20 15M4 9l3-2.6A7 7 0 0118.9 9"/></svg>';
const _picon = (k) => ((typeof window !== 'undefined' && window.PanelIcons && window.PanelIcons[k]) || '');   // the shared panel glyphs
let _justAdded = false;       // the plan on screen was opened just now (not picked from the switcher)
let _shown = -1;              // index into _history of the plan on screen (-1 = the newest)
let _isFullscreen = false;

function _shownIndex() { return _shown < 0 || _shown >= _history.length ? _history.length - 1 : _shown; }

/** A plan's name in the switcher and the title: where it came from, and when. */
function _planLabel(s) {
  const at = s._at ? new Date(s._at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
  // the badge in the menu bar says where it came from; the title names WHICH plan it is
  const from = s.origin === 'canvas' ? (s.connection_name || 'SQL canvas')
    : `Statement ${s.statement != null ? s.statement : ''}`.trim();
  return `${from}${at ? ' · ' + at : ''}`;
}

function _mountSwitcher() {
  const btn = document.getElementById('explain-plan-switcher-btn');
  if (!btn || typeof window.mountSplitPanelSwitcher !== 'function') return;
  window.mountSplitPanelSwitcher({
    btnEl: btn,
    items: () => _history.map((s, i) => ({ i, label: _planLabel(s), meta: `${(s.steps || []).filter(x => !['LOCK', 'END TRANSACTION', 'PARALLEL'].includes(x.op)).length} steps` })).reverse(),
    idFn: it => it.i, labelFn: it => it.label, metaField: 'meta',
    getCurrentId: () => _shownIndex(),
    onSelect: (i) => { _shown = i; _renderPanel(); _syncPlanCards(); },
    emptyLabel: 'No plans yet',
  });
}

function _toggleFullscreen() {
  const main = document.getElementById('main-content-area');
  if (!main) return;
  _isFullscreen = !_isFullscreen;
  if (_isFullscreen) {
    const nav = document.querySelector('body > nav');
    document.documentElement.style.setProperty('--xplan-fullscreen-top', (nav ? nav.offsetHeight : 0) + 'px');
    main.classList.add('xplan-fullscreen');
  } else {
    main.classList.remove('xplan-fullscreen');
    document.documentElement.style.removeProperty('--xplan-fullscreen-top');
  }
  const b = document.getElementById('explain-plan-split-fullscreen');
  if (b) { b.title = _isFullscreen ? 'Exit fullscreen' : 'Fullscreen'; b.classList.toggle('is-active', _isFullscreen); }
}

/** The plan as text — what Copy and Download hand over: the statement, the summary the model
 *  findings, and every step's own words. Pure — exported for the gate. */
export function planText(s) {
  const out = [];
  if (s.sql) out.push('Statement:', s.sql, '');
  const fs = s.findings || [];
  out.push(fs.length ? 'Findings:' : 'Findings: none of the checked patterns (product join, estimate without statistics, data movement, full scan).');
  for (const f of fs) out.push(`- [${f.severity}] ${f.text}`);
  if (s.total_seconds != null) out.push(`Total estimated time: ${s.total_seconds} s`);
  out.push('', 'Steps:');
  for (const st of s.steps || []) out.push(`${st.id}) ${st.text || st.op}`);
  if (s.result) out.push(`-> ${s.result}`);
  return out.join('\n');
}

async function _reExplain(cur, btn) {
  if (!cur.connection_id || !cur.sql) return;
  if (btn) btn.disabled = true;
  try {
    const token = localStorage.getItem('tda_auth_token');
    const r = await fetch('/api/v1/canvas/explain', {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify({ connection_id: cur.connection_id, code: cur.sql }) });
    const data = await r.json().catch(() => ({}));
    if (data.status === 'success') {
      const same = JSON.stringify((data.spec.steps || []).map(x => x.text)) === JSON.stringify((cur.steps || []).map(x => x.text));
      openExplainPlanPanel({ ...data.spec, connection_id: cur.connection_id, connection_name: cur.connection_name });
      _note(same ? 'Explained again — the plan is unchanged.' : 'Explained again — the plan changed.');
    } else {
      _note(data.message || `Re-explain failed (HTTP ${r.status})`, true);
    }
  } catch (e) { _note(`Re-explain failed: ${e.message || e}`, true); }
  finally { if (btn) btn.disabled = false; }
}

function _note(text, isError) {
  const bar = document.getElementById('explain-plan-subtoolbar');
  if (!bar) return;
  let n = bar.querySelector('.xplan-bar-note');
  if (!n) { n = document.createElement('span'); n.className = 'xplan-bar-note'; bar.appendChild(n); }
  n.textContent = text;
  n.classList.toggle('is-error', !!isError);
  clearTimeout(n._t);
  n._t = setTimeout(() => { if (n.isConnected) n.remove(); }, isError ? 8000 : 2500);
}

function _download(cur) {
  const blob = new Blob([planText(cur)], { type: 'text/plain' });
  const stamp = new Date(cur._at || Date.now()).toISOString().slice(0, 16).replace(/[:T]/g, '-');
  window.FilesKeep.deliver(blob, `explain-plan-${stamp}.txt`, { what: 'explain plan', componentId: 'explain_plan' });
}

function _renderPanel() {
  const body = document.getElementById('explain-plan-split-content');
  const bar = document.getElementById('explain-plan-subtoolbar');
  if (!body) return;
  const idx = _shownIndex();
  const cur = _history[idx];
  const title = document.getElementById('explain-plan-split-title');
  if (title) title.textContent = cur ? _planLabel(cur) : 'EXPLAIN Plan';
  if (!cur) {
    if (bar) bar.innerHTML = '';
    body.innerHTML = '<div class="xplan">No plan yet — press Explain in the SQL canvas, or open a plan from an answer.</div>';
    return;
  }
  const prev = idx > 0 ? _history[idx - 1] : null;
  if (bar) {
    bar.innerHTML = `<span class="pc-badge pc-badge--accent">${cur.origin === 'canvas' ? 'SQL canvas' : 'Answer'}</span>`
      + (prev ? `<button type="button" class="pc-btn xplan-cmp-toggle${_compareOn ? ' is-active' : ''}" aria-pressed="${_compareOn}" title="Show what changed from the previous plan">${_ICON_COMPARE}<span>Compare</span></button>` : '')
      + (cur.connection_id && cur.sql ? `<button type="button" class="pc-btn xplan-reexplain" title="Explain the same statement again on ${_esc(cur.connection_name || 'its connection')} — e.g. after collecting statistics">${_ICON_REFRESH}<span>Re-explain</span></button>` : '')
      + (cur.sql ? `<button type="button" class="pc-btn xplan-to-canvas" title="Open this statement in the SQL canvas">${_ICON_CANVAS}<span>Open in canvas</span></button>` : '')
      + `<span class="xplan-bar-gap"></span>`
      + `<button type="button" class="pc-btn pc-btn--icon xplan-copy" title="Copy the plan as text" aria-label="Copy the plan as text">${_picon('copy')}</button>`
      + `<button type="button" class="pc-btn pc-btn--icon xplan-download" title="Download the plan as a text file" aria-label="Download the plan as a text file">${_picon('download')}</button>`;
    const re = bar.querySelector('.xplan-reexplain');
    if (re) re.addEventListener('click', () => _reExplain(cur, re));
    const cp = bar.querySelector('.xplan-copy');
    if (cp) cp.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(planText(cur)); _note('Plan copied.'); }
      catch (_) { _note('Could not copy — the browser refused clipboard access.', true); }
    });
    const dl = bar.querySelector('.xplan-download');
    if (dl) dl.addEventListener('click', () => _download(cur));
    const tog = bar.querySelector('.xplan-cmp-toggle');
    if (tog) tog.addEventListener('click', () => { _compareOn = !_compareOn; _renderPanel(); });
    const toCanvas = bar.querySelector('.xplan-to-canvas');
    if (toCanvas) toCanvas.addEventListener('click', () => _openStatementInCanvas(cur));
  }
  body.innerHTML = `<div id="explain-plan-split-graph"></div>`;
  // A plan that just arrived with something to compare opens on the Overview, where the
  // comparison is; otherwise the panel keeps the segment the reader chose last.
  const showCompare = !!(prev && _compareOn);
  const initialSeg = showCompare && _justAdded ? 'overview' : undefined;
  _justAdded = false;
  renderExplainPlan('explain-plan-split-graph', { spec: cur }, {
    inPanel: true, onSelectStep: _highlightStep, initialSeg,
    prelude: showCompare ? _compareHtml(prev, cur) : '',
    graphNote: _panelCanvasOpen() ? 'Selecting a step selects its tables in the SQL canvas.' : '',
  });
}

/** Open the panel on a plan spec (the canvas's Explain, or a plan from an answer). The same
 *  plan opened twice in a row is not added again; a new plan is shown. */
export function openExplainPlanPanel(spec) {
  _css();
  const panel = document.getElementById('explain-plan-split-panel');
  if (!panel || !spec || !Array.isArray(spec.steps)) return false;
  const last = _history[_history.length - 1];
  if (!last || _planKey(last) !== _planKey(spec)) {
    _justAdded = true;
    _history.push({ ...spec, _at: Date.now() });
    while (_history.length > HISTORY_MAX) _history.shift();
    // the Workspace lists these plans — tell it one was added
    UderiaBus.announce('explain_plan', 'created');
  }
  _shown = -1;
  panel.style.display = 'flex';
  panel.offsetHeight;
  panel.classList.add('xplan-split--open');
  const close = document.getElementById('explain-plan-split-close');
  if (close) close.onclick = closeExplainPlanPanel;
  const fs = document.getElementById('explain-plan-split-fullscreen');
  if (fs) fs.onclick = _toggleFullscreen;
  _mountSwitcher();
  _renderPanel();
  _syncPlanCards();
  try { window.SplitPanelResizer?.update(); } catch (_) {}
  return true;
}

export function closeExplainPlanPanel() {
  const panel = document.getElementById('explain-plan-split-panel');
  if (!panel) return;
  if (_isFullscreen) _toggleFullscreen();
  panel.classList.remove('xplan-split--open');
  _syncPlanCards();
  panel.style.width = ''; panel.style.minWidth = ''; panel.style.maxWidth = ''; panel.style.flex = '';
  let done = false;
  const finish = () => {
    if (done) return; done = true;
    if (!panel.classList.contains('xplan-split--open')) panel.style.display = 'none';
    try { window.SplitPanelResizer?.update(); } catch (_) {}
  };
  panel.addEventListener('transitionend', finish, { once: true });
  setTimeout(finish, 400);
}

/** The panel's plans as Workspace rows, newest first. Plans are held in this page only
 *  (the last HISTORY_MAX), so this is the whole list — there is no server copy. */
export function panelPlans() {
  return _history.map((s, i) => ({
    i, label: _planLabel(s),
    steps: (s.steps || []).filter(x => !['LOCK', 'END TRANSACTION', 'PARALLEL'].includes(x.op)).length,
    findings: (s.findings || []).length,
    origin: s.origin || 'agent',
    created_at: s._at ? new Date(s._at).toISOString() : null,
  })).reverse();
}

/** Open the panel on the plan at history index i (a Workspace row). */
export function showPanelPlan(i) {
  if (!(i >= 0 && i < _history.length)) return false;
  const ok = openExplainPlanPanel(_history[_history.length - 1]);   // opens without adding (same key)
  _shown = i; _justAdded = false;
  _renderPanel(); _syncPlanCards();
  return ok;
}

/** The panel's plans (newest last) — for the gate. */
export function _panelHistory() { return _history.slice(); }

/** Empty the panel's plan history — for the gate only. */
export function _resetPanelForTest() { _history.length = 0; _shown = -1; }
