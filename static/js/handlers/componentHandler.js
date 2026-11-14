/**
 * Component Handler
 *
 * Manages the top-level Components view (sidebar → Components):
 * - Fetches component list from GET /v1/components
 * - Renders card gallery with category/status filter pills
 * - Card selection → fetches detail from GET /v1/components/<id>
 * - Detail panel with capabilities table + tool arguments table
 * - Reload button for hot-reload from disk
 * - Import button governance (hidden when admin disables user components)
 */

import { fetchRoles, countsLine, reachHTML, declarationLine, esc as _rolesEsc } from './platformRolesView.js?v=1.2';

let _componentSettings = {};
let _cachedComponents = [];
let _selectedComponentId = null;
let _initialized = false;
let _pendingJobsTabCollection = null;

// ─── DOM IDs (top-level view) ───────────────────────────────────────────────
const GRID_ID = 'components-grid-top';
const DETAIL_ID = 'component-detail-panel';
const RELOAD_BTN_ID = 'reload-components-btn-top';
const CATEGORY_FILTERS_ID = 'component-category-filters-top';
const STATUS_FILTERS_ID = 'component-status-filters-top';

// ─── Per-component icon + accent map (mirrors _COMP_META in platformConnectorAdminHandler) ──
// ─── IFOC Methodology Naming ────────────────────────────────────────────────
// Canonical mapping from configurationHandler.js ifocTagConfig
const IFOC_CONFIG = {
    'llm_only':      { label: 'Ideate',     badgeClass: 'bg-green-500/20 text-green-300 comp-lt-green' },
    'rag_focused':   { label: 'Focus',      badgeClass: 'bg-blue-500/20 text-blue-300 comp-lt-blue' },
    'tool_enabled':  { label: 'Optimize',   badgeClass: 'bg-purple-500/20 text-purple-300 comp-lt-purple' },
    'genie':         { label: 'Coordinate', badgeClass: 'bg-orange-500/20 text-orange-300 comp-lt-orange' },
};
const IFOC_ORDER = ['llm_only', 'rag_focused', 'tool_enabled', 'genie'];

// ─── Split-panel accent (single source of truth) ─────────────────────────────
// Each component's accent colour lives ONLY in its manifest (`icon_accent`).
// The admin card icons already read it; this propagates the SAME value to each
// component's split-panel <aside> as --sp-accent, so the panel header/icon/
// switcher/outline match the manifest with no per-panel hardcoding in index.html.
// Component accents are a deliberately NON-IFOC "tool" palette so they never
// collide with the conversation panel (whose accent is the active profile's IFOC
// colour: Ideate green / Focus blue / Optimize purple / Coordinate orange).
const COMPONENT_PANEL_ASIDE = {
    canvas:          'canvas-split-panel',
    knowledge_graph: 'kg-split-panel',
    scheduler:       'scheduler-split-panel',
    dashboard:       'dashboard-split-panel',
    flow:            'flow-split-panel',
    memory:          'memory-split-panel',
    database_navigator: 'dbnav-split-panel',
    explain_plan:    'explain-plan-split-panel',
    action:          'action-split-panel',
    obda_read:       'obda-read-split-panel',
    obda_model:      'obda-model-split-panel',
    files:           'files-split-panel',
};

export async function applyComponentPanelAccents() {
    try {
        const token = localStorage.getItem('tda_auth_token');
        if (!token) return;
        const resp = await fetch('/api/v1/components', { headers: { 'Authorization': `Bearer ${token}` } });
        if (!resp.ok) return;
        const data = await resp.json();
        const comps = Array.isArray(data) ? data : (data.components || []);
        // Publish a live component_id -> icon_accent map (single source of truth: the
        // manifest, surfaced via /api/v1/components). Inline component cards (e.g. a
        // flow/KG preview pinned as a dashboard tile) read this at render time so their
        // footer button matches the component's split-view accent without hardcoding it.
        const accentMap = window.__componentAccents || (window.__componentAccents = {});
        for (const comp of comps) {
            if (comp.component_id && comp.icon_accent) accentMap[comp.component_id] = comp.icon_accent;
            const asideId = COMPONENT_PANEL_ASIDE[comp.component_id];
            const accent = comp.icon_accent;
            if (!asideId || !accent) continue;
            const aside = document.getElementById(asideId);
            if (aside) aside.style.setProperty('--sp-accent', accent);
        }
    } catch (_) { /* CSS .sp-panel default covers failure */ }
}

/**
 * Entry point called by ui.js performViewSwitch() when navigating to components-view.
 * Loads components and initializes handlers (once).
 */
export async function loadComponentsView() {
    if (!_initialized) {
        _initializeHandlers();
        _initialized = true;
    }
    await _loadComponents();
}

/**
 * Legacy export kept for backward compatibility if any caller still uses it.
 */
export async function loadComponents() {
    await _loadComponents();
}

/**
 * Navigate directly to the Task Scheduler → Jobs → Platform Jobs for the
 * given collection. Called from knowledge repository card "Manage" buttons.
 *
 * Does a bare-DOM view switch (no dynamic import / no competing _loadComponents)
 * so there is only one _onCardClick in flight. The _pendingJobsTabCollection flag
 * is consumed inside _onCardClick after _renderDetailPanel completes.
 */
export async function openSchedulerToCollection(collectionId) {
    // 1. Switch to components-view via bare DOM (avoids triggering a second _loadComponents)
    document.querySelectorAll('.app-view').forEach(v => {
        v.classList.toggle('active', v.id === 'components-view');
    });
    document.querySelectorAll('.view-switch-button').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.view === 'components-view');
    });

    // 2. Pre-select scheduler so _loadComponents auto-select picks the right card,
    //    and set the flag so _onCardClick activates Jobs tab after render.
    _selectedComponentId = 'scheduler';
    _pendingJobsTabCollection = collectionId;

    // 3. Load components grid (if not already loaded) then open scheduler detail
    if (_cachedComponents.length === 0) {
        // _loadComponents sees _selectedComponentId='scheduler' and calls
        // _onCardClick('scheduler') internally, which consumes the flag.
        await _loadComponents();
    } else {
        // Grid already rendered; go straight to scheduler detail.
        await _onCardClick('scheduler');
    }
}

/**
 * Open the Components view directly on a given component's detail panel, optionally
 * activating a specific tab. Used to return to a component's management tab after a
 * full-screen sub-view (e.g. the Knowledge Graph inspector) closes.
 *
 * Bare-DOM view switch (mirrors openSchedulerToCollection) to avoid a competing
 * _loadComponents, then opens the detail via _onCardClick and clicks the target tab.
 */
export async function openComponentDetail(componentId, { activateTabId } = {}) {
    document.querySelectorAll('.app-view').forEach(v => {
        v.classList.toggle('active', v.id === 'components-view');
    });
    document.querySelectorAll('.view-switch-button').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.view === 'components-view');
    });

    _selectedComponentId = componentId;
    if (_cachedComponents.length === 0) {
        // _loadComponents sees _selectedComponentId and opens its detail internally.
        await _loadComponents();
    } else {
        await _onCardClick(componentId);
    }

    if (activateTabId) {
        document.getElementById(activateTabId)?.click();
    }
}

/**
 * Fetch components from the API and render the card grid.
 */
async function _loadComponents() {
    const grid = document.getElementById(GRID_ID);
    if (!grid) return;

    grid.innerHTML = '<div class="col-span-full text-center text-gray-400 py-8">Loading components...</div>';

    try {
        const token = localStorage.getItem('tda_auth_token');
        const resp = await fetch('/api/v1/components', {
            headers: { 'Authorization': `Bearer ${token}` }
        });

        if (!resp.ok) {
            grid.innerHTML = '<div class="col-span-full text-center text-red-400 py-8">Failed to load components</div>';
            return;
        }

        const data = await resp.json();
        _cachedComponents = (data.components || []).slice().sort((a, b) =>
            String(a.display_name || a.component_id || '').localeCompare(String(b.display_name || b.component_id || '')));
        _componentSettings = data._settings || {};

        if (_cachedComponents.length === 0) {
            grid.innerHTML = '<div class="col-span-full text-center text-gray-400 py-8">No components installed</div>';
            _hideDetailPanel();
            return;
        }

        grid.innerHTML = _cachedComponents.map(comp => _renderComponentCard(comp)).join('');
        _fillRoleLines(grid);

        // Attach click handlers to cards
        grid.querySelectorAll('.component-card').forEach(card => {
            card.addEventListener('click', () => _onCardClick(card.dataset.componentId));
            card.style.cursor = 'pointer';
        });

        // Auto-select first card if nothing selected
        if (!_selectedComponentId || !_cachedComponents.find(c => c.component_id === _selectedComponentId)) {
            _onCardClick(_cachedComponents[0].component_id);
        } else {
            // Re-select the previously selected card
            _onCardClick(_selectedComponentId);
        }

    } catch (err) {
        console.error('[ComponentHandler] Error loading components:', err);
        grid.innerHTML = '<div class="col-span-full text-center text-red-400 py-8">Error loading components</div>';
    }
}

/**
 * Render a single component card as an HTML string.
 */
// Whose the things a component keeps are — the manifest's `instances` (every component
// declares it), the same field that scopes its Workspace section.
const INSTANCE_PILLS = {
    user:  { label: 'Per User',  tip: 'What it keeps belongs to the user — listed in the Workspace whichever agent is active' },
    agent: { label: 'Per Agent', tip: 'What it lists belongs to the agent — the Workspace shows the active agent’s own' },
    page:  { label: 'Per Page',  tip: 'Held in the open page only — not kept after a reload' },
    none:  { label: 'Stateless', tip: 'Keeps nothing of its own — it acts within the turn' },
};

// Acts on every turn without being called — the vocabulary's "active" (always a subset of
// enabled). A declaration in the manifest (acts_every_turn), never a switch.
function _everyTurnPill(comp) {
    const on = !!(comp.acts_every_turn ?? comp.manifest?.acts_every_turn);
    if (!on) return '';
    return `<span class="comp-every-turn-pill u-tip text-xs px-2 py-0.5 rounded" data-every-turn="1" data-tip="In effect on every turn without being called — active whenever it is enabled" aria-label="Every turn: in effect on every turn without being called"
                 style="background:rgba(6,182,212,0.10);color:#22d3ee;border:1px solid rgba(6,182,212,0.3)">every turn</span>`;
}

function _instancePill(comp) {
    const p = INSTANCE_PILLS[comp.instances];
    if (!p) return '';
    return `<span class="comp-instances-pill u-tip text-xs px-2 py-0.5 rounded" data-instances="${comp.instances}" data-tip="${p.tip}" aria-label="${p.label}: ${p.tip}"
        style="color:var(--text-primary);background:var(--hover-bg-strong, rgba(148,163,184,0.12));border:1px solid var(--border-primary)">${p.label}</span>`;
}

function _renderComponentCard(comp) {
    const sourceClasses = {
        builtin: 'bg-violet-500/20 text-violet-300 comp-lt-violet',
        agent_pack: 'bg-cyan-500/20 text-cyan-300 comp-lt-cyan',
        user: 'bg-amber-500/20 text-amber-300 comp-lt-amber',
    };
    const typeClasses = {
        action: 'bg-blue-500/20 text-blue-300 comp-lt-blue',
        structural: 'bg-gray-500/20 text-gray-300 comp-lt-gray',
        service: 'bg-purple-500/20 text-purple-300 comp-lt-purple',
    };

    const isDisabled = !!comp.globally_disabled;
    const sourceBadge = sourceClasses[comp.source] || sourceClasses.builtin;
    const typeBadge = typeClasses[comp.component_type] || typeClasses.action;
    const toolName = comp.tool_name ? `<span class="text-xs font-mono ${isDisabled ? 'comp-lt-text-muted' : 'text-cyan-300 comp-lt-text-cyan'}">${comp.tool_name}</span>` : '';

    const iconBadge = comp.icon_svg && comp.icon_accent
        ? `<div class="flex-shrink-0 flex items-center justify-center w-8 h-8 rounded-lg mr-2" style="background:${comp.icon_accent}22;color:${comp.icon_accent}">${comp.icon_svg}</div>`
        : '';

    const renderTargets = (comp.render_targets?.supports || ['inline']).map(t =>
        `<span class="text-xs px-2 py-0.5 rounded bg-gray-500/20 text-gray-300 comp-lt-gray">${t}</span>`
    ).join('');

    const statusFooter = isDisabled
        ? `<div class="flex items-center gap-1 text-xs" style="color:var(--text-muted)">
               <svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                   <path stroke-linecap="round" stroke-linejoin="round" d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636"/>
               </svg>
               Disabled globally
           </div>`
        : comp.has_handler
            ? '<div class="flex items-center gap-1 text-xs text-green-400"><svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" /></svg> Handler loaded</div>'
            : '<div class="flex items-center gap-1 text-xs text-yellow-400"><svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg> No handler</div>';

    return `
        <div class="glass-panel rounded-lg p-4 flex flex-col gap-3 component-card transition-all duration-150${isDisabled ? ' opacity-50' : ''}"
             data-component-type="${comp.component_type}"
             data-component-source="${comp.source}"
             data-component-id="${comp.component_id}"
             data-globally-disabled="${isDisabled}">
            <!-- Title row: icon · name · pills. The description sits BELOW at the card's full
                 width — beside the pills it was squeezed into what they left over. -->
            <div class="flex items-start justify-between gap-2">
                <div class="flex items-center flex-1 min-w-0">
                    ${iconBadge}
                    <h4 class="text-sm font-semibold" style="color:var(--text-primary)">${comp.display_name}</h4>
                </div>
                <div class="flex flex-wrap items-center justify-end gap-1.5 flex-shrink-0" style="max-width:60%">
                    ${isDisabled ? `<span class="text-xs px-2 py-0.5 rounded bg-gray-500/20 text-gray-400 comp-lt-gray">inactive</span>` : ''}
                    ${comp.experimental ? `<span class="text-xs px-2 py-0.5 rounded" style="background:rgba(245,158,11,0.15);color:#fbbf24;border:1px solid rgba(245,158,11,0.3);">experimental</span>` : ''}
                    ${_instancePill(comp)}
                    ${_everyTurnPill(comp)}
                    <span class="text-xs px-2 py-0.5 rounded ${sourceBadge}">${comp.source}</span>
                    <span class="text-xs px-2 py-0.5 rounded ${typeBadge}">${comp.component_type}</span>
                </div>
            </div>
            <p class="text-xs -mt-1" style="color:var(--text-muted)">${comp.description || ''}</p>
            ${comp.roles ? `<div class="comp-roles-line text-xs -mt-1" data-roles-line="${comp.component_id}" style="color:var(--text-muted)">Roles</div>` : ''}
            <div class="flex items-center justify-between text-xs">
                <div class="flex items-center gap-2">
                    ${toolName}
                    <span style="color:var(--text-muted)">v${comp.version}</span>
                </div>
                <div class="flex items-center gap-1">
                    ${renderTargets}
                </div>
            </div>
            ${statusFooter}
        </div>
    `;
}

// ─── Card Selection & Detail Panel ──────────────────────────────────────────

/**
 * Handle card click — highlight selected card and fetch detail.
 */
async function _onCardClick(componentId) {
    if (!componentId) return;
    _selectedComponentId = componentId;

    // Update selection outline (uses outline, not ring/box-shadow, to avoid
    // conflict with .glass-panel:hover box-shadow)
    const grid = document.getElementById(GRID_ID);
    if (grid) {
        grid.querySelectorAll('.component-card').forEach(card => {
            if (card.dataset.componentId === componentId) {
                card.style.outline = '2px solid rgba(34,211,238,0.6)';
                card.style.outlineOffset = '-2px';
            } else {
                card.style.outline = '';
                card.style.outlineOffset = '';
            }
        });
    }

    // Fetch full component detail
    try {
        const token = localStorage.getItem('tda_auth_token');
        const resp = await fetch(`/api/v1/components/${componentId}`, {
            headers: { 'Authorization': `Bearer ${token}` }
        });

        if (!resp.ok) {
            _showDetailError('Failed to load component details');
            return;
        }

        const data = await resp.json();
        _renderDetailPanel(data.component);

        // Deep-link: activate Jobs tab after render if requested for this scheduler render
        if (_pendingJobsTabCollection !== null && data.component.component_id === 'scheduler') {
            const collId = _pendingJobsTabCollection;
            _pendingJobsTabCollection = null;
            requestAnimationFrame(() => {
                const jobsTab = document.getElementById('comp-tab-jobs');
                if (jobsTab) {
                    jobsTab.click();
                    window.jobsHandler?.openToCollection(collId);
                }
            });
        }

    } catch (err) {
        console.error('[ComponentHandler] Detail fetch error:', err);
        _showDetailError('Error loading component details');
    }
}

/**
 * Render the full detail panel for a selected component.
 */
function _renderDetailPanel(comp) {
    const panel = document.getElementById(DETAIL_ID);
    if (!panel) return;

    const manifest = comp.manifest || {};
    const toolDef = manifest.tool_definition || {};
    const renderTargets = comp.render_targets || manifest.render_targets || {};
    const profileDefaults = comp.profile_defaults || manifest.profile_defaults || {};
    const backend = manifest.backend || {};

    // ── Header Section ──
    const sourceClasses = {
        builtin: 'bg-violet-500/20 text-violet-300 comp-lt-violet',
        agent_pack: 'bg-cyan-500/20 text-cyan-300 comp-lt-cyan',
        user: 'bg-amber-500/20 text-amber-300 comp-lt-amber',
    };
    const typeClasses = {
        action: 'bg-blue-500/20 text-blue-300 comp-lt-blue',
        structural: 'bg-gray-500/20 text-gray-300 comp-lt-gray',
        service: 'bg-purple-500/20 text-purple-300 comp-lt-purple',
    };
    const sourceBadge = sourceClasses[comp.source] || sourceClasses.builtin;
    const typeBadge = typeClasses[comp.component_type] || typeClasses.action;

    const supportsStr = (renderTargets.supports || ['inline']).map(t =>
        `<span class="px-2 py-0.5 rounded bg-gray-500/20 text-gray-300 comp-lt-gray text-xs">${t}</span>`
    ).join('');

    const sortedEnabledFor = [...(profileDefaults.enabled_for || [])].sort(
        (a, b) => (IFOC_ORDER.indexOf(a) >>> 0) - (IFOC_ORDER.indexOf(b) >>> 0)
    );
    const enabledFor = sortedEnabledFor.map(p => {
        const ifoc = IFOC_CONFIG[p] || { label: p, badgeClass: 'bg-gray-500/20 text-gray-300 comp-lt-gray' };
        return `<span class="px-2 py-0.5 rounded ${ifoc.badgeClass} text-xs">${ifoc.label}</span>`;
    }).join('');

    const headerHTML = `
        <div class="flex items-start gap-4 mb-6">
            <div class="flex-shrink-0 w-12 h-12 rounded-lg flex items-center justify-center"
                 style="background:${comp.icon_accent ? comp.icon_accent + '22' : 'rgba(34,211,238,0.12)'};color:${comp.icon_accent || '#22d3ee'}">
                ${comp.icon_svg
                    ? comp.icon_svg.replace(/class="h-4 w-4"/, 'class="h-6 w-6"')
                    : `<svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                           <path stroke-linecap="round" stroke-linejoin="round" d="M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM4 13a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6zM16 13a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z" />
                       </svg>`}
            </div>
            <div class="flex-1 min-w-0">
                <div class="flex items-center gap-3 flex-wrap">
                    <h2 class="text-xl font-bold" style="color:var(--text-primary)">${comp.display_name}</h2>
                    <span class="text-sm" style="color:var(--text-muted)">v${comp.version}</span>
                    ${_instancePill(comp)}
                    <span class="text-xs px-2 py-0.5 rounded ${sourceBadge}">${comp.source}</span>
                    <span class="text-xs px-2 py-0.5 rounded ${typeBadge}">${comp.component_type}</span>
                    <div class="ml-auto flex items-center gap-2 px-2.5 py-1 rounded-md flex-shrink-0"
                         style="background:var(--hover-bg);border:1px solid var(--border-secondary)"
                         title="Your creation default: when on, a new agent of yours starts with this component enabled. An agent that stores no setting follows the component's class default (Auto) until you toggle it.">
                        <span class="text-xs" style="color:var(--text-muted)">On for my new agents</span>
                        <label class="ind-toggle ind-toggle--sm">
                            <input type="checkbox" id="comp-profile-default-toggle"
                                   data-component-id="${comp.component_id}"
                                   ${(comp.user_profile_default_enabled ?? false) ? 'checked' : ''}>
                            <span class="ind-track"></span>
                        </label>
                    </div>
                </div>
                <p class="text-sm mt-1" style="color:var(--text-muted)">${comp.description || ''}</p>
                <div class="flex items-center gap-4 mt-3 text-sm">
                    ${toolDef.name ? `<div class="flex items-center gap-1.5">
                        <span style="color:var(--text-muted)">Tool:</span>
                        <span class="font-mono text-cyan-300 comp-lt-text-cyan">${toolDef.name}</span>
                    </div>` : ''}
                    ${backend.fast_path ? `<div class="flex items-center gap-1 px-2 py-0.5 rounded-full text-emerald-400 comp-lt-emerald">
                        <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
                        </svg>
                        Fast-path
                    </div>` : ''}
                    <div class="flex items-center gap-1.5">
                        <span style="color:var(--text-muted)">Render:</span>
                        ${supportsStr}
                    </div>
                </div>
                ${enabledFor ? `<div class="flex items-center gap-1.5 mt-2 text-sm">
                    <span style="color:var(--text-muted)">Profiles:</span>
                    ${enabledFor}
                </div>` : ''}
            </div>
        </div>
    `;

    // ── Capabilities Section (component-specific) ──
    const capabilitiesHTML = _renderCapabilities(manifest, comp);

    // ── Tool Arguments Section ──
    const toolArgsHTML = _renderToolArguments(toolDef);

    // ── Tabbed Layout ──
    const capIcon = `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z"/></svg>`;
    const argsIcon = `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/><path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg>`;
    const profIcon = `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z"/></svg>`;

    const tabs = [];
    const tabPanels = [];

    if (capabilitiesHTML) {
        // Label follows the content: only the chart component has chart types.
        const capLabel = (manifest.chart_types && Object.keys(manifest.chart_types).length > 0)
            ? 'Chart Types' : 'Capabilities';
        tabs.push({ id: 'comp-tab-cap', label: capLabel, icon: capIcon, panelId: 'comp-panel-cap' });
        tabPanels.push({ id: 'comp-panel-cap', html: capabilitiesHTML });
    }
    if (toolArgsHTML) {
        tabs.push({ id: 'comp-tab-args', label: 'Tool Arguments', icon: argsIcon, panelId: 'comp-panel-args' });
        tabPanels.push({ id: 'comp-panel-args', html: toolArgsHTML });
    }
    // Connections tab — only for the canvas component: points to the one connection manager
    if (comp.component_id === 'canvas') {
        const connIcon = `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4"/></svg>`;
        tabs.push({ id: 'comp-tab-conn', label: 'Connections', icon: connIcon, panelId: 'comp-panel-conn' });
        tabPanels.push({
            id: 'comp-panel-conn',
            html: `<div id="component-connections">
                <div class="flex items-center gap-2 mb-3">
                    <h3 class="text-lg font-semibold" style="color:var(--text-primary)">SQL Connections</h3>
                </div>
                <div class="text-sm" style="color:var(--text-muted)">Loading connections...</div>
            </div>`
        });
    }

    // Libraries tab — only for the skill_library component. Mirrors canvas/Connections
    // and scheduler/Jobs: the component-specific tab lists what this component actually
    // serves — every skill that uses progressive disclosure, its sections, and which
    // agents it is assigned to (editable here, same effect as the profile editor).
    if (comp.component_id === 'skill_library') {
        const libIcon = `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"/></svg>`;
        tabs.push({ id: 'comp-tab-lib', label: 'Libraries', icon: libIcon, panelId: 'comp-panel-lib' });
        tabPanels.push({
            id: 'comp-panel-lib',
            html: `<div id="component-skill-libraries">
                <div class="text-sm" style="color:var(--text-muted)">Loading libraries...</div>
            </div>`
        });
    }

    // Roles tab — for a component whose manifest declares roles (today: Platform): what each
    // role reaches and teaches and who holds it, from the rules the platform enforces.
    if (manifest.roles || comp.roles) {
        const rolesIcon = `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/></svg>`;
        tabs.push({ id: 'comp-tab-roles', label: 'Roles', icon: rolesIcon, panelId: 'comp-panel-roles' });
        tabPanels.push({
            id: 'comp-panel-roles',
            html: `<div id="component-platform-roles"><div class="text-sm" style="color:var(--text-muted)">Loading roles...</div></div>`,
        });
    }

    tabs.push({ id: 'comp-tab-prof', label: 'Profiles', icon: profIcon, panelId: 'comp-panel-prof' });
    tabPanels.push({
        id: 'comp-panel-prof',
        html: `<div id="component-profile-assignments">
            <div class="flex items-center gap-2 mb-3">
                <h3 class="text-lg font-semibold" style="color:var(--text-primary)">Profile Assignments</h3>
            </div>
            <div class="text-sm" style="color:var(--text-muted)">Loading profiles...</div>
        </div>`
    });

    // Jobs tab — only for the scheduler component (after Profiles)
    if (comp.component_id === 'scheduler') {
        const jobsIcon = `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><rect x="2" y="7" width="20" height="14" rx="2"/><path stroke-linecap="round" stroke-linejoin="round" d="M16 7V5a2 2 0 00-2-2h-4a2 2 0 00-2 2v2"/></svg>`;
        tabs.push({ id: 'comp-tab-jobs', label: 'Jobs', icon: jobsIcon, panelId: 'comp-panel-jobs' });
        tabPanels.push({ id: 'comp-panel-jobs', html: '<div id="jobs-tab-root"></div>' });
    }

    // Flows tab — only for the flow component (after Profiles)
    if (comp.component_id === 'flow') {
        const flowsIcon = `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><circle cx="5" cy="5" r="2"/><circle cx="19" cy="5" r="2"/><circle cx="12" cy="19" r="2"/><path stroke-linecap="round" stroke-linejoin="round" d="M7 5h10M12 7v10"/></svg>`;
        tabs.push({ id: 'comp-tab-flows', label: 'Flows', icon: flowsIcon, panelId: 'comp-panel-flows' });
        tabPanels.push({ id: 'comp-panel-flows', html: '<div id="flows-tab-root"></div>' });
    }

    // Dashboards tab — only for the dashboard component (after Profiles)
    if (comp.component_id === 'dashboard') {
        const dashIcon = `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>`;
        tabs.push({ id: 'comp-tab-dashboards', label: 'Dashboards', icon: dashIcon, panelId: 'comp-panel-dashboards' });
        tabPanels.push({ id: 'comp-panel-dashboards', html: '<div id="dashboards-tab-root"></div>' });
    }

    // Canvases tab — only for the canvas component (after Profiles)
    if (comp.component_id === 'canvas') {
        const cnvIcon = `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path stroke-linecap="round" stroke-linejoin="round" d="M3 9h18M9 21V9"/></svg>`;
        tabs.push({ id: 'comp-tab-canvases', label: 'Canvases', icon: cnvIcon, panelId: 'comp-panel-canvases' });
        tabPanels.push({ id: 'comp-panel-canvases', html: '<div id="canvases-tab-root"></div>' });
    }

    // Files tab — only for the files component (after Profiles): the user's files, managed in place
    if (comp.component_id === 'files') {
        const filesIcon = `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2zM9 12h6M9 15h4"/></svg>`;
        tabs.push({ id: 'comp-tab-files', label: 'Files', icon: filesIcon, panelId: 'comp-panel-files' });
        tabPanels.push({ id: 'comp-panel-files', html: '<div id="files-tab-root"></div>' });
    }

    // Knowledge Graphs tab — only for the knowledge_graph component (after Profiles)
    if (comp.component_id === 'knowledge_graph') {
        const kgIcon = `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><circle cx="12" cy="5" r="2"/><circle cx="5" cy="19" r="2"/><circle cx="19" cy="19" r="2"/><path stroke-linecap="round" stroke-linejoin="round" d="M12 7v5M12 12l-5 5M12 12l5 5"/></svg>`;
        tabs.push({ id: 'comp-tab-kgs', label: 'Knowledge Graphs', icon: kgIcon, panelId: 'comp-panel-kgs' });
        tabPanels.push({ id: 'comp-panel-kgs', html: '<div id="kg-tab-root"></div>' });
    }

    const tabBarHTML = tabs.map((t, i) => `
        <button class="ind-tab ind-tab--underline ${i === 0 ? 'active' : ''}"
                style="--tab-color: 6, 182, 212"
                data-tab-target="${t.panelId}"
                id="${t.id}">
            ${t.icon}
            ${t.label}
        </button>
    `).join('');

    const panelsHTML = tabPanels.map((p, i) => `
        <div id="${p.id}" class="${i === 0 ? '' : 'hidden'}">
            ${p.html}
        </div>
    `).join('');

    // ── Assemble ──
    panel.innerHTML = `
        <div class="glass-panel rounded-xl p-6">
            ${headerHTML}
            <div class="flex gap-1 border-b border-white/10 mb-4 mt-2">
                ${tabBarHTML}
            </div>
            ${panelsHTML}
        </div>
    `;
    panel.classList.remove('hidden');

    // Wire tab switching
    panel.querySelectorAll('.ind-tab--underline').forEach(tab => {
        tab.addEventListener('click', () => {
            panel.querySelectorAll('.ind-tab--underline').forEach(t => t.classList.remove('active'));
            tabPanels.forEach(p => {
                const el = document.getElementById(p.id);
                if (el) el.classList.add('hidden');
            });
            tab.classList.add('active');
            const target = document.getElementById(tab.dataset.tabTarget);
            if (target) target.classList.remove('hidden');
        });
    });

    // "Enable on new profiles" toggle — saves per-user preference
    const profileDefaultToggle = panel.querySelector('#comp-profile-default-toggle');
    if (profileDefaultToggle) {
        profileDefaultToggle.addEventListener('change', async () => {
            const componentId = profileDefaultToggle.dataset.componentId;
            const enabled = profileDefaultToggle.checked;
            try {
                await fetch('/api/v1/user/component-profile-defaults', {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${localStorage.getItem('tda_auth_token')}`,
                    },
                    body: JSON.stringify({ [componentId]: enabled }),
                });
            } catch (e) {
                console.error('Failed to save component profile default:', e);
            }
        });
    }

    // Async-load profile assignments (doesn't block the detail render)
    _loadProfileAssignments(comp);

    // Async-load connections tab (canvas component only)
    if (comp.component_id === 'canvas') {
        _loadConnections();
    }

    // Async-load the Libraries tab (skill_library component only)
    if (comp.component_id === 'skill_library') {
        _loadSkillLibraries();
    }

    // Async-load the Roles tab (a component whose manifest declares roles)
    if (manifest.roles || comp.roles) {
        _loadPlatformRoles();
    }

    // Init Jobs tab (scheduler component only)
    if (comp.component_id === 'scheduler') {
        window.jobsHandler?.initJobsTab('jobs-tab-root');
    }

    // Init Flows tab (flow component only). Pass the component object so the tab
    // can drive its instance Export/Import affordances from the manifest
    // (supports_instance_io + icon/accent) rather than hardcoding.
    if (comp.component_id === 'flow') {
        window.flowsHandler?.initFlowsTab('flows-tab-root', comp);
    }

    // Init Dashboards tab (dashboard component only)
    if (comp.component_id === 'dashboard') {
        window.dashboardsHandler?.initDashboardsTab('dashboards-tab-root');
    }

    // Init Canvases tab (canvas component only). Pass the component object so the
    // tab can drive its instance Export/Import affordances from the manifest
    // (supports_instance_io + icon/accent) rather than hardcoding.
    if (comp.component_id === 'canvas') {
        window.canvasesHandler?.initCanvasesTab('canvases-tab-root', comp);
    }

    // Init Files tab (files component only)
    if (comp.component_id === 'files') {
        window.filesTabHandler?.initFilesTab('files-tab-root', comp);
    }

    // Init Knowledge Graphs tab (knowledge_graph component only).
    // knowledgeGraphPanelHandler.js is an ES module (not registered on window),
    // so reuse its management view via dynamic import.
    if (comp.component_id === 'knowledge_graph') {
        import('./knowledgeGraphPanelHandler.js?v=1.189')
            .then(mod => mod.initKgManagementTab?.('kg-tab-root'))
            .catch(err => console.error('[Components] Failed to load KG management tab:', err));
    }
}

/**
 * Render the capabilities section based on component manifest.
 * - chart component: show chart_types table
 * - generic: show key manifest capabilities
 */
function _renderCapabilities(manifest, comp) {
    // Chart component — chart_types table
    if (manifest.chart_types && Object.keys(manifest.chart_types).length > 0) {
        const rows = Object.entries(manifest.chart_types).map(([type, info]) => {
            const required = (info.mapping_roles || []).join(', ') || '\u2014';
            const optional = (info.optional_roles || []).length > 0
                ? info.optional_roles.join(', ')
                : '\u2014';
            return `
                <tr class="border-t border-white/5">
                    <td class="py-2 pr-4 font-mono text-cyan-300 comp-lt-text-cyan">${type}</td>
                    <td class="py-2 pr-4" style="color:var(--text-secondary,#d1d5db)">${info.g2plot_type}</td>
                    <td class="py-2 pr-4" style="color:var(--text-muted)">${required}</td>
                    <td class="py-2" style="color:var(--text-muted)">${optional}</td>
                </tr>
            `;
        }).join('');

        return `
            <div>
                <div class="flex items-center gap-2 mb-3">
                    <h3 class="text-lg font-semibold" style="color:var(--text-primary)">Supported Chart Types</h3>
                    <span class="text-xs" style="color:var(--text-muted)">${Object.keys(manifest.chart_types).length} types</span>
                </div>
                <div class="overflow-x-auto max-h-72 overflow-y-auto">
                    <table class="w-full text-sm">
                        <thead class="sticky top-0 bg-[var(--card-bg,#1a1a2e)]">
                            <tr class="text-left text-xs uppercase tracking-wide" style="color:var(--text-muted)">
                                <th class="pb-2 pr-4">Type</th>
                                <th class="pb-2 pr-4">G2Plot</th>
                                <th class="pb-2 pr-4">Required Roles</th>
                                <th class="pb-2">Optional Roles</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${rows}
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    }

    // Non-chart components: a CURATED capability view.
    // This used to dump every unrecognised key of the API dict — so each component
    // showed internal plumbing (has_handler, globally_disabled, source, agent_pack_id,
    // user_profile_default_enabled) and even the raw icon_svg string, under a tab
    // hardcoded to the label "Chart Types". Only genuinely capability-bearing facts
    // are shown now, and none that the header already states; nothing left to say
    // means NO tab (honest absence) rather than a table of noise.
    const capRows = [];
    const addRow = (label, value) => capRows.push([label, value]);

    const targets = manifest.render_targets?.supports;
    if (Array.isArray(targets) && targets.length) {
        addRow('Render targets', targets.join(', '));
    }
    const enabledFor = manifest.profile_defaults?.enabled_for;
    if (Array.isArray(enabledFor)) {
        addRow('Default-on for', enabledFor.length ? enabledFor.join(', ')
                                                   : 'no profile type (opt-in per profile)');
    }
    // the one recorded exception to "a new agent follows the class default": a component may
    // start NEW agents without it while every existing agent (and its signature) keeps it
    if (manifest.profile_defaults?.on_for_new_agents === false) {
        addRow('New agents', 'start without it — existing agents keep it (their signature untouched)');
    }
    if (manifest.acts_every_turn) {
        addRow('Every turn', 'acts on every turn without being called — active whenever enabled');
    }
    if (manifest.supports_instance_io) {
        addRow('Instance portability', 'export / import / publish supported');
    }
    // Component signals (components/signals.py): how this component talks to the others —
    // the events its renderer raises and follows, the writes its store announces, whether it
    // reads an earlier result by reference. One declaration, read here and by the gate.
    const sig = manifest.signals || {};
    const shortEv = (n) => String(n).replace(/^uderia:/, '');
    if (Array.isArray(sig.emits) && sig.emits.length) addRow('Emits', sig.emits.map(shortEv).join(' · '));
    if (Array.isArray(sig.listens) && sig.listens.length) addRow('Follows', sig.listens.map(shortEv).join(' · '));
    if (Array.isArray(sig.announces) && sig.announces.length) addRow('Announces', sig.announces.join(' · '));
    if (sig.consumes_refs) {
        addRow('Reads by reference', sig.consumes_refs && sig.consumes_refs.implicit
            ? `source_ref · else the ${sig.consumes_refs.implicit.replace('_', ' ')} result`
            : 'source_ref');
    }
    const conflicts = Array.isArray(sig.conflicts_with) && sig.conflicts_with.length ? sig.conflicts_with : manifest.conflicts_with;
    if (Array.isArray(conflicts) && conflicts.length) {
        addRow('Conflicts with', conflicts.join(', '));
    }
    if (manifest.tactical_prompt_rule) {
        addRow('Tactical prompt rule', 'contributes a per-phase planner rule');
    }
    if (manifest.experimental) addRow('Maturity', 'experimental');

    if (capRows.length === 0) return '';

    const propRows = capRows.map(([key, value]) => `
            <tr class="border-t border-white/5">
                <td class="py-2 pr-4" style="color:var(--text-muted)">${_escapeHtml(key)}</td>
                <td class="py-2 font-mono text-cyan-300 comp-lt-text-cyan text-xs">${_escapeHtml(String(value))}</td>
            </tr>
        `).join('');

    return `
        <div>
            <div class="flex items-center gap-2 mb-3">
                <h3 class="text-lg font-semibold" style="color:var(--text-primary)">Capabilities</h3>
            </div>
            <table class="w-full text-sm">
                <thead>
                    <tr class="text-left text-xs uppercase tracking-wide" style="color:var(--text-muted)">
                        <th class="pb-2 pr-4">Capability</th>
                        <th class="pb-2">Detail</th>
                    </tr>
                </thead>
                <tbody>
                    ${propRows}
                </tbody>
            </table>
        </div>
    `;
}

/**
 * Render the tool arguments table from the tool definition.
 */
function _renderToolArguments(toolDef) {
    if (!toolDef.args || Object.keys(toolDef.args).length === 0) return '';

    const rows = Object.entries(toolDef.args).map(([argName, argDef]) => {
        const required = argDef.required
            ? '<span class="text-emerald-400 comp-lt-emerald" style="background:none">&#10003;</span>'
            : `<span style="color:var(--text-muted)">\u2014</span>`;
        return `
            <tr class="border-t border-white/5">
                <td class="py-2 pr-4 font-mono text-cyan-300 comp-lt-text-cyan">${argName}</td>
                <td class="py-2 pr-4" style="color:var(--text-muted)">${argDef.type || 'string'}</td>
                <td class="py-2 pr-4 text-center">${required}</td>
                <td class="py-2 text-xs" style="color:var(--text-muted)">${argDef.description || ''}</td>
            </tr>
        `;
    }).join('');

    return `
        <div>
            <div class="flex items-center gap-2 mb-3">
                <h3 class="text-lg font-semibold" style="color:var(--text-primary)">Tool Arguments</h3>
                <span class="text-xs font-mono text-cyan-300 comp-lt-text-cyan">${toolDef.name || ''}</span>
            </div>
            <div class="overflow-x-auto">
                <table class="w-full text-sm">
                    <thead>
                        <tr class="text-left text-xs uppercase tracking-wide" style="color:var(--text-muted)">
                            <th class="pb-2 pr-4">Argument</th>
                            <th class="pb-2 pr-4">Type</th>
                            <th class="pb-2 pr-4 text-center">Required</th>
                            <th class="pb-2">Description</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${rows}
                    </tbody>
                </table>
            </div>
        </div>
    `;
}

// ─── Skill Libraries (skill_library component) ──────────────────────────────
/** This module's auth-header idiom (localStorage token), kept local to it. */
function _libAuthHeaders() {
    return { 'Authorization': `Bearer ${localStorage.getItem('tda_auth_token')}` };
}

/** Fill each component card's roles line ("Roles · 4 active · 4 planned"). */
async function _fillRoleLines(grid) {
    const lines = grid.querySelectorAll('[data-roles-line]');
    if (!lines.length) return;
    try {
        const data = await fetchRoles(false);
        const roles = data.roles || [];
        const active = roles.filter(r => r.status === 'active').length;
        const planned = roles.length - active;
        lines.forEach(el => {
            el.textContent = `Roles · ${active} active${planned ? ` · ${planned} planned` : ''} — playbooks from ${(data.declaration || {}).playbooks || 'its skill'}`;
        });
    } catch {
        lines.forEach(el => { el.textContent = 'Roles · could not be read'; });
    }
}

/**
 * The Roles tab: what each role is for, what it reaches and teaches (from the rules the
 * platform enforces — GET /v1/platform/roles?detail=1), and which agents hold it. Read-only:
 * a role is chosen per agent in the agent editor.
 */
async function _loadPlatformRoles() {
    const root = document.getElementById('component-platform-roles');
    if (!root) return;
    try {
        const data = await fetchRoles(true);
        const roles = data.roles || [];
        const pill = (text, color) => `<span class="text-[10px] font-medium px-2 py-0.5 rounded whitespace-nowrap"
            style="background:${color}1f;color:${color};border:1px solid ${color}40">${_rolesEsc(text)}</span>`;
        root.innerHTML = `
            <div class="flex items-center gap-2 mb-1">
                <h3 class="text-lg font-semibold" style="color:var(--text-primary)">Roles</h3>
                <span class="text-xs" style="color:var(--text-muted)">${roles.filter(r => r.status === 'active').length} active · ${roles.filter(r => r.status !== 'active').length} planned</span>
            </div>
            <p class="text-xs mb-3" style="color:var(--text-muted)">${declarationLine(data)}</p>
            <div class="space-y-3">${roles.map(r => `
                <div class="rounded-lg p-3" data-role-card="${_rolesEsc(r.id)}"
                     style="background:var(--hover-bg,rgba(148,163,184,0.05));border:1px solid var(--border-secondary,rgba(148,163,184,0.15));${r.status !== 'active' ? 'opacity:0.7;' : ''}">
                    <div class="flex items-center gap-2 flex-wrap">
                        <span class="text-sm font-medium" style="color:var(--text-primary)">${_rolesEsc(r.title)}</span>
                        ${r.status === 'active' ? pill('active', '#22c55e') : pill('planned', '#94a3b8')}
                        ${pill((r.effects || []).includes('write') ? 'reads and may change' : 'reads', '#38bdf8')}
                        ${(data.default || []).includes(r.id) ? pill('default', '#a78bfa') : ''}
                        <span class="ml-auto text-[10px] font-mono" style="color:var(--text-muted)" title="What it grants, hashed into the signature of every agent that holds it">${_rolesEsc(r.digest || '')}</span>
                    </div>
                    <p class="text-xs mt-1" style="color:var(--text-muted)">${_rolesEsc(r.description)}</p>
                    <p class="text-xs mt-1" style="color:var(--text-primary)">${_rolesEsc(countsLine(r))}</p>
                    ${reachHTML(r)}
                </div>`).join('')}
            </div>`;
    } catch (e) {
        root.innerHTML = `<p class="text-sm" style="color:#f87171">Could not load the roles: ${_escapeHtml(String(e))}</p>`;
    }
}

/**
 * The Libraries tab: every skill that uses progressive disclosure, what it carries,
 * and which agents it is assigned to — assignable here, exactly as in the profile
 * editor (same `skillsConfig.skills[]` entry, same effect on the runtime).
 *
 * Component-specific tab, mirroring canvas/Connections and scheduler/Jobs: the
 * generic Profiles tab governs the COMPONENT's enablement; this governs the
 * LIBRARIES the component serves.
 */
async function _loadSkillLibraries() {
    const root = document.getElementById('component-skill-libraries');
    if (!root) return;
    try {
        const [skillsRes, profilesRes] = await Promise.all([
            fetch('/api/v1/skills', { headers: _libAuthHeaders() }),
            fetch('/api/v1/profiles', { headers: _libAuthHeaders() }),
        ]);
        if (!skillsRes.ok || !profilesRes.ok) throw new Error('load failed');
        const skills = ((await skillsRes.json()).skills || []).filter(s => s.progressive);
        const pdata = await profilesRes.json();
        const profiles = Array.isArray(pdata) ? pdata : (pdata.profiles || []);
        // The skill whose playbooks the Platform roles slice: which role is taught which section.
        let roles = null;
        try { roles = await fetchRoles(true); } catch { roles = null; }
        _libRoles = roles;

        if (!skills.length) {
            root.innerHTML = `
                <div class="flex items-center gap-2 mb-3">
                    <h3 class="text-lg font-semibold" style="color:var(--text-primary)">Skill Libraries</h3>
                </div>
                <p class="text-sm" style="color:var(--text-muted)">
                    No installed skill uses progressive disclosure yet. A skill becomes one by
                    shipping a <span class="font-mono">library/</span> subtree and an anchor index;
                    this component then serves its sections on demand
                    (<span class="font-mono">TDA_SkillLibrary</span>) — and stays inert everywhere else.
                </p>`;
            return;
        }

        root.innerHTML = `
            <div class="flex items-center gap-2 mb-3">
                <h3 class="text-lg font-semibold" style="color:var(--text-primary)">Skill Libraries</h3>
                <span class="text-xs" style="color:var(--text-muted)">${skills.length} skill${skills.length === 1 ? '' : 's'}</span>
            </div>
            <div class="space-y-3">${skills.map(sk => _skillLibraryCard(sk, profiles, roles)).join('')}</div>`;

        _wireSkillLibraryCards(root, skills, profiles);
    } catch (e) {
        root.innerHTML = `<p class="text-sm" style="color:#f87171">Could not load skill libraries: ${_escapeHtml(String(e))}</p>`;
    }
}

/** Which profiles carry this skill, and how wide their library grant is. */
function _skillAssignments(skillId, profiles) {
    return profiles.map(p => {
        const entry = ((p.skillsConfig || {}).skills || []).find(x => x.id === skillId);
        const anchors = entry && Array.isArray(entry.anchors) ? entry.anchors : null;
        return {
            id: p.id, tag: p.tag, name: p.name, type: p.profile_type,
            assigned: !!(entry && entry.active),
            grant: anchors === null ? null : anchors.length,
        };
    });
}

let _libRoles = null;

/** For the skill the Platform roles slice: anchor → the titles of the roles taught it. */
function _taughtTo(skillId, roles) {
    if (!roles || (roles.declaration || {}).playbooks !== skillId) return null;
    const title = Object.fromEntries((roles.roles || []).map(r => [r.id, r.title]));
    return Object.fromEntries((roles.playbooks || []).map(p => [p.anchor, (p.taught_to || []).map(id => title[id] || id)]));
}

function _skillLibraryCard(sk, profiles, roles) {
    const rows = _skillAssignments(sk.skill_id, profiles);
    const taught = _taughtTo(sk.skill_id, roles);
    const rolesHtml = taught
        ? `<div class="mt-2 text-xs" style="color:var(--text-muted)">Sliced by the Platform component's roles: ${(roles.roles || [])
              .filter(r => r.status === 'active').map(r => `<span style="color:var(--text-primary)">${_rolesEsc(r.title)}</span> ${(r.counts || {}).playbooks || 0}`)
              .join(' · ')} — open Sections to see which role is taught each playbook.</div>`
        : '';
    const on = rows.filter(r => r.assigned);
    const packTag = sk.pack_name
        ? `<span class="text-[10px] font-medium px-2 py-0.5 rounded whitespace-nowrap" style="background:${sk.pack_orphaned ? 'rgba(245,158,11,0.12);color:#fbbf24;border:1px solid rgba(245,158,11,0.25)' : 'rgba(168,85,247,0.12);color:#c084fc;border:1px solid rgba(168,85,247,0.25)'}">${sk.pack_orphaned ? 'Orphaned' : 'Pack'} &middot; ${_escapeHtml(sk.pack_name)}</span>`
        : '';
    // Each chip is the ENTRY POINT to that agent's library slice — the same dialog
    // the profile editor opens, so the boundary can be set from whichever view the
    // question arose in ("what does this agent get?" vs "who gets this library?").
    const assignHtml = on.length
        ? on.map(r => `<button type="button" data-lib-grant="${_escapeHtml(sk.skill_id)}" data-profile-id="${_escapeHtml(r.id)}"
                class="text-[10px] font-mono px-2 py-0.5 rounded transition-colors"
                title="${r.grant === null ? 'Full library access — click to restrict' : r.grant + ' of ' + sk.library_sections + ' sections granted — click to change'}"
                style="background:${r.grant === null ? 'rgba(59,130,246,0.12);color:#60a5fa;border:1px solid rgba(59,130,246,0.25)' : 'rgba(245,158,11,0.12);color:#fbbf24;border:1px solid rgba(245,158,11,0.25)'}">@${_escapeHtml(r.tag)} · ${r.grant === null ? 'all' : r.grant + '/' + sk.library_sections}</button>`).join(' ')
        : `<span class="text-xs" style="color:var(--text-muted)">assigned to no agent</span>`;

    return `
        <div class="rounded-lg p-3" data-lib-card="${_escapeHtml(sk.skill_id)}"
             style="background:var(--hover-bg,rgba(148,163,184,0.05));border:1px solid var(--border-secondary,rgba(148,163,184,0.15))">
            <div class="flex items-center gap-2 flex-wrap">
                <span class="text-sm font-medium" style="color:var(--text-primary)">${_escapeHtml(sk.name || sk.skill_id)}</span>
                <span class="text-[10px] font-medium px-2 py-0.5 rounded whitespace-nowrap"
                      style="background:rgba(14,165,233,0.12);color:#38bdf8;border:1px solid rgba(14,165,233,0.25)">Library &middot; ${sk.library_sections} section${sk.library_sections === 1 ? '' : 's'}</span>
                ${packTag}
                <button type="button" data-lib-sections="${_escapeHtml(sk.skill_id)}"
                        class="ml-auto text-xs px-2 py-0.5 rounded transition-colors"
                        style="background:var(--input-bg);border:1px solid var(--border-primary);color:var(--text-muted)">Sections</button>
            </div>
            <div class="mt-2 flex items-center gap-1.5 flex-wrap">
                <span class="text-xs" style="color:var(--text-muted)">Assigned:</span>${assignHtml}
            </div>
            ${rolesHtml}
            <div data-lib-sections-panel="${_escapeHtml(sk.skill_id)}" class="hidden mt-2 pt-2 max-h-48 overflow-y-auto"
                 style="border-top:1px solid var(--border-secondary)"></div>
            <div class="mt-2 pt-2" style="border-top:1px solid var(--border-secondary)">
                <div class="text-xs mb-1.5" style="color:var(--text-muted)">Assign to agents — same entry the profile editor writes</div>
                <div class="space-y-1 max-h-48 overflow-y-auto">
                    ${rows.map(r => `
                        <label class="flex items-center gap-2 text-xs cursor-pointer py-0.5" style="color:var(--text-primary)">
                            <input type="checkbox" data-lib-assign="${_escapeHtml(sk.skill_id)}" data-profile-id="${_escapeHtml(r.id)}" ${r.assigned ? 'checked' : ''}>
                            <span class="font-mono" style="color:#60a5fa">@${_escapeHtml(r.tag)}</span>
                            <span class="truncate" style="color:var(--text-muted)">${_escapeHtml(r.name || '')}</span>
                            ${r.assigned && r.grant !== null ? `<span class="text-[10px] ml-auto" style="color:#fbbf24">${r.grant}/${sk.library_sections} sections</span>` : ''}
                        </label>`).join('')}
                </div>
            </div>
        </div>`;
}

function _wireSkillLibraryCards(root, skills, profiles) {
    const byId = Object.fromEntries(skills.map(s => [s.skill_id, s]));

    // Sections disclosure — the library's own contents, fetched on demand
    root.querySelectorAll('[data-lib-sections]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const sid = btn.dataset.libSections;
            const panel = root.querySelector(`[data-lib-sections-panel="${sid}"]`);
            if (!panel) return;
            const opening = panel.classList.contains('hidden');
            panel.classList.toggle('hidden');
            if (!opening || panel.dataset.loaded === '1') return;
            panel.innerHTML = `<p class="text-xs" style="color:var(--text-muted)">Loading sections…</p>`;
            try {
                const res = await fetch(`/api/v1/skills/${encodeURIComponent(sid)}/library-index`, { headers: _libAuthHeaders() });
                const anchors = res.ok ? ((await res.json()).anchors || []) : [];
                const taught = _taughtTo(sid, _libRoles);
                panel.innerHTML = anchors.length
                    ? anchors.map(a => `<div class="text-xs py-0.5"><span class="font-mono" style="color:#38bdf8">${_escapeHtml(a.anchor)}</span>${a.title && a.title !== a.anchor ? `<span style="color:var(--text-muted)"> — ${_escapeHtml(a.title)}</span>` : ''}${
                        taught ? `<span data-taught-to="${_escapeHtml(a.anchor)}" style="color:var(--text-muted)"> · ${(taught[a.anchor] || []).length ? 'taught to ' + _escapeHtml(taught[a.anchor].join(', ')) : 'taught to no role'}</span>` : ''}</div>`).join('')
                    : `<p class="text-xs" style="color:var(--text-muted)">No sections declared.</p>`;
                panel.dataset.loaded = '1';
            } catch {
                panel.innerHTML = `<p class="text-xs" style="color:#f87171">Could not load sections.</p>`;
            }
        });
    });

    // Grant chips — open the shared dialog and persist through the same partial PUT.
    root.querySelectorAll('[data-lib-grant]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const sid = btn.dataset.libGrant, pid = btn.dataset.profileId;
            const prof = profiles.find(p => p.id === pid);
            const sk = byId[sid];
            if (!prof || !sk || !window.openSkillGrantDialog) return;
            const entry = ((prof.skillsConfig || {}).skills || []).find(x => x.id === sid) || {};
            window.openSkillGrantDialog({
                skillId: sid, skillName: sk.name || sid, totalSections: sk.library_sections || 0,
                profileTag: prof.tag,
                anchors: Array.isArray(entry.anchors) ? entry.anchors : null,
                onSave: async (grant) => {
                    const cfg = JSON.parse(JSON.stringify(prof.skillsConfig || { skills: [] }));
                    cfg.skills = cfg.skills || [];
                    let e2 = cfg.skills.find(x => x.id === sid);
                    if (!e2) { e2 = { id: sid, enabled: true, active: true, param: null }; cfg.skills.push(e2); }
                    if (grant === null) delete e2.anchors; else e2.anchors = grant;
                    const res = await fetch(`/api/v1/profiles/${encodeURIComponent(pid)}`, {
                        method: 'PUT', headers: { ..._libAuthHeaders(), 'Content-Type': 'application/json' },
                        body: JSON.stringify({ skillsConfig: cfg }),
                    });
                    if (!res.ok) throw new Error(`HTTP ${res.status}`);
                    prof.skillsConfig = cfg;
                    if (window.showAppBanner) {
                        window.showAppBanner(`Library access updated for @${prof.tag}`, 'success', 2500);
                    }
                    _loadSkillLibraries();
                },
            });
        });
    });

    // Assignment toggles — write the SAME skillsConfig entry the profile editor writes.
    // A partial PUT is merged server-side, so nothing else on the profile is touched;
    // an existing entry (and its anchor grant) is preserved, never rebuilt.
    root.querySelectorAll('[data-lib-assign]').forEach(cb => {
        cb.addEventListener('change', async () => {
            const sid = cb.dataset.libAssign;
            const pid = cb.dataset.profileId;
            const want = cb.checked;
            cb.disabled = true;
            try {
                const prof = profiles.find(p => p.id === pid);
                const cfg = JSON.parse(JSON.stringify(prof.skillsConfig || { skills: [] }));
                cfg.skills = cfg.skills || [];
                const existing = cfg.skills.find(x => x.id === sid);
                if (want) {
                    if (existing) { existing.enabled = true; existing.active = true; }
                    else cfg.skills.push({ id: sid, enabled: true, active: true, param: null });
                } else if (existing) {
                    existing.active = false;   // keep the entry: its anchor grant survives
                }
                const res = await fetch(`/api/v1/profiles/${encodeURIComponent(pid)}`, {
                    method: 'PUT', headers: { ..._libAuthHeaders(), 'Content-Type': 'application/json' },
                    body: JSON.stringify({ skillsConfig: cfg }),
                });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                prof.skillsConfig = cfg;
                if (window.showAppBanner) {
                    window.showAppBanner(`${byId[sid]?.name || sid} ${want ? 'assigned to' : 'removed from'} @${prof.tag}`, 'success', 2500);
                }
                _loadSkillLibraries();   // re-render so the summary chips stay truthful
            } catch (e) {
                cb.checked = !want;
                if (window.showAppBanner) window.showAppBanner(`Could not update assignment: ${e}`, 'error');
            } finally {
                cb.disabled = false;
            }
        });
    });
}

// ─── Profile Assignments ────────────────────────────────────────────────────

/**
 * Async-load profiles and render the Profile Assignments section.
 * Called after the detail panel is rendered so it doesn't block.
 */
async function _loadProfileAssignments(comp) {
    const container = document.getElementById('component-profile-assignments');
    if (!container) return;

    try {
        const token = localStorage.getItem('tda_auth_token');
        const resp = await fetch('/api/v1/profiles', {
            headers: { 'Authorization': `Bearer ${token}` }
        });

        if (!resp.ok) {
            container.querySelector('div:last-child').textContent = 'Failed to load profiles';
            return;
        }

        const data = await resp.json();
        const profiles = data.profiles || [];

        if (profiles.length === 0) {
            container.querySelector('div:last-child').textContent = 'No profiles configured';
            return;
        }

        const isAction = comp.component_type === 'action';
        const componentId = comp.component_id;

        const sortedProfiles = [...profiles].sort((a, b) => {
            const ia = IFOC_ORDER.indexOf(a.profile_type);
            const ib = IFOC_ORDER.indexOf(b.profile_type);
            return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
        });

        const IFOC_HEX = { llm_only: '#4ade80', rag_focused: '#3b82f6', tool_enabled: '#9333ea', genie: '#F15F22' };

        function _buildProfileRow(profile) {
            const compConfig = (profile.componentConfig || {})[componentId] || {};
            const isEnabled = compConfig.enabled !== undefined ? compConfig.enabled : (comp.user_profile_default_enabled ?? false);
            const intensity = compConfig.intensity || comp.profile_defaults?.default_intensity || 'medium';

            const intensityLevels = (comp.manifest?.instructions?.intensity_levels || ['none', 'medium', 'heavy'])
                .filter(l => l !== 'none');
            const intensityOptions = intensityLevels.map(level => {
                const label = level.charAt(0).toUpperCase() + level.slice(1);
                return `<option value="${level}" ${intensity === level ? 'selected' : ''}>${label}</option>`;
            }).join('');
            const manifestTooltips = comp.manifest?.instructions?.intensity_tooltips || {};
            const intensityTooltip = intensityLevels
                .map(l => `${l.charAt(0).toUpperCase() + l.slice(1)} — ${manifestTooltips[l] || (l === 'medium' ? 'Use when appropriate' : 'Proactively use at every opportunity')}`)
                .join('\n');
            const isScheduler = componentId === 'scheduler';
            const intensitySelect = (isAction && !isScheduler) ? `
                <select class="profile-comp-intensity text-xs bg-gray-800 border border-gray-600 text-gray-300 comp-lt-select rounded px-2 py-1"
                        data-profile-id="${profile.id}" ${!isEnabled ? 'disabled' : ''}
                        data-tooltip="${intensityTooltip}">
                    ${intensityOptions}
                </select>` : '';
            const taskCountBadge = isScheduler
                ? `<span class="profile-sched-task-count" data-profile-id="${profile.id}" style="font-size:11px;padding:2px 10px;border-radius:20px;background:rgba(168,85,247,0.08);border:1px solid rgba(168,85,247,0.2);color:var(--text-muted,#94a3b8);white-space:nowrap;">…</span>`
                : '';

            return `
                <div class="flex items-center justify-between py-2 px-3 rounded-lg bg-gray-800/30 border border-gray-700/20 comp-lt-row"
                     data-profile-id="${profile.id}" data-profile-tag="${_escapeHtml(profile.tag || '')}">
                    <div class="flex items-center gap-3 min-w-0">
                        <span class="text-xs font-mono px-2 py-0.5 rounded bg-cyan-500/20 text-cyan-300 comp-lt-cyan flex-shrink-0">@${_escapeHtml(profile.tag || '?')}</span>
                        <span class="text-sm truncate" style="color:var(--text-primary)">${_escapeHtml(profile.name || profile.id)}</span>
                    </div>
                    <div class="flex items-center gap-3 flex-shrink-0">
                        ${intensitySelect}
                        ${taskCountBadge}
                        <label class="ind-toggle ind-toggle--sm">
                            <input type="checkbox" class="profile-comp-toggle"
                                   data-profile-id="${profile.id}" ${isEnabled ? 'checked' : ''}>
                            <span class="ind-track"></span>
                        </label>
                    </div>
                </div>
            `;
        }

        // Group by IFOC type (already sorted)
        const groups = {};
        sortedProfiles.forEach(p => {
            const t = p.profile_type || 'llm_only';
            (groups[t] = groups[t] || []).push(p);
        });
        const groupsHtml = IFOC_ORDER
            .filter(t => groups[t])
            .map(t => {
                const color = IFOC_HEX[t] || '#9ca3af';
                const label = IFOC_CONFIG[t]?.label || t;
                return `
                <div class="comp-profile-group mb-4 last:mb-0" data-ifoc-type="${t}">
                    <div class="flex items-center gap-2 mb-2">
                        <span class="text-[10px] font-semibold uppercase tracking-widest"
                              style="color:${color}">${label}</span>
                        <div class="flex-1 h-px" style="background:${color}22"></div>
                        <span class="text-[10px]" style="color:var(--text-muted)">${groups[t].length}</span>
                    </div>
                    <div class="space-y-1.5">
                        ${groups[t].map(_buildProfileRow).join('')}
                    </div>
                </div>`;
            }).join('');

        // Filter chips — one per present IFOC type + "All"
        const typeChips = IFOC_ORDER
            .filter(t => groups[t])
            .map(t => {
                const c = IFOC_HEX[t] || '#9ca3af';
                return `<button class="filter-pill comp-ifoc-chip" data-type="${t}" data-color="${c}"
                                onclick="window._compFilterByType(this,'${t}')"
                                style="font-size:11px">${IFOC_CONFIG[t]?.label || t}</button>`;
            }).join('');

        // Replace the loading placeholder, keep the header
        container.innerHTML = `
            <div class="flex items-center gap-2 mb-3">
                <h3 class="text-lg font-semibold" style="color:var(--text-primary)">Profile Assignments</h3>
                <span class="text-xs" style="color:var(--text-muted)">${profiles.length} profiles</span>
            </div>
            <div class="flex items-center gap-1.5 flex-wrap mb-2">
                <button class="filter-pill filter-pill--active comp-ifoc-chip" data-type="all"
                        onclick="window._compFilterByType(this,'all')"
                        style="font-size:11px">All</button>
                ${typeChips}
                <div class="ml-auto flex items-center gap-1">
                    <button onclick="window._compSelectAll(true)"
                            style="font-size:11px;padding:3px 10px;border-radius:6px;border:1px solid var(--border-secondary);
                                   background:transparent;color:var(--text-muted);cursor:pointer;transition:background .12s,color .12s;"
                            onmouseover="this.style.background='rgba(255,255,255,0.07)';this.style.color='var(--text-primary)'"
                            onmouseout="this.style.background='transparent';this.style.color='var(--text-muted)'">
                        Select all
                    </button>
                    <button onclick="window._compSelectAll(false)"
                            style="font-size:11px;padding:3px 10px;border-radius:6px;border:1px solid var(--border-secondary);
                                   background:transparent;color:var(--text-muted);cursor:pointer;transition:background .12s,color .12s;"
                            onmouseover="this.style.background='rgba(255,255,255,0.07)';this.style.color='var(--text-primary)'"
                            onmouseout="this.style.background='transparent';this.style.color='var(--text-muted)'">
                        Unselect all
                    </button>
                </div>
            </div>
            <div class="relative mb-3">
                <input type="text" id="comp-profile-search" placeholder="Search profiles…"
                       oninput="window._compApplyFilters()"
                       class="w-full text-xs px-3 py-1.5 rounded-lg pr-8"
                       style="background:rgba(255,255,255,0.05);border:1px solid var(--border-secondary);
                              color:var(--text-primary);outline:none;">
                <svg class="absolute right-2.5 top-1/2 -translate-y-1/2 w-3 h-3 pointer-events-none"
                     style="color:var(--text-muted)" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                          d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
                </svg>
            </div>
            <div id="comp-profile-groups">
                ${groupsHtml}
            </div>
        `;

        // Wire toggle handlers
        container.querySelectorAll('.profile-comp-toggle').forEach(toggle => {
            toggle.addEventListener('change', async (e) => {
                const profileId = e.target.dataset.profileId;
                const enabled = e.target.checked;
                const row = e.target.closest('.flex.items-center.justify-between');
                const intensitySelect = row?.querySelector('.profile-comp-intensity');
                if (intensitySelect) intensitySelect.disabled = !enabled;

                await _updateProfileComponentConfig(profileId, componentId, { enabled }, profiles);

                // For the Knowledge Graph component, refresh its management tab so the
                // "assignment inactive — component disabled" warnings update immediately
                // (the enabled-map is re-fetched), without needing a page reload.
                if (componentId === 'knowledge_graph' && document.getElementById('kg-comp-grid-container')) {
                    import('./knowledgeGraphPanelHandler.js?v=1.189')
                        .then(mod => mod.loadKnowledgeGraphsIntelligenceTab?.({
                            gridId: 'kg-comp-grid-container',
                            filterId: 'kg-comp-active-filter',
                            tabBtnId: null,
                        }))
                        .catch(err => console.error('[Components] Failed to refresh KG tab after toggle:', err));
                }
            });
        });

        // Wire intensity handlers (not used for scheduler)
        container.querySelectorAll('.profile-comp-intensity').forEach(select => {
            select.addEventListener('change', async (e) => {
                const profileId = e.target.dataset.profileId;
                const intensity = e.target.value;
                await _updateProfileComponentConfig(profileId, componentId, { intensity }, profiles);
            });
        });

        // For scheduler: fetch task counts per profile asynchronously
        if (componentId === 'scheduler') {
            const badges = container.querySelectorAll('.profile-sched-task-count');
            badges.forEach(async badge => {
                const profileId = badge.dataset.profileId;
                try {
                    const token = localStorage.getItem('tda_auth_token') || '';
                    const resp = await fetch(`/api/v1/scheduled-tasks?profile_id=${encodeURIComponent(profileId)}`, {
                        headers: token ? { 'Authorization': `Bearer ${token}` } : {}
                    });
                    if (resp.ok) {
                        const data = await resp.json();
                        const count = (data.tasks || []).length;
                        badge.textContent = count === 0 ? 'No tasks' : `${count} task${count !== 1 ? 's' : ''}`;
                        if (count > 0) {
                            badge.style.background = 'rgba(168,85,247,0.15)';
                            badge.style.borderColor = 'rgba(168,85,247,0.4)';
                            badge.style.color = '#c084fc';
                        } else {
                            badge.style.color = 'var(--text-muted, #94a3b8)';
                        }
                    } else {
                        badge.textContent = '';
                    }
                } catch (_) { badge.textContent = ''; }
            });
        }

    } catch (err) {
        console.error('[ComponentHandler] Error loading profiles:', err);
        const placeholder = container.querySelector('div:last-child');
        if (placeholder) placeholder.textContent = 'Error loading profiles';
    }
}

/**
 * Update a single component's config within a profile and save via PUT.
 */
async function _updateProfileComponentConfig(profileId, componentId, updates, profiles) {
    try {
        const profile = profiles.find(p => p.id === profileId);
        if (!profile) return;

        const config = { ...(profile.componentConfig || {}) };
        config[componentId] = { ...(config[componentId] || {}), ...updates };

        // Update the in-memory copy so subsequent changes stack correctly
        profile.componentConfig = config;

        const token = localStorage.getItem('tda_auth_token');
        const resp = await fetch(`/api/v1/profiles/${profileId}`, {
            method: 'PUT',
            headers: {
                'Authorization': `Bearer ${token}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ componentConfig: config })
        });

        if (!resp.ok) {
            console.error('[ComponentHandler] Failed to update profile:', resp.status);
            if (window.showNotification) {
                window.showNotification('error', 'Failed to update profile');
            }
        } else {
            if (window.configState?.loadProfiles) window.configState.loadProfiles();  // fire-and-forget — sync shared cache
            // The Workspace and the enablement gate follow at once (no page reload).
            UderiaBus.emit('uderia:components-changed', { profileId });
        }
    } catch (err) {
        console.error('[ComponentHandler] Error updating profile component config:', err);
        if (window.showNotification) {
            window.showNotification('error', 'Error updating profile');
        }
    }
}

// ─── Canvas · database connections (managed in one place) ──────────────────
// The Canvas reads the one Database Connection store (Components → Connectors → Database
// Connection) through its connection picker. It used to carry a second full connection
// manager here — a duplicate UI over the same store — so this tab now points there.
function _loadConnections() {
    const container = document.getElementById('component-connections');
    if (!container) return;
    container.innerHTML = `
        <div class="flex items-center gap-2 mb-3">
            <h3 class="text-lg font-semibold" style="color:var(--text-primary)">Database connections</h3>
        </div>
        <p class="text-sm" style="color:var(--text-muted);max-width:640px;">
            The canvas runs SQL on your database connections, picked from the connection menu in its
            toolbar. Connections are managed in one place and shared by the canvas, the Database
            Navigator and PDM deployment.
        </p>
        <button type="button" class="comp-open-db-connections card-btn mt-3">Manage database connections →</button>`;
    container.querySelector('.comp-open-db-connections')?.addEventListener('click', () => {
        if (typeof window.openPlatformConnector === 'function') window.openPlatformConnector('uderia-database');
    });
}

// ─── Detail Panel Helpers ───────────────────────────────────────────────────

function _hideDetailPanel() {
    const panel = document.getElementById(DETAIL_ID);
    if (panel) {
        panel.classList.add('hidden');
        panel.innerHTML = '';
    }
    _selectedComponentId = null;
}

function _showDetailError(message) {
    const panel = document.getElementById(DETAIL_ID);
    if (panel) {
        panel.innerHTML = `<div class="glass-panel rounded-xl p-6 text-center text-red-400">${message}</div>`;
        panel.classList.remove('hidden');
    }
}

function _escapeHtml(str) {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ─── Event Handlers (Reload, Filters) ───────────────────────────────────────

/**
 * Initialize event handlers for the top-level Components view (once).
 */
function _initializeHandlers() {
    // Reload button
    const reloadBtn = document.getElementById(RELOAD_BTN_ID);
    if (reloadBtn) {
        reloadBtn.addEventListener('click', async () => {
            reloadBtn.disabled = true;
            try {
                const token = localStorage.getItem('tda_auth_token');
                const resp = await fetch('/api/v1/components/reload', {
                    method: 'POST',
                    headers: { 'Authorization': `Bearer ${token}` }
                });
                const data = await resp.json();
                if (resp.ok && data.status === 'success') {
                    if (window.showNotification) {
                        window.showNotification('success', `Reloaded ${data.loaded} component(s)`);
                    }
                    await _loadComponents();
                } else {
                    if (window.showNotification) {
                        window.showNotification('error', 'Failed to reload components');
                    }
                }
            } catch (err) {
                console.error('[ComponentHandler] Reload error:', err);
            } finally {
                reloadBtn.disabled = false;
            }
        });
    }

    // Category filter pills
    _setupFilterPills(CATEGORY_FILTERS_ID, (filter) => {
        _filterComponentCards('type', filter);
    });

    // Status filter pills
    _setupFilterPills(STATUS_FILTERS_ID, (filter) => {
        _filterComponentCards('status', filter);
    });
}

/**
 * Setup filter pill click handlers for a pill container.
 */
function _setupFilterPills(containerId, onFilter) {
    const container = document.getElementById(containerId);
    if (!container) return;

    container.querySelectorAll('.filter-pill').forEach(pill => {
        pill.addEventListener('click', () => {
            container.querySelectorAll('.filter-pill').forEach(p => p.classList.remove('filter-pill--active'));
            pill.classList.add('filter-pill--active');
            onFilter(pill.dataset.filter);
        });
    });
}

/**
 * Filter component cards by type or status.
 */
function _filterComponentCards(dimension, filter) {
    const grid = document.getElementById(GRID_ID);
    if (!grid) return;

    grid.querySelectorAll('.component-card').forEach(card => {
        let visible = true;

        if (dimension === 'type' && filter !== 'all') {
            visible = card.dataset.componentType === filter;
        }
        if (dimension === 'status' && filter !== 'all') {
            const isDisabled = card.dataset.globallyDisabled === 'true';
            visible = filter === 'active' ? !isDisabled : isDisabled;
        }

        card.style.display = visible ? '' : 'none';
    });
}

// ── IFOC type filter + text search ────────────────────────────────────────────

window._compFilterByType = function(chip, type) {
    chip.closest('.flex').querySelectorAll('.comp-ifoc-chip').forEach(c => {
        c.classList.remove('filter-pill--active');
        c.style.background = '';
        c.style.color = '';
        c.style.borderColor = '';
    });
    chip.classList.add('filter-pill--active');
    const color = chip.dataset.color;
    if (color) {
        chip.style.background = `${color}20`;
        chip.style.color = color;
        chip.style.borderColor = `${color}50`;
    }
    window._compApplyFilters();
};

window._compApplyFilters = function() {
    const groups = document.getElementById('comp-profile-groups');
    if (!groups) return;
    const activeChip = document.querySelector('.comp-ifoc-chip.filter-pill--active');
    const activeType = activeChip?.dataset.type || 'all';
    const q = (document.getElementById('comp-profile-search')?.value || '').toLowerCase().trim();

    groups.querySelectorAll('.comp-profile-group').forEach(group => {
        const typeMatch = activeType === 'all' || group.dataset.ifocType === activeType;
        let anyRowVisible = false;
        group.querySelectorAll('[data-profile-tag]').forEach(row => {
            const tagMatch = !q || (row.dataset.profileTag || '').toLowerCase().includes(q);
            row.style.display = tagMatch ? '' : 'none';
            if (tagMatch) anyRowVisible = true;
        });
        group.style.display = (typeMatch && anyRowVisible) ? '' : 'none';
    });
};

// Toggles all currently visible profile rows on or off.
// Respects the active IFOC filter + search — "Select all" on the Ideate filter
// only touches Ideate profiles, leaving all others unchanged.
window._compSelectAll = function(enable) {
    const groups = document.getElementById('comp-profile-groups');
    if (!groups) return;
    groups.querySelectorAll('.comp-profile-group').forEach(group => {
        if (group.style.display === 'none') return;
        group.querySelectorAll('[data-profile-tag]').forEach(row => {
            if (row.style.display === 'none') return;
            const toggle = row.querySelector('.profile-comp-toggle');
            const intensitySelect = row.querySelector('.profile-comp-intensity');
            if (!toggle || toggle.checked === enable) return;
            toggle.checked = enable;
            if (intensitySelect) intensitySelect.disabled = !enable;
            toggle.dispatchEvent(new Event('change', { bubbles: true }));
        });
    });
};
