import { nodesLabel, relationshipsLabel } from '../kgVocabulary.js?v=1.0';   // the graph's counting vocabulary
// ontologyPanelHandler.js — the "Ontology" nav view (Tier 5 Step 1).
//
// An ADDITIONAL-VALUE surface, never a config surface. It shows only what no other
// compartment can — the systemic + trust insight across the whole estate — and it
// LINKS OUT to existing compartments to go deeper. It never embeds configuration.
//
//   • Discover & Route  — "Ask your world": a topic → which agent is grounded to
//                          answer it → ask it (uses the agent; links to chat).
//   • Findings          — trust (ungrounded / conformance), risk (shared / blast-
//                          radius), coverage, gaps — click to locate on the map.
//   • The map           — agents (IFOC-coloured) + the knowledge/trust layer they
//                          stand on. Click a node → inspect + link-out.
//
// Build-on-read (the backend derives it fresh from source-of-truth tables every
// open) → always in sync. See docs/roadmap/TIER_5_PLATFORM_ONTOLOGY.md §5/§5a.

const GRAPH_ID = 'ontology-graph-container';
let _spec = null;          // current segment spec (client-side index for discover/inspect)
let _wired = false;
let _liveWired = false;
let _rendered = false;     // true once a successful build has rendered the live DOM

// ── theme-aware styles (CSS vars + monochrome icons; no hardcoded colours) ──
function _injectCSS() {
    if (document.getElementById('ontology-view-css')) return;
    const css = `
    #ontology-discover .onto-ask {
        display:flex; align-items:center; gap:0.5rem; background:var(--card-bg);
        border:1px solid var(--border-primary); border-radius:0.6rem; padding:0.4rem 0.7rem; }
    #ontology-discover .onto-ask input {
        flex:1; background:transparent; border:none; outline:none; color:var(--text-primary);
        font-size:0.9rem; }
    #ontology-discover .onto-ask input::placeholder { color:var(--text-muted); }
    #ontology-discover .onto-matches { margin-top:0.4rem; display:flex; flex-direction:column; gap:0.3rem; }
    .onto-match { display:flex; align-items:center; justify-content:space-between; gap:0.75rem;
        background:var(--card-bg); border:1px solid var(--border-secondary); border-radius:0.5rem;
        padding:0.45rem 0.7rem; }
    .onto-match .m-main { display:flex; align-items:center; gap:0.6rem; min-width:0; }
    .onto-match .m-dot { width:9px; height:9px; border-radius:50%; flex-shrink:0; }
    .onto-match .m-tag { font-weight:700; color:var(--text-primary); }
    .onto-match .m-knows { color:var(--text-muted); font-size:0.8rem;
        white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .onto-insight-row { display:flex; align-items:center; gap:0.55rem; padding:0.42rem 0.4rem;
        border-radius:0.4rem; font-size:0.84rem; color:var(--text-primary); }
    .onto-insight-row + .onto-insight-row { border-top:1px solid var(--border-secondary); }
    .onto-insight-row.locatable { cursor:pointer; }
    .onto-insight-row.locatable:hover { background:var(--hover-bg-strong); }
    .onto-insight-row .oi-dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }
    .onto-insight-row .oi-text { flex:1; min-width:0; }
    .onto-insight-row .oi-go { color:var(--text-muted); font-size:0.72rem; font-weight:600;
        opacity:0; transition:opacity .12s; white-space:nowrap; }
    .onto-insight-row.locatable:hover .oi-go { opacity:1; }
    .onto-btn { display:inline-flex; align-items:center; gap:0.35rem; font-size:0.8rem; font-weight:600;
        border-radius:0.45rem; padding:0.32rem 0.6rem; cursor:pointer; white-space:nowrap;
        border:1px solid var(--border-primary); background:var(--card-bg); color:var(--text-primary); }
    .onto-btn:hover { background:var(--hover-bg-strong); }
    .onto-btn--accent { border-color:var(--teradata-orange); color:var(--teradata-orange); }
    .onto-btn svg { width:13px; height:13px; }
    #ontology-inspect { width:340px; max-width:88%; transform:translateX(106%); transition:transform .22s ease;
        background:var(--card-bg); border-left:1px solid var(--border-primary);
        box-shadow:-12px 0 30px rgba(0,0,0,0.18); display:flex; flex-direction:column;
        border-top-right-radius:0; overflow:hidden; }
    #ontology-inspect.open { transform:translateX(0); }
    #ontology-inspect .ins-head { display:flex; align-items:flex-start; justify-content:space-between;
        gap:0.5rem; padding:0.9rem 1rem 0.6rem; border-bottom:1px solid var(--border-secondary); }
    #ontology-inspect .ins-title { font-weight:700; color:var(--text-primary); font-size:1.02rem; line-height:1.25; }
    #ontology-inspect .ins-sub { color:var(--text-muted); font-size:0.78rem; margin-top:0.15rem; }
    #ontology-inspect .ins-body { padding:0.8rem 1rem; overflow-y:auto; display:flex; flex-direction:column; gap:0.75rem; }
    #ontology-inspect .ins-x { cursor:pointer; color:var(--text-muted); background:transparent; border:none; padding:2px; line-height:0; }
    #ontology-inspect .ins-x:hover { color:var(--text-primary); }
    .ins-chip { display:inline-flex; align-items:center; gap:0.3rem; border-radius:999px;
        padding:0.18rem 0.55rem; font-size:0.74rem; font-weight:600; border:1px solid; }
    .ins-row { color:var(--text-primary); font-size:0.86rem; }
    .ins-label { color:var(--text-muted); font-size:0.72rem; text-transform:uppercase; letter-spacing:0.04em; margin-bottom:0.2rem; }
    .ins-actions { display:flex; flex-direction:column; gap:0.4rem; margin-top:0.2rem; }

    /* Sync transparency: clear start → end state for the (async) build. */
    .onto-sync { display:inline-flex; align-items:center; gap:0.4rem; font-size:0.8rem;
        padding:0.22rem 0.6rem; border-radius:999px; border:1px solid var(--border-secondary);
        background:var(--card-bg); }
    .onto-sync .dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }
    .onto-sync--busy { color:var(--text-muted); }
    .onto-sync--busy .dot { background:var(--teradata-orange); animation:onto-pulse 1s ease-in-out infinite; }
    .onto-sync--ok { color:#10b981; border-color:#10b98155; }
    .onto-sync--ok .dot { background:#10b981; }
    .onto-sync--err { color:#f59e0b; border-color:#f59e0b55; }
    .onto-sync--err .dot { background:#f59e0b; }
    .onto-sync .counts { color:var(--text-muted); }
    @keyframes onto-pulse { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:0.35;transform:scale(0.7)} }

    /* Change-awareness toast: what changed in your world, in plain language. */
    #ontology-change-toast { position:absolute; top:0.6rem; left:50%; transform:translateX(-50%) translateY(-14px);
        z-index:40; min-width:280px; max-width:520px; opacity:0; pointer-events:none;
        background:var(--card-bg); border:1px solid var(--border-primary); border-radius:0.7rem;
        box-shadow:0 14px 34px rgba(0,0,0,0.22); padding:0.7rem 0.9rem; transition:opacity .25s, transform .25s; }
    #ontology-change-toast.show { opacity:1; transform:translateX(-50%) translateY(0); pointer-events:auto; }
    #ontology-change-toast .ct-head { display:flex; align-items:center; gap:0.45rem; font-weight:700;
        color:var(--text-primary); font-size:0.86rem; margin-bottom:0.35rem; }
    #ontology-change-toast .ct-head svg { width:15px; height:15px; }
    #ontology-change-toast .ct-line { font-size:0.82rem; color:var(--text-primary); display:flex; gap:0.4rem; align-items:baseline; }
    #ontology-change-toast .ct-line .ct-mark { flex-shrink:0; }

    /* Cockpit: view-mode tabs + lens chips */
    .onto-scope-btn { display:inline-flex; align-items:center; gap:0.45rem; font-size:0.82rem; font-weight:600;
        padding:0.4rem 0.75rem; border-radius:0.55rem; cursor:pointer; color:var(--text-primary);
        background:var(--card-bg); border:1px solid var(--border-secondary); margin-right:0.5rem; }
    .onto-scope-btn svg { width:15px; height:15px; }
    .onto-scope-btn:hover { background:var(--hover-bg-strong); }
    .onto-scope-btn.active { border-color:var(--teradata-orange); color:var(--teradata-orange); }
    .onto-scope-btn .osc-caret { color:var(--text-muted); font-size:0.7rem; }
    .onto-scope-pop { position:fixed; z-index:10000; width:300px; background:var(--card-bg);
        border:1px solid var(--border-primary); border-radius:0.7rem; box-shadow:0 12px 40px rgba(0,0,0,0.35);
        padding:0.6rem; display:flex; flex-direction:column; gap:0.5rem; }
    .onto-scope-pop .osp-quick { display:flex; gap:0.4rem; }
    .onto-scope-pop .osp-opt { flex:1; display:inline-flex; align-items:center; justify-content:center; gap:0.4rem;
        font-size:0.8rem; font-weight:600; padding:0.45rem; border-radius:0.5rem; cursor:pointer;
        color:var(--text-primary); background:var(--bg-overlay,transparent); border:1px solid var(--border-secondary); }
    .onto-scope-pop .osp-opt svg { width:14px; height:14px; }
    .onto-scope-pop .osp-opt:hover { background:var(--hover-bg-strong); }
    .onto-scope-pop .osp-opt.active { border-color:var(--teradata-orange); color:var(--teradata-orange); }
    .onto-scope-pop .osp-divider { font-size:0.68rem; text-transform:uppercase; letter-spacing:0.05em;
        color:var(--text-muted); font-weight:700; padding:0.1rem 0.1rem 0; }
    .onto-scope-pop .osp-search { width:100%; box-sizing:border-box; font-size:0.82rem; padding:0.4rem 0.6rem;
        border-radius:0.5rem; border:1px solid var(--border-secondary); background:var(--bg-primary,transparent);
        color:var(--text-primary); }
    .onto-scope-pop .osp-list { max-height:200px; overflow:auto; display:flex; flex-direction:column; gap:0.1rem; }
    .onto-scope-pop .osp-row { display:flex; align-items:center; gap:0.5rem; padding:0.35rem 0.4rem;
        border-radius:0.45rem; cursor:pointer; font-size:0.82rem; color:var(--text-primary); }
    .onto-scope-pop .osp-row:hover { background:var(--hover-bg-strong); }
    .onto-scope-pop .osp-name { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .onto-scope-pop .osp-tier { font-size:0.66rem; text-transform:uppercase; letter-spacing:0.04em;
        color:var(--text-muted); border:1px solid var(--border-secondary); border-radius:0.4rem; padding:0.05rem 0.3rem; }
    .onto-scope-pop .osp-empty { color:var(--text-muted); font-size:0.8rem; padding:0.5rem; text-align:center; }
    .onto-scope-pop .osp-foot { display:flex; justify-content:flex-end; }
    .onto-scope-pop .osp-apply { font-size:0.8rem; font-weight:600; padding:0.4rem 0.8rem; border-radius:0.5rem;
        cursor:pointer; color:#fff; background:var(--teradata-orange); border:none; }
    .onto-scope-pop .osp-apply:disabled { opacity:0.45; cursor:not-allowed; }
    .onto-modes { display:inline-flex; gap:0.25rem; background:var(--card-bg); border:1px solid var(--border-secondary);
        border-radius:0.6rem; padding:0.2rem; }
    .onto-mode-tab { display:inline-flex; align-items:center; gap:0.4rem; font-size:0.82rem; font-weight:600;
        padding:0.35rem 0.7rem; border-radius:0.45rem; cursor:pointer; color:var(--text-muted);
        background:transparent; border:none; }
    .onto-mode-tab svg { width:15px; height:15px; }
    .onto-mode-tab:hover { color:var(--text-primary); }
    .onto-mode-tab.active { color:var(--text-primary); background:var(--hover-bg-strong); }
    .onto-lenses { display:inline-flex; gap:0.3rem; align-items:center; flex-wrap:wrap; }
    .onto-lenses .ll-label { color:var(--text-muted); font-size:0.74rem; text-transform:uppercase; letter-spacing:0.04em; margin-right:0.15rem; }
    .onto-lens-chip { font-size:0.8rem; font-weight:600; padding:0.3rem 0.65rem; border-radius:999px; cursor:pointer;
        border:1px solid var(--border-primary); background:var(--card-bg); color:var(--text-muted); }
    .onto-lens-chip:hover { color:var(--text-primary); }
    .onto-lens-chip.active { color:#fff; border-color:transparent; }

    /* Matrix view: agent × knowledge heatmap */
    .onto-matrix-wrap { padding:0.5rem 0.4rem 2rem; }
    .onto-matrix-legend { display:flex; gap:1.2rem; color:var(--text-muted); font-size:0.78rem; margin:0 0.2rem 1.6rem; align-items:center; flex-wrap:wrap; }
    .onto-matrix-legend .lg { display:inline-flex; align-items:center; gap:0.4rem; }
    .onto-matrix-legend .sw { width:14px; height:14px; border-radius:3px; }
    .onto-matrix-table { border-collapse:separate; border-spacing:0; font-size:0.8rem; }
    .onto-matrix-table th, .onto-matrix-table td { padding:0; }
    .onto-matrix-table .corner { vertical-align:bottom; }
    .onto-matrix-table .col-head { writing-mode:vertical-rl; transform:rotate(180deg); white-space:nowrap;
        color:var(--text-muted); font-weight:600; height:140px; vertical-align:bottom; padding:0 0 8px; }
    .onto-matrix-table .col-head.shared { color:#ef4444; }
    .onto-matrix-table .col-tot { text-align:center; color:var(--text-muted); font-weight:700; font-size:0.74rem; padding-top:6px; }
    .onto-matrix-table .col-tot.shared { color:#ef4444; }
    .onto-matrix-table .row-head { text-align:right; padding-right:0.7rem; white-space:nowrap; color:var(--text-primary); font-weight:600; }
    .onto-matrix-table .row-head .rh-dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:0.45rem; vertical-align:middle; }
    .onto-matrix-table .row-tot { text-align:center; color:var(--text-muted); font-weight:600; padding-left:0.5rem; white-space:nowrap; }
    .onto-matrix-table tbody tr:hover .row-head { color:var(--teradata-orange); }
    .onto-matrix-table .axis-cap { color:var(--text-muted); font-size:0.7rem; font-weight:600; text-transform:uppercase; letter-spacing:0.04em; text-align:right; padding-right:0.7rem; }
    .onto-cell { width:34px; height:34px; border:1px solid var(--border-secondary); position:relative; }
    .onto-cell.colshared { background:rgba(239,68,68,0.05); }
    .onto-cell .cell-fill { position:absolute; inset:4px; border-radius:5px; transition:transform .1s; }
    .onto-cell:hover .cell-fill { transform:scale(1.12); }
    /* Two-pane matrix: grid (left) + Concentration Risk panel (right, fills the width) */
    .onto-matrix-split { display:flex; gap:2rem; align-items:flex-start; flex-wrap:wrap; }
    .onto-matrix-split .mx-grid { flex:0 0 auto; }
    .onto-matrix-table .mx-col-hi { background:rgba(245,158,11,0.14); }
    .onto-matrix-table th.col-head.mx-col-hi { color:var(--teradata-orange) !important; }
    .onto-cr { flex:1 1 300px; min-width:280px; max-width:640px; position:sticky; top:0.5rem; }
    .onto-cr-title { font-size:0.72rem; text-transform:uppercase; letter-spacing:0.05em; color:var(--text-muted); font-weight:700; margin:0 0 0.8rem; }
    .onto-cr-row { display:grid; grid-template-columns:10px 1fr auto; align-items:center; gap:0.6rem; margin-bottom:0.5rem; cursor:pointer; padding:0.25rem 0.35rem; border-radius:8px; transition:background .12s ease; }
    .onto-cr-row:hover { background:var(--hover-bg-strong, rgba(255,255,255,0.05)); }
    .onto-cr-row.active { background:rgba(239,68,68,0.1); }
    .onto-cr-kind { width:9px; height:9px; border-radius:2px; }
    .onto-cr-mid { min-width:0; }
    .onto-cr-name { font-size:0.84rem; color:var(--text-primary); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; display:block; }
    .onto-cr-bar { height:6px; border-radius:999px; background:var(--border-secondary,#293548); margin-top:4px; overflow:hidden; }
    .onto-cr-bar > span { display:block; height:100%; border-radius:999px; background:#ef4444; }
    .onto-cr-n { font-weight:700; font-size:0.84rem; color:var(--text-primary); font-variant-numeric:tabular-nums; }
    .onto-cr-empty { color:var(--text-muted); font-size:0.85rem; padding:0.3rem 0.35rem; }
    .onto-cr-sub { font-size:0.7rem; text-transform:uppercase; letter-spacing:0.05em; color:var(--text-muted); font-weight:700; margin:1.1rem 0 0.5rem; }
    .onto-cr-cov { display:flex; flex-direction:column; gap:0.45rem; font-size:0.83rem; color:var(--text-muted); }
    .onto-cr-cov b { color:var(--text-primary); font-weight:700; font-variant-numeric:tabular-nums; }
    /* Estate Dependencies — value-first gallery (headline risk + source cards) */
    .dg-wrap { padding:0.5rem 0.3rem 2rem; }
    .dg-headline { display:flex; align-items:center; gap:0.9rem; padding:0.9rem 1.15rem; border-radius:14px; margin:0.2rem 0 1.3rem;
        background:var(--card-bg); border:1px solid var(--border-secondary); }
    .dg-headline.risk { border-color:rgba(239,68,68,0.35); background:linear-gradient(90deg, rgba(239,68,68,0.10), rgba(239,68,68,0.015)); }
    .dg-hl-ico { flex:0 0 auto; color:#10b981; display:inline-flex; }
    .dg-headline.risk .dg-hl-ico { color:#ef4444; }
    .dg-hl-body { flex:1; min-width:0; }
    .dg-hl-main { font-size:1rem; color:var(--text-primary); }
    .dg-hl-main b { font-weight:800; }
    .dg-hl-sub { font-size:0.85rem; color:var(--text-muted); margin-top:0.15rem; }
    .dg-hl-stats { display:flex; gap:1.2rem; flex:0 0 auto; font-size:0.82rem; color:var(--text-muted); white-space:nowrap; }
    .dg-hl-stats b { color:var(--text-primary); font-weight:700; font-variant-numeric:tabular-nums; }
    .dg-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(300px, 1fr)); gap:1rem; align-items:start; }
    .dg-card { border:1px solid var(--border-secondary); border-radius:13px; padding:0.75rem 0.9rem; background:var(--card-bg);
        transition:transform .12s ease, box-shadow .15s ease, border-color .15s ease; }
    .dg-card:hover { transform:translateY(-2px); box-shadow:0 10px 26px rgba(0,0,0,0.28); }
    .dg-card.spof { border-color:rgba(239,68,68,0.4); }
    .dg-card-hd { display:flex; align-items:center; gap:0.55rem; margin-bottom:0.55rem; }
    .dg-kind { width:11px; height:11px; border-radius:3px; flex:0 0 auto; }
    .dg-name { flex:1; min-width:0; font-weight:700; font-size:0.92rem; color:var(--text-primary); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .dg-count { font-weight:800; font-size:1.15rem; color:var(--text-primary); font-variant-numeric:tabular-nums; flex:0 0 auto; }
    .dg-bar { height:5px; border-radius:999px; background:var(--border-secondary); overflow:hidden; margin-bottom:0.6rem; }
    .dg-bar > span { display:block; height:100%; border-radius:999px; transition:width .3s ease; }
    .dg-spof-tag { display:inline-flex; align-items:center; gap:0.3rem; font-size:0.72rem; font-weight:600; color:#ef4444; margin-bottom:0.55rem; }
    .dg-spof-tag svg { width:13px; height:13px; }
    .dg-chips { display:flex; flex-wrap:wrap; gap:0.35rem; }
    .dg-chip { display:inline-flex; align-items:center; gap:0.34rem; font-size:0.78rem; font-weight:600; padding:0.22rem 0.55rem; border-radius:999px;
        border:1px solid var(--border-secondary); background:transparent; color:var(--text-primary); cursor:pointer;
        transition:background .12s ease, border-color .12s ease; }
    .dg-chip:hover { background:var(--hover-bg-strong, rgba(255,255,255,0.07)); border-color:var(--border-primary); }
    .dg-chip .dg-dot { width:7px; height:7px; border-radius:50%; flex:0 0 auto; }
    .dg-chip.dim { opacity:0.55; cursor:default; }
    .dg-chip.dim:hover { background:transparent; border-color:var(--border-secondary); }
    .dg-unconf { margin-top:1.4rem; padding-top:1rem; border-top:1px solid var(--border-secondary); display:flex; flex-wrap:wrap; gap:0.4rem; align-items:center; }
    .dg-unconf-hd { font-size:0.7rem; text-transform:uppercase; letter-spacing:0.05em; color:var(--text-muted); font-weight:700; margin-right:0.4rem; }
    .dg-empty { color:var(--text-muted); font-size:0.9rem; padding:2rem; text-align:center; }
    @media (prefers-reduced-motion: reduce) { .dg-card, .dg-bar > span { transition:none; } .dg-card:hover { transform:none; } }

    /* Trust scoreboard */
    .onto-board { padding:0.5rem 0.2rem 2rem; display:flex; flex-direction:column; gap:1rem; }
    .onto-board-cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:0.75rem; }
    .onto-stat { background:var(--card-bg); border:1px solid var(--border-secondary); border-radius:0.7rem; padding:0.8rem 0.9rem; }
    .onto-stat .s-num { font-size:1.9rem; font-weight:800; line-height:1; }
    .onto-stat .s-label { color:var(--text-muted); font-size:0.8rem; margin-top:0.35rem; }
    .onto-stat .s-sub { color:var(--text-muted); font-size:0.74rem; margin-top:0.3rem; }
    .onto-board-section-title { color:var(--text-primary); font-weight:700; font-size:0.95rem; margin:0.3rem 0; }
    .onto-agent-row { display:flex; align-items:center; justify-content:space-between; gap:0.75rem; padding:0.5rem 0.7rem;
        border:1px solid var(--border-secondary); border-radius:0.5rem; background:var(--card-bg); }
    .onto-agent-row .ar-main { display:flex; align-items:center; gap:0.55rem; min-width:0; }
    .onto-agent-row .ar-dot { width:10px; height:10px; border-radius:50%; flex-shrink:0; }
    .onto-agent-row .ar-tag { font-weight:700; color:var(--text-primary); }
    .onto-agent-row .ar-knows { color:var(--text-muted); font-size:0.8rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }

    /* Split Explorer: taxonomy tree navigator (left pane) */
    #ontology-tree { width:288px; border-right:1px solid var(--border-primary); background:var(--bg-overlay, var(--card-bg)); padding:0.5rem 0.4rem 2rem; }
    #ontology-graph-container { left:288px; }
    .onto-tree-head { font-size:0.7rem; font-weight:700; text-transform:uppercase; letter-spacing:0.06em; color:var(--text-muted); padding:0.35rem 0.5rem 0.5rem; }
    .tree-cat, .tree-sub, .tree-leaf { display:flex; align-items:center; gap:0.45rem; cursor:pointer; border-radius:0.4rem; user-select:none; }
    .tree-cat { padding:0.4rem 0.5rem; font-weight:700; color:var(--text-primary); font-size:0.86rem; }
    .tree-sub { padding:0.32rem 0.5rem 0.32rem 1.3rem; font-weight:600; color:var(--text-primary); font-size:0.82rem; }
    .tree-leaf { padding:0.3rem 0.5rem 0.3rem 2.3rem; color:var(--text-primary); font-size:0.82rem; }
    .tree-cat:hover, .tree-sub:hover, .tree-leaf:hover { background:var(--hover-bg-strong); }
    .tree-leaf.selected { background:color-mix(in srgb, var(--teradata-orange) 16%, transparent); }
    .tree-leaf.selected .leaf-label { color:var(--teradata-orange); font-weight:600; }
    .tree-cat.sel, .tree-sub.sel { background:color-mix(in srgb, var(--teradata-orange) 11%, transparent); }
    .tree-cat.sel .tree-cat-label, .tree-sub.sel > span:nth-child(2) { color:var(--teradata-orange); }
    .tree-cat .caret, .tree-sub .caret { width:12px; flex-shrink:0; transition:transform .12s; color:var(--text-muted); font-size:0.7rem; }
    .tree-cat.collapsed .caret, .tree-sub.collapsed .caret { transform:rotate(-90deg); }
    .tree-cat-label, .tree-sub > span:nth-child(2) { flex:1; min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .tree-count { color:var(--text-muted); font-size:0.72rem; font-weight:600; background:var(--hover-bg-strong); border-radius:999px; padding:0.05rem 0.45rem; }
    .leaf-dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }
    .leaf-label { flex:1; min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .tree-leaf .leaf-caret, .tree-subleaf .leaf-caret { width:11px; flex-shrink:0; display:inline-flex; justify-content:center; color:var(--text-muted); font-size:0.65rem; transition:transform .12s; cursor:pointer; }
    .tree-leaf .leaf-caret.collapsed, .tree-subleaf .leaf-caret.collapsed { transform:rotate(-90deg); }
    .tree-leaf .leaf-caret:hover, .tree-subleaf .leaf-caret:hover { color:var(--teradata-orange); }
    .leaf-caret-spacer { width:11px; flex-shrink:0; }
    .tree-subleaf { display:flex; align-items:center; gap:0.45rem; cursor:pointer; border-radius:0.4rem;
        padding:0.26rem 0.5rem 0.26rem 3.1rem; color:var(--text-muted); font-size:0.8rem; }
    .tree-subleaf:hover { background:var(--hover-bg-strong); color:var(--text-primary); }
    `;
    const el = document.createElement('style');
    el.id = 'ontology-view-css';
    el.textContent = css;
    document.head.appendChild(el);
}

const ICONS = {
    shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>',
    badge: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="6"/><path d="M9 13.5 7 22l5-3 5 3-2-8.5"/></svg>',
    globe: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.5 2.5 15 0 18M12 3c-2.5 2.5-2.5 15 0 18"/></svg>',
    share: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="M8.6 13.5l6.8 4M15.4 6.5l-6.8 4"/></svg>',
    cpu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="6" width="12" height="12" rx="1.5"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3"/></svg>',
    unlink: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 17H7A5 5 0 0 1 7 7M15 7h2a5 5 0 0 1 3.5 8.5M8 12h3M2 2l20 20"/></svg>',
    search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
    chat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
    arrow: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
    x: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>',
    sparkle: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v4M12 17v4M3 12h4M17 12h4M6.3 6.3l2.4 2.4M15.3 15.3l2.4 2.4M17.7 6.3l-2.4 2.4M8.7 15.3l-2.4 2.4"/></svg>',
    plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
    minus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M5 12h14"/></svg>',
    map: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="5" r="2"/><circle cx="5" cy="18" r="2"/><circle cx="19" cy="18" r="2"/><path d="M12 7v3M10.5 12.5 6.5 16M13.5 12.5l4 3.5"/></svg>',
    wrench: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a4 4 0 0 0-5.4 5.2l-6 6 2.2 2.2 6-6a4 4 0 0 0 5.2-5.4l-2.3 2.3-2.1-2.1z"/></svg>',
    grid: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>',
    user: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M5 21v-1a7 7 0 0 1 14 0v1"/></svg>',
    users: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="3.5"/><path d="M3 21v-1a6 6 0 0 1 12 0v1"/><path d="M16 5.5a3.5 3.5 0 0 1 0 7M21 21v-1a6 6 0 0 0-4-5.6"/></svg>',
};

// KG entity-type classification — so an expanded knowledge graph reads as a typed
// model (database / table / column / metric …), not an undifferentiated pile.
const _KG_TYPE_META = {
    database: { label: 'database', color: '#3b82f6' },
    schema: { label: 'schema', color: '#60a5fa' },
    domain: { label: 'domain', color: '#f59e0b' },
    entity: { label: 'entity', color: '#10b981' },
    table: { label: 'table', color: '#22c55e' },
    physical_table: { label: 'table', color: '#22c55e' },
    attribute: { label: 'attribute', color: '#14b8a6' },
    column: { label: 'column', color: '#94a3b8' },
    physical_column: { label: 'column', color: '#94a3b8' },
    metric: { label: 'metric', color: '#a855f7' },
    taxonomy: { label: 'type class', color: '#eab308' },
    ddl_artifact: { label: 'DDL', color: '#64748b' },
};
const _KG_TYPE_ORDER = ['database', 'schema', 'domain', 'entity', 'table', 'attribute', 'column', 'metric', 'type class', 'DDL'];
function _kgMeta(t) { return _KG_TYPE_META[String(t || '').toLowerCase()] || { label: String(t || 'concept'), color: '#f59e0b' }; }
// Top-tier ontology CLASSES: a KG expands to these (each aggregates its logical +
// physical realization types), not the granular type soup — so you see Databases /
// Entities / Attributes / Metrics, and drill a class for ALL of its members.
// Each granular type maps to exactly ONE class so counts never double (a logical entity
// and its physical table are the same thing at two layers, counted once each here).
const _KG_CLASSES = [
    // Logical — the business model
    { key: 'databases', label: 'Databases', color: '#3b82f6', layer: 'logical', types: ['database'] },
    { key: 'domains', label: 'Domains', color: '#f59e0b', layer: 'logical', types: ['domain'] },
    { key: 'entities', label: 'Entities', color: '#10b981', layer: 'logical', types: ['entity'] },
    { key: 'attributes', label: 'Attributes', color: '#14b8a6', layer: 'logical', types: ['attribute'] },
    { key: 'metrics', label: 'Metrics', color: '#a855f7', layer: 'logical', types: ['metric'] },
    { key: 'typeclasses', label: 'Type classes', color: '#eab308', layer: 'logical', types: ['taxonomy'] },
    // Physical — the realization
    { key: 'schemas', label: 'Schemas', color: '#60a5fa', layer: 'physical', types: ['schema'] },
    { key: 'tables', label: 'Tables', color: '#22c55e', layer: 'physical', types: ['table', 'physical_table'] },
    { key: 'columns', label: 'Columns', color: '#94a3b8', layer: 'physical', types: ['column', 'physical_column'] },
    { key: 'artifacts', label: 'Artifacts', color: '#64748b', layer: 'physical', types: ['ddl_artifact'] },
];

// ── cockpit state ───────────────────────────────────────────────────────
let _viewMode = 'map';   // 'map' | 'matrix' | 'trust'
let _lens = 'overview';  // map lens: 'overview' | 'trust' | 'knowledge' | 'risk' | 'coordination'
// Admin scope selector: who the ontology spans. 'me' (default) | 'all' | 'specific'.
let _isAdmin = false;

let _scope = 'me';
let _scopeUuids = [];        // selected user uuids when _scope === 'specific'
let _scopeUsers = null;      // cached [{uuid, username, tier}] for the picker (lazy)
function _scopeParam() {
    if (_scope === 'all') return 'all';
    if (_scope === 'specific' && _scopeUuids.length) return _scopeUuids.join(',');
    return 'me';
}

const LENSES = [
    { id: 'overview', label: 'Overview', color: '#9333ea' },   // native IFOC colours
    { id: 'trust', label: 'Trust', color: '#10b981' },
    { id: 'knowledge', label: 'Knowledge', color: '#38bdf8' },
    { id: 'risk', label: 'Risk', color: '#ef4444' },
    { id: 'coordination', label: 'Coordination', color: '#F15F22' },
];
const MODES = [
    { id: 'map', label: 'Map', icon: 'map' },
    { id: 'matrix', label: 'Matrix', icon: 'grid' },
    { id: 'trust', label: 'Trust', icon: 'shield' },
];

const SEVERITY = {
    good: '#10b981', warn: '#f59e0b', info: 'var(--text-muted)',
};
const TYPE_COLORS = {
    agent_ideate: '#4ade80', agent_focus: '#3b82f6', agent_optimize: '#9333ea',
    agent_coordinate: '#F15F22', agent: '#94a3b8',
    knowledge_graph: '#10b981', collection: '#38bdf8', collection_planner: '#818cf8', memory: '#a855f7', domain: '#f59e0b',
    component: '#a78bfa', segment: '#cbd5e1', class_group: '#64748b', session: '#fbbf24', user: '#e2e8f0', kg_typegroup: '#64748b', kg_layer: '#818cf8', onto_group: '#a78bfa',
    concept: '#f59e0b', document: '#0ea5e9', memory_item: '#a855f7',
    llm: '#8b5cf6', mcp_server: '#0ea5e9', platform_connector: '#06b6d4', skill: '#14b8a6', extension: '#eab308', agent_pack: '#ec4899', instance: '#22d3ee',
    vector_store: '#2dd4bf', kg_backend: '#34d399', context_window: '#c084fc',
};

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// ── link-outs to existing compartments (navigation, never config-in-place) ──
async function _switchView(viewId) {
    const { handleViewSwitch } = await import('../ui.js?v=1.287');
    handleViewSwitch(viewId);
}
function _askAgent(tag) {
    _switchView('conversation-view').then(() => {
        const ta = document.getElementById('user-input');
        if (ta) {
            ta.value = `@${tag} `;
            ta.focus();
            ta.dispatchEvent(new Event('input', { bubbles: true }));
            try { ta.setSelectionRange(ta.value.length, ta.value.length); } catch (_) {}
        }
    });
}
async function _inspectKG(kgId, profileId) {
    try {
        const { openKGSplitPanel } = await import('/api/v1/components/knowledge_graph/renderer');
        openKGSplitPanel({ kg_id: kgId, profile_id: profileId, action: 'open', nodes: [] });
    } catch (e) { console.error('[Ontology] inspect KG failed', e); }
}
// ── CONTEXT-SPECIFIC configuration deep-links (to THIS item, never the category) ──
// Deep-link to an Anatomy tab. Switches to the Anatomy view and
// activates the requested sub-tab via its real tab button (the genuine UI path).
async function _openConfigTab(tabId) {
    try {
        const { handleViewSwitch } = await import('../ui.js?v=1.287');
        handleViewSwitch('credentials-view');
    } catch (e) { console.error('[Ontology]', e); return; }
    // Retry the click until it takes effect — the tab buttons are static HTML but their
    // click handler is attached during the config view's (async) init, so an early click
    // can be a no-op. Loop until the target content is actually active.
    for (let i = 0; i < 30; i++) {
        const btn = document.querySelector(`.config-tab[data-tab="${tabId}"]`);
        const content = document.getElementById(tabId);
        if (btn) btn.click();
        if (content && content.classList.contains('active')) return;
        await new Promise(r => setTimeout(r, 100));
    }
}

// Node type → the Anatomy sub-tab that owns it (so every config artefact links
// straight to where it's managed).
const _CONFIG_TAB = {
    llm: 'llm-providers-tab', mcp_server: 'mcp-servers-tab', connector: 'mcp-servers-tab',
    vector_store: 'vector-stores-tab', kg_backend: 'kg-stores-tab',
    skill: 'skills-tab', extension: 'extensions-tab', agent_pack: 'agent-packs-tab',
    context_window: 'context-window-tab',
};

async function _openProfileConfig(profileId) {
    if (!profileId) return;
    await _openConfigTab('profiles-tab');     // switch + load the Profiles tab
    // Trigger the profile card's REAL edit button once it renders (robust against the
    // async profile load — openProfileForEdit's fixed 140ms can fire before it arrives).
    for (let i = 0; i < 30; i++) {
        const btn = document.querySelector(`[data-action="edit-profile"][data-profile-id="${CSS.escape(profileId)}"]`);
        if (btn) { btn.click(); return; }
        await new Promise(r => setTimeout(r, 120));
    }
    try { const m = await import('./configurationHandler.js?v=2.214'); m.openProfileForEdit && m.openProfileForEdit(profileId); } catch (e) { console.error('[Ontology]', e); }
}
async function _openCollection(id, name, repoType) {
    try { const m = await import('../ui.js?v=1.287'); if (m.openCollectionInspection) return m.openCollectionInspection(id, name, repoType || 'knowledge'); } catch (e) { console.error('[Ontology]', e); }
}
async function _openKGInspect(profileId, profileName, kgId) {
    try { const m = await import('./knowledgeGraphPanelHandler.js?v=1.189'); if (m.openKnowledgeGraphInspection) return m.openKnowledgeGraphInspection(profileId, profileName, kgId); } catch (e) { console.error('[Ontology]', e); }
}
async function _openComponentSettings(componentId) {
    try { const m = await import('./componentHandler.js?v=1.189'); if (m.openComponentDetail) return m.openComponentDetail(componentId); } catch (e) { console.error('[Ontology]', e); }
}
function _openAgentPerf(tag) {
    if (window.agentPerformance && window.agentPerformance.openAgent) window.agentPerformance.openAgent(tag);
    else _switchView('agents-view');
}
// Open a component INSTANCE in the conversation surface (its split panel overlays the
// chat) — mirrors the Workspace panel's openers exactly.
async function _openInstance(node) {
    const p = node.properties || {};
    const cid = p.component_id;
    const it = p.raw || { id: p.instance_id, canvas_id: p.instance_id, flow_id: p.instance_id, name: node.name };
    try { await _switchView('conversation-view'); } catch (_) {}
    try {
        if (cid === 'dashboard') {
            const m = await import('/api/v1/components/dashboard/renderer');
            return m.openDashboardPanel({ dashboard_id: it.id ?? p.instance_id, name: it.name ?? node.name });
        }
        if (cid === 'flow') {
            const m = await import('/api/v1/components/flow/renderer');
            return m.openFlowSplitPanel({ view: 'canvas', flow: it, definition: it.definition || { nodes: [], edges: [], scopes: [] }, action: 'open' });
        }
        if (cid === 'scheduler') {
            const m = await import('/api/v1/components/scheduler/renderer');
            return m.openSchedulerSplitPanel({ action: 'list' });
        }
        if (cid === 'canvas') return _openCanvasInstance(it.canvas_id ?? p.instance_id);
    } catch (e) { console.error('[Ontology] open instance failed', e); }
}
// Deep-link straight into a conversation (a session node, or the source session a
// learned memory was extracted from).
async function _openSessionInConversation(sessionId) {
    try { await _switchView('conversation-view'); } catch (_) {}
    try {
        const { handleLoadSession } = await import('./sessionManagement.js?v=3.209');
        await handleLoadSession(sessionId);
    } catch (e) { console.error('[Ontology] open session failed', e); }
}
// Open the actual Agent Memory instantiation (the read/write split canvas) over the
// conversation — scoped to a specific agent (profile_id) or the global pool. NOT the
// component settings (that's for the capability class, not an instantiation).
async function _openMemoryPanel(spec) {
    try { await _switchView('conversation-view'); } catch (_) {}
    try {
        const m = await import('/api/v1/components/memory/renderer');
        if (m.openMemorySplitPanel) m.openMemorySplitPanel(spec || {});
    } catch (e) { console.error('[Ontology] open memory panel failed', e); }
}
async function _openCanvasInstance(canvasId) {
    const token = localStorage.getItem('tda_auth_token');
    let spec = { canvas_id: canvasId, action: 'open' };
    try {
        const r = await fetch(`/api/v1/canvases/${canvasId}`, { headers: { Authorization: `Bearer ${token}` } });
        if (r.ok) {
            const c = await r.json();
            const lang = (c.language || '').toLowerCase();
            spec = { canvas_id: c.canvas_id, title: c.title, language: lang, content: c.content, sources: c.sources || null,
                     previewable: ['html', 'svg', 'markdown'].includes(lang),
                     line_count: ((c.content || '').match(/\n/g) || []).length + 1, versions: c.versions || [], action: 'open' };
        }
    } catch (_) { /* id-only fallback */ }
    const { renderCanvas } = await import('/api/v1/components/canvas/renderer');
    const tempId = `canvas-onto-open-${canvasId}`;
    let tempDiv = document.getElementById(tempId);
    if (!tempDiv) { tempDiv = document.createElement('div'); tempDiv.id = tempId; tempDiv.style.display = 'none'; document.body.appendChild(tempDiv); }
    Promise.resolve(renderCanvas(tempId, { component: 'canvas', ...spec })).finally(() => tempDiv.remove());
}

// ── Discover & Route ────────────────────────────────────────────────────
function _agentSearchIndex() {
    return (_spec.nodes || [])
        .filter(n => (n.type || '').startsWith('agent'))
        .map(n => {
            const p = n.properties || {};
            const knows = (p.grounded_on || []);
            return {
                node: n, tag: p.tag || n.name, ifoc: p.ifoc_class || '', knows,
                hay: [p.tag, p.name, p.ifoc_class, ...knows].filter(Boolean).join(' ').toLowerCase(),
            };
        });
}
function _renderDiscover() {
    const host = document.getElementById('ontology-discover');
    if (!host) return;
    host.innerHTML = `
        <div class="onto-ask">
            <span style="color:var(--text-muted);width:16px;height:16px;display:inline-block">${ICONS.search}</span>
            <input id="onto-ask-input" type="text" placeholder="Ask your world — a topic, and who's grounded to answer it…" autocomplete="off" />
        </div>
        <div id="onto-matches" class="onto-matches"></div>`;
    const input = document.getElementById('onto-ask-input');
    const out = document.getElementById('onto-matches');
    const idx = _agentSearchIndex();
    const run = () => {
        const q = (input.value || '').trim().toLowerCase();
        out.innerHTML = '';
        if (!q) return;
        const toks = q.split(/\s+/);
        const hits = idx
            .map(a => ({ a, score: toks.reduce((s, t) => s + (a.hay.includes(t) ? 1 : 0), 0) }))
            .filter(h => h.score > 0)
            .sort((x, y) => y.score - x.score)
            .slice(0, 6);
        if (!hits.length) {
            out.innerHTML = `<div style="color:var(--text-muted);font-size:0.82rem;padding:0.3rem 0.2rem">No agent in your world is grounded on that yet — a coverage gap.</div>`;
            return;
        }
        hits.forEach(({ a }) => {
            const col = TYPE_COLORS[a.node.type] || '#94a3b8';
            const knows = a.knows.length ? `knows ${esc(a.knows.slice(0, 4).join(', '))}${a.knows.length > 4 ? '…' : ''}` : 'ungrounded';
            const row = document.createElement('div');
            row.className = 'onto-match';
            row.innerHTML = `
                <div class="m-main">
                    <span class="m-dot" style="background:${col}"></span>
                    <span class="m-tag">@${esc(a.tag)}</span>
                    <span class="m-knows">${a.ifoc ? esc(a.ifoc) + ' · ' : ''}${knows}</span>
                </div>
                <button class="onto-btn onto-btn--accent">${ICONS.chat}<span>Ask</span></button>`;
            row.querySelector('button').addEventListener('click', () => _askAgent(a.tag));
            row.querySelector('.m-main').style.cursor = 'pointer';
            row.querySelector('.m-main').addEventListener('click', () => _openInspect(a.node));
            out.appendChild(row);
        });
    };
    input.addEventListener('input', run);
}

// ── Command-center band: estate KPIs + the two cards (Trust-panel idiom) ─
// `tip` → Uderia-UX-compliant custom tooltip (body-appended, overflow-safe).
// `goto` → a mode id ('map'|'matrix'|'trust'); when set the tile becomes a
// context-sensitive control that switches the lower-pane view mode.
function _kpiTile(label, big, sub, accent, ico, tip, goto) {
    const clickable = goto
        ? ` role="button" tabindex="0" data-onto-goto="${goto}" style="cursor:pointer;"`
        : '';
    return `
        <div class="perf-kpi-card"${clickable}${tip ? ` data-tooltip="${esc(tip)}"` : ''}>
            <div class="flex items-center justify-between">
                <div class="min-w-0">
                    <p class="text-sm font-medium" style="color: var(--text-muted);">${label}</p>
                    <p class="text-3xl font-bold mt-1" style="color: var(--text-primary);">${big}</p>
                    ${sub ? `<p class="text-[11px] mt-1 truncate" style="color: var(--text-muted);">${sub}</p>` : ''}
                </div>
                <div class="perf-kpi-icon" style="--kpi-accent: ${accent};">${ico}</div>
            </div>
        </div>`;
}
function _kpiIcon(path) {
    return `<svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`;
}
function _renderKpis() {
    const host = document.getElementById('ontology-kpis');
    if (!host || !_spec) return;
    const ec = _spec.entity_type_counts || {};
    const nodes = _spec.nodes || [];
    const agents = nodes.filter(n => (n.type || '').startsWith('agent'));
    const grounded = agents.filter(n => (n.properties || {}).config_state === 'grounded').length;
    const unconfigured = agents.filter(n => (n.properties || {}).config_state === 'unconfigured').length;
    const totalNodes = _spec.total_nodes ?? nodes.length;
    const edges = (_spec.links || _spec.relationships || []).length;
    const kgNodes = nodes.filter(n => n.type === 'knowledge_graph');
    const kgN = kgNodes.length || (ec.knowledge_graph || 0);
    const kgConformant = kgNodes.filter(n => ((n.properties || {}).conformance_status || '').toLowerCase() === 'conformant').length;
    const domains = ec.domain || 0;
    const findings = _spec.findings || [];
    const warns = findings.filter(f => f.severity === 'warn').length;

    host.innerHTML =
        _kpiTile('Agents mapped', agents.length,
            `${grounded} grounded${unconfigured ? ` · ${unconfigured} unconfigured` : ''}`,
            'var(--teradata-orange)',
            _kpiIcon('<path d="M17 20h5v-2a4 4 0 00-3-3.87M9 20H4v-2a4 4 0 013-3.87m6-2.13a4 4 0 10-4-4 4 4 0 004 4z"/>'),
            `${agents.length} agent${agents.length === 1 ? '' : 's'} on the estate map — ${grounded} grounded on a knowledge graph${unconfigured ? `, ${unconfigured} still unconfigured` : ''}. Click to open the Matrix — who's grounded on what.`,
            'matrix')
        + _kpiTile('Estate nodes', totalNodes, `${edges} connection(s)`, '#38bdf8',
            _kpiIcon('<circle cx="5" cy="6" r="2.2"/><circle cx="19" cy="6" r="2.2"/><circle cx="12" cy="18" r="2.2"/><path d="M6.8 7.4 10.7 16M17.2 7.4 13.3 16M7.2 6h9.6"/>'),
            `${totalNodes} node(s) and ${edges} connection(s) across your whole estate. Click to open the relationship Map.`,
            'map')
        + _kpiTile('Business domains', domains, kgN ? `across ${kgN} knowledge graph${kgN === 1 ? '' : 's'}` : 'no knowledge graphs yet', '#f59e0b',
            _kpiIcon('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>'),
            `${domains} business domain(s)${kgN ? ` spanning ${kgN} knowledge graph${kgN === 1 ? '' : 's'}` : ' — no knowledge graphs yet'}. Click to open the relationship Map.`,
            'map')
        + _kpiTile('Knowledge graphs', kgN, kgN ? `${kgConformant} of ${kgN} conformant` : 'ground an agent to begin', '#10b981',
            _kpiIcon('<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3z"/><path d="M12 12l8-4.5M12 12v9M12 12L4 7.5"/>'),
            kgN ? `${kgN} knowledge graph(s), ${kgConformant} of ${kgN} conformant to the canonical topology. Click to open the relationship Map.` : `No knowledge graphs yet — ground an agent to begin. Click to open the relationship Map.`,
            'map')
        + _kpiTile('Open insights', findings.length, findings.length ? `${warns} warning(s) · ${findings.length - warns} informational` : 'nothing to surface', '#a78bfa',
            _kpiIcon('<path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5"/><path d="M9 18h6"/><path d="M10 22h4"/>'),
            findings.length ? `${findings.length} systemic finding(s): ${warns} warning(s), ${findings.length - warns} informational. Click to open the Trust scoreboard — what to fix.` : `Nothing to surface — your estate looks coherent. Click to open the Trust scoreboard.`,
            'trust');

    // Context-sensitive clicks: each tile switches the lower-pane view mode.
    // Idempotent property assignment (this runs on every refresh).
    host.onclick = (ev) => {
        const tile = ev.target.closest('[data-onto-goto]');
        if (tile) _switchViewMode(tile.dataset.ontoGoto);
    };
    host.onkeydown = (ev) => {
        if (ev.key !== 'Enter' && ev.key !== ' ') return;
        const tile = ev.target.closest('[data-onto-goto]');
        if (tile) { ev.preventDefault(); _switchViewMode(tile.dataset.ontoGoto); }
    };
}
function _renderBandCards() {
    const host = document.getElementById('ontology-band-cards');
    if (!host || !_spec) return;
    const card = (title, note, body, tip, goto) => `
        <div class="rounded-xl p-5 border"${tip ? ` data-tooltip="${esc(tip)}"` : ''}${goto ? ` data-onto-goto="${goto}" role="button" tabindex="0"` : ''} style="background-color: var(--card-bg); border-color: var(--border-primary);${goto ? ' cursor:pointer;' : ''}">
            <div class="flex items-center justify-between mb-4">
                <span class="font-bold" style="color: var(--text-primary);">${title}</span>
                <span class="text-xs" style="color: var(--text-muted);">${note}</span>
            </div>${body}</div>`;

    // Left: estate composition — top entity types as chart rows (config-chart idiom)
    const ec = _spec.entity_type_counts || {};
    const agentTotal = Object.keys(ec).filter(k => k.startsWith('agent')).reduce((a, k) => a + ec[k], 0);
    const merged = { ...ec };
    Object.keys(merged).forEach(k => { if (k.startsWith('agent')) delete merged[k]; });
    if (agentTotal) merged.agents = agentTotal;
    const NICE = { agents: 'Agents', knowledge_graph: 'Knowledge graphs', collection: 'Knowledge repositories',
                   collection_planner: 'Planner repositories', component: 'Components', session: 'Sessions',
                   extension: 'Extensions', skill: 'Skills', llm: 'LLMs', mcp_server: 'MCP servers',
                   context_window: 'Context windows', memory: 'Agent memory', agent_pack: 'Agent packs',
                   domain: 'Business domains', platform_connector: 'Connectors' };
    const rows = Object.entries(merged)
        .filter(([k]) => NICE[k])
        .sort((a, b) => b[1] - a[1])
        .slice(0, 6);
    const maxC = rows.length ? rows[0][1] : 1;
    const colorFor = (k) => k === 'agents' ? 'var(--teradata-orange)' : (TYPE_COLORS[k] || '#94a3b8');
    const crows = rows.map(([k, n]) => `<div class="an-chart-row">
            <span class="an-chart-label">${NICE[k]}</span>
            <span class="an-chart-track"><span class="an-chart-fill" style="width:${Math.round(n / maxC * 100)}%;background:${colorFor(k)};"></span></span>
            <span class="an-chart-num">${n}</span></div>`).join('')
        || `<div style="color:var(--text-muted);font-size:0.82rem;">Your world is empty — create an agent to begin.</div>`;

    // Right: estate insights — the systemic findings as a ranked list (click to locate)
    const findings = _spec.findings || [];
    const irows = findings.length ? findings.map((f, i) => {
        const color = SEVERITY[f.severity] || SEVERITY.info;
        const locatable = !!(f.focus && f.focus.length);
        return `<div class="onto-insight-row${locatable ? ' locatable' : ''}" data-fidx="${i}">
            <span class="oi-dot" style="background:${f.severity === 'info' ? 'var(--text-muted)' : color}"></span>
            <span class="oi-text">${esc(f.text)}</span>
            ${locatable ? '<span class="oi-go">locate →</span>' : ''}</div>`;
    }).join('') : `<div style="color:var(--text-muted);font-size:0.82rem;">Nothing to surface — your estate looks coherent.</div>`;

    host.innerHTML =
        card('Estate Composition', `${_spec.total_nodes ?? (_spec.nodes || []).length} node(s)`, crows,
            'The makeup of your estate by entity type \u2014 what your world is built from. Click to open the relationship Map.', 'map')
        + card('Estate Insights', findings.length ? `${findings.length} finding(s) · click to locate` : 'live systemic findings', irows,
            findings.length ? 'Systemic findings across your estate. Click any locatable finding to jump to it on the Map.' : 'Live systemic findings \u2014 nothing to surface right now.');

    // Delegated clicks: the Composition card switches to the Map view; an
    // insight row locates its subject. Idempotent (runs on every refresh).
    host.onclick = (ev) => {
        const row = ev.target.closest('.onto-insight-row.locatable');
        if (row) {
            const f = findings[parseInt(row.dataset.fidx, 10)];
            if (f && f.focus && f.focus.length) {
                const node = (_spec.nodes || []).find(n => n.entity_id === f.focus[0]);
                if (node) _openInspect(node);
            }
            return;
        }
        const goto = ev.target.closest('[data-onto-goto]');
        if (goto) _switchViewMode(goto.dataset.ontoGoto);
    };
    host.onkeydown = (ev) => {
        if (ev.key !== 'Enter' && ev.key !== ' ') return;
        const goto = ev.target.closest('[data-onto-goto]');
        if (goto) { ev.preventDefault(); _switchViewMode(goto.dataset.ontoGoto); }
    };
}

// ── Canonical mode tab strip (Map · Matrix · Trust — the ind-tab idiom) ──
function _renderModeTabs() {
    const host = document.getElementById('ontology-mode-tabs');
    if (!host) return;
    host.innerHTML = '';
    MODES.forEach(m => {
        const b = document.createElement('button');
        b.className = 'ind-tab ind-tab--underline' + (m.id === _viewMode ? ' active' : '');
        b.innerHTML = `<div class="flex items-center gap-1.5">${ICONS[m.icon]}<span>${m.label}</span></div>`;
        b.title = ({ map: 'Relationship map', matrix: 'Agent × knowledge grid — who\'s grounded on what', trust: 'Trust scoreboard — what to fix' })[m.id];
        b.addEventListener('click', () => _switchViewMode(m.id));
        host.appendChild(b);
    });
}

// ── Inspect panel (understand + link-out; NEVER config-in-place) ─────────
function _closeInspect() {
    const panel = document.getElementById('ontology-inspect');
    if (panel) { panel.classList.remove('open'); panel.setAttribute('aria-hidden', 'true'); }
    _focusEids = null;   // focus released → stop re-applying the ring on re-renders
}
function _openInspect(node) {
    const panel = document.getElementById('ontology-inspect');
    if (!panel || !node) return;
    const p = node.properties || {};
    const col = TYPE_COLORS[node.type] || '#94a3b8';
    const actions = [];
    let head, sub = '', body = '';
    const chip = (txt, c) => `<span class="ins-chip" style="border-color:${c};color:${c}">${txt}</span>`;
    const row = (label, val) => `<div><div class="ins-label">${label}</div><div class="ins-row">${val}</div></div>`;
    const tags = (arr) => [...new Set(arr || [])].map(t => '@' + esc(t)).join(', ');

    if ((node.type || '').startsWith('agent')) {
        const st = p.config_state || 'unconfigured';
        const sm = ({ grounded: { c: '#10b981', i: ICONS.shield, l: 'Configured' }, ungrounded: { c: '#f59e0b', i: ICONS.shield, l: 'Ungrounded' }, unconfigured: { c: '#64748b', i: ICONS.wrench, l: 'Not configured' } })[st] || { c: '#64748b', i: ICONS.wrench, l: 'Not configured' };
        head = '@' + esc(p.tag || node.name);
        sub = `${esc(p.name || '')}${p.ifoc_class ? ' · ' + esc(p.ifoc_class) : ''}`;
        body += `<div>${chip(sm.i + sm.l, sm.c)} ${p.is_coordinator ? chip((p.expert_count || 0) + ' expert' + (p.expert_count === 1 ? '' : 's'), col) : ''}</div>`;
        // Trust Membrane badge (compositional — resolved async from /v1/trust) + one-click
        // detail/sign-off. Guarded: the whole row is omitted if the module isn't loaded.
        if (window.TrustBadge && p.profile_id) {
            body += `<div><div class="ins-label">Trust</div><div class="ins-row" id="onto-trust-slot"><span style="color:var(--text-muted);font-size:0.8rem">resolving…</span></div></div>`;
            actions.push(['', ICONS.badge, 'Trust &amp; sign-off', () => window.TrustBadge.openDetail(p.profile_id, p.tag || node.name)]);
        }
        if (p.grounded_on && p.grounded_on.length) body += row('Grounded on', p.grounded_on.map(esc).join(' · '));
        else body += `<div class="ins-row" style="color:var(--text-muted)">No knowledge, tools or experts bound yet — not configured.</div>`;
        if (p.llm) body += row('Model', esc(p.llm));
        if (p.connector) body += row('Connector', esc(p.connector));
        if ((p.components || []).length) {
            const on = p.components.filter(c => c.enabled !== false);
            const off = p.components.filter(c => c.enabled === false);
            const compChip = (c, en) => `<span class="ins-chip" data-comp="${esc(c.id)}" style="border-color:${en ? (c.accent || '#a78bfa') + '66' : 'var(--border-secondary)'};color:${en ? (c.accent || '#a78bfa') : 'var(--text-muted)'};cursor:pointer${en ? '' : ';opacity:0.65'}" title="${en ? 'Enabled' : 'Disabled'} · open ${esc(c.name)} settings">${esc(c.name)}</span>`;
            body += `<div><div class="ins-label">Components (${on.length} on · ${off.length} off)</div><div style="display:flex;flex-wrap:wrap;gap:0.3rem">${on.map(c => compChip(c, true)).join('')}${off.map(c => compChip(c, false)).join('')}</div></div>`;
        }
        if (p.description) body += row('About', esc(p.description));
        actions.push(['accent', ICONS.chat, `Ask @${esc(p.tag || node.name)}`, () => _askAgent(p.tag || node.name)]);
        actions.push(['', ICONS.arrow, `Configure @${esc(p.tag || node.name)}`, () => _openProfileConfig(p.profile_id)]);
        actions.push(['', ICONS.arrow, 'Anatomy', () => { if (window.AgentAnatomy && window.AgentAnatomy.open) window.AgentAnatomy.open(p.profile_id); }]);
        actions.push(['', ICONS.arrow, 'Agent Performance', () => _openAgentPerf(p.tag || node.name)]);
    } else if (node.type === 'knowledge_graph') {
        head = esc(node.name); sub = p.owner_missing ? 'Knowledge graph · owning agent no longer exists' : `Knowledge graph · owned by @${esc(p.owner_tag || '')}`;
        body += row('Knowledge', `${p.entities != null ? nodesLabel(p.entities) : '– nodes'} · ${p.relationships != null ? relationshipsLabel(p.relationships) : '– relationships'}${p.domains ? ' · ' + p.domains + ' domains' : ''}`);
        const confOk = String(p.conformance_status || '').toLowerCase() === 'conformant';
        body += `<div><div class="ins-label">Trust</div><div>${chip(ICONS.badge + esc(p.conformance_status || 'unrated'), confOk ? '#10b981' : '#f59e0b')} ${p.lifecycle_status ? chip(esc(p.lifecycle_status), 'var(--text-muted)') : ''} ${p.is_active ? chip('active', col) : ''}</div></div>`;
        // used_by = the agents whose RUNTIME grounding resolves to this graph (assignment
        // table), which is a different population from the owner — an owner whose graph
        // is inactive owns it without grounding on it; a pack expert grounds without owning.
        const u = [...new Set(p.used_by || [])];
        body += row('Grounds', u.length ? tags(u) : 'no agent — not the active graph of any assignment');
        if (u.length > 1) body += row('Shared blast-radius', `Grounds ${u.length} agents: ${tags(u)}`);
        body += `<div class="ins-row" style="color:var(--text-muted);font-size:0.8rem">Double-click the node to expand it in place; "Inspect" opens the full graph.</div>`;
        actions.push(['accent', ICONS.arrow, 'Inspect this graph', () => _openKGInspect(p.owner_profile_id, p.owner_tag, p.kg_id)]);
    } else if (node.type === 'collection' || node.type === 'collection_planner') {
        const isPlanner = node.type === 'collection_planner' || p.repository_type === 'planner';
        head = esc(node.name);
        sub = isPlanner ? 'Planner repository · champion strategies' : 'Knowledge repository · reference documents';
        const users = [...new Set(p.used_by || [])];
        body += row(isPlanner ? 'Learned by' : 'Grounds', users.length ? tags(users) : 'No agent (unused)');
        const meta = []; if (p.document_count != null) meta.push(`${p.document_count} ${isPlanner ? 'cases' : 'docs'}`); if (p.embedding_model) meta.push(esc(p.embedding_model)); if (p.owner_username) meta.push('owner ' + esc(p.owner_username));
        if (meta.length) body += row('Details', meta.join(' · '));
        if (users.length > 1) body += `<div class="ins-row" style="color:var(--text-muted);font-size:0.82rem">Shared — changing it affects all ${users.length} agents.</div>`;
        actions.push(['accent', ICONS.arrow, isPlanner ? 'Open this repository' : 'Open this collection', () => _openCollection(p.collection_id, node.name, isPlanner ? 'planner' : p.repository_type)]);
        users.slice(0, 3).forEach(t => actions.push(['', ICONS.chat, `Ask @${esc(t)}`, () => _askAgent(t)]));
    } else if (node.type === 'component') {
        head = esc(node.name);
        if (p.owner_tag) {                       // per-agent variant (agent-expand child)
            const state = p.activated ? 'Activated' : (p.available ? 'Available — not activated' : 'Not available (governance)');
            sub = `Capability · ${state} for @${esc(p.owner_tag)}`;
            body += `<div class="ins-row">${p.activated
                ? '@' + esc(p.owner_tag) + ' has this turned on' + (_childExpandable(node) ? ' — expand to see its instantiations.' : '.')
                : (p.available ? 'Available to @' + esc(p.owner_tag) + ', but not turned on.' : 'Not permitted for @' + esc(p.owner_tag) + ' by governance.')}</div>`;
        } else {                                 // catalogue base node (Components dept)
            const activated = [...new Set(p.activated_by || p.used_by || [])].filter(Boolean);
            const available = [...new Set(p.available_by || [])].filter(Boolean);
            const availOnly = available.filter(t => !activated.includes(t));
            sub = activated.length ? `Capability · activated by ${activated.length} agent${activated.length === 1 ? '' : 's'}`
                : (available.length ? 'Capability · available, not activated anywhere' : 'Capability · in the catalogue');
            if (activated.length) body += row('Activated for', tags(activated));
            if (availOnly.length) body += row('Available — not activated', tags(availOnly));
            if (!activated.length && !available.length) body += `<div class="ins-row" style="color:var(--text-muted)">Not activated or available for any agent yet — open its settings to turn it on for a profile.</div>`;
            body += `<div class="ins-row" style="color:var(--text-muted);font-size:0.8rem"><b>Activated</b> = the profile turned it on · <b>Available</b> = governance permits it but it's off.</div>`;
            activated.slice(0, 3).forEach(t => actions.push(['', ICONS.chat, `Ask @${esc(t)}`, () => _askAgent(t)]));
        }
        actions.push(['accent', ICONS.arrow, `Open ${esc(node.name)} settings`, () => _openComponentSettings(p.component_id)]);
    } else if (node.type === 'memory') {
        head = esc(node.name); sub = p.scope === 'global' ? 'Recalled by every agent' : 'Agent memory';
        body += `<div class="ins-row">${p.items || 0} remembered item${p.items === 1 ? '' : 's'}${p.scope === 'global' ? ' · shared pool' : ''}.</div>`;
        actions.push(['accent', ICONS.arrow, p.scope === 'global' ? 'Open Global memory' : 'Open @' + esc(p.owner_tag || '') + ' memory',
            () => _openMemoryPanel(p.scope === 'global' ? { key: 'global' } : { profile_id: p.owner_profile_id })]);
    } else if (node.type === 'user') {
        head = esc(node.name); sub = (p.tier ? esc(p.tier) + ' · ' : '') + 'platform user';
        if (p.agents != null) body += row('Owns', `${p.agents} agent${p.agents === 1 ? '' : 's'}`);
        body += `<div class="ins-row" style="color:var(--text-muted);font-size:0.82rem">Double-click in the graph (or open in the tree) to drill into this user's agents.</div>`;
        actions.push(['accent', ICONS.search, 'Focus this user', () => _focusGraph([node.entity_id], node.entity_id)]);
    } else if (node.type === 'class_group') {
        head = esc(node.name) + ' agents'; sub = 'Taxonomy group';
        const members = (_spec.nodes || []).filter(n => (n.type || '').startsWith('agent') && (n.properties || {}).ifoc_class === p.ifoc_class);
        const conf = members.filter(m => (m.properties || {}).config_state === 'grounded').length;
        body += row('Agents', `${members.length} · ${conf} configured`);
        actions.push(['accent', ICONS.search, 'Focus this group', () => _focusGraph(members.map(m => m.entity_id))]);
    } else if (node.type === 'segment') {
        head = 'Your Ontology'; sub = 'The whole segment';
        const ec = _spec.entity_type_counts || {};
        const agentN = Object.keys(ec).filter(k => k.startsWith('agent')).reduce((a, k) => a + ec[k], 0);
        body += row('Overview', `${agentN} agents · ${_spec.total_nodes || 0} nodes across ${Object.keys(_spec.taxonomy || {}).length} categories`);
    } else if (node.type === 'instance') {
        const it = p.raw || {};
        const compName = { dashboard: 'Dashboard', flow: 'Flow', canvas: 'Canvas', scheduler: 'Scheduled task' }[p.component_id] || 'Instance';
        head = esc(node.name); sub = compName;
        // Key facts — mirror the conversation card's meta line.
        const facts = [];
        if (p.component_id === 'dashboard' && it.tile_count != null) facts.push(`${it.tile_count} tile${it.tile_count === 1 ? '' : 's'}`);
        if (p.component_id === 'flow') { const n = ((it.definition || {}).nodes || []).length; facts.push(`${n} node${n === 1 ? '' : 's'}`); }
        if (p.component_id === 'canvas' && it.language) facts.push(esc(it.language));
        if (p.component_id === 'scheduler') { const s = it._human_schedule || it.schedule; if (s) facts.push(esc(s)); facts.push(it.enabled ? 'active' : 'paused'); }
        if (it.updated_at) facts.push('updated ' + esc(String(it.updated_at).slice(0, 10)));
        if (facts.length) body += row('Details', facts.join(' · '));
        if (it.description) body += row('About', esc(it.description));
        actions.push(['accent', ICONS.chat, 'Open in conversation', () => _openInstance(node)]);
    } else if (node.type === 'memory_item') {
        const it = p.raw || {};
        head = esc(String(it.content || node.name)); sub = 'Remembered fact';
        const scoped = !!it.profile_id;
        const meta = [];
        if (it.kind) meta.push(esc(it.kind));
        meta.push(scoped ? 'this agent' : 'global');
        if (it.source) meta.push('via ' + esc(it.source));
        body += row('Memory', meta.join(' · '));
        const stats = [];
        if (it.confidence != null) stats.push(Math.round(it.confidence * 100) + '% confidence');
        if (it.recall_count != null) stats.push('recalled ' + it.recall_count + '×');
        if (it.created_at) stats.push('since ' + esc(String(it.created_at).slice(0, 10)));
        if (stats.length) body += row('Provenance', stats.join(' · '));
        if (it.session_id) actions.push(['accent', ICONS.chat, 'Open in conversation', () => _openSessionInConversation(it.session_id)]);
        actions.push(['', ICONS.arrow, 'Open in Memory canvas', () => _openMemoryPanel(it.profile_id ? { profile_id: it.profile_id } : { key: 'global' })]);
    } else if (node.type === 'session') {
        head = esc(node.name); sub = p.profile_tag ? 'Ran under @' + esc(p.profile_tag) : 'Conversation';
        const meta = [];
        if (p.archived) meta.push('archived');
        if (p.is_genie_slave) meta.push('expert sub-session');
        const models = [...new Set(p.models || [])];
        if (models.length) meta.push(models.slice(0, 3).map(esc).join(', '));
        if (meta.length) body += row('Run', meta.join(' · '));
        const when = p.last_updated || p.created_at;
        if (when) body += row('Last active', esc(String(when).slice(0, 16).replace('T', ' ')));
        if (p.profile_id) actions.push(['', ICONS.arrow, p.profile_tag ? 'About @' + esc(p.profile_tag) : 'About agent', () => _focusGraph([`agent:${p.profile_id}`], `agent:${p.profile_id}`)]);
        actions.push(['accent', ICONS.chat, 'Open in conversation', () => _openSessionInConversation(p.session_id)]);
    } else if (node.type === 'kg_layer') {
        head = esc(node.name) + ' layer'; sub = 'Knowledge-graph layer' + (p.kg_name ? ' · ' + esc(p.kg_name) : '');
        if (p.count != null) body += row('Entities', `${p.count} in the ${esc(String(node.name).toLowerCase())} layer`);
        body += `<div class="ins-row" style="color:var(--text-muted);font-size:0.82rem">${p.layer === 'physical' ? 'The realization (schemas, tables, columns) — implements the logical model.' : 'The business model (databases, domains, entities, attributes, metrics).'}</div>`;
        if (p.kg_id) actions.push(['accent', ICONS.arrow, 'Inspect this graph', () => _openKGInspect(p.owner_profile_id, p.owner_tag, p.kg_id)]);
    } else if (node.type === 'kg_typegroup') {
        head = esc(node.name); sub = (p.layer ? esc(p.layer) + ' · ' : '') + 'knowledge-graph class' + (p.kg_name ? ' · ' + esc(p.kg_name) : '');
        if (p.count != null) body += row('In this class', `${p.count} ${esc(String(node.name).toLowerCase())}`);
        body += `<div class="ins-row" style="color:var(--text-muted);font-size:0.82rem">Expand to see all of them; double-click in the graph to drill in place.</div>`;
        if (p.kg_id) actions.push(['accent', ICONS.arrow, 'Inspect this graph', () => _openKGInspect(p.owner_profile_id, p.owner_tag, p.kg_id)]);
    } else if (node.type === 'concept') {
        const it = p.raw || {}; const props = it.properties || {};
        head = esc(node.name); sub = (p.kg_type ? esc(p.kg_type) + ' · ' : '') + 'knowledge-graph node';
        if (props.description) body += row('About', esc(String(props.description).slice(0, 240)));
        const meta = [];
        if (props.layer) meta.push(esc(props.layer) + ' layer');
        if (props.domain) meta.push('domain ' + esc(props.domain));
        if (props.semantic_type) meta.push(esc(props.semantic_type));
        if (it.importance != null) meta.push(Math.round(it.importance * 100) + '% importance');
        if (meta.length) body += row('Details', meta.join(' · '));
        if (p.owner_tag) body += row('In graph of', '@' + esc(p.owner_tag));
        if (p.kg_id) actions.push(['accent', ICONS.arrow, 'Inspect this graph', () => _openKGInspect(p.owner_profile_id, p.owner_tag, p.kg_id)]);
    } else if (node.type === 'document') {
        const it = p.raw || {};
        head = esc(node.name); sub = 'Document in ' + esc(p.collection_name || 'collection');
        const meta = [];
        const chunks = it.chunk_count ?? it.chunks; if (chunks != null) meta.push(chunks + ' chunk' + (chunks === 1 ? '' : 's'));
        if (it.embedding_model) meta.push(esc(it.embedding_model));
        const upd = it.updated_at || it.last_checked_at || it.created_at; if (upd) meta.push('updated ' + esc(String(upd).slice(0, 10)));
        if (meta.length) body += row('Details', meta.join(' · '));
        if (it.source_uri) body += row('Source', esc(it.source_uri));
        if (it.sync_enabled != null) body += row('Sync', it.sync_enabled ? 'continuous (CDC)' : 'manual');
        if (p.collection_id != null) actions.push(['accent', ICONS.arrow, 'Open this collection', () => _openCollection(p.collection_id, p.collection_name, p.repository_type)]);
    } else if (node.type === 'onto_group') {
        // A grouping folder under an agent: Components → { User Interface, Connectors }.
        head = esc(node.name);
        sub = (p.group === 'components' ? 'Components' : p.group === 'connectors' ? 'Platform connectors' : 'User-interface components')
            + (p.owner_tag ? ' · @' + esc(p.owner_tag) : '');
        const kids = p._subgroups || p._members || [];
        const cnt = (p._count != null) ? p._count : kids.length;
        body += row(p.group === 'components' ? 'Sub-classes' : 'Members', String(cnt));
        if ((p._members || []).length) body += row('Includes', esc(p._members.map(m => m.name).slice(0, 10).join(', ')));
        body += `<div class="ins-row" style="color:var(--text-muted);font-size:0.82rem">Expand to drill in; this hierarchy mirrors the agent's component composition.</div>`;
        // A folder; individual components link to their own settings when you drill in.
        if (p.group === 'connectors') actions.push(['accent', ICONS.arrow, 'Open Connectors settings', () => _openComponentSettings('connectors')]);
    } else if (node.type === 'platform_connector') {
        head = esc(node.name);
        const owner = p.owner_tag;   // set on a per-agent connector child (not the shared estate node)
        sub = 'Platform connector' + (owner ? ' · @' + esc(owner) : (p.requires_user_auth ? ' · per-user auth' : ' · shared'));
        const users = owner ? [owner] : [...new Set(p.used_by || [])].filter(Boolean);
        if (owner) body += row('Status', p.enabled ? 'Enabled' : (p.available ? 'Available (not on)' : 'Blocked'));
        else body += row('Enabled for', users.length ? tags(users) : 'No agent has it on');
        if (users.length > 1) body += `<div class="ins-row" style="color:var(--text-muted);font-size:0.82rem">Shared across ${users.length} agents.</div>`;
        body += `<div class="ins-row" style="color:var(--text-muted);font-size:0.8rem">A capability tool (a sub-class of components) — open its settings to configure governance.</div>`;
        actions.push(['accent', ICONS.arrow, 'Open Connectors settings', () => _openComponentSettings('connectors')]);
        users.slice(0, 3).forEach(t => actions.push(['', ICONS.chat, `Ask @${esc(t)}`, () => _askAgent(t)]));
    } else if (node.type === 'skill') {
        head = esc(node.name); sub = (p.is_builtin === false ? 'Custom skill' : 'Built-in skill') + (p.component_id ? ' · companion of ' + esc(p.component_id) : '');
        if (p.description) body += row('About', esc(String(p.description).slice(0, 240)));
        const users = [...new Set(p.used_by || [])].filter(Boolean);
        const explicit = [...new Set(p.explicit_by || [])].filter(Boolean);
        const auto = [...new Set(p.auto_by || [])].filter(Boolean);
        if (explicit.length) body += row('Activated for', tags(explicit));
        if (auto.length) body += row('Auto-assigned for', tags(auto));
        if (!explicit.length && !auto.length) body += row('Active on', users.length ? tags(users) : '—');
        if (p.component_id) body += `<div class="ins-row" style="color:var(--text-muted);font-size:0.8rem"><b>Activated</b> = opted in · <b>Auto-assigned</b> = follows the ${esc(p.component_id)} component being enabled.</div>`;
        users.slice(0, 3).forEach(t => actions.push(['', ICONS.chat, `Ask @${esc(t)}`, () => _askAgent(t)]));
    } else if (node.type === 'agent_pack') {
        head = esc(node.name); sub = (p.pack_type ? esc(p.pack_type) + ' pack' : 'Agent pack') + (p.version ? ' · v' + esc(p.version) : '');
        const meta = [];
        if (p.profiles_count != null) meta.push(`${p.profiles_count} agent${p.profiles_count === 1 ? '' : 's'}`);
        if (p.experts_count) meta.push(`${p.experts_count} expert${p.experts_count === 1 ? '' : 's'}`);
        if (p.collections_count) meta.push(`${p.collections_count} collection${p.collections_count === 1 ? '' : 's'}`);
        if (meta.length) body += row('Bundles', meta.join(' · '));
        const prov = [];
        if (p.author) prov.push('by ' + esc(p.author));
        prov.push(p.is_owned ? 'owned' : 'subscribed');
        body += row('Provenance', prov.join(' · '));
        if (p.coordinator_tag) body += row('Coordinator', '@' + esc(p.coordinator_tag));
        if (p.description) body += row('About', esc(String(p.description).slice(0, 200)));
        if (p.coordinator_tag) actions.push(['accent', ICONS.chat, 'Ask @' + esc(p.coordinator_tag), () => _askAgent(p.coordinator_tag)]);
    } else if (node.type === 'llm') {
        head = esc(node.name); sub = 'LLM' + (p.provider ? ' · ' + esc(p.provider) : '');
        if (p.model) body += row('Model', esc(p.model));
        const u = [...new Set(p.used_by || [])].filter(Boolean);
        body += row('Used by', u.length ? tags(u) : 'No agent uses it');
        u.slice(0, 3).forEach(t => actions.push(['', ICONS.chat, `Ask @${esc(t)}`, () => _askAgent(t)]));
    } else if (node.type === 'mcp_server') {
        head = esc(node.name); sub = 'MCP server · data source';
        const u = [...new Set(p.used_by || [])].filter(Boolean);
        body += row('Tools enabled for', u.length ? tags(u) : 'No agent');
        u.slice(0, 3).forEach(t => actions.push(['', ICONS.chat, `Ask @${esc(t)}`, () => _askAgent(t)]));
    } else if (node.type === 'vector_store') {
        head = esc(node.name); sub = 'Vector store backend' + (p.backend_type ? ' · ' + esc(p.backend_type) : '');
        body += row('Backs', (p.collection_count || 0) + ' knowledge repositor' + (p.collection_count === 1 ? 'y' : 'ies'));
        body += `<div class="ins-row" style="color:var(--text-muted);font-size:0.8rem">Where these repositories physically store their vectors (a data-sovereignty fact).</div>`;
    } else if (node.type === 'kg_backend') {
        head = esc(node.name); sub = 'Graph store backend' + (p.backend_type ? ' · ' + esc(p.backend_type) : '');
        body += row('Backs', (p.kg_count || 0) + ' knowledge graph' + (p.kg_count === 1 ? '' : 's'));
        body += `<div class="ins-row" style="color:var(--text-muted);font-size:0.8rem">Where these knowledge graphs physically live (a data-sovereignty fact).</div>`;
    } else if (node.type === 'context_window') {
        head = esc(node.name); sub = 'Context Window · session working memory';
        if (p.description) body += row('About', esc(String(p.description).slice(0, 200)));
        const u = [...new Set(p.used_by || [])].filter(Boolean);
        body += row('Used by', u.length ? tags(u) : 'No agent');
        body += `<div class="ins-row" style="color:var(--text-muted);font-size:0.8rem">Distinct from Agent Memory — this governs per-session token budgeting, not durable user facts.</div>`;
        u.slice(0, 3).forEach(t => actions.push(['', ICONS.chat, `Ask @${esc(t)}`, () => _askAgent(t)]));
    } else if (node.type === 'extension') {
        head = esc(node.name); sub = 'Extension' + (p.category ? ' · ' + esc(p.category) : '');
        if (p.description) body += row('About', esc(String(p.description).slice(0, 200)));
        const fl = (p.used_in_flows || []).filter(Boolean);
        body += row('Used in flows', fl.length ? esc(fl.slice(0, 8).join(', ')) : 'Not used in any flow yet');
    } else {
        head = esc(node.name); sub = esc(String(node.type || '').replace(/_/g, ' '));
        // Graceful default: surface any scalar facts so every artefact shows detail.
        const skip = new Set(['kind', 'accent', 'raw', 'taxonomy', 'importance', 'component_id', 'instance_id']);
        const facts = Object.entries(p).filter(([k, v]) => !skip.has(k)
            && (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') && String(v).length);
        facts.slice(0, 8).forEach(([k, v]) => body += row(esc(k.replace(/_/g, ' ')), esc(String(v))));
        if (!facts.length) body += `<div class="ins-row" style="color:var(--text-muted)">No additional detail.</div>`;
    }

    // Every Anatomy-department artefact links straight to where it's managed.
    if (_CONFIG_TAB[node.type]) {
        actions.push(['accent', ICONS.arrow, 'Open in Anatomy', () => _openConfigTab(_CONFIG_TAB[node.type])]);
    }

    panel.innerHTML = `
        <div class="ins-head">
            <div style="display:flex;align-items:center;gap:0.5rem;min-width:0">
                <span style="width:11px;height:11px;border-radius:50%;background:${col};flex-shrink:0"></span>
                <div style="min-width:0"><div class="ins-title">${head}</div>${sub ? `<div class="ins-sub">${sub}</div>` : ''}</div>
            </div>
            <button class="ins-x" title="Close">${ICONS.x}</button>
        </div>
        <div class="ins-body">${body}<div class="ins-actions"></div></div>`;
    panel.querySelector('.ins-x').addEventListener('click', _closeInspect);
    panel.querySelectorAll('[data-comp]').forEach(ch => ch.addEventListener('click', () => _openComponentSettings(ch.getAttribute('data-comp'))));
    const actHost = panel.querySelector('.ins-actions');
    actions.forEach(([kind, icon, label, fn]) => {
        const b = document.createElement('button');
        b.className = 'onto-btn' + (kind === 'accent' ? ' onto-btn--accent' : '');
        b.innerHTML = `${icon}<span>${label}</span>`;
        b.addEventListener('click', fn);
        actHost.appendChild(b);
    });
    panel.classList.add('open');
    panel.setAttribute('aria-hidden', 'false');
    // Resolve + fill the agent's live trust badge (async), click → the full detail/sign-off dialog.
    if ((node.type || '').startsWith('agent') && window.TrustBadge && (node.properties || {}).profile_id) {
        const _pid = node.properties.profile_id, _tag = node.properties.tag || node.name;
        const _slot = panel.querySelector('#onto-trust-slot');
        if (_slot) window.TrustBadge.fetchAgent(_pid).then(t => {
            if (!_slot.isConnected) return;
            _slot.innerHTML = window.TrustBadge.badgeHTML(t ? t.state : 'unsigned');
            const b = _slot.querySelector('.tb-badge');
            if (b) b.addEventListener('click', () => window.TrustBadge.openDetail(_pid, _tag));
        });
    }
}

// ── live sync: rebuild when the view is active and the estate changes ────
function _wireLiveSync() {
    if (_liveWired) return;
    _liveWired = true;
    const maybe = () => {
        const v = document.getElementById('ontology-view');
        if (v && v.classList.contains('active')) loadOntologyView({ force: true });
    };
    UderiaBus.on('uderia:turn-complete', maybe);
    UderiaBus.on('uderia:config-saved', maybe);
}

// ── sync transparency (clear start → end of the build) ──────────────────
let _lastSyncAt = 0;
let _relTimer = null;
function _relTime(ts) {
    if (!ts) return '';
    const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
    if (s < 5) return 'just now';
    if (s < 60) return `${s}s ago`;
    const m = Math.round(s / 60);
    if (m < 60) return `${m}m ago`;
    return `${Math.round(m / 60)}h ago`;
}
function _countsText() {
    if (!_spec) return '';
    const ec = _spec.entity_type_counts || {};
    const agentN = Object.keys(ec).filter(k => k.startsWith('agent')).reduce((a, k) => a + ec[k], 0);
    const n = _spec.total_nodes ?? (_spec.nodes || []).length;
    return `${agentN} agent${agentN === 1 ? '' : 's'} · ${n} node${n === 1 ? '' : 's'}`;
}
function _setSync(state) {
    const host = document.getElementById('ontology-summary');
    if (!host) return;
    if (state === 'busy') {
        host.innerHTML = `<span class="onto-sync onto-sync--busy"><span class="dot"></span>Syncing…</span>`;
    } else if (state === 'ok') {
        host.innerHTML = `<span class="onto-sync onto-sync--ok"><span class="dot"></span>In sync · <span class="onto-rel">just now</span></span> <span class="onto-sync"><span class="counts">${_countsText()}</span></span>`;
        _startRelTicker();
    } else if (state === 'err') {
        host.innerHTML = `<span class="onto-sync onto-sync--err"><span class="dot"></span>Couldn't sync</span>`;
    }
}
function _startRelTicker() {
    _stopRelTicker();
    _relTimer = setInterval(() => {
        const v = document.getElementById('ontology-view');
        const el = document.querySelector('#ontology-summary .onto-rel');
        if (!v || !v.classList.contains('active')) { _stopRelTicker(); return; }
        if (el) el.textContent = _relTime(_lastSyncAt);
    }, 5000);
}
function _stopRelTicker() { if (_relTimer) { clearInterval(_relTimer); _relTimer = null; } }

// ── change-awareness: what changed in your world since the last sync ─────
function _agentsById(spec, kind) {
    const m = {};
    (spec.nodes || []).forEach(n => {
        if (kind === 'agent' && (n.type || '').startsWith('agent')) m[n.entity_id] = n;
        else if (kind && n.type === kind) m[n.entity_id] = n;
    });
    return m;
}
function _diffSpecs(prev, next) {
    if (!prev || !next) return null;
    const lines = [];
    const pa = _agentsById(prev, 'agent'), na = _agentsById(next, 'agent');
    const pIds = new Set(Object.keys(pa)), nIds = new Set(Object.keys(na));
    // grounding transitions (the trust-relevant change)
    Object.keys(na).forEach(id => {
        if (pa[id]) {
            const was = !!pa[id].properties?.grounded, now = !!na[id].properties?.grounded;
            const tag = na[id].properties?.tag || na[id].name;
            if (!was && now) lines.push({ m: 'plus', warn: false, t: `@${tag} is now grounded` });
            if (was && !now) lines.push({ m: 'minus', warn: true, t: `@${tag} is no longer grounded (trust risk)` });
        }
    });
    const addedA = [...nIds].filter(id => !pIds.has(id));
    const removedA = [...pIds].filter(id => !nIds.has(id));
    addedA.forEach(id => lines.push({ m: 'plus', warn: false, t: `New agent @${na[id].properties?.tag || na[id].name}` }));
    if (removedA.length) lines.push({ m: 'minus', warn: false, t: `${removedA.length} agent${removedA.length === 1 ? '' : 's'} removed` });
    // knowledge added/removed
    const kgDelta = (next.entity_type_counts?.knowledge_graph || 0) - (prev.entity_type_counts?.knowledge_graph || 0);
    const colDelta = (next.entity_type_counts?.collection || 0) - (prev.entity_type_counts?.collection || 0);
    if (kgDelta > 0) lines.push({ m: 'plus', warn: false, t: `${kgDelta} knowledge graph${kgDelta === 1 ? '' : 's'} added` });
    if (colDelta > 0) lines.push({ m: 'plus', warn: false, t: `${colDelta} collection${colDelta === 1 ? '' : 's'} added` });
    if (colDelta < 0) lines.push({ m: 'minus', warn: false, t: `${-colDelta} collection${colDelta === -1 ? '' : 's'} removed` });
    return lines.slice(0, 4);
}
let _toastTimer = null;
function _showChangeToast(lines) {
    if (!lines || !lines.length) return;
    const stage = document.getElementById('ontology-stage');
    if (!stage) return;
    let toast = document.getElementById('ontology-change-toast');
    if (!toast) { toast = document.createElement('div'); toast.id = 'ontology-change-toast'; stage.appendChild(toast); }
    toast.innerHTML = `
        <div class="ct-head"><span style="color:var(--teradata-orange)">${ICONS.sparkle}</span>Your world changed</div>
        ${lines.map(l => `<div class="ct-line"><span class="ct-mark" style="color:${l.warn ? '#f59e0b' : '#10b981'}">${ICONS[l.m]}</span><span>${esc(l.t)}</span></div>`).join('')}`;
    requestAnimationFrame(() => toast.classList.add('show'));
    if (_toastTimer) clearTimeout(_toastTimer);
    _toastTimer = setTimeout(() => { toast.classList.remove('show'); }, 6000);
    toast.onclick = () => { toast.classList.remove('show'); };
}

// ── cockpit: view-mode tabs + lens chips ────────────────────────────────
// Admin-only scope selector — who the ontology spans (default: just me). Best-practice:
// quick "Just me" / "All users", plus a searchable multi-select; applied on choice.
function _scopeLabel() {
    if (_scope === 'all') return 'All users';
    if (_scope === 'specific') return _scopeUuids.length === 1 ? '1 user' : `${_scopeUuids.length} users`;
    return 'Just me';
}
async function _ensureScopeUsers() {
    if (_scopeUsers) return _scopeUsers;
    try {
        const token = localStorage.getItem('tda_auth_token');
        const r = await fetch('/api/v1/platform-ontology/users', { headers: { Authorization: `Bearer ${token}` } });
        const d = await r.json();
        _scopeUsers = (d.status === 'success' && d.users) ? d.users : [];
    } catch (_) { _scopeUsers = []; }
    return _scopeUsers;
}
function _applyScope(scope, uuids) {
    _scope = scope;
    if (uuids) _scopeUuids = uuids;
    _rendered = false;                       // force a real rebuild for the new scope
    _closeScopePopover();
    loadOntologyView({ force: true });
}
let _scopePop = null;
function _closeScopePopover() { if (_scopePop) { _scopePop.remove(); _scopePop = null; document.removeEventListener('keydown', _scopeEsc); } }
function _scopeEsc(e) { if (e.key === 'Escape') _closeScopePopover(); }
async function _openScopePopover(anchor) {
    if (_scopePop) { _closeScopePopover(); return; }
    const pop = document.createElement('div');
    pop.className = 'onto-scope-pop';
    _scopePop = pop;
    pop.innerHTML = `
        <div class="osp-quick">
            <button class="osp-opt${_scope === 'me' ? ' active' : ''}" data-scope="me">${ICONS.user || ''}<span>Just me</span></button>
            <button class="osp-opt${_scope === 'all' ? ' active' : ''}" data-scope="all">${ICONS.users || ICONS.user || ''}<span>All users</span></button>
        </div>
        <div class="osp-divider">Specific users</div>
        <input class="osp-search" type="text" placeholder="Search users…" aria-label="Search users">
        <div class="osp-list" role="listbox"></div>
        <div class="osp-foot"><button class="osp-apply" disabled>Apply selection</button></div>`;
    document.body.appendChild(pop);
    const r = anchor.getBoundingClientRect();
    pop.style.top = `${Math.round(r.bottom + 6)}px`;
    pop.style.left = `${Math.round(Math.min(r.left, window.innerWidth - 312))}px`;
    pop.querySelectorAll('.osp-opt').forEach(b => b.addEventListener('click', () => _applyScope(b.dataset.scope, [])));
    const listEl = pop.querySelector('.osp-list');
    const applyBtn = pop.querySelector('.osp-apply');
    const sel = new Set(_scope === 'specific' ? _scopeUuids : []);
    const syncApply = () => { applyBtn.disabled = sel.size === 0; };
    const renderList = (users, q) => {
        const ql = (q || '').toLowerCase();
        listEl.innerHTML = users
            .filter(u => !ql || (u.username || '').toLowerCase().includes(ql))
            .map(u => `<label class="osp-row"><input type="checkbox" data-uuid="${esc(u.uuid)}" ${sel.has(u.uuid) ? 'checked' : ''}>
                <span class="osp-name">${esc(u.username)}</span><span class="osp-tier">${esc(u.tier || 'user')}</span></label>`).join('')
            || `<div class="osp-empty">No users</div>`;
        listEl.querySelectorAll('input[type=checkbox]').forEach(cb => cb.addEventListener('change', () => {
            cb.checked ? sel.add(cb.dataset.uuid) : sel.delete(cb.dataset.uuid); syncApply();
        }));
    };
    listEl.innerHTML = `<div class="osp-empty">Loading…</div>`;
    const users = await _ensureScopeUsers();
    renderList(users, '');
    syncApply();
    pop.querySelector('.osp-search').addEventListener('input', (e) => renderList(users, e.target.value));
    applyBtn.addEventListener('click', () => sel.size && _applyScope('specific', [...sel]));
    setTimeout(() => document.addEventListener('keydown', _scopeEsc), 0);
    setTimeout(() => document.addEventListener('click', function oc(ev) {
        if (_scopePop && !_scopePop.contains(ev.target) && ev.target !== anchor && !anchor.contains(ev.target)) {
            _closeScopePopover(); document.removeEventListener('click', oc);
        }
    }), 0);
}

function _renderControls() {
    const host = document.getElementById('ontology-controls');
    if (!host) return;

    const lenses = document.createElement('div');
    lenses.className = 'onto-lenses';
    if (_viewMode === 'map') {
        lenses.innerHTML = `<span class="ll-label">Lens</span>`;
        LENSES.forEach(l => {
            const c = document.createElement('button');
            const on = l.id === _lens;
            c.className = 'onto-lens-chip' + (on ? ' active' : '');
            if (on) { c.style.background = l.color; c.style.borderColor = l.color; }
            c.textContent = l.label;
            c.addEventListener('click', () => _applyLens(l.id));
            lenses.appendChild(c);
        });
    }
    host.innerHTML = '';
    // Left group: scope (admin only) — the mode switcher lives in the tab strip now.
    const left = document.createElement('div');
    left.style.cssText = 'display:inline-flex;align-items:center;gap:0.5rem;';
    if (_isAdmin) {
        const scope = document.createElement('button');
        scope.className = 'onto-scope-btn' + (_scope !== 'me' ? ' active' : '');
        scope.innerHTML = `${ICONS.users || ICONS.user || ''}<span>Scope: ${esc(_scopeLabel())}</span><span class="osc-caret">▾</span>`;
        scope.title = 'Choose whose agents the ontology spans';
        scope.addEventListener('click', (e) => { e.stopPropagation(); _openScopePopover(scope); });
        left.appendChild(scope);
    }
    host.appendChild(left);
    host.appendChild(lenses);

    // Graph actions (zoom-to-fit + export PNG) — hosted in this controls row, which has
    // spare space, instead of wrapping to a lonely line under the pills bar.
    if (_viewMode === 'map') {
        const acts = document.createElement('div');
        acts.className = 'onto-graph-acts';
        acts.style.cssText = 'display:inline-flex;gap:0.3rem;align-items:center;';
        const FIT = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg>';
        const DL = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
        const mk = (svg, title, name) => {
            const b = document.createElement('button');
            b.className = 'onto-mode-tab';
            b.title = title;
            b.innerHTML = svg;
            b.addEventListener('click', async () => {
                try { const m = await import('/api/v1/components/knowledge_graph/renderer'); m[name] && m[name](); } catch (e) { console.error('[Ontology]', e); }
            });
            return b;
        };
        acts.appendChild(mk(FIT, 'Zoom to fit', 'kgFitView'));
        acts.appendChild(mk(DL, 'Export as PNG', 'kgExportPNG'));
        host.appendChild(acts);
    }
}

function _show(id, on) {
    const el = document.getElementById(id);
    if (el) el.style.display = on ? '' : 'none';
}
function _switchViewMode(mode) {
    _viewMode = mode;
    _closeInspect();
    _show('ontology-tree', mode === 'map');          // the navigator pane (Explorer)
    _show('ontology-graph-container', mode === 'map');
    _show('ontology-matrix', mode === 'matrix');
    _show('ontology-scoreboard', mode === 'trust');
    const gc = document.getElementById('ontology-graph-container');
    if (gc) gc.style.left = mode === 'map' ? '288px' : '0';
    _renderModeTabs();
    _renderControls();
    if (mode === 'map') { _renderTree(); _renderMap(); }
    else if (mode === 'matrix') _renderMatrix();
    else if (mode === 'trust') _renderScoreboard();
}
function _applyLens(lens) {
    _lens = lens;
    _renderControls();
    _renderMap();
}
async function _renderActiveView() {
    _renderControls();
    if (_viewMode === 'matrix') return _renderMatrix();
    if (_viewMode === 'trust') return _renderScoreboard();
    _renderTree();
    return _renderMap();
}

// ── Map: the relationship graph, re-encoded by the active lens ──────────
// Nodes the user has double-clicked to EXPAND (their children are injected in place).
const _expandedNodes = new Set();

// Cache of fetched children (KG concepts / collection docs / memory items / component
// instances), keyed by the parent node's entity_id, so re-expanding is instant.
const _childCache = {};

// Lookup over the CURRENTLY RENDERED graph (base nodes + injected children), so a
// click/double-click on an injected node (component, concept, instance…) resolves.
let _renderedNodes = new Map();
let _focusEids = null;   // entity ids currently focused (re-applied after a re-render so the ring survives expand/lens)
let _focusPrimary = null; // the node the focus is ABOUT — its one-hop neighbours stay lit (focusKGNodes opts.primary)
function _findNode(eid, name) {
    return _renderedNodes.get(eid)
        || (_spec && (_spec.nodes || []).find(n => n.entity_id === eid || n.name === name))
        || null;
}

// The children a node reveals when expanded (double-click). Agents are synchronous
// (their components are already in the spec); KG/collection/memory are fetched once
// by _ensureChildren and read from the cache here.
function _childrenOf(node) {
    const p = node.properties || {};
    if ((node.type || '').startsWith('agent')) {
        const children = [];
        const pid = p.profile_id;
        // Configuration composition: model + data-source connector (shared ids → dedup).
        if (p.llm_id) children.push({ entity_id: `llm:${p.llm_id}`, name: p.llm || 'Model', type: 'llm', rel: 'uses_model', properties: { kind: 'llm', accent: '#8b5cf6' }, importance: 0.42 });
        if (p.connector_id) children.push({ entity_id: `mcp_server:${p.connector_id}`, name: p.connector || 'Connector', type: 'mcp_server', rel: 'uses_connector', properties: { kind: 'mcp_server', accent: '#0ea5e9' }, importance: 0.42 });
        // Components hierarchy: Components → { User Interface, Connectors }. The UI
        // components (chart/canvas/dashboard/flow/KG/memory/scheduler…) and the platform
        // connectors (browser/files/Google/Slack…) are the two sub-classes. The dot is
        // greyed by INSTANTIATION count (renderer), not enablement, so carry the real
        // accent; activated vs available are kept distinct.
        const uiKids = (p.components || []).map(c => {
            const on = c.enabled !== false;
            return {
                entity_id: `component:${pid}:${c.id}`,
                name: c.name, type: 'component', rel: on ? 'can_use' : (c.available ? 'available' : 'blocked'),
                properties: { kind: 'component', component_id: c.id, accent: c.accent || '#a78bfa', enabled: on, available: c.available !== false, activated: !!c.activated, owner_tag: p.tag, owner_profile_id: pid },
                importance: on ? 0.4 : 0.28,
            };
        });
        const connKids = (p.connectors || []).map(c => {
            const on = c.enabled !== false;
            return {
                entity_id: `platform_connector:${pid}:${c.id}`,
                name: c.name || c.id, type: 'platform_connector', rel: on ? 'can_use' : 'available',
                properties: { kind: 'platform_connector', connector_id: c.id, accent: TYPE_COLORS.platform_connector, enabled: on, available: c.available !== false, owner_tag: p.tag, owner_profile_id: pid },
                importance: on ? 0.38 : 0.26,
            };
        });
        children.push({
            entity_id: `compgroup:${pid}`, name: 'Components', type: 'onto_group', rel: 'has', importance: 0.46,
            properties: {
                kind: 'onto_group', group: 'components', accent: '#a78bfa', owner_tag: p.tag, owner_profile_id: pid,
                _count: uiKids.length + connKids.length,
                _subgroups: [
                    { entity_id: `compui:${pid}`, name: 'User Interface', type: 'onto_group', rel: 'contains', importance: 0.42,
                      properties: { kind: 'onto_group', group: 'ui', accent: '#a78bfa', owner_tag: p.tag, owner_profile_id: pid, _count: uiKids.length, _members: uiKids } },
                    { entity_id: `compconn:${pid}`, name: 'Connectors', type: 'onto_group', rel: 'contains', importance: 0.42,
                      properties: { kind: 'onto_group', group: 'connectors', accent: TYPE_COLORS.platform_connector, owner_tag: p.tag, owner_profile_id: pid, _count: connKids.length, _members: connKids } },
                ],
            },
        });
        return children;
    }
    if (node.type === 'onto_group') return (p._subgroups || p._members || []);
    // Agent Memory component (when activated) ALWAYS exposes two sub-nodes — "Agents"
    // (this agent's pool) and "Global Memory" (the shared pool) — named to stay consistent
    // with the top-tier Memory department (Memory → Agents / Global). Each links to the
    // real memory node in the graph, which itself drills to its remembered items.
    if (node.type === 'component' && p.component_id === 'memory' && p.owner_profile_id) {
        const local = _findNode('memory:' + p.owner_profile_id);
        const global = _findNode('memory:__global__')
            || (_spec.nodes || []).find(x => x.type === 'memory' && (x.properties || {}).scope === 'global');
        const out = [];
        if (local) out.push({ entity_id: local.entity_id, name: 'Agents', type: 'memory', rel: 'has',
            properties: { ...(local.properties || {}), accent: TYPE_COLORS.memory }, importance: 0.4 });
        if (global) out.push({ entity_id: global.entity_id, name: 'Global Memory', type: 'memory', rel: 'has',
            properties: { ...(global.properties || {}), accent: TYPE_COLORS.memory }, importance: 0.4 });
        return out;
    }
    return _childCache[node.entity_id] || [];
}

// Fetch + cache a node's children (uniform "expand in place" for every type).
async function _ensureChildren(node) {
    const eid = node.entity_id;
    const isMemComp = node.type === 'component' && (node.properties || {}).component_id === 'memory';
    if ((node.type || '').startsWith('agent') || node.type === 'onto_group' || isMemComp || _childCache[eid]) return;
    const token = localStorage.getItem('tda_auth_token');
    const auth = { headers: { Authorization: `Bearer ${token}` } };
    let children = [];
    try {
        if (node.type === 'knowledge_graph') {
            // A KG expands to its two LAYERS — Logical (the business model) first, then
            // Physical (its realization) — each with a full count. Drill a layer for its
            // classes, a class for ALL its members. No double-counting across layers.
            const p = node.properties || {};
            const q = new URLSearchParams({ kg_id: p.kg_id, max_nodes: '1' });
            if (p.owner_profile_id) q.set('profile_id', p.owner_profile_id);
            const r = await fetch(`/api/v1/knowledge-graph/graph-spec?${q}`, auth);
            const d = await r.json();
            const counts = (d.spec && d.spec.entity_type_counts) || {};
            const LAYERS = [
                { key: 'logical', label: 'Logical', color: '#818cf8' },
                { key: 'physical', label: 'Physical', color: '#22d3ee' },
            ];
            children = LAYERS
                .map(L => ({ L, n: _KG_CLASSES.filter(c => c.layer === L.key)
                    .reduce((s, c) => s + c.types.reduce((ss, t) => ss + (counts[t] || 0), 0), 0) }))
                .filter(({ n }) => n > 0)
                .map(({ L, n }) => ({
                    entity_id: `kglayer:${p.kg_id}:${L.key}`,
                    name: L.label, type: 'kg_layer', rel: 'contains',
                    properties: { kind: 'kg_layer', accent: L.color, layer: L.key, count: n, _counts: counts, kg_id: p.kg_id, kg_name: node.name, owner_profile_id: p.owner_profile_id, owner_tag: p.owner_tag },
                    importance: 0.5,
                }));
        } else if (node.type === 'kg_layer') {
            // Drill a layer → its top-tier CLASSES (Databases/Entities/Attributes/Metrics
            // for logical; Schemas/Tables/Columns for physical), each with a full count.
            const p = node.properties || {};
            const counts = p._counts || {};
            children = _KG_CLASSES.filter(c => c.layer === p.layer)
                .map(c => ({ c, n: c.types.reduce((s, t) => s + (counts[t] || 0), 0) }))
                .filter(({ n }) => n > 0)
                .map(({ c, n }) => ({
                    entity_id: `kgclass:${p.kg_id}:${c.key}`,
                    name: c.label, type: 'kg_typegroup', rel: 'contains',
                    properties: { kind: 'kg_typegroup', accent: c.color, kg_types: c.types, count: n, layer: c.layer, kg_id: p.kg_id, kg_name: p.kg_name, owner_profile_id: p.owner_profile_id, owner_tag: p.owner_tag },
                    importance: 0.42,
                }));
        } else if (node.type === 'kg_typegroup') {
            // Drill a class → ALL its members (logical + physical realization), the full
            // count is on the parent so completeness is honest.
            const p = node.properties || {};
            const q = new URLSearchParams({ kg_id: p.kg_id, max_nodes: '200', types: (p.kg_types || []).join(',') });
            if (p.owner_profile_id) q.set('profile_id', p.owner_profile_id);
            const r = await fetch(`/api/v1/knowledge-graph/graph-spec?${q}`, auth);
            const d = await r.json();
            const kn = (d.spec && d.spec.nodes) || [];
            children = kn.slice(0, 200).map(e => {
                const m = _kgMeta(e.type);
                return {
                    entity_id: `concept:${p.kg_id}:${e.entity_id ?? e.id ?? e.name}`,
                    name: e.name, type: 'concept', rel: 'contains',
                    properties: { kind: 'concept', accent: m.color, kg_type: e.type, kg_type_label: m.label, kg_id: p.kg_id, owner_profile_id: p.owner_profile_id, owner_tag: p.owner_tag, raw: e },
                    importance: 0.3,
                };
            });
            children.sort((a, b) => String(a.name).localeCompare(String(b.name)));
        } else if (node.type === 'collection') {
            const p = node.properties || {};
            if (p.collection_id != null) {
                const r = await fetch(`/api/v1/knowledge/repositories/${p.collection_id}/documents`, auth);
                const d = await r.json();
                children = (d.documents || d.items || []).slice(0, 12).map((doc, i) => ({
                    entity_id: `doc:${p.collection_id}:${doc.id ?? doc.document_id ?? i}`,
                    name: doc.filename || doc.name || doc.title || `Document ${i + 1}`,
                    type: 'document', rel: 'contains',
                    properties: { kind: 'document', accent: '#0ea5e9', collection_id: p.collection_id, collection_name: node.name, repository_type: p.repository_type, raw: doc }, importance: 0.3,
                }));
            }
        } else if (node.type === 'memory') {
            const pid = (eid.startsWith('memory:') && eid !== 'memory:__global__') ? eid.slice('memory:'.length) : null;
            const q = new URLSearchParams(); if (pid) q.set('profile_id', pid);
            const r = await fetch(`/api/v1/memory?${q}`, auth);
            const d = await r.json();
            const items = d.items || d.memories || (Array.isArray(d) ? d : []);
            children = items.slice(0, 12).map((it, i) => ({
                entity_id: `memitem:${it.id ?? i}`,
                name: String(it.content || it.text || `Item ${i + 1}`).slice(0, 44),
                type: 'memory_item', rel: 'contains',
                properties: { kind: 'memory_item', accent: '#a855f7', scope: (node.properties || {}).scope, raw: it }, importance: 0.3,
            }));
        } else if (node.type === 'component') {
            // A component expands to ITS INSTANTIATIONS: dashboards/flows/canvases/tasks
            // for those; the agent's remembered items for Agent Memory (when activated).
            const p = node.properties || {};
            const cid = p.component_id;
            if (cid === 'memory') {
                const q = new URLSearchParams(); if (p.owner_profile_id) q.set('profile_id', p.owner_profile_id);
                const r = await fetch(`/api/v1/memory?${q}`, auth);
                const d = await r.json();
                const items = d.items || d.memories || (Array.isArray(d) ? d : []);
                children = items.slice(0, 40).map((it, i) => ({
                    entity_id: `memitem:${p.owner_profile_id || 'x'}:${it.id ?? i}`,
                    name: String(it.content || it.text || `Item ${i + 1}`).slice(0, 44),
                    type: 'memory_item', rel: 'instantiates',
                    properties: { kind: 'memory_item', accent: '#a855f7', scope: 'agent', raw: it }, importance: 0.3,
                }));
            } else {
                const SRC = {
                    dashboard: { url: '/api/v1/dashboards', key: 'dashboards', id: 'id', label: 'name' },
                    flow: { url: '/api/v1/flows', key: 'flows', id: 'flow_id', label: 'name' },
                    canvas: { url: '/api/v1/canvases', key: 'canvases', id: 'canvas_id', label: 'title' },
                    scheduler: { url: '/api/v1/scheduled-tasks', key: 'tasks', id: 'id', label: 'name' },
                }[cid];
                if (SRC) {
                    const r = await fetch(SRC.url, auth);
                    const d = await r.json();
                    const items = d[SRC.key] || d.items || (Array.isArray(d) ? d : []);
                    children = items.slice(0, 12).map((it, i) => ({
                        entity_id: `inst:${cid}:${it[SRC.id] ?? i}`,
                        name: it[SRC.label] || `${cid} ${i + 1}`, type: 'instance', rel: 'instantiates',
                        properties: { kind: 'instance', component_id: cid, instance_id: it[SRC.id], accent: p.accent || '#a78bfa', raw: it },
                        importance: 0.3,
                    }));
                }
            }
        }
    } catch (e) { children = []; }
    _childCache[eid] = children;
}

// Inject the children of every expanded node into the (already lensed) spec.
function _injectExpanded(spec) {
    if (!_expandedNodes.size) return spec;
    const nodes = spec.nodes.slice();
    const links = spec.links.slice();
    const idByEid = {}; nodes.forEach(n => { idByEid[n.entity_id] = n.id; });
    let nextId = nodes.length;
    _expandedNodes.forEach(eid => {
        const parent = nodes.find(n => n.entity_id === eid);
        if (!parent) return;
        // Injected children inherit the parent's department so they cluster WITH the
        // parent in the graph (e.g. a KG's concepts sit with the KG in Intelligence)
        // instead of falling into the "Other" region.
        const parentCat = ((parent.properties || {}).taxonomy || {}).category;
        _childrenOf(parent).forEach(ch => {
            let cid = idByEid[ch.entity_id];
            if (cid == null) {
                cid = nextId++;
                idByEid[ch.entity_id] = cid;
                const props = { ...(ch.properties || {}) };
                if (parentCat && !(props.taxonomy && props.taxonomy.category)) {
                    props.taxonomy = { category: parentCat, subcategory: (props.taxonomy || {}).subcategory || null };
                }
                nodes.push({ id: cid, entity_id: ch.entity_id, name: ch.name, type: ch.type,
                             properties: props, importance: ch.importance || 0.4 });
            }
            links.push({ source: parent.id, target: cid, type: ch.rel || 'has' });
        });
    });
    const colors = { ...spec.entity_type_colors, component: '#a78bfa', concept: '#f59e0b', document: '#0ea5e9', memory_item: '#a855f7', llm: '#8b5cf6', mcp_server: '#0ea5e9', skill: '#14b8a6', extension: '#eab308', agent_pack: '#ec4899', instance: '#22d3ee', kg_typegroup: '#64748b', kg_layer: '#818cf8', onto_group: '#a78bfa', platform_connector: '#06b6d4', vector_store: '#2dd4bf', kg_backend: '#34d399', context_window: '#c084fc' };
    const counts = {}; nodes.forEach(n => { counts[n.type] = (counts[n.type] || 0) + 1; });
    return { ...spec, nodes, links, entity_type_colors: colors, entity_type_counts: counts, shown_type_counts: counts };
}

async function _renderMap() {
    if (!_spec || !(_spec.nodes || []).length) return;
    const spec = _injectExpanded(_lensSpec(_spec, _lens));
    _renderedNodes = new Map(spec.nodes.map(n => [n.entity_id, n]));   // incl. injected children
    const mod = await import('/api/v1/components/knowledge_graph/renderer');
    // Keep the focus identical across expand/collapse + lens changes (no layout jump).
    if (mod.preserveKGView) mod.preserveKGView();
    mod.renderKGFull(GRAPH_ID, spec);
    // The render is deferred (ResizeObserver debounce), so the active focus is re-applied
    // by the 'kg:rendered' listener once _graphState is actually live — not here.
}

async function _toggleExpand(node) {
    const eid = node.entity_id;
    if (_expandedNodes.has(eid)) {
        _expandedNodes.delete(eid);
    } else {
        await _ensureChildren(node);                 // fetch (KG/collection/memory) if needed
        if (!_childrenOf(node).length) { _openInspect(node); return; }  // nothing to expand → show details
        _expandedNodes.add(eid);
    }
    _renderMap();                  // graph: inject / remove the children in place
    _syncTreeExpansion(node);      // hierarchy panel: mirror the same expansion
}

// Drillable instance-bearing components (REST instances + a drill-down). And the
// broader set that has countable CONTENT (adds KG + memory, counted from the estate) —
// chart is the only genuinely ephemeral one (no content). Counted = gets a colour+count
// when populated; instance = also gets a caret to drill in.
const _INSTANCE_COMPS = ['dashboard', 'flow', 'canvas', 'scheduler'];
const _COUNTED_COMPS = ['dashboard', 'flow', 'canvas', 'scheduler', 'knowledge_graph', 'memory'];
function _isInstanceComp(n) { return n.type === 'component' && _INSTANCE_COMPS.includes((n.properties || {}).component_id); }
function _isCountedComp(n) { return n.type === 'component' && _COUNTED_COMPS.includes((n.properties || {}).component_id); }
// A component is drillable to its instantiations when it has REST instances. (Per-agent
// Agent Memory doesn't drill here — clicking it navigates to the agent's memory NODE,
// which is itself drillable to its items; see _resolveNode.)
function _compDrillable(n) {
    const p = n.properties || {};
    if (n.type !== 'component') return false;
    // Agent Memory (when activated) drills to its Local + Global sub-nodes — imperative.
    if (p.component_id === 'memory') return p.enabled !== false;
    return _INSTANCE_COMPS.includes(p.component_id);
}
// The agent's OWN content count for a per-agent KG/Memory component (estate counts are
// wrong here — a KG owned by another profile, or global memory, isn't this agent's).
function _agentContentCount(n) {
    const p = (n && n.properties) || {};
    if (n.type !== 'component' || !p.owner_profile_id) return null;
    if (p.component_id === 'knowledge_graph') {
        return (_spec.nodes || []).filter(x => x.type === 'knowledge_graph'
            && (x.properties || {}).owner_profile_id === p.owner_profile_id).length;
    }
    if (p.component_id === 'memory') {
        const mem = _findNode('memory:' + p.owner_profile_id);
        return mem ? ((mem.properties || {}).items || 0) : 0;
    }
    return null;
}
// A per-agent Agent Memory / Knowledge Graph component resolves to the agent's actual
// memory or KG NODE so a click takes you to it, not the capability tile.
function _resolveNode(n) {
    const p = (n && n.properties) || {};
    if (n && n.type === 'component' && p.owner_profile_id) {
        // Agent Memory is NOT resolved to a single node — it EXPANDS to Local + Global
        // (see _childrenOf), so clicking it drills rather than jumping.
        if (p.component_id === 'knowledge_graph') {
            const kg = (_spec.nodes || []).find(x => x.type === 'knowledge_graph'
                && (x.properties || {}).owner_profile_id === p.owner_profile_id);
            if (kg) return _findNode(kg.entity_id) || kg;
        }
    }
    return n;
}

// A tree child is drillable when it has a deeper layer (instance-bearing component →
// instances, KG → concepts, collection → docs, memory → items).
function _childExpandable(ch) {
    if (ch.type === 'component') return _compDrillable(ch);
    if (ch.type === 'onto_group') { const q = ch.properties || {}; return (q._count != null ? q._count : (q._subgroups || q._members || []).length) > 0; }
    return ['knowledge_graph', 'collection', 'memory', 'kg_layer', 'kg_typegroup'].includes(ch.type);
}
// Dot + label (+ count for instance-bearing components). A component dot is its REAL
// colour only when it has instantiations (count > 0), else grey; instance-bearing
// components carry a count badge filled async by _applyCompCounts. Non-instance
// components (chart/KG/memory) have no instantiations → grey, no count.
function _leafDotAndCount(n) {
    const p = n.properties || {};
    if (n.type === 'component') {
        const cid = p.component_id;
        // Dot colour is driven by ACTIVATION, not instance count: an activated component
        // always shows its real accent; only a deactivated/blocked one is grey. The count
        // badge is a separate, supplementary signal (how many instances/items exist).
        const on = p.enabled !== false;
        const dot = on ? (p.accent || TYPE_COLORS.component) : '#64748b';
        const lbl = `<span class="leaf-dot" style="background:${dot}"></span><span class="leaf-label">${esc(n.name)}</span>`;
        // Per-agent KG/Memory: show the AGENT's own content count (estate counts mislead —
        // a KG owned by another profile, or global memory, isn't this agent's).
        if ((cid === 'knowledge_graph' || cid === 'memory') && p.owner_profile_id) {
            const cnt = _agentContentCount(n);
            return lbl + (cnt > 0 ? `<span class="tree-count">${cnt}</span>` : '');
        }
        if (_isCountedComp(n)) return lbl + `<span class="tree-count" data-comp-count="${esc(cid)}">·</span>`;
        return lbl;
    }
    if (n.type === 'onto_group') {   // Components / User Interface / Connectors grouping folder
        const cnt = (p._count != null) ? p._count : (p._subgroups || p._members || []).length;
        const dot = cnt > 0 ? (p.accent || TYPE_COLORS.component) : '#64748b';
        return `<span class="leaf-dot" style="background:${dot}"></span><span class="leaf-label">${esc(n.name)}</span>`
            + (cnt > 0 ? `<span class="tree-count">${cnt}</span>` : '');
    }
    if (n.type === 'kg_layer' || n.type === 'kg_typegroup') {   // a KG layer / class folder: dot + name + full count
        const col = p.accent || '#94a3b8';
        return `<span class="leaf-dot" style="background:${col}"></span><span class="leaf-label">${esc(n.name)}</span>`
            + (p.count != null ? `<span class="tree-count">${p.count}</span>` : '');
    }
    const col = p.accent || TYPE_COLORS[n.type] || '#94a3b8';
    const label = (String(n.type).startsWith('agent') ? '@' : '') + esc(p.tag || n.name);
    return `<span class="leaf-dot" style="background:${col}"></span><span class="leaf-label">${label}</span>`;
}
function _childRowHtml(ch) {
    const exp = _childExpandable(ch);
    const p = ch.properties || {};
    // Activated components are full-strength; available-but-not-activated are dimmed;
    // governance-blocked are dimmer still — so the two states read at a glance.
    let dim = '';
    if (ch.type === 'component') {
        if (!p.activated && p.available) dim = ' style="opacity:0.6"';
        else if (!p.available) dim = ' style="opacity:0.4"';
    }
    return `<div class="tree-subleaf${exp ? ' tsl-exp' : ''}"${dim} data-eid="${esc(ch.entity_id)}" title="${esc(ch.name)}">`
        + (exp ? `<span class="leaf-caret collapsed">▾</span>` : `<span class="leaf-caret-spacer"></span>`)
        + _leafDotAndCount(ch) + `</div>`;
}
function _wireChildRow(row) {
    if (row.__owired) return; row.__owired = true;
    const eid = row.getAttribute('data-eid');
    const caret = row.querySelector('.leaf-caret');
    if (caret) caret.addEventListener('click', (e) => { e.stopPropagation(); const n = _findNode(eid); if (n) _toggleExpand(n); });
    row.addEventListener('click', (e) => {
        e.stopPropagation();
        const n0 = _findNode(eid);
        const n = _resolveNode(n0);                 // memory component → the agent's memory node
        _focusGraph(n ? _focusSetFor(n) : [eid], eid);
        if (n) { _openInspect(n); if (n === n0 && row.classList.contains('tsl-exp')) _toggleExpand(n); }
    });
}

// Mirror a node's graph expansion into the taxonomy tree (works at ANY depth — leaf
// or subleaf), surgically so the rest of the tree's open/closed state is preserved.
function _syncTreeExpansion(node) {
    const host = document.getElementById('ontology-tree');
    if (!host) return;
    const el = host.querySelector(`[data-eid="${CSS.escape(node.entity_id)}"]`);
    if (!el) return;
    const expanded = _expandedNodes.has(node.entity_id);
    el.classList.toggle('expanded', expanded);
    const caret = el.querySelector('.leaf-caret');
    if (caret) caret.classList.toggle('collapsed', !expanded);
    let body = el.nextElementSibling;
    const isBody = body && body.getAttribute && body.getAttribute('data-tbody') === node.entity_id;
    if (expanded) {
        if (!isBody) {
            body = document.createElement('div');
            body.setAttribute('data-tbody', node.entity_id);
            body.style.paddingLeft = '0.85rem';
            body.innerHTML = _childrenOf(node).map(_childRowHtml).join('');
            el.after(body);
            body.querySelectorAll('[data-eid]').forEach(_wireChildRow);
        } else { body.style.display = ''; }
    } else if (isBody) {
        body.remove();
    }
    _fillComponentCounts();   // colour + count any component subleaves just revealed
}

// Re-encode the segment for a lens (the renderer colours by node.type + sizes by
// importance, so each lens remaps type→meaning + a fresh colour map). Anomaly-first:
// the Trust lens foregrounds ungrounded agents. Links are preserved (index-keyed).
function _lensSpec(spec, lens) {
    const colors = {};
    const reg = (t, c) => { colors[t] = c; return t; };
    const expert = new Set();
    if (lens === 'coordination') {
        (spec.links || []).forEach(l => { if (l.type === 'coordinates') expert.add(typeof l.target === 'object' ? l.target.id : l.target); });
    }
    const MUTE = '#475569';
    const STRUCT = { segment: '#cbd5e1', class_group: '#64748b' };
    const nodes = (spec.nodes || []).map(n => {
        const p = n.properties || {};
        const base = n.type || '';
        // Taxonomy spine nodes (root + class groups) are structural — preserve them
        // across every lens so the hierarchy stays intact.
        if (STRUCT[base]) {
            reg(base, STRUCT[base]);
            return { ...n, importance: base === 'segment' ? 1.9 : 1.2, properties: p };
        }
        if (lens === 'overview') {
            // Native IFOC + type colours — the platform's identity view (the default).
            reg(base, TYPE_COLORS[base] || '#94a3b8');
            return { ...n, properties: p };
        }
        const isAgent = base.startsWith('agent');
        let type = base, imp = n.importance;
        if (lens === 'trust') {
            if (isAgent) {
                const st = p.config_state || (p.grounded ? 'grounded' : 'unconfigured');
                if (st === 'grounded') { type = reg('grounded', '#10b981'); imp = 1.0; }
                else if (st === 'ungrounded') { type = reg('ungrounded', '#f59e0b'); imp = 1.3; }
                else { type = reg('not configured', '#64748b'); imp = 0.55; }   // muted, not an alarm
            }
            else if (base === 'knowledge_graph') { type = (String(p.conformance_status || '').toLowerCase() === 'conformant') ? reg('conformant', '#10b981') : reg('unrated', '#f59e0b'); imp = 0.6; }
            else { type = reg('knowledge', MUTE); imp = 0.3; }
        } else if (lens === 'knowledge') {
            if (base === 'knowledge_graph') { type = reg('knowledge_graph', '#10b981'); imp = 0.85; }
            else if (base === 'collection') { type = reg('knowledge repo', '#38bdf8'); imp = 0.65; }
            else if (base === 'collection_planner') { type = reg('planner repo', '#818cf8'); imp = 0.6; }
            else if (base === 'memory') { type = reg('memory', '#a855f7'); imp = 0.4; }
            else { type = reg('agent', MUTE); imp = 0.4; }
        } else if (lens === 'risk') {
            if (base === 'collection' || base === 'collection_planner' || base === 'knowledge_graph') {
                const users = new Set(p.used_by || []).size;
                type = users > 1 ? reg('shared', '#ef4444') : reg('solo', '#64748b');
                imp = users > 1 ? Math.min(1.7, 0.6 + 0.28 * users) : 0.4;
            } else if (isAgent) { type = reg('agent', MUTE); imp = 0.5; }
            else { type = reg('memory', '#64748b'); imp = 0.3; }
        } else if (lens === 'coordination') {
            if (base === 'agent_coordinate') { type = reg('coordinator', '#F15F22'); imp = 1.45; }
            else if (isAgent && expert.has(n.id)) { type = reg('expert', '#3b82f6'); imp = 0.95; }
            else if (isAgent) { type = reg('agent', MUTE); imp = 0.4; }
            else { type = reg('knowledge', MUTE); imp = 0.3; }
        }
        return { ...n, type, importance: imp, properties: p };
    });
    const counts = {};
    nodes.forEach(n => { counts[n.type] = (counts[n.type] || 0) + 1; });
    return {
        ...spec, nodes, links: spec.links, entity_type_colors: colors,
        entity_type_counts: counts, shown_type_counts: counts, _lens: lens,
    };
}

// ── Matrix: agent × knowledge (who's grounded on what; shared columns flagged) ──
// Estate Dependencies — value-first: headline risk + a source-centric gallery, sliceable
// by CATEGORY (Knowledge · Tools · Model) and RISK band. Concentration is felt, not
// decoded; every agent chip is clickable; full tooltips on anything truncated.
function _renderMatrix() {
    const host = document.getElementById('ontology-matrix');
    if (!host || !_spec) return;
    _injectScoreboardCss();   // the .sb-pill filter-pile styles are shared with the Trust view
    const nodes = _spec.nodes || [];
    const byId = new Map(nodes.map(n => [n.id, n]));
    const agents = nodes.filter(n => (n.type || '').startsWith('agent') && (n.properties || {}).profile_id);
    const agentById = new Map(agents.map(a => [a.id, a]));
    const stateOf = (a) => (a.properties || {}).config_state || ((a.properties || {}).grounded ? 'grounded' : 'unconfigured');
    if (!agents.length) {
        host.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-muted);text-align:center;padding:2rem">No agents yet.</div>`;
        return;
    }
    const ALERT = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.3 3.9 1.8 18a1.5 1.5 0 0 0 1.3 2.2h17.8a1.5 1.5 0 0 0 1.3-2.2L13.7 3.9a1.5 1.5 0 0 0-2.6 0z"/><path d="M12 9v4"/><circle cx="12" cy="17" r=".6" fill="currentColor"/></svg>';
    const OK = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';

    const dep = {};
    const add = (aid, cid) => { (dep[aid] = dep[aid] || new Set()).add(cid); };
    (_spec.links || []).forEach(l => {
        const s = typeof l.source === 'object' ? l.source.id : l.source;
        const t = typeof l.target === 'object' ? l.target.id : l.target;
        if (l.type === 'grounds_on' || l.type === 'plans_with') add(s, t);
        else if (l.type === 'connected_by') add(t, s);
        else if (l.type === 'uses_model') add(t, s);
    });

    // Categories of the things agents stand on.
    const KIND = { collection: 'knowledge', collection_planner: 'knowledge', knowledge_graph: 'knowledge', mcp_server: 'tools', llm: 'model' };
    const CAT_LABEL = { knowledge: 'Knowledge', tools: 'Tools', model: 'Model' };
    const catColor = { knowledge: '#60a5fa', tools: '#22d3ee', model: '#a78bfa' };
    const src = new Map();
    agents.forEach(a => (dep[a.id] || new Set()).forEach(cid => {
        const node = byId.get(cid); if (!node) return; const cat = KIND[node.type]; if (!cat) return;
        let s = src.get(cid); if (!s) { s = { id: cid, name: node.name, cat, agents: [] }; src.set(cid, s); }
        s.agents.push(a.id);
    }));
    const sources = [...src.values()].sort((a, b) => b.agents.length - a.agents.length || a.name.localeCompare(b.name));
    const total = agents.length;
    const configuredCount = agents.filter(a => stateOf(a) === 'grounded').length;
    const noDep = agents.filter(a => !(dep[a.id] && [...dep[a.id]].some(c => KIND[(byId.get(c) || {}).type])));
    const spofThreshold = Math.max(3, Math.ceil(total * 0.34));

    // ── filter piles (reuse the Trust view's pill component): Category · Risk ──
    const catPiles = [{ id: 'all', label: 'All', color: '#64748b', n: sources.length }].concat(
        ['knowledge', 'tools', 'model'].map(k => ({ id: k, label: CAT_LABEL[k], color: catColor[k], n: sources.filter(s => s.cat === k).length })).filter(p => p.n));
    const spofN = sources.filter(s => s.agents.length >= spofThreshold).length;
    const riskPiles = [{ id: 'all', label: 'All', color: '#64748b', n: sources.length }];
    if (spofN) riskPiles.push({ id: 'spof', label: 'Single point of failure', color: '#ef4444', n: spofN });
    riskPiles.push({ id: 'shared', label: 'Shared', color: '#f59e0b', n: sources.filter(s => s.agents.length > 1).length });
    riskPiles.push({ id: 'solo', label: 'Single-agent', color: '#10b981', n: sources.filter(s => s.agents.length === 1).length });
    const pill = (p, group, active) => `<button class="sb-pill${active ? ' on' : ''}" data-${group}="${p.id}" style="${active ? `background:${p.color};` : `color:${p.color === '#64748b' ? 'var(--text-muted)' : p.color};border-color:${p.color}44;`}">${esc(p.label)}${p.n != null ? `<span class="sb-n">${p.n}</span>` : ''}</button>`;

    let gs = sources;
    if (_dgKind !== 'all') gs = gs.filter(s => s.cat === _dgKind);
    if (_dgRisk === 'spof') gs = gs.filter(s => s.agents.length >= spofThreshold);
    else if (_dgRisk === 'shared') gs = gs.filter(s => s.agents.length > 1);
    else if (_dgRisk === 'solo') gs = gs.filter(s => s.agents.length === 1);

    const chip = (aid, dim) => {
        const a = agentById.get(aid); if (!a) return '';
        const p = a.properties || {}; const cls = _classOf(a); const col = _IFOC[cls] ? _IFOC[cls].color : '#94a3b8';
        const tag = p.tag || a.name;
        return `<button class="dg-chip${dim ? ' dim' : ''}" data-aid="${esc(a.id)}" title="@${esc(tag)}${_IFOC[cls] ? ' · ' + _IFOC[cls].label + ' agent' : ''}${dim ? ' — not configured (no knowledge, tools or model bound)' : ''}"><span class="dg-dot" style="background:${col}"></span>@${esc(tag)}</button>`;
    };

    let html = `<div class="dg-wrap">`;

    // 1) Headline — the single most valuable insight, in plain language.
    if (sources.length) {
        const top = sources[0]; const pct = Math.round(100 * top.agents.length / total);
        const risky = top.agents.length >= spofThreshold;
        html += `<div class="dg-headline${risky ? ' risk' : ''}">
            <span class="dg-hl-ico">${risky ? ALERT : OK}</span>
            <div class="dg-hl-body">
                <div class="dg-hl-main">${risky ? `Biggest single point of failure: <b>${esc(top.name)}</b>` : `Most-relied-on source: <b>${esc(top.name)}</b>`}</div>
                <div class="dg-hl-sub">${top.agents.length} of ${total} agents (${pct}%) depend on it${risky ? ' — a failure here takes most of your estate down. Consider diversifying.' : '.'}</div>
            </div>
            <div class="dg-hl-stats">
                <span><b>${configuredCount}</b>/${total} configured</span>
                <span><b>${sources.length}</b> sources</span>
            </div>
        </div>`;
    }

    // 2) Filter piles — Category · Risk.
    html += `<div class="sb-filters" style="margin:0 0 1.1rem">
        <div class="sb-pills"><span class="sb-plabel">Category</span>${catPiles.map(p => pill(p, 'dgk', _dgKind === p.id)).join('')}</div>
        <div class="sb-pills"><span class="sb-plabel">Risk</span>${riskPiles.map(p => pill(p, 'dgr', _dgRisk === p.id)).join('')}</div>
    </div>`;

    // 3) Dependency gallery — one card per source (filtered), biggest reach first.
    if (gs.length) {
        const maxN = sources.length ? sources[0].agents.length : 1;
        html += `<div class="dg-grid">`;
        gs.forEach(s => {
            const n = s.agents.length; const w = Math.round(100 * n / maxN); const spof = n >= spofThreshold;
            html += `<div class="dg-card${spof ? ' spof' : ''}">
                <div class="dg-card-hd">
                    <span class="dg-kind" style="background:${catColor[s.cat]}" title="${CAT_LABEL[s.cat]}"></span>
                    <span class="dg-name" title="${esc(s.name)} · ${CAT_LABEL[s.cat]}">${esc(s.name)}</span>
                    <span class="dg-count" title="${n} agent${n === 1 ? '' : 's'} depend on this">${n}</span>
                </div>
                <div class="dg-bar"><span style="width:${w}%;background:${spof ? '#ef4444' : (n > 1 ? '#f59e0b' : '#10b981')}"></span></div>
                ${spof ? `<div class="dg-spof-tag">${ALERT}Single point of failure</div>` : ''}
                <div class="dg-chips">${s.agents.map(id => chip(id)).join('')}</div>
            </div>`;
        });
        html += `</div>`;
    } else {
        html += `<div class="dg-empty">No sources match this filter.</div>`;
    }

    // 4) Honest gap — unconfigured agents.
    if (noDep.length) {
        html += `<div class="dg-unconf"><span class="dg-unconf-hd">Not configured · ${noDep.length}</span>${noDep.map(a => chip(a.id, true)).join('')}</div>`;
    }

    html += `</div>`;
    host.innerHTML = html;

    // Wiring — pills re-render; agent chips open their inspect panel.
    const setF = (g, id) => {
        if (g === 'dgk') { _dgKind = id; try { localStorage.setItem('uderia.onto.dgKind', id); } catch (_) {} }
        else { _dgRisk = id; try { localStorage.setItem('uderia.onto.dgRisk', id); } catch (_) {} }
        _renderMatrix();
    };
    host.querySelectorAll('[data-dgk]').forEach(b => b.addEventListener('click', () => setF('dgk', b.dataset.dgk)));
    host.querySelectorAll('[data-dgr]').forEach(b => b.addEventListener('click', () => setF('dgr', b.dataset.dgr)));
    host.querySelectorAll('.dg-chip[data-aid]:not(.dim)').forEach(b => b.addEventListener('click', () => {
        const a = agentById.get(b.dataset.aid); if (a) _openInspect(a);
    }));
}


// ── Trust scoreboard: anomaly-first — what to trust, what to fix ────────
// Scoreboard filter state (the "piles"): trust state × IFOC class. Persisted per browser.
let _sbTrust = (() => { try { return localStorage.getItem('uderia.onto.sbTrust') || 'all'; } catch (_) { return 'all'; } })();
let _sbClass = (() => { try { return localStorage.getItem('uderia.onto.sbClass') || 'all'; } catch (_) { return 'all'; } })();
let _sbConfig = (() => { try { return localStorage.getItem('uderia.onto.sbConfig') || 'all'; } catch (_) { return 'all'; } })();
let _dgKind = (() => { try { return localStorage.getItem('uderia.onto.dgKind') || 'all'; } catch (_) { return 'all'; } })();
let _dgRisk = (() => { try { return localStorage.getItem('uderia.onto.dgRisk') || 'all'; } catch (_) { return 'all'; } })();
const _IFOC = { ideate: { label: 'Ideate', color: '#4ade80' }, focus: { label: 'Focus', color: '#3b82f6' }, optimize: { label: 'Optimize', color: '#9333ea' }, coordinate: { label: 'Coordinate', color: '#F15F22' } };
const _classOf = (a) => {
    const t = a.type || '';
    if (t.startsWith('agent_')) { const s = t.slice(6); return _IFOC[s] ? s : 'ideate'; }
    const ic = String((a.properties || {}).ifoc_class || '').toLowerCase();
    return ['ideate', 'focus', 'optimize', 'coordinate'].find(c => ic.includes(c)) || 'ideate';
};

function _injectScoreboardCss() {
    if (document.getElementById('onto-sb-css')) return;
    const el = document.createElement('style'); el.id = 'onto-sb-css';
    el.textContent = `
    .sb-filters{display:flex;flex-direction:column;gap:0.4rem;margin:0.3rem 0 0.7rem}
    .sb-pills{display:flex;flex-wrap:wrap;gap:0.35rem;align-items:center}
    .sb-plabel{font-size:0.66rem;text-transform:uppercase;letter-spacing:0.05em;color:var(--text-muted);min-width:44px}
    .sb-pill{display:inline-flex;align-items:center;gap:0.34rem;font-size:0.77rem;font-weight:600;padding:0.24rem 0.6rem;
      border-radius:999px;border:1px solid var(--border-secondary,#334155);background:transparent;color:var(--text-muted,#94a3b8);
      cursor:pointer;transition:background .13s ease,color .13s ease,border-color .13s ease;}
    .sb-pill:hover{color:var(--text-primary);}
    .sb-pill:focus-visible{outline:2px solid currentColor;outline-offset:2px;}
    .sb-pill.on{color:#fff;border-color:transparent;}
    .sb-pill .sb-n{font-size:0.68rem;padding:0.02rem 0.34rem;border-radius:999px;background:color-mix(in srgb,currentColor 16%,transparent);}
    .sb-pill.on .sb-n{background:rgba(255,255,255,0.22);}
    .sb-roster{max-height:min(46vh,440px);overflow:auto;border:1px solid var(--border-secondary,#293548);border-radius:11px;padding:0.4rem 0.5rem;}
    .sb-count{font-size:0.74rem;color:var(--text-muted);margin:0 0 0.4rem 0.15rem;}
    .sb-empty{color:var(--text-muted);font-size:0.86rem;padding:1.4rem;text-align:center;}
    `;
    document.head.appendChild(el);
}

async function _renderScoreboard() {
    const host = document.getElementById('ontology-scoreboard');
    if (!host || !_spec) return;
    _injectScoreboardCss();
    const nodes = _spec.nodes || [];
    const agents = nodes.filter(n => (n.type || '').startsWith('agent'));
    const stateOf = (a) => (a.properties || {}).config_state || ((a.properties || {}).grounded ? 'grounded' : 'unconfigured');
    const configured = agents.filter(a => stateOf(a) === 'grounded');
    const kgs = nodes.filter(n => n.type === 'knowledge_graph');
    const conformant = kgs.filter(k => String((k.properties || {}).conformance_status || '').toLowerCase() === 'conformant');
    const spofs = (_spec.findings || []).filter(f => f.id && f.id.startsWith('shared_'));
    const pct = agents.length ? Math.round(100 * configured.length / agents.length) : 0;

    // Trust Membrane — compositional agent badges (one batch call, cached).
    const tapi = window.TrustBadge;
    const tmap = new Map();
    if (tapi) { try { const all = await tapi.fetchAll(); (all.agents || []).forEach(a => tmap.set(a.profile_id, a)); } catch (_) {} }
    // Trust is per real agent (a profile). Exclude agent_pack bundle nodes.
    const trustAgents = agents.filter(a => (a.properties || {}).profile_id);
    const trustOf = (a) => (tmap.get((a.properties || {}).profile_id) || {}).state || 'unsigned';
    const trusted = trustAgents.filter(a => trustOf(a) === 'trusted');
    const changed = trustAgents.filter(a => trustOf(a) === 'changed');
    const needsSign = trustAgents.filter(a => trustOf(a) !== 'trusted');
    const tpct = trustAgents.length ? Math.round(100 * trusted.length / trustAgents.length) : 0;
    // Config over the SAME real-agent set as trust (aligned denominators — excludes packs).
    const configuredT = trustAgents.filter(a => stateOf(a) === 'grounded');
    const unconfiguredT = trustAgents.filter(a => stateOf(a) !== 'grounded');
    const cpct = trustAgents.length ? Math.round(100 * configuredT.length / trustAgents.length) : 0;

    const stat = (num, color, label, sub) => `<div class="onto-stat"><div class="s-num" style="color:${color}">${num}</div><div class="s-label">${label}</div>${sub ? `<div class="s-sub">${sub}</div>` : ''}</div>`;

    // ── filter piles (compartment + agent-class) ────────────────────────────
    const trustPiles = [
        { id: 'all', label: 'All', n: trustAgents.length, color: '#64748b' },
        { id: 'needs', label: 'Needs signing', n: needsSign.length, color: '#F15F22' },
        { id: 'trusted', label: 'Trusted', n: trusted.length, color: '#10b981' },
    ];
    if (changed.length) trustPiles.push({ id: 'changed', label: 'Changed', n: changed.length, color: '#f59e0b' });
    const classCount = (c) => trustAgents.filter(a => _classOf(a) === c).length;
    const classPiles = [{ id: 'all', label: 'All', color: '#64748b' }].concat(
        Object.keys(_IFOC).map(c => ({ id: c, label: _IFOC[c].label, color: _IFOC[c].color, n: classCount(c) })).filter(p => p.n)
    );
    const configPiles = [{ id: 'all', label: 'All', n: trustAgents.length, color: '#64748b' },
        { id: 'configured', label: 'Configured', n: configuredT.length, color: '#10b981' }];
    if (unconfiguredT.length) configPiles.push({ id: 'unconfigured', label: 'Not configured', n: unconfiguredT.length, color: '#94a3b8' });
    const pill = (p, group, active) => `<button class="sb-pill${active ? ' on' : ''}" data-${group}="${p.id}"
        style="${active ? `background:${p.color};` : `color:${p.color === '#64748b' ? 'var(--text-muted)' : p.color};border-color:${p.color}44;`}">${esc(p.label)}${p.n != null ? `<span class="sb-n">${p.n}</span>` : ''}</button>`;

    // ── apply filters ───────────────────────────────────────────────────────
    let list = trustAgents;
    if (_sbTrust === 'needs') list = list.filter(a => trustOf(a) !== 'trusted');
    else if (_sbTrust === 'trusted') list = list.filter(a => trustOf(a) === 'trusted');
    else if (_sbTrust === 'changed') list = list.filter(a => trustOf(a) === 'changed');
    if (_sbClass !== 'all') list = list.filter(a => _classOf(a) === _sbClass);
    if (_sbConfig === 'configured') list = list.filter(a => stateOf(a) === 'grounded');
    else if (_sbConfig === 'unconfigured') list = list.filter(a => stateOf(a) !== 'grounded');

    let html = `<div class="onto-board">
        <div class="onto-board-cards">
            ${tapi ? stat(`${tpct}%`, tpct >= 80 ? '#10b981' : (trusted.length ? '#f59e0b' : '#64748b'), 'Agents trusted', `${trusted.length} of ${trustAgents.length} signed`) : ''}
            ${stat(`${cpct}%`, cpct >= 80 ? '#10b981' : (cpct >= 50 ? '#f59e0b' : '#64748b'), 'Agents configured', `${configuredT.length} of ${trustAgents.length} set up`)}
            ${stat(`${conformant.length}/${kgs.length || 0}`, (kgs.length && conformant.length === kgs.length) ? '#10b981' : '#f59e0b', 'Conformant graphs', kgs.length ? 'governed & validated' : 'no graphs yet')}
            ${stat(spofs.length, spofs.length ? '#f59e0b' : '#10b981', 'Single points of failure', 'in Insights')}
        </div>
        <div class="sb-filters">
            <div class="sb-pills"><span class="sb-plabel">Trust</span>${trustPiles.map(p => pill(p, 'sbt', _sbTrust === p.id)).join('')}</div>
            <div class="sb-pills"><span class="sb-plabel">Class</span>${classPiles.map(p => pill(p, 'sbc', _sbClass === p.id)).join('')}</div>
            <div class="sb-pills"><span class="sb-plabel">Config</span>${configPiles.map(p => pill(p, 'sbcfg', _sbConfig === p.id)).join('')}</div>
        </div>
        <div class="sb-count">${list.length} agent${list.length === 1 ? '' : 's'}${_sbClass !== 'all' ? ' · ' + _IFOC[_sbClass].label : ''}${_sbTrust !== 'all' ? ' · ' + trustPiles.find(p => p.id === _sbTrust).label.toLowerCase() : ''}${_sbConfig !== 'all' ? ' · ' + (_sbConfig === 'configured' ? 'configured' : 'not configured') : ''}</div>
        <div class="sb-roster">`;

    if (!list.length) {
        html += `<div class="sb-empty">No agents match this filter.</div>`;
    } else {
        html += list.map(a => {
            const p = a.properties || {};
            const dot = _IFOC[_classOf(a)] ? _IFOC[_classOf(a)].color : (TYPE_COLORS[a.type] || '#94a3b8');
            const knows = (p.grounded_on || []).length ? 'grounded on ' + esc((p.grounded_on || []).slice(0, 3).join(', ')) : 'not configured yet';
            const cell = tapi
                ? `<span class="onto-trust-cell" data-pid="${esc(p.profile_id)}" data-tag="${esc(p.tag || a.name)}" style="cursor:pointer">${tapi.badgeHTML(trustOf(a))}</span>`
                : `<span class="ins-chip" style="border-color:#64748b;color:#64748b">n/a</span>`;
            return `<div class="onto-agent-row" style="margin-bottom:0.4rem">
                <div class="ar-main"><span class="ar-dot" style="background:${dot}"></span><span class="ar-tag">@${esc(p.tag || a.name)}</span><span class="ar-knows">${knows}</span></div>
                ${cell}
            </div>`;
        }).join('');
    }
    html += `</div></div>`;
    host.innerHTML = html;

    // ── wiring ──────────────────────────────────────────────────────────────
    const setFilter = (group, id) => {
        if (group === 'sbt') { _sbTrust = id; try { localStorage.setItem('uderia.onto.sbTrust', id); } catch (_) {} }
        else if (group === 'sbc') { _sbClass = id; try { localStorage.setItem('uderia.onto.sbClass', id); } catch (_) {} }
        else { _sbConfig = id; try { localStorage.setItem('uderia.onto.sbConfig', id); } catch (_) {} }
        _renderScoreboard();
    };
    host.querySelectorAll('[data-sbt]').forEach(b => b.addEventListener('click', () => setFilter('sbt', b.dataset.sbt)));
    host.querySelectorAll('[data-sbc]').forEach(b => b.addEventListener('click', () => setFilter('sbc', b.dataset.sbc)));
    host.querySelectorAll('[data-sbcfg]').forEach(b => b.addEventListener('click', () => setFilter('sbcfg', b.dataset.sbcfg)));
    host.querySelectorAll('[data-ask]').forEach(b => b.addEventListener('click', () => _askAgent(b.getAttribute('data-ask'))));
    if (tapi) {
        host.querySelectorAll('.onto-trust-cell').forEach(c => c.addEventListener('click', () => tapi.openDetail(c.dataset.pid, c.dataset.tag)));
        if (!window.__ontoTrustListener) {
            window.__ontoTrustListener = true;
            UderiaBus.on('uderia:trust-changed', () => {
                const sb = document.getElementById('ontology-scoreboard');
                if (sb && sb.offsetParent !== null) _renderScoreboard();
            });
        }
    }
}

// ── Split Explorer: taxonomy tree navigator (left), linked to the graph ──
const TREE_CAT_ORDER = ['Agents', 'Memory', 'Sessions', 'Intelligence', 'Components', 'Anatomy', 'Other'];

// Progressive Year ▸ Month ▸ Day nesting for sessions. Reuses the .tree-sub caret
// wiring (which toggles its matching [data-sub-body] at any depth) + the .tree-leaf
// click handler. Only buckets that actually contain sessions are rendered; everything
// is collapsed by default; newest period first.
const _MONTHS = ['', 'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December'];
function _renderSessionDateTree(nodes) {
    const grp = {};
    nodes.forEach(n => {
        const d = String((n.properties || {}).last_updated || (n.properties || {}).created_at || '').slice(0, 10);
        const [y, m, dd] = (/^\d{4}-\d{2}-\d{2}$/.test(d)) ? d.split('-') : ['Undated', '', ''];
        (((grp[y] = grp[y] || {})[m] = grp[y][m] || {})[dd] = grp[y][m][dd] || []).push(n);
    });
    const descKeys = (o) => Object.keys(o).sort((a, b) => b.localeCompare(a));
    const count = (o) => Object.values(o).reduce((a, v) => a + (Array.isArray(v) ? v.length : count(v)), 0);
    // Indents are absolute (the base .tree-sub already pads 1.3rem, .tree-leaf 2.3rem),
    // so each level sets an explicit, strictly-increasing padding-left.
    const leaf = (n) => {
        const col = TYPE_COLORS[n.type] || '#94a3b8';
        return `<div class="tree-leaf" data-eid="${esc(n.entity_id)}" title="${esc(n.name)}" style="padding-left:4.3rem">`
            + `<span class="leaf-caret-spacer"></span><span class="leaf-dot" style="background:${col}"></span>`
            + `<span class="leaf-label">${esc(n.name)}</span></div>`;
    };
    let html = '';
    descKeys(grp).forEach(y => {
        const yId = `Sessions/${y}`;
        html += `<div class="tree-sub collapsed" data-sub="${esc(yId)}" style="padding-left:1.3rem"><span class="caret">▾</span><span>${esc(y)}</span><span class="tree-count">${count(grp[y])}</span></div>`;
        html += `<div data-sub-body="${esc(yId)}" style="display:none">`;
        descKeys(grp[y]).forEach(m => {
            const mId = `${yId}/${m}`;
            const mLabel = _MONTHS[parseInt(m, 10)] || m || '—';
            html += `<div class="tree-sub collapsed" data-sub="${esc(mId)}" style="padding-left:2.3rem"><span class="caret">▾</span><span>${esc(mLabel)}</span><span class="tree-count">${count(grp[y][m])}</span></div>`;
            html += `<div data-sub-body="${esc(mId)}" style="display:none">`;
            descKeys(grp[y][m]).forEach(dd => {
                const dId = `${mId}/${dd}`;
                const arr = grp[y][m][dd];
                html += `<div class="tree-sub collapsed" data-sub="${esc(dId)}" style="padding-left:3.3rem"><span class="caret">▾</span><span>${esc(dd || '—')}</span><span class="tree-count">${arr.length}</span></div>`;
                html += `<div data-sub-body="${esc(dId)}" style="display:none">` + arr.map(leaf).join('') + `</div>`;
            });
            html += `</div>`;
        });
        html += `</div>`;
    });
    return html;
}
function _renderTree() {
    const host = document.getElementById('ontology-tree');
    if (!host || !_spec) return;
    // Leaves are the real nodes; the taxonomy categories/subcategories ARE the tree
    // structure, so the structural spine nodes (root, class groups) aren't listed.
    const nodes = (_spec.nodes || []).filter(n => n.type !== 'segment' && n.type !== 'class_group');
    if (!nodes.length) { host.innerHTML = ''; return; }
    const multi = !!_spec.is_multi_user;
    const tree = {};
    let catOrder;
    if (multi) {
        // Admin platform scope → top layer is Users; second layer is the department.
        nodes.filter(n => n.type !== 'user').forEach(n => {
            const p = n.properties || {};
            const uname = p.owner_username || '—';
            const dept = ((p.taxonomy || {}).category) || 'Other';
            ((tree[uname] = tree[uname] || {})[dept] = tree[uname][dept] || []).push(n);
        });
        catOrder = (_spec.users || []).map(u => u.username).filter(u => tree[u]);
    } else {
        nodes.forEach(n => {
            const tx = (n.properties && n.properties.taxonomy) || { category: 'Other' };
            const cat = tx.category || 'Other', sub = tx.subcategory || 'General';
            ((tree[cat] = tree[cat] || {})[sub] = tree[cat][sub] || []).push(n);
        });
        catOrder = TREE_CAT_ORDER;
    }
    let html = `<div class="onto-tree-head">${multi ? 'Taxonomy · Users' : 'Taxonomy'}</div>`;
    catOrder.filter(c => tree[c]).forEach(cat => {
        const subs = tree[cat];
        const catCount = Object.values(subs).reduce((a, arr) => a + arr.length, 0);
        html += `<div class="tree-cat" data-cat="${esc(cat)}"><span class="caret">▾</span><span class="tree-cat-label">${esc(cat)}</span><span class="tree-count">${catCount}</span></div>`;
        html += `<div data-cat-body="${esc(cat)}">`;
        // Sessions get a progressive Year ▸ Month ▸ Day nesting (only buckets that
        // actually have sessions are rendered; all collapsed by default).
        if (!multi && cat === 'Sessions') { html += _renderSessionDateTree(Object.values(subs).flat()); html += `</div>`; return; }
        const subKeys = Object.keys(subs).sort((a, b) => a.localeCompare(b));
        subKeys.forEach(sub => {
            const arr = subs[sub].slice().sort((a, b) =>
                String((a.properties || {}).tag || a.name).localeCompare(String((b.properties || {}).tag || b.name)));
            const sid = cat + '/' + sub;
            html += `<div class="tree-sub collapsed" data-sub="${esc(sid)}"><span class="caret">▾</span><span>${esc(sub)}</span><span class="tree-count">${arr.length}</span></div>`;
            html += `<div data-sub-body="${esc(sid)}" style="display:none">`;
            arr.forEach(n => {
                const p = n.properties || {};
                const label = (String(n.type).startsWith('agent') ? '@' : '') + esc(p.tag || n.name);
                const expandable = String(n.type).startsWith('agent')
                    ? (p.components || []).length
                    : (['knowledge_graph', 'collection', 'memory'].includes(n.type) || _compDrillable(n));
                const isExp = _expandedNodes.has(n.entity_id);
                html += `<div class="tree-leaf${expandable ? ' expandable' : ''}" data-eid="${esc(n.entity_id)}" title="${label}">`;
                html += expandable ? `<span class="leaf-caret${isExp ? '' : ' collapsed'}">▾</span>` : `<span class="leaf-caret-spacer"></span>`;
                html += _leafDotAndCount(n);
                html += `</div>`;
                if (expandable && isExp) {
                    html += `<div data-tbody="${esc(n.entity_id)}" style="padding-left:0.85rem">` + _childrenOf(n).map(_childRowHtml).join('') + `</div>`;
                }
            });
            html += `</div>`;
        });
        html += `</div>`;
    });
    host.innerHTML = html;

    const bodySelFor = (row) => row.classList.contains('tree-cat')
        ? `[data-cat-body="${CSS.escape(row.getAttribute('data-cat'))}"]`
        : `[data-sub-body="${CSS.escape(row.getAttribute('data-sub'))}"]`;
    const eidsIn = (bsel) => Array.from(host.querySelectorAll(`${bsel} .tree-leaf`)).map(e => e.getAttribute('data-eid'));
    const markRow = (el) => {
        host.querySelectorAll('.tree-cat.sel, .tree-sub.sel, .tree-leaf.selected')
            .forEach(e => e.classList.remove('sel', 'selected'));
        el.classList.add(el.classList.contains('tree-leaf') ? 'selected' : 'sel');
    };
    const expand = (row, bsel) => { row.classList.remove('collapsed'); const b = host.querySelector(bsel); if (b) b.style.display = ''; };

    // The caret alone toggles expand/collapse (so a topic click can mean "focus").
    host.querySelectorAll('.tree-cat > .caret, .tree-sub > .caret').forEach(c => c.addEventListener('click', (e) => {
        e.stopPropagation();
        const row = c.parentElement;
        const bsel = bodySelFor(row);
        const collapsed = row.classList.toggle('collapsed');
        const b = host.querySelector(bsel);
        if (b) b.style.display = collapsed ? 'none' : '';
    }));
    // Clicking a TOPIC (category or subcategory) → select it, expand it, and FOCUS the
    // graph on its members. Clicking a LEAF → focus that node + open its detail.
    host.querySelectorAll('.tree-cat, .tree-sub').forEach(el => el.addEventListener('click', () => {
        const bsel = bodySelFor(el);
        // Clicking a topic TOGGLES it (collapse if open, expand if closed) — same as its
        // caret — and focuses the graph on its members only when it opens.
        const willExpand = el.classList.contains('collapsed');
        el.classList.toggle('collapsed', !willExpand);
        const b = host.querySelector(bsel);
        if (b) b.style.display = willExpand ? '' : 'none';
        markRow(el);
        if (willExpand) _focusGraph(eidsIn(bsel));
    }));
    // Leaf caret → expand/collapse the agent's components (synced with the graph).
    host.querySelectorAll('.tree-leaf > .leaf-caret').forEach(c => c.addEventListener('click', (e) => {
        e.stopPropagation();
        const node = (_spec.nodes || []).find(n => n.entity_id === c.parentElement.getAttribute('data-eid'));
        if (node) _toggleExpand(node);
    }));
    host.querySelectorAll('.tree-leaf').forEach(el => el.addEventListener('click', () => {
        const eid = el.getAttribute('data-eid');
        const node = _findNode(eid);
        markRow(el);
        _focusGraph(node ? _focusSetFor(node) : [eid], eid);
        if (node) {
            _openInspect(node);
            // Clicking an expandable row also drills into the next layer (synced with the
            // graph), so the hierarchy behaves like a real drill-down navigator.
            if (el.classList.contains('expandable')) _toggleExpand(node);
        }
    }));
    host.querySelectorAll('.tree-subleaf').forEach(_wireChildRow);
    _fillComponentCounts();
}

// Badge every instance-bearing component leaf with its instance count. Counts are
// fetched once per instance type (not per node) and cached for the session; a forced
// reload (scope/refresh) clears the cache. In multi-user scope the counts reflect the
// viewer's own instances (cross-user instance listing is a separate follow-up).
let _compCounts = null;
function _applyCompCounts() {
    const host = document.getElementById('ontology-tree');
    if (!host || !_compCounts) return;
    Object.entries(_compCounts).forEach(([cid, n]) => {
        // Show the count ONLY when there are instantiations; an empty instance-bearing
        // component then reads identically to a non-instance one (grey, no count, no
        // caret) — no stray '0' badges next to the blank ones.
        host.querySelectorAll(`[data-comp-count="${cid}"]`).forEach(el => {
            el.textContent = n > 0 ? String(n) : '';
            el.style.display = n > 0 ? '' : 'none';
        });
        // Dot is the real colour only when there are instantiations, else grey; and the
        // caret only appears when there's something to drill — drop it when empty so a
        // 0-count component reads the same as any other component with no instances.
        host.querySelectorAll(`[data-comp-dot="${cid}"]`).forEach(el => {
            el.style.background = n > 0 ? (el.getAttribute('data-accent') || '#64748b') : '#64748b';
            const row = el.closest('.tree-leaf, .tree-subleaf');
            if (row && n === 0) {
                row.classList.remove('expandable', 'tsl-exp');
                const caret = row.querySelector('.leaf-caret');
                if (caret) { const sp = document.createElement('span'); sp.className = 'leaf-caret-spacer'; caret.replaceWith(sp); }
            }
        });
    });
}
async function _fillComponentCounts() {
    const host = document.getElementById('ontology-tree');
    if (!host || !host.querySelector('[data-comp-count]')) return;
    if (_compCounts) { _applyCompCounts(); return; }
    const token = localStorage.getItem('tda_auth_token');
    const auth = { headers: { Authorization: `Bearer ${token}` } };
    const SRC = {
        dashboard: ['/api/v1/dashboards', 'dashboards'], flow: ['/api/v1/flows', 'flows'],
        canvas: ['/api/v1/canvases', 'canvases'], scheduler: ['/api/v1/scheduled-tasks', 'tasks'],
    };
    const counts = {};
    await Promise.all(Object.entries(SRC).map(async ([cid, [url, key]]) => {
        if (!host.querySelector(`[data-comp-count="${cid}"]`)) return;
        try {
            const r = await fetch(url, auth);
            const d = await r.json();
            counts[cid] = (d[key] || d.items || (Array.isArray(d) ? d : [])).length;
        } catch (_) { counts[cid] = 0; }
    }));
    // KG + memory components reflect estate CONTENT (knowledge graphs / memory nodes),
    // which the ontology already carries — so a Knowledge Graph component never reads
    // empty when a graph exists. Counted from the spec, not a REST list.
    const ec = (_spec && _spec.entity_type_counts) || {};
    counts.knowledge_graph = ec.knowledge_graph || 0;
    counts.memory = ec.memory || 0;
    _compCounts = counts;
    _applyCompCounts();
}

// Drive the graph's selection/focus from the taxonomy tree (the linked half of the
// split). The renderer module is browser-cached, so repeated calls are cheap.
async function _focusGraph(entityIds, primary) {
    _focusEids = (entityIds && entityIds.length) ? entityIds.slice() : null;
    _focusPrimary = (_focusEids && primary) ? primary : null;
    if (_viewMode !== 'map') return;
    try {
        const mod = await import('/api/v1/components/knowledge_graph/renderer');
        // An empty list clears the focus (resets opacities + ring) — used on background deselect.
        if (mod.focusKGNodes) mod.focusKGNodes(entityIds || [], { primary: _focusPrimary });
    } catch (e) { /* graph not mounted yet */ }
}

// The agent profile(s) an instantiation node relates to — general across EVERY type
// (collection · KG · LLM · connector · skill · component · memory), derived from the
// node's REAL projection edges so it needs no per-type property. Clicking the node then
// focuses [node + these agents], lighting up the connecting edge (see kg:node-select).
// Robust to d3 mutating link source/target from an index into a node object.
function _connectedAgentEids(node) {
    if (!node || !_spec) return [];
    const nodes = _spec.nodes || [];
    const isAgent = (t) => String(t || '').startsWith('agent');
    const agentEids = new Set(nodes.filter(n => isAgent(n.type)).map(n => n.entity_id));
    const out = new Set();
    const p = node.properties || {};
    // Injected/owned instantiations (per-agent components, connectors, memory) carry their owner.
    if (p.owner_profile_id && agentEids.has(`agent:${p.owner_profile_id}`)) out.add(`agent:${p.owner_profile_id}`);
    // Base instantiation nodes: any projection edge whose other end is an agent.
    const eidOf = (end) => (end && typeof end === 'object') ? end.entity_id : (nodes[end] ? nodes[end].entity_id : null);
    for (const l of (_spec.links || [])) {
        const a = eidOf(l.source), b = eidOf(l.target);
        if (a === node.entity_id && agentEids.has(b)) out.add(b);
        else if (b === node.entity_id && agentEids.has(a)) out.add(a);
    }
    return [...out];
}

// The focus set for a clicked node: an agent focuses itself (its own edges light up);
// any instantiation focuses itself PLUS the agent profile(s) it relates to, so the
// connecting edge pops. Shared by the graph-click and tree-click paths.
function _focusSetFor(node) {
    if (!node) return [];
    if ((node.type || '').startsWith('agent')) return [node.entity_id];
    const agents = _connectedAgentEids(node);
    return agents.length ? [node.entity_id, ...agents] : [node.entity_id];
}
function _selectTreeLeaf(eid) {
    const host = document.getElementById('ontology-tree');
    if (!host) return;
    host.querySelectorAll('.tree-leaf.selected, .tree-cat.sel, .tree-sub.sel').forEach(e => e.classList.remove('selected', 'sel'));
    const el = host.querySelector(`.tree-leaf[data-eid="${CSS.escape(eid)}"]`);
    if (!el) return;
    el.classList.add('selected');
    // Open its containing sub-branch so the selection is visible (graph→tree link).
    const subBody = el.closest('[data-sub-body]');
    if (subBody && subBody.style.display === 'none') {
        subBody.style.display = '';
        const sub = host.querySelector(`.tree-sub[data-sub="${CSS.escape(subBody.getAttribute('data-sub-body'))}"]`);
        if (sub) sub.classList.remove('collapsed');
    }
    el.scrollIntoView({ block: 'nearest' });
}

// ── main ────────────────────────────────────────────────────────────────
export async function loadOntologyView(opts) {
    const force = !!(opts && opts.force);
    if (force) _compCounts = null;   // scope change / refresh → recount instances
    _injectCSS();
    _wireLiveSync();
    const container = document.getElementById(GRAPH_ID);
    if (!container) return;

    // Preserve state across view switches: on a plain re-entry (not a forced refresh /
    // live-sync), leave the live DOM exactly as the user left it — graph focus, expanded
    // nodes, tree open/closed state, and the inspect panel. The view is only hidden on
    // switch-away, so its DOM survives; reloading would throw all of that away.
    if (!force && _rendered && _spec) { _setSync('ok'); return; }

    if (!_wired) {
        _wired = true;
        const btn = document.getElementById('ontology-refresh-btn');
        if (btn) btn.addEventListener('click', () => loadOntologyView({ force: true }));
        // Deselect inspect when the graph background fires a KG deselect.
        UderiaBus.on('uderia:kg-node-deselect', () => {
            const v = document.getElementById('ontology-view');
            if (v && v.classList.contains('active')) { _closeInspect(); _focusGraph(null); }
        });
        // After every (deferred) graph render, re-apply the active focus so the ring +
        // dim survive an expand / lens re-render — e.g. clicking an agent expands it,
        // which re-renders and would otherwise drop the ring before it's seen.
        UderiaBus.on('uderia:kg-rendered', (e) => {
            const v = document.getElementById('ontology-view');
            if (!v || !v.classList.contains('active') || !_focusEids) return;
            // ONLY re-apply on the ontology's OWN graph — never the KG inspector or a card
            // graph (they share the renderer but render into other containers).
            const c = e.detail && e.detail.container;
            if (!c || !c.closest || !c.closest('#' + GRAPH_ID)) return;
            import('/api/v1/components/knowledge_graph/renderer')
                .then(mod => { if (mod.focusKGNodes) mod.focusKGNodes(_focusEids, { noZoom: true, primary: _focusPrimary }); })
                .catch(() => {});
        });
        // Node click on the ontology graph → inspect (the renderer dispatches this).
        UderiaBus.on('uderia:kg-node-select', (e) => {
            const v = document.getElementById('ontology-view');
            if (!v || !v.classList.contains('active')) return;
            const sel = e.detail && e.detail.node;
            if (!sel || !_spec) return;
            const node = _resolveNode(_findNode(sel.entity_id, sel.name));
            if (node) {
                _openInspect(node);
                _selectTreeLeaf(node.entity_id);   // graph → tree link
                // Reveal the relationship: focus the clicked node together with the agent
                // profile(s) it relates to, so the connecting edge is highlighted and the
                // rest of the graph dims. General across every instantiation type.
                _focusGraph(_focusSetFor(node), node.entity_id);
            }
        });
        // Double-click a graph node → expand it contextually.
        UderiaBus.on('uderia:kg-node-dblclick', (e) => {
            const v = document.getElementById('ontology-view');
            if (!v || !v.classList.contains('active')) return;
            const sel = e.detail && e.detail.node;
            if (!sel || !_spec) return;
            const node = _findNode(sel.entity_id, sel.name);
            if (!node) return;
            const p = node.properties || {};
            // Uniform: double-click EXPANDS the node in place (agent→config composition,
            // component→its instances, KG→concepts, collection→docs, memory→items); again collapses.
            const expandable = (node.type || '').startsWith('agent')
                ? true   // always exposes its Components → { User Interface, Connectors } group
                : ['knowledge_graph', 'collection', 'memory', 'component', 'onto_group'].includes(node.type);
            if (expandable) _toggleExpand(node);
            else _openInspect(node);
        });
    }

    const isRefresh = !!_spec;     // a re-sync (live-sync / refresh), not the first open
    const prevSpec = _spec;
    _setSync('busy');
    if (!isRefresh) {
        container.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-muted);">Building your ontology…</div>`;
    }

    try {
        const token = localStorage.getItem('tda_auth_token');
        const sp = _scopeParam();
        const url = '/api/v1/platform-ontology/segment' + (sp !== 'me' ? `?users=${encodeURIComponent(sp)}` : '');
        const resp = await fetch(url, { headers: { 'Authorization': `Bearer ${token}` } });
        const data = await resp.json();
        if (!resp.ok || data.status !== 'success') throw new Error(data.message || `HTTP ${resp.status}`);
        _spec = data.spec || {};
        _isAdmin = !!data.is_admin;
        _lastSyncAt = Date.now();
        _setSync('ok');

        // Change-awareness: tell the user what changed since the last sync.
        if (isRefresh) _showChangeToast(_diffSpecs(prevSpec, _spec));

        _closeInspect();
        _renderDiscover();
        _renderKpis();
        _renderBandCards();
        _renderModeTabs();

        if (!_spec.nodes || _spec.nodes.length === 0) {
            const ctrls = document.getElementById('ontology-controls');
            if (ctrls) ctrls.innerHTML = '';
            _show('ontology-tree', false);
            _show('ontology-matrix', false);
            _show('ontology-scoreboard', false);
            _show('ontology-graph-container', true);
            container.style.left = '0';   // no tree → graph fills the stage
            container.innerHTML = `
                <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;gap:0.75rem;text-align:center;color:var(--text-muted);">
                    <div style="font-weight:600;color:var(--text-primary);">Your world is empty</div>
                    <div style="max-width:32rem;font-size:0.875rem;">Create an agent and ground it on knowledge — your estate's trust map and what it knows will take shape here.</div>
                </div>`;
            return;
        }

        // Render the active cockpit view (Map / Matrix / Trust), default Map + Trust lens.
        _show('ontology-tree', _viewMode === 'map');
        _show('ontology-graph-container', _viewMode === 'map');
        _show('ontology-matrix', _viewMode === 'matrix');
        _show('ontology-scoreboard', _viewMode === 'trust');
        container.style.left = _viewMode === 'map' ? '288px' : '0';   // tree pane offset
        await _renderActiveView();
        _rendered = true;   // a build has rendered the live DOM → preserve it on re-entry
    } catch (err) {
        console.error('[Ontology] segment load failed', err);
        _setSync('err');
        if (!_spec) {
            container.innerHTML = `<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;gap:0.5rem;text-align:center;color:var(--text-muted);">
                <div style="font-weight:600;color:var(--text-primary);">Couldn't build your ontology</div>
                <div style="max-width:32rem;font-size:0.875rem;">${esc(err.message)}</div></div>`;
        }
    }
}

window.loadOntologyView = loadOntologyView;

// Deep-link: focus ONE agent in the estate — used by the Anatomy and Trust
// Circle cross-panel pills so the agent travels with the jump. Waits for the
// segment build (the view mounts async), then inspects + focuses the node.
async function ontologyFocusAgent(tag) {
    if (!tag) return;
    for (let i = 0; i < 40; i++) {
        if (_spec && (_spec.nodes || []).length && _rendered) break;
        await new Promise((r) => setTimeout(r, 200));
    }
    const node = ((_spec && _spec.nodes) || []).find((n) =>
        (n.type || '').startsWith('agent') &&
        (((n.properties || {}).tag === tag) || n.name === tag));
    if (!node) return;
    if (_viewMode !== 'map') _switchViewMode('map');
    _openInspect(node);
    _selectTreeLeaf(node.entity_id);
    _focusGraph(_focusSetFor(node), node.entity_id);
}
window.ontologyFocusAgent = ontologyFocusAgent;
