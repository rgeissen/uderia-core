/**
 * Flow Component Renderer — React Flow canvas island
 *
 * Loaded as an ES module by ComponentRendererRegistry.
 * Lazily loads React + ReactDOM + @xyflow/react from CDN.
 *
 * Architecture:
 *   - CDN UMD bundles → global window.React / window.ReactDOM / window.XYFlowExports
 *   - React island mounted into the provided container element
 *   - Custom node types for all flow node kinds
 *   - Inspector side panel for node configuration
 *   - Bidirectional sync: canvas mutations → PATCH /api/v1/flows/{id}/patch
 *   - SSE live execution animation
 *
 * CSS rules use var(--*) tokens — no hardcoded colours.
 * IFOC colour constants follow the canonical mapping from configurationHandler.js.
 */

// ── CDN URLs ──────────────────────────────────────────────────────────────────

const REACT_CDN = 'https://cdn.jsdelivr.net/npm/react@18/umd/react.production.min.js';
const REACT_DOM_CDN = 'https://cdn.jsdelivr.net/npm/react-dom@18/umd/react-dom.production.min.js';
const XYFLOW_CSS_CDN = 'https://cdn.jsdelivr.net/npm/@xyflow/react@12/dist/style.css';
const XYFLOW_CDN = 'https://cdn.jsdelivr.net/npm/@xyflow/react@12/dist/umd/index.js';

// ── IFOC profile colours (canonical — see configurationHandler.js) ────────────

// Single source of truth: window.ProfileColors (profileColors.js); local fallback
// keeps the component self-contained if that global hasn't loaded.
const IFOC = (typeof window !== 'undefined' && window.ProfileColors && window.ProfileColors.IFOC_CONFIG) || {
  tool_enabled: { color: '#9333ea', label: 'Optimize' },
  llm_only:     { color: '#4ade80', label: 'Ideate'   },
  rag_focused:  { color: '#3b82f6', label: 'Focus'    },
  genie:        { color: '#F15F22', label: 'Coordinate'},
};

// ── Execution status colours ──────────────────────────────────────────────────

const STATUS_COLORS = {
  idle:     'var(--border-primary)',
  queued:   '#64748b',
  running:  '#0284c7',
  success:  '#16a34a',
  error:    '#dc2626',
  skipped:  '#94a3b8',
  cancelled:'#d97706',
};

// ── Node type colours ─────────────────────────────────────────────────────────

const NODE_COLORS = {
  start:         '#16a34a',
  end:           '#9333ea',
  task_llm:      '#0284c7',
  task_prompt:   '#6366f1',
  task_tool:     '#0d9488',
  task_function: '#d97706',
  decision:      '#db2777',
  merge:         '#64748b',
  // PROP-007 increment 3: a judge decides (gold — the verdict), an artefact writes to
  // a canvas (the canvas component's own accent).
  judge:         '#ca8a04',
  artefact:      '#3b82f6',
  // PROP-008 §6: a signed action performed by an agent (the action component's accent)
  task_action:   '#e11d48',
  // PROP-010 §4.10: a flow reads and keeps files (the Files component's own accent)
  file_input:    '#78716c',
  file_output:   '#78716c',
};

// ── Addable node types — ONE declaration ─────────────────────────────────────
// The left palette, the ⌘K palette, the edge "+" menu, the number-key shortcuts,
// the help overlay and the new-node label/defaults all read this list, so a node
// type added here appears on every surface at once (five hand-kept copies drifted:
// judge and canvas reached none of them). `optim` = Optimize-profile flows only.
// `icon` is a list of [svgTag, attrs] drawn monochrome in the node's colour.
export const ADDABLE_NODES = [
  { type: 'task_llm', label: 'LLM Task', short: 'LLM', key: '1',
    title: 'Add LLM Task node — ask an agent',
    icon: [['path', { d: 'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z' }]] },
  { type: 'task_tool', label: 'Tool', short: 'Tool', key: '5',
    title: 'Add Tool node — call a tool directly, no LLM',
    icon: [['path', { d: 'M14.7 6.3a4 4 0 0 1 5 5l-9 9-4 1 1-4 7-7z' }], ['path', { d: 'M3 21l6-6' }]] },
  { type: 'task_prompt', label: 'Prompt', short: 'Prompt', key: null, optim: true,
    title: 'Add Prompt node — run an MCP prompt (Optimize profiles only)',
    icon: [['path', { d: 'M4 4h16v12H5.17L4 17.17V4z' }], ['line', { x1: 8, y1: 9, x2: 16, y2: 9 }], ['line', { x1: 8, y1: 12, x2: 13, y2: 12 }]] },
  { type: 'task_function', label: 'Function', short: 'Fn', key: '2',
    title: 'Add Function node — a deterministic expression, no LLM',
    icon: [['polyline', { points: '16 18 22 12 16 6' }], ['polyline', { points: '8 6 2 12 8 18' }]] },
  { type: 'decision', label: 'Decision', short: 'If', key: '3',
    title: 'Add Decision node — branch on a condition',
    icon: [['path', { d: 'M12 2l10 10-10 10L2 12z' }]] },
  { type: 'merge', label: 'Merge', short: 'Merge', key: '4',
    title: 'Add Merge node — join branches',
    icon: [['line', { x1: 8, y1: 6, x2: 21, y2: 6 }], ['line', { x1: 8, y1: 12, x2: 21, y2: 12 }], ['line', { x1: 8, y1: 18, x2: 21, y2: 18 }],
           ['line', { x1: 3, y1: 6, x2: 3.01, y2: 6 }], ['line', { x1: 3, y1: 12, x2: 3.01, y2: 12 }], ['line', { x1: 3, y1: 18, x2: 3.01, y2: 18 }]] },
  { type: 'judge', label: 'Judge', short: 'Judge', key: '6',
    title: 'Add Judge node — an agent or the eval judge panel gives a verdict (pass / fail) on the previous step',
    icon: [['path', { d: 'M12 3v18M5 7h14M5 7l-3 7a3 3 0 0 0 6 0zM19 7l-3 7a3 3 0 0 0 6 0zM8 21h8' }]] },
  { type: 'artefact', label: 'Canvas', short: 'Canvas', key: '7',
    title: 'Add Canvas node — writes the previous step’s code into one canvas per run, a version each time',
    icon: [['path', { d: 'M8 9l-3 3 3 3M16 9l3 3-3 3M13 6l-2 12' }]] },
  // PROP-008 §6 — offered only while OBDA-MODEL · OBDA-ACT is on (the canvas probes the switch); no keyboard
  // shortcut, so a key can never add a node the palette does not offer.
  { type: 'task_action', label: 'Action', short: 'Act', key: null, act: true,
    title: 'Add Action node — an agent performs a signed action; its posture decides (enforce writes, review parks, observe records)',
    icon: [['polygon', { points: '13 2 3 14 12 14 11 22 21 10 12 10 13 2' }]] },
  // PROP-010 §4.10 — offered only while Files is available to this user (the canvas probes
  // /api/v1/files); no keyboard shortcut, for the same reason as the Action node.
  { type: 'file_input', label: 'File input', short: 'File in', key: null, files: true,
    title: 'Add File input node — reads a file kept in Files: a table becomes rows, a document its text (no LLM)',
    icon: [['path', { d: 'M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z' }], ['polyline', { points: '14 3 14 9 20 9' }],
           ['path', { d: 'M12 11v7M9 15l3 3 3-3' }]] },
  { type: 'file_output', label: 'File output', short: 'File out', key: null, files: true,
    title: 'Add File output node — keeps its input as a file in Files: rows as CSV, text as Markdown, an object as JSON (no LLM)',
    icon: [['path', { d: 'M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z' }], ['polyline', { points: '14 3 14 9 20 9' }],
           ['path', { d: 'M12 18v-7M9 14l3-3 3 3' }]] },
];

// The data a freshly added node starts with. A judge defaults to the eval judge
// panel (a sensible default that is not an agent choice — it runs without one).
export function _defaultNodeData(type) {
  if (type === 'task_llm')    return { extensions: [] };
  if (type === 'task_tool')   return { tool_name: '', arguments: {} };
  if (type === 'task_prompt') return { prompt_name: '', prompt_arguments: {} };
  if (type === 'judge')       return { profile_id: 'judge_panel', rubric: '' };
  if (type === 'artefact')    return { source: 'output' };
  if (type === 'task_action') return { action: '', invocation: { key: '', values: {} } };
  if (type === 'file_input')  return { file_id: '', filename: '' };
  if (type === 'file_output') return { source: 'output', format: 'auto', filename: '' };
  return {};
}

// PROP-008 §6 — an action node's invocation as written in the inspector. Pure: the
// object must be {key, values}; `$name` / `{name}` references inside are resolved by the
// executor from upstream outputs, so they are kept as text here.
export function _parseInvocation(text) {
  let v;
  try { v = JSON.parse(text || '{}'); }
  catch (err) { return { error: 'Invalid JSON: ' + err.message }; }
  if (typeof v !== 'object' || v === null || Array.isArray(v)) {
    return { error: 'The invocation must be an object, e.g. { "key": "$customer_id", "values": { "Email": "$email" } }' };
  }
  if (v.values !== undefined && (typeof v.values !== 'object' || v.values === null || Array.isArray(v.values))) {
    return { error: '"values" must be an object of attribute → value' };
  }
  return { value: v };
}

// The actions an action node may pick: only those the acting agent is GRANTED — the same
// rule the save check and the run enforce, so the picker never offers a refusal.
export function _grantedActions(rows) {
  return (rows || []).filter(r => r && r.granted).map(r => ({
    name: r.action, label: r.action, posture: r.posture, trust: r.trust,
    entity: r.entity, key: r.key, writable: r.writable || [],
  }));
}

// The OBDA-ACT switch as /api/v1/obda/planes reports it (component chain + KG tier gate):
// on only when the endpoint answered and says so; no answer, a refusal or a missing key ⇒ off.
export function _actOnFromPlanes(body) {
  return !!(body && body.planes && body.planes.act === true);
}

// What an unattended act does, said by its posture (the words the panel shows).
export function _postureWords(posture) {
  return ({ enforce: 'writes when it runs', annotate: 'writes when it runs, and discloses it',
            review: 'parks the write for your approval — nothing is written until you approve',
            observe: 'records what it would write — nothing is written' })[posture] || 'its posture decides';
}

// The round-limit range a loop frame offers — mirrors flow_db.SCOPE_MAX_ROUNDS.
export const SCOPE_MIN_ROUNDS = 1;
export const SCOPE_MAX_ROUNDS = 20;

export function _addableNodes(isOptim, actOn = false, filesOn = false) {
  return ADDABLE_NODES.filter(n => (!n.optim || isOptim) && (!n.act || actOn) && (!n.files || filesOn));
}

// What a File input node can read — the executor's own rule (flow_executor._execute_file_input_node).
export const FILE_INPUT_EXT = ['csv', 'tsv', 'xlsx', 'json', 'jsonld', 'md', 'markdown', 'txt', 'sql', 'py', 'js', 'ts',
                               'yaml', 'yml', 'xml', 'ttl', 'nt', 'rdf', 'sh', 'r', 'css', 'pdf', 'docx'];
export function _fileInputAccepts(f) {
  const ext = String((f && f.ext) || '').toLowerCase();
  return FILE_INPUT_EXT.includes(ext)
    ? { ok: true }
    : { ok: false, note: `A flow can't read .${ext || '?'} files — choose a table, JSON, text or document file` };
}

// What a File output node writes, said in words (the inspector and the node subtitle).
export function _fileOutputWords(d) {
  const fmt = ({ csv: 'CSV', md: 'Markdown', json: 'JSON', txt: 'text' })[(d && d.format) || 'auto'];
  return fmt ? `keeps its input in Files as ${fmt}` : 'keeps its input in Files — rows as CSV, text as Markdown';
}

function _addableLabel(type) {
  const n = ADDABLE_NODES.find(x => x.type === type);
  return n ? n.label : type;
}

// ── Built-in extension VISUAL hints (abbr + colour only) ──────────────────────
// NOT a source of truth for which extensions exist, are flow-eligible, or take a
// parameter — all of that comes from the manifest via /v1/extensions/activated
// (see _fetchFlowExtensions). This map is purely presentation: it gives the known
// built-ins a stable badge/colour. Any extension not listed here gets a derived
// abbreviation and a deterministic colour, so new extensions render correctly
// without touching this file.
const EXT_VISUALS = {
  boolean_check: { abbr: 'B',  color: '#10b981', label: 'Boolean Check' },
  decision:      { abbr: 'D',  color: '#f43f5e', label: 'Decision'      },
  classify:      { abbr: 'Cl', color: '#8b5cf6', label: 'Classify'      },
  condition:     { abbr: 'Cn', color: '#14b8a6', label: 'Condition'     },
  extract:       { abbr: 'Ex', color: '#f59e0b', label: 'Extract'       },
  format:        { abbr: 'Fm', color: '#6366f1', label: 'Format'        },
  json:          { abbr: 'J',  color: '#0ea5e9', label: 'JSON'          },
  summary:       { abbr: 'S',  color: '#a855f7', label: 'Summary'       },
};
const _EXT_FALLBACK_COLORS = ['#0ea5e9','#10b981','#f59e0b','#8b5cf6','#f43f5e','#14b8a6','#6366f1','#a855f7'];

// Resolve visual hints for an extension id, deriving a stable fallback for ids
// not in EXT_VISUALS (e.g. user-imported extensions).
function _extVisual(id, label) {
  if (EXT_VISUALS[id]) return EXT_VISUALS[id];
  const base = String(label || id || '?').replace(/[^a-zA-Z0-9]/g, '');
  const abbr = (base.slice(0, 2) || '?').replace(/^./, c => c.toUpperCase());
  let hash = 0;
  for (const ch of String(id)) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return { abbr, color: _EXT_FALLBACK_COLORS[hash % _EXT_FALLBACK_COLORS.length], label: label || id };
}

// Fetch the flow-eligible extensions for the picker: those the user has activated
// AND the admin has enabled AND that are flow-node eligible (manifest
// flow_node_eligible !== false). Identity, label, description and hasParam all come
// from the manifest — the single source of truth — so a newly activated extension
// or a new built-in appears here automatically.
//
// No long-lived cache: the picker opens rarely and on user action, so we always
// reflect the current activation state (an extension activated mid-session shows up
// on the next picker open without a page reload).
async function _fetchFlowExtensions() {
  try {
    const token = localStorage.getItem('tda_auth_token');
    const r = await fetch('/api/v1/extensions/activated', {
      headers: token ? { 'Authorization': `Bearer ${token}` } : {},
    });
    if (!r.ok) throw new Error(r.status);
    const { extensions = [] } = await r.json();
    const seen = new Set();
    const out = [];
    for (const e of extensions) {
      const id = e.extension_id;
      if (!id || seen.has(id)) continue;          // collapse multiple activations of one extension
      if (e.flow_node_eligible === false) continue; // terminal/export extensions (e.g. pdf)
      seen.add(id);
      const vis = _extVisual(id, e.display_name);
      out.push({
        id,
        label: e.display_name || vis.label || id,
        desc: e.description || '',
        category: e.category || 'Other',
        keywords: Array.isArray(e.keywords) ? e.keywords : [],
        hasParam: !!(e.parameters && e.parameters.supported),
        isLlm: !!e.requires_llm,
        abbr: vis.abbr,
        color: vis.color,
      });
    }
    return out;
  } catch (_) {
    // Degraded fallback on fetch error only: the known built-in visuals so the
    // picker is usable through a transient blip. (Empty array = nothing activated,
    // which the picker renders as a helpful "activate them" message — distinct.)
    return Object.keys(EXT_VISUALS).map(id => ({
      id, label: EXT_VISUALS[id].label, desc: '', category: 'Other', keywords: [],
      hasParam: false, isLlm: false,
      abbr: EXT_VISUALS[id].abbr, color: EXT_VISUALS[id].color,
    }));
  }
}

// Search predicate mirroring the Extensions config screen (extensionHandler.js):
// matches id + label + description + keywords, case-insensitive substring.
function _extMatchesSearch(ext, query) {
  const q = (query || '').trim().toLowerCase();
  if (!q) return true;
  const hay = [ext.id, ext.label, ext.desc, ...(ext.keywords || [])].join(' ').toLowerCase();
  return hay.includes(q);
}

// Group a flat extension list into ordered category sections for the picker.
// Known flow categories first (Transform → Automation → Analysis), then any others
// alphabetically; items within a section keep their incoming (display) order.
const _EXT_CATEGORY_ORDER = ['Transform', 'Automation', 'Analysis'];
function _groupExtsByCategory(exts) {
  const byCat = new Map();
  for (const e of exts) {
    const cat = e.category || 'Other';
    if (!byCat.has(cat)) byCat.set(cat, []);
    byCat.get(cat).push(e);
  }
  const cats = [...byCat.keys()].sort((a, b) => {
    const ia = _EXT_CATEGORY_ORDER.indexOf(a), ib = _EXT_CATEGORY_ORDER.indexOf(b);
    if (ia !== -1 || ib !== -1) return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
    return a.localeCompare(b);
  });
  return cats.map(cat => ({ category: cat, items: byCat.get(cat) }));
}

// ── CSS injection (once per page) ─────────────────────────────────────────────

function _injectCSS() {
  if (document.getElementById('flow-renderer-css')) return;
  const style = document.createElement('style');
  style.id = 'flow-renderer-css';
  style.textContent = `
/* ── Container layout ──────────────────────────────────────────────────────── */
.flow-root {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 400px;
  /* Transparent — inherits the viewport-fixed body gradient so the flow canvas
     matches the dashboard component's backdrop (one unified surface). */
  background: transparent;
  color: var(--text-primary);
  font-family: inherit;
  position: relative;
  overflow: hidden;
}
.flow-main {
  display: flex;
  flex: 1;
  overflow: hidden;
  position: relative;
}
.flow-canvas-area {
  flex: 1;
  position: relative;
  overflow: hidden;
}
/* ── Node palette sidebar ─────────────────────────────────────────────────── */
.flow-node-palette {
  width: 60px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 8px 4px;
  border-right: 1px solid var(--border-secondary);
  /* Side bar — raised chrome surface (graphite), matches the toolbars. */
  background: var(--bg-secondary, rgba(31,33,38,0.92));
  overflow-y: auto;
}
.flow-palette-label {
  font-size: 0.58rem;
  color: var(--text-muted);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  margin-bottom: 2px;
  text-align: center;
}
.flow-palette-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  width: 48px;
  padding: 5px 2px;
  border-radius: 6px;
  border: 1px solid var(--border-secondary);
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 0.58rem;
  text-align: center;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.flow-palette-btn:hover {
  background: var(--hover-bg-strong);
  color: var(--text-primary);
}
/* React Flow override — match the other split panels' body background */
.flow-root .react-flow {
  /* Transparent — shows the viewport-fixed body gradient through, matching the
     dashboard and KG graph canvases. */
  background: transparent;
}
.flow-root .react-flow__background pattern circle {
  fill: var(--border-secondary, rgba(255,255,255,0.06));
}
.flow-root .react-flow__controls {
  background: var(--card-bg);
  border: 1px solid var(--border-primary);
  border-radius: 6px;
}
.flow-root .react-flow__controls-button {
  background: var(--card-bg);
  border-bottom: 1px solid var(--border-secondary);
  color: var(--text-primary);
}
.flow-root .react-flow__controls-button:hover {
  background: var(--hover-bg-strong);
}
.flow-root .react-flow__minimap {
  background: var(--bg-overlay, rgba(20, 22, 25,0.9));
  border: 1px solid var(--border-primary);
  border-radius: 6px;
}
/* ── Toolbar ────────────────────────────────────────────────────────────────── */
.flow-toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-bottom: 1px solid var(--border-secondary);
  /* Same fill as .sp-header — one cohesive elevated header surface (slate-800). */
  background: var(--bg-secondary, rgba(31, 33, 38,0.92));
  flex-shrink: 0;
  /* Wrap to multiple rows when the panel narrows — browser-native stacking,
     identical to the knowledge-graph toolbar (rock-solid, no JS measurement). */
  flex-wrap: wrap;
  min-width: 0;
}
/* Items keep their size and wrap rather than squishing. */
.flow-toolbar-cluster > * { flex-shrink: 0; }
.flow-toolbar-cluster { flex-wrap: wrap; }
.flow-toolbar-label {
  font-size: 0.7rem;
  color: var(--text-muted);
  letter-spacing: 0.05em;
  text-transform: uppercase;
  margin-right: 2px;
}
.flow-btn {
  /* Ghost button — PCS-aligned (panel-controls.css .pc-btn). Token-driven and
     theme-aware. Semantic variants below set --pc-tint; everything derives. */
  --pc-tint: var(--sp-accent, var(--teradata-orange, #F15F22));
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  min-height: var(--ctl-height, 28px);
  padding: 0 var(--ctl-pad-x, 9px);
  font-size: var(--ctl-font, 0.78rem);
  font-weight: 500;
  line-height: 1;
  border-radius: var(--ctl-radius, 8px);
  border: 1px solid transparent;
  background: transparent;
  color: var(--text-muted, #94a3b8);
  cursor: pointer;
  white-space: nowrap;
  transition: background var(--duration-fast,0.15s) ease, color var(--duration-fast,0.15s) ease,
              border-color var(--duration-fast,0.15s) ease, transform var(--duration-instant,0.1s) ease;
}
.flow-btn:hover {
  background: var(--hover-bg, rgba(148,163,184,0.08));
  color: var(--text-primary, #f8fafc);
}
.flow-btn:active { transform: var(--ctl-press, scale(0.94)); }

/* Segmented control — PCS-aligned (.pc-seg). Token-driven track + pill segments;
   each option's colour comes from --pc-tint (flow-seg-opt--* variants). */
.flow-seg {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 2px;
  border-radius: var(--ctl-radius, 8px);
  background: var(--hover-bg, rgba(148,163,184,0.07));
}
.flow-seg > button {
  --pc-tint: var(--sp-accent, var(--teradata-orange, #F15F22));
  border: none;
  border-radius: var(--ctl-radius-inner, 6px);
  padding: 3px 9px;
  font: inherit;
  font-size: var(--ctl-font, 0.78rem);
  font-weight: 500;
  color: var(--text-muted, #94a3b8);
  background: none;
  cursor: pointer;
  transition: color var(--duration-fast,0.15s) ease, background var(--duration-fast,0.15s) ease, transform var(--duration-instant,0.1s) ease;
}
.flow-seg > button:active { transform: var(--ctl-press, scale(0.94)); }
.flow-seg-opt--success { --pc-tint: var(--status-success, #10b981); }
.flow-seg-opt--warning { --pc-tint: var(--status-warning, #f59e0b); }
.flow-seg-opt--info    { --pc-tint: var(--status-info, #3b82f6); }
.flow-seg > .is-active {
  color: var(--pc-tint);
  background: color-mix(in srgb, var(--pc-tint) 16%, transparent);
  font-weight: 600;
}

/* ── Priority+ overflow menu ────────────────────────────────────────────────── */
.flow-overflow-btn.is-open { background: var(--hover-bg, rgba(148,163,184,0.08)); color: var(--text-primary, #f8fafc); }
.flow-overflow-menu {
  z-index: 1000;
  min-width: 200px;
  max-width: 320px;
  max-height: 70vh;
  overflow-y: auto;
  padding: 6px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  background: var(--card-bg, #1e293b);
  border: 1px solid var(--border-primary, rgba(148,163,184,0.18));
  border-radius: var(--radius-lg, 12px);
  box-shadow: var(--shadow-xl, 0 20px 40px rgba(0,0,0,0.45));
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  animation: flow-overflow-in 0.16s cubic-bezier(0.16,1,0.3,1);
}
@keyframes flow-overflow-in {
  from { opacity: 0; transform: translateY(-8px) scale(0.97); }
  to   { opacity: 1; transform: translateY(0) scale(1); }
}
/* Each row hosts the real control (kept fully functional in the menu). */
.flow-overflow-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 8px;
  border-radius: var(--radius-md, 8px);
}
.flow-overflow-row:hover { background: var(--hover-bg, rgba(148,163,184,0.06)); }
/* Controls relax to full-width rows inside the menu (no longer space-constrained). */
.flow-overflow-row > * { flex: 1 1 auto; }
.flow-overflow-row .flow-btn { justify-content: flex-start; width: 100%; }
.flow-btn-primary { --pc-tint: var(--sp-accent, var(--teradata-orange, #F15F22)); color: var(--pc-tint); border-color: color-mix(in srgb, var(--pc-tint) 38%, transparent); }
.flow-btn-primary:hover { background: color-mix(in srgb, var(--pc-tint) 12%, transparent); }
.flow-btn-run { --pc-tint: var(--status-success, #10b981); color: var(--pc-tint); border-color: color-mix(in srgb, var(--pc-tint) 38%, transparent); }
.flow-btn-run:hover { background: color-mix(in srgb, var(--pc-tint) 12%, transparent); }
.flow-btn-danger { --pc-tint: var(--status-error, #ef4444); color: var(--pc-tint); border-color: color-mix(in srgb, var(--pc-tint) 38%, transparent); }
.flow-btn-danger:hover { background: color-mix(in srgb, var(--pc-tint) 12%, transparent); }

/* ── Flow selector ────────────────────────────────────────────────────────── */
.flow-selector-wrapper {
  /* Flow switching now lives in the standard .sp-header switcher (consistent with
     the other panels); the in-canvas selector is hidden to avoid duplication. */
  display: none;
}
.flow-selector {
  appearance: none;
  -webkit-appearance: none;
  display: block;
  text-align: left;
  padding: 3px 22px 3px 8px;
  font-size: 0.72rem;
  font-family: inherit;
  line-height: 1.4;
  border-radius: 4px;
  border: 1px solid color-mix(in srgb, var(--sp-accent, #f59e0b) 35%, transparent);
  background: color-mix(in srgb, var(--sp-accent, #f59e0b) 8%, transparent);
  color: var(--sp-accent, #f59e0b);
  cursor: pointer;
  max-width: 160px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  /* chevron (matches the flow accent) */
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath d='M1 1l4 4 4-4' stroke='%23f59e0b' stroke-width='1.5' fill='none' stroke-linecap='round'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 6px center;
  transition: border-color 0.15s, background-color 0.15s;
}
.flow-selector:hover  { border-color: color-mix(in srgb, var(--sp-accent, #f59e0b) 60%, transparent); background-color: color-mix(in srgb, var(--sp-accent, #f59e0b) 12%, transparent); }
.flow-selector:focus  { outline: none; border-color: var(--sp-accent, #f59e0b); }

/* Custom flow-selector dropdown items (rendered in the shared .flow-ver-dropdown portal) */
.flow-sel-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  cursor: pointer;
  font-size: 0.78rem;
  color: var(--text-primary);
  white-space: nowrap;
  transition: background 0.1s;
}
.flow-sel-item:hover { background: var(--hover-bg-strong); }
.flow-sel-item--current { background: color-mix(in srgb, var(--sp-accent, #f59e0b) 12%, transparent); color: var(--sp-accent, #f59e0b); }
.flow-sel-check {
  flex-shrink: 0;
  width: 12px;
  text-align: center;
  color: var(--sp-accent, #f59e0b);
  font-size: 0.74rem;
}
.flow-sel-name { overflow: hidden; text-overflow: ellipsis; }
/* IFOC profile-class colour dot for profile options. */
.flow-sel-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }

/* Themed <select> replacement trigger (native popup can't be styled). Reuses the
   .flow-ver-dropdown / .flow-sel-item portal list above for the open menu. */
.flow-cs-wrap { position: relative; display: block; width: 100%; }
.flow-cs-trigger {
  display: flex; align-items: center; width: 100%; box-sizing: border-box;
  text-align: left; cursor: pointer;
  background: var(--bg-primary, rgba(20, 22, 25,0.8));
  border: 1px solid var(--border-secondary);
  border-radius: 4px; color: var(--text-primary);
  font-size: 0.78rem; font-family: inherit; line-height: 1.4;
  padding: 5px 22px 5px 8px;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath d='M1 1l4 4 4-4' stroke='%23999' stroke-width='1.5' fill='none' stroke-linecap='round'/%3E%3C/svg%3E");
  background-repeat: no-repeat; background-position: right 7px center;
  transition: border-color 0.15s, background-color 0.15s;
}
.flow-cs-trigger:hover { border-color: var(--border-primary); }
.flow-cs-trigger:focus-visible { outline: none; border-color: rgba(147,51,234,0.5); }
.flow-cs-trigger-label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ── Version history dropdown ─────────────────────────────────────────────── */
.flow-ver-wrapper { position: relative; }
.flow-ver-btn {
  /* PCS ghost button — canonical History control, matched to KG. */
  --pc-tint: var(--sp-accent, var(--teradata-orange, #F15F22));
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  min-height: var(--ctl-height, 28px);
  padding: 0 var(--ctl-pad-x, 9px);
  font-size: var(--ctl-font, 0.78rem);
  font-weight: 500;
  line-height: 1;
  border-radius: var(--ctl-radius, 8px);
  border: 1px solid transparent;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  white-space: nowrap;
  transition: color var(--duration-fast,0.15s) ease, background var(--duration-fast,0.15s) ease, transform var(--duration-instant,0.1s) ease;
}
.flow-ver-btn:hover { background: var(--hover-bg, rgba(148,163,184,0.08)); color: var(--text-primary); }
.flow-ver-btn:active { transform: var(--ctl-press, scale(0.94)); }
.flow-ver-btn svg { width: var(--ctl-icon, 16px); height: var(--ctl-icon, 16px); }
.flow-ver-badge {
  --pc-tint: var(--status-info, #3b82f6);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 1.05rem;
  height: 1.05rem;
  padding: 0 4px;
  border-radius: var(--radius-full, 9999px);
  background: color-mix(in srgb, var(--pc-tint) 18%, transparent);
  color: var(--pc-tint);
  font-size: 0.62rem;
  font-weight: 700;
}
.flow-ver-dropdown {
  /* Rendered in a body portal with inline fixed position/width/maxHeight so it
     escapes the toolbar overflow clip and stays inside the viewport. */
  min-width: 0;
  max-height: 280px;
  overflow-y: auto;
  background: var(--card-bg, rgba(20, 22, 25,0.97));
  border: 1px solid var(--border-primary);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.5);
  z-index: 100;
  padding: 4px 0;
}
.flow-ver-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 12px;
  cursor: default;
  font-size: 0.78rem;
  color: var(--text-primary);
  transition: background 0.1s;
}
.flow-ver-item:hover { background: var(--hover-bg-strong); }
.flow-ver-item--current { background: rgba(59,130,246,0.08); }
.flow-ver-item-label { display: flex; flex-direction: column; gap: 1px; }
.flow-ver-item-title { font-weight: 500; }
.flow-ver-item-num {
  display: inline-block;
  font-size: 0.62rem;
  font-weight: 600;
  color: var(--text-muted);
  background: var(--hover-bg-strong, rgba(255,255,255,0.06));
  border-radius: 3px;
  padding: 0 4px;
  margin-right: 2px;
}
.flow-ver-item-time  { font-size: 0.66rem; color: var(--text-muted); }
.flow-ver-item-restore {
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 0.68rem;
  color: #60a5fa;
  border: 1px solid rgba(59,130,246,0.3);
  background: transparent;
  cursor: pointer;
  transition: all 0.1s;
  white-space: nowrap;
}
.flow-ver-item-restore:hover { background: rgba(59,130,246,0.15); border-color: rgba(59,130,246,0.6); }

/* ── Flow parameters panel ───────────────────────────────────────────────────── */
.flow-params-wrapper { position: relative; }
.flow-params-panel {
  /* Body portal — inline fixed position/width/maxHeight set in JS. */
  overflow-y: auto;
  background: var(--card-bg, rgba(20, 22, 25,0.97));
  border: 1px solid var(--border-primary);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.5);
  z-index: 100;
  padding: 10px;
}
.flow-params-section { display: flex; flex-direction: column; }
.flow-params-divider { height: 1px; background: var(--border-secondary, rgba(255,255,255,0.1)); margin: 12px 0; }
.flow-params-title {
  font-size: 0.74rem; font-weight: 600; color: var(--text-primary); margin-bottom: 2px;
}
.flow-params-hint { font-size: 0.64rem; color: var(--text-muted); margin-bottom: 8px; }
.flow-params-hint code {
  font-size: 0.62rem; padding: 0 3px; border-radius: 3px;
  background: var(--hover-bg-strong, rgba(255,255,255,0.06)); color: var(--text-primary);
}
.flow-params-empty { font-size: 0.7rem; color: var(--text-muted); font-style: italic; padding: 4px 0 8px; }
.flow-params-row { display: flex; gap: 6px; align-items: center; margin-bottom: 6px; }
.flow-params-key, .flow-params-val {
  font-size: 0.72rem; padding: 4px 6px; border-radius: 4px;
  border: 1px solid var(--border-secondary, rgba(255,255,255,0.1));
  background: var(--bg-overlay, rgba(20, 22, 25,0.6)); color: var(--text-primary);
  outline: none; min-width: 0;
}
.flow-params-key { flex: 0 0 38%; font-family: ui-monospace, monospace; }
.flow-params-val { flex: 1; }
.flow-params-key:focus, .flow-params-val:focus { border-color: var(--teradata-orange, #F15F22); }
.flow-params-del {
  flex-shrink: 0; display: flex; align-items: center; justify-content: center;
  width: 24px; height: 24px; border-radius: 4px; cursor: pointer;
  border: 1px solid var(--border-secondary, rgba(255,255,255,0.1));
  background: transparent; color: var(--text-muted); transition: all 0.1s;
}
.flow-params-del:hover { color: #f87171; border-color: rgba(248,113,113,0.5); }
.flow-params-add {
  display: flex; align-items: center; gap: 4px; margin-top: 4px;
  padding: 4px 8px; border-radius: 4px; cursor: pointer; font-size: 0.68rem;
  border: 1px dashed var(--border-secondary, rgba(255,255,255,0.15));
  background: transparent; color: var(--text-muted); transition: all 0.1s;
}
.flow-params-add:hover { color: var(--text-primary); border-color: var(--teradata-orange, #F15F22); }
/* In-scope parameter hint shown in the node inspector */
.flow-param-scope { margin-top: 4px; font-size: 0.62rem; color: var(--text-muted); }
.flow-param-scope code {
  font-size: 0.62rem; padding: 0 3px; border-radius: 3px; margin-right: 3px;
  background: var(--hover-bg-strong, rgba(255,255,255,0.06)); color: var(--text-primary);
}
/* ── Smart field ($-autocomplete) ─────────────────────────────────────────── */
.flow-sf-wrap { position: relative; }
.flow-sf-menu {
  position: absolute; left: 0; right: 0; top: calc(100% + 2px); z-index: 50;
  max-height: 220px; overflow-y: auto;
  background: var(--card-bg, rgba(20, 22, 25,0.98));
  border: 1px solid var(--border-primary);
  border-radius: 6px; box-shadow: 0 8px 24px rgba(0,0,0,0.5); padding: 3px;
}
.flow-sf-item {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 4px 8px; border-radius: 4px; cursor: pointer;
}
.flow-sf-item.active { background: var(--hover-bg-strong, rgba(255,255,255,0.08)); }
.flow-sf-name {
  font-family: ui-monospace, monospace; font-size: 0.72rem;
  color: var(--teradata-orange, #F15F22);
}
.flow-sf-src { font-size: 0.6rem; color: var(--text-muted); white-space: nowrap; }
.flow-sf-warn {
  display: flex; align-items: center; gap: 4px; margin-top: 4px;
  font-size: 0.64rem; color: #fbbf24;
}

/* ── Debug bar ──────────────────────────────────────────────────────────────── */
.flow-debug-bar {
  background: rgba(0,0,0,0.55);
  border-bottom: 1px solid rgba(234,179,8,0.35);
  font-family: 'JetBrains Mono', 'Fira Mono', monospace;
  font-size: 0.62rem;
  color: #fde047;
  flex-shrink: 0;
  max-height: 120px;
  display: flex;
  flex-direction: column;
}
.flow-debug-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 8px;
  border-bottom: 1px solid rgba(234,179,8,0.2);
}
.flow-debug-title {
  font-weight: 700;
  color: #fbbf24;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  font-size: 0.6rem;
}
.flow-debug-stats { color: #a3e635; flex: 1; }
.flow-debug-close {
  background: none; border: none; color: #fde047;
  cursor: pointer; font-size: 0.7rem; padding: 0 2px;
  opacity: 0.6; line-height: 1;
}
.flow-debug-close:hover { opacity: 1; }
.flow-debug-log {
  overflow-y: auto;
  flex: 1;
  padding: 3px 8px;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.flow-debug-line {
  white-space: pre;
  opacity: 0.85;
  line-height: 1.4;
}

.flow-node {
  background: var(--card-bg, rgba(20, 22, 25,0.9));
  border: 2px solid var(--border-primary);
  border-radius: 8px;
  padding: 8px 12px;
  min-width: 140px;
  max-width: 220px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.3);
  /* animated props — enter/leave/update share these transitions */
  transition: border-color 0.2s, box-shadow 0.35s ease,
              opacity 0.32s cubic-bezier(0.34,1.56,0.64,1),
              transform 0.32s cubic-bezier(0.34,1.56,0.64,1),
              filter 0.28s ease;
  will-change: opacity, transform, filter;
  cursor: pointer;
  position: relative;
}
.flow-node:hover {
  box-shadow: 0 4px 16px rgba(0,0,0,0.4);
}
.flow-node.selected {
  box-shadow: 0 0 0 2px rgba(147,51,234,0.5);
}
/* ── PROP-007 slots: an open slot is a promise the consumer has not kept yet — dashed in the
   panel accent so it reads as "fill me", never as a broken node. ── */
.flow-node.flow-node--slot-open {
  border-style: dashed;
  border-color: var(--sp-accent, #f59e0b);
  background: color-mix(in srgb, var(--sp-accent, #f59e0b) 6%, var(--card-bg, rgba(20,22,25,0.9)));
}
.flow-node-slot-badge {
  margin-left: auto; margin-right: 16px; flex-shrink: 0;
  font-size: 0.6rem; font-weight: 700; line-height: 1.4; letter-spacing: 0.02em;
  padding: 0 5px; border-radius: 4px;
  color: var(--sp-accent, #f59e0b);
  border: 1px dashed var(--sp-accent, #f59e0b);
  background: color-mix(in srgb, var(--sp-accent, #f59e0b) 12%, transparent);
  white-space: nowrap;
}
.flow-node-slot-badge.is-judge {
  border-style: solid;
  color: var(--text-muted, #94a3b8);
  border-color: var(--border-secondary, rgba(148,163,184,0.3));
  background: var(--hover-bg-strong, rgba(148,163,184,0.08));
}
.flow-slot-pill {
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 0.68rem; font-weight: 600; line-height: 1.4;
  padding: 2px 8px; border-radius: 999px; cursor: pointer; white-space: nowrap;
  color: var(--sp-accent, #f59e0b);
  border: 1px dashed var(--sp-accent, #f59e0b);
  background: color-mix(in srgb, var(--sp-accent, #f59e0b) 10%, transparent);
}
.flow-slot-pill:hover { background: color-mix(in srgb, var(--sp-accent, #f59e0b) 18%, transparent); }
.flow-slot-hint {
  font-size: 0.72rem; color: var(--text-muted, #94a3b8); line-height: 1.4;
  margin: 2px 0 6px;
}
.flow-slot-hint b { color: var(--sp-accent, #f59e0b); font-weight: 700; }
/* ── Node lifecycle animations ─────────────────────────────────────────────── */
.flow-node,
.flow-node-oval {
  transition: opacity 0.32s cubic-bezier(0.34,1.56,0.64,1),
              transform 0.32s cubic-bezier(0.34,1.56,0.64,1),
              filter 0.28s ease, box-shadow 0.35s ease;
  will-change: opacity, transform, filter;
}
.flow-node.flow-node--entering,
.flow-node-oval.flow-node--entering {
  opacity: 0;
  transform: scale(0.6);
  filter: blur(6px);
}
.flow-node.flow-node--leaving,
.flow-node-oval.flow-node--leaving {
  opacity: 0 !important;
  transform: scale(0.72) translateY(10px) !important;
  filter: blur(4px) !important;
  pointer-events: none !important;
}
.flow-node.flow-node--updated {
  box-shadow: 0 0 0 2px rgba(147,51,234,0.85),
              0 0 22px rgba(147,51,234,0.35) !important;
}
.flow-node-oval.flow-node--updated {
  box-shadow: 0 0 0 2px rgba(147,51,234,0.85),
              0 0 18px rgba(147,51,234,0.35) !important;
}
/* ── Edge lifecycle animation ─────────────────────────────────────────────── */
@keyframes flow-edge-spark {
  0%   { opacity: 0; filter: drop-shadow(0 0 7px rgba(147,51,234,1)); }
  45%  { opacity: 1; filter: drop-shadow(0 0 5px rgba(147,51,234,0.55)); }
  100% { opacity: 1; filter: drop-shadow(0 0 0px rgba(147,51,234,0)); }
}
.react-flow__edge.flow-edge--entering {
  animation: flow-edge-spark 480ms cubic-bezier(0.16,1,0.3,1) both;
}
.react-flow__edge.flow-edge--leaving { animation: flow-fade-out 220ms ease-out both; }
@keyframes flow-fade-out { to { opacity: 0; } }
.flow-rename-input {
  z-index: 1000;
  font-size: 0.72rem; font-weight: 600;
  padding: 4px 8px; border-radius: 6px;
  background: var(--card-bg, rgba(20, 22, 25,0.98));
  color: var(--text-primary);
  border: 1px solid var(--teradata-orange, #F15F22);
  box-shadow: 0 4px 16px rgba(0,0,0,0.4); outline: none;
}
.flow-edge-add {
  z-index: 60; width: 22px; height: 22px; padding: 0;
  display: flex; align-items: center; justify-content: center;
  border-radius: 50%; cursor: pointer;
  color: #fff; background: var(--teradata-orange, #F15F22);
  border: 2px solid var(--card-bg, #0f172a);
  box-shadow: 0 2px 8px rgba(0,0,0,0.4);
  transition: transform 0.1s;
}
.flow-edge-add:hover { transform: scale(1.15); }
.flow-edge-tools { z-index: 60; display: flex; gap: 6px; }
.flow-edge-del {
  width: 22px; height: 22px; padding: 0;
  display: flex; align-items: center; justify-content: center;
  border-radius: 50%; cursor: pointer;
  color: #fff; background: #dc2626; border: 2px solid var(--card-bg, #0f172a);
  box-shadow: 0 2px 8px rgba(0,0,0,0.4); transition: transform 0.1s;
}
.flow-edge-del:hover { transform: scale(1.15); }
/* ── Contextual selection toolbar ───────────────────────────────────────────── */
.flow-sel-toolbar {
  position: absolute; top: 12px; left: 50%; transform: translateX(-50%);
  z-index: 30; display: flex; align-items: center; gap: 6px;
  padding: 5px 8px; border-radius: 8px;
  background: var(--card-bg, rgba(20, 22, 25,0.97));
  border: 1px solid var(--border-primary);
  box-shadow: 0 6px 20px rgba(0,0,0,0.4);
  animation: flow-sel-in 0.14s ease-out both;
}
@keyframes flow-sel-in { from { opacity: 0; transform: translate(-50%, -6px); } to { opacity: 1; transform: translate(-50%, 0); } }
.flow-sel-count { font-size: 0.7rem; font-weight: 600; color: var(--text-primary); padding: 0 4px; }
.flow-sel-sep { width: 1px; height: 16px; background: var(--border-secondary, rgba(255,255,255,0.12)); }
.flow-sel-btn {
  display: flex; align-items: center; gap: 5px;
  padding: 4px 9px; border-radius: 6px; cursor: pointer; font-size: 0.7rem;
  color: var(--text-primary); background: transparent;
  border: 1px solid var(--border-secondary, rgba(255,255,255,0.1));
  transition: all 0.1s;
}
.flow-sel-btn:hover { background: var(--hover-bg-strong); border-color: var(--teradata-orange, #F15F22); }
.flow-sel-btn--danger { color: var(--status-error, #ef4444); }
.flow-sel-btn--danger:hover { background: color-mix(in srgb, var(--status-error, #ef4444) 12%, transparent); border-color: color-mix(in srgb, var(--status-error, #ef4444) 50%, transparent); }
/* ── Help overlay (shortcuts cheatsheet) ────────────────────────────────────── */
.flow-health-help {
  background: none; border: none; padding: 0; cursor: pointer;
  color: var(--teradata-orange, #F15F22); font-size: inherit; text-decoration: underline; text-underline-offset: 2px;
}
.flow-help-overlay, .flow-cmd-overlay {
  position: absolute; inset: 0; z-index: 200;
  display: flex; align-items: flex-start; justify-content: center;
  background: rgba(0,0,0,0.45); backdrop-filter: blur(2px);
}
.flow-help-modal {
  margin-top: 8%; width: min(620px, 90%);
  background: var(--card-bg, rgba(20, 22, 25,0.98)); border: 1px solid var(--border-primary);
  border-radius: 12px; box-shadow: 0 16px 48px rgba(0,0,0,0.5); padding: 16px 18px;
}
.flow-help-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.flow-help-title { font-size: 0.92rem; font-weight: 600; color: var(--text-primary); }
.flow-help-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px 24px; }
.flow-help-gtitle {
  font-size: 0.6rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--teradata-orange, #F15F22); margin-bottom: 6px;
}
.flow-help-row { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.flow-help-kbd {
  flex-shrink: 0; min-width: 78px; text-align: center;
  font-family: ui-monospace, monospace; font-size: 0.64rem;
  color: var(--text-primary); background: var(--hover-bg-strong, rgba(255,255,255,0.07));
  border: 1px solid var(--border-secondary, rgba(255,255,255,0.12));
  border-radius: 4px; padding: 2px 6px;
}
.flow-help-desc { font-size: 0.72rem; color: var(--text-muted); }
/* ── Command palette (⌘K) ───────────────────────────────────────────────────── */
.flow-cmd-modal {
  margin-top: 10%; width: min(460px, 88%);
  background: var(--card-bg, rgba(20, 22, 25,0.98)); border: 1px solid var(--border-primary);
  border-radius: 10px; box-shadow: 0 16px 48px rgba(0,0,0,0.55); overflow: hidden;
}
.flow-cmd-input {
  width: 100%; box-sizing: border-box; padding: 12px 14px; font-size: 0.82rem;
  background: transparent; color: var(--text-primary); border: none;
  border-bottom: 1px solid var(--border-secondary, rgba(255,255,255,0.1)); outline: none;
}
.flow-cmd-list { max-height: 320px; overflow-y: auto; padding: 4px; }
.flow-cmd-item {
  padding: 8px 12px; border-radius: 6px; cursor: pointer; font-size: 0.76rem; color: var(--text-primary);
}
.flow-cmd-item:hover, .flow-cmd-item.active { background: var(--hover-bg-strong, rgba(255,255,255,0.08)); }
.flow-cmd-empty { padding: 12px; font-size: 0.74rem; color: var(--text-muted); font-style: italic; }
.flow-node-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}
.flow-node-type-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.flow-node-label {
  font-size: 0.78rem;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.flow-node-subtitle {
  font-size: 0.68rem;
  color: var(--text-muted);
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* ── Node status icon (top-right corner of each node) ────────────────────── */
.flow-node-status-icon {
  position: absolute;
  top: 7px;
  right: 7px;
  width: 13px;
  height: 13px;
  pointer-events: none;
  z-index: 10;
  overflow: visible;
}
/* Running: a rotating gear (cog) signals the node is actively executing. */
@keyframes _flow-spin  { to { transform: rotate(360deg); } }
.flow-node-status-icon.running {
  width: 15px;
  height: 15px;
  top: 6px;
  right: 6px;
  animation: _flow-spin 2.2s linear infinite;
  transform-origin: 50% 50%;
  filter: drop-shadow(0 0 4px rgba(56,189,248,0.7));
}
/* Success pop-in */
@keyframes _flow-check { from { stroke-dashoffset: 24; opacity: 0; } to { stroke-dashoffset: 0; opacity: 1; } }
.flow-node-status-icon.success polyline {
  stroke-dasharray: 24;
  stroke-dashoffset: 24;
  animation: _flow-check 0.35s cubic-bezier(0.16,1,0.3,1) forwards;
}
/* Error pop-in */
@keyframes _flow-cross { from { opacity: 0; transform: scale(0.5); } to { opacity: 1; transform: scale(1); } }
.flow-node-status-icon.error { animation: _flow-cross 0.25s ease-out forwards; }
/* Decision (diamond) node: the status icon is rendered in the unrotated outer
   wrapper (inside the rotated diamond it would be skewed 45 deg and land
   off-shape). Centre it just below the top vertex, using left (not transform)
   because the running gear uses transform for its spin animation. */
.flow-decision-wrap > .flow-node-status-icon {
  top: 28px;
  right: auto;
  left: 57px;
}
.flow-decision-wrap > .flow-node-status-icon.running {
  top: 27px;
  right: auto;
  left: 56px;
}

/* Node-level "executing" glow — pulses while a node is running so the whole
   card (not just the corner gear) signals activity. */
@keyframes flow-running-glow {
  0%, 100% { box-shadow: 0 0 0 2px rgba(56,189,248,0.30), 0 0 14px rgba(56,189,248,0.22); }
  50%       { box-shadow: 0 0 0 2px rgba(56,189,248,0.65), 0 0 24px rgba(56,189,248,0.45); }
}
.flow-root .flow-node.flow-node--running,
.flow-root .flow-node-oval.flow-node--running,
.flow-root .flow-node-diamond.flow-node--running {
  border-color: #38bdf8 !important;
  animation: flow-running-glow 1.3s ease-in-out infinite;
}

/* Status bar dot keep its own pulse */
@keyframes flow-node-pulse {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.4; }
}
/* Diamond decision node */
.flow-node-diamond {
  background: var(--card-bg);
  border: 2px solid #db2777;
  width: 90px;
  height: 90px;
  display: flex;
  align-items: center;
  justify-content: center;
  transform: rotate(45deg);
  border-radius: 8px;
}
.flow-node-diamond-inner {
  transform: rotate(-45deg);
  text-align: center;
}
/* Start/End oval nodes */
.flow-node-oval {
  background: var(--card-bg);
  border: 2px solid;
  border-radius: 24px;
  padding: 6px 18px;
  min-width: 80px;
  text-align: center;
  font-size: 0.78rem;
  font-weight: 600;
  cursor: default;
  will-change: opacity, transform, filter;
}
/* ── Inspector panel ─────────────────────────────────────────────────────────── */
.flow-inspector {
  width: 0;
  min-width: 0;
  max-width: 320px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  background: var(--card-bg);
  border-left: 1px solid var(--border-primary);
  transition: width 0.25s ease, min-width 0.25s ease;
  flex-shrink: 0;
}
.flow-inspector.open {
  width: 280px;
  min-width: 220px;
}
.flow-inspector-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border-secondary);
  background: var(--bg-overlay);
  flex-shrink: 0;
}
.flow-inspector-title {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--text-primary);
}
.flow-inspector-close {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 1rem;
  padding: 2px 4px;
  border-radius: 4px;
}
.flow-inspector-close:hover { color: var(--text-primary); background: var(--hover-bg-strong); }
.flow-inspector-body {
  flex: 1;
  overflow-y: auto;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.flow-field {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.flow-field label {
  font-size: 0.68rem;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.flow-field input,
.flow-field select,
.flow-field textarea {
  background: var(--bg-primary, rgba(20, 22, 25,0.8));
  border: 1px solid var(--border-secondary);
  border-radius: 4px;
  color: var(--text-primary);
  font-size: 0.78rem;
  padding: 5px 8px;
  width: 100%;
  box-sizing: border-box;
  resize: vertical;
}
.flow-field input:focus,
.flow-field select:focus,
.flow-field textarea:focus {
  outline: none;
  border-color: rgba(147,51,234,0.5);
}
.flow-field textarea {
  min-height: 70px;
  font-family: ui-monospace, monospace;
  font-size: 0.72rem;
}
.flow-inspector-save {
  margin: 8px 12px 12px;
  padding: 6px 12px;
  background: rgba(147,51,234,0.15);
  border: 1px solid rgba(147,51,234,0.4);
  border-radius: 4px;
  color: #a855f7;
  font-size: 0.78rem;
  cursor: pointer;
  width: calc(100% - 24px);
}
.flow-inspector-save:hover { background: rgba(147,51,234,0.25); }
/* ── Scope group node ────────────────────────────────────────────────────────── */
.flow-scope-node {
  background: rgba(147,51,234,0.06);
  border: 1.5px dashed rgba(147,51,234,0.35);
  border-radius: 10px;
  padding: 24px 8px 8px;
  position: relative;
  min-width: 150px;
  min-height: 80px;
}
.flow-scope-label {
  position: absolute;
  top: 4px;
  left: 8px;
  font-size: 0.65rem;
  font-weight: 600;
  color: rgba(168,85,247,0.8);
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.flow-scope-node { box-sizing: border-box; pointer-events: none; }
.flow-scope-node.is-live { border-color: rgba(168,85,247,0.7); background: rgba(147,51,234,0.09); }
.flow-runs-wrap { margin-left: auto; position: relative; display: inline-flex; align-items: center; gap: 8px; }
.flow-runs-shown { font-size: 0.65rem; color: var(--text-muted); }
.flow-runs-btn { display: inline-flex; align-items: center; gap: 5px; font-size: 0.65rem; padding: 3px 10px; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--border-secondary); background: var(--card-bg); color: var(--text-primary); }
.flow-runs-btn:hover:not(:disabled) { background: var(--hover-bg-strong); }
.flow-runs-btn:focus-visible { outline: 2px solid var(--teradata-orange, #F15F22); outline-offset: 1px; }
.flow-runs-btn:disabled { opacity: 0.45; cursor: default; }
.flow-runs-menu { position: absolute; right: 0; bottom: calc(100% + 6px); z-index: 60; width: 260px; max-height: 300px; overflow-y: auto;
  background: var(--card-bg); border: 1px solid var(--border-primary); border-radius: 8px; box-shadow: 0 8px 28px rgba(0,0,0,0.35); padding: 4px; }
.flow-runs-item { width: 100%; display: flex; align-items: center; gap: 8px; padding: 6px 8px; border: none; background: transparent;
  color: var(--text-primary); font-size: 0.7rem; text-align: left; border-radius: 5px; cursor: pointer; }
.flow-runs-item:hover, .flow-runs-item.is-current { background: var(--hover-bg-strong); }
.flow-runs-item:focus-visible { outline: 2px solid var(--teradata-orange, #F15F22); outline-offset: -2px; }
.flow-runs-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.flow-runs-meta { margin-left: auto; color: var(--text-muted); font-size: 0.64rem; }
.flow-runs-empty { padding: 8px; font-size: 0.7rem; color: var(--text-muted); }
.flow-scope-edit { pointer-events: auto; margin-left: 8px; display: inline-flex; align-items: center; gap: 3px;
  font-size: 0.6rem; font-weight: 600; letter-spacing: 0; text-transform: none; padding: 2px 9px; border-radius: 999px; cursor: pointer;
  border: 1px solid rgba(168,85,247,0.35); background: var(--card-bg); color: var(--text-primary); }
.flow-scope-edit:hover:not(:disabled) { background: rgba(147,51,234,0.15); }
.flow-scope-edit:focus-visible { outline: 2px solid rgba(168,85,247,0.8); outline-offset: 1px; }
.flow-scope-edit:disabled { opacity: 0.4; cursor: default; }
.flow-loop-modal .flow-field { margin-bottom: 10px; }
.flow-loop-row { display: flex; gap: 8px; align-items: center; }
.flow-loop-note { font-size: 0.66rem; color: var(--text-muted); margin-top: 3px; line-height: 1.4; }
.flow-loop-actions { display: flex; gap: 8px; justify-content: space-between; margin-top: 14px; }
.flow-loop-actions .flow-loop-right { display: flex; gap: 8px; }
.flow-loop-btn { font-size: 0.74rem; padding: 6px 12px; border-radius: 6px; cursor: pointer;
  border: 1px solid var(--border-primary); background: var(--card-bg); color: var(--text-primary); }
.flow-loop-btn:hover { background: var(--hover-bg-strong); }
.flow-loop-btn--primary { border-color: rgba(147,51,234,0.6); background: rgba(147,51,234,0.18); }
.flow-loop-btn--danger { color: var(--status-error, #ef4444); }
.flow-loop-btn:disabled { opacity: 0.45; cursor: default; }
.flow-scope-rounds { position: absolute; left: 8px; bottom: 4px; display: flex; align-items: center; gap: 4px;
  pointer-events: auto; font-size: 0.6rem; color: rgba(168,85,247,0.85); }
.flow-scope-step { width: 20px; height: 20px; line-height: 18px; padding: 0; border-radius: 4px; cursor: pointer;
  font-size: 0.7rem; border: 1px solid rgba(168,85,247,0.35); background: var(--card-bg); color: var(--text-primary); }
.flow-scope-step:hover:not(:disabled) { background: rgba(147,51,234,0.15); }
.flow-scope-step:focus-visible { outline: 2px solid rgba(168,85,247,0.8); outline-offset: 1px; }
.flow-scope-step:disabled { opacity: 0.35; cursor: default; }
.flow-scope-iter-badge.is-pass { color: #16a34a; border-color: rgba(22,163,74,0.4); }
.flow-scope-iter-badge.is-warn { color: #d97706; border-color: rgba(217,119,6,0.4); }
.flow-node-verdict { margin: 3px 8px 0; font-size: 0.6rem; font-weight: 700; letter-spacing: 0.02em; }
.flow-node-verdict.is-pass { color: #16a34a; }
.flow-node-verdict.is-fail { color: #dc2626; }
.flow-node-verdict.is-none { color: var(--text-muted, #94a3b8); }
.flow-node-canvas-btn { margin: 4px 8px 0; font-size: 0.6rem; padding: 1px 7px; border-radius: 4px; cursor: pointer;
  border: 1px solid rgba(59,130,246,0.45); background: rgba(59,130,246,0.1); color: var(--text-primary); }
.flow-node-canvas-btn:hover { background: rgba(59,130,246,0.2); }
.flow-scope-iter-badge {
  position: absolute;
  top: 4px;
  right: 8px;
  font-size: 0.62rem;
  color: rgba(168,85,247,0.6);
  border: 1px solid rgba(168,85,247,0.25);
  border-radius: 10px;
  padding: 1px 6px;
}
/* ── Run status bar ─────────────────────────────────────────────────────────── */
.flow-status-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 12px;
  border-top: 1px solid var(--border-secondary);
  /* Hint bar — raised chrome surface (graphite), matches the toolbars. */
  background: var(--bg-secondary);
  font-size: 0.7rem;
  flex-shrink: 0;
}
.flow-status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}
.flow-status-dot.running { animation: flow-node-pulse 1.2s ease-in-out infinite; }
.flow-status-text { color: var(--text-muted); }
.flow-status-tokens {
  margin-left: auto;
  color: var(--text-muted);
  font-size: 0.65rem;
}
/* ── Empty state ────────────────────────────────────────────────────────────── */
.flow-empty {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  pointer-events: none;
}
.flow-empty-title {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--text-muted);
}
.flow-empty-hint {
  font-size: 0.72rem;
  color: var(--text-muted);
  opacity: 0.7;
  text-align: center;
  max-width: 260px;
}

/* ── Inline flow card (appears in chat) ─────────────────────────────────────── */
.flow-inline-card {
  /* Fallback accent only. The live manifest icon_accent is read at render time and set
     inline on the card root (see _liveFlowAccent) so the footer button matches the flow
     split-view colour without a frozen hardcode; this value applies just until the live
     map loads, and stops another panel's --sp-accent (e.g. a dashboard tile's pink)
     cascading in. */
  --sp-accent: #f59e0b;
  background: var(--card-bg, rgba(20, 22, 25,0.6));
  border: 1px solid var(--border-primary);
  border-radius: 10px;
  overflow: hidden;
  font-family: inherit;
}
.flow-inline-card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px 8px;
  border-bottom: 1px solid var(--border-secondary);
}
/* inside an evidence row the card header (name · size) is hidden by the row's one rule
   ([data-ans-restates], answer.css) — the body then opens without a divider */
.ans-ev-b .flow-inline-card-body { border-top: 0; }
.flow-inline-card-icon { color: var(--sp-accent, #f59e0b); flex-shrink: 0; }
.flow-inline-card-name {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--text-primary);
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.flow-inline-card-badge {
  font-size: 0.7rem;
  color: var(--text-muted);
  background: color-mix(in srgb, var(--sp-accent, #f59e0b) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--sp-accent, #f59e0b) 15%, transparent);
  border-radius: 20px;
  padding: 1px 8px;
  flex-shrink: 0;
}
.flow-inline-card-body {
  padding: 8px 14px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.flow-inline-card-desc {
  font-size: 0.78rem;
  color: var(--text-muted);
  line-height: 1.4;
}
.flow-inline-chips { display: flex; flex-wrap: wrap; gap: 4px; }
.flow-type-chip {
  display: inline-flex;
  align-items: center;
  padding: 1px 8px;
  border-radius: 20px;
  font-size: 0.7rem;
  font-weight: 500;
  background: color-mix(in srgb, var(--sp-accent, #f59e0b) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--sp-accent, #f59e0b) 20%, transparent);
  color: var(--sp-accent, #f59e0b);
}
.flow-inline-card-footer {
  padding: 8px 14px;
  border-top: 1px solid var(--border-secondary);
  display: flex;
  align-items: center;
  justify-content: flex-end;
}
/* ── Flow split panel ───────────────────────────────────────────────────────── */
#flow-split-panel {
  width: 45%;
  min-width: 320px;
  max-width: 680px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-left: 1px solid var(--border-primary);
  background: var(--card-bg, rgba(20, 22, 25,0.6));
  height: 100%;
  overflow: hidden;
  transition: width 0.3s ease;
}
#flow-split-panel.flow-split--open { width: 45%; }
/* Header chrome (.sp-header/.sp-title/.sp-action-btn) is in static/css/split-panels.css */
.flow-split-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}
/* ── Fullscreen ─────────────────────────────────────────────────────────────── */
.flow-fullscreen #flow-split-panel.flow-split--open {
  position: fixed !important;
  top: var(--flow-fullscreen-top, 0px);
  left: 0 !important;
  right: 0 !important;
  bottom: 0 !important;
  width: auto !important;
  height: auto !important;
  max-width: none;
  z-index: 50;
}

/* ══════════════════════════════════════════════════════════════════════════════
   P0 UX INTELLIGENCE — World-class interaction layer
   ══════════════════════════════════════════════════════════════════════════════ */

/* ── Drag-from-palette ───────────────────────────────────────────────────────── */
.flow-palette-btn[draggable="true"] { cursor: grab; user-select: none; }
.flow-palette-btn[draggable="true"]:active { cursor: grabbing; }
.flow-canvas-area.drag-over::after {
  content: '';
  position: absolute;
  inset: 3px;
  border: 2px dashed rgba(245,158,11,0.6);
  border-radius: 8px;
  background: rgba(245,158,11,0.04);
  pointer-events: none;
  z-index: 99;
  animation: flow-drag-pulse 1s ease-in-out infinite;
}
@keyframes flow-drag-pulse {
  0%, 100% { border-color: rgba(245,158,11,0.45); background: rgba(245,158,11,0.03); }
  50%       { border-color: rgba(245,158,11,0.9);  background: rgba(245,158,11,0.07); }
}

/* ── Ghost connection line ───────────────────────────────────────────────────── */
@keyframes flow-dash-anim { to { stroke-dashoffset: -12; } }

/* ── Handle compatibility highlighting ──────────────────────────────────────── */
/* IMPORTANT: ReactFlow centres each handle on its connection point with an inline
   transform translate() whose value differs per side (top/bottom/left/right).
   Never set transform in these rules — it would replace that centering and make
   the dot/focus ring jump off-centre. Use box-shadow for hover/valid feedback.
   z-index keeps handles (and their hit area) above node bodies, including the
   rotated decision diamond which otherwise paints over its top input handle. */
.flow-root .react-flow__handle {
  width: 11px;
  height: 11px;
  border: 2px solid var(--card-bg, #0f172a);
  border-radius: 50%;
  z-index: 20;
  transition: box-shadow 0.12s ease, background-color 0.12s ease;
}
/* Generous invisible hit/focus target centred on the dot so the connector is
   easy to grab even when the cursor isn't dead-on it. The pseudo-element is
   part of the handle, so pointer events over it count as handle events. */
.flow-root .react-flow__handle::after {
  content: '';
  position: absolute;
  top: 50%;
  left: 50%;
  width: 26px;
  height: 26px;
  transform: translate(-50%, -50%);
  border-radius: 50%;
  background: transparent;
}
.flow-root .react-flow__handle.connectable:hover {
  box-shadow: 0 0 0 5px rgba(22,163,74,0.45), 0 0 16px rgba(22,163,74,0.30);
}
.flow-root .react-flow__handle.valid {
  box-shadow: 0 0 0 5px rgba(22,163,74,0.65), 0 0 16px rgba(22,163,74,0.35) !important;
}
.flow-root .react-flow__handle.invalid {
  box-shadow: 0 0 0 5px rgba(220,38,38,0.55) !important;
}

/* ── Selection indicators ───────────────────────────────────────────────────── */
/* Node selected: purple ring + glow */
.flow-root .react-flow__node.selected .flow-node-card {
  outline: 2px solid rgba(139,92,246,0.85) !important;
  box-shadow: 0 0 0 3px rgba(139,92,246,0.22), 0 4px 24px rgba(139,92,246,0.28) !important;
}
.flow-root .react-flow__node.selected .flow-node-oval {
  outline: 2px solid rgba(139,92,246,0.85) !important;
  box-shadow: 0 0 0 3px rgba(139,92,246,0.22), 0 0 18px rgba(139,92,246,0.35) !important;
}
/* Edge selected: purple stroke + glow */
.flow-root .react-flow__edge.selected .react-flow__edge-path {
  stroke: rgba(139,92,246,0.9) !important;
  stroke-width: 2.5px !important;
  filter: drop-shadow(0 0 5px rgba(139,92,246,0.55)) !important;
}
/* Selection box */
.flow-root .react-flow__selection {
  background: rgba(139,92,246,0.06);
  border: 1.5px dashed rgba(139,92,246,0.55);
}

/* ── Edge insertion highlight ────────────────────────────────────────────────── */
.flow-edge-insert { stroke: rgba(245,158,11,0.85) !important; stroke-width: 3 !important; }

/* ── Snap target indicator ───────────────────────────────────────────────────── */
.flow-node.snap-target {
  box-shadow: 0 0 0 2px rgba(245,158,11,0.65), 0 0 20px rgba(245,158,11,0.22) !important;
}

/* ── Graph health bar ────────────────────────────────────────────────────────── */
.flow-health-bar {
  position: relative;
  display: flex;
  align-items: center;
  padding: 3px 10px;
  border-bottom: 1px solid var(--border-secondary);
  background: var(--bg-overlay);
  flex-shrink: 0;
  gap: 8px;
  min-height: 26px;
}
.flow-health-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 10px;
  border-radius: 20px;
  border: 1px solid;
  background: transparent;
  font-size: 0.68rem;
  font-weight: 600;
  cursor: pointer;
  letter-spacing: 0.02em;
  transition: opacity 0.15s;
  white-space: nowrap;
}
.flow-health-pill:hover { opacity: 0.82; }
.flow-health-panel {
  position: absolute;
  top: calc(100% + 2px);
  left: 10px;
  min-width: 290px;
  max-width: 400px;
  background: var(--card-bg, rgba(20, 22, 25,0.98));
  border: 1px solid var(--border-primary);
  border-radius: 10px;
  box-shadow: 0 10px 32px rgba(0,0,0,0.55);
  z-index: 200;
  padding: 6px 0;
}
.flow-issue-row {
  display: flex;
  align-items: center;
  padding: 5px 12px;
  gap: 6px;
  transition: background 0.1s;
  cursor: default;
}
.flow-issue-row:hover { background: var(--hover-bg-strong); }
.flow-issue-jump {
  margin-left: auto;
  padding: 1px 8px;
  border-radius: 4px;
  border: 1px solid rgba(59,130,246,0.3);
  background: transparent;
  color: #60a5fa;
  font-size: 0.65rem;
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
  transition: background 0.1s;
}
.flow-issue-jump:hover { background: rgba(59,130,246,0.14); }
.flow-health-hint {
  font-size: 0.62rem;
  color: var(--text-muted);
  opacity: 0.7;
  margin-left: auto;
  letter-spacing: 0.01em;
  white-space: nowrap;
}

/* ── Context menu ────────────────────────────────────────────────────────────── */
.flow-ctx-menu {
  position: fixed;
  min-width: 170px;
  background: var(--card-bg, rgba(20, 22, 25,0.98));
  border: 1px solid var(--border-primary);
  border-radius: 10px;
  box-shadow: 0 10px 32px rgba(0,0,0,0.58), 0 2px 8px rgba(0,0,0,0.3);
  z-index: 9999;
  padding: 4px 0;
  overflow: hidden;
  animation: flow-ctx-appear 0.12s cubic-bezier(0.34,1.56,0.64,1) both;
}
@keyframes flow-ctx-appear {
  from { opacity: 0; transform: scale(0.92) translateY(-4px); }
  to   { opacity: 1; transform: scale(1) translateY(0); }
}
.flow-ctx-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 14px;
  font-size: 0.78rem;
  color: var(--text-primary);
  cursor: pointer;
  transition: background 0.08s;
  white-space: nowrap;
}
.flow-ctx-item:hover { background: var(--hover-bg-strong); }
.flow-ctx-item--danger { color: #f87171; }
.flow-ctx-item--danger:hover { background: rgba(248,113,113,0.1); }
.flow-ctx-item--ai { color: #c084fc; }
.flow-ctx-item--ai:hover { background: rgba(168,85,247,0.1); }
.flow-ctx-separator { height: 1px; background: var(--border-secondary); margin: 3px 0; }

/* ── Last execution result in Inspector ─────────────────────────────────────── */
.flow-exec-result {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--border-secondary);
}
.flow-exec-result-label {
  font-size: 0.62rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
  margin-bottom: 5px;
}
.flow-exec-result-text {
  font-family: ui-monospace, monospace;
  font-size: 0.68rem;
  line-height: 1.5;
  color: #4ade80;
  background: rgba(74,222,128,0.05);
  border: 1px solid rgba(74,222,128,0.15);
  border-radius: 5px;
  padding: 7px 9px;
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
  max-height: 160px;
  overflow-y: auto;
}
.flow-exec-result-text.error {
  color: #f87171;
  background: rgba(248,113,113,0.05);
  border-color: rgba(248,113,113,0.2);
}
/* ── Inspector data panel (per-node run input/output) ───────────────────────── */
.flow-data-panel { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border-secondary); }
.flow-data-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.flow-data-title {
  font-size: 0.62rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em;
  color: var(--text-muted);
}
.flow-data-status { font-size: 0.6rem; font-weight: 600; text-transform: uppercase; }
.flow-data-run {
  margin-left: auto; display: flex; align-items: center; gap: 4px;
  padding: 3px 8px; border-radius: 4px; font-size: 0.66rem; cursor: pointer;
  color: var(--teradata-orange, #F15F22);
  border: 1px solid rgba(241,95,34,0.35); background: rgba(241,95,34,0.08);
  transition: all 0.1s;
}
.flow-data-run:hover { background: rgba(241,95,34,0.18); border-color: rgba(241,95,34,0.6); }
.flow-data-empty { font-size: 0.68rem; color: var(--text-muted); font-style: italic; }
.flow-data-block { margin-bottom: 8px; }
.flow-data-label {
  font-size: 0.58rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em;
  color: var(--text-muted); margin-bottom: 3px;
}
.flow-data-pre {
  font-family: ui-monospace, monospace; font-size: 0.66rem; line-height: 1.45;
  color: var(--text-primary);
  background: var(--bg-overlay, rgba(20, 22, 25,0.5));
  border: 1px solid var(--border-secondary, rgba(255,255,255,0.08));
  border-radius: 5px; padding: 6px 8px; margin: 0;
  white-space: pre-wrap; word-break: break-word; max-height: 180px; overflow-y: auto;
}
.flow-data-pre.error { color: #f87171; background: rgba(248,113,113,0.05); border-color: rgba(248,113,113,0.2); }

/* ── Node execution analytics overlay ───────────────────────────────────────── */
.flow-node-stat-overlay {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
  margin-top: 5px;
  padding-top: 5px;
  border-top: 1px solid var(--border-secondary);
}
.flow-node-stat-pill {
  display: inline-flex;
  align-items: center;
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 0.6rem;
  font-variant-numeric: tabular-nums;
  background: rgba(14,165,233,0.1);
  color: #38bdf8;
  border: 1px solid rgba(14,165,233,0.2);
}

/* ── Fix with AI button on error nodes ──────────────────────────────────────── */
.flow-node-fix-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 3px;
  margin-top: 5px;
  width: 100%;
  padding: 3px 7px;
  border-radius: 4px;
  border: 1px solid rgba(168,85,247,0.4);
  background: rgba(168,85,247,0.08);
  color: #c084fc;
  font-size: 0.62rem;
  cursor: pointer;
  transition: background 0.14s;
  animation: flow-fix-pulse 2s ease-in-out infinite;
}
.flow-node-fix-btn:hover { background: color-mix(in srgb, var(--sp-accent, #f59e0b) 22%, transparent); border-color: color-mix(in srgb, var(--sp-accent, #f59e0b) 65%, transparent); }
@keyframes flow-fix-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(168,85,247,0); }
  50%       { box-shadow: 0 0 0 3px rgba(168,85,247,0.18); }
}

/* ── NL "AI Build" command wand ─────────────────────────────────────────────── */
.flow-nl-wand {
  position: absolute;
  bottom: 172px;
  right: 12px;
  z-index: 50;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6px;
}
.flow-nl-trigger {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 7px 13px;
  border-radius: 20px;
  border: 1px solid rgba(168,85,247,0.5);
  background: rgba(168,85,247,0.14);
  color: #c084fc;
  font-size: 0.72rem;
  font-weight: 600;
  cursor: pointer;
  backdrop-filter: blur(10px);
  box-shadow: 0 4px 18px rgba(0,0,0,0.38);
  transition: all 0.18s;
  letter-spacing: 0.025em;
}
.flow-nl-trigger:hover {
  background: rgba(168,85,247,0.24);
  border-color: rgba(168,85,247,0.75);
  box-shadow: 0 6px 22px rgba(168,85,247,0.28), 0 2px 8px rgba(0,0,0,0.3);
  transform: translateY(-1px);
}
.flow-nl-input-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
  background: var(--card-bg, rgba(20, 22, 25,0.94));
  border: 1px solid rgba(168,85,247,0.55);
  border-radius: 12px;
  padding: 7px 8px;
  box-shadow: 0 8px 28px rgba(0,0,0,0.48), 0 0 0 1px rgba(168,85,247,0.1);
  backdrop-filter: blur(12px);
  min-width: 320px;
  max-width: 500px;
  animation: flow-nl-enter 0.18s cubic-bezier(0.34,1.56,0.64,1) both;
}
@keyframes flow-nl-enter {
  from { opacity: 0; transform: scale(0.9) translateY(8px); }
  to   { opacity: 1; transform: scale(1) translateY(0); }
}
.flow-nl-input {
  flex: 1;
  background: transparent;
  border: none;
  color: var(--text-primary);
  font-size: 0.82rem;
  padding: 2px 4px;
  min-width: 0;
}
.flow-nl-input:focus { outline: none; }
.flow-nl-input::placeholder { color: var(--text-muted); }
.flow-nl-submit {
  padding: 5px 11px;
  border-radius: 7px;
  border: 1px solid rgba(168,85,247,0.45);
  background: rgba(168,85,247,0.22);
  color: #c084fc;
  font-size: 0.82rem;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.12s;
  flex-shrink: 0;
}
.flow-nl-submit:hover:not(:disabled) { background: rgba(168,85,247,0.38); }
.flow-nl-submit:disabled { opacity: 0.38; cursor: default; }

/* ── Live cost meter ─────────────────────────────────────────────────────────── */
.flow-cost-meter {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 1px 7px;
  border-radius: 10px;
  border: 1px solid rgba(251,191,36,0.32);
  background: rgba(251,191,36,0.07);
  color: #fbbf24;
  font-size: 0.65rem;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  letter-spacing: 0.01em;
  animation: flow-cost-blink 2.2s ease-in-out infinite;
}
@keyframes flow-cost-blink {
  0%, 100% { border-color: rgba(251,191,36,0.3); }
  50%       { border-color: rgba(251,191,36,0.65); }
}

/* ── Analytics toggle (active) ───────────────────────────────────────────────── */
.flow-btn-analytics-on {
  --pc-tint: var(--status-info, #3b82f6);
  color: var(--pc-tint) !important;
  border-color: color-mix(in srgb, var(--pc-tint) 40%, transparent) !important;
  background: color-mix(in srgb, var(--pc-tint) 12%, transparent) !important;
}

/* ── Readiness modal ─────────────────────────────────────────────────────────── */
.flow-readiness-overlay {
  position: absolute;
  inset: 0;
  background: rgba(0,0,0,0.58);
  z-index: 500;
  display: flex;
  align-items: center;
  justify-content: center;
  backdrop-filter: blur(5px);
  animation: flow-overlay-in 0.15s ease both;
}
@keyframes flow-overlay-in { from { opacity: 0; } to { opacity: 1; } }
.flow-readiness-modal {
  background: var(--card-bg, rgba(20, 22, 25,0.98));
  border: 1px solid var(--border-primary);
  border-radius: 14px;
  padding: 22px 26px;
  max-width: 360px;
  width: calc(100% - 48px);
  box-shadow: 0 20px 56px rgba(0,0,0,0.65);
  animation: flow-modal-in 0.2s cubic-bezier(0.34,1.56,0.64,1) both;
}
@keyframes flow-modal-in {
  from { opacity: 0; transform: scale(0.88) translateY(12px); }
  to   { opacity: 1; transform: scale(1) translateY(0); }
}
.flow-readiness-title {
  font-size: 0.9rem;
  font-weight: 700;
  color: var(--text-primary);
  margin-bottom: 12px;
}
.flow-readiness-list {
  display: flex;
  flex-direction: column;
  gap: 5px;
  margin-bottom: 16px;
  max-height: 160px;
  overflow-y: auto;
}
.flow-readiness-actions { display: flex; gap: 8px; justify-content: flex-end; }

/* ── Diamond selection ring ──────────────────────────────────────────────────── */
.flow-root .react-flow__node.selected .flow-node-diamond {
  outline: 2px solid rgba(139,92,246,0.85);
  box-shadow: 0 0 0 3px rgba(139,92,246,0.22), 0 0 18px rgba(139,92,246,0.35);
}

/* ── Extension slot strip (LLM node bottom) ──────────────────────────────────── */
/* Horizontal row pinned to the bottom of the node; slots grow leftward from +   */
.flow-ext-rail {
  position: relative;   /* picker positions absolute relative to this container */
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: flex-end;   /* + is rightmost; filled slots stack left of it  */
  gap: 4px;
  margin-top: 6px;
  padding-top: 5px;
  border-top: 1px solid var(--border-secondary);
  z-index: 5;
}
.flow-ext-slot {
  position: relative;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.48rem;
  font-weight: 800;
  color: #fff;
  cursor: pointer;
  transition: transform 0.13s, box-shadow 0.13s;
  user-select: none;
  letter-spacing: -0.02em;
}
.flow-ext-slot:hover {
  transform: scale(1.25);
  box-shadow: 0 0 0 3px rgba(255,255,255,0.18);
  z-index: 10;
}
.flow-ext-slot-remove {
  position: absolute;
  top: -4px;
  right: -4px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #ef4444;
  color: #fff;
  font-size: 0.45rem;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.12s;
  line-height: 1;
}
.flow-ext-slot:hover .flow-ext-slot-remove {
  opacity: 1;
  pointer-events: auto;
}
.flow-ext-slot-add {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 1.5px dashed rgba(100,116,139,0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(100,116,139,0.65);
  font-size: 0.7rem;
  line-height: 1;
  cursor: pointer;
  transition: border-color 0.13s, color 0.13s, transform 0.13s;
  user-select: none;
}
.flow-ext-slot-add:hover {
  border-color: rgba(100,116,139,1);
  color: rgba(100,116,139,1);
  transform: scale(1.15);
}
/* ── Extension picker popover ────────────────────────────────────────────────── */
/* Floats above the slot strip, right-aligned with the rail container             */
.flow-ext-picker {
  position: absolute;
  bottom: calc(100% + 5px);
  right: 0;
  width: 260px;
  background: var(--card-bg, rgba(20, 22, 25,0.98));
  border: 1px solid var(--border-primary);
  border-radius: 10px;
  box-shadow: 0 10px 36px rgba(0,0,0,0.6);
  padding: 9px;
  z-index: 1000;
  animation: flow-ext-picker-in 0.15s cubic-bezier(0.34,1.56,0.64,1) both;
}
@keyframes flow-ext-picker-in {
  from { opacity: 0; transform: scale(0.92) translateY(6px); }
  to   { opacity: 1; transform: scale(1)    translateY(0);   }
}
.flow-ext-picker-title {
  font-size: 0.62rem;
  font-weight: 700;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.07em;
  margin-bottom: 7px;
  padding: 0 2px;
}
/* Search box — mirrors the Extensions config screen filter input */
.flow-ext-picker-search {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 8px;
  margin-bottom: 7px;
  border-radius: 7px;
  background: var(--bg-primary, rgba(255,255,255,0.04));
  border: 1px solid var(--border-secondary, rgba(255,255,255,0.12));
}
.flow-ext-picker-search:focus-within { border-color: var(--teradata-orange, #F15F22); }
.flow-ext-picker-search-icon { width: 13px; height: 13px; flex-shrink: 0; color: var(--text-muted); }
.flow-ext-picker-search input {
  flex: 1;
  min-width: 0;
  background: transparent;
  border: none;
  outline: none;
  font-size: 0.72rem;
  color: var(--text-primary);
}
.flow-ext-picker-search input::placeholder { color: var(--text-muted); }
.flow-ext-picker-list { display: flex; flex-direction: column; gap: 2px; max-height: 280px; overflow-y: auto; }
/* Category section header */
.flow-ext-picker-group + .flow-ext-picker-group { margin-top: 4px; }
.flow-ext-picker-group-label {
  font-size: 0.56rem;
  font-weight: 700;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  padding: 4px 6px 2px;
}
.flow-ext-picker-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 6px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.1s;
}
.flow-ext-picker-item:hover { background: var(--hover-bg-strong, rgba(255,255,255,0.06)); }
.flow-ext-picker-item.active { background: rgba(14,165,233,0.1); }
.flow-ext-picker-dot {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.48rem;
  font-weight: 800;
  color: #fff;
}
.flow-ext-picker-info { flex: 1; min-width: 0; }
.flow-ext-picker-label {
  font-size: 0.7rem;
  font-weight: 600;
  color: var(--text-primary);
}
.flow-ext-picker-desc {
  font-size: 0.6rem;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.flow-ext-picker-check { width: 13px; height: 13px; color: #4ade80; flex-shrink: 0; }
.flow-ext-picker-param {
  margin-top: 7px;
  padding-top: 7px;
  border-top: 1px solid var(--border-secondary);
}
.flow-ext-picker-param-label { font-size: 0.6rem; color: var(--text-muted); margin-bottom: 4px; }
.flow-ext-picker-param input {
  width: 100%;
  box-sizing: border-box;
  background: var(--bg-primary, rgba(20, 22, 25,0.6));
  border: 1px solid var(--border-primary);
  border-radius: 5px;
  color: var(--text-primary);
  font-size: 0.68rem;
  padding: 4px 7px;
  outline: none;
}
.flow-ext-picker-param input:focus { border-color: #0ea5e9; }
.flow-ext-picker-save {
  margin-top: 7px;
  width: 100%;
  padding: 4px 0;
  border-radius: 5px;
  border: 1px solid rgba(14,165,233,0.45);
  background: rgba(14,165,233,0.09);
  color: #38bdf8;
  font-size: 0.68rem;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.12s;
  display: block;
}
.flow-ext-picker-save:hover { background: rgba(14,165,233,0.2); }
.flow-ext-picker-empty {
  font-size: 0.65rem;
  color: var(--text-muted);
  text-align: center;
  padding: 10px 4px;
  line-height: 1.45;
}
`;
  document.head.appendChild(style);
}

function _loadStylesheet(href) {
  if (document.querySelector(`link[href="${href}"]`)) return;
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = href;
  document.head.appendChild(link);
}

async function _loadScript(src) {
  if (document.querySelector(`script[src="${src}"]`)) {
    // Script tag present — wait until it's marked loaded
    return new Promise((resolve, reject) => {
      const el = document.querySelector(`script[src="${src}"]`);
      if (el.dataset.loaded === '1') { resolve(); return; }
      el.addEventListener('load', () => resolve());
      el.addEventListener('error', () => reject(new Error(`Failed to load ${src}`)));
    });
  }
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = src;
    script.onload = () => { script.dataset.loaded = '1'; resolve(); };
    script.onerror = () => reject(new Error(`Failed to load ${src}`));
    document.head.appendChild(script);
  });
}

async function _loadDeps() {
  _loadStylesheet(XYFLOW_CSS_CDN);
  if (!window.React)    await _loadScript(REACT_CDN);
  if (!window.ReactDOM) await _loadScript(REACT_DOM_CDN);
  // @xyflow/react v12 UMD bundle uses the new JSX transform. In browser/global
  // mode its factory reads `globalThis.jsxRuntime` (not `react/jsx-runtime`).
  // The React 18 UMD sets only `window.React`, not that global, so shim it.
  if (window.React && !window.jsxRuntime) {
    window.jsxRuntime = {
      jsx:      window.React.createElement,
      jsxs:     window.React.createElement,
      Fragment: window.React.Fragment,
    };
  }
  if (!window.ReactFlow) await _loadScript(XYFLOW_CDN);
}

// ── Inline-card helpers ───────────────────────────────────────────────────────

function _escHtml(s) {
  return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function _nodeLabel(type) {
  return { start: 'Start', end: 'End', task_llm: 'LLM', task_prompt: 'Prompt', task_tool: 'Tool', task_function: 'Function',
           decision: 'Decision', merge: 'Merge', judge: 'Judge', artefact: 'Canvas', task_action: 'Action',
           file_input: 'File input', file_output: 'File output' }[type] || type;
}

// ── Inline compact card (rendered in chat message) ────────────────────────────

// Derive the badge/chip stats for an inline card from a flow definition.
// Used both at first render and when a live `flow:patch` event refreshes the card.
function _flowCardStats(definition) {
  const allNodes = (definition && definition.nodes) || [];
  const edges    = (definition && definition.edges) || [];
  const nodeCounts = {};
  for (const n of allNodes) {
    const t = n.type || 'unknown';
    if (t === 'scope') continue;
    nodeCounts[t] = (nodeCounts[t] || 0) + 1;
  }
  const visibleNodes = allNodes.filter(n => n.type !== 'scope').length;
  const chips = Object.entries(nodeCounts)
    .map(([t, n]) => `<span class="flow-type-chip">${_nodeLabel(t)}&times;${n}</span>`)
    .join('');
  return { visibleNodes, edgeCount: edges.length, chips, isEmpty: visibleNodes === 0 };
}

// Refresh an already-rendered inline card's badge + chips in-place from a new
// definition (and optionally a renamed flow). Keeps the chat card consistent
// with the live canvas as same-turn patch ops mutate the flow.
// ONE count for one flow: the evidence row around the card carries the flow's size in words,
// written by the server at the moment of the tool call ("… · 2 nodes · created"); the card
// is the LIVE reading (it re-fetches the flow and follows flow:patch). The row follows the
// card — a header that says 2 above a card that says 4 is two answers to one question.
function _syncRowSummary(wrapper, flow, visibleNodes, edgeCount) {
  const row = wrapper && wrapper.closest ? wrapper.closest('details.ans-ev') : null;
  const t = row && row.querySelector('summary.ans-ev-s .ans-ev-t');
  if (!t) return;
  let txt = t.textContent;
  let next = txt.replace(/\b\d+ nodes?\b/, `${visibleNodes} node${visibleNodes !== 1 ? 's' : ''}`);
  if (typeof edgeCount === 'number') {
    next = /\b\d+ edges?\b/.test(next)
      ? next.replace(/\b\d+ edges?\b/, `${edgeCount} edge${edgeCount !== 1 ? 's' : ''}`)
      : next.replace(/(\b\d+ nodes?\b)/, `$1 · ${edgeCount} edge${edgeCount !== 1 ? 's' : ''}`);
  }
  if (flow && flow.name) {
    const sep = next.indexOf(' · ');
    if (sep > 0) next = flow.name + next.slice(sep);
  }
  if (next !== txt) t.textContent = next;
}

function _refreshInlineCardStats(wrapper, definition, flow) {
  if (!wrapper) return;
  const { visibleNodes, edgeCount, chips, isEmpty } = _flowCardStats(definition);
  _syncRowSummary(wrapper, flow, visibleNodes, edgeCount);

  const badge = wrapper.querySelector('.flow-inline-card-badge');
  if (badge) {
    badge.innerHTML = `${visibleNodes} node${visibleNodes !== 1 ? 's' : ''} ` +
      `&middot; ${edgeCount} edge${edgeCount !== 1 ? 's' : ''}`;
  }

  if (flow && flow.name) {
    const nameEl = wrapper.querySelector('.flow-inline-card-name');
    if (nameEl) nameEl.textContent = flow.name;
  }

  // The chips/empty-state live in the card body. The body block is only present
  // when there is a description, chips, or an empty-state to show, so it may not
  // exist yet on a card that was first rendered empty without a description.
  let body = wrapper.querySelector('.flow-inline-card-body');
  const desc = (flow && flow.description) || '';
  if (!body && (desc || chips || isEmpty)) {
    body = document.createElement('div');
    body.className = 'flow-inline-card-body';
    const header = wrapper.querySelector('.flow-inline-card-header');
    if (header) header.insertAdjacentElement('afterend', body);
    else wrapper.prepend(body);
  }
  if (body) {
    body.innerHTML =
      (desc ? `<div class="flow-inline-card-desc">${_escHtml(desc)}</div>` : '') +
      (chips ? `<div class="flow-inline-chips">${chips}</div>` : '') +
      (isEmpty ? `<div class="flow-inline-card-desc" style="font-style:italic">Empty flow &mdash; add nodes via conversation</div>` : '');
  }
}

// Single delegated listener: when any flow is patched, refresh every inline chat
// card bound to that flow so its node/edge count stays authoritative regardless
// of how the flow was built (create-then-patch, AI Build, manual edits).
if (typeof document !== 'undefined' && !window.__flowInlineCardPatchBound) {
  window.__flowInlineCardPatchBound = true;
  UderiaBus.on('uderia:flow-patch', (e) => {
    const detail = e.detail || {};
    const fid = detail.flow_id;
    const flow = detail.flow || {};
    const definition = flow.definition;
    if (!fid || !definition) return;
    document.querySelectorAll(`.flow-inline-card[data-flow-id="${fid}"]`)
      .forEach((card) => _refreshInlineCardStats(card, definition, flow));
  });
}

function _renderFlowInlineCard(container, spec) {
  const flow = spec.flow || {};
  const definition = spec.definition || flow.definition || {};
  const flowId   = flow.flow_id || (spec.metadata || {}).flow_id || '';
  const edges    = definition.edges || [];
  const name     = flow.name || 'Untitled Flow';
  const desc     = flow.description || '';

  // Node type chips
  const { visibleNodes, chips, isEmpty } = _flowCardStats(definition);

  const wrapper = document.createElement('div');
  wrapper.className = 'flow-inline-card';
  // Live flow accent (manifest icon_accent) — overrides any --sp-accent cascading from
  // a host panel (e.g. dashboard tile) so the footer button keeps flow's own colour.
  { const _a = _liveFlowAccent(); if (_a) wrapper.style.setProperty('--sp-accent', _a); }
  if (flowId) wrapper.dataset.flowId = flowId;
  wrapper.innerHTML = `
    <div class="flow-inline-card-header" data-ans-restates>
      <svg class="flow-inline-card-icon" width="16" height="16" viewBox="0 0 24 24" fill="none"
           stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="5" r="2"/><circle cx="5" cy="19" r="2"/><circle cx="19" cy="19" r="2"/>
        <line x1="12" y1="7" x2="5" y2="17"/><line x1="12" y1="7" x2="19" y2="17"/>
      </svg>
      <span class="flow-inline-card-name">${_escHtml(name)}</span>
      <span class="flow-inline-card-badge">${visibleNodes} node${visibleNodes !== 1 ? 's' : ''} &middot; ${edges.length} edge${edges.length !== 1 ? 's' : ''}</span>
    </div>
    ${desc || chips || isEmpty ? `
    <div class="flow-inline-card-body">
      ${desc ? `<div class="flow-inline-card-desc">${_escHtml(desc)}</div>` : ''}
      ${chips ? `<div class="flow-inline-chips">${chips}</div>` : ''}
      ${isEmpty ? `<div class="flow-inline-card-desc" style="font-style:italic">Empty flow &mdash; add nodes via conversation</div>` : ''}
    </div>` : ''}
    <div class="flow-inline-card-footer">
      ${_flowOpenControlHtml(flowId, name)}
    </div>
  `;

  container.innerHTML = '';
  container.appendChild(wrapper);

  // Self-heal: the spec baked into this card is a snapshot from the tool call
  // that produced it (e.g. the `create`, before same-turn add_node patches, or
  // a stale snapshot restored on session reload). Reconcile the badge/chips to
  // the flow's current state once, best-effort. Live edits are handled by the
  // delegated `flow:patch` listener above.
  if (flowId) {
    const token = localStorage.getItem('tda_auth_token');
    fetch(`/api/v1/flows/${flowId}`, { headers: { 'Authorization': `Bearer ${token}` } })
      .then((r) => (r.ok ? r.json() : null))
      .then((freshFlow) => {
        if (freshFlow && freshFlow.definition) {
          _refreshInlineCardStats(wrapper, freshFlow.definition, freshFlow);
        }
      })
      .catch(() => {});
  }

  if (flowId) _flowCardSpecs.set(String(flowId), spec);

  // ── Inline card: Compact/Individual + In session/New session + Run/Stop ──
  const footer = wrapper.querySelector('.flow-inline-card-footer');
  if (footer) {
    footer.style.cssText = 'padding:8px 14px;border-top:1px solid var(--border-secondary);display:flex;align-items:center;gap:6px;flex-wrap:wrap;';

    // ── Pill toggle factory ───────────────────────────────────────────────
    function _mkPill(opts) {
      const wrap = document.createElement('div');
      wrap.style.cssText = 'display:flex;align-items:center;border-radius:4px;border:1px solid var(--border-secondary,rgba(255,255,255,0.1));overflow:hidden;';
      const btns = opts.map(({ value, label, color, bg }) => {
        const b = document.createElement('button');
        b.textContent = label;
        b.dataset.value = value;
        b.style.cssText = 'padding:2px 8px;border:none;cursor:pointer;font-size:0.6rem;font-weight:400;transition:all 0.12s;background:transparent;color:var(--text-muted);';
        wrap.appendChild(b);
        return { b, value, color, bg };
      });
      wrap.setActive = (val) => btns.forEach(({ b, value, color, bg }) => {
        const on = val === value;
        b.style.background = on ? bg : 'transparent';
        b.style.color      = on ? color : 'var(--text-muted)';
        b.style.fontWeight = on ? '600' : '400';
      });
      wrap._entries = btns;
      return wrap;
    }

    // Session sharing is governed by the flow's Serialize|Parallelize setting
    // (in the canvas header), not a per-run pill — so no Compact/Individual
    // control here. Serialize → one shared session; Parallelize → one per node.

    // ── Exec-mode pill (In session / New session) ──────────────────────────
    const execPill = _mkPill([
      { value: 'in_session', label: 'In session',  color: '#c084fc', bg: 'rgba(168,85,247,0.18)' },
      { value: 'new',        label: 'New session', color: '#38bdf8', bg: 'rgba(14,165,233,0.18)' },
    ]);
    execPill.title = 'In session: result in current chat | New session: independent sidebar entry';
    execPill.setActive(_flowModeStore.execMode);
    execPill._entries.forEach(({ b, value }) =>
      b.addEventListener('click', () => { _setFlowMode({ execMode: value }); })
    );

    // ── Subscribe to store changes → auto-refresh pills ───────────────────
    _onFlowModeChange(({ execMode }) => {
      execPill.setActive(execMode);
    });

    // ── Run / Stop button ─────────────────────────────────────────────────
    const flowId = (spec.flow || {}).flow_id || (spec.metadata || {}).flow_id;
    let _cardRunId = null;

    const actionBtn = document.createElement('button');
    actionBtn.style.cssText = 'margin-left:auto;display:inline-flex;align-items:center;gap:5px;padding:4px 12px;border-radius:6px;font-size:0.72rem;cursor:pointer;transition:all 0.15s;border:1px solid rgba(22,163,74,0.4);background:rgba(22,163,74,0.08);color:#4ade80;font-weight:500;';

    const ICONS = {
      run:  `<svg width="9" height="9" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>`,
      stop: `<svg width="9" height="9" viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>`,
      spin: `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" style="animation:_flow-spin 1.4s linear infinite;transform-origin:50% 50%"><circle cx="12" cy="12" r="9" stroke-dasharray="32" stroke-dashoffset="24"/></svg>`,
    };

    function _showRun() {
      actionBtn.innerHTML = `${ICONS.run} Run`;
      actionBtn.style.borderColor = 'rgba(22,163,74,0.4)';
      actionBtn.style.background  = 'rgba(22,163,74,0.08)';
      actionBtn.style.color       = '#4ade80';
      actionBtn.disabled = false;
    }
    function _showRunning() {
      actionBtn.innerHTML = `${ICONS.spin} Running…`;
      actionBtn.style.borderColor = 'rgba(56,189,248,0.4)';
      actionBtn.style.background  = 'rgba(56,189,248,0.08)';
      actionBtn.style.color       = '#38bdf8';
      actionBtn.disabled = false; // keep enabled so user can stop
      actionBtn._isRunning = true;
    }
    function _showStop() {
      actionBtn.innerHTML = `${ICONS.stop} Stop`;
      actionBtn.style.borderColor = 'rgba(248,113,113,0.4)';
      actionBtn.style.background  = 'rgba(248,113,113,0.08)';
      actionBtn.style.color       = '#f87171';
      actionBtn.disabled = false;
      actionBtn._isRunning = true;
    }
    _showRun();

    // Subscribe to run store to reflect canvas state
    _onFlowRunChange(({ runId, status }) => {
      _cardRunId = runId || _cardRunId;
      if (status === 'running' || status === 'queued') { _showStop(); }
      else { _cardRunId = null; _showRun(); }
    });
    // Init from store in case canvas is already running
    if (_flowRunStore.status === 'running' || _flowRunStore.status === 'queued') {
      _cardRunId = _flowRunStore.runId;
      _showStop();
    }

    actionBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!flowId) return;
      const token = localStorage.getItem('tda_auth_token');
      if (!token) return;

      // ── Stop path ─────────────────────────────────────────────────────
      if (actionBtn._isRunning && _cardRunId) {
        actionBtn.disabled = true;
        actionBtn.innerHTML = `${ICONS.spin} Stopping…`;
        try {
          await fetch(`/api/v1/flows/runs/${_cardRunId}/cancel`, {
            method: 'POST', headers: { 'Authorization': `Bearer ${token}` },
          });
        } catch (_) {}
        _cardRunId = null;
        actionBtn._isRunning = false;
        _setFlowRun({ status: 'cancelled', runId: null });
        _showRun();
        return;
      }

      // ── Run path ──────────────────────────────────────────────────────
      _showRunning();
      const { execMode, nodeSessionMode } = _flowModeStore;
      const callerSessionId = execMode === 'in_session' ? (window.__currentSessionId || localStorage.getItem('currentSessionId') || null) : null;
      console.log(`[FlowCard] execMode=${execMode} __currentSessionId=${window.__currentSessionId} localStorage=${localStorage.getItem('currentSessionId')} → callerSessionId=${callerSessionId} nodeSessionMode=${nodeSessionMode}`);
      try {
        const res = await fetch(`/api/v1/flows/${flowId}/execute`, {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ input_params: {}, session_id: callerSessionId, node_session_mode: nodeSessionMode }),
        });
        const d = res.ok ? await res.json() : null;
        if (d?.run_id) {
          _cardRunId = d.run_id;
          _setFlowRun({ runId: d.run_id, status: 'running', flowId });
          _showStop();
          // ── Poll run status so the card resets when execution ends ──────
          // (SSE EventSource is only active when canvas is open; card needs its own watcher)
          const _token = token;
          const _rid   = d.run_id;
          (async function _pollCard() {
            let attempts = 0;
            while (attempts++ < 120) {  // max 10 min at 5s intervals
              await new Promise(r => setTimeout(r, 5000));
              try {
                const sr = await fetch(`/api/v1/flows/runs/${_rid}`, {
                  headers: { 'Authorization': `Bearer ${_token}` },
                });
                if (!sr.ok) break;
                const sd = await sr.json();
                const s = sd.status;
                if (s === 'complete' || s === 'error' || s === 'cancelled') {
                  _setFlowRun({ status: s, runId: null });
                  break;
                }
              } catch (_) { break; }
            }
            // Always reset if we exit the loop
            if (_flowRunStore.status === 'running' || _flowRunStore.status === 'queued') {
              _setFlowRun({ status: 'complete', runId: null });
            }
          })();
        } else {
          _showRun();
          actionBtn._isRunning = false;
        }
      } catch (_) {
        _showRun();
        actionBtn._isRunning = false;
      }
    });
    actionBtn.addEventListener('mouseenter', () => {
      if (!actionBtn.disabled) actionBtn.style.opacity = '0.85';
    });
    actionBtn.addEventListener('mouseleave', () => { actionBtn.style.opacity = '1'; });

    // the run-mode pill sits before the open control, Run after it (as before)
    footer.insertBefore(execPill, footer.querySelector('.sp-open-ctl'));
    footer.appendChild(actionBtn);
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function _uuidShort() {
  return Math.random().toString(36).slice(2, 10);
}

function _nodeSubtitle(node) {
  const d = node.data || {};
  if (node.type === 'task_llm') {
    // PROP-007: an open slot reads its ROLE, not the template prompt — the prompt is
    // the shape's text, the role is what the consumer still has to decide.
    if (node._slotOpen && node._slot) {
      const hint = node._slot.hint ? ` — ${node._slot.hint}` : '';
      const t = `${node._slot.role}${hint}`;
      return t.length > 56 ? t.slice(0, 56) + '…' : t;
    }
    const label = d.profile_id ? (IFOC[d.profile_id]?.label || d.profile_id.slice(0, 12)) : 'LLM';
    const preview = d.prompt ? d.prompt.slice(0, 40) + (d.prompt.length > 40 ? '…' : '') : '';
    return preview || label;
  }
  if (node.type === 'task_prompt')   return d.prompt_name ? d.prompt_name.slice(0, 40) : 'prompt';
  if (node.type === 'task_tool')     return d.tool_name ? d.tool_name.slice(0, 40) : 'tool';
  if (node.type === 'task_action')   return d.action ? d.action.slice(0, 40) : 'action';
  if (node.type === 'task_function') return d.expression ? d.expression.slice(0, 40) + '…' : 'fn()';
  if (node.type === 'decision')      return d.condition  ? d.condition.slice(0, 40) + '…'  : 'condition';
  if (node.type === 'judge') {
    if (node._slotOpen && node._slot) {
      const t = `${node._slot.role}${node._slot.hint ? ` — ${node._slot.hint}` : ''}`;
      return t.length > 56 ? t.slice(0, 56) + '…' : t;
    }
    const r = d.rubric || 'decides whether the round passes';
    return r.length > 56 ? r.slice(0, 56) + '…' : r;
  }
  if (node.type === 'artefact') return 'writes each round\u2019s code to one canvas';
  if (node.type === 'file_input')  return d.filename ? `reads ${d.filename.slice(0, 40)}` : 'choose a file kept in Files';
  if (node.type === 'file_output') return d.filename ? `keeps ${d.filename.slice(0, 40)}` : _fileOutputWords(d);
  return '';
}

// ── PROP-007: the canvas's ONE reading of "is this slot open" ─────────────────
// Mirrors `core/flow_topologies/slots.py:slot_is_open`: a REQUIRED slot is open when
// it has no fill, when its fill names no known profile (a deleted agent is an open
// slot, not a default), or when it names the judge panel on a slot that does not
// accept it. Exported so the gate can drive it directly.
// How a FINISHED run ended, as the node/frame data the canvas draws — the same
// fields the live stream sets (status, verdict, canvas, output, frame ending), so a
// reopened flow shows its last outcome exactly as it looked when the run ended.
// `run` is GET /v1/flows/runs/<id>; the last record per node wins (a loop records
// one per round). Pure, so the gate executes it.
// ── Loop editor model — pure, so the gate executes it ──────────────────────────
// The form a loop's editor starts from, and the update it sends. The server
// (core/flow_loops.py) owns the rules and builds the stored condition from `mode`.
const _THRESHOLD_RE = /^passed == True and \(score is None or score >= ([0-9.]+)\)$/;
export function _loopFormFromScope(scope, members) {
  const ew = (scope && scope.exit_when) || null;
  const cond = ew ? String(ew.condition || '').trim() : '';
  let stop = { kind: 'none', node: '', threshold: '0.8', expression: '' };
  if (ew && ew.node) {
    const m = cond.match(_THRESHOLD_RE);
    if (cond === 'passed == True') stop = { ...stop, kind: 'pass', node: ew.node };
    else if (m) stop = { ...stop, kind: 'threshold', node: ew.node, threshold: m[1] };
    else stop = { ...stop, kind: 'custom', node: ew.node, expression: cond };
  } else {
    const judge = [...(members || [])].reverse().find(x => x.type === 'judge');
    if (judge) stop.node = judge.id;
  }
  return {
    name: (scope && scope.name) || 'Loop',
    rounds: Number((scope && scope.max_iterations) || 3),
    history: scope && scope.history_window ? String(scope.history_window) : 'all',
    stop,
  };
}
export function _loopUpdatesFromForm(form) {
  const st = form.stop || {};
  let exit_when = null;
  if (st.kind === 'pass' && st.node) exit_when = { node: st.node, mode: 'pass' };
  else if (st.kind === 'threshold' && st.node) exit_when = { node: st.node, mode: 'threshold', threshold: Number(st.threshold) };
  else if (st.kind === 'custom' && st.node) exit_when = { node: st.node, mode: 'expression', expression: String(st.expression || '') };
  return {
    name: String(form.name || '').trim() || 'Loop',
    max_iterations: Math.max(SCOPE_MIN_ROUNDS, Math.min(SCOPE_MAX_ROUNDS, Number(form.rounds) || SCOPE_MIN_ROUNDS)),
    history_window: form.history === 'all' ? null : Number(form.history),
    exit_when,
  };
}
// Which selected steps can become a loop (start/end and the frame itself never can).
export function _loopableIds(nodes) {
  return (nodes || []).filter(n => n.selected && !['start', 'end', 'scope'].includes(n.type)).map(n => n.id);
}

export function _runOutcomeOverlay(run, nodeRuns) {
  const nodes = {}, scopes = {};
  const TERMINAL = new Set(['success', 'error', 'cancelled', 'skipped']);
  for (const nr of (nodeRuns || [])) {
    if (!nr || !nr.node_id || !TERMINAL.has(nr.status)) continue;
    const out = (nr.output_state && typeof nr.output_state === 'object') ? nr.output_state : {};
    const d = { _execStatus: nr.status, _outputPreview: null, _errorMsg: null };
    if (nr.status === 'success') d._outputPreview = String(out.output == null ? '' : out.output).slice(0, 200) || null;
    if (nr.status === 'error') d._errorMsg = nr.error_msg || 'This step failed.';
    if (nr.node_type === 'judge' && nr.status === 'success') {
      d._verdict = { passed: out.passed ?? null, score: out.score ?? null, verdict: out.verdict ?? null, judge: out.judge ?? null };
    }
    if (nr.node_type === 'artefact' && out.canvas_id) {
      d._canvasId = out.canvas_id; d._canvasVersion = out.version || 1;
    }
    if ((nr.node_type === 'file_input' || nr.node_type === 'file_output') && out.file_id) {
      d._fileId = out.file_id; d._fileName = out.filename || null;
    }
    nodes[nr.node_id] = d;
  }
  const meta = (run && run.output_result && run.output_result.scopes) || {};
  for (const [sid, m] of Object.entries(meta)) {
    if (!m || !m.exit_reason) continue;
    scopes[sid] = { _round: null, _exitReason: m.exit_reason, _roundsDone: m.rounds_completed || 0 };
  }
  return { nodes, scopes, status: run ? run.status : null };
}

// Paint a run's overlay onto the canvas nodes. Every run field is cleared first, so a
// run shown after another never inherits its predecessor's verdicts or statuses.
const _RUN_FIELDS = ['_execStatus', '_verdict', '_canvasId', '_canvasVersion', '_outputPreview',
                     '_errorMsg', '_round', '_exitReason', '_roundsDone', '_fileId', '_fileName'];
export function _paintRunOverlay(rfNodes, ov) {
  return (rfNodes || []).map(n => {
    const data = { ...n.data };
    for (const k of _RUN_FIELDS) data[k] = null;
    const extra = n.type === 'scope' ? (ov && ov.scopes || {})[String(n.id).replace(/^scope-/, '')] : (ov && ov.nodes || {})[n.id];
    return { ...n, data: extra ? { ...data, ...extra } : data };
  });
}

export function _slotIsOpen(nodeData, profiles) {
  const slot = nodeData && nodeData.slot;
  if (!slot || typeof slot !== 'object') return false;
  if (slot.required === false) return false;
  const fill = typeof nodeData.profile_id === 'string' ? nodeData.profile_id.trim() : '';
  if (!fill) return true;
  if (fill === 'judge_panel') return !(slot.accepts || []).includes('judge_panel');
  if (!Array.isArray(profiles) || profiles.length === 0) return false; // profiles not loaded yet: not a verdict
  return !profiles.some(p => p && p.id === fill);
}

// ── Auth helper ───────────────────────────────────────────────────────────────

function _authHeader() {
  const token = localStorage.getItem('tda_auth_token');
  return token ? { 'Authorization': `Bearer ${token}` } : {};
}

// ── Parallel LLM fan-out detection ────────────────────────────────────────
// The Serialize|Parallelize choice only exists because of the *session* trade-off,
// and only `task_llm` nodes touch a session. So the toggle should appear only
// when a fan-out can run ≥2 LLM nodes concurrently — NOT for decision branches
// (mutually exclusive) or function/decision-only fan-outs (sessionless, always safe).
// A fan-out originates at a non-decision node with ≥2 forward successors; a branch
// "has an LLM" if it reaches a task_llm before hitting a merge (past a join is
// sequential, so a single post-merge LLM does not count). ≥2 such branches → show.
// Tolerant of both ReactFlow and raw-definition node/edge shapes.
function _flowHasParallelLLMFanout(nodes, edges) {
  if (!Array.isArray(nodes) || !Array.isArray(edges)) return false;
  const typeById = {};
  nodes.forEach(n => { typeById[n.id] = (n.type || n.data?.nodeType || n.data?.type); });
  const adj = {};
  edges.forEach(e => {
    const et = e.type || e.data?.edgeType;
    if (et === 'back' || et === 'error') return;
    (adj[e.source] = adj[e.source] || []).push(e.target);
  });
  function branchReachesLLM(start) {
    const seen = new Set();
    const stack = [start];
    while (stack.length) {
      const id = stack.pop();
      if (seen.has(id)) continue;
      seen.add(id);
      const t = typeById[id];
      if (t === 'task_llm') return true;
      if (t === 'merge') continue;            // stop at joins — past here is sequential
      (adj[id] || []).forEach(n => { if (!seen.has(n)) stack.push(n); });
    }
    return false;
  }
  for (const n of nodes) {
    if (typeById[n.id] === 'decision') continue;          // branches are exclusive
    const succ = [...new Set(adj[n.id] || [])];           // dedupe parallel edges
    if (succ.length < 2) continue;
    let branchesWithLLM = 0;
    for (const s of succ) { if (branchReachesLLM(s)) branchesWithLLM++; }
    if (branchesWithLLM >= 2) return true;
  }
  return false;
}

// ── Shared mode store (bridges React canvas ↔ inline card) ─────────────────
// Both the canvas toolbar and the inline card read/write from here so they
// stay in sync regardless of which one the user interacted with last.
const _flowModeStore   = { execMode: 'new', nodeSessionMode: 'compact' };
const _flowModeListeners = new Set();
function _setFlowMode(patch) {
  Object.assign(_flowModeStore, patch);
  _flowModeListeners.forEach(fn => fn({ ..._flowModeStore }));
}
function _onFlowModeChange(fn) {
  _flowModeListeners.add(fn);
  return () => _flowModeListeners.delete(fn);
}

// ── Shared run-state store (bridges canvas ↔ card for stop button) ───────────
const _flowRunStore = { runId: null, status: null };
const _flowRunListeners = new Set();
function _setFlowRun(patch) {
  Object.assign(_flowRunStore, patch);
  _flowRunListeners.forEach(fn => fn({ ..._flowRunStore }));
}
function _onFlowRunChange(fn) {
  _flowRunListeners.add(fn);
  return () => _flowRunListeners.delete(fn);
}

// ── Flow version store (module-level, survives React remounts) ──────────────
// Keyed by flow_id → array of version objects loaded from backend.
// Updated by FlowApp after each auto-snapshot.
const _flowVersions = new Map();

// ── React Flow app factory ────────────────────────────────────────────────────

function _buildFlowApp(spec) {
  const R = window.React;
  const h = R.createElement;
  const {
    useState, useCallback, useEffect, useRef, useMemo, useLayoutEffect,
  } = R;

  // ── Priority+ overflow toolbar ──────────────────────────────────────────────
  // Best-practice responsive toolbar: high-priority controls stay inline; when the
  // row runs out of width the lowest-priority ones collapse into a single "⋯ More"
  // menu — one fixed-height row, nothing ever hidden without an affordance, every
  // control reachable by mouse / keyboard / screen reader.
  //
  // Children with a truthy `data-pin` prop are PINNED (never overflow) and render
  // at the right — used for the primary actions (Run / Cancel / Analytics). The
  // rest are flexible and collapse right-to-left (source order = priority).
  //
  // Measurement is reconciliation-safe: a ResizeObserver + a pre-paint
  // useLayoutEffect shrink loop converge before the browser paints, so there is no
  // flash. Defined at factory scope so its identity is stable across FlowApp
  // re-renders (otherwise it would remount and lose its overflow state).
  function ToolbarOverflow({ children }) {
    const rowRef    = useRef(null);
    const menuBtnRef = useRef(null);
    const all      = R.Children.toArray(children).filter(Boolean);
    const pinned   = all.filter(c => c && c.props && c.props['data-pin']);
    const flexible = all.filter(c => !(c && c.props && c.props['data-pin']));

    const [visible, setVisible]   = useState(flexible.length);
    const [menuOpen, setMenuOpen] = useState(false);
    const [menuPos, setMenuPos]   = useState(null);
    const [, setNonce]            = useState(0);
    const pendingReset            = useRef(false);

    // When the set of flexible items changes (a conditional control appears /
    // disappears), reset to "show all" and let the measure pass re-collapse.
    useEffect(() => { setVisible(flexible.length); setMenuOpen(false); }, [flexible.length]);

    // Re-measure on container resize. We bump a NONCE (not setVisible) so the
    // layout effect ALWAYS re-runs — setVisible(same value) bails out of the
    // render, so the measurement would never happen when the panel narrows while
    // already showing everything. pendingReset makes the next pass grow-then-trim.
    useEffect(() => {
      const el = rowRef.current;
      if (!el || typeof ResizeObserver === 'undefined') return;
      let raf = 0;
      const ro = new ResizeObserver(() => {
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(() => { pendingReset.current = true; setNonce(n => n + 1); });
      });
      ro.observe(el);
      return () => { ro.disconnect(); cancelAnimationFrame(raf); };
    }, []);

    // Pre-paint measure: sum the rendered children's widths against the available
    // width. No overflow:hidden needed (so inline dropdowns aren't clipped). On a
    // resize, first grow back to all, then trim one-by-one until it fits — all
    // before the browser paints, so there's no flash.
    useLayoutEffect(() => {
      const el = rowRef.current;
      if (!el) return;
      if (pendingReset.current) {
        pendingReset.current = false;
        if (visible !== flexible.length) { setVisible(flexible.length); return; }
      }
      const avail = el.clientWidth;
      const kids = el.children;
      let used = 0;
      for (let i = 0; i < kids.length; i++) used += kids[i].offsetWidth;
      used += 6 * Math.max(0, kids.length - 1); // account for the flex gap
      if (used > avail + 1 && visible > 0) {
        setVisible(v => Math.max(0, v - 1));
      }
    });

    // Close the overflow menu on outside-click / Escape.
    useEffect(() => {
      if (!menuOpen) return;
      const onDoc = (e) => {
        if (!e.target.closest || (!e.target.closest('.flow-overflow-menu') && !e.target.closest('.flow-overflow-btn'))) {
          setMenuOpen(false);
        }
      };
      const onEsc = (e) => { if (e.key === 'Escape') setMenuOpen(false); };
      document.addEventListener('mousedown', onDoc);
      document.addEventListener('keydown', onEsc);
      return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onEsc); };
    }, [menuOpen]);

    const inline     = flexible.slice(0, visible);
    const overflowed = flexible.slice(visible);

    const toggleMenu = () => {
      const r = menuBtnRef.current && menuBtnRef.current.getBoundingClientRect();
      if (r) setMenuPos({ top: r.bottom + 6, right: Math.max(8, window.innerWidth - r.right) });
      setMenuOpen(o => !o);
    };

    const menuPortal = (menuOpen && overflowed.length > 0 && menuPos && window.ReactDOM)
      ? window.ReactDOM.createPortal(
          h('div', {
            className: 'flow-overflow-menu',
            role: 'menu',
            style: { position: 'fixed', top: menuPos.top + 'px', right: menuPos.right + 'px' },
          },
            overflowed.map((node, i) => h('div', { key: i, className: 'flow-overflow-row', role: 'menuitem' }, node)),
          ),
          document.body)
      : null;

    const moreBtn = overflowed.length > 0 && h('button', {
      ref: menuBtnRef,
      className: 'flow-btn flow-overflow-btn' + (menuOpen ? ' is-open' : ''),
      title: 'More toolbar actions',
      'aria-haspopup': 'menu',
      'aria-expanded': menuOpen ? 'true' : 'false',
      onClick: toggleMenu,
      style: { padding: '3px 5px' },
    },
      h('svg', { width: 16, height: 16, viewBox: '0 0 24 24', fill: 'currentColor' },
        h('circle', { cx: 5,  cy: 12, r: 1.7 }),
        h('circle', { cx: 12, cy: 12, r: 1.7 }),
        h('circle', { cx: 19, cy: 12, r: 1.7 }),
      ),
    );

    return h('div', {
      ref: rowRef,
      className: 'flow-toolbar-cluster',
      style: {
        // flex-basis 0 (not auto) is critical: it sizes the row by AVAILABLE space,
        // so it tracks the panel width and shrinks when the panel narrows. With
        // basis:auto the row would size to its content, never change size on a panel
        // resize, and the ResizeObserver would never fire → "nothing happens".
        marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center',
        minWidth: 0, flex: '1 1 0%', justifyContent: 'flex-end',
        flexWrap: 'nowrap',
      },
    },
      inline,
      moreBtn,
      menuPortal,
      pinned,
    );
  }

  // ── Themed dropdown — replaces native <select> (whose OS popup can't be styled).
  //    Reuses the existing .flow-ver-dropdown / .flow-sel-item body-portal pattern.
  //    options: [{ value, label, title? }]. Keeps the same value/onChange contract. ──
  function FlowSelect({ value, options, onChange, placeholder, title, btnStyle, wrapStyle }) {
    const [open, setOpen] = useState(false);
    const btnRef = useRef(null);
    const dropRef = useRef(null);
    useEffect(() => {
      if (!open) return;
      const onDown = (e) => {
        if (btnRef.current && btnRef.current.contains(e.target)) return;
        if (dropRef.current && dropRef.current.contains(e.target)) return;
        setOpen(false);
      };
      const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
      const onResize = () => setOpen(false);
      document.addEventListener('mousedown', onDown, true);
      document.addEventListener('keydown', onKey, true);
      window.addEventListener('resize', onResize);
      return () => {
        document.removeEventListener('mousedown', onDown, true);
        document.removeEventListener('keydown', onKey, true);
        window.removeEventListener('resize', onResize);
      };
    }, [open]);

    const sel = (options || []).find(o => o.value === value);
    const label = sel ? sel.label : (placeholder || 'Select…');

    let dropStyle = null;
    if (open && btnRef.current) {
      const rect = btnRef.current.getBoundingClientRect();
      const GAP = 4, MARGIN = 8;
      const width = Math.max(rect.width, Math.min(260, window.innerWidth - MARGIN * 2));
      const left = Math.max(MARGIN, Math.min(rect.left, window.innerWidth - width - MARGIN));
      const maxHeight = Math.max(160, window.innerHeight - rect.bottom - GAP - MARGIN);
      dropStyle = { position: 'fixed', top: (rect.bottom + GAP) + 'px', left: left + 'px',
                    right: 'auto', width: width + 'px', maxHeight: maxHeight + 'px' };
    }

    const dropdown = (open && dropStyle && window.ReactDOM)
      ? window.ReactDOM.createPortal(
          h('div', { className: 'flow-ver-dropdown', ref: dropRef, style: dropStyle, onClick: e => e.stopPropagation() },
            (options || []).map((o) => {
              const isActive = o.value === value;
              return h('div', {
                key: o.value,
                className: `flow-sel-item${isActive ? ' flow-sel-item--current' : ''}`,
                title: o.title || undefined,
                onClick: () => { setOpen(false); if (o.value !== value) onChange(o.value); },
              },
                h('span', { className: 'flow-sel-check' }, isActive ? '✓' : ''),
                // IFOC profile-class colour: a dot + class-tinted name when present.
                o.color ? h('span', { className: 'flow-sel-dot', style: { background: o.color } }) : null,
                h('span', { className: 'flow-sel-name', style: o.color ? { color: o.color } : undefined }, o.label),
              );
            }),
          ),
          document.body)
      : null;

    return h('div', { className: 'flow-cs-wrap', style: wrapStyle || undefined },
      h('button', {
        type: 'button',
        ref: btnRef,
        className: 'flow-cs-trigger',
        title: title || label,
        style: btnStyle || undefined,
        onClick: e => { e.stopPropagation(); setOpen(o => !o); },
      },
        h('span', { className: 'flow-cs-trigger-label', style: (sel && sel.color) ? { color: sel.color } : undefined }, label),
      ),
      dropdown,
    );
  }

  const RF = window.ReactFlow || {};
  // @xyflow/react v12 UMD exports everything on window.ReactFlow
  const {
    ReactFlow,
    Controls,
    MiniMap,
    Background,
    BackgroundVariant,
    Handle,
    Position,
    addEdge,
    applyNodeChanges,
    applyEdgeChanges,
    MarkerType,
    useUpdateNodeInternals,
    useNodesInitialized,
    useReactFlow,
  } = RF;

  // Whether this UMD build exposes the v12 store hooks we use to repair handle bounds.
  const _hasRFHooks = !!(useUpdateNodeInternals && useNodesInitialized && useReactFlow);

  // HandleBoundsFixer — rendered as a CHILD of <ReactFlow> so it lives inside the
  // ReactFlow store context (like Controls/MiniMap) and can use store hooks.
  //
  // Root cause it fixes: on a cold load (React Flow CDN just finished loading), a
  // custom node's <Handle> children mount one frame AFTER the node itself is first
  // measured. ReactFlow computes handle bounds during the node-measurement pass, so
  // it misses the handles and stores handleBounds=null. Node dimensions are still
  // measured (nodes stay centred) but every edge loses its endpoints and renders
  // nothing. Nothing re-measures handles afterward (the node's ResizeObserver only
  // fires on a SIZE change, which doesn't happen when the handle children appear),
  // so edges stay invisible until a full remount (switch session away+back) — which
  // is exactly the reported symptom. The pre-existing inst.updateNodeInternals path
  // fired only in the first ~100ms (before the handles painted) and never again.
  //
  // Fix: when useNodesInitialized() flips true (all nodes measured → handles are in
  // the DOM), force updateNodeInternals() for every node to re-register handle
  // bounds, with two post-paint retries for safety.
  function HandleBoundsFixer() {
    const nodesInitialized   = useNodesInitialized();
    const updateNodeInternals = useUpdateNodeInternals();
    const rf                 = useReactFlow();
    useEffect(() => {
      if (!nodesInitialized) return;
      const ids = rf.getNodes().map(n => n.id);
      if (!ids.length) return;
      const fire = () => { try { updateNodeInternals(ids); } catch (_) {} };
      fire();                                    // re-register now
      const t1 = setTimeout(fire, 60);           // after handle DOM paints
      const t2 = setTimeout(() => { fire(); try { rf.fitView({ padding: 0.2, duration: 200 }); } catch (_) {} }, 260);
      return () => { clearTimeout(t1); clearTimeout(t2); };
    }, [nodesInitialized, updateNodeInternals, rf]);
    return null;
  }

  const flow = spec.flow || {};
  const def  = (flow.definition || spec.definition) || { nodes: [], edges: [], scopes: [], global_params: {} };
  console.log('[FlowDebug] _buildFlowApp: ReactFlow available:', !!ReactFlow, '| def.nodes:', def.nodes?.length, '| def.edges:', def.edges?.length,
    '| node positions:', (def.nodes||[]).slice(0,3).map(n => `${n.id}@(${n.position?.x},${n.position?.y})`).join(', '));

  // ── Convert definition nodes/edges to React Flow format ──────────────────

  // Node dimensions — must match the CSS so React Flow applies nodeOrigin
  // correctly before ResizeObserver fires. All rect nodes: 200×72.
  // Oval nodes (start/end): 140×40. Decision diamond: 90×90.
  const NODE_DIMS = {
    start:         { width: 140, height: 40 },
    end:           { width: 140, height: 40 },
    task_llm:      { width: 200, height: 72 },
    task_prompt:   { width: 200, height: 72 },
    task_tool:     { width: 200, height: 72 },
    task_action:   { width: 200, height: 72 },
    task_function: { width: 200, height: 72 },
    merge:         { width: 200, height: 72 },
    decision:      { width: 128, height: 128 },
    judge:         { width: 200, height: 72 },
    artefact:      { width: 200, height: 72 },
    file_input:    { width: 200, height: 72 },
    file_output:   { width: 200, height: 72 },
  };
  function _nodeDims(type) {
    return NODE_DIMS[type] || { width: 200, height: 72 };
  }

  function _toRFNodes(defNodes, defScopes) {
    const scopeMap = {};
    (defScopes || []).forEach(s => s.node_ids.forEach(nid => scopeMap[nid] = s.id));

    const nodes = (defNodes || []).map(n => ({
      id:       n.id,
      type:     n.type || 'task_llm',
      position: n.position || { x: 0, y: 100 },
      ...(_nodeDims(n.type)),
      data:     { ...n, _type: n.type, _scopeId: scopeMap[n.id] || null },
    }));

    // Add scope group nodes — the loop frame. Its position/size come from the layout
    // (flow_layout.frame_scopes), so it always encloses its members; it sits behind
    // the nodes and is not dragged on its own (it would drift off its members).
    const _labelOf = {};
    (defNodes || []).forEach(n => { _labelOf[n.id] = n.label || n.id; });
    (defScopes || []).forEach(scope => {
      const size = scope.size || { width: 200, height: 150 };
      const exitId = scope.exit_when && scope.exit_when.node;
      nodes.push({
        id:     `scope-${scope.id}`,
        type:   'scope',
        position: scope.position || { x: 50, y: 50 },
        width: size.width, height: size.height,
        style:  { width: size.width, height: size.height },
        zIndex: -1,
        data:   { ...scope, _exitLabel: exitId ? _labelOf[exitId] : null },
        draggable: false,
        selectable: false,
        connectable: false,
      });
    });

    return nodes;
  }

  function _toRFEdges(defEdges) {
    return (defEdges || []).map(e => {
      // Decision branches carry condition 'true'/'false' (the source handle id).
      // Render them as clear, color-coded pills instead of muted lowercase text.
      const isBool = e.condition === 'true' || e.condition === 'false';
      const branchColor = e.condition === 'true' ? '#16a34a' : '#dc2626';
      return {
        id:             e.id,
        source:         e.source,
        target:         e.target,
        sourceHandle:   isBool ? e.condition : undefined,
        type:           e.type === 'cycle' ? 'loopback' : 'smoothstep',
        animated:       e.type === 'cycle',
        style:          isBool ? { ..._edgeStyle(e.type), stroke: branchColor } : _edgeStyle(e.type),
        markerEnd:      { type: MarkerType?.ArrowClosed || 'arrowclosed', width: 18, height: 18,
                          color: isBool ? branchColor : _edgeColor(e.type) },
        label:          isBool ? (e.condition === 'true' ? 'True' : 'False') : (e.condition || undefined),
        labelStyle:     isBool ? { fill: '#fff', fontSize: 10, fontWeight: 700 }
                               : { fill: 'var(--text-muted)', fontSize: 10 },
        labelShowBg:    isBool,
        labelBgStyle:   isBool ? { fill: branchColor } : undefined,
        labelBgPadding: isBool ? [6, 2] : undefined,
        labelBgBorderRadius: isBool ? 4 : undefined,
        data:           e,
      };
    });
  }

  function _edgeColor(type) {
    if (type === 'error')       return '#dc2626';
    if (type === 'conditional') return '#d97706';
    if (type === 'cycle')       return '#9333ea';
    return 'var(--border-primary)';
  }
  function _edgeStyle(type) {
    return { stroke: _edgeColor(type), strokeWidth: type === 'cycle' ? 1.5 : 1.5,
             strokeDasharray: type === 'cycle' ? '5,3' : undefined };
  }

  // ── Status icon helper ───────────────────────────────────────────────────
  function _statusIcon(status) {
    if (!status) return null;
    if (status === 'running') return h('svg', {
      className: 'flow-node-status-icon running',
      viewBox: '0 0 24 24', fill: 'none', stroke: '#38bdf8', strokeWidth: 2,
      strokeLinecap: 'round', strokeLinejoin: 'round',
    },
      // Rotating gear (cog) — visualises the node actively executing.
      h('circle', { cx: 12, cy: 12, r: 3 }),
      h('path', { d: 'M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z' }),
    );
    if (status === 'success') return h('svg', {
      className: 'flow-node-status-icon success',
      viewBox: '0 0 24 24', fill: 'none', stroke: '#4ade80', strokeWidth: 2.5, strokeLinecap: 'round', strokeLinejoin: 'round',
    }, h('polyline', { points: '20 6 9 17 4 12' }));
    if (status === 'error') return h('svg', {
      className: 'flow-node-status-icon error',
      viewBox: '0 0 24 24', fill: 'none', stroke: '#f87171', strokeWidth: 2.5, strokeLinecap: 'round',
    }, h('line', { x1: 18, y1: 6, x2: 6, y2: 18 }), h('line', { x1: 6, y1: 6, x2: 18, y2: 18 }));
    return null;
  }

  // ── Custom node components ────────────────────────────────────────────────

  function OvalNode({ data, selected }) {
    const type = data._type || data.type;
    const color = NODE_COLORS[type] || 'var(--border-primary)';
    const status = data._execStatus;
    const isStart = type === 'start';
    const isEnd   = type === 'end';
    const animCls = data._entering ? ' flow-node--entering'
                  : data._leaving  ? ' flow-node--leaving'
                  : data._updated  ? ' flow-node--updated' : '';
    return h('div', {
      className: `flow-node-oval${animCls}${data._execStatus === 'running' ? ' flow-node--running' : ''}`,
      style: { borderColor: color, color },
    },
      _statusIcon(status),
      // Target handle for end node (top)
      isEnd && h(Handle, { type: 'target', position: Position?.Top || 'top',
        style: { background: color } }),
      h('span', { style: { color } }, data.label || type),
      // Source handle for start node (bottom)
      isStart && h(Handle, { type: 'source', position: Position?.Bottom || 'bottom',
        style: { background: color } }),
    );
  }

  function StandardNode({ data, selected }) {
    const type = data._type || data.type || 'task_llm';
    const color = NODE_COLORS[type] || '#0284c7';
    const status = data._execStatus;
    const subtitle = _nodeSubtitle(data);
    const profileType = data.data?.profile_type;
    const ifocColor = profileType ? IFOC[profileType]?.color : null;

    const animCls = data._entering ? ' flow-node--entering'
                  : data._leaving  ? ' flow-node--leaving'
                  : data._updated  ? ' flow-node--updated' : '';
    return h('div', {
      className: `flow-node ${selected ? 'selected' : ''}${animCls}${data._execStatus === 'running' ? ' flow-node--running' : ''}`,
      style: { borderColor: color },
    },
      _statusIcon(status),
      // Top handle
      h(Handle, { type: 'target', position: Position?.Top || 'top', style: { background: color } }),
      // Content
      h('div', { className: 'flow-node-header' },
        h('div', { className: 'flow-node-type-dot', style: { background: color } }),
        h('div', { className: 'flow-node-label' }, data.label || type),
        ifocColor && h('div', {
          style: {
            width: 6, height: 6, borderRadius: '50%',
            background: ifocColor, marginLeft: 'auto', flexShrink: 0,
          },
        }),
      ),
      subtitle && h('div', { className: 'flow-node-subtitle' }, subtitle),
      // Execution stats appear ONLY when the "show execution stats" toggle is on
      // (the analytics overlay below) — no always-on token line, no duplication.
      data._showAnalytics && data._runStats && h('div', { className: 'flow-node-stat-overlay' },
        data._runStats.ms   != null && h('span', { className: 'flow-node-stat-pill' }, `⏱ ${(data._runStats.ms / 1000).toFixed(1)}s`),
        data._runStats.tIn  != null && h('span', { className: 'flow-node-stat-pill' }, `↑ ${(data._runStats.tIn || 0).toLocaleString()}`),
        data._runStats.tOut != null && h('span', { className: 'flow-node-stat-pill' }, `↓ ${(data._runStats.tOut || 0).toLocaleString()}`),
        data._runStats.rows != null && h('span', { className: 'flow-node-stat-pill' }, `${data._runStats.rows} rows`),
      ),
      // Fix with AI button — appears on error nodes
      data._execStatus === 'error' && h('button', {
        className: 'flow-node-fix-btn',
        onClick: e => {
          e.stopPropagation();
          UderiaBus.emit('uderia:flow-fix-node', { node_id: data.id, node: data });
        },
      },
        h('svg', { width: 10, height: 10, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2 },
          h('polygon', { points: '12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2' }),
        ),
        '✦ Fix with AI',
      ),
      // Bottom handle
      h(Handle, { type: 'source', position: Position?.Bottom || 'bottom', style: { background: color } }),
    );
  }

  // ── LLMNode: LLM task node with embedded extension slot rail ─────────────
  // Replaces StandardNode for task_llm. Keeps the same layout but adds a
  // vertically-stacked extension slot rail on the right side below the status
  // icon. Slots show activated extensions; "+" opens a profile-aware picker.

  function LLMNode({ data, selected }) {
    const type        = data._type || data.type || 'task_llm';
    const color       = NODE_COLORS[type] || '#0284c7';
    const status      = data._execStatus;
    const subtitle    = _nodeSubtitle(data);
    // Effective profile (own or inherited flow default) stamped by displayNodes.
    // Re-derived on every render, so changing the node's profile in the inspector
    // (which updates rfNodes → displayNodes re-stamps) updates this badge live.
    const profileType = data._profileType;
    const ifocColor   = profileType ? IFOC[profileType]?.color : null;
    const extensions  = data.data?.extensions || [];

    // pickerSlot: null=closed, -1=add new, N=edit slot N
    const [pickerSlot,  setPickerSlot]  = useState(null);
    const [pickerSelId, setPickerSelId] = useState(null);
    const [pickerParam, setPickerParam] = useState('');
    const [pickerSearch, setPickerSearch] = useState('');
    const [availExts,   setAvailExts]   = useState(null); // null = not yet loaded

    const pickerRef = useRef(null);
    const railRef   = useRef(null);
    const searchRef = useRef(null);
    const [pickerPos, setPickerPos] = useState(null);

    const animCls = data._entering ? ' flow-node--entering'
                  : data._leaving  ? ' flow-node--leaving'
                  : data._updated  ? ' flow-node--updated' : '';

    // Fetch flow-eligible extensions when the picker opens. The list is built
    // entirely from the manifest-backed endpoint (_fetchFlowExtensions applies
    // admin governance, user activation, and flow_node_eligible) — re-fetched on
    // every open so newly activated extensions appear without a page reload.
    useEffect(() => {
      if (pickerSlot === null) { setPickerPos(null); return; }
      if (railRef.current) {
        const rect = railRef.current.getBoundingClientRect();
        setPickerPos({ bottom: window.innerHeight - rect.top + 5, left: Math.max(8, rect.right - 260) });
      }
      setPickerSearch('');
      _fetchFlowExtensions().then(setAvailExts);
      // Pre-populate picker when editing an existing slot
      if (pickerSlot >= 0 && pickerSlot < extensions.length) {
        const existing = extensions[pickerSlot];
        setPickerSelId(existing.id);
        setPickerParam(existing.param || '');
      } else {
        setPickerSelId(null);
        setPickerParam('');
      }
      // Auto-focus the search box once the popover is mounted.
      setTimeout(() => { if (searchRef.current) searchRef.current.focus(); }, 30);
    }, [pickerSlot]);

    // Close picker on Escape or click outside the picker popover
    useEffect(() => {
      if (pickerSlot === null) return;
      const onKey   = e => { if (e.key === 'Escape') setPickerSlot(null); };
      const onClick = e => { if (pickerRef.current && !pickerRef.current.contains(e.target)) setPickerSlot(null); };
      document.addEventListener('keydown',     onKey);
      document.addEventListener('pointerdown', onClick);
      return () => {
        document.removeEventListener('keydown',     onKey);
        document.removeEventListener('pointerdown', onClick);
      };
    }, [pickerSlot]);

    function _dispatchSave(newExts) {
      UderiaBus.emit('uderia:flow-node-extensions-save', { node_id: data.id, extensions: newExts });
      setPickerSlot(null);
    }

    function handleConfirm() {
      if (!pickerSelId) return;
      const selMeta = (availExts || []).find(b => b.id === pickerSelId);
      const entry   = { id: pickerSelId, ...(selMeta?.hasParam && pickerParam ? { param: pickerParam } : {}) };
      const newExts = pickerSlot === -1
        ? [...extensions, entry]
        : extensions.map((e, i) => i === pickerSlot ? entry : e);
      _dispatchSave(newExts);
    }

    function handleRemove() {
      _dispatchSave(extensions.filter((_, i) => i !== pickerSlot));
    }

    const editingExt = (pickerSlot >= 0 && pickerSlot < extensions.length) ? extensions[pickerSlot] : null;
    const selMeta    = (availExts || []).find(b => b.id === pickerSelId);

    // Search-filtered + category-grouped picker list (search semantics mirror the
    // Extensions config screen: id + label + description + keywords).
    const _filteredExts = (availExts || []).filter(e => _extMatchesSearch(e, pickerSearch));
    const _extGroups    = _groupExtsByCategory(_filteredExts);

    const _renderPickerItem = (ext) => h('div', {
      key: ext.id,
      className: `flow-ext-picker-item${pickerSelId === ext.id ? ' active' : ''}`,
      onClick: () => { setPickerSelId(ext.id); if (!ext.hasParam) setPickerParam(''); },
    },
      h('div', { className: 'flow-ext-picker-dot', style: { background: ext.color } }, ext.abbr),
      h('div', { className: 'flow-ext-picker-info' },
        h('div', { className: 'flow-ext-picker-label' }, ext.label),
        h('div', { className: 'flow-ext-picker-desc'  }, ext.desc),
      ),
      pickerSelId === ext.id && h('svg', {
        className: 'flow-ext-picker-check',
        viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
        strokeWidth: 3, strokeLinecap: 'round', strokeLinejoin: 'round',
      }, h('polyline', { points: '20 6 9 17 4 12' })),
    );

    // Picker rendered via portal into document.body so it escapes the React Flow
    // node's stacking context (which is created by will-change: transform on .flow-node).
    const pickerPortal = pickerSlot !== null && pickerPos && window.ReactDOM
      ? window.ReactDOM.createPortal(
          h('div', {
            className: 'flow-ext-picker',
            ref: pickerRef,
            style: { position: 'fixed', bottom: pickerPos.bottom + 'px', left: pickerPos.left + 'px', right: 'auto', top: 'auto' },
            onMouseDown: e => e.stopPropagation(),
            onClick:     e => e.stopPropagation(),
          },
            h('div', { className: 'flow-ext-picker-title' }, editingExt ? 'Edit extension' : 'Add extension'),
            // Search box — only when there is a list to search.
            availExts && availExts.length > 0 && h('div', { className: 'flow-ext-picker-search' },
              h('svg', { className: 'flow-ext-picker-search-icon', viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' },
                h('circle', { cx: 11, cy: 11, r: 8 }),
                h('line', { x1: 21, y1: 21, x2: 16.65, y2: 16.65 }),
              ),
              h('input', {
                ref: searchRef,
                type: 'text', value: pickerSearch,
                placeholder: 'Search extensions…',
                onChange:  e => setPickerSearch(e.target.value),
                onKeyDown: e => {
                  e.stopPropagation();
                  // Escape closes the picker (stopPropagation above would otherwise
                  // swallow the document-level close handler).
                  if (e.key === 'Escape') { setPickerSlot(null); return; }
                  // Enter selects the sole match for fast keyboard add.
                  if (e.key === 'Enter' && _filteredExts.length === 1) {
                    setPickerSelId(_filteredExts[0].id);
                    if (!_filteredExts[0].hasParam) setPickerParam('');
                  }
                },
              }),
            ),
            availExts === null
              ? h('div', { className: 'flow-ext-picker-empty' }, 'Loading…')
              : availExts.length === 0
                ? h('div', { className: 'flow-ext-picker-empty' }, 'No extensions activated — enable them in the Extensions tab.')
                : _filteredExts.length === 0
                  ? h('div', { className: 'flow-ext-picker-empty' }, `No extensions match “${pickerSearch}”.`)
                  : h('div', { className: 'flow-ext-picker-list' },
                      ..._extGroups.map(group =>
                        h('div', { key: group.category, className: 'flow-ext-picker-group' },
                          h('div', { className: 'flow-ext-picker-group-label' }, group.category),
                          ...group.items.map(_renderPickerItem),
                        )
                      ),
                    ),
            selMeta?.hasParam && h('div', { className: 'flow-ext-picker-param' },
              h('div', { className: 'flow-ext-picker-param-label' }, 'Parameter (optional)'),
              h('input', {
                type: 'text', value: pickerParam,
                placeholder: 'e.g. critical',
                onChange:  e => setPickerParam(e.target.value),
                onKeyDown: e => { if (e.key === 'Enter' && pickerSelId) handleConfirm(); e.stopPropagation(); },
              }),
            ),
            pickerSelId && h('button', {
              className: 'flow-ext-picker-save',
              onClick: handleConfirm,
            }, editingExt ? 'Update' : 'Add'),
            editingExt && h('button', {
              className: 'flow-ext-picker-save',
              style: { background: 'rgba(239,68,68,0.1)', borderColor: 'rgba(239,68,68,0.4)', color: '#f87171', marginTop: 3 },
              onClick: handleRemove,
            }, 'Remove'),
          ),
          document.body
        )
      : null;

    return h(R.Fragment, null,
      h('div', {
        className: `flow-node ${selected ? 'selected' : ''}${animCls} flow-node-llm${data._execStatus === 'running' ? ' flow-node--running' : ''}${data._slotOpen ? ' flow-node--slot-open' : ''}`,
        style: data._slotOpen ? undefined : { borderColor: color },
      },
      _statusIcon(status),
      h(Handle, { type: 'target', position: Position?.Top || 'top', style: { background: color } }),
      // Header (no type dot — cleaner look)
      h('div', { className: 'flow-node-header' },
        h('div', { className: 'flow-node-label' }, data.label || type),
        // Profile tag pill — coloured by the profile's IFOC type (colour conveys
        // the type, so no separate type label). Dimmed when inherited from the
        // flow default rather than set on the node itself.
        data._profileTag && h('div', {
          title: data._profileInherited
            ? `Inherits flow default: @${data._profileTag}`
            : `Profile: @${data._profileTag}`,
          style: {
            // Reserve the top-right corner for the status icon (✓/spinner,
            // absolute at right:7px ~15px wide) — sit the pill to its left.
            marginLeft: 'auto', marginRight: '16px', flexShrink: 0,
            fontSize: '0.6rem', fontWeight: 700, lineHeight: 1.4,
            padding: '1px 8px', borderRadius: 999,
            color: ifocColor || 'var(--text-muted, #94a3b8)',
            border: `1px solid ${ifocColor || 'var(--border-secondary, rgba(255,255,255,0.15))'}`,
            background: ifocColor ? `${ifocColor}1a` : 'transparent',
            opacity: data._profileInherited ? 0.65 : 1,
          },
        }, `@${data._profileTag}`),
        // PROP-007 slot badge: an open slot says what it is waiting for (role), with the
        // hint on hover; a judge slot filled with the eval panel says so (it is not a profile).
        data._slotOpen && data._slot && h('div', {
          className: 'flow-node-slot-badge',
          'data-slot-role': data._slot.role,
          title: `Open slot — ${data._slot.role}${data._slot.hint ? ': ' + data._slot.hint : ''}. Pick an agent in the inspector.`,
        }, `slot · ${data._slot.role}`),
        !data._slotOpen && data._slotJudgePanel && h('div', {
          className: 'flow-node-slot-badge is-judge',
          title: 'This slot is graded by the eval judge panel (not a profile).',
        }, 'judge panel'),
      ),
      subtitle && h('div', { className: 'flow-node-subtitle' }, subtitle),
      // Execution stats appear ONLY when the "show execution stats" toggle is on.
      data._showAnalytics && data._runStats && h('div', { className: 'flow-node-stat-overlay' },
        data._runStats.ms   != null && h('span', { className: 'flow-node-stat-pill' }, `⏱ ${(data._runStats.ms / 1000).toFixed(1)}s`),
        data._runStats.tIn  != null && h('span', { className: 'flow-node-stat-pill' }, `↑ ${(data._runStats.tIn || 0).toLocaleString()}`),
        data._runStats.tOut != null && h('span', { className: 'flow-node-stat-pill' }, `↓ ${(data._runStats.tOut || 0).toLocaleString()}`),
        data._runStats.rows != null && h('span', { className: 'flow-node-stat-pill' }, `${data._runStats.rows} rows`),
      ),
      // Executed LLM-backed extensions — each with its own cost. Surfaced after a
      // run so an LLM extension is a visible step, not an invisible cost folded
      // into the node total. Only LLM extensions populate this (non-LLM run free).
      Array.isArray(data._extensions) && data._extensions.length > 0 && h('div', {
        style: { display: 'flex', flexWrap: 'wrap', gap: '3px', margin: '3px 6px 0' },
      },
        ...data._extensions.map((ex, i) => h('span', {
          key: i,
          title: `Extension !${ex.name} (LLM) ran in this node · `
               + `${((ex.tokens_input || 0) + (ex.tokens_output || 0)).toLocaleString()} tokens`
               + `${ex.cost_usd ? ' · $' + Number(ex.cost_usd).toFixed(4) : ''}`
               + `${ex.success ? '' : ' · FAILED'}`,
          style: {
            fontSize: '0.55rem', fontWeight: 600, lineHeight: 1.4,
            padding: '0 4px', borderRadius: 3,
            display: 'inline-flex', alignItems: 'center', gap: '4px',
            color: ex.success ? 'var(--text-muted, #94a3b8)' : 'var(--error-color, #f87171)',
            border: '1px solid var(--border-secondary, rgba(148,163,184,0.25))',
            background: 'var(--hover-bg-strong, rgba(148,163,184,0.08))',
          },
        },
          `!${ex.name}`,
          ex.cost_usd ? h('span', { style: { opacity: 0.75 } }, `$${Number(ex.cost_usd).toFixed(4)}`) : null,
        )),
      ),
      data._execStatus === 'error' && h('button', {
        className: 'flow-node-fix-btn',
        onClick: e => {
          e.stopPropagation();
          UderiaBus.emit('uderia:flow-fix-node', { node_id: data.id, node: data });
        },
      },
        h('svg', { width: 10, height: 10, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2 },
          h('polygon', { points: '12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2' }),
        ),
        '✦ Fix with AI',
      ),
      h(Handle, { type: 'source', position: Position?.Bottom || 'bottom', style: { background: color } }),
      // ── Extension slot rail ────────────────────────────────────────────────
      h('div', {
        className: 'flow-ext-rail',
        ref: railRef,
        onMouseDown: e => e.stopPropagation(),
        onClick:     e => e.stopPropagation(),
      },
        // Filled slots — one per configured extension
        ...extensions.map((ext, i) => {
          const meta = _extVisual(ext.id);
          return h('div', {
            key: i,
            className: 'flow-ext-slot',
            style: { background: meta.color || '#64748b' },
            title: `${meta.label || ext.id}${ext.param ? ': ' + ext.param : ''}`,
            onClick: e => { e.stopPropagation(); setPickerSlot(i); },
          },
            h('span', null, meta.abbr || (ext.id[0] || '?').toUpperCase()),
            // Quick-remove × button (appears on hover via CSS)
            h('span', {
              className: 'flow-ext-slot-remove',
              onClick: e => { e.stopPropagation(); _dispatchSave(extensions.filter((_, j) => j !== i)); },
            }, '×'),
          );
        }),
        // Always-visible "+" add slot
        h('div', {
          className: 'flow-ext-slot-add',
          title: 'Add post-processing extension',
          onClick: e => { e.stopPropagation(); setPickerSlot(-1); },
        }, '+'),
        // Picker rendered via portal — see pickerPortal variable above
      ),
    ),
    pickerPortal,
    );
  }

  function DecisionNode({ data, selected }) { // — the exact visual bounding box of a 90×90 square
    // rotated 45°. Handles sit at 0%/50%/100% edges of this box, which aligns
    // them perfectly with the four visual tips of the diamond shape.
    const status = data._execStatus;
    return h('div', {
      className: 'flow-decision-wrap',
      style: { position: 'relative', width: 128, height: 128,
               display: 'flex', alignItems: 'center', justifyContent: 'center' },
    },
      // Status icon rendered in the OUTER (unrotated) container — inside the
      // rotated diamond it would be rotated 45° and land off the shape. CSS
      // (.flow-decision-wrap > .flow-node-status-icon) centres it near the top.
      _statusIcon(status),
      h(Handle, { type: 'target', position: Position?.Top    || 'top',    style: { background: '#db2777' } }),
      h('div', {
        className: `flow-node-diamond ${selected ? 'selected' : ''}${data._execStatus === 'running' ? ' flow-node--running' : ''}`,
        title: data.data?.condition,
      },
        h('div', { className: 'flow-node-diamond-inner' },
          h('div', { style: { fontSize: '0.68rem', fontWeight: 600, color: '#db2777' } }, data.label || 'Decision'),
        ),
      ),
      h(Handle, { id: 'true',  type: 'source', position: Position?.Right  || 'right',  style: { background: '#16a34a' } }),
      h(Handle, { id: 'false', type: 'source', position: Position?.Bottom || 'bottom', style: { background: '#dc2626' } }),
    );
  }

  // The loop editor: name, round limit, when the loop stops, how many earlier rounds each
  // round sees — and removing the loop. Builder scope so its local state survives
  // FlowApp re-renders; the server validates and answers in words when it refuses.
  function LoopEditor({ scope, members, onSave, onRemove, onClose }) {
    const [form, setForm] = useState(() => _loopFormFromScope(scope, members));
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState(null);
    const set = (k, v) => setForm(f => ({ ...f, [k]: v }));
    const setStop = (k, v) => setForm(f => ({ ...f, stop: { ...f.stop, [k]: v } }));
    const judges = members.filter(m => m.type === 'judge');
    const decider = form.stop.kind === 'custom' ? members : judges;
    async function save() {
      setBusy(true); setErr(null);
      const res = await onSave(_loopUpdatesFromForm(form));
      setBusy(false);
      if (res && res.error) setErr(res.error); else onClose();
    }
    const kindOpts = [['none', 'After the last round (no early stop)'],
                      ...(judges.length ? [['pass', 'When a judge passes the work'], ['threshold', 'When a judge passes with a score of at least…']] : []),
                      ['custom', 'When a custom condition holds']];
    return h('div', { className: 'flow-help-overlay', onClick: onClose },
      h('div', { className: 'flow-help-modal flow-loop-modal', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Edit loop',
                 onClick: e => e.stopPropagation(), onKeyDown: e => { if (e.key === 'Escape') { e.stopPropagation(); onClose(); } } },
        h('div', { className: 'flow-help-head' },
          h('span', { className: 'flow-help-title' }, 'Edit loop'),
          h('button', { className: 'flow-inspector-close', 'aria-label': 'Close', onClick: onClose }, '×')),
        h('div', { className: 'flow-loop-note', style: { marginBottom: 10 } },
          'Repeats: ', members.map(m => m.label).join(' → '), '.'),
        h('div', { className: 'flow-field' },
          h('label', { htmlFor: 'flow-loop-name' }, 'Name'),
          h('input', { id: 'flow-loop-name', value: form.name, onChange: e => set('name', e.target.value), autoFocus: true })),
        h('div', { className: 'flow-field' },
          h('label', { htmlFor: 'flow-loop-rounds' }, 'Up to how many rounds'),
          h('input', { id: 'flow-loop-rounds', type: 'number', min: SCOPE_MIN_ROUNDS, max: SCOPE_MAX_ROUNDS, value: form.rounds,
                       onChange: e => set('rounds', e.target.value) })),
        h('div', { className: 'flow-field' },
          h('label', { htmlFor: 'flow-loop-stop' }, 'Stop'),
          h('select', { id: 'flow-loop-stop', value: form.stop.kind, onChange: e => setStop('kind', e.target.value) },
            kindOpts.map(([v, l]) => h('option', { key: v, value: v }, l))),
          form.stop.kind !== 'none' && h('div', { className: 'flow-loop-row', style: { marginTop: 6 } },
            h('select', { 'aria-label': 'The step that decides', value: form.stop.node, onChange: e => setStop('node', e.target.value) },
              h('option', { value: '' }, 'Choose the step that decides…'),
              decider.map(m => h('option', { key: m.id, value: m.id }, m.label))),
            form.stop.kind === 'threshold' && h('input', { type: 'number', min: 0, max: 1, step: 0.05, 'aria-label': 'Minimum score',
              value: form.stop.threshold, style: { width: 80 }, onChange: e => setStop('threshold', e.target.value) })),
          form.stop.kind === 'custom' && h('input', { 'aria-label': 'Condition', style: { marginTop: 6 }, value: form.stop.expression,
            placeholder: 'passed == True and score >= 0.8', onChange: e => setStop('expression', e.target.value) }),
          h('div', { className: 'flow-loop-note' },
            form.stop.kind === 'none' ? 'The loop runs every round.'
            : form.stop.kind === 'custom' ? 'Read over what the chosen step returns (passed, score, verdict, or its own fields). A round with no verdict never stops the loop.'
            : 'A round the judge cannot rule on never stops the loop; it runs to the limit.')),
        h('div', { className: 'flow-field' },
          h('label', { htmlFor: 'flow-loop-hist' }, 'Each round sees'),
          h('select', { id: 'flow-loop-hist', value: form.history, onChange: e => set('history', e.target.value) },
            h('option', { value: 'all' }, 'Every earlier round'),
            [1, 2, 3, 5].map(n => h('option', { key: n, value: String(n) }, n === 1 ? 'Only the previous round' : `The last ${n} rounds`))),
          h('div', { className: 'flow-loop-note' }, 'Fewer rounds keep each prompt shorter and cheaper. A step after the loop always reads the whole transcript.')),
        err && h('div', { role: 'alert', className: 'flow-loop-note', style: { color: 'var(--status-error, #ef4444)' } }, err),
        h('div', { className: 'flow-loop-actions' },
          h('button', { type: 'button', className: 'flow-loop-btn flow-loop-btn--danger', disabled: busy,
                        title: 'The steps stay; the repeat and its frame go', onClick: async () => { setBusy(true); await onRemove(); setBusy(false); onClose(); } }, 'Remove loop'),
          h('div', { className: 'flow-loop-right' },
            h('button', { type: 'button', className: 'flow-loop-btn', onClick: onClose }, 'Cancel'),
            h('button', { type: 'button', className: 'flow-loop-btn flow-loop-btn--primary', disabled: busy, onClick: save }, busy ? 'Saving…' : 'Save'))),
      ));
  }

  function ScopeGroupNode({ data }) {
    // The loop frame: its name, how it ends, and — during a run — which round it is on
    // and how it ended (converged / ran out of rounds). Words, never a bare count.
    const max = data.max_iterations || 10;
    const exitTxt = data._exitLabel ? `stops when ${data._exitLabel} passes · up to ${max} rounds` : `${max} rounds`;
    const live = data._round ? `round ${data._round} of ${max}` : null;
    const liveNow = !!(data._round && !data._exitReason);
    const setRounds = n => UderiaBus.emit('uderia:flow-scope-rounds', { scope_id: data.id, max_iterations: Math.max(SCOPE_MIN_ROUNDS, Math.min(SCOPE_MAX_ROUNDS, n)) });
    const end = data._exitReason === 'converged' ? `passed in round ${data._roundsDone}`
              : data._exitReason === 'max_rounds' ? `no pass in ${data._roundsDone} rounds`
              : data._exitReason ? `stopped (${data._exitReason.replace('_', ' ')})` : null;
    return h('div', { className: `flow-scope-node${data._round && !data._exitReason ? ' is-live' : ''}`, style: { width: '100%', height: '100%' } },
      h('div', { className: 'flow-scope-label' },
        h('svg', { width: 11, height: 11, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2.2, strokeLinecap: 'round', style: { marginRight: 4, verticalAlign: '-1px' } },
          h('path', { d: 'M3 12a9 9 0 0 1 15.5-6.3L21 8M21 3v5h-5M21 12a9 9 0 0 1-15.5 6.3L3 16M3 21v-5h5' })),
        data.name || 'Loop',
        h('button', { type: 'button', className: 'flow-scope-edit nodrag nopan', disabled: liveNow,
                      title: liveNow ? 'The loop is running — edit it when the run ends' : 'Edit this loop: name, rounds, when it stops, how much history each round sees',
                      'aria-label': `Edit loop ${data.name || ''}`.trim(),
                      onClick: e => { e.stopPropagation(); UderiaBus.emit('uderia:flow-scope-edit', { scope_id: data.id }); } },
          h('svg', { width: 10, height: 10, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2.2, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': 'true' },
            h('path', { d: 'M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z' })),
          'Edit')),
      h('div', { className: `flow-scope-iter-badge${end ? (data._exitReason === 'converged' ? ' is-pass' : ' is-warn') : ''}`,
                 title: exitTxt }, end || live || exitTxt),
      // The round limit, editable in place — never while the loop is running.
      h('div', { className: 'flow-scope-rounds nodrag nopan', title: 'The most rounds this loop may run' },
        h('button', { type: 'button', className: 'flow-scope-step', disabled: liveNow || max <= SCOPE_MIN_ROUNDS,
                      'aria-label': `Fewer rounds (now ${max})`, onClick: e => { e.stopPropagation(); setRounds(max - 1); } }, '\u2212'),
        h('span', { className: 'flow-scope-rounds-val', 'aria-live': 'polite' }, `up to ${max} round${max === 1 ? '' : 's'}`),
        h('button', { type: 'button', className: 'flow-scope-step', disabled: liveNow || max >= SCOPE_MAX_ROUNDS,
                      'aria-label': `More rounds (now ${max})`, onClick: e => { e.stopPropagation(); setRounds(max + 1); } }, '+'),
      ),
    );
  }

  // A judge decides a round: gold, a gavel-like mark, its slot state and — after a run —
  // its verdict in words (pass / fail / no verdict). An agent judge shows its agent's tag.
  function JudgeNode({ data, selected }) {
    const color = NODE_COLORS.judge;
    const status = data._execStatus;
    const subtitle = _nodeSubtitle(data);
    const ifocColor = data._profileType ? IFOC[data._profileType]?.color : null;
    const v = data._verdict;
    const vTxt = !v ? null : v.passed === true ? `pass${v.score != null ? ' · ' + Number(v.score).toFixed(2) : ''}`
               : v.passed === false ? `fail${v.score != null ? ' · ' + Number(v.score).toFixed(2) : ''}` : 'no verdict';
    return h('div', {
      className: `flow-node flow-node--judge ${selected ? 'selected' : ''}${status === 'running' ? ' flow-node--running' : ''}${data._slotOpen ? ' flow-node--slot-open' : ''}`,
      style: { borderColor: data._slotOpen ? undefined : color },
    },
      _statusIcon(status),
      h(Handle, { type: 'target', position: Position?.Top || 'top', style: { background: color } }),
      h('div', { className: 'flow-node-header' },
        h('svg', { width: 12, height: 12, viewBox: '0 0 24 24', fill: 'none', stroke: color, strokeWidth: 2.2, strokeLinecap: 'round', strokeLinejoin: 'round', style: { flexShrink: 0 } },
          h('path', { d: 'M12 3v18M5 7h14M5 7l-3 7a3 3 0 0 0 6 0zM19 7l-3 7a3 3 0 0 0 6 0zM8 21h8' })),
        h('div', { className: 'flow-node-label' }, data.label || 'Judge'),
        data._profileTag && !data._slotOpen && h('span', {
          className: 'flow-node-profile-tag', title: `Judged by @${data._profileTag}`,
          // Same pill as an LLM node's agent tag, clear of the status tick in the corner.
          style: { marginLeft: 'auto', marginRight: '16px', flexShrink: 0, fontSize: '0.6rem', fontWeight: 700, lineHeight: 1.4,
                   padding: '1px 8px', borderRadius: 999, color: ifocColor || 'var(--text-muted)',
                   border: `1px solid ${ifocColor || 'var(--border-secondary, rgba(255,255,255,0.15))'}`,
                   background: ifocColor ? `${ifocColor}1a` : 'transparent' },
        }, `@${data._profileTag}`),
        data._slotOpen && data._slot && h('div', { className: 'flow-node-slot-badge', title: 'Open slot — choose an agent to run this role.' }, `slot · ${data._slot.role}`),
        !data._slotOpen && data._slotJudgePanel && h('div', {
          className: 'flow-node-slot-badge is-judge', title: 'Graded by the eval judge panel (not a profile).',
        }, 'judge panel'),
      ),
      subtitle && h('div', { className: 'flow-node-subtitle' }, subtitle),
      vTxt && h('div', { className: `flow-node-verdict ${v.passed === true ? 'is-pass' : v.passed === false ? 'is-fail' : 'is-none'}`,
                         title: 'The verdict of the latest round' }, vTxt),
      h(Handle, { type: 'source', position: Position?.Bottom || 'bottom', style: { background: color } }),
    );
  }

  // An artefact writes each round's code into ONE canvas; after a run it names the
  // version and opens the canvas.
  function ArtefactNode({ data, selected }) {
    const color = NODE_COLORS.artefact;
    const status = data._execStatus;
    return h('div', {
      className: `flow-node ${selected ? 'selected' : ''}${status === 'running' ? ' flow-node--running' : ''}`,
      style: { borderColor: color },
    },
      _statusIcon(status),
      h(Handle, { type: 'target', position: Position?.Top || 'top', style: { background: color } }),
      h('div', { className: 'flow-node-header' },
        h('svg', { width: 12, height: 12, viewBox: '0 0 24 24', fill: 'none', stroke: color, strokeWidth: 2.2, strokeLinecap: 'round', strokeLinejoin: 'round', style: { flexShrink: 0 } },
          h('path', { d: 'M8 9l-3 3 3 3M16 9l3 3-3 3M13 6l-2 12' })),
        h('div', { className: 'flow-node-label' }, data.label || 'Canvas'),
      ),
      h('div', { className: 'flow-node-subtitle' }, _nodeSubtitle(data)),
      data._canvasId && h('button', {
        className: 'flow-node-canvas-btn', type: 'button',
        title: 'Open this run\u2019s canvas (one version per round)',
        onClick: e => { e.stopPropagation(); UderiaBus.emit('uderia:flow-open-canvas', { canvas_id: data._canvasId }); },
      }, `Open canvas · v${data._canvasVersion || 1}`),
      h(Handle, { type: 'source', position: Position?.Bottom || 'bottom', style: { background: color } }),
    );
  }

  // A File node reads a kept file into the flow, or keeps its input as one; after a run it
  // names the file and opens it in Files.
  function FileNode({ data, selected }) {
    const type = data._type || 'file_input';
    const color = NODE_COLORS[type] || NODE_COLORS.file_input;
    const status = data._execStatus;
    const def = ADDABLE_NODES.find(n => n.type === type);
    return h('div', {
      className: `flow-node ${selected ? 'selected' : ''}${status === 'running' ? ' flow-node--running' : ''}`,
      style: { borderColor: color },
    },
      _statusIcon(status),
      h(Handle, { type: 'target', position: Position?.Top || 'top', style: { background: color } }),
      h('div', { className: 'flow-node-header' },
        h('svg', { width: 12, height: 12, viewBox: '0 0 24 24', fill: 'none', stroke: color, strokeWidth: 2.2, strokeLinecap: 'round', strokeLinejoin: 'round', style: { flexShrink: 0 } },
          ...((def && def.icon) || []).map(([tag, attrs], i) => h(tag, { key: i, ...attrs }))),
        h('div', { className: 'flow-node-label' }, data.label || _nodeLabel(type)),
      ),
      h('div', { className: 'flow-node-subtitle' }, _nodeSubtitle(data)),
      data._fileId && h('button', {
        className: 'flow-node-canvas-btn', type: 'button',
        title: type === 'file_output' ? 'The file this run kept — open it in Files' : 'The file this run read — open it in Files',
        onClick: e => { e.stopPropagation(); UderiaBus.emit('uderia:flow-open-file', { file_id: data._fileId }); },
      }, `Open in Files${data._fileName ? ' · ' + data._fileName.slice(0, 22) : ''}`),
      h(Handle, { type: 'source', position: Position?.Bottom || 'bottom', style: { background: color } }),
    );
  }

  // ── Custom ghost connection line (animated purple dashes while dragging) ──────
  function FlowConnectionLine({ fromX, fromY, toX, toY }) {
    const midY = (fromY + toY) / 2;
    const d = `M${fromX},${fromY} C${fromX},${midY} ${toX},${midY} ${toX},${toY}`;
    return h('g', null,
      h('defs', null,
        h('filter', { id: 'flow-conn-glow', x: '-40%', y: '-40%', width: '180%', height: '180%' },
          h('feGaussianBlur', { stdDeviation: 2.5, result: 'blur' }),
          h('feMerge', null,
            h('feMergeNode', { in: 'blur' }),
            h('feMergeNode', { in: 'SourceGraphic' }),
          ),
        ),
        h('marker', { id: 'flow-conn-arw', markerWidth: 8, markerHeight: 7, refX: 8, refY: 3.5, orient: 'auto' },
          h('polygon', { points: '0 0, 8 3.5, 0 7', fill: '#9333ea' }),
        ),
      ),
      h('path', {
        d,
        fill: 'none',
        stroke: '#9333ea',
        strokeWidth: 2,
        strokeDasharray: '8 4',
        strokeOpacity: 0.85,
        markerEnd: 'url(#flow-conn-arw)',
        filter: 'url(#flow-conn-glow)',
        style: { animation: 'flow-dash-anim 0.4s linear infinite' },
      }),
    );
  }

  // ── Loop-back edge (PROP-007 increment 3) ──────────────────────────────────
  // A loop's back edge runs from its last node up to its first, which sits directly
  // ABOVE it — a smoothstep between two vertically aligned nodes collapses onto the
  // straight forward path and stops reading as a loop. This one leaves the bottom of
  // the source, runs up a lane to the right inside the loop frame, and enters the
  // target from above — the "and round again" drawn where the eye expects it.
  const LOOP_LANE_OFFSET = 112;   // half a node (~88) + clearance; inside the frame padding
  function _loopBackPath(sx, sy, tx, ty, lane) {
    const rx = Math.max(sx, tx) + (lane || LOOP_LANE_OFFSET);
    const by = sy + 14, ay = ty - 14, r = 8;
    return [
      `M ${sx} ${sy}`, `L ${sx} ${by - r}`, `Q ${sx} ${by} ${sx + r} ${by}`,
      `L ${rx - r} ${by}`, `Q ${rx} ${by} ${rx} ${by - r}`,
      `L ${rx} ${ay + r}`, `Q ${rx} ${ay} ${rx - r} ${ay}`,
      `L ${tx + r} ${ay}`, `Q ${tx} ${ay} ${tx} ${ay + r}`, `L ${tx} ${ty}`,
    ].join(' ');
  }
  function LoopBackEdge({ id, sourceX, sourceY, targetX, targetY, style, markerEnd }) {
    const d = _loopBackPath(sourceX, sourceY, targetX, targetY);
    return h('g', { className: 'flow-loopback-edge' },
      h('path', { d, fill: 'none', stroke: 'transparent', strokeWidth: 14, className: 'react-flow__edge-interaction' }),
      h('path', { id, d, fill: 'none', className: 'react-flow__edge-path', style, markerEnd }),
    );
  }
  const _staticEdgeTypes = { loopback: LoopBackEdge };

  // nodeTypes is defined as a stable constant — DO NOT compute here.
  // It is defined inside FlowApp with useMemo so React's hook rules are satisfied.
  const _staticNodeTypes = {
    start:         OvalNode,
    end:           OvalNode,
    task_llm:      LLMNode,
    task_prompt:   StandardNode,
    task_tool:     StandardNode,
    task_action:   StandardNode,
    task_function: StandardNode,
    decision:      DecisionNode,
    merge:         StandardNode,
    scope:         ScopeGroupNode,
    judge:         JudgeNode,
    artefact:      ArtefactNode,
    file_input:    FileNode,
    file_output:   FileNode,
  };

  // ── Inspector panel component ─────────────────────────────────────────────

  // Category dot colours — mirrors getCategoryColor() in configurationHandler.js
  // so the flow tool picker matches the Classification Results dialog.
  function _categoryColor(category) {
    const colors = {
      'Performance': '#3b82f6', 'Data Quality': '#10b981', 'Data Visualization': '#8b5cf6',
      'Database Information': '#06b6d4', 'Database Administration': '#f59e0b',
      'Table Management': '#f59e0b', 'Security': '#ef4444', 'User Management': '#ec4899',
      'Utilities': '#6b7280', 'Reporting': '#14b8a6', 'Archiving': '#a855f7',
      'Components': '#0d9488', 'Built-in': '#64748b', 'General': '#9ca3af',
    };
    return colors[category] || '#9ca3af';
  }

  // Categorized tool picker modal — same visual language as the Classification
  // Results dialog. Click a card to select; hover for the tool's description.
  function ToolPicker({ toolList, value, onSelect, onClose, loading = false, kind = 'tool' }) {
    const noun = kind === 'prompt' ? 'prompt' : 'tool';
    const [q, setQ] = useState('');
    const ql = q.trim().toLowerCase();
    const filtered = (toolList || []).filter(t => !ql
      || (t.name && t.name.toLowerCase().includes(ql))
      || (t.description && t.description.toLowerCase().includes(ql))
      || (t.category && t.category.toLowerCase().includes(ql)));
    const groups = {};
    for (const t of filtered) {
      const c = t.category || 'General';
      (groups[c] = groups[c] || []).push(t);
    }
    const cats = Object.keys(groups).sort();

    return h('div', {
      style: { position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,0.55)',
               display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 },
      onClick: onClose,
    },
      h('div', {
        onClick: e => e.stopPropagation(),
        style: { width: 'min(860px, 92vw)', maxHeight: '86vh', display: 'flex', flexDirection: 'column',
                 background: 'var(--bg-primary, #0f172a)', border: '1px solid var(--border-primary)',
                 borderRadius: 14, overflow: 'hidden', boxShadow: '0 20px 60px rgba(0,0,0,0.5)' },
      },
        h('div', { style: { padding: '16px 20px', borderBottom: '1px solid var(--border-secondary)', display: 'flex', alignItems: 'center', gap: 12 } },
          h('div', { style: { flex: 1 } },
            h('div', { style: { fontSize: '1rem', fontWeight: 700, color: 'var(--text-primary)' } }, kind === 'prompt' ? 'Select a prompt' : 'Select a tool'),
            h('div', { style: { fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: 2 } },
              loading ? 'Loading…' : `${cats.length} categories • ${filtered.length} ${noun}s — click to select, hover for the description`),
          ),
          h('button', { onClick: onClose, title: 'Close',
            style: { background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', fontSize: '1.4rem', lineHeight: 1, padding: 4 } }, '×'),
        ),
        h('div', { style: { padding: '10px 20px', borderBottom: '1px solid var(--border-secondary)' } },
          h('input', { autoFocus: true, value: q, onChange: e => setQ(e.target.value), placeholder: `Search ${noun}s by name, description, or category…`,
            style: { width: '100%', boxSizing: 'border-box', padding: '7px 10px', borderRadius: 8,
                     background: 'var(--card-bg)', color: 'var(--text-primary)', border: '1px solid var(--border-primary)', fontSize: '0.8rem' } }),
        ),
        h('div', { style: { padding: 16, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 14 } },
          loading && h('div', { style: { color: 'var(--text-muted)', fontSize: '0.8rem', textAlign: 'center', padding: 24 } }, 'Loading tools…'),
          !loading && filtered.length === 0 && h('div', { style: { color: 'var(--text-muted)', fontSize: '0.8rem', textAlign: 'center', padding: 24 } }, `No ${noun}s available for this profile — pick a profile with an MCP server, or type a name below.`),
          ...cats.map(cat => {
            const color = _categoryColor(cat);
            const items = groups[cat];
            return h('div', { key: cat, style: { borderRadius: 10, padding: 14, background: 'var(--card-bg)', border: '1px solid var(--border-secondary)' } },
              h('div', { style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 } },
                h('div', { style: { display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.8rem', fontWeight: 600, color } },
                  h('span', { style: { width: 8, height: 8, borderRadius: '50%', background: color, display: 'inline-block' } }),
                  cat),
                h('span', { style: { fontSize: '0.7rem', color: 'var(--text-muted)' } }, `${items.length} total`),
              ),
              h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 8 } },
                ...items.map(t => {
                  const selected = t.name === value;
                  return h('div', {
                    key: t.name,
                    title: t.description || t.name,
                    onClick: () => { onSelect(t.name); onClose(); },
                    style: {
                      fontFamily: 'monospace', fontSize: '0.75rem', padding: '7px 10px', borderRadius: 6, cursor: 'pointer',
                      color: 'var(--text-primary)',
                      background: selected ? 'rgba(13,148,136,0.18)' : 'rgba(255,255,255,0.03)',
                      border: `1px solid ${selected ? 'rgba(13,148,136,0.7)' : 'var(--border-secondary)'}`,
                      whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', transition: 'background 0.1s',
                    },
                    onMouseEnter: e => { if (!selected) e.currentTarget.style.background = 'var(--hover-bg-strong, rgba(255,255,255,0.07))'; },
                    onMouseLeave: e => { if (!selected) e.currentTarget.style.background = 'rgba(255,255,255,0.03)'; },
                  }, t.name);
                }),
              ),
            );
          }),
        ),
        h('div', { style: { padding: '10px 20px', borderTop: '1px solid var(--border-secondary)', display: 'flex', alignItems: 'center', gap: 8 } },
          h('span', { style: { fontSize: '0.7rem', color: 'var(--text-muted)', flexShrink: 0 } }, `Or type a custom ${noun} name:`),
          h('input', { placeholder: `${noun}_name + Enter`,
            onKeyDown: e => { if (e.key === 'Enter' && e.target.value.trim()) { onSelect(e.target.value.trim()); onClose(); } },
            style: { flex: 1, padding: '5px 8px', borderRadius: 6, background: 'var(--card-bg)', color: 'var(--text-primary)', border: '1px solid var(--border-primary)', fontSize: '0.74rem', fontFamily: 'monospace' } }),
        ),
      ),
    );
  }

  function Inspector({ node, onClose, onSave, profiles, toolList = [], scopeItems = [], nodeData: runData = null, onRunNode, defaultProfileId = '' }) {
    const data    = node.data || {};
    const nodeData = data.data || {};
    // When a node has no profile of its own, it inherits the flow's default
    // profile at run time — so the blank option should say so, not look unset.
    const _defProf = defaultProfileId ? (profiles || []).find(p => p.id === defaultProfileId) : null;
    const _inheritLabel = _defProf
      ? `— inherit flow default: ${_defProf.tag || _defProf.name} —`
      : '— select profile —';
    // Hint listing names in scope for this node's prompt/expression/condition.
    const scopeHint = scopeItems.length > 0 && h('div', { className: 'flow-param-scope' },
      'In scope: ', ...scopeItems.map((s, i) => h('code', { key: i }, (s.name === 'output' ? '' : '$') + s.name)));
    const [label,   setLabel]   = useState(data.label   || '');
    const [prompt,  setPrompt]  = useState(nodeData.prompt  || '');
    const [rubric,  setRubric]  = useState(nodeData.rubric  || '');
    const [profile, setProfile] = useState(nodeData.profile_id || '');
    const _slotRow = (nodeData.slot && typeof nodeData.slot === 'object') ? nodeData.slot : null;
    // Default to 'in_session' when unset: the executor treats an unset session_context
    // (compact mode) as "run in the caller's session", so the dropdown must show that —
    // defaulting to 'new' contradicted the node's actual behaviour.
    const [sessCtx, setSessCtx] = useState(nodeData.session_context || 'in_session');
    const [expr,    setExpr]    = useState(nodeData.expression || '');
    const [cond,    setCond]    = useState(nodeData.condition  || '');
    const [outAs,   setOutAs]   = useState(nodeData.output_as  || '');
    const [toolName, setToolName] = useState(nodeData.tool_name || '');
    const [toolArgs, setToolArgs] = useState(() => {
      try { return JSON.stringify(nodeData.arguments || {}, null, 2); } catch (_) { return '{}'; }
    });
    const [toolArgsErr, setToolArgsErr] = useState('');
    const [actName, setActName] = useState(nodeData.action || '');
    const [actInv, setActInv] = useState(() => {
      try { return JSON.stringify(nodeData.invocation || { key: '', values: {} }, null, 2); } catch (_) { return '{}'; }
    });
    const [actInvErr, setActInvErr] = useState('');
    const [actChoices, setActChoices] = useState(null);   // null = loading
    // PROP-010: a File input reads one kept file; a File output keeps its input as one
    const [fileSel, setFileSel] = useState(() => ({ id: nodeData.file_id || '', name: nodeData.filename || '' }));
    const [fileSource, setFileSource] = useState(nodeData.source || 'output');
    const [fileFormat, setFileFormat] = useState(nodeData.format || 'auto');
    const [fileName, setFileName] = useState(nodeData.filename || '');
    const _actAgent = profile || defaultProfileId || '';
    useEffect(() => {
      if ((data._type || node.type) !== 'task_action') return;
      if (!_actAgent) { setActChoices([]); return; }
      setActChoices(null);
      fetch(`/api/v1/actions?profile_id=${encodeURIComponent(_actAgent)}`,
            { headers: { Authorization: `Bearer ${localStorage.getItem('tda_auth_token')}` } })
        .then(r => r.ok ? r.json() : null)
        .then(d => setActChoices(_grantedActions(d && d.actions)))
        .catch(() => setActChoices([]));
    }, [_actAgent]);
    const [promptName, setPromptName] = useState(nodeData.prompt_name || '');
    const [promptArgs, setPromptArgs] = useState(() => {
      try { return JSON.stringify(nodeData.prompt_arguments || {}, null, 2); } catch (_) { return '{}'; }
    });
    const [promptArgsErr, setPromptArgsErr] = useState('');
    const [pickerOpen, setPickerOpen] = useState(false);
    const [pickerItems, setPickerItems] = useState(null);   // null = loading
    function openPicker(kind) {
      // Tools/prompts are profile-scoped: fetch for the node's effective profile
      // (its own override, else the flow default) so only that profile's MCP
      // server tools/prompts show.
      const eff = (profile || defaultProfileId || '').trim();
      setPickerItems(null);
      setPickerOpen(true);
      const url = eff ? `/api/v1/flows/tools?profile_id=${encodeURIComponent(eff)}` : '/api/v1/flows/tools';
      fetch(url, { headers: { Authorization: `Bearer ${localStorage.getItem('tda_auth_token')}` } })
        .then(r => r.ok ? r.json() : null)
        .then(d => setPickerItems((d && (kind === 'prompt' ? d.prompts : d.tools)) || []))
        .catch(() => setPickerItems([]));
    }
    const type = data._type || node.type;
    // Nodes that produce a referenceable result
    const producesOutput = type === 'task_llm' || type === 'task_prompt' || type === 'task_tool' || type === 'task_action' || type === 'task_function' || type === 'decision' || type === 'judge' || type === 'file_input';

    function handleSave() {
      const updates = { label };
      const outputAs = (outAs || '').trim();
      if (type === 'task_llm')      updates.data = { prompt, profile_id: profile, session_context: sessCtx, extensions: nodeData.extensions || [], output_as: outputAs };
      if (type === 'task_tool') {
        let args;
        try { args = JSON.parse(toolArgs || '{}'); }
        catch (err) { setToolArgsErr('Invalid JSON: ' + err.message); return; }
        if (typeof args !== 'object' || args === null || Array.isArray(args)) {
          setToolArgsErr('Arguments must be a JSON object, e.g. { "key": "value" }'); return;
        }
        updates.data = { tool_name: (toolName || '').trim(), arguments: args, profile_id: profile, output_as: outputAs };
      }
      if (type === 'task_prompt') {
        let args;
        try { args = JSON.parse(promptArgs || '{}'); }
        catch (err) { setPromptArgsErr('Invalid JSON: ' + err.message); return; }
        if (typeof args !== 'object' || args === null || Array.isArray(args)) {
          setPromptArgsErr('Arguments must be a JSON object, e.g. { "database_name": "mydb" }'); return;
        }
        updates.data = { prompt_name: (promptName || '').trim(), prompt_arguments: args, profile_id: profile, session_context: sessCtx, output_as: outputAs };
      }
      if (type === 'judge')         updates.data = { prompt, rubric, profile_id: profile, output_as: outputAs };
      if (type === 'task_action') {
        const inv = _parseInvocation(actInv);
        if (inv.error) { setActInvErr(inv.error); return; }
        updates.data = { action: (actName || '').trim(), invocation: inv.value, profile_id: profile, output_as: outputAs };
      }
      if (type === 'task_function') updates.data = { expression: expr, output_as: outputAs };
      if (type === 'file_input')    updates.data = { file_id: fileSel.id, filename: fileSel.name, output_as: outputAs };
      if (type === 'file_output')   updates.data = { source: (fileSource || 'output').trim(), format: fileFormat, filename: (fileName || '').trim(), profile_id: profile };
      if (type === 'decision')      updates.data = { condition: cond, output_as: outputAs };
      onSave(node.id, updates);
    }

    // The agent field — ONE element for every node that runs under an agent (an LLM
    // node, a judge): the shared, filterable agent picker; a slot offers only what it
    // accepts (plus the judge panel where accepted) and never an inherit option.
    const agentField = (type === 'task_llm' || type === 'judge' || type === 'task_action' || type === 'file_output') ? (
          h('div', { className: 'flow-field' },
            h('label', null, _slotRow ? 'Agent for this slot' : 'Profile ID'),
            // PROP-007: a slot shows its role + hint, offers only the classes it accepts
            // (plus the judge panel where accepted) and has NO "inherit" option — an
            // unfilled slot is open, never a fallback to the flow default.
            _slotRow && h('div', { className: 'flow-slot-hint', 'data-slot-role': _slotRow.role },
              h('b', null, _slotRow.role), _slotRow.hint ? ` — ${_slotRow.hint}` : ''),
            // The agent field opens the platform's shared agent picker (search + IFOC class
            // pills) — the same one as the Anatomy star; FlowSelect is the fallback.
            profiles.length > 0 && _sharedAgentPicker()
              ? (() => {
                  const cur = profile === 'judge_panel' ? null : profiles.find(p => p.id === profile);
                  const col = cur ? ((IFOC[cur.profile_type] || {}).color || '#94a3b8') : null;
                  return h('button', {
                    type: 'button', className: 'flow-agent-trigger', 'aria-haspopup': 'listbox',
                    title: 'Choose an agent — search and filter by class',
                    onClick: (e) => {
                      const host = e.currentTarget.closest('.sp-panel') || e.currentTarget.closest('.flow-root') || e.currentTarget.parentElement;
                      _openSharedAgentPicker(host, {
                        items: _agentPickerItems(profiles, _slotRow ? _slotRow.accepts : null),
                        currentId: profile || null,
                        emptyLabel: _slotRow ? 'Leave this slot open' : _inheritLabel.replace(/^—\s*|\s*—$/g, ''),
                        onPick: (id) => setProfile(id),
                      });
                    },
                    style: { width: '100%', display: 'flex', alignItems: 'center', gap: 8, padding: '7px 10px', borderRadius: 6,
                             cursor: 'pointer', textAlign: 'left', background: 'var(--card-bg)',
                             color: profile ? 'var(--text-primary)' : 'var(--text-muted)', border: '1px solid var(--border-primary)' },
                  },
                    profile === 'judge_panel' ? h('span', null, 'Eval judge panel (not a profile)')
                    : cur ? h(R.Fragment, null,
                        h('span', { style: { color: col, background: `${col}1a`, border: `1px solid ${col}4d`, borderRadius: 4, padding: '0 5px',
                                             fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontWeight: 600, fontSize: '0.72rem' } }, `@${cur.tag || cur.name}`),
                        h('span', { style: { color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: '0.74rem' } }, cur.name || ''))
                    : h('span', null, _slotRow ? '— choose an agent for this slot —' : _inheritLabel),
                    h('svg', { width: 10, height: 10, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2.4, strokeLinecap: 'round', style: { marginLeft: 'auto', flexShrink: 0 } },
                      h('path', { d: 'M6 9l6 6 6-6' })));
                })()
              : profiles.length > 0
              ? h(FlowSelect, {
                  value: profile,
                  onChange: setProfile,
                  placeholder: _slotRow ? '— choose an agent for this slot —' : _inheritLabel,
                  options: [
                    { value: '', label: _slotRow ? '— choose an agent for this slot —' : _inheritLabel },
                    ...(_slotRow && (_slotRow.accepts || []).includes('judge_panel')
                        ? [{ value: 'judge_panel', label: 'Eval judge panel (not a profile)' }] : []),
                    ...profiles
                      .filter(p => !_slotRow || !(_slotRow.accepts || []).length || (_slotRow.accepts || []).includes(p.profile_type))
                      .map(p => ({ value: p.id, label: `${p.tag || p.name} (${IFOC[p.profile_type]?.label || p.profile_type || 'profile'})`, color: IFOC[p.profile_type]?.color })),
                  ],
                })
              : h('input', { value: profile, onChange: e => setProfile(e.target.value),
                             placeholder: _slotRow ? 'profile-id for this slot' : 'profile-id or leave blank for default' }),
          )
    ) : null;

    return h('div', { style: { display: 'flex', flexDirection: 'column', height: '100%' } },
      h('div', { className: 'flow-inspector-header' },
        h('span', { className: 'flow-inspector-title' }, `Edit: ${data.label || type}`),
        h('button', { className: 'flow-inspector-close', onClick: onClose }, '×'),
      ),
      h('div', { className: 'flow-inspector-body' },
        h('div', { className: 'flow-field' },
          h('label', null, 'Label'),
          h('input', { value: label, onChange: e => setLabel(e.target.value) }),
        ),
        type === 'task_llm' && h(R.Fragment, null,
          h('div', { className: 'flow-field' },
            h('label', null, 'Prompt template'),
            h(SmartTextField, {
              value: prompt,
              onChange: setPrompt,
              scopeItems,
              placeholder: 'Use {variable} or $variable for dynamic inputs',
              rows: 5,
            }),
          ),
          agentField,
          h('div', { className: 'flow-field' },
            h('label', null, 'Session context'),
            h(FlowSelect, {
              value: sessCtx,
              onChange: setSessCtx,
              options: [
                { value: 'in_session', label: 'In session (result in chat)' },
                { value: 'new',        label: 'New session (own sidebar entry)' },
              ],
            }),
          ),
        ),
        type === 'judge' && h(R.Fragment, null,
          h('div', { className: 'flow-field' },
            h('label', null, 'Criteria — what passes'),
            h('textarea', { value: rubric, rows: 3, onChange: e => setRubric(e.target.value),
                            placeholder: 'e.g. Does the draft fully and correctly answer the task?' }),
          ),
          h('div', { className: 'flow-field' },
            h('label', null, 'What it reviews'),
            h(SmartTextField, { value: prompt, onChange: setPrompt, scopeItems, rows: 4,
                                placeholder: 'e.g. TASK: {task}  DRAFT: {draft}' }),
          ),
          agentField,
          h('div', { className: 'flow-param-scope' },
            'Its verdict ends the loop when it passes. An agent judge ends its reply with VERDICT: PASS or FAIL; '
            + 'the eval judge panel grades against the criteria. No verdict is never read as a pass.'),
        ),
        type === 'file_input' && h(R.Fragment, null,
          h('div', { className: 'flow-field' },
            h('label', null, 'File'),
            h('div', { style: { display: 'flex', gap: 8, alignItems: 'center' } },
              h('div', { style: { flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                                  color: fileSel.id ? 'var(--text-primary)' : 'var(--text-muted)', fontSize: '0.8rem' } },
                fileSel.name || 'No file chosen'),
              h('button', {
                type: 'button', className: 'card-btn card-btn--neutral card-btn--sm',
                onClick: () => window.FilesKeep && window.FilesKeep.pick({
                  title: 'Choose a file for this node', actionLabel: 'Choose', single: true,
                  note: 'The node reads the file each time the flow runs. A table becomes rows; a document its text.',
                  kinds: [['all', 'All'], ['table', 'Tables'], ['document', 'Documents'], ['export', 'Exports'], ['upload', 'Uploads']],
                  accept: _fileInputAccepts,
                  choose: (picked) => { const f = picked && picked[0]; if (f) setFileSel({ id: f.id, name: f.filename }); },
                }),
              }, fileSel.id ? 'Change…' : 'Choose…'),
            ),
          ),
          h('div', { className: 'flow-param-scope' },
            'A table (CSV, TSV, XLSX) arrives as rows — later nodes read a column as $name, as after a query. '
            + 'JSON arrives as its value; a document or text file as its text.'),
        ),
        type === 'file_output' && h(R.Fragment, null,
          h('div', { className: 'flow-field' },
            h('label', null, 'What to keep'),
            h('input', { value: fileSource, onChange: e => setFileSource(e.target.value), placeholder: 'output',
                         'aria-label': 'The value to keep', spellCheck: false }),
          ),
          h('div', { className: 'flow-field' },
            h('label', null, 'Format'),
            h(FlowSelect, {
              value: fileFormat, onChange: setFileFormat,
              options: [
                { value: 'auto', label: 'Automatic — rows as CSV, text as Markdown, an object as JSON' },
                { value: 'csv',  label: 'CSV' },
                { value: 'md',   label: 'Markdown' },
                { value: 'json', label: 'JSON' },
                { value: 'txt',  label: 'Plain text' },
              ],
            }),
          ),
          h('div', { className: 'flow-field' },
            h('label', null, 'File name'),
            h('input', { value: fileName, onChange: e => setFileName(e.target.value), spellCheck: false,
                         placeholder: `${data.label || 'flow output'} {date}`, 'aria-label': 'File name' }),
          ),
          agentField,
          h('div', { className: 'flow-param-scope' },
            `Keeps {${fileSource || 'output'}} in Files on every run; the file names this flow, the run and this node. `
            + 'Use $name or {name} from earlier nodes, and {date}, {time} or {run}, in the name. The input passes on unchanged. '
            + 'The agent decides which store the file lands in (else the flow\u2019s).'),
        ),
        type === 'artefact' && h('div', { className: 'flow-param-scope', style: { padding: '0 2px' } },
          `Writes the code from {${nodeData.source || 'output'}} into ONE canvas per run — each round records a new version, `
          + 'so the rounds can be compared in the canvas history.'),
        type === 'task_action' && h(R.Fragment, null,
          h('div', { className: 'flow-field' },
            h('label', null, 'Action'),
            actChoices === null
              ? h('div', { className: 'flow-param-scope' }, 'Loading the actions this agent is granted…')
              : (actChoices.length === 0
                  ? h('div', { className: 'flow-param-scope', 'data-act-empty': '1' },
                      _actAgent ? 'This agent is granted no action. Grant one from the Actions panel of its knowledge graph.'
                                : 'Choose the agent that performs the action first.')
                  : h('select', { value: actName, onChange: e => setActName(e.target.value),
                                  'aria-label': 'Action the agent performs' },
                      h('option', { value: '' }, 'Select an action…'),
                      ...actChoices.map(a => h('option', { key: a.name, value: a.name },
                        `${a.name}${a.entity ? ' — ' + a.entity : ''}${a.trust && a.trust !== 'trusted' ? ' (' + a.trust + ')' : ''}`)))),
          ),
          (() => {
            const a = (actChoices || []).find(x => x.name === actName);
            return a && h('div', { className: 'flow-param-scope', 'data-act-posture': a.posture || '' },
              `Unattended: ${_postureWords(a.posture)}. Key: ${a.key || '—'}; writable: ${(a.writable || []).join(', ') || '—'}.`);
          })(),
          h('div', { className: 'flow-field' },
            h('label', null, 'Invocation (JSON)'),
            h('textarea', {
              value: actInv, rows: 5, spellCheck: false, 'aria-label': 'Invocation',
              onChange: e => { setActInv(e.target.value); setActInvErr(''); },
              style: { fontFamily: 'monospace', fontSize: '0.75rem' },
            }),
            actInvErr && h('div', { role: 'alert', style: { color: 'var(--trust-danger, #f87171)', fontSize: '0.7rem', marginTop: 4 } }, actInvErr),
            h('div', { className: 'flow-param-scope' }, 'Reference an earlier value with $name, e.g. { "key": "$customer_id", "values": { "Phone": "$phone" } }.'),
          ),
        ),
        type === 'task_tool' && h(R.Fragment, null,
          h('div', { className: 'flow-field' },
            h('label', null, 'Tool'),
            h('button', {
              type: 'button',
              onClick: () => openPicker('tool'),
              title: 'Choose a tool from this profile',
              style: { width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8,
                       padding: '7px 10px', borderRadius: 6, cursor: 'pointer', textAlign: 'left',
                       background: 'var(--card-bg)', color: toolName ? 'var(--text-primary)' : 'var(--text-muted)',
                       border: '1px solid var(--border-primary)',
                       fontFamily: toolName ? 'monospace' : 'inherit', fontSize: '0.78rem' },
            },
              h('span', { style: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, toolName || 'Select a tool…'),
              h('svg', { width: 14, height: 14, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round', style: { flexShrink: 0, opacity: 0.6 } },
                h('polyline', { points: '6 9 12 15 18 9' })),
            ),
          ),
          h('div', { className: 'flow-field' },
            h('label', null, 'Arguments (JSON)'),
            h('textarea', {
              value: toolArgs,
              onChange: e => { setToolArgs(e.target.value); if (toolArgsErr) setToolArgsErr(''); },
              rows: 5,
              spellcheck: false,
              placeholder: '{ "sql": "SELECT * FROM t WHERE d = \'$date\'" }',
              style: {
                width: '100%', fontFamily: 'monospace', fontSize: '0.72rem',
                background: 'var(--card-bg)', color: 'var(--text-primary)',
                border: '1px solid var(--border-primary)', borderRadius: 6,
                padding: '6px 8px', resize: 'vertical', boxSizing: 'border-box',
              },
            }),
            h('div', { className: 'flow-param-scope' }, 'String values can reference upstream data with $name or {name}.'),
            toolArgsErr && h('div', { style: { color: '#f87171', fontSize: '0.7rem', marginTop: 4 } }, toolArgsErr),
          ),
          h('div', { className: 'flow-field' },
            h('label', null, 'Profile (optional — selects the data connection)'),
            profiles.length > 0
              ? h(FlowSelect, {
                  value: profile, onChange: setProfile, placeholder: _inheritLabel,
                  options: [
                    { value: '', label: _inheritLabel },
                    ...profiles.map(p => ({ value: p.id, label: `${p.tag || p.name} (${IFOC[p.profile_type]?.label || p.profile_type || 'profile'})`, color: IFOC[p.profile_type]?.color })),
                  ],
                })
              : h('input', { value: profile, onChange: e => setProfile(e.target.value),
                             placeholder: 'leave blank for flow default' }),
          ),
        ),
        type === 'task_prompt' && h(R.Fragment, null,
          h('div', { className: 'flow-field' },
            h('label', null, 'MCP prompt'),
            h('button', {
              type: 'button',
              onClick: () => openPicker('prompt'),
              title: 'Choose an MCP prompt from this profile',
              style: { width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8,
                       padding: '7px 10px', borderRadius: 6, cursor: 'pointer', textAlign: 'left',
                       background: 'var(--card-bg)', color: promptName ? 'var(--text-primary)' : 'var(--text-muted)',
                       border: '1px solid var(--border-primary)',
                       fontFamily: promptName ? 'monospace' : 'inherit', fontSize: '0.78rem' },
            },
              h('span', { style: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, promptName || 'Select a prompt…'),
              h('svg', { width: 14, height: 14, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round', style: { flexShrink: 0, opacity: 0.6 } },
                h('polyline', { points: '6 9 12 15 18 9' })),
            ),
          ),
          h('div', { className: 'flow-field' },
            h('label', null, 'Arguments (JSON)'),
            h('textarea', {
              value: promptArgs,
              onChange: e => { setPromptArgs(e.target.value); if (promptArgsErr) setPromptArgsErr(''); },
              rows: 5,
              spellcheck: false,
              placeholder: '{ "database_name": "$db" }',
              style: {
                width: '100%', fontFamily: 'monospace', fontSize: '0.72rem',
                background: 'var(--card-bg)', color: 'var(--text-primary)',
                border: '1px solid var(--border-primary)', borderRadius: 6,
                padding: '6px 8px', resize: 'vertical', boxSizing: 'border-box',
              },
            }),
            h('div', { className: 'flow-param-scope' }, 'Prompt argument values can reference upstream data with $name or {name}.'),
            promptArgsErr && h('div', { style: { color: '#f87171', fontSize: '0.7rem', marginTop: 4 } }, promptArgsErr),
          ),
          h('div', { className: 'flow-field' },
            h('label', null, 'Profile (Optimize) — runs the prompt'),
            profiles.length > 0
              ? h(FlowSelect, {
                  value: profile,
                  // Changing the profile invalidates the selected prompt (prompts are
                  // per-profile/per-MCP-server), so clear it — the selector then shows
                  // "Select a prompt…" and the next open fetches the new profile's prompts.
                  onChange: (v) => { setProfile(v); setPromptName(''); setPromptArgsErr(''); setPickerItems(null); },
                  placeholder: _inheritLabel,
                  options: [
                    { value: '', label: _inheritLabel },
                    ...profiles.filter(p => p.profile_type === 'tool_enabled').map(p => ({ value: p.id, label: `${p.tag || p.name} (${IFOC[p.profile_type]?.label || p.profile_type || 'profile'})`, color: IFOC[p.profile_type]?.color })),
                  ],
                })
              : h('input', { value: profile, onChange: e => { setProfile(e.target.value); setPromptName(''); setPickerItems(null); },
                             placeholder: 'leave blank for flow default' }),
          ),
        ),
        // Shared tool/prompt picker modal (task_tool selects tools, task_prompt selects prompts).
        (type === 'task_tool' || type === 'task_prompt') && pickerOpen && h(ToolPicker, {
          toolList: pickerItems || [],
          loading: pickerItems === null,
          kind: type === 'task_prompt' ? 'prompt' : 'tool',
          value: type === 'task_prompt' ? promptName : toolName,
          onSelect: type === 'task_prompt' ? setPromptName : setToolName,
          onClose: () => setPickerOpen(false),
        }),
        type === 'task_function' && h('div', { className: 'flow-field' },
          h('label', null, 'Python expression'),
          h(SmartTextField, { value: expr, onChange: setExpr, scopeItems,
                          placeholder: 'e.g. float(output) * $multiplier', rows: 3 }),
        ),
        type === 'decision' && h('div', { className: 'flow-field' },
          h('label', null, 'Condition (Python bool)'),
          h(SmartTextField, { value: cond, onChange: setCond, scopeItems,
                          placeholder: 'e.g. float(output) > $threshold', rows: 2 }),
        ),
        (type === 'task_llm' || type === 'task_prompt' || type === 'task_tool' || type === 'task_function' || type === 'decision' || type === 'judge') && scopeHint,
        producesOutput && h('div', { className: 'flow-field' },
          h('label', null, 'Publish result as'),
          h('input', {
            value: outAs,
            onChange: e => setOutAs(e.target.value.replace(/[^A-Za-z0-9_]/g, '')),
            placeholder: 'optional — e.g. customer_count',
          }),
          h('div', { className: 'flow-param-scope' },
            outAs.trim()
              ? h(R.Fragment, null, 'Downstream nodes can reference ', h('code', null, '$' + outAs.trim()), ' (and still ', h('code', null, 'output'), ').')
              : h(R.Fragment, null, 'Optional. Blank = publish only as ', h('code', null, 'output'), '.'),
          ),
        ),
      ),
      h('button', { className: 'flow-inspector-save', onClick: handleSave }, 'Save changes'),
      // ── Run + Data panel (per-node input/output from the latest run) ──────
      producesOutput && _renderDataPanel({ data, runData, onRunNode }),
    );
  }

  // Pretty-print a value for the data panel; truncates very large blobs.
  function _fmtData(v) {
    if (v === undefined || v === null) return '—';
    let s;
    try { s = typeof v === 'string' ? v : JSON.stringify(v, null, 2); }
    catch (_) { s = String(v); }
    return s.length > 4000 ? s.slice(0, 4000) + '\n… (truncated)' : s;
  }

  function _renderDataPanel({ data, runData, onRunNode }) {
    const status = runData?.status || (data._errorMsg ? 'error' : (data._outputPreview ? 'success' : null));
    const statusColor = status === 'error' ? '#f87171' : status === 'success' ? '#4ade80' : 'var(--text-muted)';
    return h('div', { className: 'flow-data-panel' },
      h('div', { className: 'flow-data-head' },
        h('span', { className: 'flow-data-title' }, 'Data'),
        status && h('span', { className: 'flow-data-status', style: { color: statusColor } }, status),
        h('button', {
          className: 'flow-data-run',
          title: 'Run only this node with its last-known inputs',
          onClick: () => onRunNode && onRunNode('single'),
        },
          h('svg', { width: 11, height: 11, viewBox: '0 0 24 24', fill: 'currentColor' }, h('polygon', { points: '6 4 20 12 6 20 6 4' })),
          'Run node'),
      ),
      (!runData && !data._outputPreview && !data._errorMsg)
        ? h('div', { className: 'flow-data-empty' }, 'No run data yet. Run the flow or this node.')
        : h(R.Fragment, null,
            h('div', { className: 'flow-data-block' },
              h('div', { className: 'flow-data-label' }, 'Input'),
              h('pre', { className: 'flow-data-pre' }, _fmtData(runData?.input_state)),
            ),
            h('div', { className: 'flow-data-block' },
              h('div', { className: 'flow-data-label' }, 'Output'),
              h('pre', { className: `flow-data-pre${data._errorMsg ? ' error' : ''}` },
                data._errorMsg || _fmtData(runData ? runData.output_state : data._outputPreview)),
            ),
          ),
    );
  }

  // ── FlowNLWand: defined at _buildFlowApp scope (not inside FlowApp) ─────────
  // Keeping it here prevents React from treating it as a new component type
  // on every FlowApp re-render, which would unmount/remount the input on each keystroke.

  function FlowNLWand({ nlBuilding, onSubmit }) {
    const [open, setOpen] = useState(false);
    const [text, setText] = useState('');
    const handleSubmit = () => {
      if (!text.trim() || nlBuilding) return;
      onSubmit(text, () => { setText(''); setOpen(false); });
    };
    return h('div', { className: 'flow-nl-wand' },
      !open
        ? h('button', {
            className: 'flow-nl-trigger',
            onClick: () => setOpen(true),
            title: 'Build flow with AI',
          },
            h('svg', { width: 14, height: 14, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' },
              h('polygon', { points: '12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2' }),
            ),
            'AI Build',
          )
        : h('div', { className: 'flow-nl-input-wrap' },
            nlBuilding && h('div', {
              style: { width: 6, height: 6, borderRadius: '50%', background: '#c084fc', flexShrink: 0, animation: 'flow-dash-anim 0.8s ease-in-out infinite alternate' },
            }),
            h('input', {
              className: 'flow-nl-input',
              placeholder: 'Describe what you want this flow to do…',
              value: text,
              onChange: e => setText(e.target.value),
              onKeyDown: e => {
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSubmit(); }
                if (e.key === 'Escape') { setOpen(false); setText(''); }
              },
              autoFocus: true,
              disabled: nlBuilding,
            }),
            h('button', {
              className: 'flow-nl-submit',
              onClick: handleSubmit,
              disabled: nlBuilding || !text.trim(),
            }, nlBuilding ? '…' : '→'),
          ),
    );
  }

  // ── SmartTextField: textarea with $-parameter autocomplete + validation ─────
  // Detects the $token under the caret and offers in-scope names; underlines
  // unknown $refs. scopeItems: [{name, value?, source}]. Defined at builder
  // scope (stable identity) so its local state survives FlowApp re-renders.

  // Returns the {start,end,query} of a $token under the caret, or null.
  function _currentDollarToken(text, caret) {
    let j = caret;
    while (j > 0 && /[A-Za-z0-9_]/.test(text[j - 1])) j--;
    if (j > 0 && text[j - 1] === '$') return { start: j - 1, end: caret, query: text.slice(j, caret) };
    return null;
  }

  function _unknownDollarRefs(text, validSet) {
    const out = [];
    const re = /\$([A-Za-z_][A-Za-z0-9_]*)/g;
    let m;
    while ((m = re.exec(text || ''))) {
      if (!validSet.has(m[1]) && !out.includes(m[1])) out.push(m[1]);
    }
    return out;
  }

  function SmartTextField({ value, onChange, scopeItems = [], placeholder, rows = 3 }) {
    const taRef = useRef(null);
    const [open, setOpen]     = useState(false);
    const [active, setActive] = useState(0);
    const [token, setToken]   = useState(null);

    const validSet = new Set(scopeItems.map(s => s.name));
    const matches = (open && token)
      ? scopeItems.filter(s => s.name.toLowerCase().includes((token.query || '').toLowerCase())).slice(0, 8)
      : [];
    const unknown = _unknownDollarRefs(value, validSet);

    function refresh() {
      const el = taRef.current;
      if (!el) return;
      const tok = _currentDollarToken(el.value, el.selectionStart);
      setToken(tok);
      if (tok) { setOpen(true); setActive(0); } else { setOpen(false); }
    }

    function insert(item) {
      const el = taRef.current;
      if (!el || !token) return;
      const before = (value || '').slice(0, token.start);
      const after  = (value || '').slice(token.end);
      const ins = '$' + item.name;
      onChange(before + ins + after);
      const caret = before.length + ins.length;
      setOpen(false); setToken(null);
      requestAnimationFrame(() => {
        if (taRef.current) { taRef.current.focus(); taRef.current.setSelectionRange(caret, caret); }
      });
    }

    function onKeyDown(e) {
      if (!open || !matches.length) return;
      if (e.key === 'ArrowDown')      { e.preventDefault(); setActive(a => (a + 1) % matches.length); }
      else if (e.key === 'ArrowUp')   { e.preventDefault(); setActive(a => (a - 1 + matches.length) % matches.length); }
      else if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); insert(matches[Math.min(active, matches.length - 1)]); }
      else if (e.key === 'Escape')    { e.preventDefault(); setOpen(false); }
    }

    return h('div', { className: 'flow-sf-wrap' },
      h('textarea', {
        ref: taRef, value: value || '', rows, placeholder,
        onChange: e => { onChange(e.target.value); requestAnimationFrame(refresh); },
        onKeyDown,
        onKeyUp: e => { if (!['ArrowDown', 'ArrowUp', 'Enter', 'Tab', 'Escape'].includes(e.key)) refresh(); },
        onClick: refresh,
        onBlur: () => setTimeout(() => setOpen(false), 120),
      }),
      open && matches.length > 0 && h('div', { className: 'flow-sf-menu' },
        matches.map((s, i) => h('div', {
          key: s.name,
          className: `flow-sf-item${i === active ? ' active' : ''}`,
          onMouseDown: e => { e.preventDefault(); insert(s); },
          onMouseEnter: () => setActive(i),
        },
          h('span', { className: 'flow-sf-name' }, '$' + s.name),
          s.source && h('span', { className: 'flow-sf-src' }, s.source),
        )),
      ),
      unknown.length > 0 && h('div', { className: 'flow-sf-warn' },
        h('svg', { width: 11, height: 11, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' },
          h('path', { d: 'M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z' }),
          h('line', { x1: 12, y1: 9, x2: 12, y2: 13 }), h('line', { x1: 12, y1: 17, x2: 12.01, y2: 17 })),
        `Not defined: ${unknown.map(u => '$' + u).join(', ')}`),
    );
  }

  // ── Main App component ────────────────────────────────────────────────────

  function FlowApp() {
    // nodeTypes must be memoized inside the component (React hook rule)
    const nodeTypes = useMemo(() => _staticNodeTypes, []);

    // ── Flow selector + version state ───────────────────────────────────────
    const [allFlows,    setAllFlows]    = useState([]);
    const [currentFlow, setCurrentFlow] = useState(flow);
    const [versions,    setVersions]    = useState(_flowVersions.get(flow.flow_id) || []);
    const [verDropOpen, setVerDropOpen] = useState(false);
    const [flowSelOpen, setFlowSelOpen] = useState(false);  // custom flow-selector dropdown
    // Flow parameters (Flowise-style Flow State): flow-level key/value pairs,
    // persisted in definition.global_params, available to every node by name.
    // Held as an ordered [{key, value}] array so blank/in-progress rows survive editing.
    const _paramsToRows = (obj) => Object.entries(obj || {}).map(([key, value]) => ({ key, value: value == null ? '' : String(value) }));
    const [flowParams,      setFlowParams]      = useState(() => _paramsToRows(def.global_params));
    const [paramsPanelOpen, setParamsPanelOpen] = useState(false);
    // Global variables (Flowise-style $vars): cross-flow, user-level constants.
    // Persisted via REST (not per-flow), available to every node as a low-priority layer.
    const [globalVars, setGlobalVars] = useState([]);
    // Inline rename: floating editor over a node's label. null = inactive.
    const [renameState, setRenameState] = useState(null);
    // Discoverability: keyboard/gesture cheatsheet + command palette.
    const [showHelp, setShowHelp] = useState(false);
    const [cmdPalette, setCmdPalette] = useState(null);  // null=closed | { query }
    const [loopEdit, setLoopEdit] = useState(null);       // scope id whose editor is open
    const [shownRun, setShownRun] = useState(null);       // the finished run painted on the canvas
    const [runsMenu, setRunsMenu] = useState(null);       // null = closed | { runs: [...] | null }
    // Per-node run data (input/output) from the latest run — powers the inspector Data panel.
    const [nodeDataMap, setNodeDataMap] = useState({});
    // Power-user editing: clipboard for copy/paste, edge "+" insert affordance.
    const _clipboard      = useRef(null);  // { nodes:[...], edges:[...] }
    const [addEdgeHover, setAddEdgeHover] = useState(null);  // { edgeId, x, y } hover "+" on an edge
    const [edgeAddMenu,  setEdgeAddMenu]  = useState(null);  // { edgeId, x, y } node-type picker
    const _edgeHoverTimer = useRef(null);
    // Client-side undo/redo: a linear stack of flow definitions captured at the
    // patchFlow chokepoint. histTick just forces re-render for button enabled state.
    const _undoHist   = useRef([]);
    const _undoIdx    = useRef(-1);
    const _restoring  = useRef(false);
    const [histTick, setHistTick] = useState(0);
    // Tracks which version_id is currently on the canvas.
    // null = "working state" (not pinned to a specific snapshot).
    // Set to version.version_id on restore; cleared on any canvas edit.
    const [activeVersionId, setActiveVersionId] = useState(null);
    // 'in_session': run nodes inside the caller's chat session (result appears in chat)
    // 'new':        each node gets its own fresh session (result appears as new session in sidebar)
    const [execMode,        setExecMode]        = useState(() => _flowModeStore.execMode);
    // 'compact': all LLM nodes share one session | 'individual': each node gets its own
    const [nodeSessionMode, setNodeSessionMode] = useState(() => _flowModeStore.nodeSessionMode);
    // Per-flow layout mode: true = auto-layout after every structural change
    const [autoLayoutEnabled,    setAutoLayoutEnabled]    = useState(() => (flow.definition?.settings?.autoLayout    ?? false));
    // Live ref so the (mount-once) resize observer reads the current auto-layout state.
    const autoLayoutEnabledRef = useRef(autoLayoutEnabled);
    autoLayoutEnabledRef.current = autoLayoutEnabled;
    // ── Per-flow intelligence settings ──────────────────────────────────────
    const [smartDeleteEnabled,   setSmartDeleteEnabled]   = useState(() => (flow.definition?.settings?.smartDelete   ?? true));
    const [snapToGridEnabled,    setSnapToGridEnabled]    = useState(() => (flow.definition?.settings?.snapToGrid    ?? true));
    const [pathHighlightEnabled, setPathHighlightEnabled] = useState(() => (flow.definition?.settings?.pathHighlight ?? false));
    // Per-flow execution mode (persisted). Default false = serialize: run nodes
    // one at a time in a shared session (coherent conversation, correct turn
    // numbers). True = parallelize: concurrent branches each get their own
    // session. Only meaningful — and only surfaced — when the flow has a
    // parallel path, so a single linear conversation never gets concurrent writers.
    const [parallelExecution,    setParallelExecution]    = useState(() => (flow.definition?.settings?.parallelExecution ?? false));
    // Default execution profile for the whole flow. task_llm nodes without their
    // own profile_id inherit it at run time. Initialised (on create) to the
    // profile that built the flow; changeable here in the header.
    const [defaultProfileId,     setDefaultProfileId]     = useState(() => (flow.definition?.settings?.defaultProfileId ?? ''));
    const [showOptionsMenu,      setShowOptionsMenu]      = useState(false);
    // Fixed viewport position of the Options menu. The menu is a body portal: the
    // toolbar is the auto-hiding sub-toolbar (overflow:hidden), so an absolute child
    // was clipped and the menu vanished behind the canvas.
    const [optionsMenuPos,       setOptionsMenuPos]       = useState(null);
    const [highlightedNodes,     setHighlightedNodes]     = useState(null); // Set<nodeId> | null
    const [highlightedEdges,     setHighlightedEdges]     = useState(null); // Set<edgeId> | null
    const currentFlowRef = useRef(flow);

    // Sync from store when inline card changes toggles
    useEffect(() => {
      return _onFlowModeChange(({ execMode: em, nodeSessionMode: nsm }) => {
        setExecMode(em);
        setNodeSessionMode(nsm);
      });
    }, []);

    // ── Canvas state (re-initialized when flow changes) ──────────────────────
    const [rfNodes, setRFNodes] = useState(() => _toRFNodes(def.nodes, def.scopes));
    const [rfEdges, setRFEdges] = useState(() => _toRFEdges(def.edges));
    const [selNode, setSelNode] = useState(null);
    // Always reflect latest node state (e.g. _outputPreview set during a live run)
    const selNodeLive = useMemo(
      () => selNode ? (rfNodes.find(n => n.id === selNode.id) || selNode) : null,
      [selNode, rfNodes],
    );
    const [runState, setRunState] = useState({ status: null, runId: spec.run_id || null,
                                               tokensIn: 0, tokensOut: 0,
                                               failedNodeId: null, errorMsg: null });
    const [profiles, setProfiles] = useState([]);
    const [toolList, setToolList] = useState([]);
    // Minimap view preference — expanded by default, movable + resizable.
    // Persisted per-user in localStorage (a personal viewport pref, NOT a flow
    // property, so it never syncs to others who open the flow). Default expanded:
    // only an explicit collapse ('0') keeps it closed; absent/'1' = expanded.
    const [miniMapOpen, setMiniMapOpen] = useState(() => {
      try { return localStorage.getItem('flowMiniMapOpen') !== '0'; } catch (_) { return true; }
    });
    const [miniMapPos, setMiniMapPos] = useState(() => {
      try { const s = localStorage.getItem('flowMiniMapPos'); return s ? JSON.parse(s) : null; } catch (_) { return null; }
    });
    const [miniMapSize, setMiniMapSize] = useState(() => {
      try { const p = JSON.parse(localStorage.getItem('flowMiniMapSize') || 'null');
            return (p && p.w && p.h) ? p : { w: 200, h: 140 }; } catch (_) { return { w: 200, h: 140 }; }
    });
    const miniBarRef = useRef(null);
    function _toggleMiniMap() {
      setMiniMapOpen(o => {
        const nv = !o;
        try { localStorage.setItem('flowMiniMapOpen', nv ? '1' : '0'); } catch (_) {}
        return nv;
      });
    }
    function _miniMapDragStart(e) {
      if (e.button !== 0) return;
      e.preventDefault(); e.stopPropagation();
      const wrap = miniBarRef.current && miniBarRef.current.parentElement;
      if (!wrap) return;
      const pane = wrap.closest('.react-flow') || wrap.parentElement;
      const paneRect = pane.getBoundingClientRect();
      const wrapRect = wrap.getBoundingClientRect();
      const offX = e.clientX - wrapRect.left, offY = e.clientY - wrapRect.top;
      function move(ev) {
        let left = ev.clientX - paneRect.left - offX;
        let top  = ev.clientY - paneRect.top  - offY;
        // Clamp inside the pane so the widget can never be dragged out of reach.
        left = Math.max(0, Math.min(left, paneRect.width  - wrapRect.width));
        top  = Math.max(0, Math.min(top,  paneRect.height - wrapRect.height));
        setMiniMapPos({ left, top });
      }
      function up() {
        window.removeEventListener('mousemove', move);
        window.removeEventListener('mouseup', up);
        setMiniMapPos(p => { try { localStorage.setItem('flowMiniMapPos', JSON.stringify(p)); } catch (_) {} return p; });
      }
      window.addEventListener('mousemove', move);
      window.addEventListener('mouseup', up);
    }
    function _miniMapResizeStart(e) {
      if (e.button !== 0) return;
      e.preventDefault(); e.stopPropagation();
      // The grip sits on the FREE corner: top-left when docked bottom-right,
      // bottom-right when the widget has been moved (top-left anchored). The
      // enlarge direction flips accordingly so dragging outward always grows it.
      const sign = miniMapPos ? 1 : -1;
      const startX = e.clientX, startY = e.clientY;
      const startW = miniMapSize.w, startH = miniMapSize.h;
      function move(ev) {
        const w = Math.max(120, Math.min(460, startW + sign * (ev.clientX - startX)));
        const h = Math.max(90,  Math.min(340, startH + sign * (ev.clientY - startY)));
        setMiniMapSize({ w, h });
      }
      function up() {
        window.removeEventListener('mousemove', move);
        window.removeEventListener('mouseup', up);
        setMiniMapSize(s => { try { localStorage.setItem('flowMiniMapSize', JSON.stringify(s)); } catch (_) {} return s; });
      }
      window.addEventListener('mousemove', move);
      window.addEventListener('mouseup', up);
    }
    const sseRef = useRef(null);
    // Tracks the runId this canvas started itself, so the run-store subscriber
    // below can tell its own runs apart from ones launched by the inline card.
    const ownRunIdRef = useRef(null);

    // Keep ref current for stable closures
    useEffect(() => {
      currentFlowRef.current = currentFlow;
      _activeSpec = { ...(_activeSpec || spec), flow: currentFlow };
    }, [currentFlow]);

    // RF instance ref — captured via onInit for post-animation fitView
    const rfInstanceRef = useRef(null);
    const onInit = useCallback(inst => {
      rfInstanceRef.current = inst;
      // fitView at React mount fires while the split panel may still be
      // display:none→flex with wrong/zero dimensions; ReactFlow then measures
      // node handles at 0×0 and edges can't route. _remeasureUntilReady retries
      // updateNodeInternals until the container is sized AND every node has been
      // measured, then does a final fitView — so edges always appear on the first
      // load instead of needing one or two page refreshes.
      _remeasureUntilReady();
    }, []);

    // Force ReactFlow to re-measure every node's handle positions so edges re-route.
    function _remeasureEdges() {
      const inst = rfInstanceRef.current;
      if (inst?.updateNodeInternals) {
        try { inst.updateNodeInternals(inst.getNodes().map(n => n.id)); } catch (_) {}
      }
    }

    // Retry handle re-measurement until the canvas is genuinely ready: the
    // container has a real size and ReactFlow has measured every node. Each tick
    // forces a re-measure (cheap + idempotent); the final pass routes the edges
    // and fits the view. Robust against the panel becoming visible late.
    function _remeasureUntilReady(attempts = 20) {
      const inst = rfInstanceRef.current;
      const el = document.querySelector('.flow-canvas-area');
      const sized = !!el && el.offsetWidth > 0 && el.offsetHeight > 0;
      const nodes = inst ? inst.getNodes() : [];
      const measured = nodes.length > 0 && nodes.every(n => (n.measured?.width || n.width));
      if (inst) _remeasureEdges();
      if ((!inst || !sized || !measured) && attempts > 0) {
        setTimeout(() => _remeasureUntilReady(attempts - 1), 110);
        return;
      }
      _remeasureEdges();
      try { inst && inst.fitView({ padding: 0.25, duration: 200 }); } catch (_) {}
    }

    // Re-measure whenever the canvas gains size (panel opens / 0→sized) or first
    // becomes visible. Covers the session-load path where the panel is already
    // mounted-but-hidden and never fires a clean 0→sized transition on its own.
    useEffect(() => {
      const area  = document.querySelector('.flow-canvas-area');
      // Watch the PANEL ROOT, not just the canvas area: the panel grows/shrinks when
      // the real estate around it changes (e.g. the resource panel is opened/closed),
      // and the canvas area alone can miss that reflow. Falls back to the area.
      const sizeTarget = document.getElementById('flow-split-panel') || area;
      if (!sizeTarget) return;
      const cleanups = [];
      if (typeof ResizeObserver !== 'undefined') {
        let lastW = 0, lastH = 0, debounce = null;
        const ro = new ResizeObserver(entries => {
          for (const entry of entries) {
            const { width, height } = entry.contentRect;
            if (!(width > 0 && height > 0)) continue;
            if (Math.abs(width - lastW) < 2 && Math.abs(height - lastH) < 2) continue;
            lastW = width; lastH = height;
            _remeasureEdges();                       // keep edges routed during the reflow
            clearTimeout(debounce);
            debounce = setTimeout(_realignToViewport, 150);  // realign once the size settles
          }
        });
        ro.observe(sizeTarget);
        cleanups.push(() => { clearTimeout(debounce); ro.disconnect(); });
      }
      if (typeof IntersectionObserver !== 'undefined' && area) {
        const io = new IntersectionObserver(entries => {
          for (const entry of entries) {
            if (entry.isIntersecting) _remeasureUntilReady();
          }
        }, { threshold: 0.01 });
        io.observe(area);
        cleanups.push(() => io.disconnect());
      }
      return () => cleanups.forEach(fn => fn());
    }, []);

    // Realign the diagram to the current viewport when the panel resizes (e.g. the
    // resource panel is opened/closed). A topological DAG layout is width-independent:
    // node positions never need to change when the canvas grows or shrinks — only the
    // zoom-to-fit reacts to the new space. So we ONLY remeasure handles + fitView here.
    // We must NOT re-run a layout: the authoritative positions come from the backend
    // (compute_layout, the same engine the Layout button uses) and are already stored
    // on every node. Re-running the lighter frontend _autoLayout here would overwrite
    // them with an inferior result on every open/resize (it collapses single-node ranks
    // to a vertical spine and does not branch decisions), forcing a manual Layout click.
    function _realignToViewport() {
      const inst = rfInstanceRef.current;
      if (!inst) { _remeasureUntilReady(); return; }
      _remeasureEdges();
      try { inst.fitView({ padding: 0.2, duration: 260 }); } catch (_) {}
    }

    // Re-measure when the displayed flow changes (flow switch within an open,
    // already-sized canvas — no resize/intersection event fires in that case).
    useEffect(() => { _remeasureUntilReady(); }, [currentFlow.flow_id]);

    // Debug: log rfNodes on first render
    useEffect(() => {
      console.log('[FlowDebug] FlowApp mounted | rfNodes:', rfNodes.length, '| rfEdges:', rfEdges.length,
        '| nodeTypes keys:', Object.keys(nodeTypes || {}).join(','));
    }, []);

    // ── P0 UX Intelligence state ─────────────────────────────────────────────
    const [isDragOver,         setIsDragOver]         = useState(false);
    const [hoveredEdgeId,      setHoveredEdgeId]      = useState(null);
    const [ctxMenu,            setCtxMenu]            = useState(null);
    const [showHealthPanel,    setShowHealthPanel]    = useState(false);
    const [showReadinessModal, setShowReadinessModal] = useState(false);
    const [showAnalytics,      setShowAnalytics]      = useState(false);
    const [nodeRunData,        setNodeRunData]        = useState({});
    const [liveCost,           setLiveCost]           = useState(0);
    const [nlBuilding,         setNlBuilding]         = useState(false);
    // Stable refs for keyboard handler (avoids stale closures)
    const panelActiveRef = useRef(false);
    const selNodeRef     = useRef(null);
    const rfEdgesRef     = useRef(rfEdges);  // stable ref for keyboard handler closures
    const rfNodesRef     = useRef(rfNodes);  // stable ref for keyboard handler closures
    const runStateRef    = useRef(null);
    const hasNodesRef    = useRef(false);
    const _vReloadTimer  = useRef(null);  // debounce: one version-list reload per patch burst
    const verBtnRef      = useRef(null);  // History button — anchor for the version dropdown portal
    const verDropRef     = useRef(null);  // version dropdown element (rendered in a body portal)
    const flowSelBtnRef  = useRef(null);  // Flow-selector trigger — anchor for its dropdown portal
    const flowSelDropRef = useRef(null);  // Flow-selector dropdown element (rendered in a body portal)
    const paramsBtnRef   = useRef(null);  // Params button — anchor for the params panel portal
    const paramsPanelRef = useRef(null);  // params panel element (rendered in a body portal)

    // ── Version helpers ──────────────────────────────────────────────────────
    async function _loadVersions(fid) {
      if (!fid || fid === 'undefined') return;
      const token = localStorage.getItem('tda_auth_token');
      if (!token) return;
      try {
        const r = await fetch(`/api/v1/flows/${fid}/versions`,
          { headers: { 'Authorization': `Bearer ${token}` } });
        if (!r.ok) return;
        const d = await r.json();
        const vs = d.versions || [];
        _flowVersions.set(fid, vs);
        if (currentFlowRef.current?.flow_id === fid) setVersions(vs);
      } catch (_) {}
    }

    async function _autoSnapshot(fid) {
      const token = localStorage.getItem('tda_auth_token');
      if (!token || !fid) return;
      try {
        await fetch(`/api/v1/flows/${fid}/versions`, {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ label: null }),
        });
        // After any canvas edit that creates a snapshot, we are no longer
        // pinned to a specific restored version.
        setActiveVersionId(null);
        _loadVersions(fid);
      } catch (_) {}
    }

    // ── Internal patchFlow — always targets the currently displayed flow ─────
    // Returns the server response data so callers can use node_positions / definition.
    const patchFlowRef = useRef(null);
    const lastPatchErrorRef = useRef(null);
    patchFlowRef.current = patchFlow;
    async function patchFlow(patchOp) {
      const fid = currentFlowRef.current?.flow_id;
      if (!fid) return null;
      const token = localStorage.getItem('tda_auth_token');
      if (!token) return null;
      try {
        const r = await fetch(`/api/v1/flows/${fid}/patch`, {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify(patchOp),
        });
        if (!r.ok) {
          // A refused edit (e.g. a loop the rules do not allow) says why — in words.
          let msg = null;
          try { const j = await r.json(); msg = j && j.error; } catch (_) {}
          lastPatchErrorRef.current = msg;
          if (msg && r.status === 400 && typeof window.showAppBanner === 'function') window.showAppBanner(msg, 'warning');
          return null;
        }
        lastPatchErrorRef.current = null;
        const data = await r.json();
        // If the server applied layout (auto-layout ON, or structural ops like remove_node/add_edge),
        // slide all non-insert_node ops to their new positions automatically.
        // insert_node and add_node handle their own animations via _applyLivePatch / result usage.
        const _autoAnimateOps = new Set(['remove_node', 'add_edge', 'remove_edge']);
        if (_autoAnimateOps.has(patchOp.op) && data.layout_applied && data.node_positions &&
            Object.keys(data.node_positions).length > 0) {
          _enableNodeSlide(350);
          setRFNodes(prev => prev.map(nd => {
            const p = data.node_positions[nd.id];
            return p ? { ...nd, position: p } : nd;
          }));
          setTimeout(() => rfInstanceRef.current?.fitView({ padding: 0.2, duration: 300 }), 410);
        }
        // Backend auto-snapshots each patch; debounce a single version-list refresh
        // so a burst of patches (e.g. insert_node) shows as one UI update.
        if (_vReloadTimer.current) clearTimeout(_vReloadTimer.current);
        _vReloadTimer.current = setTimeout(() => {
          _vReloadTimer.current = null;
          _loadVersions(fid);
          setActiveVersionId(null);  // any canvas edit clears the restore pin
        }, 800);
        // Capture this edit for undo/redo (skipped while restoring).
        if (data?.definition) _recordHistory(data.definition);
        return data;  // { flow_id, op, definition[, node_positions, all_edges] }
      } catch (err) {
        console.error('[FlowRenderer] Patch failed:', err);
        return null;
      }
    }

    // ── Manual layout: call server Sugiyama–Barycenter endpoint ────────────
    async function handleLayout() {
      const fid = currentFlowRef.current?.flow_id;
      const token = localStorage.getItem('tda_auth_token');
      if (!fid || !token) return;
      try {
        const r = await fetch(`/api/v1/flows/${fid}/layout`, {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${token}` },
        });
        if (!r.ok) return;
        const data = await r.json();
        if (data.node_positions && Object.keys(data.node_positions).length > 0) {
          _enableNodeSlide(400);
          setRFNodes(prev => prev.map(nd => {
            const p = data.node_positions[nd.id];
            return p ? { ...nd, position: p } : nd;
          }));
          setTimeout(() => rfInstanceRef.current?.fitView({ padding: 0.2, duration: 360 }), 450);
        }
        // Layout creates a snapshot — refresh version list
        const _vFid = fid;
        setTimeout(() => { _loadVersions(_vFid); setActiveVersionId(null); }, 900);
      } catch (err) {
        console.error('[FlowRenderer] handleLayout failed:', err);
      }
    }

    // ── Toggle per-flow auto-layout setting ────────────────────────────────
    async function handleToggleAutoLayout() {
      const newVal = !autoLayoutEnabled;
      setAutoLayoutEnabled(newVal);  // optimistic
      await patchFlow({ op: 'update_settings', payload: { settings: { autoLayout: newVal } } });
      // Turning auto-layout ON should immediately tidy the current graph (the
      // update_settings patch itself doesn't re-run layout) — match the user's
      // expectation that "auto-layout enabled" means an arranged canvas.
      if (newVal) handleLayout();
    }

    // ── Intelligence feature toggles ─────────────────────────────────────────
    async function handleToggleSmartDelete() {
      const newVal = !smartDeleteEnabled;
      setSmartDeleteEnabled(newVal);
      await patchFlow({ op: 'update_settings', payload: { settings: { smartDelete: newVal } } });
    }
    async function handleToggleSnapToGrid() {
      const newVal = !snapToGridEnabled;
      setSnapToGridEnabled(newVal);
      await patchFlow({ op: 'update_settings', payload: { settings: { snapToGrid: newVal } } });
    }
    async function handleTogglePathHighlight() {
      const newVal = !pathHighlightEnabled;
      setPathHighlightEnabled(newVal);
      if (!newVal) { setHighlightedNodes(null); setHighlightedEdges(null); }
      await patchFlow({ op: 'update_settings', payload: { settings: { pathHighlight: newVal } } });
    }
    async function handleSetParallelExecution(newVal) {
      if (newVal === parallelExecution) return;
      setParallelExecution(newVal);  // optimistic
      await patchFlow({ op: 'update_settings', payload: { settings: { parallelExecution: newVal } } });
    }
    async function handleSetDefaultProfile(pid) {
      if (pid === defaultProfileId) return;
      setDefaultProfileId(pid);  // optimistic
      await patchFlow({ op: 'update_settings', payload: { settings: { defaultProfileId: pid } } });
    }

    // ── Path highlight: BFS forward + backward from hovered node ─────────────
    const onNodeMouseEnter = useCallback((_, node) => {
      if (!pathHighlightEnabled) return;
      const fwd = new Set(), bwd = new Set();
      const fwdQ = [node.id];
      while (fwdQ.length) {
        const n = fwdQ.shift();
        if (fwd.has(n)) continue;
        fwd.add(n);
        rfEdgesRef.current.forEach(e => { if (e.source === n && !fwd.has(e.target)) fwdQ.push(e.target); });
      }
      const bwdQ = [node.id];
      while (bwdQ.length) {
        const n = bwdQ.shift();
        if (bwd.has(n)) continue;
        bwd.add(n);
        rfEdgesRef.current.forEach(e => { if (e.target === n && !bwd.has(e.source)) bwdQ.push(e.source); });
      }
      const all = new Set([...fwd, ...bwd]);
      setHighlightedNodes(all);
      setHighlightedEdges(new Set(
        rfEdgesRef.current.filter(e => all.has(e.source) && all.has(e.target)).map(e => e.id)
      ));
    }, [pathHighlightEnabled]);

    const onNodeMouseLeave = useCallback(() => {
      if (!pathHighlightEnabled) return;
      setHighlightedNodes(null);
      setHighlightedEdges(null);
    }, [pathHighlightEnabled]);

    // ── Switch to a different flow ───────────────────────────────────────────
    async function switchFlow(flowId) {
      if (flowId === currentFlowRef.current?.flow_id) return;
      const token = localStorage.getItem('tda_auth_token');
      if (!token) return;
      try {
        const r = await fetch(`/api/v1/flows/${flowId}`,
          { headers: { 'Authorization': `Bearer ${token}` } });
        if (!r.ok) return;
        const newFlow = await r.json();
        const newDef = newFlow.definition || { nodes: [], edges: [], scopes: [] };
        currentFlowRef.current = newFlow;
        setCurrentFlow(newFlow);
        setAutoLayoutEnabled(newDef.settings?.autoLayout    ?? false);
        setSmartDeleteEnabled(newDef.settings?.smartDelete  ?? true);
        setSnapToGridEnabled(newDef.settings?.snapToGrid    ?? true);
        setPathHighlightEnabled(newDef.settings?.pathHighlight ?? false);
        setParallelExecution(newDef.settings?.parallelExecution ?? false);
        setDefaultProfileId(newDef.settings?.defaultProfileId ?? '');
        setHighlightedNodes(null);
        setHighlightedEdges(null);
        setRFNodes(_toRFNodes(newDef.nodes, newDef.scopes));
        setRFEdges(_toRFEdges(newDef.edges));
        setSelNode(null);
        setRunState({ status: null, runId: null, tokensIn: 0, tokensOut: 0 });
        setVersions(_flowVersions.get(flowId) || []);
        const titleEl = document.getElementById('flow-split-title');
        if (titleEl) titleEl.textContent = newFlow.name || 'Flow Canvas';
        _loadVersions(flowId);
        // Refresh flow list so name changes appear
        fetch('/api/v1/flows', { headers: { 'Authorization': `Bearer ${token}` } })
          .then(r => r.ok ? r.json() : null)
          .then(d => { if (d?.flows) setAllFlows(d.flows); })
          .catch(() => {});
      } catch (err) {
        console.error('[FlowRenderer] switchFlow failed:', err);
      }
    }

    // ── Restore a historical version ─────────────────────────────────────────
    async function handleRestoreVersion(version) {
      const fid = currentFlowRef.current?.flow_id;
      const token = localStorage.getItem('tda_auth_token');
      if (!fid || !token) return;
      try {
        const r = await fetch(`/api/v1/flows/${fid}/versions/${version.version_id}/restore`, {
          method: 'POST', headers: { 'Authorization': `Bearer ${token}` },
        });
        if (!r.ok) return;
        const restored = await r.json();
        const newDef = restored.definition || { nodes: [], edges: [], scopes: [] };
        setRFNodes(_toRFNodes(newDef.nodes, newDef.scopes));
        setRFEdges(_toRFEdges(newDef.edges));
        setSelNode(null);
        setVerDropOpen(false);
        // Pin the active marker to this exact version so the dropdown can
        // show Restore on every other entry — including the most recent one.
        setActiveVersionId(version.version_id);
        _loadVersions(fid);
      } catch (err) {
        console.error('[FlowRenderer] Restore failed:', err);
      }
    }

    // ── Load all flows + profiles on mount ───────────────────────────────────
    useEffect(() => {
      const token = localStorage.getItem('tda_auth_token');
      if (!token) return;
      fetch('/api/v1/flows', { headers: { 'Authorization': `Bearer ${token}` } })
        .then(r => r.ok ? r.json() : null)
        .then(d => { if (d?.flows) setAllFlows(d.flows); })
        .catch(() => {});
      fetch('/api/v1/profiles', { headers: { 'Authorization': `Bearer ${token}` } })
        .then(r => r.ok ? r.json() : null)
        .then(d => { if (d?.profiles) setProfiles(d.profiles); })
        .catch(() => {});
      // Tool catalog for the task_tool node picker (datalist autocomplete).
      fetch('/api/v1/flows/tools', { headers: { 'Authorization': `Bearer ${token}` } })
        .then(r => r.ok ? r.json() : null)
        .then(d => { if (d?.tools) setToolList(d.tools); })
        .catch(() => {});
      if (flow.flow_id) _loadVersions(flow.flow_id);
    }, []);

    // SSE subscription for live execution events
    useEffect(() => {
      const runId = runState.runId;
      if (!runId) return;
      const token = localStorage.getItem('tda_auth_token');
      if (!token) return;

      const es = new EventSource(`/api/v1/flows/runs/${runId}/stream?token=${encodeURIComponent(token)}`);
      sseRef.current = es;

      es.onmessage = (evt) => {
        try {
          const { type, payload } = JSON.parse(evt.data);
          _handleRunEvent(type, payload);
        } catch (_) {}
      };
      es.onerror = () => es.close();

      return () => es.close();
    }, [runState.runId]);

    // ── Adopt runs launched from the inline flow card ────────────────────────
    // The card writes the new runId to the shared run store but only the card
    // listened to it, so a card-initiated run never animated the open canvas.
    // Here the canvas subscribes too: when a run starts for the flow this canvas
    // currently shows (and we didn't start it ourselves), pull the runId into
    // local state so the SSE → node-animation pipeline above fires.
    useEffect(() => {
      return _onFlowRunChange(({ runId, status, flowId }) => {
        if (!runId) return;
        if (status !== 'running' && status !== 'queued') return;
        // Only adopt runs for the flow this canvas currently displays.
        if (flowId && flowId !== currentFlowRef.current?.flow_id) return;
        // Ignore our own runs — handleExecute already wired those up.
        if (runId === ownRunIdRef.current) return;
        setRunState(prev => (prev.runId === runId
          ? prev
          : { status: 'running', runId, tokensIn: 0, tokensOut: 0 }));
        // Clear stale execution badges so the animation starts from a clean slate.
        setRFNodes(nds => nds.map(n => ({ ...n, data: { ...n.data, _execStatus: null } })));
      });
    }, []);

    // ── Adopt runs started ANYWHERE via the per-user notification channel ────
    // The always-on notification channel signals flow_run_start for a run started
    // by ANY initiator — a chat LLM tool call ("execute the X flow"), REST/n8n,
    // the scheduler, another session — not just this canvas's Run button or the
    // inline card (the _onFlowRunChange adopter above covers only the card-button
    // path). When the run targets the flow this canvas shows (and isn't one we
    // started), pull its runId into local state so the SSE → node-animation
    // pipeline above fires. This is what makes a flow executed from the
    // conversation animate the open canvas — mirroring how KG animation works off
    // this same channel.
    useEffect(() => {
      function onRunEvent(e) {
        const { type, payload } = e.detail || {};
        if (type !== 'flow_run_start' || !payload || !payload.run_id) return;
        // Guard by flow_id — only animate the flow this canvas currently displays.
        if (payload.flow_id && payload.flow_id !== currentFlowRef.current?.flow_id) return;
        if (payload.run_id === ownRunIdRef.current) return;  // our own run, already wired
        setRunState(prev => (prev.runId === payload.run_id
          ? prev
          : { status: 'running', runId: payload.run_id, tokensIn: 0, tokensOut: 0 }));
        setRFNodes(nds => nds.map(n => ({ ...n, data: { ...n.data, _execStatus: null } })));
      }
      UderiaBus.on('uderia:flow-run-event', onRunEvent);
      return () => UderiaBus.off('uderia:flow-run-event', onRunEvent);
    }, []);

    // ── Reconcile on mount: adopt a run already in flight for this flow ───────
    // Snapshot-on-connect, the complement to the live broadcast above. The canvas
    // mounts fresh every time a flow is opened/switched, and it may mount AFTER a
    // run already started — e.g. you execute a flow in one session, then switch to
    // another session showing the same flow mid-run. The one-shot flow_run_start
    // broadcast already fired, so we reconcile against current state: ask the
    // backend for this flow's latest run and, if it is still in flight, adopt it.
    // Setting runState.runId hands off to the existing SSE + replay-buffer path,
    // which back-fills the events that fired before this mount — making the canvas
    // self-healing regardless of when it opened ("snapshot + stream").
    useEffect(() => {
      const flowId = currentFlowRef.current?.flow_id || flow.flow_id;
      if (!flowId) return;
      const token = localStorage.getItem('tda_auth_token');
      if (!token) return;
      let cancelled = false;
      fetch(`/api/v1/flows/${flowId}/runs?limit=1`, { headers: { 'Authorization': `Bearer ${token}` } })
        .then(r => (r.ok ? r.json() : null))
        .then(d => {
          if (cancelled || !d) return;
          const latest = (d.runs || [])[0];
          if (!latest) return;
          if (latest.status !== 'running' && latest.status !== 'queued') {
            // Finished: show how it ended (statuses, verdicts, canvas, loop endings).
            fetch(`/api/v1/flows/runs/${latest.run_id}`, { headers: { 'Authorization': `Bearer ${token}` } })
              .then(r => (r.ok ? r.json() : null))
              .then(run => {
                if (cancelled || !run) return;
                const ov = _runOutcomeOverlay(run, run.node_runs);
                setRFNodes(nds => _paintRunOverlay(nds, ov));
                setShownRun({ run_id: run.run_id, status: run.status, at: run.completed_at || run.started_at || run.created_at, latest: true });
              })
              .catch(() => {});
            return;
          }
          if (latest.run_id === ownRunIdRef.current) return;  // our own run, already wired
          setRunState(prev => (prev.runId === latest.run_id
            ? prev
            : { status: 'running', runId: latest.run_id, tokensIn: 0, tokensOut: 0 }));
          setRFNodes(nds => nds.map(n => ({ ...n, data: { ...n.data, _execStatus: null } })));
        })
        .catch(() => {});
      return () => { cancelled = true; };
    }, []);

    // ── Loops: make from a selection, edit, remove (rules: core/flow_loops.py) ──
    function _applyServerDefinition(def) {
      if (!def) return;
      setCurrentFlow(prev => ({ ...prev, definition: def }));
      setRFNodes(_toRFNodes(def.nodes, def.scopes));
      setRFEdges(_toRFEdges(def.edges));
      setTimeout(() => rfInstanceRef.current?.fitView({ padding: 0.2, duration: 360 }), 120);
    }
    async function _makeLoop(ids) {
      if (!ids || !ids.length) return;
      const data = await patchFlowRef.current?.({ op: 'create_scope', payload: { node_ids: ids } });
      if (!data?.definition) return;
      _applyServerDefinition(data.definition);
      const made = (data.definition.scopes || []).find(sc => ids.every(i => (sc.node_ids || []).includes(i)));
      if (made) setLoopEdit(made.id);
    }
    async function _saveLoop(scopeId, updates) {
      const data = await patchFlowRef.current?.({ op: 'update_scope', payload: { scope_id: scopeId, updates } });
      if (!data?.definition) return { error: lastPatchErrorRef.current || 'The loop could not be saved.' };
      _applyServerDefinition(data.definition);
      return { ok: true };
    }
    async function _removeLoop(scopeId) {
      const data = await patchFlowRef.current?.({ op: 'delete_scope', payload: { scope_id: scopeId } });
      if (data?.definition) _applyServerDefinition(data.definition);
    }
    // ── Earlier runs: pick one and see how it ended (statuses, verdicts, loops) ──
    async function _openRunsMenu() {
      if (runsMenu) { setRunsMenu(null); return; }
      setRunsMenu({ runs: null });
      const fid = currentFlowRef.current?.flow_id;
      const token = localStorage.getItem('tda_auth_token');
      if (!fid || !token) { setRunsMenu({ runs: [] }); return; }
      try {
        const r = await fetch(`/api/v1/flows/${fid}/runs?limit=15`, { headers: { 'Authorization': `Bearer ${token}` } });
        const d = r.ok ? await r.json() : null;
        setRunsMenu({ runs: ((d && d.runs) || []).filter(x => x.status !== 'running' && x.status !== 'queued') });
      } catch (_) { setRunsMenu({ runs: [] }); }
    }
    async function _showRun(runId, isLatest) {
      setRunsMenu(null);
      const token = localStorage.getItem('tda_auth_token');
      try {
        const r = await fetch(`/api/v1/flows/runs/${runId}`, { headers: { 'Authorization': `Bearer ${token}` } });
        const run = r.ok ? await r.json() : null;
        if (!run) return;
        setRFNodes(nds => _paintRunOverlay(nds, _runOutcomeOverlay(run, run.node_runs)));
        setShownRun({ run_id: run.run_id, status: run.status, at: run.completed_at || run.started_at || run.created_at, latest: !!isLatest });
      } catch (_) {}
    }
    function _runWhen(ts) {
      if (!ts) return 'unknown time';
      const d = new Date(ts.endsWith && !/[zZ]|[+-]\d\d:?\d\d$/.test(ts) ? ts + 'Z' : ts);
      return isNaN(d) ? ts : d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    }
    useEffect(() => {
      if (!runsMenu) return;
      const close = (e) => { if (!(e.target && e.target.closest && e.target.closest('.flow-runs-wrap'))) setRunsMenu(null); };
      const esc = (e) => { if (e.key === 'Escape') setRunsMenu(null); };
      document.addEventListener('pointerdown', close);
      document.addEventListener('keydown', esc);
      return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', esc); };
    }, [runsMenu]);
    function buildRunsControl() {
      const running = runState.status === 'running' || runState.status === 'queued';
      return h('span', { className: 'flow-runs-wrap' },
        shownRun && !running && h('span', { className: 'flow-runs-shown' },
          `Showing ${shownRun.latest ? 'the last run' : 'the run'} of ${_runWhen(shownRun.at)} — ${shownRun.status}`),
        h('button', { type: 'button', className: 'flow-runs-btn', disabled: running, 'aria-haspopup': 'listbox', 'aria-expanded': !!runsMenu,
                      title: running ? 'A run is in progress' : 'Show how an earlier run ended', onClick: _openRunsMenu },
          'Earlier runs',
          h('svg', { width: 10, height: 10, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2.2, strokeLinecap: 'round', 'aria-hidden': 'true' },
            h('polyline', { points: '6 15 12 9 18 15' }))),
        runsMenu && h('div', { className: 'flow-runs-menu', role: 'listbox', 'aria-label': 'Earlier runs',
                               onPointerDown: e => e.stopPropagation() },
          runsMenu.runs === null ? h('div', { className: 'flow-runs-empty' }, 'Loading runs…')
          : runsMenu.runs.length === 0 ? h('div', { className: 'flow-runs-empty' }, 'No finished runs yet.')
          : runsMenu.runs.map((x, i) => h('button', { key: x.run_id, type: 'button', role: 'option',
              'aria-selected': shownRun && shownRun.run_id === x.run_id,
              className: `flow-runs-item${shownRun && shownRun.run_id === x.run_id ? ' is-current' : ''}`,
              onClick: () => _showRun(x.run_id, i === 0) },
              h('span', { className: 'flow-runs-dot', style: { background: STATUS_COLORS[x.status] || STATUS_COLORS.idle }, 'aria-hidden': 'true' }),
              h('span', null, _runWhen(x.completed_at || x.started_at || x.created_at)),
              h('span', { className: 'flow-runs-meta' }, `${x.status}${x.cost_usd ? ' · $' + Number(x.cost_usd).toFixed(4) : ''}`))),
        ));
    }

    function buildLoopEditor() {
      if (!loopEdit) return null;
      const def = currentFlowRef.current?.definition || {};
      const scope = (def.scopes || []).find(sc => sc.id === loopEdit);
      if (!scope) return null;
      const byId = Object.fromEntries((def.nodes || []).map(n => [n.id, n]));
      const members = (scope.node_ids || []).filter(i => byId[i]).map(i => ({ id: i, label: byId[i].label || i, type: byId[i].type }));
      return h(LoopEditor, { key: scope.id, scope, members,
        onSave: (u) => _saveLoop(scope.id, u), onRemove: () => _removeLoop(scope.id), onClose: () => setLoopEdit(null) });
    }
    useEffect(() => {
      function onEdit(e) { const id = e && e.detail && e.detail.scope_id; if (id) setLoopEdit(id); }
      UderiaBus.on('uderia:flow-scope-edit', onEdit);
      return () => UderiaBus.off('uderia:flow-scope-edit', onEdit);
    }, []);

    // A loop frame's round stepper (ScopeGroupNode is static, so it signals here).
    useEffect(() => {
      function onRounds(e) {
        const { scope_id, max_iterations } = (e && e.detail) || {};
        if (!scope_id || !max_iterations) return;
        setRFNodes(nds => nds.map(n => n.id === `scope-${scope_id}`
          ? { ...n, data: { ...n.data, max_iterations } } : n));
        patchFlowRef.current?.({ op: 'update_scope', payload: { scope_id, updates: { max_iterations } } });
      }
      UderiaBus.on('uderia:flow-scope-rounds', onRounds);
      return () => UderiaBus.off('uderia:flow-scope-rounds', onRounds);
    }, []);

    // Close version dropdown on outside click
    useEffect(() => {
      if (!verDropOpen) return;
      function handleOutside(e) {
        const wrapper = document.querySelector('.flow-ver-wrapper');
        // The dropdown is rendered in a body portal (escapes the toolbar's overflow
        // clip), so a click inside it is NOT inside the wrapper — check both.
        const insideWrapper = wrapper && wrapper.contains(e.target);
        const insideDrop = verDropRef.current && verDropRef.current.contains(e.target);
        if (!insideWrapper && !insideDrop) setVerDropOpen(false);
      }
      document.addEventListener('pointerdown', handleOutside);
      return () => document.removeEventListener('pointerdown', handleOutside);
    }, [verDropOpen]);

    // Close flow-selector dropdown on outside click (portal-aware, mirrors above).
    useEffect(() => {
      if (!flowSelOpen) return;
      function handleOutside(e) {
        const wrapper = document.querySelector('.flow-selector-wrapper');
        const insideWrapper = wrapper && wrapper.contains(e.target);
        const insideDrop = flowSelDropRef.current && flowSelDropRef.current.contains(e.target);
        if (!insideWrapper && !insideDrop) setFlowSelOpen(false);
      }
      document.addEventListener('pointerdown', handleOutside);
      return () => document.removeEventListener('pointerdown', handleOutside);
    }, [flowSelOpen]);

    // Resync flow parameters + reset the undo history when the displayed flow changes.
    useEffect(() => {
      setFlowParams(_paramsToRows(currentFlow.definition?.global_params));
      _undoHist.current = currentFlow.definition ? [currentFlow.definition] : [];
      _undoIdx.current = currentFlow.definition ? 0 : -1;
      setHistTick(t => t + 1);
    }, [currentFlow.flow_id]);

    // ── Undo / redo ───────────────────────────────────────────────────────────
    // Records every structural edit (all flow through patchFlow). Capped at 50.
    function _recordHistory(definition) {
      if (_restoring.current || !definition) return;
      const next = _undoHist.current.slice(0, _undoIdx.current + 1);
      next.push(definition);
      const capped = next.slice(-50);
      _undoHist.current = capped;
      _undoIdx.current = capped.length - 1;
      setHistTick(t => t + 1);
    }

    async function _restoreDefinition(def) {
      if (!def) return;
      _restoring.current = true;
      setCurrentFlow(prev => ({ ...prev, definition: def }));
      setRFNodes(_toRFNodes(def.nodes, def.scopes));
      setRFEdges(_toRFEdges(def.edges));
      setFlowParams(_paramsToRows(def.global_params));
      setSelNode(null);
      const token = localStorage.getItem('tda_auth_token');
      const fid = currentFlowRef.current?.flow_id;
      if (token && fid) {
        try {
          await fetch(`/api/v1/flows/${fid}`, {
            method: 'PUT',
            headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ definition: def }),
          });
        } catch (_) {}
        _loadVersions(fid);
      }
      setTimeout(() => rfInstanceRef.current?.fitView({ padding: 0.2, duration: 300 }), 60);
      _restoring.current = false;
    }

    function _undo() {
      if (_undoIdx.current <= 0) return;
      _undoIdx.current -= 1;
      setHistTick(t => t + 1);
      _restoreDefinition(_undoHist.current[_undoIdx.current]);
    }
    function _redo() {
      if (_undoIdx.current >= _undoHist.current.length - 1) return;
      _undoIdx.current += 1;
      setHistTick(t => t + 1);
      _restoreDefinition(_undoHist.current[_undoIdx.current]);
    }

    // Close params panel on outside click (panel is a body portal — check both anchor + panel).
    useEffect(() => {
      if (!paramsPanelOpen) return;
      function handleOutside(e) {
        const wrapper = document.querySelector('.flow-params-wrapper');
        const insideWrapper = wrapper && wrapper.contains(e.target);
        const insidePanel = paramsPanelRef.current && paramsPanelRef.current.contains(e.target);
        if (!insideWrapper && !insidePanel) setParamsPanelOpen(false);
      }
      document.addEventListener('pointerdown', handleOutside);
      return () => document.removeEventListener('pointerdown', handleOutside);
    }, [paramsPanelOpen]);

    // Persist flow parameters to definition.global_params via the patch op.
    // Collapses the row array to a dict (last write wins on dup keys, blanks dropped).
    async function _saveParams(rows) {
      const clean = {};
      for (const row of rows) {
        const key = (row.key || '').trim();
        if (key) clean[key] = row.value;
      }
      const data = await patchFlow({ op: 'update_global_params', payload: { params: clean } });
      if (data?.definition) {
        setCurrentFlow(prev => ({ ...prev, definition: data.definition }));
      }
    }

    // Load cross-flow global variables once on mount.
    useEffect(() => {
      const token = localStorage.getItem('tda_auth_token');
      if (!token) return;
      fetch('/api/v1/flow-variables', { headers: { 'Authorization': `Bearer ${token}` } })
        .then(r => r.ok ? r.json() : null)
        .then(d => { if (d?.variables) setGlobalVars(d.variables.map(v => ({ key: v.name, value: v.value == null ? '' : String(v.value) }))); })
        .catch(() => {});
    }, []);

    // Persist global variables (replace-whole-set) via REST.
    async function _saveGlobalVars(rows) {
      const token = localStorage.getItem('tda_auth_token');
      if (!token) return;
      const variables = {};
      for (const row of rows) {
        const key = (row.key || '').trim();
        if (key) variables[key] = row.value;
      }
      try {
        await fetch('/api/v1/flow-variables', {
          method: 'PUT',
          headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ variables }),
        });
      } catch (_) {}
    }

    // Load per-node input/output data from the latest run (for the inspector Data panel).
    async function _loadNodeData() {
      const fid = currentFlowRef.current?.flow_id;
      const token = localStorage.getItem('tda_auth_token');
      if (!fid || !token) return;
      try {
        const r = await fetch(`/api/v1/flows/${fid}/node-data`, { headers: { 'Authorization': `Bearer ${token}` } });
        if (!r.ok) return;
        const d = await r.json();
        setNodeDataMap(d.node_data || {});
      } catch (_) {}
    }
    // Refresh node data when the inspector opens and whenever a run completes.
    useEffect(() => { if (selNode) _loadNodeData(); }, [selNode?.id]);
    useEffect(() => { if (runState.status === 'complete') _loadNodeData(); }, [runState.status]);

    // Collect `output_as` names published by all ancestors of a node (reverse BFS
    // over edges) — used to show available $references in the inspector.
    function _collectUpstreamNames(nodeId) {
      const defn = currentFlow.definition || {};
      const edges = defn.edges || [];
      const byId = {};
      (defn.nodes || []).forEach(n => { byId[n.id] = n; });
      const anc = new Set();
      const stack = [nodeId];
      while (stack.length) {
        const cur = stack.pop();
        for (const e of edges) {
          if (e.target === cur && !anc.has(e.source)) { anc.add(e.source); stack.push(e.source); }
        }
      }
      const names = [];
      anc.forEach(id => {
        const oa = ((byId[id] || {}).data || {}).output_as;
        if (oa && String(oa).trim()) names.push(String(oa).trim());
      });
      return [...new Set(names)];
    }

    // Build the ordered, de-duplicated scope shown for a node's smart fields:
    // flow parameters → global variables → upstream named outputs → default output.
    // First occurrence wins (flow params shadow globals shadow upstream).
    function _buildScopeItems(nodeId) {
      const items = [];
      const seen = new Set();
      const push = (name, source, value) => {
        const n = (name || '').trim();
        if (n && !seen.has(n)) { seen.add(n); items.push({ name: n, source, value }); }
      };
      flowParams.forEach(r => push(r.key, 'Flow parameter', r.value));
      globalVars.forEach(r => push(r.key, 'Global variable', r.value));
      _collectUpstreamNames(nodeId).forEach(n => push(n, 'Upstream output'));
      push('output', 'Previous node');
      return items;
    }

    // ── Live canvas patch — receives CustomEvents from renderFlow('silent_patch') ──
    // Uses refs for all reads so the effect is stable with [] deps.
    // Temporarily inject a CSS transition on all existing RF node wrappers so they
    // slide smoothly when their positions change. Removed after SLIDE_MS.
    function _enableNodeSlide(ms) {
      const sheet = document.getElementById('_rf_slide_style') ||
        Object.assign(document.createElement('style'), { id: '_rf_slide_style' });
      sheet.textContent =
        `.react-flow__node { transition: transform ${ms}ms cubic-bezier(0.4,0,0.2,1) !important; }`;
      document.head.appendChild(sheet);
      setTimeout(() => sheet.textContent = '', ms + 50);
    }

    // Returns true when the caller should skip the generic server reconcile
    // (insert_node carries the full final state in its payload).
    function _applyLivePatch(op, payload) {
      if (op === 'add_node') {
        const n = payload.node || {};
        const rfNode = {
          id: n.id, type: n.type || 'task_llm',
          position: n.position || { x: 0, y: 250 },
          ...(_nodeDims(n.type)),
          data: { ...n, _type: n.type, _scopeId: null, _entering: true },
        };
        setRFNodes(prev => [...prev, rfNode]);
        requestAnimationFrame(() => requestAnimationFrame(() => {
          setRFNodes(prev => prev.map(nd =>
            nd.id === rfNode.id ? { ...nd, data: { ...nd.data, _entering: false } } : nd
          ));
        }));

      } else if (op === 'remove_node') {
        const { node_id } = payload;
        setRFNodes(prev => prev.map(nd =>
          nd.id === node_id ? { ...nd, data: { ...nd.data, _leaving: true } } : nd
        ));
        setTimeout(() => {
          setRFNodes(prev => prev.filter(nd => nd.id !== node_id));
          setRFEdges(prev => prev.filter(e => e.source !== node_id && e.target !== node_id));
        }, 280);

      } else if (op === 'update_node') {
        const { node_id, updates } = payload;
        setRFNodes(prev => prev.map(nd =>
          nd.id === node_id ? { ...nd, data: { ...nd.data, ...updates, _updated: true } } : nd
        ));
        setTimeout(() => {
          setRFNodes(prev => prev.map(nd =>
            nd.id === node_id ? { ...nd, data: { ...nd.data, _updated: false } } : nd
          ));
        }, 650);

      } else if (op === 'add_edge') {
        const e = payload.edge || {};
        // Route through _toRFEdges so decision branches get their colour-coded
        // True/False pill immediately (not just after a page refresh).
        const rfEdge = { ..._toRFEdges([e])[0], className: 'flow-edge--entering' };
        setRFEdges(prev => [...prev, rfEdge]);
        requestAnimationFrame(() => requestAnimationFrame(() => {
          setRFEdges(prev => prev.map(ed =>
            ed.id === rfEdge.id ? { ...ed, className: '' } : ed
          ));
        }));

      } else if (op === 'remove_edge') {
        setRFEdges(prev => prev.filter(e => e.id !== payload.edge_id));

      } else if (op === 'insert_node') {
        // ── World-class choreographed insert ─────────────────────────────────
        // node_positions: server's _auto_layout result for every node.
        // all_edges:      final edge list from server.
        // Sequence:
        //   T=0    Remove old edge (instant — it's gone before eyes register)
        //   T=0    Enable 380ms CSS slide on all existing node wrappers
        //   T=0    Move existing nodes to their new _auto_layout positions (they slide)
        //   T=0    Insert new node at EXACT correct position, _entering=true (scale=0)
        //   T=dRAF Remove _entering → spring scale-in to full size
        //   T=200  Add new edges with spark animation (after node is visible)
        //   T=380  Clean up slide CSS
        // No reconcile needed — we already have the final state.
        const { node, removed_edge_id, edge1, edge2, node_positions = {}, all_edges } = payload;
        const SLIDE_MS = 380;

        const mkRFEdge = e => ({ ..._toRFEdges([e])[0], className: 'flow-edge--entering' });

        // T=0: remove old edge
        setRFEdges(prev => prev.filter(e => e.id !== removed_edge_id));

        // T=0: slide existing nodes to their new layout positions
        if (Object.keys(node_positions).length > 0) {
          _enableNodeSlide(SLIDE_MS);
          setRFNodes(prev => prev.map(nd => {
            const p = node_positions[nd.id];
            return p ? { ...nd, position: p } : nd;
          }));
        }

        // T=0: insert new node at its correct position, spring-enter from scale=0
        const correctPos = node_positions[node.id] || { x: 0, y: 250 };
        const rfNode = {
          id: node.id, type: node.type || 'task_llm',
          position: correctPos,
          ...(_nodeDims(node.type)),
          data: { ...node, _type: node.type, _scopeId: null, _entering: true },
        };
        setRFNodes(prev => [...prev, rfNode]);

        // T=dRAF: trigger spring-in transition
        requestAnimationFrame(() => requestAnimationFrame(() => {
          setRFNodes(prev => prev.map(nd =>
            nd.id === rfNode.id ? { ...nd, data: { ...nd.data, _entering: false } } : nd
          ));
        }));

        // T=200ms: spark new edges in (after node is visually present)
        const newEdges = [edge1, edge2].filter(Boolean).map(mkRFEdge);
        setTimeout(() => {
          setRFEdges(prev => [...prev, ...newEdges]);
          requestAnimationFrame(() => requestAnimationFrame(() => {
            setRFEdges(prev => prev.map(ed =>
              newEdges.some(ne => ne.id === ed.id) ? { ...ed, className: '' } : ed
            ));
          }));
        }, 200);

        // T=450ms: fitView after slide + spring-in have both settled
        setTimeout(() => {
          rfInstanceRef.current?.fitView({ padding: 0.2, duration: 300 });
        }, 450);

        return true; // signal: skip server reconcile, we have final state
      }

      return false;
    }

    useEffect(() => {
      // Module-level debounce timer — one server reconcile per burst of patch events.
      // Declared outside React state so it survives re-renders.
      let _reconcileTimer = null;

      function onPatchEvent(ev) {
        const { flow_id, op, payload } = ev.detail || {};
        _dbg(`flow:patch received  op=${op}  flow=${flow_id?.slice(-6)}  mine=${currentFlowRef.current?.flow_id?.slice(-6)}`);
        if (!op || flow_id !== currentFlowRef.current?.flow_id) {
          _dbg('  → IGNORED (flow_id mismatch or missing op)');
          return;
        }
        _dbg(`  → applying ${op}`);

        // 1. Optimistic local animation (instant feedback).
        //    Returns true when the patch carries full final state (insert_node).
        const skipReconcile = _applyLivePatch(op, payload);

        // 1b. When auto-layout is ON, the server has already re-laid-out the graph and
        //     the patch event carries the authoritative definition (ev.detail.flow).
        //     Apply those positions immediately (slide) so the canvas matches the
        //     server layout right away — without waiting for the debounced reconcile
        //     or a manual Layout click. (insert_node handles its own positions.)
        const freshNodes = ev.detail.flow?.definition?.nodes;
        if (!skipReconcile && autoLayoutEnabledRef.current && Array.isArray(freshNodes) && freshNodes.length) {
          const posMap = {};
          freshNodes.forEach(n => { if (n.position) posMap[n.id] = n.position; });
          _enableNodeSlide(320);
          setRFNodes(prev => prev.map(n => (posMap[n.id] ? { ...n, position: posMap[n.id] } : n)));
          setTimeout(() => rfInstanceRef.current?.fitView({ padding: 0.2, duration: 300 }), 360);
        }

        // 2. Debounced server reconcile — fires 600 ms after the LAST patch in the burst.
        //    Normally skipped for insert_node (its per-op choreography carries the
        //    final state) — BUT that choreography doesn't always capture the server's
        //    authoritative layout (e.g. decision branch offsets), so when auto-layout
        //    is ON we ALWAYS reconcile to guarantee the canvas matches the stored
        //    layout without a manual Layout click.
        if (skipReconcile && !autoLayoutEnabledRef.current) return;
        if (_reconcileTimer) clearTimeout(_reconcileTimer);
        _reconcileTimer = setTimeout(async () => {
          _reconcileTimer = null;
          const fid = currentFlowRef.current?.flow_id;
          if (!fid) return;
          const token = localStorage.getItem('tda_auth_token');
          try {
            const r = await fetch(`/api/v1/flows/${fid}`,
              { headers: { 'Authorization': `Bearer ${token}` } });
            if (!r.ok) return;
            const freshFlow = await r.json();
            const def = freshFlow.definition || {};
            console.log(`[reconcile] final: nodes=${def.nodes?.length} edges=${def.edges?.length}`);
            // Slide to the authoritative server positions when auto-layout is on.
            if (autoLayoutEnabledRef.current) _enableNodeSlide(320);
            setRFNodes(_toRFNodes(def.nodes, def.scopes));
            setRFEdges(_toRFEdges(def.edges));
            setCurrentFlow(freshFlow);
            _loadVersions(fid);
            if (autoLayoutEnabledRef.current) {
              setTimeout(() => rfInstanceRef.current?.fitView({ padding: 0.2, duration: 300 }), 360);
            }
          } catch (e) { console.warn('[reconcile] fetch failed', e); }
        }, 600);
      }
      UderiaBus.on('uderia:flow-patch', onPatchEvent);
      return () => {
        UderiaBus.off('uderia:flow-patch', onPatchEvent);
        if (_reconcileTimer) clearTimeout(_reconcileTimer);
      };
    }, []); // stable — all reads go through refs or stable setters

    function _handleRunEvent(type, payload) {
      if (type === 'flow_node_start') {
        _setNodeExecStatus(payload.node_id, 'running');
        // Animate incoming edges of the running node (marching ants)
        setRFEdges(eds => eds.map(e =>
          e.target === payload.node_id
            ? { ...e, animated: true, style: { ...e.style, stroke: '#38bdf8', strokeWidth: 2 } }
            : e
        ));
      } else if (type === 'flow_node_complete') {
        _setNodeExecStatus(payload.node_id, 'success');
        // Restore edges, briefly flash green
        setRFEdges(eds => eds.map(e =>
          e.target === payload.node_id
            ? { ...e, animated: false, style: { ...e.style, stroke: '#4ade80', strokeWidth: 2 } }
            : e
        ));
        // Fade edge back to normal after 800ms
        setTimeout(() => {
          setRFEdges(eds => eds.map(e =>
            e.target === payload.node_id
              ? { ...e, style: _edgeStyle(e.data?.type) }
              : e
          ));
        }, 800);
        if (payload.tokens_input != null) {
          setRFNodes(nds => nds.map(n => n.id === payload.node_id
            ? { ...n, data: { ...n.data, _tokensIn: payload.tokens_input, _tokensOut: payload.tokens_output } }
            : n));
        }
        // Accumulate per-node run stats for analytics overlay
        const stats = {};
        if (payload.duration_ms   != null) stats.ms   = payload.duration_ms;
        if (payload.tokens_input  != null) stats.tIn  = payload.tokens_input;
        if (payload.tokens_output != null) stats.tOut = payload.tokens_output;
        if (payload.row_count     != null) stats.rows = payload.row_count;
        if (Object.keys(stats).length)
          setNodeRunData(prev => ({ ...prev, [payload.node_id]: stats }));
        // Accumulate live cost (backend emits `cost_usd`, not `cost`)
        const _nodeCost = payload.cost_usd != null ? payload.cost_usd : payload.cost;
        if (_nodeCost != null)
          setLiveCost(prev => prev + (_nodeCost || 0));
        // Store LLM-backed extensions that ran inside this node — surfaced as a
        // distinct line on the node (and in the Inspector) with their own cost,
        // so an LLM extension is never an invisible cost folded into the node.
        const _exts = Array.isArray(payload.extensions) ? payload.extensions : [];
        if (_exts.length) {
          setRFNodes(nds => nds.map(n =>
            n.id === payload.node_id
              ? { ...n, data: { ...n.data, _extensions: _exts } }
              : n
          ));
        }
        if (payload.verdict) {
          setRFNodes(nds => nds.map(n => n.id === payload.node_id ? { ...n, data: { ...n.data, _verdict: payload.verdict } } : n));
        }
        if (payload.canvas_id) {
          setRFNodes(nds => nds.map(n => n.id === payload.node_id
            ? { ...n, data: { ...n.data, _canvasId: payload.canvas_id, _canvasVersion: payload.version } } : n));
        }
        // Store output preview on the node — shown in Inspector on click
        if (payload.output_preview) {
          setRFNodes(nds => nds.map(n =>
            n.id === payload.node_id
              ? { ...n, data: { ...n.data, _outputPreview: payload.output_preview, _errorMsg: null } }
              : n
          ));
        }
      } else if (type === 'flow_node_error') {
        _setNodeExecStatus(payload.node_id, 'error');
        setRFEdges(eds => eds.map(e =>
          e.target === payload.node_id
            ? { ...e, animated: false, style: { ...e.style, stroke: '#f87171', strokeWidth: 2 } }
            : e
        ));
        // Store error message on the node — shown in Inspector on click
        if (payload.error) {
          setRFNodes(nds => nds.map(n =>
            n.id === payload.node_id
              ? { ...n, data: { ...n.data, _errorMsg: payload.error, _outputPreview: null } }
              : n
          ));
        }
        // Remember the first failing node so the run-level error banner can offer
        // a one-click "retry from failed node" even if flow_run_error omits it.
        setRunState(r => ({
          ...r,
          failedNodeId: r.failedNodeId || payload.node_id || null,
          errorMsg: r.errorMsg || payload.error || null,
        }));
      } else if (type === 'flow_node_cancelled') {
        // Node abandoned because the run was cancelled — clear its spinner and
        // de-animate its incoming edges (not an error, no banner).
        _setNodeExecStatus(payload.node_id, 'cancelled');
        setRFEdges(eds => eds.map(e =>
          e.target === payload.node_id ? { ...e, animated: false, style: _edgeStyle(e.data?.type) } : e
        ));
      } else if (type === 'flow_scope_iteration') {
        // A loop entered a new round — the frame says which (words, not a bare count).
        setRFNodes(nds => nds.map(n => n.id === `scope-${payload.scope_id}`
          ? { ...n, data: { ...n.data, _round: payload.round || (payload.iteration + 1), _exitReason: null } } : n));
      } else if (type === 'flow_scope_complete') {
        setRFNodes(nds => nds.map(n => n.id === `scope-${payload.scope_id}`
          ? { ...n, data: { ...n.data, _exitReason: payload.exit_reason || 'max_rounds', _roundsDone: payload.iterations_completed } } : n));
      } else if (type === 'flow_run_start') {
        // Clear stale execution results from previous run
        setRFNodes(nds => nds.map(n => ({
          ...n, data: { ...n.data, _outputPreview: null, _errorMsg: null, _extensions: null,
                        _verdict: null, _canvasId: null, _round: null, _exitReason: null },
        })));
        setRunState(r => ({ ...r, status: 'running', failedNodeId: null, errorMsg: null }));
        setShownRun(null);
      } else if (type === 'flow_run_complete') {
        setRunState(r => ({ ...r, status: payload.status, tokensIn: payload.tokens_input || 0,
                             tokensOut: payload.tokens_output || 0 }));
        _setFlowRun({ status: payload.status });
        // Reset all edge animations
        setRFEdges(eds => eds.map(e => ({ ...e, animated: false, style: _edgeStyle(e.data?.type) })));
        // Reload the conversation canvas so new messages appear without a page refresh
        window.EventHandlers?.reloadCurrentSession?.();
      } else if (type === 'flow_run_error') {
        setRunState(r => ({
          ...r, status: 'error',
          failedNodeId: payload.node_id || r.failedNodeId || null,
          errorMsg: payload.error || r.errorMsg || null,
        }));
        _setFlowRun({ status: 'error' });
        setRFEdges(eds => eds.map(e => ({ ...e, animated: false, style: _edgeStyle(e.data?.type) })));
      }
    }

    function _setNodeExecStatus(nodeId, status) {
      setRFNodes(nds => nds.map(n => n.id === nodeId ? { ...n, data: { ...n.data, _execStatus: status } } : n));
    }

    // ── Graph mutation callbacks ────────────────────────────────────────────

    const onNodesChange = useCallback((changes) => {
      setRFNodes(nds => applyNodeChanges(changes, nds));
    }, []);

    const onEdgesChange = useCallback((changes) => {
      setRFEdges(eds => applyEdgeChanges(changes, eds));
    }, []);

    const onConnect = useCallback(async (params) => {
      const newEdge = {
        id:     `edge-${_uuidShort()}`,
        source: params.source,
        target: params.target,
        type:   'normal',
        condition: params.sourceHandle || undefined,
      };
      // Build the optimistic edge through _toRFEdges so it renders identically
      // to the post-refresh path — i.e. decision branches get their colour-coded
      // True/False pill immediately instead of only after a page reload.
      const rfEdge = _toRFEdges([newEdge])[0];
      if (params.targetHandle) rfEdge.targetHandle = params.targetHandle;
      setRFEdges(eds => addEdge(rfEdge, eds));
      await patchFlow({ op: 'add_edge', payload: { edge: newEdge } });
    }, []);

    const onNodeClick = useCallback((_, node) => {
      if (node.type === 'scope') return;
      setSelNode(node);
    }, []);

    const onPaneClick = useCallback(() => { setSelNode(null); setEdgeAddMenu(null); setAddEdgeHover(null); }, []);

    // ── Add node helper ─────────────────────────────────────────────────────

    async function addNode(type) {
      const id = `node-${_uuidShort()}`;
      const label = _addableLabel(type);
      const newNode = {
        id, type, label,
        position: { x: 100 + Math.random() * 200, y: 100 + Math.random() * 200 },
        data: _defaultNodeData(type),
      };
      setRFNodes(nds => [...nds, {
        id,
        type,
        position: newNode.position,
        data: { ...newNode, _type: type },
      }]);
      await patchFlow({ op: 'add_node', payload: { node: newNode } });
    }

    // ── addNodeAt: place node at explicit canvas position (used by DnD drop) ──

    // ── BFS auto-layout — vertical, exact match to backend constants ─────────
    // nodeOrigin=[0.5,0]: x=0 is the visual horizontal centre of the canvas.
    // Constants must stay in sync with flow_db.py _auto_layout:
    //   X_CENTER=0  X_STEP=220  Y_START=80  Y_STEP=160
    // Used by the toolbar Re-layout button and window.__flowDebug.layout().

    function _autoLayout(nodes, edges) {
      if (nodes.length <= 1) return nodes;
      const scopeNodes = nodes.filter(n => n.type === 'scope');
      const flowNodes  = nodes.filter(n => n.type !== 'scope');
      if (flowNodes.length === 0) return nodes;

      // Build adjacency + parent maps (skip cycle edges — match backend)
      const out = {}, parents = {}, inDeg = {};
      flowNodes.forEach(n => { out[n.id] = []; parents[n.id] = []; inDeg[n.id] = 0; });
      edges.forEach(e => {
        if (e.data?.type === 'cycle' || e.animated) return;
        if (out[e.source]     !== undefined) out[e.source].push(e.target);
        if (parents[e.target] !== undefined) parents[e.target].push(e.source);
        if (inDeg[e.target]   !== undefined) inDeg[e.target]++;
      });

      // Kahn's topological sort — rank = max(parent ranks) + 1 (matches backend)
      const rank  = {};
      const queue = flowNodes.filter(n => inDeg[n.id] === 0).map(n => n.id);
      queue.forEach(id => { rank[id] = 0; });
      let head = 0;
      while (head < queue.length) {
        const nid = queue[head++];
        rank[nid] = Math.max(-1, ...(parents[nid] || []).map(p => rank[p] ?? -1)) + 1;
        (out[nid] || []).forEach(child => {
          if (--inDeg[child] === 0) queue.push(child);
        });
      }
      // Unreachable nodes (cycles) → max rank + 1
      let maxRank = Math.max(0, ...Object.values(rank));
      flowNodes.forEach(n => { if (rank[n.id] === undefined) rank[n.id] = ++maxRank; });

      // Group by rank
      const byRank = {};
      flowNodes.forEach(n => { (byRank[rank[n.id]] = byRank[rank[n.id]] || []).push(n.id); });

      // Vertical layout — exact backend constants
      const X_CENTER = 0, X_STEP = 220, Y_START = 80, Y_STEP = 160;
      const posMap = {};
      Object.keys(byRank).sort((a, b) => +a - +b).forEach(r => {
        const ids  = byRank[r];
        const half = (ids.length - 1) * X_STEP / 2;
        ids.forEach((id, i) => {
          posMap[id] = { x: X_CENTER - half + i * X_STEP, y: Y_START + +r * Y_STEP };
        });
      });

      const laid = flowNodes.map(n => ({ ...n, position: posMap[n.id] || n.position }));
      return [...laid, ...scopeNodes];
    }

    // ── Power-user editing: selection, duplicate, copy/paste, bulk delete ────
    const _SEL_EXCLUDE = ['start', 'end', 'scope'];

    function _selectedNodeIds() {
      const sel = rfNodesRef.current.filter(n => n.selected && !_SEL_EXCLUDE.includes(n.type)).map(n => n.id);
      if (sel.length) return sel;
      // Fall back to the inspector's single selection.
      const s = selNodeRef.current;
      return (s && !_SEL_EXCLUDE.includes(s.type)) ? [s.id] : [];
    }

    function _toDefNode(rfNode) {
      return { type: rfNode.type, label: rfNode.data?.label || rfNode.type,
               position: rfNode.position || { x: 0, y: 0 }, data: { ...(rfNode.data?.data || {}) } };
    }

    // Snapshot a set of nodes + the edges entirely within that set.
    function _snapshotNodes(ids) {
      const set = new Set(ids);
      const nodes = rfNodesRef.current.filter(n => set.has(n.id)).map(n => ({ _srcId: n.id, ..._toDefNode(n) }));
      const edges = rfEdgesRef.current.filter(e => set.has(e.source) && set.has(e.target))
        .map(e => ({ _srcSource: e.source, _srcTarget: e.target,
                     type: e.data?.type || 'normal', condition: e.data?.condition }));
      return { nodes, edges };
    }

    // Re-create a snapshot as new nodes/edges, offset and selected. Preserves data.
    async function _instantiateSnapshot(snap, dx = 48, dy = 48) {
      if (!snap || !snap.nodes?.length) return;
      const idMap = {};
      const defNodes = snap.nodes.map(n => {
        const id = `node-${_uuidShort()}`;
        idMap[n._srcId] = id;
        return { id, type: n.type, label: n.label,
                 position: { x: (n.position?.x || 0) + dx, y: (n.position?.y || 0) + dy },
                 data: { ...(n.data || {}) } };
      });
      const defEdges = (snap.edges || []).map(e => ({
        id: `edge-${_uuidShort()}`, source: idMap[e._srcSource], target: idMap[e._srcTarget],
        type: e.type, condition: e.condition }));
      // Optimistic add (deselect originals, select the copies).
      setRFNodes(prev => [
        ...prev.map(n => ({ ...n, selected: false })),
        ...defNodes.map(dn => ({ id: dn.id, type: dn.type, position: dn.position, ..._nodeDims(dn.type),
          selected: true, data: { ...dn, _type: dn.type, _entering: true } })),
      ]);
      let last = null;
      for (const dn of defNodes) last = await patchFlow({ op: 'add_node', payload: { node: dn } });
      for (const ed of defEdges) last = await patchFlow({ op: 'add_edge', payload: { edge: ed } });
      if (last?.definition?.nodes) {
        const srvPos = Object.fromEntries(last.definition.nodes.map(n => [n.id, n.position]));
        _enableNodeSlide(340);
        setRFNodes(prev => prev.map(nd => srvPos[nd.id] ? { ...nd, position: srvPos[nd.id] } : nd));
      }
      requestAnimationFrame(() => requestAnimationFrame(() =>
        setRFNodes(prev => prev.map(n => n.data?._entering ? { ...n, data: { ...n.data, _entering: false } } : n))));
      setTimeout(() => rfInstanceRef.current?.fitView({ padding: 0.2, duration: 360 }), 410);
    }

    async function _duplicateNodes(ids) {
      const list = (ids && ids.length) ? ids : _selectedNodeIds();
      if (list.length) await _instantiateSnapshot(_snapshotNodes(list));
    }
    function _copySelection() {
      const ids = _selectedNodeIds();
      if (ids.length) _clipboard.current = _snapshotNodes(ids);
    }
    async function _pasteClipboard() {
      if (_clipboard.current) await _instantiateSnapshot(_clipboard.current);
    }

    async function _deleteNodes(ids) {
      const set = new Set((ids || []).filter(id => {
        const n = rfNodesRef.current.find(x => x.id === id);
        return n && !_SEL_EXCLUDE.includes(n.type);
      }));
      if (!set.size) return;
      setRFNodes(nds => nds.map(n => set.has(n.id) ? { ...n, data: { ...n.data, _leaving: true } } : n));
      setTimeout(() => {
        setRFNodes(nds => nds.filter(n => !set.has(n.id)));
        setRFEdges(eds => eds.filter(e => !set.has(e.source) && !set.has(e.target)));
      }, 280);
      if (set.has(selNodeRef.current?.id)) setSelNode(null);
      for (const id of set) await patchFlow({ op: 'remove_node', payload: { node_id: id } });
    }

    async function addNodeAt(type, position, insertEdgeId) {
      const id = `node-${_uuidShort()}`;
      const label = _addableLabel(type);

      if (insertEdgeId) {
        // ── Edge insertion: single atomic op → 1 backend snapshot ────────────
        // Server runs _auto_layout and returns node_positions so we can trigger
        // the choreographed world-class insert animation.
        const edge = rfEdges.find(e => e.id === insertEdgeId);
        if (!edge) return;
        const e1id = `edge-${_uuidShort()}`, e2id = `edge-${_uuidShort()}`;
        const srcNode = rfNodes.find(n => n.id === edge.source);
        const tgtNode = rfNodes.find(n => n.id === edge.target);
        const midPos  = (srcNode && tgtNode)
          ? { x: (srcNode.position.x + tgtNode.position.x) / 2,
              y: (srcNode.position.y + tgtNode.position.y) / 2 }
          : position;
        const newNode = { id, type, label, position: midPos, data: _defaultNodeData(type) };
        const edge1   = { id: e1id, source: edge.source, target: id,           type: 'normal' };
        const edge2   = { id: e2id, source: id,           target: edge.target, type: 'normal' };

        const result = await patchFlow({
          op: 'insert_node',
          payload: { node: newNode, removed_edge_id: insertEdgeId, edge1, edge2 },
        });
        if (result?.node_positions) {
          // Reuse the identical choreographed animation already built for TDA_Flow.
          _applyLivePatch('insert_node', {
            node: newNode, removed_edge_id: insertEdgeId, edge1, edge2,
            node_positions: result.node_positions,
            all_edges: result.all_edges || [],
          });
        }

      } else {
        // ── Plain drop: optimistic add at drop position, then slide to server layout ──
        const newNode = { id, type, label, position, data: _defaultNodeData(type) };
        const rfNode  = { id, type, position, ...(_nodeDims(type)),
                          data: { ...newNode, _type: type, _entering: true } };
        setRFNodes(prev => [...prev, rfNode]);
        requestAnimationFrame(() => requestAnimationFrame(() => {
          setRFNodes(prev => prev.map(nd =>
            nd.id === id ? { ...nd, data: { ...nd.data, _entering: false } } : nd
          ));
        }));
        setTimeout(() => rfInstanceRef.current?.fitView({ padding: 0.18, duration: 380 }), 150);

        const result = await patchFlow({ op: 'add_node', payload: { node: newNode } });
        if (result?.definition?.nodes) {
          // Slide all nodes to server-computed vertical layout positions.
          const srvPos = Object.fromEntries(result.definition.nodes.map(n => [n.id, n.position]));
          _enableNodeSlide(340);
          setRFNodes(prev => prev.map(nd => srvPos[nd.id] ? { ...nd, position: srvPos[nd.id] } : nd));
          setTimeout(() => rfInstanceRef.current?.fitView({ padding: 0.2, duration: 360 }), 410);
        }
      }
    }

    // ── isValidConnection: block invalid handle pairings ────────────────────

    const isValidConnection = useCallback((conn) => {
      if (conn.source === conn.target) return false;
      const src = rfNodes.find(n => n.id === conn.source);
      const tgt = rfNodes.find(n => n.id === conn.target);
      if (src?.type === 'end')   return false;
      if (tgt?.type === 'start') return false;
      return true;
    }, [rfNodes]);

    // ── onNodeDrag: edge-insertion hover detection ──────────────────────────

    const onNodeDrag = useCallback((_, node) => {
      const cx = node.position.x, cy = node.position.y;
      let closest = null, minDist = 80;
      rfEdges.forEach(e => {
        if (e.source === node.id || e.target === node.id) return;
        const src = rfNodes.find(n => n.id === e.source);
        const tgt = rfNodes.find(n => n.id === e.target);
        if (!src || !tgt) return;
        const mx = (src.position.x + tgt.position.x) / 2;
        const my = (src.position.y + tgt.position.y) / 2;
        const d = Math.hypot(cx - mx, cy - my);
        if (d < minDist) { minDist = d; closest = e.id; }
      });
      setHoveredEdgeId(closest);
    }, [rfEdges, rfNodes]);

    // ── onNodeDragStop: commit edge insertion if hovering ───────────────────

    const onNodeDragStop = useCallback(async (_, node) => {
      const eid = hoveredEdgeId;
      setHoveredEdgeId(null);
      if (!eid) return;
      const edge = rfEdges.find(e => e.id === eid);
      if (!edge) return;
      const e1id = `edge-${_uuidShort()}`, e2id = `edge-${_uuidShort()}`;
      // The first segment (source → inserted node) inherits the split edge's
      // type and branch condition, so inserting a node onto a decision's
      // True/False branch keeps that routing. The second segment is a plain
      // continuation to the original target.
      const edge1 = {
        id: e1id, source: edge.source, target: node.id,
        type: edge.data?.type || 'normal',
        condition: edge.data?.condition || undefined,
      };
      const edge2 = { id: e2id, source: node.id, target: edge.target, type: 'normal' };
      // Optimistic edge reconnection (instant visual feedback) — route through
      // _toRFEdges so an inherited branch keeps its colour-coded True/False pill.
      const mkRFEdge = e => ({ ..._toRFEdges([e])[0], className: 'flow-edge--entering' });
      setRFEdges([
        ...rfEdges.filter(e => e.id !== eid),
        mkRFEdge(edge1),
        mkRFEdge(edge2),
      ]);
      // Single atomic insert_node op → 1 snapshot, server-authoritative vertical layout.
      // Node already exists so backend skips the add; just reconnects edges + re-layouts.
      const nodeSnap = { id: node.id, type: node.type,
                         label: node.data?.label, position: node.position, data: {} };
      const result = await patchFlow({
        op: 'insert_node',
        payload: { node: nodeSnap, removed_edge_id: eid, edge1, edge2 },
      });
      if (result?.node_positions) {
        _enableNodeSlide(350);
        setRFNodes(prev => prev.map(nd =>
          result.node_positions[nd.id] ? { ...nd, position: result.node_positions[nd.id] } : nd
        ));
        setTimeout(() => rfInstanceRef.current?.fitView({ padding: 0.18, duration: 360 }), 420);
      } else {
        setTimeout(() => rfInstanceRef.current?.fitView({ padding: 0.18, duration: 360 }), 150);
      }
    }, [hoveredEdgeId, rfEdges]);

    // ── Context menu ────────────────────────────────────────────────────────

    const onNodeContextMenu = useCallback((e, node) => {
      e.preventDefault();
      e.stopPropagation();
      setCtxMenu({ x: e.clientX, y: e.clientY, node });
    }, []);

    const onPaneContextMenu = useCallback((e) => {
      e.preventDefault();
      setCtxMenu(null);
    }, []);

    // ── Inline rename (double-click a node's label) ─────────────────────────
    const onNodeDoubleClick = useCallback((e, node) => {
      if (node.type === 'start' || node.type === 'end' || node.type === 'scope') return;
      const el = document.querySelector(`.react-flow__node[data-id="${node.id}"]`);
      const rect = el ? el.getBoundingClientRect()
                      : { left: e.clientX, top: e.clientY, width: 160, height: 28 };
      setCtxMenu(null);
      setRenameState({ nodeId: node.id, value: node.data?.label || '',
                       x: rect.left, y: rect.top, w: Math.max(120, rect.width) });
    }, []);

    async function _commitRename() {
      if (!renameState) return;
      const { nodeId, value } = renameState;
      const label = (value || '').trim();
      setRenameState(null);
      if (!label) return;
      setRFNodes(nds => nds.map(n => n.id === nodeId ? { ...n, data: { ...n.data, label } } : n));
      await patchFlow({ op: 'update_node', payload: { node_id: nodeId, updates: { label } } });
    }

    // ── Edge deletion (hover the edge → × affordance) ───────────────────────
    async function _deleteEdge(edgeId) {
      setAddEdgeHover(null);
      setRFEdges(eds => eds.map(x => x.id === edgeId ? { ...x, className: 'flow-edge--leaving' } : x));
      setTimeout(() => setRFEdges(eds => eds.filter(x => x.id !== edgeId)), 220);
      await patchFlow({ op: 'remove_edge', payload: { edge_id: edgeId } });
    }

    // ── Edge hover tools: hovering an edge shows + (insert) and × (disconnect) ─
    const _edgeMidpointScreen = (edge) => {
      const s = document.querySelector(`.react-flow__node[data-id="${edge.source}"]`);
      const t = document.querySelector(`.react-flow__node[data-id="${edge.target}"]`);
      if (!s || !t) return null;
      const sr = s.getBoundingClientRect(), tr = t.getBoundingClientRect();
      return { x: (sr.left + sr.width / 2 + tr.left + tr.width / 2) / 2,
               y: (sr.top + sr.height / 2 + tr.top + tr.height / 2) / 2 };
    };
    const onEdgeMouseEnter = useCallback((e, edge) => {
      if (_edgeHoverTimer.current) { clearTimeout(_edgeHoverTimer.current); _edgeHoverTimer.current = null; }
      const mid = _edgeMidpointScreen(edge);
      if (mid) setAddEdgeHover({ edgeId: edge.id, x: mid.x, y: mid.y });
    }, []);
    const onEdgeMouseLeave = useCallback(() => {
      if (_edgeHoverTimer.current) clearTimeout(_edgeHoverTimer.current);
      _edgeHoverTimer.current = setTimeout(() => setAddEdgeHover(null), 260);
    }, []);
    async function _insertOnEdge(type, edgeId) {
      setEdgeAddMenu(null);
      setAddEdgeHover(null);
      await addNodeAt(type, null, edgeId);
    }

    // ── Canvas DnD drop handlers ────────────────────────────────────────────

    const onCanvasDragOver = useCallback((e) => {
      if (!e.dataTransfer.types.includes('application/flow-node-type')) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
      setIsDragOver(true);
      // Edge-insertion hover detection during palette drag
      if (rfInstanceRef.current && rfEdges.length > 0) {
        const pos = rfInstanceRef.current.screenToFlowPosition({ x: e.clientX, y: e.clientY });
        let closest = null, minDist = 120;
        rfEdges.forEach(edge => {
          const src = rfNodes.find(n => n.id === edge.source);
          const tgt = rfNodes.find(n => n.id === edge.target);
          if (!src || !tgt) return;
          const mx = (src.position.x + tgt.position.x) / 2;
          const my = (src.position.y + tgt.position.y) / 2;
          const d = Math.hypot(pos.x - mx, pos.y - my);
          if (d < minDist) { minDist = d; closest = edge.id; }
        });
        setHoveredEdgeId(closest);
      }
    }, [rfEdges, rfNodes]);

    const onCanvasDrop = useCallback((e) => {
      e.preventDefault();
      setIsDragOver(false);
      const nodeType = e.dataTransfer.getData('application/flow-node-type');
      if (!nodeType || !rfInstanceRef.current) return;
      const pos = rfInstanceRef.current.screenToFlowPosition({ x: e.clientX, y: e.clientY });
      const eid = hoveredEdgeId;
      setHoveredEdgeId(null);
      addNodeAt(nodeType, pos, eid || undefined);
    }, [hoveredEdgeId]);

    const onCanvasDragLeave = useCallback((e) => {
      if (!e.currentTarget.contains(e.relatedTarget)) { setIsDragOver(false); setHoveredEdgeId(null); }
    }, []);

    // ── Fix with AI: send error context to active session ───────────────────

    function _handleFixWithAI(node) {
      const sessionId = window.__currentSessionId || localStorage.getItem('currentSessionId');
      const token     = localStorage.getItem('tda_auth_token');
      const fid       = currentFlowRef.current?.flow_id;
      if (!sessionId || !token || !fid) return;
      const errInfo = node.data?._execError || 'Execution failed';
      const prompt  = `The flow node "${node.data?.label || node.id}" (type: ${node.type}, flow: ${fid}) ` +
                      `failed with: ${errInfo}. Please diagnose and fix it using TDA_Flow.`;
      fetch(`/api/v1/sessions/${sessionId}/query`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt }),
      }).catch(() => {});
    }

    // ── NL "AI Build" submission ────────────────────────────────────────────

    async function handleNLBuild(text, clearFn) {
      if (!text?.trim() || nlBuilding) return;
      setNlBuilding(true);
      const token     = localStorage.getItem('tda_auth_token');
      const sessionId = window.__currentSessionId || localStorage.getItem('currentSessionId');
      const fid       = currentFlowRef.current?.flow_id;
      if (!token || !sessionId || !fid) { setNlBuilding(false); return; }
      const prompt = `Build or extend the flow (flow_id: ${fid}) so that it does: ${text.trim()}. ` +
                     `Use TDA_Flow to add nodes and edges. Make each step a separate node.`;
      try {
        await fetch(`/api/v1/sessions/${sessionId}/query`, {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt }),
        });
        clearFn?.();
      } catch (_) {}
      setNlBuilding(false);
    }

    // ── window.__flowDebug: browser-console debug API ────────────────────────
    // Usage: window.__flowDebug.positions()  → node position table
    //        window.__flowDebug.edges()       → edge list table
    //        window.__flowDebug.layout()      → apply _autoLayout + before/after diff
    //        window.__flowDebug.validateGraph()→ detect orphans, missing start/end
    //        window.__flowDebug.runTests()    → layout algorithm test suite

    useEffect(() => {
      window.__flowDebug = {
        positions() {
          console.table(rfNodes.map(n => ({
            id: n.id, type: n.type,
            x: Math.round(n.position.x), y: Math.round(n.position.y),
            label: n.data?.label,
          })));
          return `${rfNodes.length} nodes`;
        },
        edges() {
          console.table(rfEdges.map(e => ({
            id: e.id, source: e.source, target: e.target,
            type: e.data?.type || e.type,
          })));
          return `${rfEdges.length} edges`;
        },
        layout(apply = true) {
          if (apply) {
            handleLayout();
            return 'Server layout requested (Sugiyāma–Barycenter via /layout endpoint)';
          }
          // Dry-run: client-side preview only (no server call)
          const before = rfNodes.map(n => ({ id: n.id, x: n.position.x, y: n.position.y }));
          const after  = _autoLayout(rfNodes, rfEdges);
          console.group('[FlowDebug] Layout preview (client-side, no server call)');
          console.table(before.map((b, i) => ({
            id:       b.id,
            type:     rfNodes[i]?.type,
            before_x: Math.round(b.x),  before_y: Math.round(b.y),
            after_x:  Math.round(after[i]?.position.x),
            after_y:  Math.round(after[i]?.position.y),
            Δx:       Math.round((after[i]?.position.x ?? b.x) - b.x),
            Δy:       Math.round((after[i]?.position.y ?? b.y) - b.y),
          })));
          console.groupEnd();
          return `Preview for ${after.length} nodes (pass apply=true to commit via server)`;
        },
        validateGraph() {
          const real    = rfNodes.filter(n => n.type !== 'scope');
          const inE     = {}, outE = {};
          rfEdges.forEach(e => {
            outE[e.source] = (outE[e.source] || 0) + 1;
            inE[e.target]  = (inE[e.target]  || 0) + 1;
          });
          const orphans = real.filter(n => !(inE[n.id] || outE[n.id]));
          const report  = {
            totalNodes:   real.length,
            totalEdges:   rfEdges.length,
            startNodes:   real.filter(n => n.type === 'start').length,
            endNodes:     real.filter(n => n.type === 'end').length,
            orphanNodes:  orphans.length,
          };
          console.log('[FlowDebug] Graph report:', report);
          if (orphans.length)
            console.warn('[FlowDebug] Orphan node IDs:', orphans.map(n => n.id));
          console.table(real.map(n => ({
            id: n.id, type: n.type,
            x: Math.round(n.position.x), y: Math.round(n.position.y),
            inEdges: inE[n.id] || 0, outEdges: outE[n.id] || 0,
          })));
          return report;
        },
        runTests() {
          console.group('[FlowDebug] Layout Test Suite');
          const laid = _autoLayout(rfNodes, rfEdges);

          // Test 1: No position overlaps
          const posKeys = laid.map(n => `${Math.round(n.position.x)},${Math.round(n.position.y)}`);
          const dupes   = posKeys.filter((k, i) => posKeys.indexOf(k) !== i);
          if (dupes.length) console.error('FAIL overlapping positions:', dupes);
          else              console.log('PASS no position overlaps');

          // Test 2: Vertical ordering (y increases with rank)
          const rows = {};
          laid.forEach(n => { const y = Math.round(n.position.y); (rows[y] = rows[y] || []).push(n.id); });
          console.log('Rank rows (y → nodes):', rows);

          // Test 3: Backend constant alignment
          const xs = laid.map(n => n.position.x), ys = laid.map(n => n.position.y);
          const firstY = Math.min(...ys);
          console.log(`Y_START check: first-row y=${firstY} (expect 80)`);
          if (laid.length > 1) {
            const sorted = [...laid].sort((a, b) => a.position.y - b.position.y);
            const yGap   = sorted[1]?.position.y - sorted[0]?.position.y;
            console.log(`Y_STEP check: gap between rows=${Math.round(yGap)} (expect 160 or multiple)`);
          }
          console.log(`X range: [${Math.min(...xs)}, ${Math.max(...xs)}]  Y range: [${Math.min(...ys)}, ${Math.max(...ys)}]`);
          console.groupEnd();
          return { rows: Object.keys(rows).length, overlaps: dupes.length };
        },
        fitView() {
          rfInstanceRef.current?.fitView({ padding: 0.2, duration: 400 });
        },
      };
      return () => { delete window.__flowDebug; };
    }, [rfNodes, rfEdges]);

    // ── Keyboard shortcuts (panel-focused guard) ────────────────────────────

    useEffect(() => {
      selNodeRef.current  = selNode;
    }, [selNode]);

    useEffect(() => {
      rfEdgesRef.current = rfEdges;
    }, [rfEdges]);
    useEffect(() => {
      rfNodesRef.current = rfNodes;
    }, [rfNodes]);

    useEffect(() => {
      runStateRef.current = runState;
    }, [runState]);

    // Close options menu when clicking outside the anchor
    useEffect(() => {
      if (!showOptionsMenu) return;
      const handler = (e) => {
        if (!e.target.closest?.('.flow-options-anchor') && !e.target.closest?.('.flow-options-menu')) setShowOptionsMenu(false);
      };
      const onKey = (e) => { if (e.key === 'Escape') setShowOptionsMenu(false); };
      // The menu is fixed-positioned from the button's rect at open time — close it
      // when the layout moves under it rather than leave it floating detached.
      const onMove = () => setShowOptionsMenu(false);
      document.addEventListener('mousedown', handler);
      document.addEventListener('keydown', onKey);
      window.addEventListener('resize', onMove);
      return () => {
        document.removeEventListener('mousedown', handler);
        document.removeEventListener('keydown', onKey);
        window.removeEventListener('resize', onMove);
      };
    }, [showOptionsMenu]);

    useEffect(() => {
      const root = document.querySelector('.flow-root');
      if (!root) return;
      const onEnter = () => { panelActiveRef.current = true; };
      const onLeave = () => { panelActiveRef.current = false; };
      root.addEventListener('mouseenter', onEnter);
      root.addEventListener('mouseleave', onLeave);
      return () => {
        root.removeEventListener('mouseenter', onEnter);
        root.removeEventListener('mouseleave', onLeave);
      };
    }, []);

    useEffect(() => {
      function onKey(e) {
        if (!panelActiveRef.current) return;
        const tag = (e.target?.tagName || '').toLowerCase();
        if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
        const isMac = /mac/i.test(navigator.platform || '');
        const ctrl  = isMac ? e.metaKey : e.ctrlKey;
        const node  = selNodeRef.current;
        if (ctrl && (e.key === 'z' || e.key === 'Z')) {
          e.preventDefault();
          if (e.shiftKey) _redo(); else _undo();
          return;
        }
        if (ctrl && (e.key === 'y' || e.key === 'Y')) { e.preventDefault(); _redo(); return; }
        if (ctrl && (e.key === 'k' || e.key === 'K')) { e.preventDefault(); setCmdPalette(c => c ? null : { query: '' }); return; }
        if (!ctrl && e.key === '?') { e.preventDefault(); setShowHelp(s => !s); return; }
        if (ctrl && (e.key === 'd' || e.key === 'D')) { e.preventDefault(); _duplicateNodes(); return; }
        if (ctrl && (e.key === 'c' || e.key === 'C')) { e.preventDefault(); _copySelection(); return; }
        if (ctrl && (e.key === 'v' || e.key === 'V')) { e.preventDefault(); _pasteClipboard(); return; }
        if ((e.key === 'Delete' || e.key === 'Backspace') && _selectedNodeIds().length > 1) {
          e.preventDefault(); _deleteNodes(_selectedNodeIds()); return;
        }
        if ((e.key === 'Delete' || e.key === 'Backspace') && node) {
          e.preventDefault();
          const deletedId = node.id;
          const edgeIdsBefore = new Set(rfEdgesRef.current.map(ed => ed.id));
          setRFNodes(nds => nds.map(x => x.id === deletedId ? { ...x, data: { ...x.data, _leaving: true } } : x));
          setTimeout(() => {
            setRFNodes(nds => nds.filter(x => x.id !== deletedId));
            setRFEdges(eds => eds.filter(e => e.source !== deletedId && e.target !== deletedId));
          }, 280);
          setSelNode(null);
          patchFlow({ op: 'remove_node', payload: { node_id: deletedId } }).then(data => {
            if (!data?.definition?.edges) return;
            const bridgeEdges = data.definition.edges.filter(e =>
              !edgeIdsBefore.has(e.id) && e.source !== deletedId && e.target !== deletedId);
            if (!bridgeEdges.length) return;
            const rfBridge = _toRFEdges(bridgeEdges).map(e => ({ ...e, className: 'flow-edge--entering' }));
            setTimeout(() => {
              setRFEdges(prev => {
                const seen = new Set(prev.map(e => e.id));
                return [...prev, ...rfBridge.filter(e => !seen.has(e.id))];
              });
              requestAnimationFrame(() => requestAnimationFrame(() => {
                setRFEdges(prev => prev.map(e => rfBridge.some(b => b.id === e.id) ? { ...e, className: '' } : e));
              }));
            }, 320);
          });
        } else if (e.key === 'Escape') {
          e.preventDefault();
          setSelNode(null); setShowHealthPanel(false); setVerDropOpen(false);
          setCtxMenu(null); setNlCmdOpen(false); setShowReadinessModal(false);
          setParamsPanelOpen(false); setEdgeAddMenu(null); setAddEdgeHover(null);
          setShowHelp(false); setCmdPalette(null); setFlowSelOpen(false); setLoopEdit(null);
        } else if (!ctrl && !e.altKey && ADDABLE_NODES.some(n => n.key === e.key)) {
          e.preventDefault(); addNode(ADDABLE_NODES.find(n => n.key === e.key).type);
        } else if (ctrl && e.shiftKey && e.key.toLowerCase() === 'f') {
          e.preventDefault(); rfInstanceRef.current?.fitView({ padding: 0.2, duration: 400 });
        } else if (ctrl && e.key === 'Enter') {
          e.preventDefault();
          if (hasNodesRef.current && !runStateRef.current?.runId) handleExecuteWithCheck();
        } else if (ctrl && e.key === 'a') {
          e.preventDefault();
          setRFNodes(nds => nds.map(n => ({ ...n, selected: true })));
        }
      }
      document.addEventListener('keydown', onKey);
      return () => document.removeEventListener('keydown', onKey);
    }, []); // stable — all reads via refs

    // ── Context menu outside-click dismiss ──────────────────────────────────

    useEffect(() => {
      if (!ctxMenu) return;
      const close = () => setCtxMenu(null);
      document.addEventListener('pointerdown', close);
      return () => document.removeEventListener('pointerdown', close);
    }, [!!ctxMenu]);

    // Close the edge "+" node-type picker on any outside pointerdown.
    useEffect(() => {
      if (!edgeAddMenu) return;
      const close = (e) => { if (!e.target.closest?.('.flow-ctx-menu') && !e.target.closest?.('.flow-edge-add')) { setEdgeAddMenu(null); setAddEdgeHover(null); } };
      document.addEventListener('pointerdown', close);
      return () => document.removeEventListener('pointerdown', close);
    }, [!!edgeAddMenu]);

    // ── flow:fix-node CustomEvent → _handleFixWithAI ─────────────────────

    useEffect(() => {
      function onFixNode(e) {
        const { node: nodeData } = e.detail || {};
        if (nodeData) _handleFixWithAI({ data: nodeData, type: nodeData._type || 'task_llm', position: { x: 0, y: 0 } });
      }
      UderiaBus.on('uderia:flow-fix-node', onFixNode);
      return () => UderiaBus.off('uderia:flow-fix-node', onFixNode);
    }, []);

    // ── flow:node-extensions-save → persist extension slots ───────────────

    useEffect(() => {
      function onExtSave(e) {
        const { node_id, extensions } = e.detail || {};
        if (!node_id || !Array.isArray(extensions)) return;
        setRFNodes(nds => nds.map(n => n.id !== node_id ? n : {
          ...n, data: { ...n.data, data: { ...(n.data.data || {}), extensions } },
        }));
        patchFlow({ op: 'update_node', payload: { node_id, updates: { data: { extensions } } } });
      }
      UderiaBus.on('uderia:flow-node-extensions-save', onExtSave);
      return () => UderiaBus.off('uderia:flow-node-extensions-save', onExtSave);
    }, []);

    // ── Analytics overlay: sync nodeRunData into rfNodes data ──────────────

    useEffect(() => {
      if (showAnalytics) {
        setRFNodes(nds => nds.map(n => ({
          ...n, data: { ...n.data, _runStats: nodeRunData[n.id] || null, _showAnalytics: true },
        })));
      } else {
        setRFNodes(nds => nds.map(n => {
          const { _runStats, _showAnalytics, ...rest } = n.data;
          return { ...n, data: rest };
        }));
      }
      // The overlay changes each node's rendered height. Tell ReactFlow to re-measure
      // after the DOM updates so the source/target handles (and their edges) snap to
      // the card's new edge — otherwise edges connect at the old height and visually
      // "leak" into the taller node.
      const inst = rfInstanceRef.current;
      if (inst?.updateNodeInternals) {
        requestAnimationFrame(() => requestAnimationFrame(() => {
          try { inst.updateNodeInternals(rfNodesRef.current.map(n => n.id)); } catch (_) {}
        }));
      }
    }, [showAnalytics]);

    // ── graphIssues: live graph validation ──────────────────────────────────

    const graphIssues = useMemo(() => {
      const real = rfNodes.filter(n => n.type !== 'scope');
      if (real.length === 0) return [];
      const issues = [];
      const inE = {}, outE = {};
      rfEdges.forEach(e => {
        outE[e.source] = (outE[e.source] || 0) + 1;
        inE[e.target]  = (inE[e.target]  || 0) + 1;
      });
      if (!real.some(n => n.type === 'start'))
        issues.push({ level: 'error', msg: 'No Start node', nodeId: null });
      if (!real.some(n => n.type === 'end'))
        issues.push({ level: 'error', msg: 'No End node', nodeId: null });
      real.forEach(n => {
        if (n.type === 'start' || n.type === 'end') return;
        if (!(inE[n.id] || outE[n.id]))
          issues.push({ level: 'error', msg: `Isolated: "${n.data?.label || n.id}"`, nodeId: n.id });
        if (n.type === 'decision' && (outE[n.id] || 0) < 2)
          issues.push({ level: 'warning', msg: `Decision "${n.data?.label || n.id}" needs 2 branches`, nodeId: n.id });
        if (n.type === 'task_llm' && !(n.data?.data?.prompt || n.data?.prompt || '').trim())
          issues.push({ level: 'warning', msg: `LLM "${n.data?.label || n.id}" has no prompt`, nodeId: n.id });
        // PROP-010: a File input with no file cannot run — say so before the click
        if (n.type === 'file_input' && !(n.data?.data?.file_id || '').trim())
          issues.push({ level: 'error', msg: `File input "${n.data?.label || n.id}" has no file chosen`, nodeId: n.id });
        // PROP-007 §4.2: an open REQUIRED slot is an error — the executor refuses the run before
        // the profile cascade can fall back, so the canvas must say so before the click.
        const slotRow = n.data?.data?.slot || n.data?.slot;
        if (slotRow && _slotIsOpen(n.data?.data || n.data || {}, profiles))
          issues.push({ level: 'error', slot: true,
                        msg: `Slot "${n.data?.label || n.id}" (${slotRow.role || 'role'}) has no agent`, nodeId: n.id });
      });
      // Unknown $reference detection — flag $names that resolve to nothing at run time.
      const paramSet = new Set([...flowParams, ...globalVars].map(r => (r.key || '').trim()).filter(Boolean));
      const adj = {};
      rfEdges.forEach(e => { (adj[e.target] = adj[e.target] || []).push(e.source); });
      const byId = {};
      real.forEach(n => { byId[n.id] = n; });
      const ancestorsOf = (id) => {
        const seen = new Set(); const stack = [id];
        while (stack.length) {
          const c = stack.pop();
          (adj[c] || []).forEach(s => { if (!seen.has(s)) { seen.add(s); stack.push(s); } });
        }
        return seen;
      };
      real.forEach(n => {
        const d = n.data?.data || {};
        const text = [d.prompt, d.expression, d.condition].filter(Boolean).join('\n');
        if (!text || text.indexOf('$') === -1) return;
        const scope = new Set(paramSet);
        scope.add('output');
        ancestorsOf(n.id).forEach(aid => {
          const oa = (byId[aid]?.data?.data || {}).output_as;
          if (oa && oa.trim()) scope.add(oa.trim());
        });
        const unknown = _unknownDollarRefs(text, scope);
        if (unknown.length)
          issues.push({ level: 'warning', msg: `Unknown ${unknown.map(u => '$' + u).join(', ')} in "${n.data?.label || n.id}"`, nodeId: n.id });
      });
      return issues;
    }, [rfNodes, rfEdges, flowParams, globalVars, profiles]);

    // ── Execute with readiness check ────────────────────────────────────────

    async function handleExecuteWithCheck(opts = {}) {
      // Guard: when wired directly as an onClick, the arg is a DOM event — ignore it
      // so sessionTarget falls back to the default rather than reading event props.
      const runOpts = (opts && typeof opts === 'object' && 'sessionTarget' in opts) ? opts : {};
      const errors = graphIssues.filter(i => i.level === 'error');
      if (errors.length > 0) { setShowReadinessModal(true); return; }
      await handleExecute(runOpts);
    }

    // ── Health bar nested component ─────────────────────────────────────────

    function FlowHealthBar() {
      const real = rfNodes.filter(n => n.type !== 'scope');
      if (real.length === 0) return null;
      const errs  = graphIssues.filter(i => i.level === 'error');
      const warns = graphIssues.filter(i => i.level === 'warning');
      const ok    = graphIssues.length === 0;
      const pillColor  = ok ? '#4ade80' : errs.length ? '#f87171' : '#fbbf24';
      const pillBg     = ok ? 'rgba(74,222,128,0.1)'  : errs.length ? 'rgba(248,113,113,0.1)'  : 'rgba(251,191,36,0.1)';
      const pillBorder = ok ? 'rgba(74,222,128,0.3)'  : errs.length ? 'rgba(248,113,113,0.3)'  : 'rgba(251,191,36,0.3)';
      const label = ok ? '✓ Valid'
                       : errs.length ? `✗ ${errs.length} error${errs.length > 1 ? 's' : ''}`
                                     : `⚠ ${warns.length} warning${warns.length > 1 ? 's' : ''}`;
      return h('div', { className: 'flow-health-bar' },
        h('button', {
          className: 'flow-health-pill',
          style: { color: pillColor, background: pillBg, borderColor: pillBorder },
          onClick: () => setShowHealthPanel(o => !o),
        }, label),
        showHealthPanel && graphIssues.length > 0 && h('div', {
          className: 'flow-health-panel',
          onClick: e => e.stopPropagation(),
        },
          graphIssues.map((issue, i) => h('div', { key: i, className: 'flow-issue-row' },
            h('span', { style: { color: issue.level === 'error' ? '#f87171' : '#fbbf24', flexShrink: 0 } },
              issue.level === 'error' ? '✗' : '⚠'),
            h('span', { style: { flex: 1, color: 'var(--text-primary)', fontSize: '0.72rem' } }, issue.msg),
            issue.nodeId && h('button', {
              className: 'flow-issue-jump',
              onClick: () => {
                const n = rfNodes.find(x => x.id === issue.nodeId);
                if (n && rfInstanceRef.current) {
                  const dims = _nodeDims(n.type);
                  rfInstanceRef.current.fitBounds(
                    { x: n.position.x - dims.width / 2 - 40, y: n.position.y - 40,
                      width: dims.width + 80, height: dims.height + 80 },
                    { padding: 0.5, duration: 400 }
                  );
                }
                setShowHealthPanel(false);
              },
            }, 'Jump →'),
          )),
        ),
        ok && h('span', { className: 'flow-health-hint' },
          h('span', null, 'Drag nodes from the left · '),
          h('button', { className: 'flow-health-help', onClick: () => setShowHelp(true) }, 'Press ? for shortcuts')),
      );
    }

    // ── Context menu nested component ───────────────────────────────────────

    function FlowContextMenu() {
      if (!ctxMenu) return null;
      const { x, y, node } = ctxMenu;
      const ICON = (d) => h('svg', { width: 12, height: 12, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' }, ...d);
      const runnable = node.type === 'task_llm' || node.type === 'task_prompt' || node.type === 'task_tool' || node.type === 'task_action' || node.type === 'task_function' || node.type === 'decision' || node.type === 'judge' || node.type === 'artefact' || node.type === 'file_input' || node.type === 'file_output';
      const PLAY = (extra) => ICON([h('polygon', { points: '6 4 20 12 6 20 6 4' }), ...(extra || [])]);
      return h('div', { className: 'flow-ctx-menu', style: { top: y, left: x }, onClick: e => e.stopPropagation(), onPointerDown: e => e.stopPropagation() },
        h('div', { className: 'flow-ctx-item', onClick: () => { setSelNode(node); setCtxMenu(null); } },
          ICON([h('circle', { cx: 12, cy: 12, r: 3 }), h('path', { d: 'M19.07 4.93l-1.41 1.41M5.34 18.66l-1.41 1.41M22 12h-2M4 12H2M19.07 19.07l-1.41-1.41M5.34 5.34L3.93 3.93' })]),
          'Inspect',
        ),
        runnable && h(R.Fragment, null,
          h('div', { className: 'flow-ctx-separator' }),
          h('div', { className: 'flow-ctx-item', onClick: () => { handleExecute({ startNodeId: node.id, runMode: 'single' }); setCtxMenu(null); } },
            PLAY(), 'Run only this node',
          ),
          h('div', { className: 'flow-ctx-item', onClick: () => { handleExecute({ startNodeId: node.id, runMode: 'downstream' }); setCtxMenu(null); } },
            ICON([h('polygon', { points: '6 4 16 12 6 20 6 4' }), h('line', { x1: 19, y1: 5, x2: 19, y2: 19 })]),
            'Run from here →',
          ),
          h('div', { className: 'flow-ctx-separator' }),
        ),
        h('div', { className: 'flow-ctx-item', onClick: async () => {
          setCtxMenu(null);
          // Duplicate the multi-selection if this node is part of it, else just this node.
          const sel = rfNodes.filter(n => n.selected).map(n => n.id);
          await _duplicateNodes(sel.includes(node.id) && sel.length > 1 ? sel : [node.id]);
        }},
          ICON([h('rect', { x: 8, y: 8, width: 13, height: 13, rx: 2 }), h('path', { d: 'M4 16V4a2 2 0 0 1 2-2h12' })]),
          'Duplicate',
        ),
        runnable && !node.data?._scopeId && h('div', { className: 'flow-ctx-item', onClick: () => {
          setCtxMenu(null);
          const sel = _loopableIds(rfNodes);
          _makeLoop(sel.includes(node.id) && sel.length > 1 ? sel : [node.id]);
        }},
          ICON([h('path', { d: 'M3 12a9 9 0 0 1 15.5-6.3L21 8M21 3v5h-5M21 12a9 9 0 0 1-15.5 6.3L3 16M3 21v-5h5' })]),
          (() => { const sel = _loopableIds(rfNodes); return sel.includes(node.id) && sel.length > 1 ? `Make loop of ${sel.length} steps` : 'Repeat as a loop'; })(),
        ),
        node.data?._scopeId && h('div', { className: 'flow-ctx-item', onClick: () => { setCtxMenu(null); setLoopEdit(node.data._scopeId); } },
          ICON([h('path', { d: 'M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z' })]), 'Edit its loop'),
        h('div', { className: 'flow-ctx-separator' }),
        h('div', { className: 'flow-ctx-item flow-ctx-item--danger', onClick: async () => {
          const deletedId = node.id;
          const edgeIdsBefore = new Set(rfEdgesRef.current.map(ed => ed.id));
          setRFNodes(nds => nds.map(n => n.id === deletedId ? { ...n, data: { ...n.data, _leaving: true } } : n));
          setTimeout(() => {
            setRFNodes(nds => nds.filter(n => n.id !== deletedId));
            setRFEdges(eds => eds.filter(e => e.source !== deletedId && e.target !== deletedId));
          }, 280);
          if (selNode?.id === deletedId) setSelNode(null);
          setCtxMenu(null);
          const data = await patchFlow({ op: 'remove_node', payload: { node_id: deletedId } });
          if (!data?.definition?.edges) return;
          const bridgeEdges = data.definition.edges.filter(e =>
            !edgeIdsBefore.has(e.id) && e.source !== deletedId && e.target !== deletedId);
          if (!bridgeEdges.length) return;
          const rfBridge = _toRFEdges(bridgeEdges).map(e => ({ ...e, className: 'flow-edge--entering' }));
          setTimeout(() => {
            setRFEdges(prev => {
              const seen = new Set(prev.map(e => e.id));
              return [...prev, ...rfBridge.filter(e => !seen.has(e.id))];
            });
            requestAnimationFrame(() => requestAnimationFrame(() => {
              setRFEdges(prev => prev.map(e => rfBridge.some(b => b.id === e.id) ? { ...e, className: '' } : e));
            }));
          }, 320);
        }},
          ICON([h('polyline', { points: '3 6 5 6 21 6' }), h('path', { d: 'M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6' }), h('path', { d: 'M10 11v6M14 11v6' })]),
          'Delete node',
        ),
        node.data?._execStatus === 'error' && h(R.Fragment, null,
          h('div', { className: 'flow-ctx-separator' }),
          h('div', { className: 'flow-ctx-item flow-ctx-item--ai', onClick: () => { _handleFixWithAI(node); setCtxMenu(null); }},
            ICON([h('polygon', { points: '12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2' })]),
            '✦ Fix with AI',
          ),
        ),
      );
    }

    // ── Readiness modal nested component ────────────────────────────────────

    function FlowReadinessModal() {
      if (!showReadinessModal) return null;
      const errors = graphIssues.filter(i => i.level === 'error');
      const warns  = graphIssues.filter(i => i.level === 'warning');
      return h('div', { className: 'flow-readiness-overlay', onClick: () => setShowReadinessModal(false) },
        h('div', { className: 'flow-readiness-modal', onClick: e => e.stopPropagation() },
          h('div', { className: 'flow-readiness-title' },
            h('svg', { width: 16, height: 16, viewBox: '0 0 24 24', fill: 'none', stroke: '#f87171', strokeWidth: 2 },
              h('circle', { cx: 12, cy: 12, r: 10 }),
              h('line', { x1: 12, y1: 8, x2: 12, y2: 12 }),
              h('line', { x1: 12, y1: 16, x2: 12.01, y2: 16 }),
            ),
            errors.length ? 'Fix errors before running' : 'Warnings detected',
          ),
          h('div', { className: 'flow-readiness-list' },
            [...errors, ...warns].map((issue, i) => h('div', { key: i, style: { display: 'flex', gap: 6, alignItems: 'center', fontSize: '0.78rem' } },
              h('span', { style: { color: issue.level === 'error' ? '#f87171' : '#fbbf24', flexShrink: 0 } },
                issue.level === 'error' ? '✗' : '⚠'),
              h('span', { style: { color: 'var(--text-primary)' } }, issue.msg),
            )),
          ),
          h('div', { className: 'flow-readiness-actions' },
            h('button', { className: 'flow-btn', onClick: () => setShowReadinessModal(false) }, 'Fix issues'),
            warns.length > 0 && !errors.length && h('button', {
              className: 'flow-btn flow-btn-run',
              onClick: async () => { setShowReadinessModal(false); await handleExecute(); },
            }, 'Run anyway'),
          ),
        ),
      );
    }

    // ── Save node edits from inspector ──────────────────────────────────────

    async function handleInspectorSave(nodeId, updates) {
      setRFNodes(nds => nds.map(n => n.id === nodeId
        ? { ...n, data: { ...n.data, label: updates.label, data: { ...(n.data.data || {}), ...updates.data } } }
        : n));
      setSelNode(null);
      await patchFlow({ op: 'update_node', payload: { node_id: nodeId, updates } });
    }

    // ── Execute flow ────────────────────────────────────────────────────────

    async function handleExecute(opts = {}) {
      const { startNodeId = null, runMode = 'full' } = opts;
      // Session target follows the canvas selector (execMode) unless an explicit
      // override is passed — so EVERY run path honors the selected mode: the Run
      // button, "Run only this node", "Run from here", and retry-from-failed-node.
      const sessionTarget = opts.sessionTarget || (execMode === 'in_session' ? 'chat' : 'flow');
      const fid = currentFlowRef.current?.flow_id;
      const token = localStorage.getItem('tda_auth_token');
      if (!token || !fid) return;
      // Reset execution status on all nodes
      setRFNodes(nds => nds.map(n => ({ ...n, data: { ...n.data, _execStatus: null } })));
      setRunState({ status: 'queued', runId: null, tokensIn: 0, tokensOut: 0,
                    failedNodeId: null, errorMsg: null });
      setLiveCost(0);
      _setFlowRun({ runId: null, status: 'queued' });
      try {
        // sessionTarget: 'chat' → bind to the current conversation (result inline);
        // 'flow' → bind to the flow's own "<flow> — runs" session (independent run).
        const callerSessionId = sessionTarget === 'chat' ? (window.__currentSessionId || localStorage.getItem('currentSessionId') || null) : null;
        console.log(`[FlowExec] sessionTarget=${sessionTarget} → callerSessionId=${callerSessionId} nodeSessionMode=${nodeSessionMode} flow=${fid} startNode=${startNodeId} runMode=${runMode}`);
        const res = await fetch(`/api/v1/flows/${fid}/execute`, {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({
            input_params: {},
            session_id: callerSessionId,
            node_session_mode: nodeSessionMode,
            start_node_id: startNodeId,
            run_mode: runMode,
          }),
        });
        const d = await res.json();
        console.log('[FlowExec] server response:', d);
        if (d.run_id) {
          ownRunIdRef.current = d.run_id;
          setRunState(r => ({ ...r, runId: d.run_id }));
          _setFlowRun({ runId: d.run_id, status: 'running', flowId: fid });
        } else {
          console.error('[FlowExec] No run_id in response:', d);
          setRunState({ status: 'error', runId: null, tokensIn: 0, tokensOut: 0 });
          _setFlowRun({ status: 'error', runId: null });
        }
      } catch (err) {
        console.error('[FlowRenderer] Execute failed:', err);
        setRunState({ status: 'error', runId: null, tokensIn: 0, tokensOut: 0 });
        _setFlowRun({ status: 'error', runId: null });
      }
    }

    // ── Status bar label ────────────────────────────────────────────────────

    function _statusLabel() {
      const s = runState.status;
      if (!s) return 'Ready';
      return { queued: 'Queued', running: 'Running', complete: 'Complete',
               error: 'Error', cancelled: 'Cancelled' }[s] || s;
    }

    // ── Debug state ──────────────────────────────────────────────────────────
    const [debugLog, setDebugLog] = useState([]);
    const [debugVisible, setDebugVisible] = useState(false);

    function _dbg(msg) {
      const ts = new Date().toISOString().slice(11, 23);
      console.log(`[FlowCanvas] ${ts} ${msg}`);
      setDebugLog(prev => [...prev.slice(-19), `${ts}  ${msg}`]);
    }

    const hasNodes = rfNodes.filter(n => n.type !== 'scope').length > 0;
    // PROP-007: the open-slot count is read from graphIssues (ONE derivation — the health
    // panel, the Run gate and the pill cannot disagree about how many slots are open).
    const openSlotCount = graphIssues.filter(i => i.slot).length;
    // The Prompt node executes an MCP prompt (LLM-driven), which only makes sense
    // for an Optimize (tool_enabled) profile. Surface it only when the flow's
    // default profile is OPTIM-class. (A node can still override to another OPTIM
    // profile in its inspector.)
    const _defaultIsOptim = (profiles || []).some(p => p.id === defaultProfileId && p.profile_type === 'tool_enabled');
    // PROP-008 §6 — the Action node is offered only while OBDA-ACT is available to this user,
    // as /api/v1/obda/planes reports it.
    const [actOn, setActOn] = useState(false);
    useEffect(() => {
      let live = true;
      fetch('/api/v1/obda/planes', { headers: { Authorization: `Bearer ${localStorage.getItem('tda_auth_token')}` } })
        .then(r => (r.ok ? r.json() : null))
        .then(d => { if (live) setActOn(_actOnFromPlanes(d)); })
        .catch(() => { if (live) setActOn(false); });
      return () => { live = false; };
    }, []);
    // PROP-010 — the File nodes are offered only while Files is available to this user.
    const [filesOn, setFilesOn] = useState(false);
    useEffect(() => {
      let live = true;
      fetch('/api/v1/files?limit=1', { headers: { Authorization: `Bearer ${localStorage.getItem('tda_auth_token')}` } })
        .then(r => (r.ok ? r.json() : null))
        .then(d => { if (live) setFilesOn(!!(d && d.available)); })
        .catch(() => { if (live) setFilesOn(false); });
      return () => { live = false; };
    }, []);
    useEffect(() => { hasNodesRef.current = hasNodes; }, [hasNodes]);

    // displayEdges: rfEdges with hover-insertion highlight + path highlight applied
    const displayEdges = useMemo(() => {
      let edges = rfEdges;
      // Hover-insertion highlight (amber glow on drag-over edge)
      if (hoveredEdgeId) {
        edges = edges.map(e =>
          e.id === hoveredEdgeId
            ? { ...e, animated: true, style: { ...e.style, stroke: 'rgba(245,158,11,0.85)', strokeWidth: 3 } }
            : e
        );
      }
      // Path highlight: dim edges not on the highlighted path
      if (pathHighlightEnabled && highlightedEdges) {
        edges = edges.map(e =>
          highlightedEdges.has(e.id)
            ? { ...e, style: { ...e.style, opacity: 1 } }
            : { ...e, style: { ...e.style, opacity: 0.12, transition: 'opacity 0.15s' } }
        );
      }
      return edges;
    }, [rfEdges, hoveredEdgeId, pathHighlightEnabled, highlightedEdges]);

    // displayNodes: rfNodes with path highlight opacity applied
    const displayNodes = useMemo(() => {
      // Stamp each task_llm node with its EFFECTIVE profile (its own, else the
      // inherited flow default) so the node can show the profile tag, coloured
      // by the profile's IFOC type colour. Then apply path-highlight dimming.
      const byId = {};
      (profiles || []).forEach(p => { byId[p.id] = p; });
      const dimming = pathHighlightEnabled && highlightedNodes;
      return rfNodes.map(n => {
        let nn = n;
        if (n.type === 'task_llm' || n.type === 'judge') {
          const own  = n.data?.data?.profile_id;
          const ownP = own ? byId[own] : null;                 // node's own profile, if it resolves
          const slot = n.data?.data?.slot && typeof n.data.data.slot === 'object' ? n.data.data.slot : null;
          // Fall back to the flow default when the node has no profile OR its
          // stored profile_id doesn't resolve (e.g. an LLM-hallucinated ID) —
          // EXCEPT on a slot (PROP-007 §4.2): a slot is a promise that THIS role runs
          // under an agent the consumer chose, so it never inherits; it is open.
          const p    = ownP || (!slot && defaultProfileId ? byId[defaultProfileId] : null);
          const slotOpen = !!slot && _slotIsOpen(n.data?.data || {}, profiles);
          nn = { ...n, data: { ...n.data,
            _profileTag:       p ? (p.tag || p.name) : null,
            _profileType:      p ? p.profile_type : null,
            _profileInherited: !ownP && !!p,                    // inherited if own didn't resolve
            _slot:             slot,
            _slotOpen:         slotOpen,
            _slotJudgePanel:   !!slot && own === 'judge_panel' && !slotOpen,
          } };
        }
        if (dimming) {
          const op = highlightedNodes.has(n.id) ? 1 : 0.18;
          nn = { ...nn, style: { ...nn.style, opacity: op, transition: 'opacity 0.15s' } };
        }
        return nn;
      });
    }, [rfNodes, profiles, defaultProfileId, pathHighlightEnabled, highlightedNodes]);

    // ── Version dropdown component ─────────────────────────────────────────
    function VersionDropdown() {
      if (versions.length < 1) return null;
      // Which version is on the canvas right now?
      // activeVersionId is set on restore; null means we're on the working state
      // (latest snapshot + any unsaved edits) → treat the most-recent entry as active.
      const activeId = activeVersionId || (versions.length > 0 ? [...versions].sort((a, b) =>
        new Date(b.created_at) - new Date(a.created_at))[0].version_id : null);

      // Position the dropdown as a fixed-position body portal so it escapes the
      // toolbar's `overflow: hidden` clip \u2014 otherwise it gets cut off on the left
      // when the flow window is narrow. Right-align under the History button, but
      // clamp into the viewport so the full panel is always visible.
      let dropStyle = null;
      if (verDropOpen && verBtnRef.current) {
        const rect = verBtnRef.current.getBoundingClientRect();
        const GAP = 4, MARGIN = 8;
        const width = Math.min(240, window.innerWidth - MARGIN * 2);
        let left = rect.right - width;                       // right-aligned to the button
        left = Math.max(MARGIN, Math.min(left, window.innerWidth - width - MARGIN));
        const maxHeight = Math.max(160, window.innerHeight - rect.bottom - GAP - MARGIN);
        dropStyle = {
          position: 'fixed', top: (rect.bottom + GAP) + 'px', left: left + 'px',
          right: 'auto', width: width + 'px', maxHeight: maxHeight + 'px',
        };
      }

      const dropdown = (verDropOpen && dropStyle && window.ReactDOM)
        ? window.ReactDOM.createPortal(
            h('div', { className: 'flow-ver-dropdown', ref: verDropRef, style: dropStyle,
              onClick: e => e.stopPropagation() },
              [...versions].sort((a, b) => new Date(b.created_at) - new Date(a.created_at)).map((v) => {
                const isActive = v.version_id === activeId;
                return h('div', { key: v.version_id,
                  className: `flow-ver-item${isActive ? ' flow-ver-item--current' : ''}` },
                  h('div', { className: 'flow-ver-item-label' },
                    h('span', { className: 'flow-ver-item-title' },
                      h('span', { className: 'flow-ver-item-num' }, `v${v.version_num}`),
                      ` ${v.label && !/^v\d+$/.test(v.label) ? v.label : 'Snapshot'}${isActive ? ' \u2713 active' : ''}`),
                    h('span', { className: 'flow-ver-item-time' },
                      new Date(v.created_at).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })),
                  ),
                  !isActive && h('button', {
                    className: 'flow-ver-item-restore',
                    onClick: () => handleRestoreVersion(v),
                  }, 'Restore'),
                );
              }),
            ),
            document.body)
        : null;

      return h('div', { className: 'flow-ver-wrapper' },
        h('button', {
          className: 'flow-ver-btn',
          ref: verBtnRef,
          onClick: e => { e.stopPropagation(); setVerDropOpen(o => !o); },
        },
          h('svg', { width: 16, height: 16, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' },
            h('path', { d: 'M3 3v5h5' }),
            h('path', { d: 'M3.05 13A9 9 0 1 0 6 5.3L3 8' }),
            h('path', { d: 'M12 7v5l4 2' }),
          ),
          'History',
          h('span', { className: 'pc-badge pc-badge--count pc-badge--info' }, versions.length),
        ),
        dropdown,
      );
    }

    // ── Params control (Flowise-style Flow State) ─────────────────────────────
    // Built as inline elements (not a component) so the text inputs keep focus
    // across re-renders. State lives on FlowApp; panel rendered in a body portal.
    // Renders one editable key/value section (used for both global vars and flow params).
    function _paramsSection(title, subtitle, rows, setRows, persist, addLabel) {
      const setRow = (idx, patch) => setRows(rs => rs.map((r, i) => i === idx ? { ...r, ...patch } : r));
      return h('div', { className: 'flow-params-section' },
        h('div', { className: 'flow-params-title' }, title),
        h('div', { className: 'flow-params-hint' }, subtitle),
        rows.length === 0
          ? h('div', { className: 'flow-params-empty' }, 'None yet.')
          : rows.map((row, idx) => h('div', { className: 'flow-params-row', key: idx },
              h('input', {
                className: 'flow-params-key', placeholder: 'name', value: row.key,
                onChange: e => setRow(idx, { key: e.target.value.replace(/[^A-Za-z0-9_]/g, '') }),
                onBlur: () => persist(rows),
              }),
              h('input', {
                className: 'flow-params-val', placeholder: 'value', value: row.value,
                onChange: e => setRow(idx, { value: e.target.value }),
                onBlur: () => persist(rows),
              }),
              h('button', {
                className: 'flow-params-del', title: 'Remove',
                onClick: () => { const next = rows.filter((_, i) => i !== idx); setRows(next); persist(next); },
              },
                h('svg', { width: 12, height: 12, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' },
                  h('line', { x1: 5, y1: 12, x2: 19, y2: 12 }))),
            )),
        h('button', {
          className: 'flow-params-add',
          onClick: () => setRows(rs => [...rs, { key: '', value: '' }]),
        },
          h('svg', { width: 12, height: 12, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' },
            h('line', { x1: 12, y1: 5, x2: 12, y2: 19 }), h('line', { x1: 5, y1: 12, x2: 19, y2: 12 })),
          addLabel),
      );
    }

    function buildParamsControl() {
      const count = flowParams.filter(r => (r.key || '').trim()).length
                  + globalVars.filter(r => (r.key || '').trim()).length;

      let panelStyle = null;
      if (paramsPanelOpen && paramsBtnRef.current) {
        const rect = paramsBtnRef.current.getBoundingClientRect();
        const GAP = 4, MARGIN = 8;
        const width = Math.min(340, window.innerWidth - MARGIN * 2);
        let left = rect.right - width;
        left = Math.max(MARGIN, Math.min(left, window.innerWidth - width - MARGIN));
        const maxHeight = Math.max(200, window.innerHeight - rect.bottom - GAP - MARGIN);
        panelStyle = {
          position: 'fixed', top: (rect.bottom + GAP) + 'px', left: left + 'px',
          right: 'auto', width: width + 'px', maxHeight: maxHeight + 'px',
        };
      }

      const panel = (paramsPanelOpen && panelStyle && window.ReactDOM)
        ? window.ReactDOM.createPortal(
            h('div', { className: 'flow-params-panel', ref: paramsPanelRef, style: panelStyle,
              onClick: e => e.stopPropagation(), onPointerDown: e => e.stopPropagation() },
              _paramsSection('Flow Parameters', ['Specific to this flow. Reference as ', h('code', null, '{name}'), ' or ', h('code', null, '$name'), '.'],
                flowParams, setFlowParams, _saveParams, 'Add parameter'),
              h('div', { className: 'flow-params-divider' }),
              _paramsSection('Global Variables', ['Shared across all your flows. Reference as ', h('code', null, '$name'), '. Not for secrets.'],
                globalVars, setGlobalVars, _saveGlobalVars, 'Add variable'),
            ),
            document.body)
        : null;

      return h('div', { className: 'flow-params-wrapper' },
        h('button', {
          className: 'flow-btn', ref: paramsBtnRef,
          title: 'Parameters & variables — values available to every node',
          style: { display: 'flex', alignItems: 'center', gap: 4 },
          onClick: e => { e.stopPropagation(); setParamsPanelOpen(o => !o); },
        },
          h('svg', { width: 13, height: 13, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' },
            h('line', { x1: 4, y1: 21, x2: 4, y2: 14 }), h('line', { x1: 4, y1: 10, x2: 4, y2: 3 }),
            h('line', { x1: 12, y1: 21, x2: 12, y2: 12 }), h('line', { x1: 12, y1: 8, x2: 12, y2: 3 }),
            h('line', { x1: 20, y1: 21, x2: 20, y2: 16 }), h('line', { x1: 20, y1: 12, x2: 20, y2: 3 }),
            h('line', { x1: 1, y1: 14, x2: 7, y2: 14 }), h('line', { x1: 9, y1: 8, x2: 15, y2: 8 }), h('line', { x1: 17, y1: 16, x2: 23, y2: 16 })),
          'Params',
          count > 0 && h('span', { className: 'flow-ver-badge' }, count),
        ),
        panel,
      );
    }

    // ── Help overlay (keyboard & gesture cheatsheet) ─────────────────────────
    function buildHelpOverlay() {
      if (!showHelp) return null;
      const groups = [
        ['Add nodes', [...ADDABLE_NODES.filter(n => n.key).map(n => [n.key, n.label]), ['Drag', 'From left palette']]],
        ['Edit', [['⌘Z / ⇧⌘Z', 'Undo / Redo'], ['⌘D', 'Duplicate'], ['⌘C / ⌘V', 'Copy / Paste'], ['Del', 'Delete selection'], ['Double-click', 'Rename node'], ['Shift + drag', 'Multi-select']]],
        ['Connections', [['Drag handle', 'Connect nodes'], ['Hover edge → +', 'Insert node'], ['Hover edge → ×', 'Disconnect']]],
        ['Run & navigate', [['⌘ Enter', 'Run flow'], ['Right-click → Run', 'Run node / from here'], ['⌘K', 'Command palette'], ['?', 'This help']]],
      ];
      return h('div', { className: 'flow-help-overlay', onClick: () => setShowHelp(false) },
        h('div', { className: 'flow-help-modal', onClick: e => e.stopPropagation() },
          h('div', { className: 'flow-help-head' },
            h('span', { className: 'flow-help-title' }, 'Keyboard & gestures'),
            h('button', { className: 'flow-inspector-close', onClick: () => setShowHelp(false) }, '×'),
          ),
          h('div', { className: 'flow-help-grid' },
            groups.map(([title, items], i) => h('div', { key: i, className: 'flow-help-group' },
              h('div', { className: 'flow-help-gtitle' }, title),
              items.map(([k, d], j) => h('div', { key: j, className: 'flow-help-row' },
                h('kbd', { className: 'flow-help-kbd' }, k),
                h('span', { className: 'flow-help-desc' }, d),
              )),
            )),
          ),
        ),
      );
    }

    // ── Command palette (⌘K) — every action by name ──────────────────────────
    function _paletteActions() {
      return [
        ..._addableNodes(_defaultIsOptim, actOn, filesOn).map(n => ({ label: `Add ${n.label} node`, run: () => addNode(n.type) })),
        { label: 'Make a loop from the selection', run: () => _makeLoop(_loopableIds(rfNodes)) },
        { label: 'Run flow', run: () => handleExecuteWithCheck() },   // uses the run-target selector
        { label: 'Duplicate selection', run: () => _duplicateNodes() },
        { label: 'Copy selection', run: () => _copySelection() },
        { label: 'Paste', run: () => _pasteClipboard() },
        { label: 'Undo', run: () => _undo() },
        { label: 'Redo', run: () => _redo() },
        { label: 'Auto-layout', run: () => handleLayout() },
        { label: 'Open parameters', run: () => setParamsPanelOpen(true) },
        { label: 'Show keyboard shortcuts', run: () => setShowHelp(true) },
      ];
    }
    function buildCommandPalette() {
      if (!cmdPalette) return null;
      const q = (cmdPalette.query || '').toLowerCase();
      const actions = _paletteActions().filter(a => a.label.toLowerCase().includes(q));
      const runAt = (i) => { const a = actions[i]; setCmdPalette(null); if (a) a.run(); };
      return h('div', { className: 'flow-cmd-overlay', onClick: () => setCmdPalette(null) },
        h('div', { className: 'flow-cmd-modal', onClick: e => e.stopPropagation() },
          h('input', {
            className: 'flow-cmd-input', placeholder: 'Type a command…', value: cmdPalette.query || '',
            ref: el => { if (el && document.activeElement !== el) el.focus(); },
            onChange: e => setCmdPalette({ query: e.target.value }),
            onKeyDown: e => {
              e.stopPropagation();
              if (e.key === 'Enter') { e.preventDefault(); runAt(0); }
              else if (e.key === 'Escape') { e.preventDefault(); setCmdPalette(null); }
            },
          }),
          h('div', { className: 'flow-cmd-list' },
            actions.length === 0
              ? h('div', { className: 'flow-cmd-empty' }, 'No matching command')
              : actions.map((a, i) => h('div', {
                  key: a.label, className: `flow-cmd-item${i === 0 ? ' active' : ''}`,
                  onClick: () => runAt(i),
                }, a.label)),
          ),
        ),
      );
    }

    // ── Contextual selection toolbar (appears on multi-select) ───────────────
    function buildSelectionToolbar() {
      const selected = rfNodes.filter(n => n.selected && !['start', 'end', 'scope'].includes(n.type));
      if (selected.length < 2) return null;
      const ids = selected.map(n => n.id);
      const ICON = (d) => h('svg', { width: 13, height: 13, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' }, ...d);
      return h('div', { className: 'flow-sel-toolbar' },
        h('span', { className: 'flow-sel-count' }, `${selected.length} selected`),
        h('div', { className: 'flow-sel-sep' }),
        h('button', { className: 'flow-sel-btn', title: 'Duplicate (⌘D)', onClick: () => _duplicateNodes(ids) },
          ICON([h('rect', { x: 8, y: 8, width: 13, height: 13, rx: 2 }), h('path', { d: 'M4 16V4a2 2 0 0 1 2-2h12' })]), 'Duplicate'),
        h('button', { className: 'flow-sel-btn', title: 'Copy (⌘C)', onClick: () => _copySelection() },
          ICON([h('rect', { x: 9, y: 9, width: 13, height: 13, rx: 2 }), h('path', { d: 'M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1' })]), 'Copy'),
        h('button', { className: 'flow-sel-btn', title: 'Repeat these steps as a loop (they must follow one another)', onClick: () => _makeLoop(ids) },
          ICON([h('path', { d: 'M3 12a9 9 0 0 1 15.5-6.3L21 8M21 3v5h-5M21 12a9 9 0 0 1-15.5 6.3L3 16M3 21v-5h5' })]), 'Make loop'),
        h('button', { className: 'flow-sel-btn flow-sel-btn--danger', title: 'Delete (Del)', onClick: () => _deleteNodes(ids) },
          ICON([h('polyline', { points: '3 6 5 6 21 6' }), h('path', { d: 'M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6' })]), 'Delete'),
      );
    }

    return h('div', { className: 'flow-root' },
      // ── Debug bar ──────────────────────────────────────────────────────────
      debugVisible && h('div', { className: 'flow-debug-bar' },
        h('div', { className: 'flow-debug-header' },
          h('span', { className: 'flow-debug-title' }, 'Debug'),
          h('span', { className: 'flow-debug-stats' },
            `flow=${currentFlow.flow_id?.slice(-6) || '?'}`,
            ' · ',
            `nodes=${rfNodes.filter(n=>n.type!=='scope').length}`,
            ' · ',
            `edges=${rfEdges.length}`,
            ' · ',
            `versions=${versions.length}`,
          ),
          h('button', {
            className: 'flow-debug-close',
            onClick: () => setDebugVisible(false),
          }, '✕'),
        ),
        h('div', { className: 'flow-debug-log' },
          debugLog.length === 0
            ? h('span', { style: { color: 'var(--text-muted)', fontStyle: 'italic' } }, 'no events yet')
            : [...debugLog].reverse().map((line, i) =>
                h('div', { key: i, className: 'flow-debug-line' }, line)
              ),
        ),
      ),
      // Toolbar (auto-hiding secondary toolbar — subHeaderAutohide.js)
      h('div', { className: 'flow-toolbar', 'data-sp-subheader': '' },
        // ── Flow selector (custom dropdown — native <select> popup can't be
        //    themed, so we render a styleguide-consistent portal list instead) ──
        (() => {
          // Position the dropdown as a fixed-position body portal so it escapes
          // the toolbar's `overflow: hidden` clip and stays inside the viewport.
          let dropStyle = null;
          if (flowSelOpen && flowSelBtnRef.current) {
            const rect = flowSelBtnRef.current.getBoundingClientRect();
            const GAP = 4, MARGIN = 8;
            const width = Math.max(rect.width, Math.min(240, window.innerWidth - MARGIN * 2));
            let left = Math.max(MARGIN, Math.min(rect.left, window.innerWidth - width - MARGIN));
            const maxHeight = Math.max(160, window.innerHeight - rect.bottom - GAP - MARGIN);
            dropStyle = {
              position: 'fixed', top: (rect.bottom + GAP) + 'px', left: left + 'px',
              right: 'auto', width: width + 'px', maxHeight: maxHeight + 'px',
            };
          }

          const items = allFlows.length === 0
            ? [{ flow_id: currentFlow.flow_id || '', name: currentFlow.name || 'Untitled Flow' }]
            : allFlows;

          const dropdown = (flowSelOpen && dropStyle && window.ReactDOM)
            ? window.ReactDOM.createPortal(
                h('div', { className: 'flow-ver-dropdown', ref: flowSelDropRef, style: dropStyle,
                  onClick: e => e.stopPropagation() },
                  items.map((f) => {
                    const isActive = f.flow_id === currentFlow.flow_id;
                    return h('div', { key: f.flow_id,
                      className: `flow-sel-item${isActive ? ' flow-sel-item--current' : ''}`,
                      onClick: () => { setFlowSelOpen(false); if (!isActive) switchFlow(f.flow_id); } },
                      h('span', { className: 'flow-sel-check' }, isActive ? '✓' : ''),
                      h('span', { className: 'flow-sel-name' }, f.name),
                    );
                  }),
                ),
                document.body)
            : null;

          return h('div', { className: 'flow-selector-wrapper' },
            h('button', {
              className: 'flow-selector',
              ref: flowSelBtnRef,
              title: currentFlow.name || 'Untitled Flow',
              onClick: e => { e.stopPropagation(); setFlowSelOpen(o => !o); },
            }, currentFlow.name || 'Untitled Flow'),
            dropdown,
          );
        })(),
        // ── Right side: all general flow options ─────────────────────────────
        h('div', {
            className: 'flow-toolbar-cluster',
            style: { display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center', marginLeft: 'auto', justifyContent: 'flex-end', minWidth: 0 },
          },
          // ── Undo / redo ───────────────────────────────────────────────────
          (() => {
            const canUndo = _undoIdx.current > 0;
            const canRedo = _undoIdx.current < _undoHist.current.length - 1;
            const _ = histTick; // subscribe to history changes for enabled state
            const btn = (title, enabled, onClick, dPath) => h('button', {
              className: 'flow-btn', title, disabled: !enabled,
              style: { padding: '3px 6px', opacity: enabled ? 1 : 0.4, cursor: enabled ? 'pointer' : 'default' },
              onClick: enabled ? onClick : undefined,
            },
              h('svg', { width: 14, height: 14, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' }, ...dPath));
            return h('div', { style: { display: 'flex', gap: 2 } },
              btn('Undo (⌘Z)', canUndo, _undo, [h('path', { d: 'M3 7v6h6' }), h('path', { d: 'M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6 2.3L3 13' })]),
              btn('Redo (⇧⌘Z)', canRedo, _redo, [h('path', { d: 'M21 7v6h-6' }), h('path', { d: 'M3 17a9 9 0 0 1 9-9 9 9 0 0 1 6 2.3L21 13' })]),
            );
          })(),
          buildParamsControl(),
          h(VersionDropdown),
          h('button', {
            className: 'flow-btn', title: 'Keyboard shortcuts & gestures (?)',
            style: { width: 26, padding: '3px 0', fontWeight: 700, justifyContent: 'center', gap: 0 },
            onClick: () => setShowHelp(true),
          }, '?'),
          // ── Layout controls ───────────────────────────────────────────────
          h('button', {
            className: 'flow-btn',
            title: 'Re-apply Sugiyama–Barycenter hierarchical layout now',
            onClick: handleLayout,
            style: { display: 'flex', alignItems: 'center', gap: 4 },
          },
            h('svg', { width: 13, height: 13, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' },
              h('circle', { cx: 12, cy: 5, r: 2 }),
              h('circle', { cx: 5, cy: 19, r: 2 }),
              h('circle', { cx: 19, cy: 19, r: 2 }),
              h('line', { x1: 12, y1: 7, x2: 5, y2: 17 }),
              h('line', { x1: 12, y1: 7, x2: 19, y2: 17 }),
            ),
            'Layout',
          ),
          h('div', { className: 'flow-seg' },
            h('button', {
              title: 'Auto-layout ON — rearrange graph automatically after every structural change',
              className: 'flow-seg-opt' + (autoLayoutEnabled ? ' is-active' : ''),
              onClick: () => { if (!autoLayoutEnabled) handleToggleAutoLayout(); },
            }, 'Auto'),
            h('button', {
              title: 'Auto-layout OFF — nodes stay where you place them; use Layout button to re-arrange',
              className: 'flow-seg-opt' + (!autoLayoutEnabled ? ' is-active' : ''),
              onClick: () => { if (autoLayoutEnabled) handleToggleAutoLayout(); },
            }, 'Off'),
          ),
          // ── Options menu ─────────────────────────────────────────────────
          h('div', { className: 'flow-options-anchor', style: { position: 'relative' } },
            h('button', {
              className: 'flow-btn',
              title: 'Flow intelligence options',
              'aria-haspopup': 'menu',
              'aria-expanded': showOptionsMenu ? 'true' : 'false',
              onClick: (e) => {
                const r = e.currentTarget.getBoundingClientRect();
                const W = 272, M = 8;
                setOptionsMenuPos({
                  top: r.bottom + 6,
                  left: Math.max(M, Math.min(r.right - W, window.innerWidth - W - M)),
                  maxHeight: Math.max(160, window.innerHeight - r.bottom - 6 - M),
                });
                setShowOptionsMenu(o => !o);
              },
              style: { display: 'flex', alignItems: 'center', gap: 4 },
            },
              h('svg', { width: 13, height: 13, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' },
                h('circle', { cx: 12, cy: 12, r: 3 }),
                h('path', { d: 'M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z' }),
              ),
              'Options',
            ),
            (showOptionsMenu && optionsMenuPos && window.ReactDOM) && window.ReactDOM.createPortal(h('div', {
              className: 'flow-options-menu', role: 'menu',
              style: {
                position: 'fixed', top: optionsMenuPos.top + 'px', left: optionsMenuPos.left + 'px',
                maxHeight: optionsMenuPos.maxHeight + 'px', overflowY: 'auto', zIndex: 1000,
                background: 'var(--card-bg, #1e293b)',
                border: '1px solid var(--border-primary, rgba(255,255,255,0.1))',
                borderRadius: 10, padding: '6px 0 8px', minWidth: 272,
                boxShadow: '0 8px 32px rgba(0,0,0,0.45)',
              },
              onClick: e => e.stopPropagation(),
            },
              h('div', { style: { padding: '4px 14px 8px', fontSize: '0.65rem', fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em' } },
                'Intelligence',
              ),
              // Reusable option row renderer
              ...[
                {
                  key: 'smartDelete', label: 'Smart delete', active: smartDeleteEnabled,
                  toggle: handleToggleSmartDelete,
                  hint: 'Auto-bridge predecessor → successor when deleting a pass-through node',
                },
                {
                  key: 'snapToGrid', label: 'Snap to grid', active: snapToGridEnabled,
                  toggle: handleToggleSnapToGrid,
                  hint: 'Snap nodes to a 20px grid while dragging',
                },
                {
                  key: 'pathHighlight', label: 'Path highlight', active: pathHighlightEnabled,
                  toggle: handleTogglePathHighlight,
                  hint: 'Dim unrelated nodes when hovering — reveals upstream and downstream paths',
                },
                {
                  key: 'showMinimap', label: 'Show minimap', active: miniMapOpen,
                  toggle: _toggleMiniMap,
                  hint: 'Show the navigator minimap (drag its bar to move it, corner to resize)',
                },
              ].map(({ key, label, active, toggle, hint }) =>
                h('div', {
                  key,
                  style: {
                    display: 'flex', alignItems: 'flex-start', gap: 10,
                    padding: '8px 14px', cursor: 'pointer', transition: 'background 0.1s',
                  },
                  onClick: toggle,
                  onMouseEnter: e => { e.currentTarget.style.background = 'var(--hover-bg-strong, rgba(255,255,255,0.05))'; },
                  onMouseLeave: e => { e.currentTarget.style.background = ''; },
                },
                  h('div', {
                    style: {
                      width: 18, height: 18, borderRadius: 4, border: '1.5px solid',
                      borderColor: active ? 'rgba(99,102,241,0.85)' : 'var(--border-primary, rgba(255,255,255,0.2))',
                      background: active ? 'rgba(99,102,241,0.28)' : 'transparent',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      flexShrink: 0, marginTop: 1, transition: 'all 0.15s',
                    },
                  },
                    active && h('svg', { width: 11, height: 11, viewBox: '0 0 24 24', fill: 'none', stroke: 'rgba(147,150,255,1)', strokeWidth: 3.5, strokeLinecap: 'round', strokeLinejoin: 'round' },
                      h('polyline', { points: '20 6 9 17 4 12' }),
                    ),
                  ),
                  h('div', null,
                    h('div', { style: { fontSize: '0.76rem', fontWeight: 600, color: 'var(--text-primary)', lineHeight: 1.3 } }, label),
                    h('div', { style: { fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: 2, lineHeight: 1.45 } }, hint),
                  ),
                )
              ),
            ), document.body),
          ),
          // ── Default profile (LLM nodes without their own profile inherit this) ─────
          // Opens the platform's shared agent picker (search + IFOC class pills — the
          // filter idiom of the Inspector and the topology picker); FlowSelect is the
          // fallback when the shared picker is not loaded.
          (profiles || []).length > 0 && _sharedAgentPicker() ? (() => {
            const cur = (profiles || []).find(p => p.id === defaultProfileId);
            const col = cur ? ((IFOC[cur.profile_type] || {}).color || '#94a3b8') : null;
            return h('button', {
              type: 'button', className: 'flow-default-agent-btn', 'aria-haspopup': 'listbox',
              'aria-label': cur ? `Default profile: @${cur.tag || cur.name} — change` : 'Choose a default profile',
              title: 'Default profile for LLM nodes that have no profile of their own — search and filter by class. Initialised to the profile that built the flow; per-node overrides win.',
              onClick: (e) => {
                const host = e.currentTarget.closest('.sp-panel') || e.currentTarget.closest('.flow-root') || e.currentTarget.parentElement;
                _openSharedAgentPicker(host, {
                  items: _agentPickerItems(profiles, null),
                  currentId: defaultProfileId || null,
                  emptyLabel: 'No default profile',
                  onPick: (id) => handleSetDefaultProfile(id || ''),
                });
              },
              style: {
                display: 'inline-flex', alignItems: 'center', gap: 6, maxWidth: '170px',
                fontSize: '0.78rem', padding: '0 8px', minHeight: 'var(--ctl-height, 28px)', borderRadius: 'var(--ctl-radius, 8px)',
                background: 'var(--card-bg)', color: cur ? 'var(--text-primary)' : 'var(--text-muted)', cursor: 'pointer',
                border: '1px solid var(--border-secondary, rgba(255,255,255,0.1))',
              },
            },
              col && h('span', { 'aria-hidden': 'true', style: { width: 8, height: 8, borderRadius: '50%', background: col, flexShrink: 0 } }),
              h('span', { style: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: col || undefined } },
                cur ? (cur.tag || cur.name || cur.id) : 'Default profile…'),
              h('svg', { width: 12, height: 12, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round', style: { flexShrink: 0, opacity: 0.6 }, 'aria-hidden': 'true' },
                h('polyline', { points: '6 9 12 15 18 9' })),
            );
          })() :
          h(FlowSelect, {
            title: 'Default profile for LLM nodes that have no profile of their own. Initialised to the profile that built the flow; per-node overrides win.',
            value: defaultProfileId || '',
            onChange: handleSetDefaultProfile,
            placeholder: 'Default profile…',
            wrapStyle: { display: 'inline-block', width: 'auto', maxWidth: '170px' },
            btnStyle: {
              // a menu-bar control: the bar's height, radius and type (as the MCP-bridge picker in the canvas)
              fontSize: '0.78rem', padding: '0 22px 0 8px', minHeight: 'var(--ctl-height, 28px)', borderRadius: 'var(--ctl-radius, 8px)',
              background: 'var(--card-bg)', color: 'var(--text-primary)',
              border: '1px solid var(--border-secondary, rgba(255,255,255,0.1))',
            },
            options: [
              { value: '', label: 'Default profile…' },
              ...(profiles || []).map(p => ({ value: p.id, label: `${p.tag || p.name || p.id}`, color: IFOC[p.profile_type] && IFOC[p.profile_type].color })),
            ],
          }),
          // ── Serialize | Parallelize (only when ≥2 LLM nodes can run in parallel) ─────
          _flowHasParallelLLMFanout(rfNodes, rfEdges) && h('div', {
            className: 'flow-seg',
            title: 'This flow fans out to multiple LLM nodes. Serialize: run them one at a time in one shared session (coherent, correct turn order). Parallelize: run them concurrently, each in its own session.',
          },
            h('button', {
              title: 'Run nodes one at a time in a shared session — coherent conversation, correct turn numbers',
              className: 'flow-seg-opt flow-seg-opt--warning' + (!parallelExecution ? ' is-active' : ''),
              onClick: () => handleSetParallelExecution(false),
            }, 'Serialize'),
            h('button', {
              title: 'Run parallel branches concurrently, each in its own session (faster; branches do not share chat history)',
              className: 'flow-seg-opt flow-seg-opt--success' + (parallelExecution ? ' is-active' : ''),
              onClick: () => handleSetParallelExecution(true),
            }, 'Parallelize'),
          ),
          // Node session sharing is no longer a separate control — the
          // Serialize|Parallelize toggle above is authoritative (serialize → one
          // shared session; parallelize → one session per node), which makes the
          // corrupting "shared + concurrent" combination unselectable.
          // ── Run target SELECTOR + single Run ────────────────────────────────
          // The selector is the run-target MODE that EVERY run action honors — the
          // Run button below, and the per-node "Run only this node" / "Run from here"
          // context actions (which call handleExecute without an explicit target, so
          // they pick up execMode). This is why it's a selector, not two run buttons:
          // a node-level run needs a target, and that target is the selected mode.
          //   • In Chat     → runs execute in the current chat session (inline result)
          //   • New Session → a fresh session per run, named after the flow
          // Serialize | Parallelize (above) stays the separate node-execution control.
          h('div', { className: 'flow-seg', 'data-pin': 1, style: { marginRight: '6px' } },
            h('button', {
              title: 'Runs execute in your current chat session — results appear in this conversation',
              className: 'flow-seg-opt flow-seg-opt--info' + (execMode === 'in_session' ? ' is-active' : ''),
              onClick: () => { _setFlowMode({ execMode: 'in_session' }); setExecMode('in_session'); },
            }, 'In Chat'),
            h('button', {
              title: 'Each run executes in a fresh session named after the flow (one per run)',
              className: 'flow-seg-opt' + (execMode === 'new' ? ' is-active' : ''),
              onClick: () => { _setFlowMode({ execMode: 'new' }); setExecMode('new'); },
            }, 'New Session'),
          ),
          (runState.status === 'running' || runState.status === 'queued') && runState.runId
            ? h('button', { 'data-pin': 1, className: 'flow-btn flow-btn-danger', onClick: async () => {
                const token = localStorage.getItem('tda_auth_token');
                if (!token || !runState.runId) {
                  setRunState({ status: null, runId: null, tokensIn: 0, tokensOut: 0 });
                  _setFlowRun({ status: null, runId: null });
                  return;
                }
                await fetch(`/api/v1/flows/runs/${runState.runId}/cancel`, {
                  method: 'POST', headers: { 'Authorization': `Bearer ${token}` },
                });
                setRunState(r => ({ ...r, status: 'cancelled' }));
                _setFlowRun({ status: 'cancelled', runId: null });
              } }, 'Cancel')
            : h('button', {
                'data-pin': 1,
                className: 'flow-btn flow-btn-run',
                title: execMode === 'in_session'
                  ? 'Run the flow in your current chat session'
                  : 'Run the flow in a fresh session named after the flow',
                onClick: handleExecuteWithCheck,   // no explicit target → uses the selector (execMode)
                // PROP-007: Run is withheld while a required slot is open — the executor
                // would refuse anyway (409 flow_slots_unfilled); the canvas says it first.
                disabled: !hasNodes || openSlotCount > 0,
                style: { opacity: (hasNodes && openSlotCount === 0) ? 1 : 0.4 },
              }, 'Run Flow'),
          openSlotCount > 0 && h('button', {
            'data-pin': 1,
            className: 'flow-slot-pill',
            'data-open-slots': openSlotCount,
            title: 'Each open slot names a role that still needs an agent. Click to list them.',
            onClick: () => setShowHealthPanel(true),
          }, `${openSlotCount} slot${openSlotCount === 1 ? '' : 's'} to fill`),
          // Analytics toggle button — visible after any run
          Object.keys(nodeRunData).length > 0 && h('button', {
            'data-pin': 1,
            className: `flow-btn${showAnalytics ? ' flow-btn-analytics-on' : ''}`,
            onClick: () => setShowAnalytics(o => !o),
            title: showAnalytics ? 'Hide execution stats overlay' : 'Show per-node execution stats',
            style: { marginLeft: 4 },
          },
            h('svg', { width: 13, height: 13, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' },
              h('line', { x1: 18, y1: 20, x2: 18, y2: 10 }),
              h('line', { x1: 12, y1: 20, x2: 12, y2: 4 }),
              h('line', { x1: 6,  y1: 20, x2: 6,  y2: 14 }),
            ),
          ),
        ),
      ),
      // Health bar — sits between toolbar and main content
      h(FlowHealthBar),
      // Run-level error banner — surfaces the failure message and offers a
      // one-click retry from the failed node (seeds upstream outputs from the
      // failed run) or a full re-run. Theme-aware; monochrome warning icon.
      runState.status === 'error' && (runState.errorMsg || runState.failedNodeId) && h('div', {
        style: {
          display: 'flex', alignItems: 'flex-start', gap: 8,
          margin: '6px 8px 0', padding: '8px 10px', borderRadius: 6,
          background: 'rgba(248,113,113,0.08)',
          border: '1px solid rgba(248,113,113,0.35)',
          color: 'var(--text-primary)', fontSize: '0.72rem', lineHeight: 1.4,
        },
      },
        h('svg', { width: 15, height: 15, viewBox: '0 0 24 24', fill: 'none',
          stroke: '#f87171', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round',
          style: { flexShrink: 0, marginTop: 1 } },
          h('path', { d: 'M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z' }),
          h('line', { x1: 12, y1: 9, x2: 12, y2: 13 }),
          h('line', { x1: 12, y1: 17, x2: 12.01, y2: 17 }),
        ),
        h('div', { style: { flex: 1, minWidth: 0 } },
          h('div', { style: { fontWeight: 600, color: '#f87171', marginBottom: 2 } },
            (() => {
              const fn = runState.failedNodeId
                ? (rfNodesRef.current?.find(n => n.id === runState.failedNodeId)?.data?.label || runState.failedNodeId)
                : null;
              return fn ? `Run failed at “${fn}”` : 'Run failed';
            })()),
          h('div', { style: { color: 'var(--text-muted)', wordBreak: 'break-word', whiteSpace: 'pre-wrap' } },
            runState.errorMsg || 'A node failed during execution.'),
          h('div', { style: { display: 'flex', gap: 6, marginTop: 6, flexWrap: 'wrap' } },
            runState.failedNodeId && h('button', {
              className: 'flow-btn flow-btn-run',
              title: 'Re-run the failed node and everything downstream, seeding the successful upstream outputs from the failed run',
              onClick: () => handleExecute({ startNodeId: runState.failedNodeId, runMode: 'downstream' }),
            }, 'Retry from failed node'),
            h('button', {
              className: 'flow-btn',
              title: 'Re-run the entire flow from the start',
              onClick: () => handleExecute(),
            }, 'Run full flow'),
            h('button', {
              className: 'flow-btn',
              title: 'Dismiss',
              onClick: () => setRunState(r => ({ ...r, errorMsg: null, failedNodeId: null })),
            }, 'Dismiss'),
          ),
        ),
      ),
      // Main area
      h('div', { className: 'flow-main' },
        // ── Node palette sidebar ──────────────────────────────────────────
        h('div', { className: 'flow-node-palette' },
          h('span', { className: 'flow-palette-label' }, 'Add'),
          ..._addableNodes(_defaultIsOptim, actOn, filesOn).map(n => {
            const color = NODE_COLORS[n.type] || 'var(--text-muted)';
            return h('button', {
              key: n.type,
              className: 'flow-palette-btn',
              'data-node-type': n.type,
              title: n.key ? `${n.title} (${n.key})` : n.title,
              'aria-label': n.title,
              draggable: true,
              onDragStart: e => { e.dataTransfer.setData('application/flow-node-type', n.type); e.dataTransfer.effectAllowed = 'copy'; },
              onClick: () => addNode(n.type),
              style: { borderColor: `color-mix(in srgb, ${color} 40%, transparent)`, color },
            },
              h('svg', { width: 14, height: 14, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' },
                ...n.icon.map(([tag, attrs], i) => h(tag, { key: i, ...attrs }))),
              n.short,
            );
          }),
        ),
        // React Flow canvas — drag-and-drop aware
        h('div', {
          className: `flow-canvas-area${isDragOver ? ' drag-over' : ''}`,
          onDragOver: onCanvasDragOver,
          onDrop: onCanvasDrop,
          onDragLeave: onCanvasDragLeave,
          style: { position: 'relative' },
        },
          !hasNodes && h('div', { className: 'flow-empty' },
            h('div', { className: 'flow-empty-title' }, 'Flow is empty'),
            h('div', { className: 'flow-empty-hint' },
              'Drag a node from the palette, use the buttons above, or describe the flow with AI Build.'),
          ),
          ReactFlow && h(ReactFlow, {
            nodes: displayNodes,
            edges: displayEdges,
            nodeTypes,
            edgeTypes: _staticEdgeTypes,
            onNodesChange,
            onEdgesChange,
            onConnect,
            onNodeClick,
            onPaneClick,
            onInit,
            onNodeDrag,
            onNodeDragStop,
            onNodeContextMenu,
            onPaneContextMenu,
            onNodeDoubleClick,
            onEdgeMouseEnter,
            onEdgeMouseLeave,
            connectionLineComponent: FlowConnectionLine,
            isValidConnection,
            snapToGrid: snapToGridEnabled,
            snapGrid: [20, 20],
            onNodeMouseEnter,
            onNodeMouseLeave,
            selectionOnDrag: false,
            panOnScroll: true,
            zoomOnDoubleClick: false,  // double-click is used for rename / edge-disconnect
            // fitView is handled via onInit (delayed) to avoid running before the container
            // has its final dimensions after the display:none→flex panel transition.
            defaultViewport: { x: 0, y: 0, zoom: 0.8 },
            // Render edges above (unselected) nodes so arrowheads — especially the
            // colour-coded True/False branch markers — sit on top of the node's
            // 90%-opaque background instead of being hidden/darkened behind it.
            defaultEdgeOptions: { zIndex: 1 },
            // nodeOrigin=[0.5,0]: position.x is the horizontal CENTER of each node.
            // All nodes at x=0 are perfectly center-aligned regardless of their width.
            nodeOrigin: [0.5, 0],
            // Hide the "React Flow" attribution badge (the small grey link in the
            // bottom-right that opens reactflow.dev). Supported by the free build.
            proOptions: { hideAttribution: true },
          },
            Controls && h(Controls),
            // Minimap navigator — the ENTIRE widget (bar included) renders only
            // when enabled. Toggle via Options → "Show minimap" or the bar's hide
            // (×) button; when off, nothing shows on the canvas. Movable (drag the
            // bar) + resizable (corner grip).
            MiniMap && miniMapOpen && h('div', {
              className: 'flow-minimap-wrap',
              style: Object.assign(
                { position: 'absolute', zIndex: 6, display: 'flex', flexDirection: 'column',
                  borderRadius: 8, overflow: 'hidden',
                  border: '1px solid var(--border-primary)', background: 'var(--card-bg)',
                  boxShadow: '0 4px 14px rgba(0,0,0,0.25)' },
                miniMapPos
                  ? { left: miniMapPos.left + 'px', top: miniMapPos.top + 'px' }
                  : { right: '12px', bottom: '12px' },
              ),
            },
              h('div', {
                ref: miniBarRef,
                onMouseDown: _miniMapDragStart,
                title: 'Drag to move',
                style: { display: 'flex', alignItems: 'center', gap: 6, cursor: 'move',
                         padding: '3px 6px', userSelect: 'none',
                         borderBottom: '1px solid var(--border-secondary, rgba(255,255,255,0.08))' },
              },
                h('svg', { width: 12, height: 12, viewBox: '0 0 24 24', fill: 'none', stroke: 'var(--text-muted)', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' },
                  h('polygon', { points: '1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6' }),
                  h('line', { x1: 8, y1: 2, x2: 8, y2: 18 }),
                  h('line', { x1: 16, y1: 6, x2: 16, y2: 22 }),
                ),
                h('span', { style: { fontSize: '0.66rem', color: 'var(--text-muted)', fontWeight: 600, marginRight: 'auto' } }, 'Map'),
                h('button', {
                  onMouseDown: e => e.stopPropagation(),
                  onClick: e => { e.stopPropagation(); _toggleMiniMap(); },
                  title: 'Hide minimap',
                  style: { background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', display: 'flex', padding: 0 },
                },
                  h('svg', { width: 12, height: 12, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' },
                    h('line', { x1: 18, y1: 6, x2: 6, y2: 18 }),
                    h('line', { x1: 6, y1: 6, x2: 18, y2: 18 }),
                  ),
                ),
              ),
              h(MiniMap, {
                nodeColor: n => NODE_COLORS[n.type] || '#64748b',
                maskColor: 'rgba(0,0,0,0.4)', pannable: true, zoomable: true,
                style: { position: 'relative', margin: 0, top: 'auto', left: 'auto',
                         right: 'auto', bottom: 'auto', width: miniMapSize.w, height: miniMapSize.h },
              }),
              // Resize grip on the free corner (top-left when docked, bottom-right when moved).
              h('div', {
                onMouseDown: _miniMapResizeStart,
                title: 'Drag to resize',
                style: Object.assign(
                  { position: 'absolute', width: 14, height: 14, zIndex: 7, cursor: 'nwse-resize',
                    display: 'flex', alignItems: 'center', justifyContent: 'center' },
                  miniMapPos ? { right: 1, bottom: 1 } : { left: 1, top: 26 },
                ),
              },
                h('svg', { width: 12, height: 12, viewBox: '0 0 24 24', fill: 'none', stroke: 'var(--text-muted)', strokeWidth: 2.2, strokeLinecap: 'round' },
                  miniMapPos
                    ? h(R.Fragment, null, h('line', { x1: 21, y1: 9, x2: 9, y2: 21 }), h('line', { x1: 21, y1: 16, x2: 16, y2: 21 }))
                    : h(R.Fragment, null, h('line', { x1: 3, y1: 15, x2: 15, y2: 3 }), h('line', { x1: 3, y1: 8, x2: 8, y2: 3 }))),
              ),
            ),
            Background && h(Background, { variant: 'dots', gap: 20, size: 1 }),
            // Re-registers handle bounds once nodes are initialized — fixes edges
            // not rendering on cold load (handles mount after node measurement).
            _hasRFHooks && h(HandleBoundsFixer),
          ),
          // Contextual selection toolbar — floats at top-center on multi-select
          buildSelectionToolbar(),
          // AI Build wand — floating over canvas
          h(FlowNLWand, { nlBuilding, onSubmit: handleNLBuild }),
          // Readiness modal — centred over canvas
          h(FlowReadinessModal),
        ),
        // Inspector panel
        h('div', { className: `flow-inspector ${selNodeLive ? 'open' : ''}` },
          selNodeLive && h(Inspector, {
            // Key by node id so React REMOUNTS the inspector when a different
            // node is selected — its form fields seed from useState initialisers
            // that only run on mount, so without this they keep the previous
            // node's values (only prop-driven elements would refresh).
            key: selNodeLive.id,
            node: selNodeLive,
            profiles,
            toolList,
            defaultProfileId,
            scopeItems: _buildScopeItems(selNodeLive.id),
            nodeData: nodeDataMap[selNodeLive.id] || null,
            onRunNode: (mode) => handleExecute({ startNodeId: selNodeLive.id, runMode: mode || 'single' }),
            onClose: () => setSelNode(null),
            onSave: handleInspectorSave,
          }),
        ),
      ),
      // Status bar
      h('div', { className: 'flow-status-bar' },
        h('div', {
          className: `flow-status-dot ${runState.status || ''}`,
          style: { background: STATUS_COLORS[runState.status || 'idle'] },
        }),
        h('span', { className: 'flow-status-text' }, _statusLabel()),
        runState.runId && h('span', {
          style: { color: 'var(--text-muted)', fontSize: '0.65rem' },
        }, `Run: ${runState.runId.slice(0, 12)}`),
        (runState.tokensIn > 0 || runState.tokensOut > 0) && h('span', { className: 'flow-status-tokens' },
          `${runState.tokensIn.toLocaleString()}↑ ${runState.tokensOut.toLocaleString()}↓ tokens`),
        liveCost > 0 && h('span', { className: 'flow-cost-meter' }, `$${liveCost.toFixed(4)}`),
        buildRunsControl(),
      ),
      // Context menu — rendered at root level (fixed position)
      h(FlowContextMenu),
      // Help overlay + command palette
      buildHelpOverlay(),
      buildLoopEditor(),
      buildCommandPalette(),
      // Inline rename editor — floats over the double-clicked node
      renameState && h('input', {
        className: 'flow-rename-input',
        style: { position: 'fixed', left: renameState.x + 'px', top: renameState.y + 'px', width: renameState.w + 'px' },
        value: renameState.value,
        onChange: e => setRenameState(s => ({ ...s, value: e.target.value })),
        onKeyDown: e => {
          e.stopPropagation();
          if (e.key === 'Enter') { e.preventDefault(); _commitRename(); }
          else if (e.key === 'Escape') { e.preventDefault(); setRenameState(null); }
        },
        onBlur: () => _commitRename(),
        ref: el => { if (el && document.activeElement !== el) { el.focus(); el.select(); } },
      }),
      // Edge hover tools — + (insert) and × (disconnect) at the midpoint of a hovered edge
      addEdgeHover && !edgeAddMenu && h('div', {
        className: 'flow-edge-tools',
        style: { position: 'fixed', left: (addEdgeHover.x - 25) + 'px', top: (addEdgeHover.y - 11) + 'px' },
        onMouseEnter: () => { if (_edgeHoverTimer.current) { clearTimeout(_edgeHoverTimer.current); _edgeHoverTimer.current = null; } },
        onMouseLeave: () => { _edgeHoverTimer.current = setTimeout(() => setAddEdgeHover(null), 260); },
      },
        h('button', {
          className: 'flow-edge-add', title: 'Insert a node on this connection',
          onClick: () => setEdgeAddMenu({ edgeId: addEdgeHover.edgeId, x: addEdgeHover.x, y: addEdgeHover.y }),
        },
          h('svg', { width: 12, height: 12, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2.5, strokeLinecap: 'round' },
            h('line', { x1: 12, y1: 5, x2: 12, y2: 19 }), h('line', { x1: 5, y1: 12, x2: 19, y2: 12 }))),
        h('button', {
          className: 'flow-edge-del', title: 'Disconnect',
          onClick: () => _deleteEdge(addEdgeHover.edgeId),
        },
          h('svg', { width: 12, height: 12, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2.5, strokeLinecap: 'round' },
            h('line', { x1: 6, y1: 6, x2: 18, y2: 18 }), h('line', { x1: 18, y1: 6, x2: 6, y2: 18 }))),
      ),
      // Edge "+" node-type picker
      edgeAddMenu && h('div', {
        className: 'flow-ctx-menu', style: { position: 'fixed', top: edgeAddMenu.y + 'px', left: edgeAddMenu.x + 'px' },
        onClick: e => e.stopPropagation(), onPointerDown: e => e.stopPropagation(),
      },
        _addableNodes(_defaultIsOptim, actOn, filesOn).map(({ type: t, label: lbl }) =>
          h('div', { key: t, className: 'flow-ctx-item', onClick: () => _insertOnEdge(t, edgeAddMenu.edgeId) }, lbl)),
      ),
    );
  }

  return FlowApp;
}

// ── Renderer registry for mounted React roots ─────────────────────────────────

const _roots = new Map();  // containerId → ReactDOM root

// ── State ─────────────────────────────────────────────────────────────────────

let _activeSpec = null;
let _isFlowFullscreen = false;
let _sessionPanelWasExpanded = false;  // track sidebar state for restore on close
// Monotonic mount token — defensive coalescing of duplicate/concurrent
// openFlowSplitPanel() calls into a single React mount. On a full session load two
// independent paths can open the panel (renderFlow()'s auto-open when the chat
// history re-renders the flow's inline card with __canvasSplitMode on, and
// sessionManagement._restoreFlowState()); both run `await _loadDeps()` then
// createRoot() on the same #flow-canvas-root. Each open bumps this token
// synchronously; a scheduled mount aborts if a newer open superseded it, so only
// the latest mount reaches createRoot() — preventing a wasteful double-mount.
// NOTE: this is hardening, NOT the fix for edges-missing-on-cold-load — that is
// HandleBoundsFixer (handle bounds were never registered after the handles mounted).
let _mountSeq = 0;

let _welcomeScreenGuard = null;        // MutationObserver that re-hides welcome screen while canvas is open

// ── Fullscreen toggle (private) ───────────────────────────────────────────────

function _toggleFlowFullscreen() {
  const mainArea = document.getElementById('main-content-area');
  if (!mainArea) return;

  _isFlowFullscreen = !_isFlowFullscreen;

  if (_isFlowFullscreen) {
    const topNav = document.querySelector('body > nav');
    const topOffset = topNav ? topNav.offsetHeight : 0;
    document.documentElement.style.setProperty('--flow-fullscreen-top', topOffset + 'px');
    mainArea.classList.add('flow-fullscreen');
  } else {
    mainArea.classList.remove('flow-fullscreen');
    document.documentElement.style.removeProperty('--flow-fullscreen-top');
  }

  const fsBtn = document.getElementById('flow-split-fullscreen');
  if (fsBtn) fsBtn.title = _isFlowFullscreen ? 'Exit fullscreen' : 'Fullscreen canvas';
}

// ── Public entry point (called by ComponentRendererRegistry from chat message) ─

/**
 * Render a flow spec into a container.
 * Canvas view renders a compact inline card with an "Open in Canvas" button.
 * List / run_status views render inline directly.
 *
 * @param {string} containerId - DOM element ID
 * @param {object|string} payload - ComponentRenderPayload.spec
 */
export function renderFlow(containerId, payload) {
  _injectCSS();
  const spec = typeof payload === 'string' ? JSON.parse(payload) : payload;
  if (!spec) return;

  const container = document.getElementById(containerId);
  if (!container) return;

  if (spec.view === 'list') { _renderFlowList(containerId, spec); return; }
  if (spec.view === 'run_status') { _renderRunStatus(containerId, spec); return; }

  // Internal LLM actions — collapse the container, nothing shown in chat.
  if (spec.view === 'silent_get' || spec.view === 'silent_list' || spec.view === 'silent_error') {
    if (container) {
      container.style.cssText = 'display:none!important;height:0;overflow:hidden;margin:0;padding:0;';
      const parent = container.parentElement;
      // only the framework's own wrapper (.response-card / .ans-silent) — never the message
      if (parent && parent !== document.body && (parent.classList.contains('response-card') || parent.classList.contains('ans-silent')))
        parent.style.cssText = 'display:none!important;height:0;overflow:hidden;margin:0;padding:0;';
    }
    return;
  }

  if (spec.view === 'silent_patch') {
    // Fire a live-update event so the open canvas updates in-place — no chat card.
    const fid = spec.flow?.flow_id;
    const op  = spec.patch?.op;
    console.log(`[FlowRenderer] silent_patch  fid=${fid?.slice(-6)}  op=${op}  _activeSpec.fid=${_activeSpec?.flow?.flow_id?.slice(-6)}`);
    if (fid && spec.patch) {
      const detail = { flow_id: fid, op: spec.patch.op, payload: spec.patch.payload, flow: spec.flow };
      console.log(`[FlowRenderer] dispatching uderia:flow-patch`, detail);
      UderiaBus.emit('uderia:flow-patch', detail);
    } else {
      console.warn('[FlowRenderer] silent_patch: missing fid or patch', spec);
    }
    // Collapse the formatter-generated container so nothing is visible in chat.
    if (container) {
      container.style.cssText = 'display:none!important;height:0;overflow:hidden;margin:0;padding:0;';
      const parent = container.parentElement;
      if (parent && parent !== document.body && (parent.classList.contains('response-card') || parent.classList.contains('ans-silent'))) {
        parent.style.cssText = 'display:none!important;height:0;overflow:hidden;margin:0;padding:0;';
      }
    }
    return;
  }

  // Canvas view → inline card. If canvas split mode is ON and this is a
  // create/open action, also auto-open the split panel immediately.
  _renderFlowInlineCard(container, spec);

  const action = spec.action || spec.metadata?.action;
  const autoOpen = window.__canvasSplitMode && (action === 'create' || action === 'open');
  if (autoOpen) {
    const flowId = spec.flow?.flow_id;
    const token  = localStorage.getItem('tda_auth_token');
    const doOpen = (s) => {
      openFlowSplitPanel(s);
    };
    if (flowId && token) {
      fetch(`/api/v1/flows/${flowId}`, { headers: { 'Authorization': `Bearer ${token}` } })
        .then(r => r.ok ? r.json() : null)
        .then(fresh => doOpen(fresh ? { ...spec, flow: fresh, definition: fresh.definition } : spec))
        .catch(() => doOpen(spec));
    } else {
      doOpen(spec);
    }
  }
}

// Resolve this component's accent LIVE from the manifest (single source of truth),
// surfaced by applyComponentPanelAccents() as window.__componentAccents and as the
// flow split-panel's --sp-accent. Returns '' when nothing live is available yet, so the
// CSS fallback on .flow-inline-card applies. Used to tint the inline card's footer
// button to the flow split-view colour even when the card lives inside another panel.
function _liveFlowAccent() {
  try {
    const m = window.__componentAccents;
    if (m && m.flow) return m.flow;
    const aside = document.getElementById('flow-split-panel');
    if (aside) {
      const v = getComputedStyle(aside).getPropertyValue('--sp-accent').trim();
      // Ignore the generic .sp-panel default (Teradata orange) — means accents
      // haven't been applied from the manifest yet.
      if (v && v.toUpperCase() !== '#F15F22') return v;
    }
  } catch (_) { /* fall back to CSS */ }
  return '';
}

// ── Dashboard tile preview ─────────────────────────────────────────────────────
// Compact, read-only flow preview for a dashboard tile. Mirrors the knowledge_graph
// `renderKnowledgeGraph` dashboard path: header (name + node/edge badge), a mini
// schematic diagram that fills the tile, and a "View in Canvas →" button that opens
// the full flow split panel. Always reconciles to the flow's live state by flow_id.
export function renderFlowPreview(containerId, payload) {
  _injectCSS();
  const spec = typeof payload === 'string' ? JSON.parse(payload) : payload;
  const container = document.getElementById(containerId);
  if (!container || !spec) return;

  const flow       = spec.flow || {};
  const flowId     = flow.flow_id || (spec.metadata || {}).flow_id || '';
  let   definition = spec.definition || flow.definition || {};
  const name       = flow.name || spec.title || 'Flow';

  const wrapper = document.createElement('div');
  wrapper.className = 'flow-inline-card';
  // Fill the tile (no inner border/radius — the tile already provides the card chrome).
  wrapper.style.cssText = 'height:100%;display:flex;flex-direction:column;border:none;border-radius:0;background:transparent;overflow:hidden;';
  // Live flow accent (manifest icon_accent), set AFTER cssText so it isn't wiped —
  // overrides any --sp-accent cascading from a host panel (e.g. dashboard tile).
  { const _a = _liveFlowAccent(); if (_a) wrapper.style.setProperty('--sp-accent', _a); }
  if (flowId) wrapper.dataset.flowId = flowId;

  const _stats = (def) => _flowCardStats(def || {});
  let s = _stats(definition);
  wrapper.innerHTML = `
    <div class="flow-inline-card-header" style="border-bottom:1px solid var(--border-subtle, var(--border-secondary, rgba(255,255,255,0.06)));">
      <svg class="flow-inline-card-icon" width="16" height="16" viewBox="0 0 24 24" fill="none"
           stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="5" r="2"/><circle cx="5" cy="19" r="2"/><circle cx="19" cy="19" r="2"/>
        <line x1="12" y1="7" x2="5" y2="17"/><line x1="12" y1="7" x2="19" y2="17"/>
      </svg>
      <span class="flow-inline-card-name">${_escHtml(name)}</span>
      <span class="flow-inline-card-badge" data-flow-badge>${s.visibleNodes} node${s.visibleNodes !== 1 ? 's' : ''} &middot; ${s.edgeCount} edge${s.edgeCount !== 1 ? 's' : ''}</span>
    </div>
    <div class="flow-preview-diagram" data-flow-diagram
         style="flex:1;min-height:0;position:relative;cursor:pointer;"></div>
    <div class="flow-inline-card-footer" style="border-top:1px solid var(--border-subtle, var(--border-secondary, rgba(255,255,255,0.06)));">
      ${_flowOpenControlHtml(flowId, name)}
    </div>`;

  container.innerHTML = '';
  container.appendChild(wrapper);

  const diagramHost = wrapper.querySelector('[data-flow-diagram]');
  const badgeEl     = wrapper.querySelector('[data-flow-badge]');
  const openBtn     = wrapper.querySelector('.sp-open-ctl');
  _renderFlowMiniDiagram(diagramHost, definition);

  if (flowId) _flowCardSpecs.set(String(flowId), spec);
  // The diagram is part of the same gesture as the button: open (or close) this flow.
  diagramHost?.addEventListener('click', (e) => {
    e.stopPropagation();
    if (openBtn && window.PanelOpenControl) window.PanelOpenControl._onClick(openBtn);
  });

  // Reconcile to live flow state by flow_id (the baked spec may be a stale snapshot
  // and often omits node positions). Re-draws the diagram + badge once on success.
  if (flowId) {
    const token = localStorage.getItem('tda_auth_token');
    fetch(`/api/v1/flows/${flowId}`, { headers: { Authorization: `Bearer ${token}` } })
      .then((r) => (r.ok ? r.json() : null))
      .then((fresh) => {
        if (fresh && fresh.definition) {
          definition = fresh.definition;
          const fs = _stats(definition);
          if (badgeEl) badgeEl.innerHTML = `${fs.visibleNodes} node${fs.visibleNodes !== 1 ? 's' : ''} &middot; ${fs.edgeCount} edge${fs.edgeCount !== 1 ? 's' : ''}`;
          _renderFlowMiniDiagram(diagramHost, definition);
        }
      })
      .catch(() => {});
  }
}

// Draw a lightweight, static schematic of the flow (nodes + edges) from the stored
// node positions — no React Flow, no simulation. Fills its host and scales to fit.
function _renderFlowMiniDiagram(host, definition) {
  if (!host) return;
  const nodes = ((definition && definition.nodes) || []).filter(n => n.type !== 'scope');
  const edges = (definition && definition.edges) || [];

  if (!nodes.length) {
    host.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:100%;
      color:var(--text-muted,#94a3b8);font-size:0.78rem;font-style:italic;">Empty flow</div>`;
    return;
  }

  // Nominal node footprint per type — only relative scale matters for a schematic.
  const _dim = (t) => t === 'decision' ? { w: 90, h: 90 }
    : (t === 'start' || t === 'end' || t === 'merge') ? { w: 110, h: 44 }
    : { w: 150, h: 52 };

  const pts = nodes.map(n => {
    const { w, h } = _dim(n.type);
    return {
      id: n.id,
      type: n.type || 'task_llm',
      cx: (n.position?.x ?? 0) + w / 2,
      cy: (n.position?.y ?? 0) + h / 2,
      w, h,
    };
  });
  const byId = Object.fromEntries(pts.map(p => [p.id, p]));

  // Loops (PROP-007 increment 3): a dashed frame around each scope's members and its
  // loop-back edge drawn as a curve outside the frame — the shape of a debate is a
  // circle, and a preview that drew it as a straight line would lie about it.
  const LOOP = '#a855f7';
  const frames = ((definition && definition.scopes) || []).map(sc => {
    const m = (sc.node_ids || []).map(id => byId[id]).filter(Boolean);
    if (!m.length) return null;
    return { x0: Math.min(...m.map(p => p.cx - p.w / 2)) - 16, x1: Math.max(...m.map(p => p.cx + p.w / 2)) + 16,
             y0: Math.min(...m.map(p => p.cy - p.h / 2)) - 16, y1: Math.max(...m.map(p => p.cy + p.h / 2)) + 16,
             back: sc.back_edge_id };
  }).filter(Boolean);
  const backIds = new Set(frames.map(f => f.back).filter(Boolean));
  edges.forEach(e => { if (e.type === 'cycle') backIds.add(e.id); });

  const minX = Math.min(...pts.map(p => p.cx - p.w / 2), ...frames.map(f => f.x0));
  const maxX = Math.max(...pts.map(p => p.cx + p.w / 2), ...frames.map(f => f.x1 + 30));
  const minY = Math.min(...pts.map(p => p.cy - p.h / 2), ...frames.map(f => f.y0));
  const maxY = Math.max(...pts.map(p => p.cy + p.h / 2), ...frames.map(f => f.y1));
  const pad  = 24;
  const ox = minX - pad, oy = minY - pad;
  const vbW = Math.max(1, maxX - minX) + pad * 2;
  const vbH = Math.max(1, maxY - minY) + pad * 2;

  const frameRects = frames.map(f =>
    `<rect class="ftp-loop-frame" x="${(f.x0 - ox).toFixed(1)}" y="${(f.y0 - oy).toFixed(1)}" width="${(f.x1 - f.x0).toFixed(1)}"
           height="${(f.y1 - f.y0).toFixed(1)}" rx="16" fill="${LOOP}12" stroke="${LOOP}" stroke-width="2" stroke-dasharray="7 5"/>`).join('');
  const loopEdges = edges.filter(e => backIds.has(e.id)).map(e => {
    const s = byId[e.source], t = byId[e.target];
    if (!s || !t) return '';
    const f = frames.find(fr => fr.back === e.id) || { x1: Math.max(s.cx + s.w / 2, t.cx + t.w / 2) + 16 };
    const xo = f.x1 + 22 - ox;
    return `<path class="ftp-loop-edge" d="M${(s.cx + s.w / 2 - ox).toFixed(1)},${(s.cy - oy).toFixed(1)}
              C${xo.toFixed(1)},${(s.cy - oy).toFixed(1)} ${xo.toFixed(1)},${(t.cy - oy).toFixed(1)} ${(t.cx + t.w / 2 - ox).toFixed(1)},${(t.cy - oy).toFixed(1)}"
              fill="none" stroke="${LOOP}" stroke-width="2" stroke-dasharray="6 4"/>`;
  }).join('');

  const edgeLines = edges.map(e => {
    const s = byId[e.source], t = byId[e.target];
    if (!s || !t || backIds.has(e.id)) return '';
    return `<line x1="${(s.cx - ox).toFixed(1)}" y1="${(s.cy - oy).toFixed(1)}"
                  x2="${(t.cx - ox).toFixed(1)}" y2="${(t.cy - oy).toFixed(1)}"
                  stroke="var(--border-primary, rgba(148,163,184,0.55))" stroke-width="2"/>`;
  }).join('');

  const nodeShapes = pts.map(p => {
    const color = NODE_COLORS[p.type] || '#64748b';
    const x = (p.cx - ox - p.w / 2).toFixed(1);
    const y = (p.cy - oy - p.h / 2).toFixed(1);
    if (p.type === 'decision') {
      // Diamond
      const cx = (p.cx - ox).toFixed(1), cy = (p.cy - oy).toFixed(1);
      const r = p.w / 2;
      return `<polygon points="${cx},${(p.cy - oy - r).toFixed(1)} ${(p.cx - ox + r).toFixed(1)},${cy} ${cx},${(p.cy - oy + r).toFixed(1)} ${(p.cx - ox - r).toFixed(1)},${cy}"
                       fill="${color}22" stroke="${color}" stroke-width="2"/>`;
    }
    return `<rect x="${x}" y="${y}" width="${p.w}" height="${p.h}" rx="12"
                  fill="${color}22" stroke="${color}" stroke-width="2"/>`;
  }).join('');

  host.innerHTML = `<svg width="100%" height="100%" viewBox="0 0 ${vbW.toFixed(1)} ${vbH.toFixed(1)}"
       preserveAspectRatio="xMidYMid meet" style="display:block;font-family:'Inter',system-ui,sans-serif;">
    ${frameRects}${edgeLines}${loopEdges}${nodeShapes}
  </svg>`;
}


// ── PROP-007 — Topology picker ───────────────────────────────────────────────────
// The graphical template picker: a gallery of SHAPES (one glyph per registry key),
// a live preview of the chosen shape at the chosen parameters, and the slot
// checklist with a coordinator's experts pre-filled where the rules allow. Plain
// DOM — it opens before any flow canvas (and therefore before React) exists, and it
// is executed under test/dom_stub.mjs by test_flow_topology_picker.mjs.
//
// What is DATA and what is CODE: the catalog (keys, labels, parameters, slot roles,
// availability + reason) is served by GET /api/v1/flows/templates; the preview +
// pre-fill is served by GET /api/v1/flows/templates/<key> (ONE generator, ONE
// pre-fill — shared with POST /v1/flows and the TDA_Flow tool). The picker renders;
// it never decides a slot by itself. The only thing declared here is the GLYPH per
// key — keyed IDENTICALLY to the registry, and the gate asserts the two agree.

/** One monochrome glyph per registry key (currentColor; theme-neutral). */
export const TOPOLOGY_GLYPHS = {
  'pipeline':
    '<circle cx="4" cy="12" r="2.2"/><circle cx="12" cy="12" r="2.2"/><circle cx="20" cy="12" r="2.2"/><line x1="6.2" y1="12" x2="9.8" y2="12"/><line x1="14.2" y1="12" x2="17.8" y2="12"/>',
  'parallel':
    '<circle cx="4" cy="5.5" r="1.8"/><circle cx="4" cy="12" r="1.8"/><circle cx="4" cy="18.5" r="1.8"/><path d="M6.5 5.5H20M6.5 12H20M6.5 18.5H20M17 3l3 2.5-3 2.5M17 9.5l3 2.5-3 2.5M17 16l3 2.5-3 2.5"/>',
  'fork-join':
    '<circle cx="4" cy="12" r="2"/><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/><rect x="17.5" y="9.5" width="5" height="5" rx="1"/><path d="M5.8 11 10.2 5.8M6 12h4M5.8 13l4.4 5.2M13.8 5.8 17.5 10M14 12h3.5M13.8 18.2 17.5 14"/>',
  'conditional':
    '<circle cx="4" cy="12" r="2"/><path d="M12 7.5 16.5 12 12 16.5 7.5 12Z"/><circle cx="20" cy="6" r="2"/><circle cx="20" cy="18" r="2"/><path d="M6 12h1.5M16.5 12l1.8-4.5M16.5 12l1.8 4.5"/>',
  'iterative':
    '<circle cx="6" cy="12" r="2.2"/><circle cx="18" cy="12" r="2.2"/><path d="M8.2 10.5A6 6 0 0 1 15.8 10.5M15.8 13.5A6 6 0 0 1 8.2 13.5"/><path d="M14.5 10.6l1.6-.3-.4-1.6"/>',
  'debate':
    '<circle cx="5" cy="7" r="2"/><circle cx="19" cy="7" r="2"/><circle cx="12" cy="19" r="2.2"/><path d="M7 7h10M6 8.8 11 17M18 8.8 13 17"/>',
  'teacher-student':
    '<circle cx="6" cy="8" r="2.2"/><circle cx="18" cy="8" r="2.2"/><circle cx="12" cy="18" r="2"/><path d="M8.2 8h7.6M7.2 9.8 11 16.2M16.8 9.8 13 16.2"/><path d="M15.4 6.8 16.8 8 15.4 9.2"/>',
  'pair-programming':
    '<circle cx="6" cy="7" r="2.2"/><circle cx="18" cy="7" r="2.2"/><rect x="7" y="14" width="10" height="6" rx="1.2"/><path d="M7.5 9 10 14M16.5 9 14 14M9.5 17h5"/>',
  'swarm':
    '<circle cx="12" cy="12" r="2.4"/><circle cx="12" cy="4" r="1.6"/><circle cx="19" cy="8" r="1.6"/><circle cx="19" cy="16" r="1.6"/><circle cx="12" cy="20" r="1.6"/><circle cx="5" cy="16" r="1.6"/><circle cx="5" cy="8" r="1.6"/><path d="M12 6v3.5M17.5 9l-3.4 1.9M17.5 15l-3.4-1.9M12 18v-3.5M6.5 15l3.4-1.9M6.5 9l3.4 1.9"/>',
  'blank':
    '<circle cx="5" cy="12" r="2.2"/><circle cx="19" cy="12" r="2.2"/><path d="M7.5 12h9" stroke-dasharray="2 2.5"/>',
};

/** The picker's own mark — a new node beside an existing one; deliberately NOT a topology glyph. */
export const PICKER_HEAD_GLYPH =
  '<rect x="2.5" y="8.5" width="7" height="7" rx="1.5"/><path d="M9.5 12h4"/><rect x="13.5" y="8.5" width="7" height="7" rx="1.5" stroke-dasharray="2 1.6"/><path d="M17 10.3v3.4M15.3 12h3.4"/>';

function _glyphSvg(key, size = 28) {
  const body = TOPOLOGY_GLYPHS[key] || TOPOLOGY_GLYPHS.blank;
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
}

let _pickerCss = false;
function _injectPickerCSS() {
  if (_pickerCss || document.getElementById('flow-topology-picker-css')) { _pickerCss = true; return; }
  _pickerCss = true;
  const style = document.createElement('style');
  style.id = 'flow-topology-picker-css';
  style.textContent = `
.ftp-overlay { position: fixed; inset: 0; z-index: 1200; background: rgba(0,0,0,0.55); backdrop-filter: blur(4px);
  display: flex; align-items: center; justify-content: center; animation: flow-overlay-in 0.15s ease both; }
.ftp-modal { width: min(980px, calc(100vw - 32px)); max-height: min(720px, calc(100vh - 32px)); display: flex; flex-direction: column;
  background: var(--card-bg, rgba(20,22,25,0.98)); border: 1px solid var(--border-primary); border-radius: 14px;
  box-shadow: 0 24px 64px rgba(0,0,0,0.6); overflow: hidden; color: var(--text-primary);
  --sp-accent: var(--ftp-accent, #f59e0b); animation: flow-modal-in 0.2s cubic-bezier(0.34,1.56,0.64,1) both; }
.ftp-head { display: flex; align-items: center; gap: 10px; padding: 14px 18px 10px; border-bottom: 1px solid var(--border-secondary); }
.ftp-head h2 { margin: 0; font-size: 0.95rem; font-weight: 700; }
.ftp-head .ftp-sub { font-size: 0.72rem; color: var(--text-muted); }
.ftp-close { margin-left: auto; width: 28px; height: 28px; border-radius: 6px; border: 1px solid transparent; background: transparent;
  color: var(--text-muted); cursor: pointer; display: inline-flex; align-items: center; justify-content: center; }
.ftp-close:hover { color: var(--text-primary); background: var(--hover-bg-strong, rgba(148,163,184,0.12)); }
.ftp-body { display: grid; grid-template-columns: minmax(280px, 2fr) minmax(300px, 3fr); min-height: 0; flex: 1; }
@media (max-width: 760px) { .ftp-body { grid-template-columns: 1fr; } .ftp-detail { border-left: none; border-top: 1px solid var(--border-secondary); } }
.ftp-gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(118px, 1fr)); gap: 8px; padding: 14px; overflow-y: auto; align-content: start; }
.ftp-card { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 12px 8px 10px; border-radius: 10px;
  border: 1px solid var(--border-secondary); background: var(--bg-overlay, var(--bg-primary)); color: var(--text-primary);
  cursor: pointer; text-align: center; transition: border-color 0.12s, transform 0.12s, box-shadow 0.12s; font: inherit; }
.ftp-card:hover { border-color: color-mix(in srgb, var(--sp-accent) 55%, transparent); transform: translateY(-1px); }
.ftp-card:focus-visible { outline: 2px solid var(--sp-accent); outline-offset: 2px; }
.ftp-card.is-selected { border-color: var(--sp-accent); box-shadow: 0 0 0 2px color-mix(in srgb, var(--sp-accent) 30%, transparent); }
.ftp-card .ftp-card-glyph { color: var(--sp-accent); }
.ftp-card .ftp-card-label { font-size: 0.74rem; font-weight: 600; }
.ftp-card.is-unavailable { opacity: 0.5; cursor: default; border-style: dashed; }
.ftp-card.is-unavailable .ftp-card-glyph { color: var(--text-muted); }
.ftp-card .ftp-card-reason { font-size: 0.6rem; color: var(--text-muted); line-height: 1.25; }
.ftp-detail { display: flex; flex-direction: column; gap: 10px; padding: 14px 18px; border-left: 1px solid var(--border-secondary); overflow-y: auto; min-height: 0; }
.ftp-detail-title { display: flex; align-items: center; gap: 8px; font-weight: 700; font-size: 0.9rem; }
.ftp-detail-title .ftp-card-glyph { color: var(--sp-accent); }
.ftp-desc { font-size: 0.76rem; color: var(--text-muted); line-height: 1.4; margin: 0; }
.ftp-preview { height: 150px; border: 1px solid var(--border-secondary); border-radius: 10px; background: var(--bg-overlay, var(--bg-primary)); position: relative; overflow: hidden; }
.ftp-preview .flow-preview-diagram { position: absolute; inset: 8px; }
.ftp-params { display: flex; flex-wrap: wrap; gap: 8px; }
.ftp-param { display: inline-flex; align-items: center; gap: 6px; padding: 2px 4px 2px 10px; border-radius: 999px;
  border: 1px solid var(--border-secondary); font-size: 0.72rem; color: var(--text-primary); }
.ftp-param .ftp-param-name { color: var(--text-muted); }
.ftp-param .ftp-param-val { font-variant-numeric: tabular-nums; min-width: 1.2em; text-align: center; font-weight: 700; }
.ftp-param button { width: 20px; height: 20px; border-radius: 50%; border: 1px solid var(--border-secondary); background: transparent;
  color: var(--text-primary); cursor: pointer; font-size: 0.8rem; line-height: 1; display: inline-flex; align-items: center; justify-content: center; }
.ftp-param button:disabled { opacity: 0.35; cursor: default; }
.ftp-param button:hover:not(:disabled) { border-color: var(--sp-accent); color: var(--sp-accent); }
.ftp-param.is-adapted .ftp-param-val { color: var(--sp-accent); }
.ftp-slots { display: flex; flex-direction: column; gap: 6px; }
.ftp-slots-head { display: flex; align-items: center; gap: 8px; font-size: 0.72rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.04em; font-weight: 600; }
.ftp-modal { position: relative; }
.ftp-agent-trigger { display: inline-flex; align-items: center; gap: 6px; min-width: 0; max-width: 240px; font: inherit; font-size: 0.72rem;
  padding: 3px 8px; border-radius: 6px; border: 1px solid var(--border-secondary); background: var(--bg-overlay, var(--bg-primary));
  color: var(--text-primary); cursor: pointer; text-align: left; }
.ftp-agent-trigger:hover { border-color: color-mix(in srgb, var(--sp-accent) 55%, transparent); }
.ftp-agent-trigger:focus-visible { outline: 2px solid var(--sp-accent); outline-offset: 1px; }
.ftp-agent-trigger .ftp-agent-chip { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-weight: 600; padding: 0 5px; border-radius: 4px; }
.ftp-agent-trigger .ftp-agent-empty { color: var(--text-muted); }
.ftp-agent-trigger .ftp-agent-name { color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ftp-agent-trigger svg { flex-shrink: 0; margin-left: auto; color: var(--text-muted); }
.ftp-team { display: grid; grid-template-columns: auto 1fr; align-items: center; gap: 4px 10px; }
.ftp-team-label { font-size: 0.72rem; color: var(--text-muted); font-weight: 600; }
.ftp-team select { font: inherit; font-size: 0.74rem; min-width: 0; background: var(--bg-overlay, var(--bg-primary)); color: var(--text-primary);
  border: 1px solid var(--border-secondary); border-radius: 6px; padding: 4px 8px; }
.ftp-team select:focus-visible { outline: 2px solid var(--sp-accent); outline-offset: 1px; }
.ftp-team .ftp-prefill-note { grid-column: 1 / -1; margin: 0; font-size: 0.68rem; color: var(--text-muted); line-height: 1.35; }
.ftp-slot { display: grid; grid-template-columns: 14px 1fr auto; align-items: center; gap: 8px; padding: 6px 8px; border-radius: 8px;
  border: 1px solid var(--border-secondary); font-size: 0.74rem; }
.ftp-slot.is-open { border-style: dashed; border-color: color-mix(in srgb, var(--sp-accent) 65%, transparent); }
.ftp-slot .ftp-slot-mark { width: 10px; height: 10px; border-radius: 50%; border: 1.5px solid var(--sp-accent); }
.ftp-slot.is-filled .ftp-slot-mark { background: var(--sp-accent); }
.ftp-slot .ftp-slot-role { font-weight: 600; }
.ftp-slot .ftp-slot-hint { color: var(--text-muted); font-size: 0.66rem; }
.ftp-slot select { font: inherit; font-size: 0.72rem; max-width: 190px; background: var(--bg-overlay, var(--bg-primary)); color: var(--text-primary);
  border: 1px solid var(--border-secondary); border-radius: 6px; padding: 3px 6px; }
.ftp-chips { display: flex; flex-wrap: wrap; gap: 4px; grid-column: 2 / 4; }
.ftp-chip { font-size: 0.64rem; padding: 2px 10px; border-radius: 999px; border: 1px solid var(--border-secondary); background: transparent; color: var(--text-primary); cursor: pointer; font-family: inherit; }
.ftp-chip:hover { border-color: var(--sp-accent); color: var(--sp-accent); }
.ftp-chip-recent { font-size: 0.56rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-muted); margin-right: 4px; }
.ftp-chip.is-recent { border-style: dashed; }
.ftp-not-placed { font-size: 0.66rem; color: var(--text-muted); }
.ftp-foot { display: flex; align-items: center; gap: 10px; padding: 10px 18px 14px; border-top: 1px solid var(--border-secondary); }
.ftp-foot .ftp-status { font-size: 0.72rem; color: var(--text-muted); }
.ftp-foot .ftp-error { font-size: 0.72rem; color: var(--error-color, #f87171); }
.ftp-create { margin-left: auto; padding: 7px 16px; border-radius: 8px; border: 1px solid var(--sp-accent); background: color-mix(in srgb, var(--sp-accent) 16%, transparent);
  color: var(--text-primary); font: inherit; font-size: 0.78rem; font-weight: 600; cursor: pointer; }
.ftp-create:hover:not(:disabled) { background: color-mix(in srgb, var(--sp-accent) 28%, transparent); }
.ftp-create:disabled { opacity: 0.5; cursor: default; }
.ftp-kbd { font-size: 0.64rem; color: var(--text-muted); border: 1px solid var(--border-secondary); border-radius: 4px; padding: 1px 7px; margin-left: 6px; }
@media (prefers-reduced-motion: reduce) { .ftp-overlay, .ftp-modal, .ftp-card { animation: none; transition: none; } }
`;
  document.head.appendChild(style);
}

function _authHeaders() {
  const token = (typeof localStorage !== 'undefined') && localStorage.getItem('tda_auth_token');
  return token ? { 'Authorization': `Bearer ${token}` } : {};
}

/** The slot label as the checklist shows it: the node label ("Worker 1", "Synthesiser")
 *  already names the role, so the role is shown only when there is no label. */
export function _slotTitle(s) {
  const label = (s && s.label) || '';
  if (label) return label;
  const role = (s && s.role) || 'slot';
  return role.charAt(0).toUpperCase() + role.slice(1);
}

/** The platform's ONE agent picker (search + IFOC class pills + keyboard), owned by the
 *  Anatomy module and shared with the Trust Circle. Absent → callers keep a native select. */
export function _sharedAgentPicker() {
  const a = (typeof window !== 'undefined') && window.AgentAnatomy;
  return a && typeof a.openAgentPicker === 'function' ? a.openAgentPicker : null;
}

const _JUDGE_ITEM = { id: 'judge_panel', tag: 'judge-panel', name: 'eval judge panel (not a profile)', type: 'judge_panel', color: '#94a3b8' };

/** Profiles as the shared picker's items, restricted to the classes a slot accepts. */
export function _agentPickerItems(profiles, accepts) {
  const pc = (typeof window !== 'undefined' && window.ProfileColors) || null;
  const acc = Array.isArray(accepts) ? accepts : [];
  const items = (profiles || [])
    .filter(p => !acc.length || acc.includes(p.profile_type))
    .map(p => {
      const type = pc && pc.canonicalProfileType ? pc.canonicalProfileType(p.profile_type) : p.profile_type;
      return { id: p.id, tag: p.tag || p.name || p.id, name: p.name || '', type,
               color: (pc && pc.getProfileColor ? pc.getProfileColor(p.profile_type) : null) || (IFOC[type] || {}).color };
    });
  if (acc.includes('judge_panel')) items.unshift(_JUDGE_ITEM);
  return items;
}

/** Open the shared picker anchored over `hostEl` (centred, as on the Anatomy star). */
// The empty choice in the shared picker: a dashed circle — "no agent here" (the open-slot mark).
const _EMPTY_ROW_ICON = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-dasharray="3 2.6" aria-hidden="true"><circle cx="12" cy="12" r="8"/></svg>';

export function _openSharedAgentPicker(hostEl, { items, currentId, emptyLabel, emptyIcon, onPick }) {
  const open = _sharedAgentPicker();
  if (!open || !hostEl) return false;
  try {
    const pos = (typeof getComputedStyle === 'function') ? getComputedStyle(hostEl).position : (hostEl.style && hostEl.style.position);
    if (!pos || pos === 'static') hostEl.style.position = 'relative';
  } catch (_) { /* positioning is cosmetic */ }
  open({ hostEl, items, currentId: currentId || null,
         specialRow: emptyLabel ? { name: emptyLabel, icon: emptyIcon || _EMPTY_ROW_ICON } : null,
         onPick: (id) => onPick(id || '') });
  return true;
}

/** A coordinator as a team roster: its experts' tags in declared order. */
export function _teamRoster(coord, profiles) {
  const ids = ((coord && coord.genieConfig) || {}).slaveProfiles || [];
  const byId = new Map((profiles || []).map(p => [p.id, p]));
  return ids.map(id => byId.get(id)).filter(Boolean).map(p => p.tag || p.name || p.id);
}

/**
 * Open the topology picker. Returns the overlay element (for tests) or null.
 *
 *   opts.sourceProfileId  the profile the picker opens FROM — a coordinator pre-fills
 *                         its experts, a single agent is offered as a suggestion
 *   opts.profiles         optional pre-fetched /v1/profiles rows (else fetched)
 *   opts.onCreated(flow)  default: open the new flow in the split panel
 *   opts.onClose()        optional
 */
export async function openTopologyPicker(opts = {}) {
  if (typeof document === 'undefined') return null;
  _injectPickerCSS();
  _injectCSS();
  const existing = document.getElementById('flow-topology-picker');
  if (existing) { if (typeof existing._ftpClose === 'function') existing._ftpClose(); else existing.remove(); }

  const accent = _liveFlowAccent();
  const overlay = document.createElement('div');
  overlay.className = 'ftp-overlay';
  overlay.id = 'flow-topology-picker';
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-modal', 'true');
  overlay.setAttribute('aria-label', 'New flow — choose a topology');
  if (accent) overlay.style.setProperty('--ftp-accent', accent);
  overlay.innerHTML = `
    <div class="ftp-modal">
      <div class="ftp-head">
        <span class="ftp-card-glyph" style="color:var(--sp-accent)"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${PICKER_HEAD_GLYPH}</svg></span>
        <div><h2>New flow</h2><div class="ftp-sub" data-ftp-sub>Choose a topology, set its size, fill its slots.</div></div>
        <button class="ftp-close" data-ftp-close title="Close (Esc)" aria-label="Close">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 18L18 6M6 6l12 12"/></svg>
        </button>
      </div>
      <div class="ftp-body">
        <div class="ftp-gallery" data-ftp-gallery role="listbox" aria-label="Topologies"><div class="ftp-desc" style="padding:8px">Loading topologies…</div></div>
        <div class="ftp-detail" data-ftp-detail></div>
      </div>
      <div class="ftp-foot">
        <span class="ftp-status" data-ftp-status></span>
        <span class="ftp-error" data-ftp-error></span>
        <button class="ftp-create" data-ftp-create disabled><span data-ftp-create-label>Create</span> <span class="ftp-kbd">↵</span></button>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  // ── state ──
  const st = {
    catalog: [], profiles: Array.isArray(opts.profiles) ? opts.profiles : null,
    key: null, params: {}, touched: new Set(),
    source: opts.sourceProfileId || null, preview: null,
    // The agent the picker opened FROM when it is NOT a coordinator: under
    // "Choose agents individually" it is still offered as a suggestion on every slot.
    individualSource: null,
    fills: {},            // node_id → profile id | 'judge_panel'  (what Create sends)
    prefillCleared: false, busy: false, seq: 0,
  };
  const gallery = overlay.querySelector('[data-ftp-gallery]');
  const detail  = overlay.querySelector('[data-ftp-detail]');
  const status  = overlay.querySelector('[data-ftp-status]');
  const errEl   = overlay.querySelector('[data-ftp-error]');
  const createBtn = overlay.querySelector('[data-ftp-create]');

  const close = () => { overlay.remove(); document.removeEventListener('keydown', onKey); if (opts.onClose) opts.onClose(); };
  const onKey = (e) => {
    // Removed from outside (not through close)? Then this listener is a ghost — release it.
    if (!overlay.isConnected) { document.removeEventListener('keydown', onKey); return; }
    if (e.key === 'Escape') { e.preventDefault(); close(); return; }
    if (e.key === 'Enter' && !(e.target && /^(SELECT|TEXTAREA|INPUT)$/.test(e.target.tagName || ''))) {
      if (!createBtn.disabled) { e.preventDefault(); create(); }
      return;
    }
    if ((e.key === 'ArrowRight' || e.key === 'ArrowLeft') && e.target && e.target.classList && e.target.classList.contains('ftp-card')) {
      const cards = Array.from(gallery.querySelectorAll('.ftp-card'));
      const i = cards.indexOf(e.target);
      const nxt = cards[i + (e.key === 'ArrowRight' ? 1 : -1)];
      if (nxt) { nxt.focus(); if (!nxt.classList.contains('is-unavailable')) select(nxt.dataset.key); }
    }
  };
  document.addEventListener('keydown', onKey);
  overlay._ftpClose = close;
  overlay.querySelector('[data-ftp-close]').addEventListener('click', close);
  createBtn.addEventListener('click', () => { if (!createBtn.disabled) create(); });
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

  const openSlots = () => (st.preview ? (st.preview.slots || []).filter(s => !st.fills[s.node_id]) : []);
  const profileById = (id) => (st.profiles || []).find(p => p.id === id) || null;

  function renderGallery() {
    gallery.innerHTML = st.catalog.map(t => {
      const un = !t.available;
      const reason = un ? (t.unavailable_reason || 'not available') : '';
      return `<button type="button" class="ftp-card${un ? ' is-unavailable' : ''}${t.key === st.key ? ' is-selected' : ''}"
                data-key="${_escHtml(t.key)}" role="option" aria-selected="${t.key === st.key}"
                ${un ? 'aria-disabled="true"' : ''} title="${_escHtml(un ? `${t.label} — ${reason}` : t.description)}">
          <span class="ftp-card-glyph">${_glyphSvg(t.glyph || t.key)}</span>
          <span class="ftp-card-label">${_escHtml(t.label)}</span>
          ${un ? `<span class="ftp-card-reason">${_escHtml(reason)}</span>` : ''}
        </button>`;
    }).join('');
    gallery.querySelectorAll('.ftp-card').forEach(card => {
      card.addEventListener('click', () => {
        if (card.classList.contains('is-unavailable')) {
          const t = st.catalog.find(x => x.key === card.dataset.key);
          status.textContent = t && t.handoff ? `${t.label}: ${t.handoff.label} (open the profile editor).` : `${t ? t.label : 'This shape'} — ${t ? t.unavailable_reason : 'not available'}.`;
          return;
        }
        select(card.dataset.key);
      });
    });
  }

  function renderDetail() {
    const t = st.catalog.find(x => x.key === st.key);
    if (!t) { detail.innerHTML = `<p class="ftp-desc">Pick a topology on the left.</p>`; return; }
    const p = st.preview;
    const params = (t.parameters || []).map(prm => {
      const v = (p && p.params && p.params[prm.name] !== undefined) ? p.params[prm.name] : (st.params[prm.name] ?? prm.default);
      const adapted = !!(prm.adapts && p && p.prefill && p.prefill.source_count > 0 && !st.touched.has(prm.name) && !st.prefillCleared);
      return `<span class="ftp-param${adapted ? ' is-adapted' : ''}" data-param="${_escHtml(prm.name)}" title="${_escHtml(prm.label)} (${prm.min}–${prm.max})${adapted ? ' — set from the team’s size' : ''}">
          <span class="ftp-param-name">${_escHtml(prm.label)}</span>
          <button type="button" data-step="-1" aria-label="fewer ${_escHtml(prm.label)}" ${v <= prm.min ? 'disabled' : ''}>−</button>
          <span class="ftp-param-val">${v}</span>
          <button type="button" data-step="1" aria-label="more ${_escHtml(prm.label)}" ${v >= prm.max ? 'disabled' : ''}>+</button>
        </span>`;
    }).join('');
    const slots = p ? (p.slots || []) : [];
    const sugg = (p && p.prefill && p.prefill.suggestions) || {};
    const notPlaced = (p && p.prefill && p.prefill.not_placed) || [];
    const sc = p && p.prefill ? p.prefill.source_count : 0;
    const srcTag = p && p.source_tag ? `@${p.source_tag}` : null;
    const slotRows = slots.map(s => {
      const fill = st.fills[s.node_id] || '';
      const filled = !!fill;
      const options = (st.profiles || []).filter(pr => !s.accepts || !s.accepts.length || s.accepts.includes(pr.profile_type));
      const canJudge = (s.accepts || []).includes('judge_panel');
      const fillLabel = fill === 'judge_panel' ? 'eval judge panel' : (profileById(fill) ? `@${profileById(fill).tag || profileById(fill).name}` : fill);
      const chev = `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 9l6 6 6-6"/></svg>`;
      const fp = fill && fill !== 'judge_panel' ? profileById(fill) : null;
      const fc = fp ? (((typeof window !== 'undefined' && window.ProfileColors && window.ProfileColors.getProfileColor) ? window.ProfileColors.getProfileColor(fp.profile_type) : null) || (IFOC[fp.profile_type] || {}).color || '#94a3b8') : '#94a3b8';
      const trigger = `<button type="button" class="ftp-agent-trigger" data-slot-pick="${_escHtml(s.node_id)}" aria-haspopup="listbox" aria-label="agent for ${_escHtml(_slotTitle(s))}">
          ${filled
            ? `<span class="ftp-agent-chip" style="color:${_escHtml(fc)};background:${_escHtml(fc)}1a">${_escHtml(fillLabel)}</span>${fp && fp.name ? `<span class="ftp-agent-name">${_escHtml(fp.name)}</span>` : ''}`
            : `<span class="ftp-agent-empty">Choose an agent…</span>`}${chev}</button>`;
      // The trigger opens the shared, filterable agent picker; the select is the fallback.
      const sel = _sharedAgentPicker() ? trigger : `<select data-slot="${_escHtml(s.node_id)}" aria-label="agent for ${_escHtml(_slotTitle(s))}">
          <option value="">— choose an agent —</option>
          ${canJudge ? `<option value="judge_panel"${fill === 'judge_panel' ? ' selected' : ''}>eval judge panel</option>` : ''}
          ${options.map(pr => `<option value="${_escHtml(pr.id)}"${pr.id === fill ? ' selected' : ''}>@${_escHtml(pr.tag || pr.name || pr.id)} · ${_escHtml((IFOC[pr.profile_type] || {}).label || pr.profile_type || '')}</option>`).join('')}
          ${fill && fill !== 'judge_panel' && !profileById(fill) ? `<option value="${_escHtml(fill)}" selected>${_escHtml(fillLabel)}</option>` : ''}
        </select>`;
      const chips = (!filled && (sugg[s.node_id] || []).length)
        ? `<div class="ftp-chips" aria-label="suggestions">${(sugg[s.node_id] || []).map(c => `<button type="button" class="ftp-chip${c.recent ? ' is-recent' : ''}" data-slot="${_escHtml(s.node_id)}" data-fill="${_escHtml(c.id)}" title="Fill this slot with @${_escHtml(c.tag || c.id)}${c.recent ? ' — you used this agent recently' : ''}">${c.recent ? '<span class="ftp-chip-recent">recent</span>' : ''}@${_escHtml(c.tag || c.id)}</button>`).join('')}</div>`
        : '';
      return `<div class="ftp-slot${filled ? ' is-filled' : ' is-open'}" data-slot-row="${_escHtml(s.node_id)}">
          <span class="ftp-slot-mark" aria-hidden="true"></span>
          <div><div class="ftp-slot-role">${_escHtml(_slotTitle(s))}</div><div class="ftp-slot-hint">${_escHtml(s.hint || '')}</div></div>
          ${sel}${chips}
        </div>`;
    }).join('');
    const openN = openSlots().length;
    // Start from a team: a coordinator is offered as a ROSTER only — the flow runs these
    // agents in its own fixed order; the coordinator's routing is never used.
    const coords = (st.profiles || []).filter(pr => pr.profile_type === 'genie');
    const srcProfile = st.source ? profileById(st.source) : null;
    const teamVal = srcProfile && srcProfile.profile_type === 'genie' && !st.prefillCleared ? srcProfile.id : '';
    const rosterText = (c) => { const r = _teamRoster(c, st.profiles); return r.length ? (r.length > 4 ? `${r.slice(0, 4).map(t => '@' + t).join(', ')} +${r.length - 4}` : r.map(t => '@' + t).join(', ')) : 'no experts'; };
    const teamRow = coords.length ? `<div class="ftp-team">
        <label class="ftp-team-label" for="ftp-team-select">Start from a team</label>
        ${_sharedAgentPicker() ? `<button type="button" id="ftp-team-select" class="ftp-agent-trigger" data-ftp-team-pick aria-haspopup="listbox" style="max-width:none">
          ${teamVal ? `<span class="ftp-agent-chip" style="color:${_escHtml((IFOC.genie || {}).color || '#F15F22')};background:${_escHtml((IFOC.genie || {}).color || '#F15F22')}1a">@${_escHtml(srcProfile.tag || srcProfile.name || '')}</span><span class="ftp-agent-name">${_escHtml(rosterText(srcProfile))}</span>`
                    : `<span class="ftp-agent-empty">Choose agents individually</span>`}
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 9l6 6 6-6"/></svg></button>` : `<select id="ftp-team-select" data-ftp-team>
          <option value=""${teamVal ? '' : ' selected'}>Choose agents individually</option>
          ${coords.map(c => `<option value="${_escHtml(c.id)}"${c.id === teamVal ? ' selected' : ''}>@${_escHtml(c.tag || c.name || c.id)} — ${_escHtml(rosterText(c))}</option>`).join('')}
        </select>`}
        ${teamVal ? `<p class="ftp-prefill-note">Runs ${_escHtml(rosterText(srcProfile))} in this flow’s fixed order — ${srcTag || '@' + _escHtml(srcProfile.tag || '')}’s own routing is not used.</p>` : ''}
      </div>` : '';
    detail.innerHTML = `
      <div class="ftp-detail-title"><span class="ftp-card-glyph">${_glyphSvg(t.glyph || t.key, 22)}</span>${_escHtml(t.label)}</div>
      <p class="ftp-desc">${_escHtml(t.description)}</p>
      <div class="ftp-preview"><div class="flow-preview-diagram" data-ftp-preview></div></div>
      ${teamRow}
      ${params ? `<div class="ftp-params" data-ftp-params>${params}</div>` : ''}
      ${slots.length ? `<div class="ftp-slots" data-ftp-slots>
        <div class="ftp-slots-head"><span>${slots.length} slot${slots.length !== 1 ? 's' : ''} · ${openN} to fill</span></div>
        ${slotRows}
        ${notPlaced.length ? `<div class="ftp-not-placed">Not placed: ${notPlaced.map(n => `@${_escHtml(n.tag || n.id)}${n.reason ? ` (${_escHtml(n.reason)})` : ''}`).join(', ')}</div>` : ''}
      </div>` : `<p class="ftp-desc">No slots — an empty canvas to build on.</p>`}`;

    const host = detail.querySelector('[data-ftp-preview]');
    if (host) _renderFlowMiniDiagram(host, p ? p.definition : (t.skeleton || { nodes: [], edges: [] }));

    detail.querySelectorAll('button[data-step]').forEach(b => b.addEventListener('click', () => {
      const name = b.closest('.ftp-param').dataset.param;
      const prm = (t.parameters || []).find(x => x.name === name);
      const cur = (p && p.params && p.params[name] !== undefined) ? p.params[name] : (st.params[name] ?? prm.default);
      const nv = Math.max(prm.min, Math.min(prm.max, cur + Number(b.dataset.step)));
      st.params[name] = nv; st.touched.add(name);
      loadPreview();
    }));
    detail.querySelectorAll('select[data-slot]').forEach(s => s.addEventListener('change', () => {
      if (s.value) st.fills[s.dataset.slot] = s.value; else delete st.fills[s.dataset.slot];
      renderDetail(); updateFoot();
    }));
    detail.querySelectorAll('.ftp-chip').forEach(c => c.addEventListener('click', () => {
      st.fills[c.dataset.slot] = c.dataset.fill; renderDetail(); updateFoot();
    }));
    const modalHost = overlay.querySelector('.ftp-modal');
    const setTeam = (val) => { st.source = val || st.individualSource; st.prefillCleared = false; st.fills = {}; loadPreview(); };
    detail.querySelectorAll('[data-slot-pick]').forEach(b => b.addEventListener('click', () => {
      const s = slots.find(x => x.node_id === b.dataset.slotPick);
      if (!s) return;
      _openSharedAgentPicker(modalHost, {
        items: _agentPickerItems(st.profiles, s.accepts), currentId: st.fills[s.node_id] || null,
        emptyLabel: 'Leave this slot open',
        onPick: (id) => { if (id) st.fills[s.node_id] = id; else delete st.fills[s.node_id]; renderDetail(); updateFoot(); },
      });
    }));
    const teamPick = detail.querySelector('[data-ftp-team-pick]');
    if (teamPick) teamPick.addEventListener('click', () => {
      // Coordinators only, each named by its roster — a team is a list of agents, not its routing.
      const items = _agentPickerItems(coords, ['genie']).map(it => ({ ...it, name: rosterText(profileById(it.id)) }));
      _openSharedAgentPicker(modalHost, { items, currentId: teamVal || null, emptyLabel: 'Choose agents individually', onPick: setTeam });
    });
    const team = detail.querySelector('[data-ftp-team]');
    // A new team replaces the pre-fill; "individually" keeps only the opening agent's suggestions.
    if (team) team.addEventListener('change', () => setTeam(team.value));
  }

  function updateFoot() {
    const t = st.catalog.find(x => x.key === st.key);
    if (!t || !st.preview) { createBtn.disabled = true; status.textContent = ''; return; }
    const n = openSlots().length;
    createBtn.disabled = st.busy;
    const lbl = createBtn.querySelector('[data-ftp-create-label]');
    if (lbl) lbl.textContent = n ? `Create with ${n} open slot${n !== 1 ? 's' : ''}` : 'Create';
    status.textContent = n ? `${n} slot${n !== 1 ? 's' : ''} still to fill — the flow is created now and cannot run until they are.` : 'Every slot is filled — the flow can run as soon as it is created.';
  }

  async function loadPreview() {
    const t = st.catalog.find(x => x.key === st.key);
    if (!t) return;
    const seq = ++st.seq;
    const q = new URLSearchParams();
    Object.entries(st.params).forEach(([k, v]) => q.set(k, String(v)));
    if (st.source && !st.prefillCleared) q.set('source_profile_id', st.source);
    if (st.touched.size) q.set('touched', Array.from(st.touched).join(','));
    errEl.textContent = '';
    try {
      const r = await fetch(`/api/v1/flows/templates/${encodeURIComponent(t.key)}?${q.toString()}`, { headers: _authHeaders() });
      if (!r.ok) throw new Error(`preview failed (HTTP ${r.status})`);
      const data = await r.json();
      if (seq !== st.seq) return;             // a newer request superseded this one
      st.preview = data;
      // The served pre-fill IS the initial fill set — what the consumer sees is
      // exactly what Create sends. A fill the consumer already made survives a
      // parameter change where its node still exists.
      const keep = { ...st.fills };
      st.fills = {};
      (data.slots || []).forEach(s => {
        if (keep[s.node_id]) st.fills[s.node_id] = keep[s.node_id];
        else if (s.profile_id) st.fills[s.node_id] = s.profile_id;
      });
      if (data.params) st.params = { ...data.params };
    } catch (err) {
      if (seq !== st.seq) return;
      errEl.textContent = err.message || String(err);
    }
    renderDetail(); updateFoot();
  }

  function select(key) {
    if (!key || key === st.key) return;
    st.key = key; st.params = {}; st.touched = new Set(); st.fills = {}; st.preview = null; st.prefillCleared = false;
    renderGallery(); renderDetail(); updateFoot();
    loadPreview();
  }

  async function create() {
    const t = st.catalog.find(x => x.key === st.key);
    if (!t || st.busy) return;
    st.busy = true; createBtn.disabled = true; errEl.textContent = '';
    try {
      // Node ids are minted per generation, so the server cannot match the preview's
      // ids — fills travel keyed by ROLE, as ordered lists over the preview's slots
      // (a hole stays null = that slot is left open). ONE translation, here.
      const byRole = {};
      (st.preview ? (st.preview.slots || []) : []).forEach(s => {
        const role = s.role || 'slot';
        (byRole[role] = byRole[role] || []).push(st.fills[s.node_id] || null);
      });
      const body = { template: t.key, params: { ...st.params }, fills: byRole };
      if (st.preview && st.preview.source_tag && !st.prefillCleared) body.source_tag = st.preview.source_tag;
      const r = await fetch('/api/v1/flows', { method: 'POST', headers: { 'Content-Type': 'application/json', ..._authHeaders() }, body: JSON.stringify(body) });
      if (!r.ok) {
        let msg = `HTTP ${r.status}`;
        try { const j = await r.json(); msg = j.message || j.error || msg; } catch (_) {}
        throw new Error(msg);
      }
      const flow = await r.json();
      close();
      if (typeof opts.onCreated === 'function') opts.onCreated(flow);
      else openFlowSplitPanel({ view: 'canvas', flow, definition: flow.definition || { nodes: [], edges: [], scopes: [] }, action: 'open' });
      UderiaBus.announce('flow', 'created', [flow && flow.flow_id].filter(Boolean));
    } catch (err) {
      st.busy = false; errEl.textContent = err.message || String(err); updateFoot();
    }
  }

  // ── load the catalog (+ profiles) ──
  try {
    const [cat, prof] = await Promise.all([
      fetch('/api/v1/flows/templates', { headers: _authHeaders() }).then(r => r.ok ? r.json() : Promise.reject(new Error(`catalog failed (HTTP ${r.status})`))),
      st.profiles ? Promise.resolve(null) : fetch('/api/v1/profiles', { headers: _authHeaders() }).then(r => r.ok ? r.json() : null).catch(() => null),
    ]);
    st.catalog = (cat && cat.templates) || [];
    if (!st.profiles) st.profiles = (prof && prof.profiles) || [];
    const opener = st.source ? profileById(st.source) : null;
    if (st.source && (!opener || opener.profile_type !== 'genie')) st.individualSource = st.source;
    renderGallery();
    const first = st.catalog.find(x => x.available);
    if (first) select(first.key);
    const firstCard = gallery.querySelector(`.ftp-card[data-key="${first ? first.key : ''}"]`) || gallery.querySelector('.ftp-card');
    if (firstCard && firstCard.focus) firstCard.focus();
  } catch (err) {
    gallery.innerHTML = `<p class="ftp-desc" style="padding:8px">${_escHtml(err.message || String(err))}</p>`;
  }
  return overlay;
}

if (typeof window !== 'undefined') window.openTopologyPicker = openTopologyPicker;

// ── Split panel: open ─────────────────────────────────────────────────────────

/**
 * Open the flow split panel and render the React Flow canvas inside it.
 * Panels can coexist side-by-side; use the drag handle to resize.
 */
/** Open a stored flow in the split panel by id (the switcher's select and "New flow"). */
// ── Open-in-panel control (shared: static/js/handlers/panelOpenControl.js) ─────
// Every flow card — the conversation card, the dashboard tile and the server-rendered
// flow-run card — uses the one control; this module reports which flow the panel shows.
const _flowCardSpecs = new Map();
function _flowOpenControlHtml(flowId, name) {
  const poc = window.PanelOpenControl;
  if (!poc) return '';
  return poc.html({ component: 'flow', id: flowId || '*', accent: '#f59e0b',
                    panelId: 'flow-split-panel', name: name || 'Flow', row: false });
}
function _reportFlowShown(id) {
  try { window.PanelOpenControl && window.PanelOpenControl.shown('flow', id); } catch (_) {}
}
if (typeof window !== 'undefined') {
  // Registered through the queue: this renderer may load before panelOpenControl.js.
  (window.__panelOpenQueue = window.__panelOpenQueue || []).push(['flow', {
    // Always open the flow's CURRENT state, not the snapshot baked into the card.
    async open(id) {
      if (id && id !== '*') {
        const spec = _flowCardSpecs.get(String(id));
        const token = localStorage.getItem('tda_auth_token');
        try {
          const r = await fetch(`/api/v1/flows/${id}`, { headers: token ? { 'Authorization': `Bearer ${token}` } : {} });
          if (r.ok) {
            const fresh = await r.json();
            openFlowSplitPanel({ ...(spec || { view: 'canvas', action: 'open' }), flow: fresh,
                                 definition: fresh.definition || { nodes: [], edges: [], scopes: [] } });
            return;
          }
          if (r.status === 404) {
            // The flow was deleted: opening the card's snapshot would show a flow that
            // no longer exists (and every edit to it would fail). Say so instead.
            if (typeof window.showAppBanner === 'function') window.showAppBanner('This flow no longer exists — it may have been deleted.', 'info');
            return;
          }
        } catch (_) { /* fall back to the card's snapshot */ }
        if (spec) openFlowSplitPanel(spec);
      }
    },
    close() { closeFlowSplitPanel(); },
  }]);
  if (window.PanelOpenControl && window.PanelOpenControl.drain) window.PanelOpenControl.drain();
}

async function _openFlowById(id) {
  const token = localStorage.getItem('tda_auth_token');
  try {
    const r = await fetch(`/api/v1/flows/${id}`, { headers: token ? { 'Authorization': `Bearer ${token}` } : {} });
    if (!r.ok) return;
    const data = await r.json();
    openFlowSplitPanel({
      view: 'canvas',
      flow: data,
      definition: data.definition || { nodes: [], edges: [], scopes: [] },
      action: 'open',
    });
  } catch (_) { /* leave current flow open on failure */ }
}

export function openFlowSplitPanel(spec) {
  const panel = document.getElementById('flow-split-panel');
  if (!panel) { console.warn('[FlowRenderer] #flow-split-panel not found in DOM'); return; }

  // Ensure CSS is injected — openFlowSplitPanel may be called without a prior
  // renderFlow() call (e.g. via the "Open Canvas" button), so the stylesheet
  // that defines #flow-split-panel { width: 45% } may not be in the document yet.
  _injectCSS();

  // Multi-panel split screens: do NOT close other open panels. The user manages
  // each panel independently via its own close (X) button; panels share the row's
  // width and #chat-canvas-split scrolls horizontally when needed (see main.css).

  // If the welcome screen is visible, hide it — flow canvas is the focus.
  // Also install a MutationObserver to suppress any future showWelcomeScreen() calls
  // that fire asynchronously (e.g. from handleViewSwitch's checkServerStatus chain).
  const welcomeScreen = document.getElementById('welcome-screen');
  if (welcomeScreen && !welcomeScreen.classList.contains('hidden')) {
    welcomeScreen.classList.add('hidden');
  }
  if (_welcomeScreenGuard) _welcomeScreenGuard.disconnect();
  if (welcomeScreen) {
    _welcomeScreenGuard = new MutationObserver(() => {
      if (!welcomeScreen.classList.contains('hidden')) {
        welcomeScreen.classList.add('hidden');
      }
    });
    _welcomeScreenGuard.observe(welcomeScreen, { attributes: true, attributeFilter: ['class'] });
  }

  // Collapse session history sidebar so canvas + chat have enough room
  const sessionPanel = document.getElementById('session-history-panel');
  _sessionPanelWasExpanded = sessionPanel ? !sessionPanel.classList.contains('collapsed') : false;
  if (_sessionPanelWasExpanded) sessionPanel.classList.add('collapsed');

  _activeSpec = spec;

  const titleEl = document.getElementById('flow-split-title');
  if (titleEl) titleEl.textContent = (spec.flow || {}).name || 'Flow Canvas';

  const contentArea = document.getElementById('flow-split-content');
  if (contentArea) contentArea.innerHTML = '';
  if (typeof window.hideSplitPanelNotice === 'function') window.hideSplitPanelNotice(panel);

  panel.style.display = 'flex';
  panel.offsetHeight; // force reflow for transition
  panel.classList.add('flow-split--open');

  // Stamp WHICH flow is open and report it: every flow card's shared open control
  // (conversation card, dashboard tile, the server-rendered flow-run card) follows the
  // one report (PanelOpenControl.shown → uderia:panel-state).
  const _openFlowId = (spec.flow || {}).flow_id || (spec.metadata || {}).flow_id || spec.flow_id || '';
  panel.dataset.flowId = _openFlowId;
  _reportFlowShown(_openFlowId || null);

  // Wire header buttons immediately (they live outside the canvas content area)
  const fsBtn = document.getElementById('flow-split-fullscreen');
  if (fsBtn) fsBtn.onclick = _toggleFlowFullscreen;
  const closeBtn = document.getElementById('flow-split-close');
  if (closeBtn) closeBtn.onclick = closeFlowSplitPanel;

  // Mount the flow instance switcher in the standard header (consistent with the
  // other panels). Switching re-opens the panel with the chosen flow. Idempotent.
  const swBtn = document.getElementById('flow-switcher-btn');
  if (swBtn && typeof window.mountSplitPanelSwitcher === 'function') {
    window.mountSplitPanelSwitcher({
      btnEl: swBtn,
      listUrl: '/api/v1/flows',
      itemsKey: 'flows',
      idField: 'flow_id',
      labelField: 'name',
      getCurrentId: () => (_activeSpec && _activeSpec.flow && _activeSpec.flow.flow_id) || null,
      onSelect: (id) => _openFlowById(id),
      newLabel: 'New flow',
      newPrompt: 'Name of the new flow',
      onNew: async (name) => {
        const token = localStorage.getItem('tda_auth_token');
        const r = await fetch('/api/v1/flows', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...(token ? { 'Authorization': `Bearer ${token}` } : {}) },
          body: JSON.stringify({ name }),
        });
        if (!r.ok) return 'Could not create the flow';
        const f = await r.json();
        await _openFlowById(f.flow_id);
        return true;
      },
      onRename: async (id, name) => {
        const token = localStorage.getItem('tda_auth_token');
        const r = await fetch(`/api/v1/flows/${id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json', ...(token ? { 'Authorization': `Bearer ${token}` } : {}) },
          body: JSON.stringify({ name }),
        });
        if (!r.ok) return 'Could not rename the flow';
        if (_activeSpec && _activeSpec.flow && _activeSpec.flow.flow_id === id) _activeSpec.flow.name = name;
        return true;
      },
      emptyLabel: 'No flows yet',
      // PROP-007: 'New flow…' opens the topology picker, pre-filling from the
      // flow the panel is on (its default profile — a coordinator brings its experts).
      newLabel: 'New flow…',
      onNew: () => openTopologyPicker({
        sourceProfileId: ((_activeSpec && _activeSpec.flow && _activeSpec.flow.definition && _activeSpec.flow.definition.settings) || {}).defaultProfileId || null,
      }),
    });
  }

  // Use a STABLE container ID so _roots.has() always finds the previous root
  // and unmounts it before creating a new one — prevents duplicate listeners.
  const CANVAS_CONTAINER_ID = 'flow-canvas-root';

  // Unmount any existing React root before touching the DOM
  if (_roots.has(CANVAS_CONTAINER_ID)) {
    try { _roots.get(CANVAS_CONTAINER_ID).unmount(); } catch (_) {}
    _roots.delete(CANVAS_CONTAINER_ID);
  }

  // Clear and recreate the render target
  if (contentArea) contentArea.innerHTML = '';
  const renderTarget = document.createElement('div');
  renderTarget.id = CANVAS_CONTAINER_ID;
  renderTarget.style.cssText = 'flex:1;display:flex;flex-direction:column;min-height:0;height:100%;';
  if (contentArea) contentArea.appendChild(renderTarget);

  // Coalesce duplicate/concurrent opens: bump the mount token synchronously now,
  // and only let the LATEST scheduled mount reach _renderFlowCanvas. Without this,
  // two near-simultaneous opens (auto-open + _restoreFlowState) race two createRoot()
  // calls on the same container and corrupt ReactFlow's handle registration → edges
  // render with no endpoints on first (cold) load. See _mountSeq note above.
  const seq = ++_mountSeq;

  // Delay the React mount by one frame so the browser has computed the panel's
  // final dimensions. Without this, ReactFlow's fitView runs on a zero-height
  // container and nodes appear off-screen.
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      if (seq !== _mountSeq) return;  // superseded by a newer open — skip this mount
      _renderFlowCanvas(CANVAS_CONTAINER_ID, spec, seq);
    });
  });
}

// ── Split panel: close ────────────────────────────────────────────────────────

// Component signals (components/signals.py): the open flow follows what happens to it
// elsewhere. Deleted anywhere → its panel closes. Written elsewhere — another tab, a REST
// script; never this page's own writes (the agent's patches during a turn, the editor's own
// saves), whose echo the bus marks `own` — → the canvas is not redrawn under the user's hand:
// the panel says "Changed elsewhere" and offers Reload (splitPanelChrome.showSplitPanelNotice).
// The agent reaches an open canvas through uderia:flow-patch, its runs through uderia:flow-run-event.
if (typeof document !== 'undefined' && typeof UderiaBus !== 'undefined' && !window.__flowFollowBound) {
  window.__flowFollowBound = true;
  UderiaBus.onComponent('flow', (e) => {
    const d = (e && e.detail) || {};
    const panel = document.getElementById('flow-split-panel');
    const open = panel && panel.dataset && panel.dataset.flowId;
    if (!open || (d.ids && d.ids.length && !d.ids.includes(String(open)))) return;
    if (d.action === 'deleted') { try { closeFlowSplitPanel(); } catch (_) { /* closing is a courtesy */ } return; }
    if (d.own || d.action !== 'updated' || d.aspect) return;   // aspect 'versions' is a snapshot record, not the flow
    if (typeof window.showSplitPanelNotice === 'function') {
      window.showSplitPanelNotice(panel, 'Changed elsewhere', { actionLabel: 'Reload', onAction: () => _openFlowById(open) });
    }
  });
}

export function closeFlowSplitPanel() {
  const panel = document.getElementById('flow-split-panel');
  if (!panel) return;
  if (typeof window.hideSplitPanelNotice === 'function') window.hideSplitPanelNotice(panel);

  // Exit fullscreen if active
  if (_isFlowFullscreen) {
    _isFlowFullscreen = false;
    const mainArea = document.getElementById('main-content-area');
    if (mainArea) mainArea.classList.remove('flow-fullscreen');
    document.documentElement.style.removeProperty('--flow-fullscreen-top');
    const fsBtn = document.getElementById('flow-split-fullscreen');
    if (fsBtn) fsBtn.title = 'Fullscreen canvas';
  }

  _activeSpec = null;


  panel.classList.remove('flow-split--open');

  // Clear the open-flow stamp and report the panel closed (every flow card follows).
  const _closedFlowId = panel.dataset.flowId || '';
  delete panel.dataset.flowId;
  _reportFlowShown(null);

  // Restore session history sidebar if we collapsed it on open
  if (_sessionPanelWasExpanded) {
    const sessionPanel = document.getElementById('session-history-panel');
    if (sessionPanel) sessionPanel.classList.remove('collapsed');
    _sessionPanelWasExpanded = false;
  }

  // Stop suppressing the welcome screen
  if (_welcomeScreenGuard) {
    _welcomeScreenGuard.disconnect();
    _welcomeScreenGuard = null;
  }

  setTimeout(() => {
    if (!panel.classList.contains('flow-split--open')) {
      panel.style.display = 'none';
      const contentArea = document.getElementById('flow-split-content');
      if (contentArea) contentArea.innerHTML = '';
      _roots.forEach(root => { try { root.unmount(); } catch (_) {} });
      _roots.clear();
    }
  }, 350);
}

export function isFlowPanelActive() {
  return document.getElementById('flow-split-panel')?.classList.contains('flow-split--open') || false;
}

/** Return the spec of the currently open flow, or null if none is open. */
export function getOpenFlowState() {
  return isFlowPanelActive() ? _activeSpec : null;
}

// Expose globally so non-module IIFE handlers (flowsHandler.js) can call it
window.openFlowSplitPanel = openFlowSplitPanel;
window.closeFlowSplitPanel = closeFlowSplitPanel;

// ── Private: mount React Flow canvas into a container ────────────────────────

async function _renderFlowCanvas(containerId, spec, seq) {
  const container = document.getElementById(containerId);
  if (!container) { console.error('[FlowDebug] _renderFlowCanvas: container not found:', containerId); return; }

  container.style.height = '100%';
  container.style.overflow = 'hidden';
  const _cRect = container.getBoundingClientRect();
  console.log('[FlowDebug] _renderFlowCanvas: container dims:', Math.round(_cRect.width), '×', Math.round(_cRect.height),
    '| parent dims:', Math.round(container.parentElement?.getBoundingClientRect()?.width), '×', Math.round(container.parentElement?.getBoundingClientRect()?.height));

  try {
    await _loadDeps();
  } catch (err) {
    container.innerHTML = `<div style="padding:16px;color:var(--text-muted);font-size:0.8rem">
      Failed to load Flow canvas dependencies: ${err.message}</div>`;
    return;
  }

  // Loading deps is async; on a cold page two overlapping opens both suspend here.
  // If a newer open superseded this one while we awaited, abort — only the latest
  // mount may proceed to createRoot(). This is the fix for the concurrent-root churn
  // that left edges unrendered on first load.
  if (seq != null && seq !== _mountSeq) return;

  const React = window.React;
  const ReactDOM = window.ReactDOM;
  if (!React || !ReactDOM || !window.ReactFlow) {
    container.innerHTML = `<div style="padding:16px;color:var(--text-muted);font-size:0.8rem">
      React Flow not available. Check console for errors.</div>`;
    return;
  }

  const AppComponent = _buildFlowApp(spec);

  if (_roots.has(containerId)) {
    try { _roots.get(containerId).unmount(); } catch (_) {}
  }

  const root = ReactDOM.createRoot(container);
  _roots.set(containerId, root);
  root.render(React.createElement(AppComponent));
}

// ── Inline fallback renderers (list / run_status) ─────────────────────────────

function _renderFlowList(containerId, spec) {
  const container = document.getElementById(containerId);
  if (!container) return;
  const flows = spec.flows || [];
  if (flows.length === 0) {
    container.innerHTML = `<div style="padding:12px;color:var(--text-muted);font-size:0.78rem">
      No flows yet. Describe a workflow in the conversation to create one.</div>`;
    return;
  }
  const rows = flows.map(f =>
    `<div style="padding:8px 12px;border-bottom:1px solid var(--border-secondary);font-size:0.78rem;">
      <strong style="color:var(--text-primary)">${f.name}</strong>
      <span style="color:var(--text-muted);margin-left:8px">${f.description || ''}</span>
      <span style="float:right;color:var(--text-muted);font-size:0.68rem">${f.flow_id?.slice(0,8)}</span>
    </div>`
  ).join('');
  container.innerHTML = `<div style="background:var(--card-bg);border-radius:8px;overflow:hidden">${rows}</div>`;
}

function _renderRunStatus(containerId, spec) {
  const container = document.getElementById(containerId);
  if (!container) return;
  const run = spec.run || {};
  const nodeRuns = spec.node_runs || [];
  const statusColor = STATUS_COLORS[run.status] || 'var(--text-muted)';

  const nodeRows = nodeRuns.map(nr => `
    <div style="padding:6px 12px;border-bottom:1px solid var(--border-secondary);font-size:0.72rem;display:flex;gap:8px;align-items:center">
      <div style="width:6px;height:6px;border-radius:50%;background:${STATUS_COLORS[nr.status] || '#64748b'};flex-shrink:0"></div>
      <span style="color:var(--text-primary)">${nr.node_id}</span>
      <span style="color:var(--text-muted)">${nr.status}</span>
      ${nr.tokens_input ? `<span style="margin-left:auto;color:var(--text-muted)">${nr.tokens_input}↑ ${nr.tokens_output || 0}↓</span>` : ''}
    </div>`).join('');

  container.innerHTML = `
    <div style="background:var(--card-bg);border-radius:8px;overflow:hidden;font-size:0.78rem">
      <div style="padding:8px 12px;border-bottom:1px solid var(--border-secondary);display:flex;align-items:center;gap:8px">
        <div style="width:8px;height:8px;border-radius:50%;background:${statusColor}"></div>
        <strong style="color:var(--text-primary)">Run ${run.run_id?.slice(0,12) || ''}</strong>
        <span style="color:var(--text-muted)">${run.status || ''}</span>
        ${run.tokens_input ? `<span style="margin-left:auto;color:var(--text-muted)">${run.tokens_input}↑ ${run.tokens_output || 0}↓ tokens</span>` : ''}
      </div>
      ${nodeRows || '<div style="padding:8px 12px;color:var(--text-muted)">No node runs yet</div>'}
    </div>`;
}
