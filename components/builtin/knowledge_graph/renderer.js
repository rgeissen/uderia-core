import { nodesLabel, relationshipsLabel } from '/static/js/kgVocabulary.js?v=1.0';   // the graph's counting vocabulary
/**
 * Knowledge Graph Component Renderer
 *
 * Premium D3.js v7 force-directed graph visualization with progressive display:
 *   (A) Inline compact — small graph preview in chat message
 *   (B) Split screen  — side panel with full interactive graph + toolbar
 *   (C) Full screen   — graph takes over entire viewport
 *
 * Follows the Canvas component's display pattern.
 *
 * @param {string} containerId - DOM element ID to render into
 * @param {object|string} payload - ComponentRenderPayload.spec from backend
 *   Expected: { nodes, links, title, center_entity, depth, entity_type_colors }
 */

// ─── Default Colors (matches handler.py ENTITY_TYPE_COLORS) ───────────
// Hardcoded fallback ensures correct colors even if the backend dict
// is lost or truncated during JSON → HTML attribute → JSON round-trip.
const DEFAULT_ENTITY_COLORS = {
    // logical (LDM)
    database: '#3b82f6',         // blue
    entity: '#22c55e',           // green  (logical relation)
    attribute: '#a3e635',        // lime   (logical field)
    foreign_key: '#f59e0b',      // amber
    business_concept: '#8b5cf6', // violet
    taxonomy: '#ec4899',         // pink
    metric: '#06b6d4',           // cyan
    domain: '#f97316',           // orange
    // product-description layer — rose/purple family: what the product says about itself
    query_recipe: '#e11d48',     // rose
    design_decision: '#c026d3',  // fuchsia
    quality_metric: '#14b8a6',   // teal-cyan
    naming_standard: '#a855f7',  // purple
    product_bridge: '#f43f5e',   // rose-red — a claimed cross-product link
    // governed-consumption layer — gold: a signed, deterministic consumption contract
    data_product: '#eab308',     // gold — an OBDA-READ data product (the signable contract)
    action_type: '#dc2626',      // signal red — a signed WRITE contract (PROP-008 OBDA-ACT)
    // physical (PDM) — slate/teal family to read as a distinct layer
    catalog: '#64748b',          // slate
    schema: '#94a3b8',           // light slate
    table: '#0d9488',            // teal       (deployed relation)
    column: '#5eead4',           // light teal (deployed field)
    ddl_artifact: '#78716c',     // stone
};

// Canonical filter-pill order: follow the ontology containment topology top→down,
// LOGICAL layer first, then the PHYSICAL (PDM/deployment) layer — instead of a flat
// alphabetical sort that interleaves the two. A visual divider is drawn where the
// layer changes (see the filter-pill loop). Any type not listed here sorts after
// both groups, alphabetically.
const KG_LOGICAL_TYPE_ORDER  = ['database', 'domain', 'entity', 'attribute', 'metric', 'taxonomy', 'business_concept', 'foreign_key',
    // product-description layer: what the product publishes about itself — ordered
    // after the structural backbone so the pills read structure → meaning → self-description
    'query_recipe', 'design_decision', 'quality_metric', 'naming_standard', 'product_bridge',
    // governed-consumption layer: the signable contract — last, so the pills read
    // structure → meaning → self-description → contract
    'data_product',
    // OBDA-ACT: a signed write contract — after the read contract, so the pills read
    // structure → meaning → self-description → read contract → write contract
    'action_type'];
const KG_PHYSICAL_TYPE_ORDER = ['catalog', 'schema', 'table', 'column', 'ddl_artifact'];

// Returns {layer, idx}: layer 0 = logical, 1 = physical, 2 = unknown (sorts last).
function _kgTypeRank(type) {
    let i = KG_LOGICAL_TYPE_ORDER.indexOf(type);
    if (i !== -1) return { layer: 0, idx: i };
    i = KG_PHYSICAL_TYPE_ORDER.indexOf(type);
    if (i !== -1) return { layer: 1, idx: i };
    return { layer: 2, idx: 0 };
}

// Order a type list by layer then containment depth (unknowns alpha-sorted at the end).
function _kgOrderTypes(types) {
    return types.slice().sort((a, b) => {
        const ra = _kgTypeRank(a), rb = _kgTypeRank(b);
        if (ra.layer !== rb.layer) return ra.layer - rb.layer;
        if (ra.layer === 2) return a.localeCompare(b);
        return ra.idx - rb.idx;
    });
}

// Default inspection lens: open on the logical business model (database → domains →
// entities + metrics); attributes + the physical/deployment layer are drill-down detail,
// off by default but one pill-click (or search / double-click expand) away. Must match
// api.js:DEFAULT_INSPECT_TYPES.
// The product-description types are included because they are FEW (tens, not
// thousands — unlike attributes/columns, which stay behind a pill) and they are the
// most distinctive content an AI-Native Data Product graph carries.  Excluding them
// would open such a graph on a plain schema and hide what makes it worth inspecting.
// MUST stay identical to api.js:DEFAULT_INSPECT_TYPES (gated in test_kg_vocabulary).
const DEFAULT_INSPECT_TYPES =
    'database,domain,entity,metric,business_concept,query_recipe,design_decision,quality_metric,naming_standard,product_bridge,data_product';

// What to surface per node type, beyond the shared fields above.  Declared here so a
// new type states its own substance instead of growing an inline if-chain.
const KG_TIP_FIELDS = {
    query_recipe: [
        { key: 'business_question', label: 'Question' },
        { key: 'sql', label: 'SQL', max: 260, mono: true },
        { key: 'complexity', label: 'Complexity' },
    ],
    design_decision: [
        { key: 'rationale', label: 'Rationale', max: 260 },
        { key: 'consequences', label: 'Consequences', max: 200 },
        { key: 'status', label: 'Status' },
    ],
    quality_metric: [
        { key: 'metric_value', label: 'Value' },
        { key: 'quality_threshold', label: 'Threshold' },
        { key: 'metric_category', label: 'Category' },
        { key: 'measured_at', label: 'Measured' },
    ],
    naming_standard: [
        { key: 'meaning', label: 'Means' },
        { key: 'usage_guidance', label: 'Use', max: 200 },
        { key: 'examples', label: 'e.g.' },
    ],
    business_concept: [
        { key: 'definition', label: null, max: 260 },
        { key: 'synonyms', label: 'Synonyms' },
    ],
    entity: [
        { key: 'deployed_table', label: 'Deployed' },
        { key: 'temporal_pattern', label: 'Temporal' },
        { key: 'ontology_iri', label: 'IRI' },
    ],
    attribute: [
        { key: 'declared_type', label: 'Declared' },
        { key: 'deployed_column', label: 'Deployed' },
    ],
    table: [
        { key: 'derives_from_external', label: 'Loaded from' },
        { key: 'load_job', label: 'Job' },
    ],
    column: [{ key: 'datatype', label: 'Type' }],
    product_bridge: [
        { key: 'links', label: null },
        // the basis is the whole point — a bridge whose mechanism is "Synthetic
        // Crosswalk Key" must never read like a verified join
        { key: 'linkage_mechanism', label: 'Basis' },
        { key: 'resolution', label: 'Targets' },
        { key: 'mapping_notes', label: 'Notes', max: 200 },
    ],
    action_type: [
        { key: 'description', label: null, max: 200 },
        // summary fields flattened onto properties at authoring so the tip never parses the manifest
        { key: 'operation', label: 'Writes' },
        { key: 'target_deployed', label: 'Target' },
        { key: 'writable', label: 'Columns' },
        { key: 'max_rows', label: 'Bound' },
        { key: 'posture', label: 'Posture' },
        // a write contract is only as good as its signature — never read as governed unsigned
        { key: 'trust_state', label: 'Signed' },
    ],
    data_product: [
        { key: 'description', label: null, max: 200 },
        { key: 'version', label: 'Version' },
        { key: 'backend', label: 'Backend' },
        // summary fields flattened onto properties at persist time so the tip never
        // has to parse the manifest
        { key: 'member_entities', label: 'Members' },
        { key: 'measure_names', label: 'Measures' },
        // the signature is the point — an unsigned product must never read as governed
        { key: 'trust_state', label: 'Signed' },
    ],
};

/** Clamp a tooltip value; collapses whitespace so multi-line SQL reads on one line. */
function _clampTip(value, max) {
    const text = Array.isArray(value) ? value.join(', ') : String(value);
    const flat = text.replace(/\s+/g, ' ').trim();
    return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

// ─── Node display names: the LEAF, disambiguated only as far as needed ──────────
// KG node names are deliberately QUALIFIED (`fitness_db.Customers.State`,
// `Customers.State`) — that qualification is load-bearing everywhere else: it is what
// keeps two products' `Incident` from merging, what the resolver hands the agent as a
// deployed identifier, and what the grounding gate scopes on. It is the wrong thing to
// PAINT: a 16-char label of a qualified name renders the prefix every node shares
// (`fitness_db.Custo…`) and truncates away the only part that identifies it.
// So the viewer shows the shortest suffix that is unique WITHIN THE RENDERED SET —
// the leaf for almost every node, one segment more only where two visible nodes would
// otherwise read identically (`Customers.City` beside `Sales.City`). Computed over the
// whole rendered set, not the visible-after-filter subset, so toggling a type pill never
// re-writes labels. The full qualified name stays the identity everywhere it matters:
// the hover tooltip, the detail panel, search, and every server round-trip.
// Only the types the KG walkers actually QUALIFY are shortened. A dot is not proof of a
// hierarchy — a platform-ontology node may legitimately be called `uderia.com` and must
// never render as `com`. An unknown/new type therefore keeps its full name (status quo,
// never a wrong one); widen this set when a walker starts qualifying another type.
const KG_QUALIFIED_TYPES = new Set([
    'entity', 'attribute', 'table', 'column', 'physical_table', 'physical_column',
]);

function _kgIsQualifiedType(node) {
    return KG_QUALIFIED_TYPES.has(String((node && node.type) || ''));
}

function _kgNameSegments(name) {
    return String(name == null ? '' : name).split('.').filter(Boolean);
}

/** Suffix of the last `n` dot-segments (falls back to the whole name). */
function _kgSuffix(name, n) {
    const parts = _kgNameSegments(name);
    if (!parts.length) return String(name == null ? '' : name);
    return parts.slice(Math.max(0, parts.length - n)).join('.');
}

/**
 * Map (type, fullName) -> display name over a node set: start at the leaf, and widen
 * ONLY the names that collide, one segment at a time, until unique or fully qualified.
 * Pure + deterministic (same node set -> same labels).
 */
function _computeDisplayNames(nodeList) {
    const out = new Map();
    const keys = [];
    for (const n of (nodeList || [])) {
        const nm = n && n.name;
        if (!nm) continue;
        const key = _kgDisplayKey(n);
        if (out.has(key)) continue;
        // An unqualified type is painted verbatim (its dots are part of the name).
        const shown = _kgIsQualifiedType(n) ? _kgSuffix(nm, 1) : nm;
        out.set(key, { name: nm, type: (n && n.type) || '', shown, fixed: !_kgIsQualifiedType(n) });
        keys.push(key);
    }
    // Widen collided groups until each (type, display) pair maps to exactly one name.
    // Uniqueness is per TYPE because the viewer already separates types by colour and by
    // the type line under the label — a `column` and the `attribute` it implements are
    // read apart on sight, so forcing `fitness_db.Customers.State` beside
    // `Customers.State` would re-create the very prefix soup this exists to remove.
    for (let depth = 1; depth <= 8; depth++) {
        const byDisplay = new Map();
        for (const key of keys) {
            const rec = out.get(key);
            const k = rec.type + '\u0000' + rec.shown;
            if (!byDisplay.has(k)) byDisplay.set(k, []);
            byDisplay.get(k).push(rec);
        }
        let collided = false;
        for (const [, group] of byDisplay) {
            if (group.length < 2) continue;
            collided = true;
            for (const rec of group) { if (!rec.fixed) rec.shown = _kgSuffix(rec.name, depth + 1); }
        }
        if (!collided) break;
    }
    const display = new Map();
    for (const key of keys) display.set(key, out.get(key).shown);
    return display;
}

/** Map key: a name is only unique WITHIN a type (an entity and a business_concept
 *  may legitimately share one name and must not collapse onto one another). */
function _kgDisplayKey(node) {
    return ((node && node.type) || '') + '\u0000' + ((node && node.name) || '');
}

/** Label text for a node: disambiguated display name, then a character cap. */
function _kgNodeLabel(d, displayMap, max) {
    const full = (d && d.name) || '';
    const shown = (displayMap && displayMap.get(_kgDisplayKey(d)))
        || (_kgIsQualifiedType(d) ? _kgSuffix(full, 1) : full) || full;
    return shown.length > max ? shown.slice(0, max - 2) + '\u2026' : shown;
}

// Key-relationship colours: FK edges + FK column markers (amber), PK column markers (gold).
// Distinguishes key constraints from regular structural edges/columns.
const KG_FK_COLOR = '#f59e0b';   // foreign key — amber
const KG_PK_COLOR = '#fbbf24';   // primary key — gold

// Resolve this component's accent LIVE from the manifest (single source of truth),
// surfaced by applyComponentPanelAccents() as window.__componentAccents and as the
// KG split-panel's --sp-accent. Returns '' when nothing live is available yet, so the
// CSS fallback on .kg-inline-compact applies. Used to tint the inline card's footer
// button to the KG split-view colour even when the card lives inside another panel.
function _liveKGAccent() {
    try {
        const m = window.__componentAccents;
        if (m && m.knowledge_graph) return m.knowledge_graph;
        const aside = document.getElementById('kg-split-panel');
        if (aside) {
            const v = getComputedStyle(aside).getPropertyValue('--sp-accent').trim();
            // Ignore the generic .sp-panel default (Teradata orange) — means accents
            // haven't been applied from the manifest yet.
            if (v && v.toUpperCase() !== '#F15F22') return v;
        }
    } catch (_) { /* fall back to CSS */ }
    return '';
}

// ─── Open-in-panel control (shared: static/js/handlers/panelOpenControl.js) ──
// The graph card (conversation + dashboard tile) uses the one control; the panel
// reports which graph it shows when it opens, switches graph and closes.
/** A graph's identity for the open control: its kg_id, else its title. */
function _kgKey(spec) {
    if (!spec) return '';
    return spec.kg_id ? String(spec.kg_id) : `title:${spec.title || 'Knowledge Graph'}`;
}
const _kgCardSpecs = new Map();
function _reportKGShown(id) {
    try { window.PanelOpenControl && window.PanelOpenControl.shown('knowledge_graph', id); } catch (_) {}
}
if (typeof window !== 'undefined') {
  // Registered through the queue: this renderer may load before panelOpenControl.js.
  (window.__panelOpenQueue = window.__panelOpenQueue || []).push(['knowledge_graph', {
    open(id) {
        const spec = _kgCardSpecs.get(String(id));
        if (spec) openKGSplitPanel(spec);
        else if (!String(id).startsWith('title:')) openKGSplitPanel({ kg_id: id, action: 'open' });
    },
    close() { closeKGSplitPanel(); },
  }]);
  if (window.PanelOpenControl && window.PanelOpenControl.drain) window.PanelOpenControl.drain();
}

// ─── State ─────────────────────────────────────────────────────────────
let _stylesInjected = false;
let _isKGFullscreen = false;
let _activeSpec = null;  // Current spec displayed in split panel
let _depsManual = false;  // True once the user manually toggles the dependency overlay —
                          // disables the zoom-driven auto on/off so their choice sticks.
                          // Reset when a NEW graph is opened (renderKnowledgeGraph / openKGSplitPanel).

// ─── Live Animation State ──────────────────────────────────────────────
let _graphState = null;          // D3 refs: { nodeGroups, linkGroup, linkLines, links, nodes, simulation, svg, g, zoom, defs, colors, width, height }
let _activeAnimations = new Map(); // nodeId -> { timeoutId }
let _userZoomOverride = false;   // Set when user manually pans/zooms; cleared on next event
let _autoZoomEnabled = false;    // Smart autozoom (viewport-driven level-of-detail) — opt-in toggle
let _pendingAutoZoom = null;     // {x,y,k} zoom transform to re-apply after an autozoom re-query
let _autoZoomSeed = null;        // {cx,cy} graph-coord centroid to seed NEW nodes near (the visible region)
let _autozoomDebounce = null;    // Timeout ID for debounced auto-zoom

// ─── Trace-driven focus state (execution animation on sampled/large graphs) ──
// renderKGFull publishes a re-query fn here so the animation can pull a focus
// neighborhood when the touched entities aren't in the current sample. Works in
// BOTH the inspect view and the component split panel (same renderKGFull path).
let _activeRequery = null;        // (types, maxNodes, focusNames) => Promise — set by renderKGFull
let _activeReveal = null;         // (entityId, onDone) => pull a node's COMPLETE neighbourhood into the live graph
let _execFocusNames = new Set();  // entity names the running query has touched (accumulates)
let _pendingAnimateNames = new Set(); // names to animate once the focus re-render lands
let _focusRequeryDebounce = null; // batches rapid execution events into one re-query
let _focusRequeryInFlight = false; // never stack focus re-queries (anti-churn / anti-latency)
let _lastFocusRequeryAt = 0;      // throttle floor between focus re-queries (ms)
let _lastActivatedNodeId = null;  // reserved for path-edge tracing between activations
let _prevPositions = null;        // {id -> {x,y}} carried across a re-query so persisting
                                  // nodes keep their place (no layout reshuffle on slider/pill)

// Throttled, guarded, fire-and-forget focus re-query for the execution animation.
// NEVER blocks execution: it only schedules a browser-side re-fetch of a focus
// neighborhood. Guard + throttle keep at most one re-render in flight and cap the
// rate, so rapid execution events don't churn the force layout or add UI latency.
function _scheduleFocusRequery() {
    if (_focusRequeryDebounce) clearTimeout(_focusRequeryDebounce);
    const since = Date.now() - _lastFocusRequeryAt;
    const delay = Math.max(120, 700 - since);   // ~min 700ms between focus re-renders
    _focusRequeryDebounce = setTimeout(async () => {
        _focusRequeryDebounce = null;
        if (_focusRequeryInFlight || !_activeRequery || !_execFocusNames.size) return;
        if (!_activeSpec || (!_activeSpec.kg_id && !_activeSpec.profile_id)) return;
        _focusRequeryInFlight = true;
        _lastFocusRequeryAt = Date.now();
        // Transcend the active type filter (types=null): the execution trace is ground
        // truth — a touched entity of a filtered-out type (e.g. a column touched while
        // the user filtered to `table`) must still come into view and animate, never be
        // silently dropped. The seed→expand engine centres on the touched names.
        const budget = Math.max(_activeSpec.requested_max || 0, 250);
        try { await _activeRequery(null, budget, [..._execFocusNames]); }
        catch (_) { /* leave view as-is */ }
        finally { _focusRequeryInFlight = false; }
    }, delay);
}

// ─── Entry Point ────────────────────────────────────────────────────────

export function renderKnowledgeGraph(containerId, payload) {
    injectStyles();

    const spec = typeof payload === 'string' ? JSON.parse(payload) : payload;
    const container = document.getElementById(containerId);
    if (!container) return null;
    if (!spec || !spec.nodes) {
        // Silent payloads (snapshot/versions/error) render nothing — collapse the
        // framework's ".response-card" wrapper so no empty box appears in chat.
        if (container && spec && typeof spec.view === 'string' && spec.view.startsWith('silent')) {
            container.style.display = 'none';
            const parent = container.parentElement;
            if (parent && parent !== document.body
                && (parent.classList.contains('response-card') || parent.classList.contains('ans-silent'))
                && parent.children.length === 1) {
                parent.style.display = 'none';
            }
        }
        return null;
    }

    // If called from inside the split panel, render full
    if (containerId.startsWith('kg-split-')) {
        return renderKGFull(containerId, spec);
    }

    // Default: render compact inline preview
    const result = renderKGInlineCompact(container, spec);

    // action='open' (from the backend 'open' action) — also auto-open the split
    // panel in the CURRENT session. This is what lets a user open, in session B,
    // a KG that was populated in session A. 'visualize' leaves this off and only
    // renders the inline preview with a manual "Open in Graph" button.
    //
    // BUT not during session-history replay: stored KG messages carry action:'open',
    // which would reopen a panel the user had explicitly closed. The replay loop sets
    // window.__suppressComponentReplayOpen; this decision is synchronous at call time,
    // so the flag reliably covers it (same guard the canvas renderer uses).
    if (spec.action === 'open' && (spec.nodes || []).length > 0
        && !window.__suppressComponentReplayOpen) {
        openKGSplitPanel(spec);
    }

    return result;
}


// ═══════════════════════════════════════════════════════════════════════
// MODE A: Inline Compact Preview
// ═══════════════════════════════════════════════════════════════════════

function renderKGInlineCompact(container, spec) {
    container.innerHTML = '';
    const nodes = spec.nodes || [];
    const links = spec.links || [];
    const colors = { ...DEFAULT_ENTITY_COLORS, ...(spec.entity_type_colors || {}) };
    const title = spec.title || 'Knowledge Graph';

    // Fill mode: stretch the preview to fill the host (e.g. a dashboard tile) instead
    // of the default fixed-height compact card used in chat messages.
    const isFill = spec._dashboardFill === true;

    const wrapper = document.createElement('div');
    wrapper.className = 'kg-inline-compact';
    if (isFill) {
        wrapper.style.cssText = 'height:100%;display:flex;flex-direction:column;border:none;border-radius:0;background:transparent;overflow:hidden;';
    }
    // Live KG accent (manifest icon_accent) — overrides any --sp-accent cascading from
    // a host panel (e.g. dashboard tile) so the footer button keeps KG's own colour.
    const _kgAccent = _liveKGAccent();
    if (_kgAccent) wrapper.style.setProperty('--sp-accent', _kgAccent);

    // Header
    const header = document.createElement('div');
    header.className = 'kg-inline-compact-header';
    header.innerHTML = `
        <div class="kg-inline-compact-title" data-ans-restates>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="flex-shrink:0">
                <circle cx="12" cy="5" r="3"/><circle cx="5" cy="19" r="3"/><circle cx="19" cy="19" r="3"/>
                <line x1="12" y1="8" x2="5" y2="16"/><line x1="12" y1="8" x2="19" y2="16"/>
            </svg>
            <span>${_escapeHtml(title)}</span>
        </div>
        <span class="kg-inline-compact-badge" data-ans-restates>${nodes.length} nodes &middot; ${links.length} edges</span>
    `;
    wrapper.appendChild(header);

    // Mini graph preview
    const graphArea = document.createElement('div');
    graphArea.className = 'kg-inline-compact-graph';
    if (isFill) graphArea.style.cssText = 'flex:1;min-height:0;height:auto;';
    wrapper.appendChild(graphArea);

    // In fill mode, derive the graph height from the host (tile body) minus the
    // header + footer; fall back to the fixed compact height for chat previews.
    let miniHeight = 200;
    if (isFill) {
        const hostH = container.clientHeight || 0;
        if (hostH > 0) miniHeight = Math.max(180, hostH - 77); // ~40px header + ~37px footer
    }

    // Render a simplified mini graph
    _renderMiniGraph(graphArea, spec, miniHeight);

    // Footer with the shared "View in Canvas" open control
    const footer = document.createElement('div');
    footer.className = 'kg-inline-compact-footer';
    _kgCardSpecs.set(_kgKey(spec), spec);
    if (window.PanelOpenControl) {
        footer.innerHTML = window.PanelOpenControl.html({
            component: 'knowledge_graph', id: _kgKey(spec), accent: '#6366f1',
            panelId: 'kg-split-panel', name: title, row: false });
    }
    wrapper.appendChild(footer);

    container.appendChild(wrapper);
    return null;
}


function _renderMiniGraph(container, spec, heightOverride) {
    const nodes = JSON.parse(JSON.stringify(spec.nodes || []));
    const links = JSON.parse(JSON.stringify(spec.links || []));
    const colors = { ...DEFAULT_ENTITY_COLORS, ...(spec.entity_type_colors || {}) };

    if (nodes.length === 0) {
        container.innerHTML = '<p style="color:var(--text-muted, #6b7280);text-align:center;padding:2rem;font-size:13px">Empty graph</p>';
        return;
    }

    const fallbackColor = _themeColor('--text-muted', '#6b7280');
    const width = container.clientWidth || 500;
    // heightOverride (fill mode) drives both the viewBox and the SVG so the graph
    // fills its host; default fixed height keeps the compact chat preview unchanged.
    const height = heightOverride || 200;
    const svgHeight = heightOverride ? '100%' : height;

    const svg = d3.select(container)
        .append('svg')
        .attr('width', '100%')
        .attr('height', svgHeight)
        .attr('viewBox', `0 0 ${width} ${height}`)
        .attr('preserveAspectRatio', 'xMidYMid meet')
        .style('font-family', "'Inter', system-ui, sans-serif");

    const g = svg.append('g');

    // Simple zoom (no controls)
    svg.call(d3.zoom().scaleExtent([0.3, 3]).on('zoom', (e) => g.attr('transform', e.transform)));

    // Force simulation (compact, fewer iterations)
    const simulation = d3.forceSimulation(nodes)
        .force('link', d3.forceLink(links).id(d => d.id).distance(60))
        .force('charge', d3.forceManyBody().strength(-150))
        .force('center', d3.forceCenter(width / 2, height / 2))
        .force('collision', d3.forceCollide().radius(25))
        .alphaDecay(0.05);

    // Edges (simplified)
    const linkLines = g.selectAll('.kg-mini-link')
        .data(links)
        .join('line')
        .attr('class', 'kg-mini-link')
        .attr('stroke-width', 1)
        .attr('stroke-opacity', 0.4);

    // Nodes (simplified circles)
    const nodeCircles = g.selectAll('.kg-mini-node')
        .data(nodes)
        .join('circle')
        .attr('class', 'kg-mini-node')
        .attr('r', d => d.is_center ? 8 : 5 + (d.importance || 0) * 10)
        .attr('fill', d => colors[d.type] || fallbackColor)
        .attr('fill-opacity', 0.7)
        .attr('stroke', d => colors[d.type] || fallbackColor)
        .attr('stroke-width', d => d.is_center ? 2 : 1);

    // Labels for larger graphs only on center/important nodes
    const _miniDisplayNames = _computeDisplayNames(nodes);
    if (nodes.length <= 20) {
        g.selectAll('.kg-mini-label')
            .data(nodes)
            .join('text')
            .attr('class', 'kg-mini-label')
            .text(d => _kgNodeLabel(d, _miniDisplayNames, 14))
            .attr('font-size', '8px')
            .attr('dx', 10)
            .attr('dy', 3)
            .style('pointer-events', 'none');
    }

    simulation.on('tick', () => {
        linkLines.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
                 .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
        nodeCircles.attr('cx', d => d.x).attr('cy', d => d.y);
        g.selectAll('.kg-mini-label').attr('x', d => d.x).attr('y', d => d.y);
    });

    // Stop simulation after settling
    setTimeout(() => simulation.stop(), 3000);
}


// ═══════════════════════════════════════════════════════════════════════
// MODE B: Split Screen Panel
// ═══════════════════════════════════════════════════════════════════════

// ═══════════════════════════════════════════════════════════════════════
// EXECUTION THEATRE — the graph as the stage of a turn (opt-in, per viewer)
//   The theatre consumes the SAME events the legacy glow animation consumed
//   (dispatchKGAnimation) plus the live stage notices and the persisted turn on
//   reload, through ONE reducer (static/js/handlers/kgTheatre.js). Off ⇒ the legacy
//   animation runs unchanged. On ⇒ the first event of a turn replaces the force graph
//   in the split panel with the stage; "Graph" in its caption strip brings the graph
//   back. The preference is per viewer (localStorage), never a server setting.
// ═══════════════════════════════════════════════════════════════════════
const _THEATRE_MODULE = '/static/js/handlers/kgTheatre.js?v=1.36';
let _theatreMod = null, _theatreLoad = null;
let _theatre = null;            // the mounted instance
let _theatreSkipTurn = false;   // a session-primer turn in flight: its events never reach the stage
let _theatreTurn = [];          // [type, payload] of the turn in flight / just ended (Replay)
let _theatreTurnOpen = false;
let _theatreLastTurnData = null;
// The armed story (the turn behind Replay) belongs to ONE session and ONE graph. Both are
// module-level state and outlive a session switch and a graph switch, so without this key
// a NEW session opened "The last turn of this session is ready to replay" — the previous
// session's turn — and a followed graph offered a story told over another graph (Sep 2026).
let _theatreArmedFor = null;    // { sessionId, kgId } of _theatreTurn / _theatreLastTurnData
function _currentSessionId() { try { return (typeof window !== 'undefined' && window.__currentSessionId) || null; } catch (_) { return null; } }
function _stampArmedStory(kgId) { _theatreArmedFor = { sessionId: _currentSessionId(), kgId: kgId || (_activeSpec && _activeSpec.kg_id) || null }; }
/** Is the armed story the one THIS session and THIS graph can replay? A story with no graph identity is trusted on the session alone. */
export function armedStoryIsCurrent(sessionId, kgId) {
    if (!_theatreArmedFor) return !!(_theatreTurn.length || _theatreLastTurnData);
    if (_theatreArmedFor.sessionId !== (sessionId === undefined ? _currentSessionId() : sessionId)) return false;
    const want = (kgId === undefined) ? ((_activeSpec && _activeSpec.kg_id) || null) : kgId;
    if (_theatreArmedFor.kgId && want && _theatreArmedFor.kgId !== want) return false;
    return true;
}
function _dropStaleArmedStory() {
    if (armedStoryIsCurrent()) return false;
    _theatreTurn = []; _theatreLastTurnData = null; _theatreArmedFor = null;
    return true;
}
function _profileTag(profileId) {
    try {
        const cs = (typeof window !== 'undefined' && window.configState) || null;
        const p = cs && Array.isArray(cs.profiles) ? cs.profiles.find(pr => pr.id === profileId) : null;
        return p && p.tag ? `@${String(p.tag).replace(/^@/, '')}` : '';
    } catch (_) { return ''; }
}
function _sessionHasTurns() {
    try { return !!document.querySelector('#chat-log .clickable-avatar[data-turn-id]'); } catch (_) { return false; }
}
/**
 * What the idle stage is waiting for, when nothing is armed — decided from the same facts the
 * platform decides them from: whether this session has a turn yet (the avatar the plan-reload
 * keys on), which agent the next question runs under (the graph's profile — the follow seam),
 * and whether that agent is the session's own or an override.
 */
export function theatreIdleNote(ctx) {
    const c = ctx || {};
    const tag = c.tag !== undefined ? c.tag : _profileTag(_activeSpec && _activeSpec.profile_id);
    const who = tag || 'the agent';
    const sessionProfile = c.sessionProfileId !== undefined ? c.sessionProfileId : ((typeof window !== 'undefined' && window.__currentProfileId) || null);
    const override = !!(c.profileId !== undefined ? c.profileId : (_activeSpec && _activeSpec.profile_id)) && !!sessionProfile && (c.profileId !== undefined ? c.profileId : _activeSpec.profile_id) !== sessionProfile;
    const fresh = c.hasTurns !== undefined ? !c.hasTurns : !_sessionHasTurns();
    if (fresh && override) return `A new session, with ${who} active for the next question — its graph is on stage. Ask, and the story starts here.`;
    if (fresh) return `A new session — nothing to replay yet. Ask the first question and watch ${who} reason over this graph.`;
    if (override) return `${who} is active for the next question — its graph is on stage. Nothing of this session has played here yet.`;
    return `Nothing to replay for this graph yet — the next question under ${who} plays out here.`;
}
function _refreshTheatreIdleNote() {
    if (!_theatre || typeof _theatre.setIdleNote !== 'function') return;
    try { _theatre.setIdleNote(theatreIdleNote()); } catch (_) {}
}
const _THEATRE_START = new Set(['kg_stage', 'kg_enrichment', 'user_query', 'tool_intent', 'conversation_tool_invoked', 'plan_generated']);
const _THEATRE_EVENTS = new Set(['kg_stage', 'kg_enrichment', 'user_query', 'tool_intent', 'tool_result', 'conversation_tool_invoked', 'conversation_tool_rewrite', 'conversation_tool_completed', 'grounding_gate', 'honesty']);

export function isKGTheatreEnabled() { try { return localStorage.getItem('kg_theatre') === '1'; } catch (_) { return false; } }
function _setTheatreEnabled(on) { try { localStorage.setItem('kg_theatre', on ? '1' : '0'); } catch (_) {} _syncTheatreToggle(); }
function _theatreDepth() { try { return localStorage.getItem('kg_theatre_depth') === 'inspect' ? 'inspect' : 'watch'; } catch (_) { return 'watch'; } }
function _loadTheatre() {
    if (_theatreMod) return Promise.resolve(_theatreMod);
    if (!_theatreLoad) _theatreLoad = import(_THEATRE_MODULE).then(m => (_theatreMod = m)).catch(e => { console.warn('[KG] theatre module unavailable', e); _theatreLoad = null; return null; });
    return _theatreLoad;
}
function _theatreHost() {
    const content = document.getElementById('kg-split-content');
    if (!content) return null;
    let host = content.querySelector('.kgt-host');
    if (!host) { host = document.createElement('div'); host.className = 'kgt-host'; host.style.cssText = 'flex:1;display:flex;flex-direction:column;min-height:0;'; content.appendChild(host); }
    return host;
}
async function _mountTheatre() {
    const mod = await _loadTheatre();
    const panel = document.getElementById('kg-split-panel');
    if (!mod || !panel || panel.style.display === 'none') return null;
    if (_theatre) return _theatre;
    _dropStaleArmedStory();   // another session's (or another graph's) turn is not this stage's story
    // hide the graph's render target (kept for the way back) and mount the stage
    _hideGraphUnderTheatre();
    const host = _theatreHost(); if (!host) return null;
    host.style.display = 'flex';
    _clearAllAnimations();
    _theatre = mod.mountTheatre(host, {
        wholeGraph: _wholeGraph(),
        depth: _theatreDepth(),
        idleNote: theatreIdleNote(),
        onExit: () => _unmountTheatre(true),
        onDepth: () => {},
    });
    return _theatre;
}
// The graph waits BEHIND the stage, not display:none: it keeps its true size, so a render
// that happens while the theatre is up (the canonical spec arriving after the mount, a
// session restore) measures the real panel instead of the renderer's 800x600 fallback —
// which otherwise letterboxes the graph for the rest of the panel's life (Sep 2026).
function _hideGraphUnderTheatre() {
    const content = document.getElementById('kg-split-content'); if (!content) return;
    if (getComputedStyle(content).position === 'static') content.style.position = 'relative';
    for (const child of [...content.children]) {
        if (child.classList.contains('kgt-host') || child.dataset.kgtHidden === '1') continue;
        child.dataset.kgtHidden = '1'; child.dataset.kgtStyle = child.getAttribute('style') || '';
        child.style.position = 'absolute'; child.style.inset = '0'; child.style.visibility = 'hidden'; child.style.pointerEvents = 'none'; child.style.zIndex = '0';
    }
}
function _showGraphAfterTheatre() {
    const content = document.getElementById('kg-split-content'); if (!content) return null;
    let target = null;
    for (const child of [...content.children]) {
        if (child.dataset.kgtHidden === '1') { child.setAttribute('style', child.dataset.kgtStyle || ''); delete child.dataset.kgtHidden; delete child.dataset.kgtStyle; }
        else child.style.display = '';
        target = child;
    }
    return target;
}
function _graphSvg(target) {
    // the graph's own svg (100% x 100% with a viewBox), never the toolbar icons
    return [...target.querySelectorAll('svg[viewBox]')].map(el => ({ el, a: el.getBoundingClientRect() })).filter(x => x.a.width > 40 && x.a.height > 40).sort((x, y) => y.a.width * y.a.height - x.a.width * x.a.height).map(x => x.el)[0] || null;
}
// The whole graph is the theatre's opening field: the loaded spec's entities, at the
// force graph's own coordinates when it rendered (so the narrowing is continuous with
// what the viewer was looking at), else positioned by the theatre. Read at mount AND at
// every turn start, so a graph that rendered after the mount still opens the story.
function _wholeGraph() {
    return (() => {
        try {
            if (_graphState && Array.isArray(_graphState.nodes) && _graphState.nodes.length) {
                // wherever the viewer left the graph — panned, zoomed, focused — is where the story opens
                let tr = null; try { const t = d3.zoomTransform(_graphState.svg.node()); tr = { k: t.k, x: t.x, y: t.y }; } catch (_) {}
                return { nodes: _graphState.nodes.map(n => ({ id: n.id, name: n.name, type: n.type, x: n.x, y: n.y, properties: n.properties })), links: (_graphState.links || []).map(l => ({ source: (l.source && l.source.id !== undefined) ? l.source.id : l.source, target: (l.target && l.target.id !== undefined) ? l.target.id : l.target, type: l.type })), transform: tr };
            }
            if (_activeSpec && Array.isArray(_activeSpec.nodes)) return { nodes: _activeSpec.nodes, links: _activeSpec.links || [] };
        } catch (_) {}
        return null;
    })();
}
function _unmountTheatre(showGraph) {
    if (_theatre) { try { _theatre.destroy(); } catch (_) {} _theatre = null; }
    const content = document.getElementById('kg-split-content');
    if (content) {
        const host = content.querySelector('.kgt-host'); if (host) host.remove();
        if (showGraph) {
            let target = _showGraphAfterTheatre();
            // the graph was left in place; if it never rendered (theatre mounted first), render now
            if (_activeSpec && (!target || !target.querySelector('svg'))) {
                if (!target) { target = document.createElement('div'); target.id = `kg-split-render-${Date.now()}`; target.style.cssText = 'flex:1;display:flex;flex-direction:column;min-height:0;'; content.appendChild(target); }
                renderKGFull(target.id, _activeSpec);
            } else if (_activeSpec && target && _graphState === null) {
                target.innerHTML = ''; renderKGFull(target.id, _activeSpec);
            } else if (_activeSpec && target) {
                // safety net: a graph drawn against a stand-in size (its viewBox disagrees with the
                // estate it now has) is re-fitted; a graph drawn at the right size keeps the viewer's frame
                requestAnimationFrame(() => {
                    const svg = _graphSvg(target); const cr = content.getBoundingClientRect();
                    const vb = svg && svg.viewBox && svg.viewBox.baseVal;
                    const off = !svg || !vb || Math.abs(vb.width - cr.width) > cr.width * 0.12 || Math.abs(vb.height - cr.height) > cr.height * 0.12;
                    if (off) { target.innerHTML = ''; renderKGFull(target.id, _activeSpec); }
                });
            }
        }
    }
}
/** Feed one event to the theatre. Returns true when the theatre consumed it. */
function _theatreConsume(type, payload) {
    if (!isKGTheatreEnabled()) return false;
    if (!_THEATRE_EVENTS.has(type)) return !!_theatre;      // a mounted theatre owns the panel; other events are noise to it
    const panel = document.getElementById('kg-split-panel');
    if (!panel || panel.style.display === 'none') return false;
    // A session primer is a BRIEFING the agent absorbs, not a question it answers (agent/primer.py):
    // its turn is never a story. The stage keeps its idle frame through the whole primer turn —
    // it used to narrate the primer's outline as if it were the question.
    if (type === 'user_query' && payload && payload.is_session_primer) { _theatreTurnOpen = true; _theatreSkipTurn = true; return !!_theatre; }
    if (_theatreSkipTurn) return !!_theatre;
    if (!_theatreTurnOpen && _THEATRE_START.has(type)) { _theatreTurnOpen = true; _theatreTurn = []; _stampArmedStory(); if (_theatre) { try { _theatre.reset({ wholeGraph: _wholeGraph(), live: true }); } catch (e) { console.warn('[kg-theatre] reset failed', e); } } }
    _theatreTurn.push([type, payload]);
    if (!_theatre) { _mountTheatre().then(t => { if (!t) return; for (const [tt, pp] of _theatreTurn) t.dispatch(tt, pp); }); return true; }
    try { _theatre.dispatch(type, payload); } catch (e) { console.warn('[kg-theatre] dispatch failed', e); }
    return true;
}
function _theatreEnd() {
    if (!_theatreTurnOpen) return;
    _theatreTurnOpen = false;
    if (_theatreSkipTurn) { _theatreSkipTurn = false; return; }   // the primer turn closed; the stage never opened it
    if (_theatre) _theatre.end();
}
/** Used by notifications.js (kg_stage + genie kg_enrichment) — session guard is the caller's. */
export function dispatchKGTheatreEvent(type, payload) { return _theatreConsume(type, payload); }
/** Reload: narrate a persisted turn through the same reducer. */
export function replayKGTheatre(turnData) {
    _theatreLastTurnData = turnData || null;
    // The owner is stamped HERE, before any early return: a last turn remembered while the
    // panel was closed must still be known as THIS session's (and this graph's) when the
    // panel opens later — an unstamped one read as current in every session.
    if (turnData) { const ev0 = turnData.kg_enrichment_event || {}; _stampArmedStory((ev0.metadata && ev0.metadata.kg_id) || ev0.kg_id || null); }
    if (!isKGTheatreEnabled() || !turnData) return false;
    const panel = document.getElementById('kg-split-panel');
    if (!panel || panel.style.display === 'none') return false;
    if (!turnData.kg_enrichment_event) return false;      // a turn the graph never touched has no story on the stage
    if (turnData.is_session_primer) return false;         // a briefing is not a story (see _theatreConsume)
    _theatreTurnOpen = false;
    // ARM, never auto-play: the stage keeps "Waiting for the question…" and offers the last
    // turn behind the Replay control. A historical story played on every session switch
    // displaced the idle frame entirely — the reader thought the feature had gone.
    _mountTheatre().then(async (t) => {
        if (!t) return;
        const mod = await _loadTheatre();
        const events = mod.eventsFromTurn(turnData);
        _theatreTurn = events.slice();
        t.arm(events);
    });
    return true;
}
function _syncTheatreToggle() {
    const btn = document.getElementById('kg-theatre-toggle'); if (!btn) return;
    const on = isKGTheatreEnabled();
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    btn.title = on ? 'Execution theatre is on — the graph narrates each turn (click to turn off)' : 'Turn on the execution theatre — the graph narrates each turn';
    btn.style.color = on ? 'var(--teradata-orange, #F15F22)' : '';
    btn.style.borderColor = on ? 'rgba(241,95,34,.45)' : '';
    btn.style.background = on ? 'rgba(241,95,34,.10)' : '';
}
function _ensureTheatreControls() {
    const actions = document.querySelector('#kg-split-panel .sp-header-actions'); if (!actions) return;
    if (document.getElementById('kg-theatre-toggle')) { _syncTheatreToggle(); return; }
    const btn = document.createElement('button');
    btn.id = 'kg-theatre-toggle'; btn.className = 'sp-action-btn'; btn.type = 'button';
    btn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h16v11H4z"/><path d="M8 21h8M12 16v5"/><path d="M8 9l3 3 2-2 3 3"/></svg>';
    actions.insertBefore(btn, actions.firstChild);
    btn.onclick = () => {
        const on = !isKGTheatreEnabled();
        _setTheatreEnabled(on);
        if (on) {
            // the stage opens idle; a turn just narrated (or the session's last turn) is ARMED behind Replay
            _dropStaleArmedStory();
            if (_theatreTurn.length) { _mountTheatre().then(t => { if (t) t.arm(_theatreTurn.slice()); }); }
            else if (!(_theatreLastTurnData && replayKGTheatre(_theatreLastTurnData))) _mountTheatre();
        } else {
            _unmountTheatre(true);
        }
    };
    _syncTheatreToggle();
}
// notifications.js reaches the theatre through this window seam (it cannot import the
// lazily-loaded renderer); the session guard stays with the caller.
if (typeof window !== 'undefined') window.__kgTheatre = { dispatch: dispatchKGTheatreEvent, isActive: () => !!_theatre, isEnabled: isKGTheatreEnabled, state: () => (_theatre ? _theatre.state : null), turn: () => _theatreTurn.slice() };

/** "New knowledge graph…": go to Intelligence → Knowledge Graphs and open its New menu
 *  (the constructors). Each step is a click on the app's own control, so the route is the
 *  one a user would take — never a second way to build a graph. */
export function openKgConstructors() {
    const nav = document.getElementById('view-switch-rag-maintenance');
    if (nav) nav.click();
    setTimeout(() => {
        const tab = document.getElementById('knowledge-graphs-tab');
        if (tab) tab.click();
        setTimeout(() => {
            const btn = document.getElementById('new-knowledge-graph-btn');
            if (btn) { btn.scrollIntoView({ block: 'nearest' }); btn.click(); }
        }, 250);
    }, 150);
}

export function openKGSplitPanel(spec) {
    const panel = document.getElementById('kg-split-panel');
    if (!panel) return;

    // Ensure the component stylesheet is present. openKGSplitPanel is a public entry
    // point reached DIRECTLY by session restore (_restoreKGState) — not only via the
    // renderKnowledgeGraph dispatcher (which injects on its own). On a cold restore
    // (server restart → fresh page → reopen a session that had the KG open) no inline
    // card has rendered yet, so without this the panel's layout CSS (#kg-split-panel
    // flex-direction:column, .kg-split-body flex:1, …) is missing and the panel falls
    // back to the inline display:flex default of row — laying the header BESIDE the
    // graph and overflowing it. Idempotent (guarded by _stylesInjected).
    injectStyles();

    _activeSpec = spec;
    _depsManual = false;   // fresh graph → dependency overlay back to zoom-driven auto
    _reportKGShown(_kgKey(spec));

    // Update title
    const titleEl = document.getElementById('kg-split-title');
    if (titleEl) titleEl.textContent = spec.title || 'Knowledge Graph';

    // Clear previous content. A theatre already on stage is unmounted FIRST: wiping the
    // content removed its host while `_theatre` stayed set, so the `!_theatre` guard below
    // never remounted it — the graph then hid itself behind a stage that was not there.
    if (_theatre) _unmountTheatre(false);
    const contentArea = document.getElementById('kg-split-content');
    if (contentArea) contentArea.innerHTML = '';

    // Show panel with animation
    panel.style.display = 'flex';
    panel.offsetHeight; // Force reflow
    panel.classList.add('kg-split--open');

    // Create render target
    const renderTarget = document.createElement('div');
    renderTarget.id = `kg-split-render-${Date.now()}`;
    renderTarget.style.cssText = 'flex:1;display:flex;flex-direction:column;min-height:0;';
    contentArea.appendChild(renderTarget);

    // The theatre owns the panel while enabled: an empty stage waits for the next question
    // (the graph still renders behind it, so "Graph" brings it straight back).
    // A last turn remembered while the panel was closed (the plan-reload ran, the stage was
    // not there) is armed now — if it is this session's and this graph's; otherwise the
    // stage opens with the idle note and nothing to replay.
    if (isKGTheatreEnabled() && !_theatre) { _mountTheatre().then(t => { if (t && !_theatreTurn.length && _theatreLastTurnData && armedStoryIsCurrent()) replayKGTheatre(_theatreLastTurnData); }); }

    // Render full graph into split panel.
    // CONSISTENCY: unless the spec is already canonical (came straight from `/graph-spec`,
    // e.g. via the switcher/reload which set _canonical), load it through the SAME pipeline
    // Inspect uses — so the conversation 'open', the inline "Open in Graph" button, and
    // session restore all render identically to Inspect (logical lens, full type-tile
    // catalog, edges) instead of a divergent hand-built sample. The passed spec is the
    // graceful fallback when there's no KG identity to re-query or the fetch fails.
    if (!spec._canonical && (spec.kg_id || spec.profile_id)) {
        _fetchCanonicalSpec(spec.kg_id, spec.profile_id,
                            { includeDrafts: !!spec.include_drafts, focusNames: spec.focus_names }).then(canon => {
            const target = document.getElementById(renderTarget.id);
            if (!target) return;   // panel was closed during the async fetch
            if (canon) {
                canon.kg_id = spec.kg_id || canon.kg_id;
                canon.profile_id = spec.profile_id || canon.profile_id;
                canon.title = canon.title || spec.title;
                canon.action = spec.action || canon.action;
                canon._canonical = true;
                // An open "on" a concept (OBDA-MODEL / Workspace row / Open in Graph): the
                // names ride the canonical spec — `focus_names` is the open itself (select +
                // zoom once), `applied_focus_names` keeps the seed through every re-query.
                if (Array.isArray(spec.focus_names) && spec.focus_names.length) {
                    canon.focus_names = spec.focus_names;
                    canon.applied_focus_names = spec.focus_names;
                }
                _activeSpec = canon;
                const t = document.getElementById('kg-split-title');
                if (t) t.textContent = canon.title || 'Knowledge Graph';
                target.innerHTML = '';
                renderKGFull(renderTarget.id, canon);
            } else {
                // Graceful degradation: render whatever spec we were handed.
                renderKGFull(renderTarget.id, spec);
            }
            if (_theatre) _hideGraphUnderTheatre();   // the theatre is on stage: the graph waits behind it
        });
    } else {
        renderKGFull(renderTarget.id, spec);
    }

    // Wire up header buttons
    _ensureTheatreControls();
    const fsBtn = document.getElementById('kg-split-fullscreen');
    if (fsBtn) fsBtn.onclick = toggleKGFullscreen;
    const closeBtn = document.getElementById('kg-split-close');
    if (closeBtn) closeBtn.onclick = _manualCloseKGSplitPanel;

    // Mount the KG instance switcher (idempotent — once per page load).
    const swBtn = document.getElementById('kg-switcher-btn');
    if (swBtn && typeof window.mountSplitPanelSwitcher === 'function') {
        window.mountSplitPanelSwitcher({
            btnEl: swBtn,
            listUrl: '/api/v1/knowledge-graph/list',
            itemsKey: 'knowledge_graphs',
            idFn: (it) => it.kg_id || it.profile_id,
            labelFn: (it) => it.kg_name || it.profile_name || 'Knowledge Graph',
            metaField: 'total_entities',
            metaSuffix: ' entities',
            getCurrentId: () => (_activeSpec && (_activeSpec.kg_id || _activeSpec.profile_id)) || null,
            // A graph the user picked here is PINNED: the composer's profile no longer
            // swaps it from under them until the panel closes (followEffectiveProfile).
            onSelect: (id, item) => { _userPinnedKgId = (item && item.kg_id) || null; _switchKGInPanel(item); },
            // A graph is BUILT by a constructor (industry model, data product, database,
            // declared model …) — not named into being empty. "New" goes to the place that
            // offers them: Intelligence → Knowledge Graphs, with its New menu opened.
            newLabel: 'New knowledge graph…',
            onNew: () => openKgConstructors(),
            onRename: async (id, name, item) => {
                const kgId = item && item.kg_id;
                if (!kgId) return 'This graph has no name of its own to change';
                const token = localStorage.getItem('tda_auth_token');
                const r = await fetch('/api/v1/knowledge-graph/rename', {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json', ...(token ? { 'Authorization': `Bearer ${token}` } : {}) },
                    body: JSON.stringify({ kg_id: kgId, name }),
                });
                if (!r.ok) return 'Could not rename the knowledge graph';
                if (_activeSpec && _activeSpec.kg_id === kgId) _activeSpec.kg_name = name;
                return true;
            },
            emptyLabel: 'No knowledge graphs',
        });
    }

    // Event-driven session state: record that THIS session now has the KG panel open
    // (and which KG/profile), so reopening the session restores it. Cleared on manual
    // close; preserved on programmatic switch-away close — that is what makes "was open
    // when I left" survive the round-trip.
    _persistKGOpenState(spec);
}

/**
 * Persist that the KG panel is OPEN for the current session. Reopening the session
 * restores it (via _restoreKGState). Requires profile_id so the graph can be
 * re-fetched on restore. Cleared on manual close.
 */
function _persistKGOpenState(spec) {
    const sid = window.__currentSessionId;
    if (!sid || !spec) return;
    try {
        // Lightweight marker only — the graph is re-fetched fresh on restore.
        // profile_id is best-effort here and re-resolved at restore time if missing.
        localStorage.setItem(`kg_state_${sid}`, JSON.stringify({
            kg_id: spec.kg_id || null,
            profile_id: spec.profile_id || window.__currentProfileId || null,
            title: spec.title || 'Knowledge Graph',
            action: 'open',
        }));
    } catch (_) { /* quota / serialization — non-fatal */ }
}

/**
 * User-initiated close: forget the open state for THIS session so reopening leaves
 * the panel closed. Programmatic switch-away close calls closeKGSplitPanel() directly
 * (NOT this), so a left-open panel survives and is restored on return.
 */
function _manualCloseKGSplitPanel() {
    const sid = window.__currentSessionId;
    if (sid) { try { localStorage.removeItem(`kg_state_${sid}`); } catch (_) {} }
    closeKGSplitPanel();
}


// ── Follow the composer's effective profile ──────────────────────────────────
//
// The split panel shows ONE agent's graph. When the profile the next turn will run
// under changes — an @TAG override typed, picked or removed, or the default profile
// switched — the panel must show THAT agent's graph, or say honestly that the agent
// has none: the previous agent's graph left on screen is exactly the misleading state.
// Driven by the one `uderia:effective-profile` signal main.js emits from the
// resource-panel seam (every override and default change already funnels there),
// so no composer site has to know the panel exists.
//
// Rules, each gated:
//   closed panel        → nothing (a closed panel costs nothing)
//   same graph          → nothing (identity, by kg_id — the switch is a no-op, not a repaint)
//   user pinned a graph → nothing until the panel closes (the switcher is an explicit act)
//   a turn is streaming → nothing (the theatre narrates the graph the RUNNING turn uses)
//   no graph bound      → the honest empty state, naming the agent
//   a stale response    → dropped (the last change wins, whatever order the fetches land)
let _userPinnedKgId = null;
let _followSeq = 0;

export async function followEffectiveProfile(profileId, opts) {
    const o = opts || {};
    const panel = document.getElementById('kg-split-panel');
    if (!panel || panel.style.display === 'none') return 'closed';
    if (!profileId) return 'no-profile';
    if (o.streaming || _theatreTurnOpen) return 'streaming';
    if (_userPinnedKgId) return 'pinned';
    const seq = ++_followSeq;
    const fetchSpec = o.fetchSpec || _fetchProfileSpec;
    let data;
    try { data = await fetchSpec(profileId); } catch (_) { data = null; }
    if (seq !== _followSeq) return 'stale';            // a later change superseded this one
    if (!data || !data.spec) return 'unavailable';      // leave what is there; never blank on a failed fetch
    const spec = data.spec;
    const kgId = spec.kg_id || null;
    const hasNodes = Array.isArray(spec.nodes) && spec.nodes.length > 0;
    if (!hasNodes) {
        // Name the agent by its TAG: the composer's own profile list first (what the
        // user just typed), the payload's tag next — never the profile id.
        const cs = (typeof window !== 'undefined' && window.configState) || null;
        const known = cs && Array.isArray(cs.profiles) ? cs.profiles.find(pr => pr.id === profileId) : null;
        _renderNoGraphState(profileId, (known && known.tag) || data.profile_tag || '');
        return 'no-graph';
    }
    if (_activeSpec && kgId && _activeSpec.kg_id === kgId) return 'same';   // the empty state has no kg_id, so it never reads as "same"
    spec.kg_id = kgId;
    spec.profile_id = profileId;
    spec.title = spec.title || spec.kg_name || 'Knowledge Graph';
    spec.action = 'open';
    spec._canonical = true;    // straight from /graph-spec — the same pipeline Inspect uses
    openKGSplitPanel(spec);
    return 'switched';
}

async function _fetchProfileSpec(profileId) {
    const token = localStorage.getItem('tda_auth_token');
    const params = new URLSearchParams({ profile_id: profileId, max_nodes: '500', types: DEFAULT_INSPECT_TYPES });
    const r = await fetch(`/api/v1/knowledge-graph/graph-spec?${params.toString()}`,
        { headers: { 'Authorization': `Bearer ${token}` } });
    if (!r.ok) return null;
    return r.json();
}

// The agent the composer selected has no graph: say so, in the panel, naming the agent —
// never keep another agent's graph on screen as if it were this one's.
function _renderNoGraphState(profileId, tag) {
    const contentArea = document.getElementById('kg-split-content');
    const titleEl = document.getElementById('kg-split-title');
    _clearAllAnimations();
    _graphState = null;
    if (_theatre) _unmountTheatre(false);   // no graph, no stage
    _activeSpec = { profile_id: profileId, kg_id: null, _empty: true, action: 'open' };
    _reportKGShown('none');   // the panel is open but shows no graph — no card is "expanded"
    if (titleEl) titleEl.textContent = 'Knowledge Graph';
    if (!contentArea) return;
    const who = tag ? `@${String(tag).replace(/^@/, '')}` : 'this agent';
    contentArea.innerHTML = `
      <div class="kg-follow-empty" style="flex:1;display:flex;align-items:center;justify-content:center;padding:32px;text-align:center;">
        <div style="max-width:360px;color:var(--text-muted,#9ca3af);font-size:13px;line-height:1.5;">
          <div style="font-weight:600;color:var(--text-primary,#e5e7eb);margin-bottom:6px;">No knowledge graph is bound to ${who}</div>
          The graph shown here follows the agent the next question will run under.
          Bind a graph to ${who} in its profile, or pick one from the switcher above.
        </div>
      </div>`;
    _persistKGOpenState(_activeSpec);
}

if (typeof window !== 'undefined') {
    UderiaBus.on('uderia:effective-profile', (ev) => {
        const d = (ev && ev.detail) || {};
        followEffectiveProfile(d.profileId, { streaming: !!d.streaming }).then(() => _refreshTheatreIdleNote(), () => {});
    });
}

export function closeKGSplitPanel() {
    const panel = document.getElementById('kg-split-panel');
    if (!panel) return;

    // Exit fullscreen if active
    if (_isKGFullscreen) {
        _isKGFullscreen = false;
        const mainArea = document.getElementById('main-content-area');
        if (mainArea) mainArea.classList.remove('kg-fullscreen');
        document.documentElement.style.removeProperty('--kg-fullscreen-top');
        _updateFullscreenIcon(false);
    }

    _activeSpec = null;
    _userPinnedKgId = null;    // a closed panel forgets the user's pick; reopening follows the profile again
    _clearAllAnimations();
    _graphState = null;
    _unmountTheatre(false); _theatreTurnOpen = false; _theatreSkipTurn = false;

    _reportKGShown(null);   // every graph card reads "View in Canvas" again

    panel.classList.remove('kg-split--open');

    // A fallback timeout guarantees the hide even when 'transitionend' never fires
    // (e.g. closing on a fresh new-chat session that doesn't force a reflow) — else
    // the panel lingers until a page refresh.
    let _hidden = false;
    const finishHide = () => {
        if (_hidden) return;
        _hidden = true;
        panel.removeEventListener('transitionend', onTransitionEnd);
        if (!panel.classList.contains('kg-split--open')) {
            panel.style.display = 'none';
            const contentArea = document.getElementById('kg-split-content');
            if (contentArea) contentArea.innerHTML = '';
        }
    };
    const onTransitionEnd = () => finishHide();
    panel.addEventListener('transitionend', onTransitionEnd);
    setTimeout(finishHide, 400);
}


// ═══════════════════════════════════════════════════════════════════════
// MODE C: Fullscreen
// ═══════════════════════════════════════════════════════════════════════

function toggleKGFullscreen() {
    const mainArea = document.getElementById('main-content-area');
    if (!mainArea) return;

    _isKGFullscreen = !_isKGFullscreen;

    if (_isKGFullscreen) {
        const topNav = document.querySelector('body > nav');
        const topOffset = topNav ? topNav.offsetHeight : 0;
        document.documentElement.style.setProperty('--kg-fullscreen-top', topOffset + 'px');
        mainArea.classList.add('kg-fullscreen');
    } else {
        mainArea.classList.remove('kg-fullscreen');
        document.documentElement.style.removeProperty('--kg-fullscreen-top');
    }

    _updateFullscreenIcon(_isKGFullscreen);
}


function _updateFullscreenIcon(isFullscreen) {
    const fsBtn = document.getElementById('kg-split-fullscreen');
    if (!fsBtn) return;
    fsBtn.title = isFullscreen ? 'Exit fullscreen' : 'Fullscreen graph';
    // The SVG already works for both states (expand arrows)
}


// ═══════════════════════════════════════════════════════════════════════
// FULL INTERACTIVE GRAPH (used by split panel and fullscreen)
// ═══════════════════════════════════════════════════════════════════════

// Public fit / export hooks so an external view (the Ontology) can host these graph
// actions in its OWN controls row instead of the in-graph toolbar (operate on the live
// _graphState; no-ops when no graph is mounted).
export function kgFitView() {
    _fitVisibleNodes(500);
}

// ONE "zoom to fit" for every caller (toolbar button, the Ontology's controls row, the
// post-animation return). It fits the bounding box of the nodes that are actually ON
// SCREEN (type-filtered nodes are display:none and excluded) into the viewBox — the
// earlier form applied a fixed scale(0.8) about the SVG centre and never looked at where
// the nodes were, so it only "fit" when the force layout happened to be centred and
// compact (never after an expand, a focus render without a centring force, a cluster
// layout, a drag, or a one-sided filter).
function _fitTransform() {
    if (!_graphState) return null;
    const { nodeGroups, nodes, width, height, zoom } = _graphState;
    const hidden = new Set();
    if (nodeGroups) nodeGroups.each(function (d) {
        if (d && this.style && this.style.display === 'none') hidden.add(d.id);
    });
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity, n = 0;
    for (const nd of nodes || []) {
        if (nd.x == null || nd.y == null || !isFinite(nd.x) || !isFinite(nd.y)) continue;
        if (hidden.has(nd.id)) continue;
        if (nd.x < minX) minX = nd.x;
        if (nd.x > maxX) maxX = nd.x;
        if (nd.y < minY) minY = nd.y;
        if (nd.y > maxY) maxY = nd.y;
        n++;
    }
    // Nothing positioned/visible → the honest fallback is the identity view.
    if (!n) return d3.zoomIdentity;
    // Node boxes are 24px + a label beside them; pad so the outermost boxes and their
    // labels sit inside the frame rather than on its edge.
    const pad = 70;
    minX -= pad; maxX += pad; minY -= pad; maxY += pad;
    const bw = Math.max(maxX - minX, 1), bh = Math.max(maxY - minY, 1);
    let scale = Math.min(width / bw, height / bh);
    // Clamp to the zoom's OWN extent (read from it, never a second typed number — a fit
    // that lands below what the wheel allows makes the next wheel tick snap), and never
    // magnify a tiny graph into a few giant boxes.
    const ext = (zoom && zoom.scaleExtent) ? zoom.scaleExtent() : [0.1, 4];
    scale = Math.max(ext[0], Math.min(scale, Math.min(ext[1], 1.5)));
    const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
    return d3.zoomIdentity.translate(width / 2, height / 2).scale(scale).translate(-cx, -cy);
}
function _fitVisibleNodes(duration) {
    if (!_graphState || !_graphState.svg || !_graphState.zoom) return;
    const t = _fitTransform();
    if (!t) return;
    const { svg, zoom } = _graphState;
    if (_autozoomDebounce) { clearTimeout(_autozoomDebounce); _autozoomDebounce = null; }
    svg.transition('autozoom').duration(duration).ease(d3.easeCubicInOut).call(zoom.transform, t);
}
export function kgExportPNG() {
    if (!_graphState || !_graphState.svg) return;
    _exportPNG(_graphState.svg.node(), _graphState.width, _graphState.height);
}

// What a collapse removes, as a pure rule over the ledger (no DOM, no d3 — the gate
// executes it). `exp` is the expansion being collapsed (already deleted from `active`),
// `active` the remaining expansions. Rule 1: a node `exp` introduced survives while an
// active expansion still claims it as a neighbour (or is its centre). Rule 2 (cascade)
// is applied by the caller before this runs. A link goes when either endpoint goes, or
// when `exp` introduced it and it does not touch an active centre (that centre's
// neighbourhood keeps it).
function _collapseSurvivors(exp, active, nodes, links) {
    const keep = new Set();
    for (const other of active.values()) { other.claimed.forEach(e => keep.add(e)); keep.add(other.centre.entity_id); }
    const activeCentres = new Set([...active.values()].map(e => e.centre));
    const goneNodes = new Set(nodes.filter(n => exp.nodeIds.has(n.id) && !keep.has(n.entity_id)));
    const goneLinks = new Set(links.filter(l => goneNodes.has(l.source) || goneNodes.has(l.target)
        || (exp.linkUids.has(l._uid) && !activeCentres.has(l.source) && !activeCentres.has(l.target))));
    return { goneNodes, goneLinks };
}

export function renderKGFull(containerId, spec) {
    injectStyles();   // defense in depth — the graph layout CSS must exist before we measure/render
    const container = document.getElementById(containerId);
    if (!container || !spec || !spec.nodes) return null;

    // Clear any previous animation state (prevents stale refs when switching between split panel and inspect)
    _clearAllAnimations();

    // Track the spec for EVERY full-render path — not just the split panel. The inspect
    // view reaches renderKGFull directly (renderKnowledgeGraph → renderKGFull), and a
    // re-query (slider/pill/dblclick/zoom) re-enters here with a fresh spec. Setting it
    // here is what makes double-click expand, semantic zoom, and the execution animation
    // work in the inspect view (was null → guards failed → "nothing happens") and keeps
    // applied_types / requested_max / truncated current after every re-query (was stale).
    _activeSpec = spec;

    container.innerHTML = '';
    container.style.display = 'flex';
    container.style.flexDirection = 'column';
    container.style.minHeight = '0';

    const nodes = JSON.parse(JSON.stringify(spec.nodes));
    const links = JSON.parse(JSON.stringify(spec.links || []));
    const colors = { ...DEFAULT_ENTITY_COLORS, ...(spec.entity_type_colors || {}) };
    const title = spec.title || 'Knowledge Graph';

    if (nodes.length === 0) {
        container.innerHTML = '<p style="color:var(--text-muted, #9ca3af);text-align:center;padding:2rem">No entities in graph</p>';
        return null;
    }

    // ─── Detail-lens re-query (type pills + node-budget slider) ──────
    // Re-fetch a type-stratified subgraph from the server and re-render in place.
    // This is the "funnel" interaction: filtering by type / changing the budget
    // changes WHAT is fetched (stratified), not just what's hidden client-side.
    async function requery(selectedTypes, maxNodes, focusNames, project, divisions, domains) {
        if (!spec.kg_id && !spec.profile_id) return;  // re-query needs an identity
        const token = localStorage.getItem('tda_auth_token');
        const params = new URLSearchParams();
        if (spec.kg_id) params.set('kg_id', spec.kg_id);
        if (spec.profile_id) params.set('profile_id', spec.profile_id);
        params.set('max_nodes', String(maxNodes));
        const allTypes = Object.keys(spec.entity_type_counts || {});
        // Pass types only when it's a real subset (all-selected = no filter).
        if (selectedTypes && selectedTypes.length && selectedTypes.length < allTypes.length) {
            params.set('types', selectedTypes.join(','));
        }
        // Facet filters (division / domain selector tiles). undefined = preserve the
        // current selection; an empty array = clear the filter (show all).
        const divs = divisions !== undefined ? divisions : (spec.applied_divisions || null);
        const doms = domains !== undefined ? domains : (spec.applied_domains || null);
        if (divs && divs.length) params.set('divisions', divs.join(','));
        if (doms && doms.length) params.set('domains', doms.join(','));
        // Derived-dependency overlay (relationship projection) — heavy, so off unless the
        // caller asks (zoom-in / expand / manual toggle).
        if (project) params.set('project', '1');
        if (spec.include_drafts) params.set('include_drafts', '1');   // a drafts view stays one
        // Focus seed (focus+context): names the budget should centre on — used by the
        // execution animation (touched entities) and by search/zoom.
        if (focusNames && focusNames.length) {
            params.set('focus_names', focusNames.join(','));
        }
        // Snapshot current node positions (synchronously, before the fetch) so that
        // persisting nodes keep their place on re-render — no layout reshuffle. Keyed by
        // ENTITY_ID (not the array index, which is reassigned per server response — a focus
        // re-query returns a different node set, so index carry would misplace everything).
        if (_graphState && _graphState.nodes) {
            _prevPositions = new Map(_graphState.nodes
                .filter(n => n.x != null && n.y != null)
                .map(n => [n.entity_id, { x: n.x, y: n.y }]));
        }
        try {
            const r = await fetch(`/api/v1/knowledge-graph/graph-spec?${params.toString()}`,
                { headers: { Authorization: `Bearer ${token}` } });
            const data = await r.json();
            if (data.status !== 'success' || !data.spec) { _prevPositions = null; return; }
            const fresh = {
                ...data.spec,
                kg_id: spec.kg_id, profile_id: spec.profile_id,
                requested_max: maxNodes,
                applied_types: (selectedTypes && selectedTypes.length < allTypes.length) ? selectedTypes : null,
                // Remember the focus so slider/pill changes preserve it (don't reset focus).
                applied_focus_names: (focusNames && focusNames.length) ? focusNames : null,
                // Remember the dependency-overlay state so slider/pill changes preserve it.
                applied_project: !!project,
                // Remember the facet selection so other interactions preserve it.
                applied_divisions: (divs && divs.length) ? divs : null,
                applied_domains: (doms && doms.length) ? doms : null,
            };
            renderKGFull(containerId, fresh);  // re-render in place
        } catch (_) { _prevPositions = null; /* leave current view on failure */ }
    }

    // Publish the re-query so the execution animation can pull a focus neighborhood
    // when touched entities aren't in view (works in inspect + component split panel).
    _activeRequery = requery;

    // ─── Toolbar ───────────────────────────────────────────────────
    const toolbar = _buildToolbar(nodes, links, colors, spec, requery);
    container.appendChild(toolbar);

    // ─── Graph Container ───────────────────────────────────────────
    const graphContainer = document.createElement('div');
    // Transparent — inherits the viewport-fixed body gradient, matching the
    // dashboard component's backdrop (all panel canvases read as one surface).
    graphContainer.style.cssText = `flex:1;position:relative;background:transparent;border-radius:0 0 8px 8px;overflow:hidden;min-height:0;`;
    container.appendChild(graphContainer);

    // Overview lens: fetch the type-level meta-graph and render it in place. Clicking
    // a type drills into the detail view filtered to that type.
    toolbar._onSchema = async () => {
        if (!spec.kg_id && !spec.profile_id) return;
        const token = localStorage.getItem('tda_auth_token');
        const params = new URLSearchParams();
        if (spec.kg_id) params.set('kg_id', spec.kg_id);
        if (spec.profile_id) params.set('profile_id', spec.profile_id);
        try {
            const r = await fetch(`/api/v1/knowledge-graph/schema-overview?${params.toString()}`,
                { headers: { Authorization: `Bearer ${token}` } });
            const data = await r.json();
            if (data.status !== 'success') return;
            const ovColors = { ...DEFAULT_ENTITY_COLORS, ...(data.entity_type_colors || {}) };
            _renderSchemaOverview(graphContainer, data.overview, ovColors,
                (type) => requery([type], Math.max(spec.requested_max || 0, 250)));
        } catch (_) { /* leave detail view as-is */ }
    };

    // Render once the container has SETTLED to a real, non-zero size. The split panel
    // animates width 0 → 50% over 0.3s, and on the cold session-restore path (server
    // restart → reopen a session that had the KG open) the panel is still mid-open /
    // unconstrained during the initial page reflow when this first runs. Measuring on a
    // single rAF then read a wrong width/height, so the force layout centred on bogus
    // dimensions and the graph rendered overflowing the panel. Wait for the size to stop
    // changing so we render against the panel's final geometry.
    let _kgRendered = false;
    const _doRender = (w, h) => {
        if (_kgRendered) return;
        _kgRendered = true;
        _renderFullGraph(graphContainer, nodes, links, colors, w, h, toolbar);
    };

    // Render only once the container size has been STABLE for a beat. During the 0.3s
    // open transition the width changes every frame, so the debounce below keeps resetting
    // and fires only after the panel reaches its final geometry — correct whether the first
    // measurement reads 0 (still closed) or an unconstrained-large value (cold restore,
    // before the panel's flex/max-width constraints settle). Avoids rendering against a
    // wrong width/height, which centred the force layout off-panel and overflowed it.
    let _settle = null;
    const ro = new ResizeObserver(() => {
        if (_kgRendered) { ro.disconnect(); return; }
        const w = graphContainer.clientWidth || 0;
        const h = graphContainer.clientHeight || 0;
        if (w <= 50 || h <= 50) return;        // not laid out / mid-transition near zero
        clearTimeout(_settle);
        _settle = setTimeout(() => {
            ro.disconnect();
            _doRender(graphContainer.clientWidth || w, graphContainer.clientHeight || h);
        }, 90);
    });
    ro.observe(graphContainer);
    // Hard fallback: render with whatever we have if the observer never settles.
    setTimeout(() => {
        if (_kgRendered) { ro.disconnect(); return; }
        ro.disconnect();
        _doRender(graphContainer.clientWidth || 800, graphContainer.clientHeight || 600);
    }, 900);

    return { container };
}


function _buildToolbar(nodes, links, colors, spec, requery) {
    const toolbar = document.createElement('div');
    toolbar.className = 'kg-toolbar';
    toolbar.dataset.spSubheader = '';   // auto-hiding secondary toolbar (subHeaderAutohide.js)

    // Direct callbacks — registered by _renderFullGraph (no CustomEvent needed)
    toolbar._onFilter = null;
    toolbar._onZoomFit = null;
    toolbar._onExportPNG = null;

    // ── Search ──
    const searchWrap = document.createElement('div');
    searchWrap.className = 'kg-toolbar-search';
    searchWrap.innerHTML = `
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="flex-shrink:0;opacity:0.5">
            <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
        </svg>
    `;
    const searchInput = document.createElement('input');
    searchInput.type = 'text';
    searchInput.placeholder = 'Search entities...';
    searchInput.className = 'kg-toolbar-search-input';
    searchWrap.appendChild(searchInput);
    toolbar.appendChild(searchWrap);

    // ── Stats badge ──
    const totalNodes = spec.total_nodes != null ? spec.total_nodes : nodes.length;
    const totalEdges = spec.total_edges != null ? spec.total_edges : links.length;
    const stats = document.createElement('span');
    stats.className = 'kg-toolbar-stats';
    stats.textContent = (totalNodes > nodes.length)
        ? `showing ${nodes.length.toLocaleString()} of ${totalNodes.toLocaleString()} nodes`
        : `${nodes.length.toLocaleString()} nodes \u00b7 ${links.length.toLocaleString()} edges`;
    stats.title = `${nodes.length} of ${nodesLabel(totalNodes)} \u00b7 ${links.length} of ${relationshipsLabel(totalEdges)} shown`;
    toolbar.appendChild(stats);

    const _catalog = spec.entity_type_counts || {};
    const _shownByType = spec.shown_type_counts || {};
    const _canRequery = typeof requery === 'function' && (spec.kg_id || spec.profile_id);

    // Node-budget slider (the "graph layout" funnel step; force is legible ~150).
    if (_canRequery) {
        const sliderWrap = document.createElement('div');
        sliderWrap.className = 'kg-toolbar-budget';
        const curMax = spec.requested_max || Math.max(nodes.length, 150);
        const blabel = document.createElement('span');
        blabel.className = 'kg-budget-label';
        blabel.textContent = `${curMax} nodes`;
        const slider = document.createElement('input');
        slider.type = 'range';
        slider.min = '50'; slider.max = '2000'; slider.step = '50';
        slider.value = String(Math.min(2000, Math.max(50, curMax)));
        slider.className = 'kg-budget-slider';
        slider.title = 'How many nodes to render (stratified across selected types). Force layout is legible to ~150.';
        slider.addEventListener('input', () => { blabel.textContent = `${slider.value} nodes`; });
        slider.addEventListener('change', () => {
            const active = toolbar._activeTypes ? [...toolbar._activeTypes] : null;
            // Preserve the current focus + dependency overlay so changing the budget
            // doesn't silently reset them.
            requery(active, parseInt(slider.value, 10), spec.applied_focus_names || null,
                    spec.applied_project || false);
        });
        sliderWrap.appendChild(blabel);
        sliderWrap.appendChild(slider);
        toolbar.appendChild(sliderWrap);
    }

    // ── Filter pills ── (left-bound, right after the stats badge)
    // Pills come from the FULL type catalog (every type + count), not just the
    // sampled nodes. Toggling re-queries a stratified subgraph (server-side).
    const allTypes = Object.keys(_catalog).length ? Object.keys(_catalog) : [...new Set(nodes.map(n => n.type))];
    if (allTypes.length > 1) {
        const filterWrap = document.createElement('div');
        filterWrap.className = 'kg-toolbar-filters';
        const active = new Set(Array.isArray(spec.applied_types) ? spec.applied_types : allTypes);
        toolbar._activeTypes = active;

        // Ordered by ontology topology (logical layer, then physical), not alphabetically.
        let _prevLayer = null;
        _kgOrderTypes(allTypes).forEach(type => {
            // Draw a subtle vertical rule where the layer changes (logical → physical),
            // so the two families read as distinct groups.
            const _layer = _kgTypeRank(type).layer;
            if (_prevLayer !== null && _layer !== _prevLayer) {
                const div = document.createElement('span');
                div.className = 'kg-filter-layer-divider';
                div.style.cssText = 'width:1px;align-self:stretch;margin:3px 5px;background:var(--border-primary,#3f3f46);opacity:0.6;flex-shrink:0;';
                filterWrap.appendChild(div);
            }
            _prevLayer = _layer;
            const pill = document.createElement('button');
            pill.className = 'kg-toolbar-filter-pill';
            const typeColor = colors[type] || _themeColor('--text-muted', '#6b7280');
            pill.style.borderColor = typeColor + '60';
            pill.style.background = typeColor + '20';
            pill.style.color = typeColor;
            const total = _catalog[type] != null ? _catalog[type] : (nodes.filter(n => n.type === type).length);
            const shown = _shownByType[type] != null ? _shownByType[type] : (nodes.filter(n => n.type === type).length);
            pill.textContent = (total > shown) ? `${type} ${shown}/${total}` : `${type} ${total}`;
            pill.dataset.entityType = type;
            pill.style.opacity = active.has(type) ? '1' : '0.35';
            pill.addEventListener('click', () => {
                if (_canRequery) {
                    if (active.has(type)) active.delete(type); else active.add(type);
                    if (active.size === 0) { active.add(type); return; }   // never empty
                    pill.style.opacity = active.has(type) ? '1' : '0.35';
                    // Preserve the current focus + dependency overlay when changing the filter.
                    requery([...active], spec.requested_max || Math.max(nodes.length, 150),
                            spec.applied_focus_names || null, spec.applied_project || false);
                } else {
                    const hidden = toolbar._hiddenTypes || (toolbar._hiddenTypes = new Set());
                    if (hidden.has(type)) { hidden.delete(type); pill.style.opacity = '1'; }
                    else { hidden.add(type); pill.style.opacity = '0.3'; }
                    if (toolbar._onFilter) toolbar._onFilter(hidden);
                }
            });
            filterWrap.appendChild(pill);
        });
        toolbar.appendChild(filterWrap);
    }

    // ── Spacer ── (after the pills, so the action buttons below stay right-bound)
    const spacer = document.createElement('div');
    spacer.style.flex = '1';
    toolbar.appendChild(spacer);

    // ── Division / Domain facet tiles ──
    // Rendered ONLY when the KG actually carries those facets ("only if present").
    // Multi-select; an empty selection = no filter (show all). Server-side re-query.
    const _facets = spec.facets || {};
    toolbar._activeDivisions = new Set(Array.isArray(spec.applied_divisions) ? spec.applied_divisions : []);
    toolbar._activeDomains = new Set(Array.isArray(spec.applied_domains) ? spec.applied_domains : []);

    function _makeFacetGroup(facetKey, label, selectedSet, onChange) {
        const values = _facets[facetKey];
        if (!values || !Object.keys(values).length) return;  // conditional-on-presence
        const wrap = document.createElement('div');
        wrap.className = 'kg-toolbar-filters';
        const lbl = document.createElement('span');
        lbl.className = 'kg-facet-label';
        lbl.textContent = label;
        lbl.style.cssText = 'font-size:11px;color:var(--text-muted,#9ca3af);margin-right:4px;align-self:center;';
        wrap.appendChild(lbl);
        const anySelected = () => selectedSet.size > 0;
        Object.keys(values).sort().forEach(val => {
            const tile = document.createElement('button');
            tile.className = 'kg-toolbar-filter-pill';
            const accent = _themeColor('--teradata-orange', '#f15f22');
            tile.style.borderColor = accent + '60';
            tile.style.background = accent + '18';
            tile.style.color = accent;
            const disp = val.charAt(0).toUpperCase() + val.slice(1);
            tile.textContent = `${disp} ${values[val]}`;
            // Full opacity when selected, or when nothing is selected (no filter = all on).
            tile.style.opacity = (!anySelected() || selectedSet.has(val)) ? '1' : '0.35';
            tile.addEventListener('click', () => {
                if (!_canRequery) return;
                if (selectedSet.has(val)) selectedSet.delete(val); else selectedSet.add(val);
                // Restyle every tile in this group (selection changes "all-on" baseline).
                wrap.querySelectorAll('.kg-toolbar-filter-pill').forEach((t, i) => {
                    const v = Object.keys(values).sort()[i];
                    t.style.opacity = (selectedSet.size === 0 || selectedSet.has(v)) ? '1' : '0.35';
                });
                onChange([...selectedSet]);
            });
            wrap.appendChild(tile);
        });
        toolbar.appendChild(wrap);
    }

    const _facetRequery = () => requery(
        toolbar._activeTypes ? [...toolbar._activeTypes] : null,
        spec.requested_max || Math.max(nodes.length, 150),
        spec.applied_focus_names || null, spec.applied_project || false,
        [...toolbar._activeDivisions], [...toolbar._activeDomains]);

    _makeFacetGroup('division', 'Division', toolbar._activeDivisions, _facetRequery);
    _makeFacetGroup('domain', 'Domain', toolbar._activeDomains, _facetRequery);

    // ── Zoom fit button ──
    const fitBtn = _makeToolbarButton(_KG_ICONS.fit, 'Zoom to fit');
    fitBtn.addEventListener('click', () => {
        if (toolbar._onZoomFit) toolbar._onZoomFit();
    });
    // The ontology hosts fit/export in its own controls row (which has the space), so
    // they don't wrap to a lonely line under the pills bar.
    if (!(spec && spec.is_platform_ontology)) toolbar.appendChild(fitBtn);

    // ── Smart Autozoom toggle (viewport-driven level-of-detail; opt-in) ──
    if (_canRequery) {
        const azBtn = _makeToolbarButton(_KG_ICONS.auto,
            'Smart autozoom — re-allocate the node budget to the area you zoom into (off by default)');
        const _styleAz = () => {
            const accent = _themeColor('--teradata-orange', '#F15F22');
            if (_autoZoomEnabled) { azBtn.style.color = accent; azBtn.style.borderColor = accent + '60'; azBtn.style.background = accent + '1a'; }
            else { azBtn.style.color = ''; azBtn.style.borderColor = ''; azBtn.style.background = ''; }
        };
        _styleAz();
        azBtn.addEventListener('click', () => { _autoZoomEnabled = !_autoZoomEnabled; _styleAz(); });
        toolbar.appendChild(azBtn);
    }

    // ── Export PNG button ──
    const exportBtn = _makeToolbarButton(_KG_ICONS.export, 'Export as PNG');
    exportBtn.addEventListener('click', () => {
        if (toolbar._onExportPNG) toolbar._onExportPNG();
    });
    if (!(spec && spec.is_platform_ontology)) toolbar.appendChild(exportBtn);

    // ── Dependency overlay toggle (show ALL derived edges, in place) ──
    // Only meaningful under a type filter (there are hidden levels to lift into derived
    // edges). Delegates to _onToggleDeps (wired by _renderFullGraph): draws/clears the
    // dashed derived edges in place — no re-query, no re-layout. (Per-node deps also
    // appear on HOVER without this toggle.)
    if (_canRequery && Array.isArray(spec.applied_types) && spec.applied_types.length) {
        const depsBtn = _makeToolbarButton(_KG_ICONS.deps,
            'Show derived dependencies between the visible nodes (or just hover a node for its own)');
        depsBtn.addEventListener('click', () => {
            if (toolbar._onToggleDeps) toolbar._onToggleDeps(depsBtn);
        });
        toolbar.appendChild(depsBtn);
    }

    // ── Schema overview (the always-complete type-level meta-graph) ──
    if (_canRequery) {
        const schemaBtn = _makeToolbarButton(_KG_ICONS.schema, 'Schema overview — every type and how they connect');
        schemaBtn.addEventListener('click', () => { if (toolbar._onSchema) toolbar._onSchema(); });
        toolbar.appendChild(schemaBtn);
    }

    // ── Version history (same pattern as canvas/flow) ──
    // Only shown for persisted KGs whose spec carries kg_id + profile_id.
    if (spec && spec.kg_id && spec.profile_id) {
        toolbar.appendChild(_buildKGHistoryControl(spec));
    }

    // Store search input ref for graph to wire up
    toolbar._searchInput = searchInput;

    // ── Ontology: collapse the whole search + filter bar (persisted) ──
    // Lets the user reclaim the space; a small chevron + the node/edge stats stay as a
    // stub to bring it back. Only on the platform ontology (KG inspector toolbar is
    // untouched).
    if (spec && spec.is_platform_ontology) {
        const KEY = 'uderia.onto.toolbarCollapsed';
        const chev = document.createElement('button');
        chev.className = 'kg-onto-collapse';
        chev.title = 'Hide / show the search and filter bar';
        chev.style.cssText = 'flex-shrink:0;background:transparent;border:none;color:var(--text-muted,#9ca3af);cursor:pointer;padding:2px 6px;display:flex;align-items:center;';
        let collapsed = false;
        try { collapsed = localStorage.getItem(KEY) === '1'; } catch (_) {}
        const apply = () => {
            [...toolbar.children].forEach(ch => {
                if (ch === chev || ch.classList.contains('kg-toolbar-stats')) return;
                ch.style.display = collapsed ? 'none' : '';
            });
            chev.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="transition:transform .15s;transform:rotate(${collapsed ? -90 : 0}deg)"><polyline points="6 9 12 15 18 9"/></svg>`;
        };
        chev.addEventListener('click', () => {
            collapsed = !collapsed;
            try { localStorage.setItem(KEY, collapsed ? '1' : '0'); } catch (_) {}
            apply();
        });
        toolbar.insertBefore(chev, toolbar.firstChild);
        apply();
    }

    return toolbar;
}

// ─── Version History control (dropdown + restore) ───────────────────────────
function _buildKGHistoryControl(spec) {
    const wrap = document.createElement('div');
    wrap.style.cssText = 'position:relative;display:inline-flex;';

    const btn = _makeToolbarButton('History', 'Version history');
    btn.innerHTML = _KG_ICONS.history
        + '<span style="margin-left:5px;">History</span>'
        + '<span class="pc-badge pc-badge--count pc-badge--info kg-ver-count" style="display:none;margin-left:4px;"></span>';
    btn.style.cssText += 'font-size:0.7rem;padding:4px 10px;width:auto;gap:5px;';
    wrap.appendChild(btn);
    // Populate the count badge (parity with the flow History control).
    (async () => {
        try {
            const tok = localStorage.getItem('tda_auth_token');
            const r = await fetch(`/api/v1/knowledge-graph/versions?profile_id=${encodeURIComponent(spec.profile_id)}`,
                { headers: { 'Authorization': `Bearer ${tok}` } });
            if (!r.ok) return;
            const count = ((await r.json()).versions || []).length;
            const badge = btn.querySelector('.kg-ver-count');
            if (badge && count > 0) { badge.textContent = String(count); badge.style.display = 'inline-flex'; }
        } catch (_) {}
    })();

    const dropdown = document.createElement('div');
    dropdown.style.cssText = [
        'position:absolute', 'top:calc(100% + 6px)', 'right:0', 'min-width:240px',
        'max-height:320px', 'overflow-y:auto', 'z-index:50', 'display:none',
        'background:var(--card-bg, #1e293b)', 'border:1px solid var(--border-primary, rgba(255,255,255,0.12))',
        'border-radius:8px', 'padding:6px', 'box-shadow:0 8px 24px rgba(0,0,0,0.35)',
    ].join(';');
    wrap.appendChild(dropdown);

    const _close = () => { dropdown.style.display = 'none'; };
    const _onDocClick = (e) => { if (!wrap.contains(e.target)) _close(); };

    btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (dropdown.style.display === 'block') { _close(); return; }
        dropdown.style.display = 'block';
        document.addEventListener('click', _onDocClick);
        await _loadKGVersionItems(dropdown, spec);
    });

    // Snapshot-now action at the top
    return wrap;
}

async function _loadKGVersionItems(dropdown, spec) {
    dropdown.innerHTML = `<div style="padding:8px;color:var(--text-muted,#94a3b8);font-size:0.7rem">Loading…</div>`;
    const token = localStorage.getItem('tda_auth_token');
    const muted = 'var(--text-muted, #94a3b8)';

    // Snapshot button
    const snapBtn = document.createElement('button');
    snapBtn.textContent = '+ Snapshot current graph';
    snapBtn.style.cssText = `width:100%;text-align:left;padding:6px 8px;margin-bottom:4px;border:none;border-radius:6px;cursor:pointer;background:color-mix(in srgb, var(--sp-accent, #F15F22) 12%, transparent);color:var(--text-primary,#e2e8f0);font-size:0.7rem;`;
    snapBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        snapBtn.disabled = true; snapBtn.textContent = 'Saving…';
        try {
            await fetch('/api/v1/knowledge-graph/versions', {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
                body: JSON.stringify({ profile_id: spec.profile_id }),
            });
        } catch (_) {}
        await _loadKGVersionItems(dropdown, spec);
    });

    let versions = [];
    try {
        const r = await fetch(`/api/v1/knowledge-graph/versions?profile_id=${encodeURIComponent(spec.profile_id)}`,
            { headers: { 'Authorization': `Bearer ${token}` } });
        if (r.ok) versions = (await r.json()).versions || [];
    } catch (_) {}

    dropdown.innerHTML = '';
    dropdown.appendChild(snapBtn);

    if (!versions.length) {
        const empty = document.createElement('div');
        empty.style.cssText = `padding:8px;color:${muted};font-size:0.7rem`;
        empty.textContent = 'No saved versions yet.';
        dropdown.appendChild(empty);
        return;
    }

    // newest first
    versions.slice().reverse().forEach((v, idx) => {
        const isLatest = idx === 0;
        const item = document.createElement('div');
        item.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:8px;padding:6px 8px;border-radius:6px;';
        item.onmouseenter = () => { item.style.background = 'var(--hover-bg-strong, rgba(255,255,255,0.06))'; };
        item.onmouseleave = () => { item.style.background = 'transparent'; };

        const label = document.createElement('div');
        const when = v.created_at ? new Date(v.created_at).toLocaleString() : '';
        label.innerHTML =
            `<div style="font-size:0.72rem;color:var(--text-primary,#e2e8f0)">Version ${v.version_num}${isLatest ? ' (latest)' : ''}</div>` +
            `<div style="font-size:0.62rem;color:${muted}">${nodesLabel(v.entity_count)} · ${v.rel_count} rels${when ? ' · ' + when : ''}</div>`;
        item.appendChild(label);

        if (!isLatest) {
            const restore = document.createElement('button');
            restore.textContent = 'Restore';
            restore.style.cssText = `padding:3px 10px;border:1px solid var(--border-secondary,rgba(255,255,255,0.15));border-radius:5px;cursor:pointer;background:transparent;color:var(--teradata-orange,#F15F22);font-size:0.65rem;`;
            restore.addEventListener('click', async (e) => {
                e.stopPropagation();
                restore.disabled = true; restore.textContent = 'Restoring…';
                try {
                    const rr = await fetch(`/api/v1/knowledge-graph/versions/${v.version_id}/restore`, {
                        method: 'POST',
                        headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
                        body: JSON.stringify({ profile_id: spec.profile_id }),
                    });
                    if (rr.ok) {
                        dropdown.style.display = 'none';
                        await _reloadKGPanel(spec);
                    } else {
                        restore.disabled = false; restore.textContent = 'Restore';
                    }
                } catch (_) {
                    restore.disabled = false; restore.textContent = 'Restore';
                }
            });
            item.appendChild(restore);
        }
        dropdown.appendChild(item);
    });
}

// THE single canonical split-panel loader. Fetches a graph's spec through the EXACT
// same `/graph-spec` pipeline the Inspect view uses (default logical lens + full type
// catalog + edges), so every split-panel entry point — the conversation 'open', the
// inline "Open in Graph" button, session restore, and the switcher — renders byte-for-
// byte identically to Inspect. One fetch site = no drift between the surfaces.
// Returns the canonical spec (with nodes), or null on failure / empty graph.
async function _fetchCanonicalSpec(kgId, profileId, opts) {
    const token = localStorage.getItem('tda_auth_token');
    const params = new URLSearchParams();
    if (profileId) params.set('profile_id', profileId);
    if (kgId) params.set('kg_id', kgId);
    params.set('max_nodes', '500');
    params.set('types', DEFAULT_INSPECT_TYPES);
    // PROP-008: the author's own drafts, drawn in context (opened from a draft row)
    if (opts && opts.includeDrafts) params.set('include_drafts', '1');
    if (opts && Array.isArray(opts.focusNames) && opts.focusNames.length) params.set('focus_names', opts.focusNames.join(','));
    try {
        const r = await fetch(`/api/v1/knowledge-graph/graph-spec?${params.toString()}`,
            { headers: { 'Authorization': `Bearer ${token}` } });
        if (!r.ok) return null;
        const data = await r.json();
        const spec = data && data.spec;
        return (spec && (spec.nodes || []).length) ? spec : null;
    } catch (_) {
        return null;
    }
}

// Switch the open panel to a different knowledge graph (from the .sp-header switcher).
async function _switchKGInPanel(item) {
    const profileId = item && item.profile_id;
    if (!profileId) return;
    const canon = await _fetchCanonicalSpec(item.kg_id, profileId);
    if (!canon) return;   // leave current panel as-is on failure
    canon.kg_id = item.kg_id;
    canon.profile_id = profileId;
    canon.title = item.kg_name || item.profile_name || 'Knowledge Graph';
    canon.action = 'open';
    canon._canonical = true;   // already canonical → openKGSplitPanel renders directly
    openKGSplitPanel(canon);
}

// Re-fetch the graph after a restore and re-render the panel in place.
async function _reloadKGPanel(spec) {
    const kept = Array.isArray(spec.applied_focus_names) && spec.applied_focus_names.length ? spec.applied_focus_names : null;
    const canon = await _fetchCanonicalSpec(spec.kg_id, spec.profile_id, { includeDrafts: !!spec.include_drafts, focusNames: kept });
    if (!canon) return;   // leave current panel as-is on failure
    canon.kg_id = spec.kg_id;
    canon.profile_id = spec.profile_id;
    canon._canonical = true;
    if (kept) canon.applied_focus_names = kept;   // the seed survives the reload; no new zoom
    openKGSplitPanel(canon);
}


// PROP-008 §2b: an UNPUBLISHED authored node — the server's own rule (_unpublished_authored),
// read here only to SAY it on a node the author asked to see (the model view never carries one).
function _kgIsDraft(d) {
    const p = (d && d.properties) || {};
    return p.layer === 'authored' && (p.status || 'draft') !== 'published';
}

// Live: the authored layer changed (a draft, a landing, a publish — from the answer, the
// Govern dialog or the Workspace) ⇒ an open panel on that graph redraws in place, keeping its
// view and whether it shows the author's drafts. No page refresh.
if (typeof document !== 'undefined' && !window.__kgAuthoredLiveBound) {
    window.__kgAuthoredLiveBound = true;
    UderiaBus.onComponent('knowledge_graph', (e) => {
        try {
            const d = (e && e.detail) || {};
            const kg = (d.ids || [])[0] || null;
            if (!_activeSpec || !_graphState) return;
            if (kg && _activeSpec.kg_id && kg !== _activeSpec.kg_id) return;
            preserveKGView();
            _reloadKGPanel(_activeSpec);
        } catch (_) { /* a redraw is a convenience, never an error */ }
    });
}


// ─── Overview lens: type-level schema meta-graph ──────────────────────────────
// Always tiny + complete (one node per entity type, sized by count; edges = aggregated
// type→type relationships). Click a type to drill into the detail view filtered to it.
function _renderSchemaOverview(container, overview, colors, onDrill) {
    _clearAllAnimations();
    _graphState = null;                 // leaving the detail force graph
    container.innerHTML = '';
    const nodes = (overview.nodes || []).map(n => ({ ...n, id: n.type }));
    const links = (overview.edges || []).map(e => ({
        source: e.source_type, target: e.target_type, rel: e.relationship_type, count: e.count }));
    if (!nodes.length) {
        container.innerHTML = '<p style="color:var(--text-muted,#9ca3af);text-align:center;padding:2rem">No schema.</p>';
        return;
    }
    const w = container.clientWidth || 800, h = container.clientHeight || 600;
    const fallback = _themeColor('--text-muted', '#6b7280');
    const svg = d3.select(container).append('svg')
        .attr('width', '100%').attr('height', '100%').attr('viewBox', `0 0 ${w} ${h}`)
        .style('font-family', "'Inter', system-ui, sans-serif");
    const g = svg.append('g');
    svg.call(d3.zoom().scaleExtent([0.4, 3]).on('zoom', e => g.attr('transform', e.transform)));

    const maxC = Math.max(1, ...nodes.map(n => n.count || 1));
    const radius = n => 20 + 30 * Math.sqrt((n.count || 1) / maxC);

    // Arrow marker for directed type→type edges.
    svg.append('defs').append('marker').attr('id', 'kg-ov-arrow')
        .attr('viewBox', '0 -5 10 10').attr('refX', 9).attr('refY', 0)
        .attr('markerWidth', 6).attr('markerHeight', 6).attr('orient', 'auto')
        .append('path').attr('d', 'M0,-5L10,0L0,5').attr('fill', fallback);

    const link = g.append('g').selectAll('line').data(links).join('line')
        .attr('stroke', fallback).attr('stroke-opacity', 0.5)
        .attr('stroke-width', d => 1 + Math.log10(1 + (d.count || 1)))
        .attr('marker-end', 'url(#kg-ov-arrow)');
    const elabel = g.append('g').selectAll('text').data(links).join('text')
        .text(d => `${d.rel} (${(d.count || 0).toLocaleString()})`)
        .attr('font-size', 9).attr('fill', fallback).attr('text-anchor', 'middle')
        .style('pointer-events', 'none');

    const node = g.append('g').selectAll('g').data(nodes).join('g')
        .style('cursor', 'pointer')
        .on('click', (e, d) => { if (onDrill) onDrill(d.type); });
    node.append('circle').attr('r', radius)
        .attr('fill', d => (colors[d.type] || fallback) + '33')
        .attr('stroke', d => colors[d.type] || fallback).attr('stroke-width', 2);
    node.append('text').text(d => d.type).attr('text-anchor', 'middle').attr('dy', -2)
        .attr('font-size', 12).attr('font-weight', 600).attr('fill', _themeColor('--text-primary', '#fff'))
        .style('pointer-events', 'none');
    node.append('text').text(d => (d.count || 0).toLocaleString())
        .attr('text-anchor', 'middle').attr('dy', 15).attr('font-size', 11)
        .attr('fill', _themeColor('--text-muted', '#9ca3af')).style('pointer-events', 'none');
    node.append('title').text(d => `${d.type}: ${(d.count || 0).toLocaleString()} — click to drill into the detail view`);

    const sim = d3.forceSimulation(nodes)
        .force('link', d3.forceLink(links).id(d => d.id).distance(170))
        .force('charge', d3.forceManyBody().strength(-900))
        .force('center', d3.forceCenter(w / 2, h / 2))
        .force('collide', d3.forceCollide().radius(n => radius(n) + 34));
    sim.on('tick', () => {
        link.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
            .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
        elabel.attr('x', d => (d.source.x + d.target.x) / 2).attr('y', d => (d.source.y + d.target.y) / 2);
        node.attr('transform', d => `translate(${d.x},${d.y})`);
    });
}


function _renderFullGraph(container, nodes, links, colors, width, height, toolbar) {
    const fallbackColor = _themeColor('--text-muted', '#6b7280');

    // ─── SVG Setup ───────────────────────────────────────────────────
    const _isOnto = !!(_activeSpec && _activeSpec.is_platform_ontology);
    const svg = d3.select(container)
        .append('svg')
        .attr('width', '100%')
        .attr('height', '100%')
        .attr('viewBox', `0 0 ${width} ${height}`)
        .classed('kg-onto', _isOnto)   // dot-graph styling + labels-on-hover for the ontology
        .style('font-family', "'Inter', system-ui, sans-serif");

    // ─── Defs: Filters, Gradients, Markers ───────────────────────────
    const defs = svg.append('defs');

    // Drop shadow filter
    const shadow = defs.append('filter').attr('id', 'kg-shadow').attr('x', '-40%').attr('y', '-40%').attr('width', '180%').attr('height', '180%');
    shadow.append('feDropShadow').attr('dx', 0).attr('dy', 2).attr('stdDeviation', 4).attr('flood-color', 'rgba(0,0,0,0.5)');

    // Glow filter (per entity type)
    Object.entries(colors).forEach(([type, color]) => {
        const glow = defs.append('filter').attr('id', `kg-glow-${type}`).attr('x', '-50%').attr('y', '-50%').attr('width', '200%').attr('height', '200%');
        glow.append('feGaussianBlur').attr('in', 'SourceGraphic').attr('stdDeviation', 6).attr('result', 'blur');
        glow.append('feFlood').attr('flood-color', color).attr('flood-opacity', 0.4).attr('result', 'color');
        glow.append('feComposite').attr('in', 'color').attr('in2', 'blur').attr('operator', 'in').attr('result', 'glow');
        const merge = glow.append('feMerge');
        merge.append('feMergeNode').attr('in', 'glow');
        merge.append('feMergeNode').attr('in', 'SourceGraphic');
    });

    // Activity glow filter (warm amber — shared for live execution animations)
    const actGlow = defs.append('filter').attr('id', 'kg-activity-glow').attr('x', '-60%').attr('y', '-60%').attr('width', '220%').attr('height', '220%');
    actGlow.append('feGaussianBlur').attr('in', 'SourceGraphic').attr('stdDeviation', 8).attr('result', 'blur');
    actGlow.append('feFlood').attr('flood-color', '#F15F22').attr('flood-opacity', 0.5).attr('result', 'color');
    actGlow.append('feComposite').attr('in', 'color').attr('in2', 'blur').attr('operator', 'in').attr('result', 'glow');
    const actMerge = actGlow.append('feMerge');
    actMerge.append('feMergeNode').attr('in', 'glow');
    actMerge.append('feMergeNode').attr('in', 'SourceGraphic');

    // Pulse animation for center entity
    defs.append('style').text(`
        @keyframes kg-pulse { 0%, 100% { opacity: 0.6; } 50% { opacity: 0.15; } }
        .kg-center-ring { animation: kg-pulse 2s ease-in-out infinite; }
        .kg-node-label { pointer-events: none; user-select: none; }
        .kg-edge-label { pointer-events: none; user-select: none; }
        /* Ontology: a clean dot-graph — labels appear on hover/focus only (no soup),
           and the per-edge labels are hidden (the 'ran'/'contains' soup). */
        .kg-onto .kg-node-label { opacity: 0; transition: opacity .12s ease; }
        .kg-onto .kg-node:hover .kg-node-label,
        .kg-onto .kg-node.kg-onto-focus .kg-node-label { opacity: 1; }
        .kg-onto .kg-edge-label { opacity: 0; }
        /* The reflected node reveals its label in bold; the ring itself is the SAME
           orange halo a graph-click draws (_setFocusRing), applied from the tree path. */
        .kg-onto .kg-node.kg-onto-focus .kg-node-name { font-weight: 700; opacity: 1; }
        /* A focused node's direct neighbours (focusKGNodes opts.primary): full-opacity dots,
           and — when the neighbourhood is small — their labels at reduced emphasis. */
        .kg-onto .kg-node.kg-onto-near .kg-node-label { opacity: .8; }
    `);

    // Gradient for each link (source color -> target color). Keyed by a STABLE per-link
    // uid (not the array index) so links merged in later get their own gradient and the
    // existing ones don't shift. One mechanism for initial + incremental.
    let _linkUidSeq = 0;
    function _ensureGradient(link) {
        if (link._uid == null) link._uid = 'g' + (_linkUidSeq++);
        if (defs.select(`#kg-grad-${link._uid}`).size()) return;
        const s = typeof link.source === 'object' ? link.source : nodes[link.source];
        const t = typeof link.target === 'object' ? link.target : nodes[link.target];
        const grad = defs.append('linearGradient').attr('id', `kg-grad-${link._uid}`).attr('gradientUnits', 'userSpaceOnUse');
        grad.append('stop').attr('offset', '0%').attr('stop-color', colors[s?.type] || fallbackColor);
        grad.append('stop').attr('offset', '100%').attr('stop-color', colors[t?.type] || fallbackColor);
    }
    links.forEach(_ensureGradient);
    // Solid per-edge stroke (source-type colour). Replaces the per-link gradient for the
    // line itself: the gradient's coords are only set in the tick HANDLER, which manual
    // pre-warm ticks never fire — so on a static/short-warmed layout the gradient had no
    // coordinates and every line painted invisibly (labels stayed visible). A solid
    // stroke is always painted, regardless of sim/gradient timing.
    function _linkStroke(d) {
        if (d.type === 'foreign_key' && !d.derived) return KG_FK_COLOR;
        const s = typeof d.source === 'object' ? d.source : nodes[d.source];
        return colors[s && s.type] || fallbackColor;
    }

    // Arrowhead marker
    defs.append('marker')
        .attr('id', 'kg-arrow')
        .attr('viewBox', '0 -5 10 10')
        .attr('refX', 28).attr('refY', 0)
        .attr('markerWidth', 6).attr('markerHeight', 6)
        .attr('orient', 'auto')
        .append('path')
        .attr('d', 'M0,-4L8,0L0,4');

    // ─── Zoom ────────────────────────────────────────────────────────
    const g = svg.append('g');

    const zoom = d3.zoom()
        .scaleExtent([0.1, 4])
        .on('zoom', (event) => {
            g.attr('transform', event.transform);
            // Detect user-initiated zoom/pan (sourceEvent is truthy for mouse/touch)
            if (event.sourceEvent) _userZoomOverride = true;
        });
    svg.call(zoom);

    // Re-apply the preserved viewport after an autozoom re-query, so the LOD re-render
    // densifies the area you're looking at WITHOUT the viewport jumping back to fit-all.
    if (_pendingAutoZoom) {
        const t = _pendingAutoZoom; _pendingAutoZoom = null;
        svg.call(zoom.transform, d3.zoomIdentity.translate(t.x, t.y).scale(t.k));
        _userZoomOverride = true;
    }

    // By default zoom is PURE pan/scale. SMART AUTOZOOM (opt-in toolbar toggle): on zoom-end,
    // re-allocate the node budget to the area you're looking at — re-query focused on the
    // currently-visible nodes. Zoom IN → smaller visible set → the budget densifies that
    // region (more detail). Zoom OUT → larger visible set → the budget spreads (broader
    // overview). Debounced + in-flight-guarded; the viewport + carried positions are
    // preserved so it reads as the area filling in, not a refocus.
    let _autoZoomTimer = null;
    zoom.on('zoom.auto', (event) => {
        if (!event.sourceEvent || !_autoZoomEnabled) return;     // only real user zoom, when enabled
        if (!_activeRequery || !_activeSpec || (!_activeSpec.kg_id && !_activeSpec.profile_id)) return;
        if (_autoZoomTimer) clearTimeout(_autoZoomTimer);
        _autoZoomTimer = setTimeout(() => {
            if (_focusRequeryInFlight) return;
            const t = d3.zoomTransform(svg.node());
            const visible = nodes.filter(n => {
                if (n.x == null || n.y == null) return false;
                const sx = t.applyX(n.x), sy = t.applyY(n.y);
                return sx >= 0 && sx <= width && sy >= 0 && sy <= height;
            });
            if (visible.length < 2) return;   // nothing meaningful in view
            // Centroid of the visible region (graph coords) so new detail seeds there.
            const cx = visible.reduce((s, n) => s + n.x, 0) / visible.length;
            const cy = visible.reduce((s, n) => s + n.y, 0) / visible.length;
            // Cap the focus seeds (a huge zoomed-out viewport could otherwise send hundreds
            // of names → an over-long URL). The sampler expands from the seeds to fill budget,
            // so a representative subset is enough; bias toward the highest-importance ones.
            const focusNames = visible
                .slice().sort((a, b) => (b.importance || 0) - (a.importance || 0))
                .slice(0, 80).map(n => n.name).filter(Boolean);
            _focusRequeryInFlight = true;
            _pendingAutoZoom = { x: t.x, y: t.y, k: t.k };
            _autoZoomSeed = { cx, cy };
            Promise.resolve(_activeRequery(
                _activeSpec.applied_types || null,
                _activeSpec.requested_max || Math.max(nodes.length, 150),
                focusNames,
                _activeSpec.applied_project || false
            )).finally(() => { _focusRequeryInFlight = false; });
        }, 420);
    });

    // ─── Force Simulation ────────────────────────────────────────────
    // Carry positions from the previous view (set by a re-query) so persisting
    // nodes keep their place — changing the slider/pills no longer reshuffles the
    // whole layout. New nodes start near the centre and settle in around them.
    let _carried = 0;
    const _seed = _autoZoomSeed; _autoZoomSeed = null;   // seed new nodes into the visible region
    if (_prevPositions && _prevPositions.size) {
        nodes.forEach(n => {
            const p = _prevPositions.get(n.entity_id);
            if (p) {
                n.x = p.x; n.y = p.y; n.vx = 0; n.vy = 0; _carried++;
                if (_seed) { n.fx = p.x; n.fy = p.y; }   // autozoom: pin the visible region as anchors
            } else if (_seed) {   // autozoom: NEW detail nestles into the zoomed area, not the centre
                const a = Math.random() * Math.PI * 2, r = 30 + Math.random() * 90;
                n.x = _seed.cx + Math.cos(a) * r; n.y = _seed.cy + Math.sin(a) * r;
            }
        });
        _prevPositions = null;
    }
    // Release the autozoom anchors once the new detail has settled, so dragging works again.
    if (_seed) setTimeout(() => { nodes.forEach(n => { n.fx = null; n.fy = null; }); }, 1500);

    // Platform ontology: AGENT-CENTRIC hub-and-spoke. Agents are the hub everything
    // attaches to, so they (+ class groups + the root) anchor at the CENTRE, and the
    // supporting departments (Memory, Sessions, Intelligence, Components, …) ring around
    // them — edges radiate outward symmetrically instead of fanning from one corner.
    // Guarded so other KG views are untouched.
    let _clusterX = null, _clusterY = null;
    if (_activeSpec && _activeSpec.is_platform_ontology) {
        const RING_ORDER = ['Memory', 'Sessions', 'Intelligence', 'Components', 'Users', 'Ontology', 'Other'];
        // Root + class groups + agents form the central hub; everything else rings it.
        const catOf = (n) => (n.type === 'segment')
            ? 'Agents' : (((n.properties && n.properties.taxonomy) || {}).category || 'Other');
        const ring = [];
        nodes.forEach(n => { const c = catOf(n); if (c && c !== 'Agents' && !ring.includes(c)) ring.push(c); });
        ring.sort((a, b) => {
            const ia = RING_ORDER.indexOf(a), ib = RING_ORDER.indexOf(b);
            return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
        });
        const cx = width / 2, cy = height / 2, R = Math.min(width, height) * 0.42;
        const anchor = { Agents: { x: cx, y: cy } };
        // Within the central hub, fan agents by IFOC class into four sub-wedges so each
        // engine class (and each agent) is readable instead of one blob. Class groups
        // join their class wedge; the root + packs stay dead-centre.
        const IFOC = ['ideate', 'focus', 'optimize', 'coordinate'];
        const rHub = R * 0.4;
        const subAnchor = {};
        IFOC.forEach((cls, i) => {
            const a = (i / IFOC.length) * Math.PI * 2 - Math.PI / 2;
            subAnchor[cls] = { x: cx + Math.cos(a) * rHub, y: cy + Math.sin(a) * rHub };
        });
        const ifocOf = (n) => {
            const t = n.type || '';
            if (t.startsWith('agent_')) return t.slice('agent_'.length);
            if (t === 'class_group') return String((n.properties || {}).ifoc_class || '').toLowerCase();
            return null;
        };
        const _deptG = g.append('g').attr('class', 'onto-dept-labels').attr('pointer-events', 'none');
        // Centre "AGENTS" label, sitting just above the hub.
        _deptG.append('text')
            .attr('x', cx).attr('y', cy - R * 0.62)
            .attr('text-anchor', 'middle').attr('dominant-baseline', 'middle')
            .attr('fill', _themeColor('--text-muted', '#94a3b8')).attr('opacity', 0.5)
            .attr('font-size', 14).attr('font-weight', 800).attr('letter-spacing', '0.08em')
            .text('AGENTS');
        ring.forEach((c, i) => {
            const a = (i / Math.max(1, ring.length)) * Math.PI * 2 - Math.PI / 2;
            anchor[c] = { x: cx + Math.cos(a) * R, y: cy + Math.sin(a) * R };
            // A faint region label pushed just outside the cluster so it titles the area.
            _deptG.append('text')
                .attr('x', cx + Math.cos(a) * R * 1.34).attr('y', cy + Math.sin(a) * R * 1.34)
                .attr('text-anchor', 'middle').attr('dominant-baseline', 'middle')
                .attr('fill', _themeColor('--text-muted', '#94a3b8')).attr('opacity', 0.4)
                .attr('font-size', 14).attr('font-weight', 800).attr('letter-spacing', '0.08em')
                .text(String(c).toUpperCase());
        });
        const hubAnchor = (n) => {
            const cls = ifocOf(n);
            return (cls && subAnchor[cls]) ? subAnchor[cls] : anchor.Agents;   // root/packs → centre
        };
        const ax = (n) => { const c = catOf(n); return c === 'Agents' ? hubAnchor(n).x : ((c && anchor[c]) ? anchor[c].x : cx); };
        const ay = (n) => { const c = catOf(n); return c === 'Agents' ? hubAnchor(n).y : ((c && anchor[c]) ? anchor[c].y : cy); };
        // Agents pull firmly to their IFOC sub-wedge (so the four classes separate);
        // ring departments pull a touch stronger still.
        _clusterX = d3.forceX(ax).strength(n => catOf(n) === 'Agents' ? 0.6 : 0.66);
        _clusterY = d3.forceY(ay).strength(n => catOf(n) === 'Agents' ? 0.6 : 0.66);
    }

    const simulation = d3.forceSimulation(nodes)
        // Clustered layout uses short, weak links so the department anchors dominate
        // (cross-department edges don't drag a node out of its neighbourhood).
        .force('link', _clusterX
            ? d3.forceLink(links).id(d => d.id).distance(24).strength(0.03)
            : d3.forceLink(links).id(d => d.id).distance(120))
        .force('charge', d3.forceManyBody().strength(_clusterX ? -55 : -350))
        // Autozoom render OR clustering: NO centring force — clustering uses per-department
        // anchors; autozoom must stay anchored on the visible region.
        .force('center', (_seed || _clusterX) ? null : d3.forceCenter(width / 2, height / 2))
        .force('collision', d3.forceCollide().radius(_clusterX ? 11 : 45))
        .stop(); // Stop auto-run so we can pre-warm synchronously
    if (_clusterX) simulation.force('clusterX', _clusterX).force('clusterY', _clusterY);

    // Pre-warm: run ticks synchronously so nodes are near their final
    // positions before fade-in (prevents the "flying in" flicker). The
    // tick count scales with graph size — small graphs settle quickly,
    // so paying for 200 ticks every time was the main snappiness cost.
    // When most positions were carried over, use a SHORT pre-warm so the
    // existing layout is preserved (only new nodes settle), not re-laid-out.
    const _mostlyCarried = _carried >= nodes.length * 0.5;
    const prewarmTicks = _mostlyCarried
        ? Math.min(40, Math.max(12, Math.round(nodes.length * 0.2)))
        : Math.min(200, Math.max(60, Math.round(30 + nodes.length * 1.5)));
    for (let _i = 0; _i < prewarmTicks; ++_i) simulation.tick();

    // ─── Edges ───────────────────────────────────────────────────────
    // `let` (not const) so an incremental merge can re-select them and every closure
    // (tick, handlers) that references the variable picks up the new elements.
    let linkGroup = g.selectAll('.kg-link')
        .data(links)
        .join('g')
        .attr('class', 'kg-link');

    // Decorate a freshly-entered link <g> (line + label). Reused for the initial render
    // and for incrementally-merged links so they look identical (consistency).
    function _decorateLinkEnter(sel) {
        sel.append('line')
            // Foreign-key edges get a distinct solid amber so a key relationship stands out
            // from the neutral structural `contains` edges (which keep the node-type gradient).
            .attr('stroke', d => _linkStroke(d))
            .attr('stroke-width', d => d.derived ? 1 + Math.min(2, Math.log10(1 + (d.count || 1)))
                                       : (d.type === 'foreign_key' ? 2 : 1.5))
            .attr('stroke-dasharray', d => d.derived ? '5,4' : null)
            .attr('stroke-opacity', 0)
            .attr('marker-end', 'url(#kg-arrow)')
            .attr('x1', d => (d.source.x || 0)).attr('y1', d => (d.source.y || 0))
            .attr('x2', d => (d.target.x || 0)).attr('y2', d => (d.target.y || 0))
            .transition().delay(80).duration(220).attr('stroke-opacity', 0.5);
        sel.append('text')
            .attr('class', 'kg-edge-label')
            .text(d => (d.derived && (d.count || 0) > 1) ? `${d.type} ×${d.count}` : d.type)
            .attr('font-size', '9px').attr('text-anchor', 'middle').attr('dy', -6)
            .style('cursor', d => (d.derived && d.via && d.via.length) ? 'help' : null);
        // Native hover tooltip: for a derived edge, the join column(s) connecting the nodes.
        sel.select('line').append('title').text(_edgeTitle);
        sel.select('text.kg-edge-label').append('title').text(_edgeTitle);
    }

    linkGroup.append('line')
        // FK edges = distinct solid amber (a key relationship); structural `contains`
        // edges keep the neutral node-type gradient. Derived edges are dashed amber-orange.
        .attr('stroke', d => _linkStroke(d))
        .attr('stroke-width', d => d.derived ? 1 + Math.min(2, Math.log10(1 + (d.count || 1)))
                                   : (d.type === 'foreign_key' ? 2 : 1.5))
        .attr('stroke-dasharray', d => d.derived ? '5,4' : null)
        .attr('stroke-opacity', 0)            // Start invisible; fade in below
        .attr('marker-end', 'url(#kg-arrow)')
        // Pre-position lines from pre-warmed node coordinates so edges are
        // solid (not dashed) regardless of length — getTotalLength() returns 0
        // before any ticks run, making the old stroke-dashoffset approach
        // produce 200px dashes that cut off long edges.
        .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y)
        .transition()
        .delay(80)
        .duration(220)
        .attr('stroke-opacity', _isOnto ? 0.13 : 0.5);   // ontology: faint edges (de-clutter)

    let linkLines = linkGroup.select('line');

    // Edge labels (fill + opacity via CSS .kg-edge-label for theme compliance)
    linkGroup.append('text')
        .attr('class', 'kg-edge-label')
        .text(d => (d.derived && (d.count || 0) > 1) ? `${d.type} ×${d.count}` : d.type)
        .attr('font-size', '9px')
        .attr('text-anchor', 'middle')
        .attr('dy', -6)
        .style('cursor', d => (d.derived && d.via && d.via.length) ? 'help' : null);
    // Native hover tooltip: for a derived edge, the join column(s) connecting the nodes.
    linkGroup.select('line').append('title').text(_edgeTitle);
    linkGroup.select('text.kg-edge-label').append('title').text(_edgeTitle);

    // ─── Nodes ───────────────────────────────────────────────────────
    const nodeW = 24, nodeH = 24;

    // Painted labels = shortest unique suffix over the rendered set (see
    // _computeDisplayNames). `let` because an in-place neighbourhood merge can introduce
    // a collision that widens an EXISTING node's label — recomputed + re-applied there.
    let _displayNames = _computeDisplayNames(nodes);
    // Only ever called after the initial render (nodeGroups is bound by then).
    const _relabelNodes = () => {
        _displayNames = _computeDisplayNames(nodes);
        nodeGroups.select('text.kg-node-name').text(d => _kgNodeLabel(d, _displayNames, 22));
    };

    // Append the visual children of a freshly-entered .kg-node <g>. Reused for the initial
    // render AND for incrementally-merged nodes so they look identical (consistency).
    function _decorateNodeEnter(sel) {
        sel.filter(d => d.is_center)
            .append('circle').attr('class', 'kg-center-ring')
            .attr('r', 24).attr('fill', 'none')
            .attr('stroke', d => colors[d.type] || fallbackColor).attr('stroke-width', 2);
        // The ontology is a clean dot-graph: no heavy rounded-rect box, just a small
        // coloured dot (sized by importance) so 95 nodes don't overlap into blobs.
        if (!_isOnto) {
            sel.append('rect')
                .attr('x', -nodeW / 2).attr('y', -nodeH / 2)
                .attr('width', nodeW).attr('height', nodeH).attr('rx', 8).attr('ry', 8)
                .attr('fill', d => (colors[d.type] || fallbackColor) + '30')
                .attr('stroke', d => colors[d.type] || fallbackColor)
                .attr('stroke-width', 1.5).attr('filter', 'url(#kg-shadow)')
                // PROP-008: a node on the AUTHORED layer (a draft a person or an agent added on
                // top of the walked model) is drawn dashed — declared, not yet realised
                .attr('stroke-dasharray', d => ((d.properties || {}).layer === 'authored') ? '5 3' : null);
        }
        if (_isOnto) sel.append('circle').attr('class', 'kg-onto-hit').attr('r', 14).attr('fill', 'transparent');  // forgiving hover/click + focus-ring target
        sel.append('circle').attr('cx', 0).attr('cy', 0)
            .attr('r', d => _isOnto ? (3 + (d.importance || 0) * 5) : (5 + (d.importance || 0) * 15))
            .attr('fill', d => colors[d.type] || fallbackColor)
            .attr('stroke', d => (_isOnto && _kgIsDraft(d)) ? (colors[d.type] || fallbackColor) : (_isOnto ? 'rgba(0,0,0,0.35)' : null))
            .attr('stroke-width', _isOnto ? 1 : null)
            .attr('stroke-dasharray', d => (_isOnto && _kgIsDraft(d)) ? '2 2' : null);
        const _lx = _isOnto ? 11 : (nodeW / 2 + 6);
        sel.append('text').attr('class', 'kg-node-label kg-node-name')
            .text(d => _kgNodeLabel(d, _displayNames, 22))
            .attr('font-size', '11px').attr('dx', _lx).attr('dy', 4)
            .attr('font-weight', d => d.is_center ? '600' : '400');
        sel.append('text').attr('class', 'kg-node-label')
            .text(d => d.type + (_kgIsDraft(d) ? ' · draft' : '')).attr('font-size', '8px')
            .attr('fill', d => colors[d.type] || fallbackColor)
            .attr('dx', _lx).attr('dy', 16);
        // Key-constraint markers on columns: PK (gold, top-left) / FK (amber, top-right).
        // PK is a column PROPERTY (a key is intrinsic to one column, not a relationship),
        // so it can't be an edge — this badge is how it's surfaced; FK columns are the
        // endpoints of the amber foreign_key edges.
        const _keyBadge = (filterFn, text, color, dx) => {
            const grp = sel.filter(filterFn).append('g')
                .attr('class', 'kg-key-badge')
                .attr('transform', `translate(${dx}, ${-nodeH / 2 - 6})`)
                .style('pointer-events', 'none');
            grp.append('rect').attr('width', 15).attr('height', 11).attr('rx', 2.5).attr('fill', color);
            grp.append('text').attr('x', 7.5).attr('y', 8.5).attr('text-anchor', 'middle')
                .attr('font-size', '7px').attr('font-weight', '700').attr('fill', '#111').text(text);
        };
        _keyBadge(d => d.properties && d.properties.is_primary_key, 'PK', KG_PK_COLOR, -nodeW / 2 - 3);
        _keyBadge(d => d.properties && d.properties.is_foreign_key, 'FK', KG_FK_COLOR, nodeW / 2 - 12);
    }

    // `let` so a merge can re-select; closures (tick/handlers) read the variable.
    const _nodeEnter = g.selectAll('.kg-node').data(nodes).enter().append('g')
        .attr('class', 'kg-node').style('cursor', 'pointer')
        .attr('transform', d => `translate(${d.x},${d.y})`)   // pre-position at settled coords
        .style('opacity', 0);
    _decorateNodeEnter(_nodeEnter);
    _nodeEnter.transition().delay((d, i) => Math.min(i * 8, 120)).duration(220).style('opacity', 1);
    let nodeGroups = g.selectAll('.kg-node');

    // ─── Derived-dependency overlay (in-place, edges-only) ───────────────
    // A node's lifted dependencies (column↔column FKs lifted to table↔table, etc.) are
    // heavy to compute, so we fetch the derived-edge set LAZILY (once per view) and cache
    // it, then draw the dashed edges IN PLACE on the current node positions — no re-query,
    // no re-layout. Used by hover (reveal one node's deps) and the toolbar toggle (all).
    let _derivedCache = null;       // [{source: nodeObj, target: nodeObj, type, count}]
    let _derivedFetching = null;    // in-flight promise (dedupe concurrent hovers)
    let _overlayOn = false;         // manual toggle: all derived edges shown persistently
    // Insert BELOW the node cards (before the first .kg-node) so derived edges render
    // like regular links — behind the nodes, not over their labels.
    const _derivedG = g.insert('g', '.kg-node').attr('class', 'kg-derived-layer');
    let _derivedTickBound = false;

    async function _ensureDerived() {
        if (_derivedCache) return _derivedCache;
        if (_derivedFetching) return _derivedFetching;
        if (!_activeSpec || (!_activeSpec.kg_id && !_activeSpec.profile_id)) return [];
        const params = new URLSearchParams();
        if (_activeSpec.kg_id) params.set('kg_id', _activeSpec.kg_id);
        if (_activeSpec.profile_id) params.set('profile_id', _activeSpec.profile_id);
        params.set('max_nodes', String(_activeSpec.requested_max || Math.max(nodes.length, 150)));
        if (Array.isArray(_activeSpec.applied_types) && _activeSpec.applied_types.length)
            params.set('types', _activeSpec.applied_types.join(','));
        if (Array.isArray(_activeSpec.applied_focus_names) && _activeSpec.applied_focus_names.length)
            params.set('focus_names', _activeSpec.applied_focus_names.join(','));
        if (_activeSpec.include_drafts) params.set('include_drafts', '1');
        params.set('project', '1');
        const token = localStorage.getItem('tda_auth_token');
        _derivedFetching = (async () => {
            try {
                const r = await fetch(`/api/v1/knowledge-graph/graph-spec?${params}`,
                                      { headers: { Authorization: `Bearer ${token}` } });
                const data = await r.json();
                if (!data || data.status !== 'success' || !data.spec) { _derivedCache = []; return _derivedCache; }
                const pnodes = data.spec.nodes || [];
                const byEid = new Map(nodes.map(n => [n.entity_id, n]));
                const out = [];
                (data.spec.links || []).forEach(l => {
                    if (!l.derived) return;
                    const sE = pnodes[l.source] && pnodes[l.source].entity_id;
                    const tE = pnodes[l.target] && pnodes[l.target].entity_id;
                    const s = byEid.get(sE), t = byEid.get(tE);
                    if (s && t && s !== t) out.push({ source: s, target: t, type: l.type, count: l.count || 1, derived: true, via: l.via || [] });
                });
                _derivedCache = out;
            } catch (_) { _derivedCache = []; }
            finally { _derivedFetching = null; }
            return _derivedCache;
        })();
        return _derivedFetching;
    }

    function _positionDerived() {
        _derivedG.selectAll('line.kg-derived-line')
            .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
            .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    }

    function _drawDerived(list, transient) {
        const subClass = transient ? 'kg-derived-transient' : 'kg-derived-persist';
        const sel = _derivedG.selectAll('line.' + subClass)
            .data(list, d => d.source.entity_id + '|' + d.target.entity_id + '|' + d.type);
        sel.exit().remove();
        const ent = sel.enter().append('line')
            .attr('class', 'kg-derived-line ' + subClass)
            .attr('stroke', _themeColor('--teradata-orange', '#F15F22'))
            .attr('stroke-width', d => 1 + Math.min(2, Math.log10(1 + (d.count || 1))))
            .attr('stroke-dasharray', '5,4')
            .attr('stroke-opacity', 0)
            .attr('marker-end', 'url(#kg-arrow)')
            .style('cursor', d => (d.via && d.via.length) ? 'help' : null);
        // Native hover tooltip: which column(s) connect the two nodes (join columns).
        ent.append('title').text(_edgeTitle);
        ent.merge(sel)
            .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
            .attr('x2', d => d.target.x).attr('y2', d => d.target.y)
            .transition().duration(160).attr('stroke-opacity', 0.8);
        if (!_derivedTickBound) { simulation.on('tick.derived', _positionDerived); _derivedTickBound = true; }
    }

    function _clearDerived(onlyTransient) {
        _derivedG.selectAll(onlyTransient ? 'line.kg-derived-transient' : 'line.kg-derived-line').remove();
    }

    // Toolbar toggle (set in _buildToolbar's deps button) → show/hide ALL derived edges
    // in place. No re-query, no re-layout. _depsManual locks out nothing here (kept for
    // back-compat); the overlay is purely client-side now.
    if (toolbar) {
        toolbar._onToggleDeps = (btn) => {
            _overlayOn = !_overlayOn;
            const accent = _themeColor('--teradata-orange', '#F15F22');
            if (_overlayOn) {
                _ensureDerived().then(all => { if (_overlayOn) _drawDerived(all, false); });
                if (btn) { btn.style.color = accent; btn.style.borderColor = accent + '60'; btn.style.background = accent + '1a'; }
            } else {
                _clearDerived(false);
                if (btn) { btn.style.color = ''; btn.style.borderColor = ''; btn.style.background = ''; }
            }
        };
    }

    // Pre-warm the derived-edge cache when a type filter is active, so the FIRST hover
    // reveals dependencies instantly (the fetch is in flight before the user hovers).
    if (Array.isArray(_activeSpec && _activeSpec.applied_types) && _activeSpec.applied_types.length) {
        _ensureDerived();
    }

    // ─── Interaction handlers (named so merged nodes get identical wiring) ────
    // Best practice (Bloom / KeyLines): hovering a node highlights the node + its DIRECT
    // neighbours and the connecting edges, and dims everything else. Plus the node's derived
    // (lifted) dependencies when levels are hidden.
    let _hoverNode = null;
    let focusedNode = null;
    let _clickTimer = null;

    function _onNodeEnter(event, d) {
        if (focusedNode) return;   // an active selection owns the view
        _hoverNode = d;
        d3.select(this).select('rect').attr('filter', `url(#kg-glow-${d.type})`);
        const connected = new Set([d.id]);
        links.forEach(l => {
            const sid = typeof l.source === 'object' ? l.source.id : l.source;
            const tid = typeof l.target === 'object' ? l.target.id : l.target;
            if (sid === d.id) connected.add(tid);
            if (tid === d.id) connected.add(sid);
        });
        const dim = () => nodeGroups.style('opacity', n => connected.has(n.id) ? 1 : 0.12);
        dim();
        linkLines.attr('stroke-opacity', l => (l.source === d || l.target === d) ? 0.9 : 0.08);
        linkGroup.select('text').style('opacity', l => (l.source === d || l.target === d) ? 0.9 : 0.08);
        if (!_overlayOn) {
            _ensureDerived().then(all => {
                if (_hoverNode !== d || _overlayOn || focusedNode || !all || !all.length) return;
                const mine = all.filter(e => e.source === d || e.target === d);
                if (!mine.length) return;
                _drawDerived(mine, true);
                mine.forEach(e => { connected.add(e.source.id); connected.add(e.target.id); });
                dim();
            });
        }
    }
    function _onNodeLeave() {
        if (focusedNode) return;
        _hoverNode = null;
        d3.select(this).select('rect').attr('filter', 'url(#kg-shadow)');
        nodeGroups.style('opacity', 1);
        linkGroup.select('text').style('opacity', null);
        linkLines.attr('stroke-opacity', 0.5);
        _clearDerived(true);
    }

    // Restore the resting view (ring off, full opacity, focus-drawn deps removed — but NOT
    // the toolbar overlay's deps if it's on).
    function _clearFocusVisuals() {
        _setFocusRing(null);
        nodeGroups.interrupt().style('opacity', 1).select('rect').attr('filter', 'url(#kg-shadow)');
        linkLines.attr('stroke-opacity', 0.5);
        linkGroup.select('text').style('opacity', null);
        if (!_overlayOn) _clearDerived(false);
    }
    function _clearFocus() {
        if (!focusedNode) return;
        focusedNode = null;
        _clearFocusVisuals();
        // Additive selection seam: let external views (e.g. the Ontology panel)
        // react to deselection. Harmless to KG views, which never subscribe.
        try { UderiaBus.emit('uderia:kg-node-deselect'); } catch (_) {}
    }
    // Apply the focus dim for node d using the given connected-id set.
    function _applyFocusDim(d, connected) {
        nodeGroups.interrupt().style('opacity', n => connected.has(n.id) ? 1 : 0.15);
        linkLines.attr('stroke-opacity', l => (l.source === d || l.target === d) ? 0.9 : 0.05);
        linkGroup.select('text').style('opacity', l => (l.source === d || l.target === d) ? 0.9 : 0.05);
    }
    function _connectedSet(d) {
        const c = new Set([d.id]);
        links.forEach(l => {
            const sid = typeof l.source === 'object' ? l.source.id : l.source;
            const tid = typeof l.target === 'object' ? l.target.id : l.target;
            if (sid === d.id) c.add(tid);
            if (tid === d.id) c.add(sid);
        });
        return c;
    }

    // A persistent ring on the focused/pinned node so it stays unmistakable even after an
    // expand pulls its whole neighbourhood in (when "dim the rest" no longer distinguishes
    // it). The ring is a child of the node's <g>, so it follows the node via the tick and
    // survives the in-place merge. d=null clears it.
    function _setFocusRing(d) {
        g.selectAll('circle.kg-focus-ring').remove();
        if (!d) return;
        nodeGroups.filter(n => n === d)
            .insert('circle', ':first-child')   // behind the card → reads as a halo
            .attr('class', 'kg-focus-ring')
            .attr('cx', 0).attr('cy', 0).attr('r', 19).attr('fill', 'none')
            .attr('stroke', _themeColor('--teradata-orange', '#F15F22'))
            .attr('stroke-width', 2.5).attr('opacity', 0.95)
            .style('pointer-events', 'none');
    }

    // SINGLE CLICK = SELECT (inspect). Highlight the node + neighbours, ring it, and reveal
    // ITS derived dependency edges persistently (the dashed lifted FKs — hover one for the
    // join columns). Purely in-place — NO graph growth. Click it again / the background to
    // deselect. A 240 ms timer disambiguates from double-click.
    function _doSingleClick(d) {
        if (focusedNode === d) { _clearFocus(); return; }   // toggle off
        _clearFocusVisuals();                               // drop any prior selection's deps
        focusedNode = d;
        _setFocusRing(d);
        const connected = _connectedSet(d);
        _applyFocusDim(d, connected);
        // Reveal this node's derived dependencies (so you can see + hover its lifted FKs).
        if (!_overlayOn) {
            _ensureDerived().then(all => {
                if (focusedNode !== d || _overlayOn || !all || !all.length) return;
                const mine = all.filter(e => e.source === d || e.target === d);
                if (!mine.length) return;
                _drawDerived(mine, false);
                mine.forEach(e => { connected.add(e.source.id); connected.add(e.target.id); });
                _applyFocusDim(d, connected);
            });
        }
        // Additive selection seam: external views (e.g. the Ontology) open a detail
        // sidebar on single-click. Harmless to KG views, which never subscribe.
        try { UderiaBus.emit('uderia:kg-node-select', { node: d, spec: _activeSpec }); } catch (_) {}
    }

    // DOUBLE CLICK = EXPAND (drill in) — or, on a node already expanded, COLLAPSE (the
    // revert). Expand selects the node, then pulls its COMPLETE real neighbourhood into the
    // graph as nodes (out-of-budget neighbours included), growing it in place, and records
    // what it introduced in the expansion ledger; the node wears a collapse mark from then
    // on. A second double-click removes exactly what that expansion introduced (see
    // _collapseExpansion for the two survival rules), so the graph returns to what it was.
    function _doDoubleClick(d) {
        _clearFocusVisuals();        // drop any prior selection's derived overlay
        focusedNode = d;
        _setFocusRing(d);
        _applyFocusDim(d, _connectedSet(d));
        // The platform ontology owns its own (client-side) expansion; its synthetic
        // nodes have no KG-store neighbourhood, so the backend reveal would 400. Skip it
        // there and let the Ontology handler act on the event below.
        if (!(_activeSpec && _activeSpec.is_platform_ontology)) {
            if (_expansions.has(d.id)) {
                _collapseExpansion(d);
                _applyFocusDim(d, _connectedSet(d));
                _setFocusRing(d);
            } else {
                _revealComplete(d.entity_id, merged => {
                    if (!merged || focusedNode !== d) return;
                    _applyFocusDim(d, _connectedSet(d));
                    _setFocusRing(d);
                }, true);
            }
        }
        // Additive expand seam: external views (e.g. the Ontology) handle double-click
        // contextually — a KG node expands its contents, an agent expands its parts.
        try { UderiaBus.emit('uderia:kg-node-dblclick', { node: d, spec: _activeSpec }); } catch (_) {}
    }

    function _onNodeClick(event, d) {
        event.stopPropagation();
        if (_clickTimer) { clearTimeout(_clickTimer); _clickTimer = null; return; } // part of a dblclick
        _clickTimer = setTimeout(() => { _clickTimer = null; _doSingleClick(d); }, 240);
    }
    function _onNodeDblclick(event, d) {
        event.stopPropagation();
        if (_clickTimer) { clearTimeout(_clickTimer); _clickTimer = null; }
        _doDoubleClick(d);
    }

    svg.on('click', () => {
        if (_clickTimer) { clearTimeout(_clickTimer); _clickTimer = null; }
        _clearFocus();   // clicking the background deselects
    });

    const _dragBehavior = d3.drag()
        .on('start', (event, d) => {
            if (!event.active) simulation.alphaTarget(0.3).restart();
            d.fx = d.x; d.fy = d.y;
        })
        .on('drag', (event, d) => { d.fx = event.x; d.fy = event.y; })
        .on('end', (event, d) => {
            if (!event.active) simulation.alphaTarget(0);
            d.fx = null; d.fy = null;
        });

    // ─── Tooltip ─────────────────────────────────────────────────────
    const tooltip = d3.select(container).append('div').attr('class', 'kg-tooltip');
    function _onTipEnter(event, d) {
        const props = d.properties || {};
        const typeColor = colors[d.type] || '';
        let html = `<div style="font-weight:600;${typeColor ? `color:${typeColor}` : ''}">${_escapeHtml(d.name)}</div>`;
        html += `<div style="color:var(--text-muted, #9ca3af);font-size:11px;margin-bottom:4px">${_escapeHtml(d.type)}</div>`;
        if (props.description) html += `<div>${_escapeHtml(props.description)}</div>`;
        if (props.data_type) html += `<div style="color:var(--text-muted, #9ca3af)">Type: ${_escapeHtml(props.data_type)}</div>`;
        if (props.is_primary_key) html += `<div style="color:${KG_PK_COLOR};font-weight:600">Primary key</div>`;
        if (props.is_foreign_key) html += `<div style="color:${KG_FK_COLOR};font-weight:600">Foreign key</div>`;
        if (props.business_meaning) html += `<div style="color:#a78bfa">Business: ${_escapeHtml(props.business_meaning)}</div>`;

        // Governance travels with the node: a column's own PII/sensitivity
        // declaration is the thing a reader most needs at a glance.
        if (props.is_pii === true || props.data_classification) {
            const bits = [];
            if (props.is_pii === true) bits.push('PII');
            if (props.data_classification) bits.push(_escapeHtml(String(props.data_classification)));
            html += `<div style="color:var(--trust-warn,#f59e0b);font-weight:600;font-size:11px">${bits.join(' · ')}</div>`;
        }
        // The decoded value enumeration — what lets an agent know 0 = Female.
        if (Array.isArray(props.allowed_values) && props.allowed_values.length) {
            const shown = props.allowed_values.slice(0, 6)
                .map(v => `${_escapeHtml(String(v.value))} = ${_escapeHtml(String(v.label))}`).join(', ');
            const more = props.allowed_values.length > 6
                ? ` … +${props.allowed_values.length - 6} more` : '';
            html += `<div style="color:var(--text-muted,#9ca3af);font-size:11px;margin-top:3px">Values: ${shown}${more}</div>`;
        }
        // Per-type substance (declared, not hardcoded inline) — without this the
        // product-description nodes render as a bare name and their content, which is
        // the whole reason they exist, stays invisible.
        if ((d.properties || {}).layer === 'authored') {
            const st = (d.properties || {}).status || 'draft';
            html += `<div style="margin:4px 0 6px;font-size:11px;color:var(--text-muted,#9ca3af);">authored layer · ${st}${(d.properties || {}).authored_by ? ` · by ${String((d.properties || {}).authored_by).replace(/[<>&]/g, '')}` : ''}${(d.properties || {}).deploy_receipt ? '' : ' · declared, not yet realised'}</div>`;
        }
        for (const f of (KG_TIP_FIELDS[d.type] || [])) {
            const raw = props[f.key];
            if (raw === undefined || raw === null || raw === '') continue;
            const text = _clampTip(raw, f.max || 220);
            html += `<div style="color:${f.color || 'var(--text-muted,#9ca3af)'};font-size:11px;margin-top:3px"${f.mono ? ' class="kg-tip-mono"' : ''}>`
                 +  `${f.label ? `<span style="opacity:.75">${f.label}:</span> ` : ''}${_escapeHtml(text)}</div>`;
        }
        if (d.type === 'metric') html += `<div style="color:var(--text-muted,#9ca3af);font-size:11px;margin-top:4px;font-style:italic">Right-click → Semantic API / RDF Data Cube</div>`;
        if (!(_activeSpec && _activeSpec.is_platform_ontology)) {
            html += `<div style="color:var(--text-muted,#9ca3af);font-size:11px;margin-top:4px;font-style:italic">${_expansions.has(d.id) ? 'Expanded — double-click to collapse' : 'Double-click to expand its neighbourhood'}</div>`;
        }
        tooltip.html(html).style('display', 'block');
    }

    // Metric nodes export as a W3C RDF Data Cube (QB) + PROV + DCAT graph.
    function _onNodeContextMenu(event, d) {
        if (!d || d.type !== 'metric') return;   // only metrics are Data Cubes
        event.preventDefault();
        event.stopPropagation();
        _openMetricExportMenu(event, d);
    }

    function _openMetricExportMenu(event, d) {
        const old = document.getElementById('kg-metric-export-menu');
        if (old) { old.remove(); document.removeEventListener('click', old._dismiss, true); }
        const items = [
            { label: 'Turtle', sub: 'RDF · .ttl', ser: 'turtle' },
            { label: 'JSON-LD', sub: 'RDF · .jsonld', ser: 'jsonld' },
            { label: 'RDF/XML', sub: '.rdf', ser: 'xml' },
            { label: 'N-Triples', sub: '.nt', ser: 'nt' },
        ];
        const menu = document.createElement('div');
        menu.id = 'kg-metric-export-menu';
        menu.style.cssText = ['position:fixed', 'z-index:10001', 'min-width:190px', 'padding:4px',
            'background:var(--card-bg,#1f2126)', 'border:1px solid var(--border-primary,rgba(148,163,184,0.25))',
            'border-radius:10px', 'box-shadow:0 10px 30px rgba(0,0,0,0.35)', 'font-size:12px'].join(';');
        menu.innerHTML =
            `<div style="padding:6px 10px;color:var(--text-muted,#9ca3af);font-size:11px;border-bottom:1px solid var(--border-secondary,rgba(148,163,184,0.15));margin-bottom:4px;">Metric “${_escapeHtml(d.name)}”</div>`
            + `<button class="kg-metric-api-opt"
                style="display:flex;align-items:center;gap:10px;width:100%;padding:8px 10px;border-radius:6px;background:transparent;border:none;cursor:pointer;color:var(--text-primary,#e5e7eb);text-align:left;">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" style="flex-shrink:0;color:var(--teradata-orange,#F15F22)"><path stroke-linecap="round" stroke-linejoin="round" d="M4 7h16M4 12h16M4 17h10"/><circle cx="18" cy="17" r="2.4"/></svg>
                <span style="display:flex;flex-direction:column;"><span style="font-weight:600;">Semantic API for BI →</span><span style="color:var(--text-muted,#94a3b8);font-size:11px;">definition · compiled SQL · query · governance</span></span></button>`
            + `<div style="padding:6px 10px 4px;color:var(--text-muted,#9ca3af);font-size:11px;border-top:1px solid var(--border-secondary,rgba(148,163,184,0.15));margin-top:4px;">Export as RDF Data Cube</div>`
            + items.map((it, i) => `<button class="kg-metric-export-opt" data-i="${i}"
                style="display:flex;align-items:baseline;justify-content:space-between;gap:12px;width:100%;padding:7px 10px;border-radius:6px;background:transparent;border:none;cursor:pointer;color:var(--text-primary,#e5e7eb);text-align:left;">
                <span style="font-weight:600;">${it.label}</span><span style="color:var(--text-muted,#94a3b8);font-size:11px;">${it.sub}</span></button>`).join('');
        document.body.appendChild(menu);
        let top = event.clientY + 4, left = event.clientX + 4;
        const mh = menu.offsetHeight, mw = menu.offsetWidth;
        if (top + mh > window.innerHeight - 8) top = Math.max(8, window.innerHeight - mh - 8);
        if (left + mw > window.innerWidth - 8) left = Math.max(8, window.innerWidth - mw - 8);
        menu.style.top = `${top}px`; menu.style.left = `${left}px`;
        const close = () => { menu.remove(); document.removeEventListener('click', dismiss, true); };
        const dismiss = (ev) => { if (!menu.contains(ev.target)) close(); };
        menu._dismiss = dismiss;
        const apiBtn = menu.querySelector('.kg-metric-api-opt');
        if (apiBtn) {
            apiBtn.addEventListener('mouseenter', () => { apiBtn.style.background = 'var(--hover-bg-strong,rgba(148,163,184,0.12))'; });
            apiBtn.addEventListener('mouseleave', () => { apiBtn.style.background = 'transparent'; });
            apiBtn.addEventListener('click', (ev) => { ev.stopPropagation(); close(); _openMetricApiPanel(d.name); });
        }
        menu.querySelectorAll('.kg-metric-export-opt').forEach(btn => {
            btn.addEventListener('mouseenter', () => { btn.style.background = 'var(--hover-bg-strong,rgba(148,163,184,0.12))'; });
            btn.addEventListener('mouseleave', () => { btn.style.background = 'transparent'; });
            btn.addEventListener('click', (ev) => {
                ev.stopPropagation();
                close();
                _exportMetricRdf(d.name, items[Number(btn.dataset.i)].ser);
            });
        });
        setTimeout(() => document.addEventListener('click', dismiss, true), 0);
    }

    // The governed metric Semantic API panel (Tier 4.2) — a BI-facing, process-focused view of
    // one metric: what it means, the deployed target, the compiled SQL, its governance/trust
    // state, and copy-pasteable endpoints (the "consume everywhere" surface, in-app).
    function _metricApiBase(metricName) {
        const pid = spec.profile_id || '__default__';
        let path = `/api/v1/profiles/${encodeURIComponent(pid)}/metrics/${encodeURIComponent(metricName)}`;
        const qs = spec.kg_id ? `?kg_id=${encodeURIComponent(spec.kg_id)}` : '';
        return { path, qs, origin: window.location.origin };
    }

    async function _openMetricApiPanel(metricName) {
        const notify = (msg, kind) => (window.showAppBanner ? window.showAppBanner(msg, kind) : console.log(`[${kind}] ${msg}`));
        const old = document.getElementById('kg-metric-api-overlay');
        if (old) old.remove();
        const V = (n, f) => `var(${n},${f})`;
        const overlay = document.createElement('div');
        overlay.id = 'kg-metric-api-overlay';
        overlay.style.cssText = ['position:fixed', 'inset:0', 'z-index:10002', 'display:flex',
            'align-items:center', 'justify-content:center', 'padding:24px',
            'background:rgba(0,0,0,0.45)', 'backdrop-filter:blur(2px)'].join(';');
        const card = document.createElement('div');
        card.style.cssText = ['width:min(620px,96vw)', 'max-height:88vh', 'overflow:auto',
            `background:${V('--card-bg', '#1a1c22')}`, `border:1px solid ${V('--border-primary', 'rgba(148,163,184,0.25)')}`,
            'border-radius:16px', 'box-shadow:0 24px 60px rgba(0,0,0,0.5)', 'padding:0'].join(';');
        card.addEventListener('click', e => e.stopPropagation());
        overlay.appendChild(card);
        const close = () => { overlay.remove(); document.removeEventListener('keydown', onKey, true); };
        const onKey = (e) => { if (e.key === 'Escape') close(); };
        overlay.addEventListener('click', close);
        document.addEventListener('keydown', onKey, true);
        card.innerHTML = `<div style="padding:20px 22px;color:${V('--text-muted', '#94a3b8')};">Loading metric API…</div>`;
        document.body.appendChild(overlay);

        const { path, qs, origin } = _metricApiBase(metricName);
        let data;
        try {
            const token = localStorage.getItem('tda_auth_token');
            const res = await fetch(`${path}${qs}`, { headers: { 'Authorization': `Bearer ${token}` } });
            data = await res.json();
            if (!res.ok || data.status === 'error') throw new Error(data.message || `HTTP ${res.status}`);
        } catch (e) {
            card.innerHTML = `<div style="padding:22px;color:${V('--text-primary', '#e5e7eb')}">`
                + `<div style="font-weight:700;margin-bottom:6px;">Metric API</div>`
                + `<div style="color:#f87171;font-size:13px;">${_escapeHtml(e.message || 'failed')}</div></div>`;
            return;
        }

        const chip = (label, value, color) => value ? `<span style="display:inline-flex;align-items:center;gap:5px;padding:3px 9px;border-radius:999px;font-size:11px;font-weight:600;background:${color}1a;color:${color};border:1px solid ${color}40;">${label}${value === true ? '' : ': ' + _escapeHtml(String(value))}</span>` : '';
        const trustColor = { trusted: '#22c55e', changed: '#f59e0b', unsigned: '#94a3b8' }[(data.ontology || {}).trust_state] || '#94a3b8';
        const trustLabel = { trusted: 'Trusted', changed: 'Signed · changed', unsigned: 'Unsigned' }[(data.ontology || {}).trust_state] || null;
        const def = data.definition || {}, dep = data.deployment || {}, comp = data.compiled || {};
        const dimChips = (def.dimensions || []).map(d => `<span class="mapi-tag">${_escapeHtml(d.name)}</span>`).join('') || '<span style="color:var(--text-muted,#94a3b8);font-size:12px;">none</span>';
        const measChips = (def.measures || []).map(m => `<span class="mapi-tag mapi-tag--m">${_escapeHtml(m.name)}</span>`).join('') || '<span style="color:var(--text-muted,#94a3b8);font-size:12px;">none</span>';
        const scope = (data.access_scope || []).map(s => `<code class="mapi-code">${_escapeHtml(s)}</code>`).join(' ') || '<span style="color:var(--text-muted,#94a3b8);font-size:12px;">logical-only</span>';
        const deployedPtr = dep.deployed_view ? `view <code class="mapi-code">${_escapeHtml(dep.deployed_view)}</code>`
            : dep.deployed_column ? `column <code class="mapi-code">${_escapeHtml(dep.deployed_column)}</code>`
            : dep.source_table ? `base table <code class="mapi-code">${_escapeHtml(dep.source_table)}</code>` : 'not deployed';
        const curl = (verb, sub, body) => `curl -s -X ${verb} '${origin}${path}${sub}${qs}'`
            + ` -H 'Authorization: Bearer <TOKEN>'`
            + (body ? ` -H 'Content-Type: application/json' -d '${body}'` : '');
        const endpoints = [
            { t: 'Definition + compiled SQL', verb: 'GET', sub: '', body: '' },
            { t: 'Compile a slice', verb: 'POST', sub: '/compile', body: '{"dimensions":[],"measures":[]}' },
            { t: 'Query (returns rows)', verb: 'POST', sub: '/query', body: '{"connection_id":"<id>"}' },
        ];

        card.innerHTML = `
          <style>
            .mapi-tag{display:inline-block;padding:2px 8px;margin:0 4px 4px 0;border-radius:6px;font-size:11px;font-weight:600;background:${V('--hover-bg-strong', 'rgba(148,163,184,0.14)')};color:${V('--text-primary', '#e5e7eb')};}
            .mapi-tag--m{background:rgba(241,95,34,0.12);color:${V('--teradata-orange', '#F15F22')};}
            .mapi-code{font-size:11px;background:${V('--hover-bg-strong', 'rgba(148,163,184,0.14)')};padding:1px 5px;border-radius:4px;color:${V('--text-primary', '#e5e7eb')};}
            .mapi-sec{padding:14px 22px;border-top:1px solid ${V('--border-secondary', 'rgba(148,163,184,0.14)')};}
            .mapi-h{font-size:11px;text-transform:uppercase;letter-spacing:0.05em;color:${V('--text-muted', '#94a3b8')};margin-bottom:8px;}
            .mapi-btn{display:inline-flex;align-items:center;gap:6px;padding:5px 10px;border-radius:7px;font-size:12px;font-weight:600;cursor:pointer;background:transparent;border:1px solid ${V('--border-primary', 'rgba(148,163,184,0.3)')};color:${V('--text-primary', '#e5e7eb')};}
            .mapi-btn:hover{background:${V('--hover-bg-strong', 'rgba(148,163,184,0.12)')};}
            .mapi-pre{margin:0;padding:12px 14px;border-radius:9px;overflow:auto;font-size:12px;line-height:1.5;background:#0b1020;color:#d1d5db;white-space:pre;}
            .mapi-ep{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px 0;border-bottom:1px solid ${V('--border-secondary', 'rgba(148,163,184,0.1)')};}
            .mapi-ep:last-child{border-bottom:none;}
          </style>
          <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:20px 22px 12px;">
            <div>
              <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.06em;color:${V('--teradata-orange', '#F15F22')};font-weight:700;">Semantic API · define once, consume everywhere</div>
              <div style="font-size:19px;font-weight:800;color:${V('--text-primary', '#f1f5f9')};margin-top:3px;">${_escapeHtml(metricName)}</div>
              ${def.description ? `<div style="font-size:13px;color:${V('--text-muted', '#94a3b8')};margin-top:3px;">${_escapeHtml(def.description)}</div>` : ''}
            </div>
            <button id="mapi-close" style="background:transparent;border:none;cursor:pointer;color:${V('--text-muted', '#94a3b8')};font-size:22px;line-height:1;">×</button>
          </div>
          <div style="padding:0 22px 12px;display:flex;flex-wrap:wrap;gap:6px;">
            ${chip(dep.realization === 'none' ? 'Logical only' : 'Realization', dep.realization === 'none' ? true : dep.realization, '#3b82f6')}
            ${chip('Queryable', dep.is_deployed ? true : '', '#22c55e')}
            ${trustLabel ? chip(trustLabel, true, trustColor) : ''}
            ${(data.ontology || {}).lifecycle_status ? chip('Ontology', (data.ontology || {}).lifecycle_status, '#a855f7') : ''}
          </div>
          <div class="mapi-sec"><div class="mapi-h">Definition</div>
            <div style="font-size:12px;color:${V('--text-muted', '#94a3b8')};margin-bottom:4px;">Dimensions</div><div>${dimChips}</div>
            <div style="font-size:12px;color:${V('--text-muted', '#94a3b8')};margin:8px 0 4px;">Measures</div><div>${measChips}</div>
          </div>
          <div class="mapi-sec"><div class="mapi-h">Deployed target &amp; access scope</div>
            <div style="font-size:13px;color:${V('--text-primary', '#e5e7eb')};">${deployedPtr}</div>
            <div style="font-size:12px;color:${V('--text-muted', '#94a3b8')};margin-top:6px;">Reads from ${scope}</div>
          </div>
          ${comp.sql ? `<div class="mapi-sec"><div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;"><div class="mapi-h" style="margin:0;">Compiled SQL</div><button class="mapi-btn" id="mapi-copy-sql">Copy SQL</button></div><pre class="mapi-pre">${_escapeHtml(comp.sql)}</pre></div>` : ''}
          <div class="mapi-sec"><div class="mapi-h">Consume from BI (endpoints)</div>
            ${endpoints.map((e, i) => `<div class="mapi-ep"><div><div style="font-size:13px;font-weight:600;color:${V('--text-primary', '#e5e7eb')};">${e.t}</div><div style="font-size:11px;color:${V('--text-muted', '#94a3b8')};"><code class="mapi-code">${e.verb} ${path}${e.sub}</code></div></div><button class="mapi-btn mapi-copy-curl" data-i="${i}">Copy cURL</button></div>`).join('')}
          </div>
          ${data.standards_export ? `<div class="mapi-sec" style="display:flex;align-items:center;justify-content:space-between;"><span style="font-size:12px;color:${V('--text-muted', '#94a3b8')};">Also an open standard — W3C RDF Data Cube (QB)</span><a href="${origin}${data.standards_export}&format=qb" target="_blank" rel="noopener" class="mapi-btn" style="text-decoration:none;">Open QB export</a></div>` : ''}
        `;
        card.querySelector('#mapi-close').addEventListener('click', close);
        const copy = (txt, okMsg) => { (navigator.clipboard ? navigator.clipboard.writeText(txt) : Promise.reject()).then(() => notify(okMsg, 'success')).catch(() => notify('Copy failed', 'error')); };
        const sqlBtn = card.querySelector('#mapi-copy-sql');
        if (sqlBtn) sqlBtn.addEventListener('click', () => copy(comp.sql, 'SQL copied'));
        card.querySelectorAll('.mapi-copy-curl').forEach(btn => {
            const e = endpoints[Number(btn.dataset.i)];
            btn.addEventListener('click', () => copy(curl(e.verb, e.sub, e.body), 'cURL copied'));
        });
    }

    async function _exportMetricRdf(metricName, serialization) {
        const notify = (msg, kind) => (window.showAppBanner ? window.showAppBanner(msg, kind) : console.log(`[${kind}] ${msg}`));
        try {
            const token = localStorage.getItem('tda_auth_token');
            const params = new URLSearchParams();
            if (spec.kg_id) params.set('kg_id', spec.kg_id);
            else if (spec.profile_id) params.set('profile_id', spec.profile_id);
            params.set('format', 'all');
            params.set('serialization', serialization);
            const res = await fetch(`/api/v1/knowledge-graph/metrics/${encodeURIComponent(metricName)}/export?${params}`,
                { headers: { 'Authorization': `Bearer ${token}` } });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.message || `HTTP ${res.status}`);
            }
            const blob = await res.blob();
            const disp = res.headers.get('Content-Disposition') || '';
            const m = disp.match(/filename="?([^"]+)"?/);
            const ext = { turtle: 'ttl', jsonld: 'jsonld', xml: 'rdf', nt: 'nt' }[serialization] || 'ttl';
            const fname = m ? m[1] : `metric-${metricName}.${ext}`;
            // a server-built export: the server recorded it; Save to Files asks the server again (?to=files)
            window.FilesKeep.deliver(blob, fname, { what: 'metric', serverBuilt: true,
                saveUrl: `/api/v1/knowledge-graph/metrics/${encodeURIComponent(metricName)}/export?${params}` });
            notify(`Metric exported as RDF Data Cube (${serialization})`, 'success');
        } catch (e) {
            console.error('[KG Metric Export] failed:', e);
            notify(`Metric export failed: ${e.message}`, 'error');
        }
    }
    function _onTipMove(event) {
        const rect = container.getBoundingClientRect();
        tooltip.style('left', (event.clientX - rect.left + 12) + 'px')
               .style('top', (event.clientY - rect.top - 10) + 'px');
    }
    function _onTipLeave() { tooltip.style('display', 'none'); }

    // Wire ALL node handlers onto a selection. Called for the initial nodes and for every
    // incrementally-merged batch, so merged nodes are first-class (hover/click/drag/tooltip).
    function _wireNodeHandlers(sel) {
        sel.on('mouseenter', _onNodeEnter)
           .on('mouseleave', _onNodeLeave)
           .on('click', _onNodeClick)
           .on('dblclick', _onNodeDblclick)
           .on('contextmenu', _onNodeContextMenu)
           .on('mouseenter.tooltip', _onTipEnter)
           .on('mousemove.tooltip', _onTipMove)
           .on('mouseleave.tooltip', _onTipLeave)
           .call(_dragBehavior);
    }
    _wireNodeHandlers(nodeGroups);

    // ─── Incremental in-place merge (complete-neighbourhood reveal) ──────
    // Append a node's out-of-budget neighbours into the LIVE graph without re-rendering:
    // existing nodes are pinned (frozen) so only the new ones grow out from the focus —
    // the viewport never jumps. This is the consistency guarantee for node focus: every
    // focus gesture can show a node's COMPLETE relationship set, never a budget-truncated
    // subset. Append-only (new elements are at the array tail → d3 enter() = exactly them).
    // Node ids are minted from ONE monotonic counter, never from `nodes.length`: once a
    // collapse can remove nodes, a length-based id would be re-minted for a later merge and
    // collide with a survivor (two elements keyed alike → a mis-bound join).
    let _nextNodeId = nodes.length;
    function _mergeNeighborhood(newNodeData, newLinkData, srcNode) {
        const preCount = nodes.length;
        const byEid = new Map(nodes.map(n => [n.entity_id, n]));
        const sx = (srcNode && srcNode.x != null) ? srcNode.x : width / 2;
        const sy = (srcNode && srcNode.y != null) ? srcNode.y : height / 2;
        const addedNodeIds = new Set(), addedLinkUids = new Set();
        (newNodeData || []).forEach(nd => {
            if (byEid.has(nd.entity_id)) return;
            const a = Math.random() * Math.PI * 2, r = 70 + Math.random() * 45;
            const n = { id: _nextNodeId++, entity_id: nd.entity_id, name: nd.name, type: nd.type,
                        properties: nd.properties, importance: nd.importance,
                        x: sx + Math.cos(a) * r, y: sy + Math.sin(a) * r };
            nodes.push(n); byEid.set(nd.entity_id, n); addedNodeIds.add(n.id);
        });
        // New links: resolve endpoints to node objects by entity_id; dedupe against existing.
        const seen = new Set(links.map(l => {
            const s = (typeof l.source === 'object' ? l.source.entity_id : nodes[l.source]?.entity_id);
            const t = (typeof l.target === 'object' ? l.target.entity_id : nodes[l.target]?.entity_id);
            return s + '|' + t + '|' + l.type;
        }));
        let addedLinks = 0;
        (newLinkData || []).forEach(ld => {
            const s = byEid.get(ld.source_entity_id), t = byEid.get(ld.target_entity_id);
            if (!s || !t || s === t) return;
            const key = s.entity_id + '|' + t.entity_id + '|' + ld.type;
            if (seen.has(key)) return; seen.add(key);
            const link = { source: s, target: t, type: ld.type, cardinality: ld.cardinality,
                           derived: ld.derived, count: ld.count };
            _ensureGradient(link);
            links.push(link); addedLinks++; addedLinkUids.add(link._uid);
        });
        if (nodes.length === preCount && !addedLinks) return false;   // nothing new
        // New link <g> inserted BEFORE the nodes (z-order: edges behind cards). Joins are
        // KEYED (uid / id): an index join is only right while the arrays are append-only,
        // and a collapse makes them not.
        const lEnter = g.selectAll('.kg-link').data(links, l => l._uid).enter().insert('g', '.kg-node').attr('class', 'kg-link');
        _decorateLinkEnter(lEnter);
        linkGroup = g.selectAll('.kg-link');
        linkLines = linkGroup.select('line');
        // New node <g> appended (above edges); decorate + wire + fade in.
        const nEnter = g.selectAll('.kg-node').data(nodes, n => n.id).enter().append('g')
            .attr('class', 'kg-node').style('cursor', 'pointer')
            .attr('transform', d => `translate(${d.x},${d.y})`).style('opacity', 0);
        _decorateNodeEnter(nEnter);
        nEnter.transition().duration(220).style('opacity', 1);
        nodeGroups = g.selectAll('.kg-node');
        _markExpanded();   // a fresh node group never wears a stale mark; centres keep theirs
        // A merged-in node can collide with an existing label (Customers.City beside
        // Sales.City) — recompute over the WHOLE set so both widen, not just the new one.
        _relabelNodes();
        _wireNodeHandlers(nEnter);
        // Re-bind the simulation; pin the existing nodes so ONLY the new ones settle in.
        simulation.nodes(nodes);
        simulation.force('link').links(links);
        for (let i = 0; i < preCount; i++) { nodes[i].fx = nodes[i].x; nodes[i].fy = nodes[i].y; }
        simulation.alpha(0.3).restart();
        setTimeout(() => { for (let i = 0; i < preCount; i++) { if (nodes[i]) { nodes[i].fx = null; nodes[i].fy = null; } } }, 1500);
        // Refresh the shared state so the execution animation reaches the merged elements.
        if (_graphState) { _graphState.nodeGroups = nodeGroups; _graphState.linkGroup = linkGroup; _graphState.linkLines = linkLines; }
        return { nodeIds: addedNodeIds, linkUids: addedLinkUids };
    }

    // ─── Expansion ledger + collapse (the double-click revert) ───────────
    // centre node id → { centre, nodeIds: what this expansion INTRODUCED, linkUids: same,
    // claimed: every entity in its full neighbourhood, present or not }. Per render: a
    // re-query builds a fresh graph and the ledger goes with the old one.
    const _expansions = new Map();
    // Two survival rules make a collapse a REVERT rather than a deletion: (1) a node this
    // expansion introduced survives while another ACTIVE expansion still claims it as a
    // neighbour — removing it would silently make that neighbourhood incomplete again, the
    // one thing expand guarantees; (2) an expansion whose centre this one introduced is
    // collapsed FIRST (tree semantics: closing a parent closes what hangs off it), so no
    // subtree is left floating with its parent gone.
    function _collapseExpansion(d) {
        const exp = _expansions.get(d.id);
        if (!exp) return false;
        _expansions.delete(d.id);
        for (const other of [..._expansions.values()]) if (exp.nodeIds.has(other.centre.id)) _collapseExpansion(other.centre);
        const { goneNodes, goneLinks } = _collapseSurvivors(exp, _expansions, nodes, links);
        _removeElements(goneNodes, goneLinks);
        _markExpanded();
        return true;
    }
    // The mirror of _mergeNeighborhood: drop the given nodes/links from the arrays, the DOM
    // (keyed exit), the gradient defs and the derived cache; rebind the simulation with the
    // survivors pinned briefly so the layout does not jump.
    function _removeElements(goneNodes, goneLinks) {
        if (!goneNodes.size && !goneLinks.size) return;
        for (let i = links.length - 1; i >= 0; i--) if (goneLinks.has(links[i])) links.splice(i, 1);
        for (let i = nodes.length - 1; i >= 0; i--) if (goneNodes.has(nodes[i])) nodes.splice(i, 1);
        goneLinks.forEach(l => defs.select(`#kg-grad-${l._uid}`).remove());
        if (_derivedCache) _derivedCache = _derivedCache.filter(e => !goneNodes.has(e.source) && !goneNodes.has(e.target));
        if (focusedNode && goneNodes.has(focusedNode)) focusedNode = null;
        g.selectAll('.kg-link').data(links, l => l._uid).exit().remove();
        // Removed synchronously, never through a fade-then-remove transition: the caller's
        // very next repaint re-selects '.kg-node' and interrupt()s every transition on it,
        // which would cancel the removal and leave the exiting elements behind as zombies.
        g.selectAll('.kg-node').data(nodes, n => n.id).exit().interrupt().remove();
        linkGroup = g.selectAll('.kg-link'); linkLines = linkGroup.select('line');
        nodeGroups = g.selectAll('.kg-node');
        _relabelNodes();
        simulation.nodes(nodes);
        simulation.force('link').links(links);
        nodes.forEach(n => { n.fx = n.x; n.fy = n.y; });
        simulation.alpha(0.2).restart();
        setTimeout(() => { nodes.forEach(n => { n.fx = null; n.fy = null; }); }, 1200);
        if (_graphState) { _graphState.nodeGroups = nodeGroups; _graphState.linkGroup = linkGroup; _graphState.linkLines = linkLines; }
    }
    // The collapse mark: a small minus badge at the card's bottom-right on every node with
    // an active expansion — the one cue that a double-click will now fold rather than grow.
    function _markExpanded() {
        nodeGroups.each(function (d) {
            const sel = d3.select(this), on = _expansions.has(d.id), mark = sel.select('g.kg-expand-mark');
            if (on && mark.empty()) {
                const m = sel.append('g').attr('class', 'kg-expand-mark')
                    .attr('transform', `translate(${nodeW / 2 - 4}, ${nodeH / 2 - 4})`).style('pointer-events', 'none');
                m.append('circle').attr('r', 5.5).attr('fill', _themeColor('--card-bg', '#1f2937'))
                    .attr('stroke', _themeColor('--text-muted', '#9ca3af')).attr('stroke-width', 1.2);
                m.append('rect').attr('x', -3).attr('y', -0.75).attr('width', 6).attr('height', 1.5).attr('rx', 0.75)
                    .attr('fill', _themeColor('--text-primary', '#e5e7eb'));
            } else if (!on && !mark.empty()) mark.remove();
        });
    }

    // Fetch a node's COMPLETE 1-hop neighbourhood (uncapped) and merge any missing
    // neighbours into the live graph. onDone(merged) fires after (so the caller can
    // re-apply its highlight to include the newly-pulled-in nodes).
    let _revealInFlight = new Set();
    async function _revealComplete(entityId, onDone, ledger) {
        if ((!_activeSpec || (!_activeSpec.kg_id && !_activeSpec.profile_id)) || _revealInFlight.has(entityId)) {
            if (onDone) onDone(false); return;
        }
        _revealInFlight.add(entityId);
        const srcNode = nodes.find(n => n.entity_id === entityId);
        const params = new URLSearchParams();
        if (_activeSpec.kg_id) params.set('kg_id', _activeSpec.kg_id);
        if (_activeSpec.profile_id) params.set('profile_id', _activeSpec.profile_id);
        params.set('entity_id', String(entityId));
        const token = localStorage.getItem('tda_auth_token');
        try {
            const r = await fetch(`/api/v1/knowledge-graph/node-neighborhood?${params}`,
                                  { headers: { Authorization: `Bearer ${token}` } });
            const data = await r.json();
            if (data && data.status === 'success') {
                const have = new Set(nodes.map(n => n.entity_id));
                const missing = (data.nodes || []).filter(n => !have.has(n.entity_id));
                const merged = _mergeNeighborhood(missing, data.links || [], srcNode);
                // Record the expansion (the double-click path) even when it introduced
                // nothing: the node is then honestly "expanded" — its neighbourhood is
                // complete — and wears the mark; a collapse of it simply removes nothing.
                if (ledger && srcNode) {
                    _expansions.set(srcNode.id, { centre: srcNode,
                        nodeIds: merged ? merged.nodeIds : new Set(), linkUids: merged ? merged.linkUids : new Set(),
                        claimed: new Set((data.nodes || []).map(n => n.entity_id)) });
                    _markExpanded();
                }
                if (onDone) onDone(!!merged);
            } else if (onDone) { onDone(false); }
        } catch (_) { if (onDone) onDone(false); }
        finally { _revealInFlight.delete(entityId); }
    }
    _activeReveal = _revealComplete;

    // ─── Tick Update ─────────────────────────────────────────────────
    simulation.on('tick', () => {
        linkGroup.select('line')
            .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
            .attr('x2', d => d.target.x).attr('y2', d => d.target.y);

        linkGroup.select('text')
            .attr('x', d => (d.source.x + d.target.x) / 2)
            .attr('y', d => (d.source.y + d.target.y) / 2);

        links.forEach((d) => {
            const grad = defs.select(`#kg-grad-${d._uid}`);
            if (grad.size()) {
                grad.attr('x1', d.source.x).attr('y1', d.source.y)
                    .attr('x2', d.target.x).attr('y2', d.target.y);
            }
        });

        nodeGroups.attr('transform', d => `translate(${d.x},${d.y})`);
    });

    // ─── Legend (in-graph overlay) ────────────────────────────────────
    _renderLegend(container, colors, nodes);

    // ─── Toolbar Event Wiring (direct callbacks — no DOM events) ────
    // Search
    if (toolbar && toolbar._searchInput) {
        // Type-to-highlight within the current view…
        toolbar._searchInput.oninput = () => {
            const q = toolbar._searchInput.value.toLowerCase();
            if (!q) { nodeGroups.style('opacity', 1); return; }
            nodeGroups.style('opacity', d => d.name.toLowerCase().includes(q) ? 1 : 0.12);
        };
        // …Enter = find across the WHOLE KG: frame in-view matches, else re-query a
        // focus neighbourhood for the searched entity (it may be outside the sample).
        toolbar._searchInput.onkeydown = (e) => {
            if (e.key !== 'Enter') return;
            const q = toolbar._searchInput.value.trim();
            if (!q) return;
            const inView = nodes.filter(n => (n.name || '').toLowerCase().includes(q.toLowerCase()));
            if (inView.length) {
                _zoomToNodes(inView);
            } else if (_activeRequery && _activeSpec && (_activeSpec.kg_id || _activeSpec.profile_id)) {
                const types = _activeSpec.applied_types || null;
                const budget = Math.max(_activeSpec.requested_max || 0, 200);
                _activeRequery(types, budget, [q]);
            }
        };
    }

    // Filter — direct callback from toolbar pills
    if (toolbar) {
        toolbar._onFilter = (hiddenTypes) => {
            nodeGroups.style('display', d => hiddenTypes.has(d.type) ? 'none' : null);
            g.selectAll('.kg-link').style('display', l => {
                const sType = (typeof l.source === 'object' ? l.source : nodes[l.source])?.type;
                const tType = (typeof l.target === 'object' ? l.target : nodes[l.target])?.type;
                return (hiddenTypes.has(sType) || hiddenTypes.has(tType)) ? 'none' : null;
            });
        };

        // Zoom fit
        toolbar._onZoomFit = () => {
            // An explicit fit is the user's own act: it must not be vetoed by the
            // "user panned, skip autozoom" latch (which would keep the old view).
            _fitVisibleNodes(500);
        };

        // Export PNG
        toolbar._onExportPNG = () => {
            _exportPNG(svg.node(), width, height);
        };
    }

    // Expose D3 state for live animation bridge
    _graphState = { nodeGroups, linkGroup, linkLines, links, nodes, simulation, svg, g, zoom, defs, colors, width, height, setFocusRing: _setFocusRing, selectNode: _doSingleClick };
    // Signal that a render completed + _graphState is live — the render is deferred
    // (ResizeObserver debounce), so external views (the Ontology) can't assume the
    // graph exists right after calling renderKGFull; they listen for this to re-apply
    // focus/selection after an expand or lens re-render.
    try { UderiaBus.emit('uderia:kg-rendered', { container }); } catch (_) {}

    // A graph opened ON a concept (the OBDA-MODEL panel's Open, a Workspace extension row,
    // "Open in Graph"): until now `focus_names` only seeded the server's SAMPLE — the node
    // was in the graph, unmarked, and the panel fit the whole graph — while the comment at
    // the opener claimed it centred. Now the opened node is SELECTED exactly as a click
    // selects it (ring, dim the rest, its lifted dependencies, the kg:node-select seam) and,
    // on the open itself, zoomed to. A re-query (slider, lens) re-applies the selection
    // without moving the view: `applied_focus_names` carries, `focus_names` does not.
    // (This runs in the deferred render; the spec being drawn is the module's _activeSpec.)
    const openedSpec = _activeSpec || {};
    const openedNames = Array.isArray(openedSpec.focus_names) && openedSpec.focus_names.length ? openedSpec.focus_names : null;
    const keptNames = Array.isArray(openedSpec.applied_focus_names) && openedSpec.applied_focus_names.length ? openedSpec.applied_focus_names : null;
    if (openedNames || keptNames) {
        const names = openedNames || keptNames, zoomTo = !!openedNames;
        openedSpec.focus_names = null;   // the open happens once; the spec may be re-rendered (resize)
        setTimeout(() => _focusOpenedNames(names, zoomTo), 220);
    }

    // Drain any animation queued for after a focus re-render (trace-driven execution
    // animation): the touched entities weren't in the previous sample, so we re-queried
    // a focus neighborhood; now that it's rendered, activate them in context.
    if (_pendingAnimateNames.size) {
        const names = [..._pendingAnimateNames];
        _pendingAnimateNames.clear();
        // Let the simulation place the new nodes for a beat, then frame + replay them
        // in order so the traversal reads as a path (ordered activation, not a flash).
        setTimeout(() => {
            if (!_graphState) return;
            const matched = _matchNodesToNames(names);
            if (!matched.length) return;
            _zoomToNodes(matched);
            const stagger = Math.min(110, Math.max(40, Math.floor(1800 / matched.length)));
            matched.forEach((node, i) => {
                setTimeout(() => {
                    if (!_graphState) return;
                    _pulseNode(node);
                    _glowNeighbors(node);
                }, i * stagger);
            });
        }, 220);
    }

    // Restart simulation with a gentle alpha for final fine-tuning.
    // The heavy settling was done in the pre-warm phase, so this only
    // handles minor adjustments and drag interactions.
    simulation.alpha(_mostlyCarried ? 0.02 : 0.05).restart();

    return { svg: svg.node(), simulation };
}


// ─── Legend (in-graph overlay) ──────────────────────────────────────────

// Human-facing labels for node types (terminology uplift — never show raw snake_case).
// Platform-ontology types get friendly names; anything unmapped falls back to Title Case.
const _LEGEND_LABELS = {
    agent: 'Agent', agent_ideate: 'Ideate agent', agent_optimize: 'Optimize agent',
    agent_focus: 'Focus agent', agent_coordinate: 'Coordinate agent',
    segment: 'Your segment', class_group: 'IFOC class', onto_group: 'Group',
    memory: 'Agent Memory', context_window: 'Context Window',
    collection: 'Knowledge Repository', collection_planner: 'Planner Repository',
    knowledge_graph: 'Knowledge Graph', domain: 'Domain',
    component: 'Component', platform_connector: 'Connector', skill: 'Skill', extension: 'Extension',
    session: 'Session', agent_pack: 'Agent Pack',
    llm: 'LLM', mcp_server: 'MCP Server', vector_store: 'Vector Store', kg_backend: 'Graph Store',
};
function _prettyType(t) {
    if (_LEGEND_LABELS[t]) return _LEGEND_LABELS[t];
    return String(t || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}
const _LEGEND_CHEV = '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>';

function _renderLegend(container, colors, nodes) {
    const presentTypes = [...new Set(nodes.map(n => n.type))];
    if (presentTypes.length <= 1) return;

    const collapsed = (() => { try { return localStorage.getItem('uderia.kg.legend.collapsed') === '1'; } catch (_) { return false; } })();

    const legend = document.createElement('div');
    legend.className = 'kg-legend' + (collapsed ? ' collapsed' : '');
    // Restore a dragged position (falls back to the CSS default bottom-right).
    try {
        const pos = JSON.parse(localStorage.getItem('uderia.kg.legend.pos') || 'null');
        if (pos && Number.isFinite(pos.left) && Number.isFinite(pos.top)) {
            legend.style.left = pos.left + 'px'; legend.style.top = pos.top + 'px';
            legend.style.right = 'auto'; legend.style.bottom = 'auto';
        }
    } catch (_) {}

    // Header — the drag handle + collapse toggle.
    const header = document.createElement('div');
    header.className = 'kg-legend-hd';
    header.innerHTML = `<span class="kg-legend-title">Legend</span><button class="kg-legend-toggle" title="${collapsed ? 'Expand' : 'Collapse'}" aria-label="Toggle legend">${_LEGEND_CHEV}</button>`;
    legend.appendChild(header);

    const body = document.createElement('div');
    body.className = 'kg-legend-body';
    if (collapsed) body.style.display = 'none';

    presentTypes.forEach(type => {
        const color = colors[type] || _themeColor('--text-muted', '#6b7280');
        const row = document.createElement('div');
        row.style.cssText = 'display:flex;align-items:center;gap:6px;margin:2px 0;';
        row.innerHTML = `
            <span style="width:8px;height:8px;border-radius:50%;background:${color};display:inline-block;flex:0 0 auto"></span>
            <span style="color:var(--text-secondary, #d1d5db)">${_escapeHtml(_prettyType(type))}</span>
        `;
        body.appendChild(row);
    });

    // Key-relationship legend (FK edge + PK/FK field markers) — when attributes/columns are in view.
    if (presentTypes.includes('attribute') || presentTypes.includes('column')) {
        const sep = document.createElement('div');
        sep.style.cssText = 'border-top:1px solid var(--border-secondary,rgba(255,255,255,0.08));margin:5px 0 3px;';
        body.appendChild(sep);
        const mk = (swatch, label) => {
            const r = document.createElement('div');
            r.style.cssText = 'display:flex;align-items:center;gap:6px;margin:2px 0;';
            r.innerHTML = `${swatch}<span style="color:var(--text-secondary,#d1d5db)">${label}</span>`;
            body.appendChild(r);
        };
        mk(`<span style="width:14px;height:0;border-top:2px solid ${KG_FK_COLOR};display:inline-block"></span>`, 'Foreign-key edge');
        mk(`<span style="font-size:7px;font-weight:700;color:#111;background:${KG_PK_COLOR};border-radius:2px;padding:0 3px">PK</span>`, 'Primary-key column');
        mk(`<span style="font-size:7px;font-weight:700;color:#111;background:${KG_FK_COLOR};border-radius:2px;padding:0 3px">FK</span>`, 'Foreign-key column');
    }
    legend.appendChild(body);

    // Collapse toggle (persisted).
    header.querySelector('.kg-legend-toggle').addEventListener('click', (e) => {
        e.stopPropagation();
        const willCollapse = body.style.display !== 'none';
        body.style.display = willCollapse ? 'none' : '';
        legend.classList.toggle('collapsed', willCollapse);
        e.currentTarget.title = willCollapse ? 'Expand' : 'Collapse';
        try { localStorage.setItem('uderia.kg.legend.collapsed', willCollapse ? '1' : '0'); } catch (_) {}
    });

    // Drag via the header (persisted). Bounds to the container.
    header.addEventListener('mousedown', (e) => {
        if (e.target.closest('.kg-legend-toggle')) return;
        const r = legend.getBoundingClientRect();
        const parent = (legend.offsetParent || container).getBoundingClientRect();
        const dx = e.clientX - r.left, dy = e.clientY - r.top;
        legend.style.right = 'auto'; legend.style.bottom = 'auto';
        e.preventDefault();
        const move = (ev) => {
            const left = Math.max(0, ev.clientX - dx - parent.left);
            const top = Math.max(0, ev.clientY - dy - parent.top);
            legend.style.left = left + 'px'; legend.style.top = top + 'px';
        };
        const up = () => {
            document.removeEventListener('mousemove', move);
            document.removeEventListener('mouseup', up);
            try { localStorage.setItem('uderia.kg.legend.pos', JSON.stringify({ left: parseFloat(legend.style.left) || 0, top: parseFloat(legend.style.top) || 0 })); } catch (_) {}
        };
        document.addEventListener('mousemove', move);
        document.addEventListener('mouseup', up);
    });

    container.appendChild(legend);
}


// ─── Export PNG ──────────────────────────────────────────────────────────

function _exportPNG(svgNode, width, height) {
    const serializer = new XMLSerializer();
    const svgStr = serializer.serializeToString(svgNode);
    const svgBlob = new Blob([svgStr], { type: 'image/svg+xml;charset=utf-8' });
    const url = URL.createObjectURL(svgBlob);

    const img = new Image();
    img.onload = () => {
        const canvas = document.createElement('canvas');
        canvas.width = width * 2;
        canvas.height = height * 2;
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = _themeColor('--bg-primary', '#0f172a');
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        URL.revokeObjectURL(url);

        canvas.toBlob(blob => {
            window.FilesKeep.deliver(blob, 'knowledge-graph.png', { what: 'knowledge graph picture', componentId: 'knowledge_graph' });
        }, 'image/png');
    };
    img.src = url;
}


// ─── Helpers ────────────────────────────────────────────────────────────

// Monochrome SVG icon paths (currentColor) for toolbar buttons — no emoji/glyphs.
const _KG_ICONS = {
    fit:     '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg>',
    export:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>',
    history: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v5h5"/><path d="M3.05 13A9 9 0 1 0 6 5.3L3 8"/><path d="M12 7v5l4 2"/></svg>',
    schema:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2" width="6" height="5" rx="1"/><rect x="2" y="17" width="6" height="5" rx="1"/><rect x="16" y="17" width="6" height="5" rx="1"/><path d="M12 7v5M12 12H5v5M12 12h7v5"/></svg>',
    deps:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="5" cy="6" r="2.5"/><circle cx="19" cy="6" r="2.5"/><circle cx="12" cy="18" r="2.5"/><path d="M7 7l3.5 9M17 7l-3.5 9" stroke-dasharray="3 3"/></svg>',
    auto:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></svg>',
};

function _makeToolbarButton(label, title) {
    const btn = document.createElement('button');
    btn.className = 'kg-toolbar-btn';
    // SVG markup → innerHTML (monochrome icon); plain text → textContent.
    if (typeof label === 'string' && label.trim().startsWith('<svg')) btn.innerHTML = label;
    else btn.textContent = label;
    btn.title = title;
    return btn;
}

function _escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

/** Read a CSS variable from the current theme (for D3 inline SVG attributes). */
function _themeColor(varName, fallback) {
    return getComputedStyle(document.documentElement).getPropertyValue(varName).trim() || fallback;
}

/** Native-tooltip text for an edge. For a DERIVED (lifted) edge it lists the real
 *  underlying endpoints — e.g. which column(s) connect two tables — so the answer is
 *  available on hover even when those columns are outside the render budget. */
function _edgeTitle(d) {
    if (d && d.derived && Array.isArray(d.via) && d.via.length) {
        const shown = d.via.slice(0, 15).map(p => `  ${p.from} → ${p.to}`).join('\n');
        const total = d.count || d.via.length;
        const more = total > d.via.length ? `\n  … (${total} total)` : '';
        return `${d.type} — connects via:\n${shown}${more}`;
    }
    return d ? (d.type || '') : '';
}


// ═══════════════════════════════════════════════════════════════════════
// LIVE ANIMATION BRIDGE — Entity extraction, animation, auto-zoom
// ═══════════════════════════════════════════════════════════════════════

// SQL keywords to exclude from entity extraction
const _SQL_KEYWORDS = new Set([
    'select', 'from', 'where', 'and', 'or', 'not', 'in', 'is', 'null',
    'join', 'inner', 'outer', 'left', 'right', 'on', 'as', 'order', 'by',
    'group', 'having', 'limit', 'offset', 'insert', 'into', 'values',
    'update', 'set', 'delete', 'create', 'drop', 'alter', 'table',
    'index', 'view', 'database', 'schema', 'like', 'between', 'exists',
    'case', 'when', 'then', 'else', 'end', 'distinct', 'count', 'sum',
    'avg', 'min', 'max', 'asc', 'desc', 'union', 'all', 'top', 'with',
]);

/**
 * Extract entity names from an event payload.
 * Returns an array of candidate names to match against graph nodes.
 */
function _extractEntityNames(eventType, payload) {
    const names = new Set();
    if (!payload) return [];

    // Tier 1: Direct argument fields
    const args = payload.arguments || payload.details?.arguments || {};
    if (args.database_name) names.add(args.database_name);
    if (args.object_name) names.add(args.object_name);
    if (args.table_name) names.add(args.table_name);
    if (args.column_name) names.add(args.column_name);

    // Tier 2: SQL parsing via regex
    const sql = args.sql || payload.details?.sql || payload.executed_sql || '';   // a rewrite event carries the statement that actually ran
    if (sql) {
        const patterns = [
            /\bFROM\s+(\w+)/gi,
            /\bJOIN\s+(\w+)/gi,
            /\bINTO\s+(\w+)/gi,
            /\bUPDATE\s+(\w+)/gi,
            /\bTABLE\s+(\w+)/gi,
        ];
        for (const pat of patterns) {
            let m;
            while ((m = pat.exec(sql)) !== null) {
                const candidate = m[1];
                if (!_SQL_KEYWORDS.has(candidate.toLowerCase())) {
                    names.add(candidate);
                }
            }
        }
    }

    // Tier 3: Match known node names against phase goal text
    if ((eventType === 'plan_generated' || eventType === 'phase_start') && _graphState) {
        const goal = payload.goal || '';
        if (goal) {
            const goalLower = goal.toLowerCase();
            for (const node of _graphState.nodes) {
                if (node.name.length >= 3 && goalLower.includes(node.name.toLowerCase())) {
                    names.add(node.name);
                }
            }
        }
        // Also extract from plan phases
        const phases = payload.phases || [];
        for (const phase of phases) {
            const phaseGoal = phase.goal || phase.goal_template || '';
            if (phaseGoal) {
                const gl = phaseGoal.toLowerCase();
                for (const node of _graphState.nodes) {
                    if (node.name.length >= 3 && gl.includes(node.name.toLowerCase())) {
                        names.add(node.name);
                    }
                }
            }
        }
    }

    return [...names];
}

/**
 * Match extracted entity names against graph nodes (case-insensitive).
 * Returns array of matched node data objects.
 */
function _matchNodesToNames(entityNames) {
    if (!_graphState || !entityNames.length) return [];
    // The names come from tool ARGUMENTS — bare identifiers (`Customers`, `V4019`) —
    // while node names are QUALIFIED (`fitness_db.Customers.V4019`). An equality test
    // therefore matched almost nothing on a qualified graph and every touched entity
    // fell through to the server round-trip, losing the instant in-view highlight.
    // Three tiers, most precise first (a precise hit suppresses the looser ones, so a
    // `State` column is never lit up across every table when the table is also known):
    //   1. the full qualified name was given verbatim,
    //   2. EVERY segment of the node name is among the arguments (db+table+column),
    //   3. only if 1+2 found nothing: the leaf segment alone.
    const argSet = new Set(entityNames.filter(Boolean).map(n => String(n).toLowerCase()));
    const exact = [], composed = [], leaf = [];
    for (const node of _graphState.nodes) {
        const full = String(node.name || '').toLowerCase();
        if (!full) continue;
        if (argSet.has(full)) { exact.push(node); continue; }
        const parts = _kgNameSegments(full);
        if (parts.length > 1 && parts.every(pt => argSet.has(pt))) { composed.push(node); continue; }
        if (parts.length && argSet.has(parts[parts.length - 1])) leaf.push(node);
    }
    const precise = exact.concat(composed);
    return precise.length ? precise : leaf;
}

/**
 * Get 1-hop neighbor node IDs for a set of source node IDs.
 */
function _getNeighborIds(sourceIds) {
    if (!_graphState) return new Set();
    const neighbors = new Set();
    const srcSet = new Set(sourceIds);
    for (const link of _graphState.links) {
        const sid = typeof link.source === 'object' ? link.source.id : link.source;
        const tid = typeof link.target === 'object' ? link.target.id : link.target;
        if (srcSet.has(sid)) neighbors.add(tid);
        if (srcSet.has(tid)) neighbors.add(sid);
    }
    // Remove the source nodes themselves
    for (const id of sourceIds) neighbors.delete(id);
    return neighbors;
}

// ─── Animation: Pulse a single node ────────────────────────────────────

function _pulseNode(nodeData) {
    if (!_graphState) return;
    const { nodeGroups, colors } = _graphState;

    // Find the node group element
    const nodeEl = nodeGroups.filter(d => d.id === nodeData.id);
    if (nodeEl.empty()) return;

    // Remove any existing activity ring on this node
    nodeEl.selectAll('.kg-activity-ring').remove();

    const color = colors[nodeData.type] || '#F15F22';

    // Append expanding ring (CSS animation handles the rest)
    nodeEl.append('circle')
        .attr('class', 'kg-activity-ring')
        .attr('r', 16)
        .attr('fill', 'none')
        .attr('stroke', color)
        .attr('stroke-width', 2)
        .attr('stroke-opacity', 0.7);

    // Apply activity glow filter to the rect temporarily
    nodeEl.select('rect')
        .transition('activity')
        .duration(300)
        .attr('filter', 'url(#kg-activity-glow)');

    // Revert rect filter after animation
    const revertTimeout = setTimeout(() => {
        if (!_graphState) return;
        nodeEl.select('rect')
            .transition('activity')
            .duration(600)
            .attr('filter', 'url(#kg-shadow)');
    }, 1200);

    // Clean up the ring element after animation completes
    const cleanupTimeout = setTimeout(() => {
        nodeEl.selectAll('.kg-activity-ring').remove();
    }, 1400);

    // Track for cleanup
    const existing = _activeAnimations.get(nodeData.id);
    if (existing) {
        clearTimeout(existing.revertTimeout);
        clearTimeout(existing.cleanupTimeout);
    }
    _activeAnimations.set(nodeData.id, { revertTimeout, cleanupTimeout });
}

// ─── Animation: Brighten connected edges ───────────────────────────────

function _rippleEdges(nodeData) {
    if (!_graphState) return;
    const { linkLines, linkGroup } = _graphState;

    // Brighten connected edges
    linkLines
        .filter(l => l.source === nodeData || l.target === nodeData ||
                     l.source.id === nodeData.id || l.target.id === nodeData.id)
        .transition('activity')
        .delay(150)
        .duration(400)
        .attr('stroke-opacity', 0.85)
        .attr('stroke-width', 2.5)
        .transition('activity')
        .duration(800)
        .attr('stroke-opacity', 0.5)
        .attr('stroke-width', 1.5);

    // Brighten connected edge labels
    linkGroup.selectAll('text')
        .filter(l => l.source === nodeData || l.target === nodeData ||
                     l.source.id === nodeData.id || l.target.id === nodeData.id)
        .transition('activity')
        .delay(150)
        .duration(400)
        .style('opacity', 0.85)
        .transition('activity')
        .duration(800)
        .style('opacity', null);
}

// ─── Animation: Soft glow on neighbor nodes ────────────────────────────

function _glowNeighbors(nodeData) {
    if (!_graphState) return;
    const { nodeGroups } = _graphState;
    const neighborIds = _getNeighborIds([nodeData.id]);

    nodeGroups
        .filter(d => neighborIds.has(d.id))
        .each(function(d) {
            const el = d3.select(this);
            // Skip hidden/filtered nodes
            if (el.style('display') === 'none') return;

            el.select('rect')
                .transition('neighbor-glow')
                .delay(300)
                .duration(400)
                .attr('filter', `url(#kg-glow-${d.type})`)
                .transition('neighbor-glow')
                .delay(200)
                .duration(600)
                .attr('filter', 'url(#kg-shadow)');
        });
}

// ─── Animation: Orchestrate all layers for matched nodes ───────────────

function _animateNodes(matchedNodes) {
    if (!_graphState || matchedNodes.length === 0) return;
    const { nodeGroups } = _graphState;

    for (const node of matchedNodes) {
        // Skip hidden nodes (filtered out by type pills)
        const nodeEl = nodeGroups.filter(d => d.id === node.id);
        if (nodeEl.empty() || nodeEl.style('display') === 'none') continue;

        _pulseNode(node);
        _rippleEdges(node);
        _glowNeighbors(node);
    }
}

// ─── Auto-Zoom: Smooth camera to relevant subgraph ────────────────────

/**
 * Select (and, on an open, zoom to) the node a graph was opened ON. The names are the
 * model's qualified names (`core.Lead`) or bare leaves (`Lead`) — matched by the same
 * three tiers the execution animation uses. The selection is the click's own
 * (`_graphState.selectNode` = the renderer's single-click path), so the halo, the dim,
 * the lifted dependencies and the `kg:node-select` seam are the ones a click gives;
 * the zoom resets the "person panned" latch, since the open IS the person's act.
 * Returns the selected node, or null when nothing matched (nothing drawn, no throw).
 */
function _focusOpenedNames(names, zoomTo) {
    if (!_graphState || !Array.isArray(names) || !names.length) return null;
    const matched = _matchNodesToNames(names);
    if (!matched.length) return null;
    const primary = matched[0];
    if (typeof _graphState.selectNode === 'function') _graphState.selectNode(primary);
    if (zoomTo) {
        _userZoomOverride = false;
        _centreOnNode(primary);
    }
    return primary;
}

/**
 * Put ONE node at the centre of the view at a reading scale — the opened concept itself,
 * not its neighbourhood's bounding box (which framed the cluster and left the node off
 * centre). Same transition name as the animation's auto-zoom, so the two never fight.
 */
function _centreOnNode(node) {
    if (!_graphState || !node || node.x == null || node.y == null) return;
    const { svg, zoom, width, height } = _graphState;
    const scale = Math.max(0.6, Math.min(1.25, d3.zoomTransform(svg.node()).k || 1));
    const transform = d3.zoomIdentity.translate(width / 2, height / 2).scale(scale).translate(-node.x, -node.y);
    svg.transition('autozoom').duration(700).ease(d3.easeCubicInOut).call(zoom.transform, transform);
}

function _zoomToNodes(matchedNodes) {
    if (!_graphState || matchedNodes.length === 0) return;
    if (_userZoomOverride) return; // User manually panned — skip this event

    const { svg, zoom, width, height, nodes } = _graphState;

    // Collect positions of matched nodes + 1-hop neighbors
    const matchedIds = matchedNodes.map(n => n.id);
    const neighborIds = _getNeighborIds(matchedIds);
    const allRelevantIds = new Set([...matchedIds, ...neighborIds]);

    const relevantNodes = nodes.filter(n => allRelevantIds.has(n.id) && n.x != null && n.y != null);
    if (relevantNodes.length === 0) return;

    // Compute bounding box with padding
    const pad = 80;
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const n of relevantNodes) {
        if (n.x < minX) minX = n.x;
        if (n.x > maxX) maxX = n.x;
        if (n.y < minY) minY = n.y;
        if (n.y > maxY) maxY = n.y;
    }
    minX -= pad; maxX += pad; minY -= pad; maxY += pad;

    const bboxW = maxX - minX;
    const bboxH = maxY - minY;
    if (bboxW <= 0 || bboxH <= 0) return;

    // Calculate scale to fit bbox in viewport
    const scaleX = width / bboxW;
    const scaleY = height / bboxH;
    let scale = Math.min(scaleX, scaleY);
    scale = Math.max(0.4, Math.min(scale, 2.5)); // Clamp

    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;

    const transform = d3.zoomIdentity
        .translate(width / 2, height / 2)
        .scale(scale)
        .translate(-cx, -cy);

    // Debounce: cancel any pending auto-zoom
    if (_autozoomDebounce) clearTimeout(_autozoomDebounce);
    _autozoomDebounce = setTimeout(() => {
        if (!_graphState) return;
        svg.transition('autozoom')
            .duration(800)
            .ease(d3.easeCubicInOut)
            .call(zoom.transform, transform);
        _autozoomDebounce = null;
    }, 200);
}

function _zoomToFitAll() {
    _fitVisibleNodes(1000);
}

// ─── Cleanup ───────────────────────────────────────────────────────────

function _clearAllAnimations() {
    // Remove all activity rings
    if (_graphState) {
        _graphState.nodeGroups.selectAll('.kg-activity-ring').remove();
        // Reset filters to baseline
        _graphState.nodeGroups.select('rect')
            .interrupt('activity')
            .attr('filter', 'url(#kg-shadow)');
        // Reset edges to baseline
        _graphState.linkLines
            .interrupt('activity')
            .attr('stroke-opacity', 0.5)
            .attr('stroke-width', 1.5);
        // Reset neighbor glows
        _graphState.nodeGroups.select('rect').interrupt('neighbor-glow');
    }

    // Cancel all tracked timeouts
    for (const [, entry] of _activeAnimations) {
        clearTimeout(entry.revertTimeout);
        clearTimeout(entry.cleanupTimeout);
    }
    _activeAnimations.clear();

    if (_autozoomDebounce) {
        clearTimeout(_autozoomDebounce);
        _autozoomDebounce = null;
    }

    _userZoomOverride = false;
}

// ─── Exported Bridge API ───────────────────────────────────────────────

/** Check if KG split panel is open and graph is rendered. */
export function isKGPanelActive() {
    // The theatre keeps the panel active while it is mounted, even before a graph has rendered.
    return (!!_graphState && !!_activeSpec) || !!_theatre;
}

/**
 * Capture the current node positions + viewport so the NEXT renderKGFull call keeps
 * the focus identical — existing nodes hold their place, new children settle in, the
 * pan/zoom doesn't jump. Used by the Ontology to expand/collapse a node in place
 * without re-laying-out the whole graph. No-op before the first render.
 */
export function preserveKGView() {
    if (!_graphState || !_graphState.nodes) return;
    _prevPositions = new Map(_graphState.nodes
        .filter(n => n.x != null && n.y != null)
        .map(n => [n.entity_id, { x: n.x, y: n.y }]));
    try {
        const t = d3.zoomTransform(_graphState.svg.node());
        if (t) _pendingAutoZoom = { x: t.x, y: t.y, k: t.k };
    } catch (_) { /* leave default view */ }
}

/**
 * Select + focus a set of nodes by entity_id (external driver, e.g. the Ontology
 * taxonomy tree). Brightens the targets, dims the rest, and smoothly zooms/centres
 * on them. Pass an empty array (or no match) to clear the focus. Additive: KG views
 * that never call it are unaffected.
 *
 * opts.primary (entity_id): the node the gesture was ABOUT. Its direct neighbours —
 * the nodes at the far end of every lit edge — stay at full opacity too, so a lit edge
 * never ends in a dimmed dot (the same one-hop rule the renderer's own click applies
 * via _connectedSet; without it the handler's re-focus overrode that dim and left only
 * the edges highlighted). Neighbour LABELS are revealed only for a small neighbourhood
 * (≤ NEAR_LABEL_MAX) — an agent with forty sessions would otherwise read as label soup.
 */
const NEAR_LABEL_MAX = 12;
export function focusKGNodes(entityIds, opts) {
    if (!_graphState) return;
    const { nodeGroups, linkLines, linkGroup, links, nodes, svg, zoom, width, height, setFocusRing } = _graphState;
    const ids = new Set(entityIds || []);
    const targets = nodes.filter(n => ids.has(n.entity_id));
    if (!targets.length) {
        // clear
        nodeGroups && nodeGroups.interrupt().style('opacity', 1).classed('kg-onto-focus kg-onto-near', false);
        linkLines && linkLines.attr('stroke-opacity', 0.6);
        linkGroup && linkGroup.select('text').style('opacity', 0.85);
        if (setFocusRing) setFocusRing(null);
        return;
    }
    // The SAME orange halo ring a direct graph-click draws — on the single selected node.
    if (setFocusRing) setFocusRing(targets.length === 1 ? targets[0] : null);
    const tset = new Set(targets.map(n => n.id));
    const endId = (e) => (typeof e === 'object' && e) ? e.id : e;
    const touches = (l) => tset.has(endId(l.source)) || tset.has(endId(l.target));
    // One-hop neighbourhood of the primary node (never of a whole group — that would
    // light most of the graph). Targets themselves are excluded: they are focused.
    const primaryEid = opts && opts.primary;
    const primary = primaryEid != null ? targets.find(n => n.entity_id === primaryEid) : null;
    const near = new Set();
    if (primary && Array.isArray(links)) {
        for (const l of links) {
            const s = endId(l.source), t = endId(l.target);
            if (s === primary.id && !tset.has(t)) near.add(t);
            else if (t === primary.id && !tset.has(s)) near.add(s);
        }
    }
    const nearLabels = near.size > 0 && near.size <= NEAR_LABEL_MAX;
    if (nodeGroups) nodeGroups.interrupt()
        .style('opacity', n => (tset.has(n.id) || near.has(n.id)) ? 1 : 0.12)
        .classed('kg-onto-focus', n => tset.has(n.id))    // pop + reveal label on the focused node(s)
        .classed('kg-onto-near', n => nearLabels && near.has(n.id));   // reveal neighbour labels when few
    if (linkLines) linkLines.attr('stroke-opacity', l => touches(l) ? 0.75 : 0.04);
    if (linkGroup) linkGroup.select('text').style('opacity', l => touches(l) ? 0.85 : 0.04);

    // Smoothly zoom/centre on the targets' bounding box.
    const xs = targets.map(n => n.x).filter(v => v != null);
    const ys = targets.map(n => n.y).filter(v => v != null);
    if (xs.length && svg && zoom && !(opts && opts.noZoom)) {
        const minX = Math.min(...xs), maxX = Math.max(...xs);
        const minY = Math.min(...ys), maxY = Math.max(...ys);
        const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
        const bw = Math.max(maxX - minX, 60), bh = Math.max(maxY - minY, 60);
        let scale = Math.min(width / (bw * 1.9), height / (bh * 1.9));
        scale = Math.max(0.45, Math.min(scale, 2.0));
        const transform = d3.zoomIdentity.translate(width / 2, height / 2).scale(scale).translate(-cx, -cy);
        svg.transition('treefocus').duration(600).ease(d3.easeCubicInOut).call(zoom.transform, transform);
    }
}

/**
 * Main entry point — called from eventHandlers.js processStream().
 * Parses event for entity references and triggers animations + auto-zoom.
 */
// ─── Trusted-scope perimeter + drift overlay (grounding gate) ───────────
// The grounding gate's verdict rendered SPATIALLY: the agent's declared scope
// as a dashed boundary around the graph, and out-of-scope references as red
// ghost markers OUTSIDE it with dashed edges crossing the perimeter — the
// canonical drift failure, visible in one glance. Pure overlay: drawn under
// the nodes, wiped by any re-render, never touches the simulation.
function _scopeBBox(pad) {
    const nodes = (_graphState && _graphState.nodes) || [];
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of nodes) {
        if (typeof n.x !== 'number' || typeof n.y !== 'number') continue;
        if (n.x < x0) x0 = n.x; if (n.x > x1) x1 = n.x;
        if (n.y < y0) y0 = n.y; if (n.y > y1) y1 = n.y;
    }
    if (!isFinite(x0)) return null;
    return { x0: x0 - pad, y0: y0 - pad, x1: x1 + pad, y1: y1 + pad };
}

function _drawScopePerimeter(scopeDbs) {
    if (!_graphState) return;
    const { g } = _graphState;
    g.selectAll('.kg-scope-perimeter').remove();
    const bb = _scopeBBox(70);
    if (!bb) return;
    const grp = g.insert('g', ':first-child').attr('class', 'kg-scope-perimeter');
    grp.append('rect')
        .attr('x', bb.x0).attr('y', bb.y0)
        .attr('width', bb.x1 - bb.x0).attr('height', bb.y1 - bb.y0)
        .attr('rx', 26).attr('fill', 'none')
        .attr('stroke-width', 1.5)
        .attr('stroke-dasharray', '7 6')
        .style('stroke', 'var(--trust-pass, #34d399)')
        .style('opacity', 0)
        .transition().duration(500).style('opacity', 0.45);
    const label = scopeDbs.slice(0, 4).join(', ') + (scopeDbs.length > 4 ? '…' : '');
    grp.append('text')
        .attr('x', bb.x0 + 14).attr('y', bb.y0 - 10)
        .attr('font-size', '12px')
        .style('fill', 'var(--trust-pass, #34d399)')
        .style('opacity', 0.8)
        .text(`Trusted scope: ${label}`);
}

function _spawnDriftMarkers(oosRefs, decision) {
    if (!_graphState) return;
    const { g } = _graphState;
    g.selectAll('.kg-drift-ghost').remove();
    const bb = _scopeBBox(70);
    if (!bb) return;
    const cy = (bb.y0 + bb.y1) / 2;
    const color = 'var(--trust-danger, #f87171)';
    oosRefs.slice(0, 5).forEach((ref, i) => {
        const gx = bb.x1 + 130;
        const gy = cy + (i - (Math.min(oosRefs.length, 5) - 1) / 2) * 64;
        const ghost = g.append('g').attr('class', 'kg-drift-ghost').style('opacity', 0);
        // Dashed red edge from the perimeter edge OUT to the ghost — drift made spatial.
        ghost.append('line')
            .attr('x1', bb.x1).attr('y1', cy)
            .attr('x2', gx - 26).attr('y2', gy)
            .attr('stroke-width', 1.5).attr('stroke-dasharray', '4 4')
            .style('stroke', color);
        ghost.append('circle')
            .attr('cx', gx).attr('cy', gy).attr('r', 22)
            .attr('fill', 'none').attr('stroke-width', 2)
            .attr('stroke-dasharray', '3 3')
            .style('stroke', color)
            .attr('class', 'kg-drift-pulse');
        ghost.append('text')
            .attr('x', gx).attr('y', gy + 4)
            .attr('text-anchor', 'middle').attr('font-size', '10px')
            .style('fill', color)
            .text(String(ref).length > 14 ? String(ref).slice(0, 13) + '…' : String(ref));
        ghost.append('text')
            .attr('x', gx).attr('y', gy + 38)
            .attr('text-anchor', 'middle').attr('font-size', '9px')
            .style('fill', color).style('opacity', 0.75)
            .text(decision === 'block' ? 'out of scope — withheld' : 'out of scope');
        ghost.transition().duration(450).style('opacity', 1);
        // Drift markers self-fade — the perimeter stays (it is informative at rest).
        ghost.transition().delay(14000).duration(1200).style('opacity', 0).remove();
    });
}

export function dispatchKGAnimation(eventType, payload) {
    // Execution theatre (opt-in): it consumes every turn event it understands and owns the
    // panel while mounted, so the legacy glow never draws underneath it.
    if (_theatreConsume(eventType, payload)) return;
    if (!_graphState || !_activeSpec) return;

    // Grounding-gate verdict → spatial scope rendering (perimeter + drift).
    if (eventType === 'grounding_gate') {
        try {
            const d = payload || {};
            const scope = d.scope_databases || [];
            if (scope.length) _drawScopePerimeter(scope);
            const oos = [...new Set([...(d.out_of_scope_dbs || []), ...(d.answer_oos_refs || [])])];
            if (oos.length) _spawnDriftMarkers(oos, d.decision);
        } catch (_) { /* overlay is best-effort; the graph is untouched */ }
        return;
    }

    const entityNames = _extractEntityNames(eventType, payload);
    if (!entityNames || entityNames.length === 0) return;

    // Clear user zoom override on new entity match (resume auto-zoom)
    _userZoomOverride = false;

    const matchedNodes = _matchNodesToNames(entityNames);
    // A bare argument (`Customers`) matches a QUALIFIED node (`fitness_db.Customers`),
    // so "did this name land?" is asked against the matched nodes' SEGMENTS as well as
    // their full names — otherwise every in-view hit would still be re-queried from the
    // server as if it had been missed.
    const matchedNames = new Set();
    for (const n of matchedNodes) {
        const full = (n.name || '').toLowerCase();
        if (!full) continue;
        matchedNames.add(full);
        for (const seg of _kgNameSegments(full)) matchedNames.add(seg);
    }
    const unmatched = entityNames.filter(nm => !matchedNames.has((nm || '').toLowerCase()));

    // 1) Animate what's already in view immediately (responsive, ordered by event arrival).
    if (matchedNodes.length) {
        _animateNodes(matchedNodes);
        _zoomToNodes(matchedNodes);
    }

    // 2) World-class trace-driven focus: entities the query touched but that aren't in
    // the current sample must NOT be silently dropped (that's the old bug on large/sampled
    // graphs). Accumulate them as the running focus and pull a focus neighborhood from the
    // server, then animate them once rendered. Focus accumulates so the whole execution
    // path stays in view with its dependency context.
    if (unmatched.length && _activeRequery && (_activeSpec.kg_id || _activeSpec.profile_id)) {
        unmatched.forEach(nm => { if (nm) { _execFocusNames.add(nm); _pendingAnimateNames.add(nm); } });
        _scheduleFocusRequery();   // throttled + guarded + fire-and-forget (no exec latency)
    }
}

/**
 * Graceful wind-down — called on execution_complete or final_answer.
 * Lets in-flight animations finish, then cleans up and zooms to fit-all.
 */
export function endKGExecutionAnimations() {
    _theatreEnd();
    if (!_graphState) return;

    // Reset trace-focus accumulation for the next run (cancel any pending re-query).
    if (_focusRequeryDebounce) { clearTimeout(_focusRequeryDebounce); _focusRequeryDebounce = null; }
    _execFocusNames.clear();
    _pendingAnimateNames.clear();
    _lastActivatedNodeId = null;

    // Let in-flight animations finish naturally, then clean up
    setTimeout(() => {
        _clearAllAnimations();
    }, 500);

    // Smooth zoom back to full view
    setTimeout(() => {
        _zoomToFitAll();
    }, 600);
}


// ─── Style Injection ────────────────────────────────────────────────────

function injectStyles() {
    if (_stylesInjected) return;
    _stylesInjected = true;

    const style = document.createElement('style');
    style.textContent = `
/* ═══════════════════════════════════════════════════════════════════
   Knowledge Graph Component Styles (theme-compliant)
   ═══════════════════════════════════════════════════════════════════ */

/* ─── Trusted-scope drift ghosts (grounding gate overlay) ─────────── */
@keyframes kgDriftPulse {
    0% { stroke-opacity: 0.9; r: 22; }
    50% { stroke-opacity: 0.35; r: 26; }
    100% { stroke-opacity: 0.9; r: 22; }
}
.kg-drift-pulse { animation: kgDriftPulse 1.6s ease-in-out infinite; }
@media (prefers-reduced-motion: reduce) { .kg-drift-pulse { animation: none; } }

/* ─── Inline Compact ────────────────────────────────────────────── */
.kg-inline-compact {
    /* Fallback accent only. The live manifest icon_accent is read at render time and
       set inline on the card root (see _liveKGAccent) so the footer button matches the
       KG split-view colour without a frozen hardcode; this value applies just until the
       live map loads, and stops another panel's --sp-accent (e.g. a dashboard tile's
       pink) cascading in. */
    --sp-accent: #6366f1;
    border: 1px solid var(--border-secondary, rgba(255,255,255,0.08));
    border-radius: 12px;
    background: var(--glass-bg, rgba(20, 22, 25, 0.5));
    overflow: hidden;
}
.kg-inline-compact-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 14px;
    border-bottom: 1px solid var(--border-subtle, var(--border-secondary, rgba(255,255,255,0.06)));
}
.kg-inline-compact-title {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    font-weight: 600;
    color: var(--text-primary, #e2e8f0);
}
.kg-inline-compact-badge {
    font-size: 11px;
    color: var(--text-muted, #9ca3af);
    background: var(--hover-bg, rgba(255,255,255,0.06));
    padding: 2px 8px;
    border-radius: 10px;
}
.kg-inline-compact-graph {
    height: 200px;
    position: relative;
    cursor: grab;
}
.kg-inline-compact-graph:active {
    cursor: grabbing;
}
.kg-inline-compact-footer {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    padding: 8px 14px;
    border-top: 1px solid var(--border-subtle, var(--border-secondary, rgba(255,255,255,0.06)));
}
/* ─── Split Panel ───────────────────────────────────────────────── */
#kg-split-panel {
    width: 0;
    min-width: 0;
    max-width: 55%;
    flex-shrink: 0;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    background: var(--card-bg, rgba(20, 22, 25,0.6));
    border-left: 1px solid var(--border-primary, rgba(255,255,255,0.1));
    transition: width 0.3s ease, min-width 0.3s ease;
}
#kg-split-panel.kg-split--open {
    width: 50%;
    min-width: 320px;
}
/* Header chrome (.sp-header/.sp-switcher/.sp-action-btn) is in static/css/split-panels.css */
.kg-split-body {
    flex: 1;
    overflow: hidden;
    padding: 0;
    display: flex;
    flex-direction: column;
    min-height: 0;
}

/* ─── Fullscreen ────────────────────────────────────────────────── */
.kg-fullscreen #kg-split-panel.kg-split--open {
    position: fixed !important;
    top: var(--kg-fullscreen-top, 0px);
    left: 0 !important;
    right: 0 !important;
    bottom: 0 !important;
    width: auto !important;
    height: auto !important;
    max-width: none;
    z-index: 50;
}

/* ─── Toolbar ───────────────────────────────────────────────────── */
.kg-toolbar {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 12px;
    /* Same fill as .sp-header — one cohesive elevated header surface (slate-800). */
    background: var(--bg-secondary, rgba(31, 33, 38, 0.92));
    border-bottom: 1px solid var(--border-secondary, rgba(255,255,255,0.08));
    flex-shrink: 0;
    flex-wrap: wrap;
}
.kg-toolbar-search {
    display: flex;
    align-items: center;
    gap: 6px;
    background: var(--input-bg, rgba(255,255,255,0.06));
    border: 1px solid var(--border-secondary, rgba(255,255,255,0.08));
    border-radius: 6px;
    padding: 4px 8px;
}
.kg-toolbar-search-input {
    background: transparent;
    border: none;
    outline: none;
    color: var(--text-primary, #e5e7eb);
    font-size: 12px;
    width: 130px;
}
.kg-toolbar-search-input::placeholder {
    color: var(--text-subtle, var(--text-muted, #6b7280));
}
.kg-toolbar-stats {
    font-size: 11px;
    color: var(--text-muted, #9ca3af);
    padding: 3px 8px;
    background: var(--hover-bg, rgba(255,255,255,0.04));
    border-radius: 10px;
    white-space: nowrap;
}
.kg-toolbar-budget {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 0 6px;
}
.kg-budget-label {
    font-size: 11px;
    color: var(--text-muted, #9ca3af);
    white-space: nowrap;
    min-width: 64px;
    text-align: right;
}
.kg-budget-slider {
    -webkit-appearance: none;
    appearance: none;
    width: 110px;
    height: 4px;
    border-radius: 3px;
    background: var(--border-primary, #374151);
    outline: none;
    cursor: pointer;
}
.kg-budget-slider::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 13px; height: 13px;
    border-radius: 50%;
    background: var(--teradata-orange, #F15F22);
    cursor: pointer;
    border: 2px solid var(--card-bg, #111827);
}
.kg-budget-slider::-moz-range-thumb {
    width: 13px; height: 13px;
    border-radius: 50%;
    background: var(--teradata-orange, #F15F22);
    cursor: pointer;
    border: 2px solid var(--card-bg, #111827);
}
.kg-toolbar-filters {
    display: flex;
    gap: 4px;
    align-items: center;
}
.kg-toolbar-filter-pill {
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 10px;
    border-width: 1px;
    border-style: solid;
    cursor: pointer;
    transition: all 0.2s;
}
.kg-toolbar-filter-pill:hover {
    filter: brightness(1.2);
}
.kg-toolbar-btn {
    /* PCS-aligned ghost button (panel-controls.css .pc-btn). Token-driven. */
    --pc-tint: var(--sp-accent, var(--teradata-orange, #F15F22));
    width: var(--ctl-height, 28px);
    height: var(--ctl-height, 28px);
    border-radius: var(--ctl-radius, 8px);
    background: none;
    border: 1px solid transparent;
    color: var(--text-muted, #94a3b8);
    font-size: 14px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: color var(--duration-fast,0.15s) ease, background var(--duration-fast,0.15s) ease, transform var(--duration-instant,0.1s) ease;
}
.kg-toolbar-btn:hover {
    background: var(--hover-bg, rgba(148,163,184,0.08));
    color: var(--text-primary, #e5e7eb);
}
.kg-toolbar-btn:active { transform: var(--ctl-press, scale(0.94)); }
.kg-toolbar-btn svg { width: var(--ctl-icon, 16px); height: var(--ctl-icon, 16px); }

/* ─── Legend ─────────────────────────────────────────────────────── */
.kg-legend {
    position: absolute;
    bottom: 10px;
    right: 10px;
    background: var(--glass-bg-strong, var(--glass-bg, rgba(15,15,20,0.85)));
    backdrop-filter: blur(8px);
    border: 1px solid var(--glass-border, rgba(255,255,255,0.08));
    border-radius: 10px;
    padding: 6px 10px 8px;
    z-index: 5;
    font-size: 11px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.25);
}
.kg-legend-hd {
    display: flex; align-items: center; gap: 8px;
    cursor: move; user-select: none;
    margin: -2px -4px 5px; padding: 2px 4px;
}
.kg-legend.collapsed .kg-legend-hd { margin-bottom: -2px; }
.kg-legend-title {
    flex: 1; font-weight: 700; font-size: 9.5px; text-transform: uppercase;
    letter-spacing: 0.06em; color: var(--text-muted, #94a3b8);
}
.kg-legend-toggle {
    width: 18px; height: 18px; display: inline-flex; align-items: center; justify-content: center;
    border: none; background: transparent; color: var(--text-muted, #94a3b8);
    cursor: pointer; border-radius: 5px; padding: 0; transition: background .12s ease, transform .15s ease;
}
.kg-legend-toggle:hover { background: var(--hover-bg-strong, rgba(255,255,255,0.08)); color: var(--text-primary, #e2e8f0); }
.kg-legend.collapsed .kg-legend-toggle { transform: rotate(-90deg); }
.kg-legend-body { max-height: 42vh; overflow-y: auto; }
@media (prefers-reduced-motion: reduce) { .kg-legend-toggle { transition: none; } }

/* ─── SVG Theme-Aware Fills ─────────────────────────────────────── */
/* CSS fill/stroke properties override SVG presentation attributes, */
/* ensuring CSS variables are evaluated live by the browser engine. */
.kg-node-name  { fill: var(--text-primary, #e5e7eb); }
.kg-edge-label { fill: var(--text-muted, #6b7280); opacity: 0.5; }
.kg-mini-label { fill: var(--text-muted, #9ca3af); }
.kg-mini-link  { stroke: var(--border-primary, #4b5563); }
#kg-arrow path { fill: var(--text-muted, #555); }

/* ─── Tooltip (HTML overlay) ────────────────────────────────────── */
.kg-tooltip {
    position: absolute;
    display: none;
    background: var(--bg-overlay, var(--bg-primary, rgba(15,15,20,0.95)));
    border: 1px solid var(--border-primary, rgba(255,255,255,0.1));
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 12px;
    color: var(--text-primary, #e5e7eb);
    pointer-events: none;
    z-index: 10;
    max-width: 280px;
    backdrop-filter: blur(8px);
}

/* ─── Live Execution Animation ─────────────────────────────────── */
@keyframes kg-activity-expand {
    0%   { stroke-opacity: 0.7; r: 16; }
    40%  { stroke-opacity: 0.5; r: 22; }
    100% { stroke-opacity: 0;   r: 28; }
}
.kg-activity-ring {
    animation: kg-activity-expand 1.2s ease-out forwards;
    pointer-events: none;
}

/* ─── Light Theme Adjustments ─────────────────────────────────── */
[data-theme="light"] .kg-edge-label { opacity: 0.7; }
[data-theme="light"] .kg-mini-link  { stroke-opacity: 0.6; }
    `;
    document.head.appendChild(style);
}
