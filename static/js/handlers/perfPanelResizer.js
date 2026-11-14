/**
 * handlers/perfPanelResizer.js
 *
 * Adds a draggable horizontal divider between the KPI/charts command center
 * (top) and the roster (bottom) on the Session Performance and Agent
 * Performance panels — drag down to give the KPIs/charts more room, drag up
 * to give the roster more room.
 *
 * Visually mirrors the conversation split-view divider (a subtle bar with a
 * centered pill handle that brightens on hover), rotated for vertical resize.
 *
 * The chosen height per panel is persisted to localStorage.
 */

const MIN_TOP = 120;     // never collapse the KPI header below this
const MIN_BOTTOM = 180;  // always keep the roster usable

/**
 * Wire one panel: insert a resizer between topEl and bottomEl (which must be
 * adjacent children of the same flex-col container) and make the top section
 * resizable + internally scrollable.
 */
function setupResizer({ topEl, bottomEl, storageKey, watchEl = null }) {
    if (!topEl || !bottomEl) return;
    const container = topEl.parentElement;
    if (!container || bottomEl.parentElement !== container) return;
    if (topEl.dataset.perfResizerWired === '1') return;  // idempotent
    topEl.dataset.perfResizerWired = '1';

    // Build the divider, matching the split-view visual.
    const resizer = document.createElement('div');
    resizer.className = 'perf-resizer';
    resizer.setAttribute('role', 'separator');
    resizer.setAttribute('aria-orientation', 'horizontal');
    resizer.title = 'Drag to resize \u00b7 Shift-click to collapse the upper area \u00b7 Double-click to maximize it';
    resizer.innerHTML = '<div class="perf-resizer-handle"></div>';
    container.insertBefore(resizer, bottomEl);

    // Top section becomes a fixed-basis, internally-scrollable region.
    topEl.style.flex = '0 0 auto';
    topEl.style.overflowY = 'auto';
    topEl.style.minHeight = '0';
    bottomEl.style.flex = '1 1 0';
    bottomEl.style.minHeight = '0';

    function applyHeight(px, { clamp = true } = {}) {
        let h = px;
        if (clamp) {
            const ch = container.clientHeight;
            if (ch > 0) {
                const max = ch - MIN_BOTTOM - resizer.offsetHeight;
                h = Math.max(MIN_TOP, Math.min(px, Math.max(MIN_TOP, max)));
            }
        }
        topEl.style.flexBasis = h + 'px';
        topEl.style.height = h + 'px';
        return h;
    }

    // ── Collapse / maximize gestures ────────────────────────────────────
    // Shift-click  → collapse the upper area completely (only the divider
    //                remains visible; `display:none` so padding can't leak).
    // Double-click → restore + maximize the upper area (its content height,
    //                capped so the lower pane keeps MIN_BOTTOM).
    // Dragging out of a collapsed state restores the upper area first.
    const collapsedKey = storageKey + ':collapsed';

    function collapseTop() {
        topEl.style.display = 'none';
        localStorage.setItem(collapsedKey, '1');
    }
    function restoreTop() {
        if (topEl.style.display === 'none') topEl.style.display = '';
        localStorage.removeItem(collapsedKey);
    }
    function maximizeTop() {
        restoreTop();
        topEl.style.flex = '0 0 auto';
        const max = Math.max(MIN_TOP, (container.clientHeight || 0) - MIN_BOTTOM - resizer.offsetHeight);
        const content = topEl.scrollHeight || max;
        const h = applyHeight(Math.min(content + 2, max));
        localStorage.setItem(storageKey, String(h));
    }

    // Restore persisted status (browser cache = localStorage): last height or
    // collapsed. With NO saved status the DEFAULT is a MAXIMIZED upper area —
    // applied on first visibility (the views are hidden at init, so the cap
    // can only be computed once the container has a height). Master-detail
    // splits (watchEl) keep their own default: the detail must stay usable.
    const saved = parseInt(localStorage.getItem(storageKey), 10);
    if (!isNaN(saved)) applyHeight(saved, { clamp: false });
    if (localStorage.getItem(collapsedKey) === '1') collapseTop();
    let needsDefaultMax = isNaN(saved) && localStorage.getItem(collapsedKey) !== '1' && !watchEl;

    let dragging = false;
    const onDown = (e) => {
        if (e.shiftKey) {           // shift-click = collapse, never a drag
            collapseTop();
            e.preventDefault();
            return;
        }
        dragging = true;
        document.body.style.cursor = 'row-resize';
        document.body.style.userSelect = 'none';
        e.preventDefault();
    };
    const onMove = (e) => {
        if (!dragging) return;
        if (topEl.style.display === 'none') {   // dragging out of collapse
            restoreTop();
            topEl.style.flex = '0 0 auto';
        }
        const clientY = e.touches ? e.touches[0].clientY : e.clientY;
        const rect = container.getBoundingClientRect();
        applyHeight(clientY - rect.top);
    };
    const onUp = () => {
        if (!dragging) return;
        dragging = false;
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        const cur = parseInt(topEl.style.flexBasis, 10);
        if (!isNaN(cur)) localStorage.setItem(storageKey, String(cur));
    };

    resizer.addEventListener('mousedown', onDown);
    resizer.addEventListener('dblclick', (e) => { e.preventDefault(); maximizeTop(); });
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
    resizer.addEventListener('touchstart', onDown, { passive: false });
    document.addEventListener('touchmove', onMove, { passive: false });
    document.addEventListener('touchend', onUp);

    // Re-clamp whenever the container becomes visible or changes size
    // (the perf views are hidden until switched to, so clientHeight starts 0).
    if (typeof ResizeObserver !== 'undefined') {
        const ro = new ResizeObserver(() => {
            if (container.clientHeight <= 0) return;
            if (needsDefaultMax) {           // first visibility, no saved status
                needsDefaultMax = false;
                maximizeTop();
            } else if (topEl.style.flexBasis) {
                applyHeight(parseInt(topEl.style.flexBasis, 10));
            }
        });
        ro.observe(container);
    }

    // Auto-collapse mode (master-detail splits): `watchEl` is the dynamic panel a
    // handler toggles `hidden` on. While it's hidden there is nothing below the
    // divider — the divider hides and the top region fills the pane; when the
    // panel appears, the split (persisted height) is restored and bottomEl (the
    // static region wrapping the panel) is revealed.
    if (watchEl) {
        const sync = () => {
            const off = watchEl.classList.contains('hidden');
            resizer.style.display = off ? 'none' : '';
            if (bottomEl !== watchEl) bottomEl.classList.toggle('hidden', off);
            if (off) {
                if (topEl.style.display === 'none') topEl.style.display = '';
                localStorage.removeItem(storageKey + ':collapsed');
                topEl.style.flex = '1 1 0';
                topEl.style.height = '';
                topEl.style.flexBasis = '';
            } else {
                topEl.style.flex = '0 0 auto';
                const savedH = parseInt(localStorage.getItem(storageKey), 10);
                applyHeight(!isNaN(savedH) ? savedH
                    : Math.max(MIN_TOP, Math.round((container.clientHeight || 640) * 0.45)));
            }
        };
        if (typeof MutationObserver !== 'undefined') {
            new MutationObserver(sync).observe(watchEl, { attributes: true, attributeFilter: ['class'] });
        }
        sync();
    }
}

export function initPerfPanelResizers() {
    // Session Performance — both children have stable IDs.
    setupResizer({
        topEl: document.getElementById('exec-command-center'),
        bottomEl: document.getElementById('session-gallery'),
        storageKey: 'perfResize:executions',
    });

    // Agent Performance — stable IDs: the command center (the always-visible
    // Overview pane: KPIs + charts) over the tabbed-topics lower pane.
    setupResizer({
        topEl: document.getElementById('agent-command-center'),
        bottomEl: document.getElementById('agent-lower-pane'),
        storageKey: 'perfResize:agents',
    });

    // Configuration (Anatomy) — the composition command center over the
    // config tab bar + panes.
    setupResizer({
        topEl: document.getElementById('anatomy-command-center'),
        bottomEl: document.getElementById('config-lower-pane'),
        storageKey: 'perfResize:configuration',
    });

    // Ontology — the estate command center over the Map/Matrix/Trust stage.
    // The partial is part of the optional enterprise suite; setupResizer
    // no-ops when the elements are absent.
    setupResizer({
        topEl: document.getElementById('ontology-command-center'),
        bottomEl: document.getElementById('ontology-lower-pane'),
        storageKey: 'perfResize:ontology',
    });

    // Intelligence — the RAG-impact KPI band over the repository tabs.
    setupResizer({
        topEl: document.getElementById('intel-command-center'),
        bottomEl: document.getElementById('intel-lower-pane'),
        storageKey: 'perfResize:intelligence',
    });

    // Components — master-detail splits: the cards region over the detail
    // region, one per tab; the divider only exists while a detail is open.
    setupResizer({
        topEl: document.getElementById('components-cards-region'),
        bottomEl: document.getElementById('component-detail-region'),
        watchEl: document.getElementById('component-detail-panel'),
        storageKey: 'perfResize:components-ui',
    });
    setupResizer({
        topEl: document.getElementById('pmcp-cards-region'),
        bottomEl: document.getElementById('pmcp-detail-region'),
        watchEl: document.getElementById('platform-mcp-detail-panel'),
        storageKey: 'perfResize:components-conn',
    });
}

// Auto-initialize when DOM is ready (the elements exist even while hidden).
if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initPerfPanelResizers);
    } else {
        initPerfPanelResizers();
    }
}
