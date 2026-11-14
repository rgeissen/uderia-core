/**
 * knowledgeGraphPanelHandler.js
 *
 * Manages Knowledge Graph views in two locations:
 *   1. Resource Panel sidebar — compact cards with active indicator + activate toggle
 *   2. Intelligence Performance page — full glass-panel cards with assignment management
 *
 * Active/Inactive model:
 *   - Multiple KGs can be ASSIGNED to a profile
 *   - Only ONE KG can be ACTIVE per profile at a time
 *   - The active KG is the one used for context enrichment
 *   - Self-assignment rows track the owner's own activation state
 */

import { nodesLabel, entitiesLabel, relationshipsLabel, sizeLabel, addedLabel, entityCountOf, draftBreakdown } from '../kgVocabulary.js?v=1.0';
import { state } from '../state.js';
import * as API from '../api.js?v=1.2';
import { showConfirmation } from '../ui.js?v=1.287';
import { tabLabel } from './resourceBadge.js?v=1.3';
import { showAppBanner } from '../bannerSystem.js';
// IFOC colour mapping — single source of truth (profileColors.js).
import { IFOC_CONFIG } from '../profileColors.js';

// ── Intelligence-tab render target ────────────────────────────────────────────
// The full management view (cards + assignment + import) renders into one of two
// surfaces: the Intelligence Performance page (default ids) or the Knowledge Graph
// component's detail panel (component-tab ids). The active target is stored here so
// all internal refresh calls (delete/edit/sync/assign/activate) re-render the
// surface that is currently shown without each call site needing to know which one.
const _DEFAULT_KG_INTEL_CFG = {
    gridId: 'knowledge-graphs-grid-container',
    filterId: 'kg-active-filter',
    tabBtnId: 'knowledge-graphs-tab',
};
let _kgIntelCfg = { ..._DEFAULT_KG_INTEL_CFG };

// Maps profile_id -> whether the Knowledge Graph component is enabled for that
// profile (same formula as the component "Profiles" tab). Used to flag assignments
// that won't take effect at runtime because the component is switched off. `null`
// means "unknown" (lookup failed) → no warnings are shown.
let _kgComponentEnabledByProfile = null;

async function _refreshKgComponentEnabledMap() {
    try {
        const headers = { 'Authorization': `Bearer ${localStorage.getItem('tda_auth_token')}` };
        const [profilesResp, compResp] = await Promise.all([
            fetch('/api/v1/profiles', { headers }),
            fetch('/api/v1/components/knowledge_graph', { headers }),
        ]);
        if (!profilesResp.ok) { _kgComponentEnabledByProfile = null; return; }

        const profiles = (await profilesResp.json()).profiles || [];

        // Default enablement for profiles that have no explicit componentConfig entry.
        // This used to read the retired `profile_defaults.default_enabled` — a SECOND
        // manifest default that contradicted `enabled_for`, so every profile without an
        // explicit entry was falsely flagged "assignment won't take effect" while at
        // runtime it did. `enabled_for` is now the single manifest default and it is
        // per-profile-TYPE, so the default must be evaluated per profile.
        // See docs/assessments/COMPONENT_ENABLEMENT_ASSESSMENT_2026-08.md.
        let enabledFor = [];
        let globallyDisabled = false;
        if (compResp.ok) {
            const compData = await compResp.json();
            const comp = compData.component || compData;
            enabledFor = comp.profile_defaults?.enabled_for || [];
            globallyDisabled = comp.globally_disabled === true;
        }

        const map = {};
        for (const p of profiles) {
            const cc = (p.componentConfig || {}).knowledge_graph || {};
            map[p.id] = globallyDisabled
                ? false
                : (cc.enabled !== undefined
                    ? !!cc.enabled
                    : enabledFor.includes(p.profile_type));
        }
        _kgComponentEnabledByProfile = map;
    } catch (e) {
        console.warn('[KG] Could not resolve component-enabled state for assignment warnings:', e);
        _kgComponentEnabledByProfile = null;
    }
}

// Small amber warning glyph appended to an assigned/owner badge when the KG
// component is disabled for that profile (the assignment is therefore inert).
function _componentDisabledWarning(profileId) {
    if (!_kgComponentEnabledByProfile || _kgComponentEnabledByProfile[profileId] !== false) return '';
    return `<span class="kg-comp-disabled-warn inline-flex items-center align-middle ml-0.5" style="color:#f59e0b;"
                  title="The Knowledge Graph component is disabled for this profile — this assignment is inactive at runtime. Enable it in Anatomy → Agent Profiles.">
                <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m0 3.75h.008M10.34 3.94l-7.5 13A1.5 1.5 0 004.14 19.5h15.72a1.5 1.5 0 001.3-2.56l-7.5-13a1.5 1.5 0 00-2.6 0z"/></svg>
            </span>`;
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function _esc(str) {
    if (str == null) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function formatTimeAgo(isoStr) {
    if (!isoStr) return 'Unknown';
    const now = Date.now();
    const then = new Date(isoStr).getTime();
    const diffMs = now - then;

    const mins  = Math.floor(diffMs / 60000);
    const hours = Math.floor(diffMs / 3600000);
    const days  = Math.floor(diffMs / 86400000);

    if (mins < 1)   return 'Just now';
    if (mins < 60)  return `${mins}m ago`;
    if (hours < 24) return `${hours}h ago`;
    if (days < 30)  return `${days}d ago`;
    return new Date(isoStr).toLocaleDateString();
}

/**
 * Determine the active/available state of a KG relative to a specific profile.
 */
function _getKgStateForProfile(kg, profileId) {
    const isOwner = kg.profile_id === profileId;
    const assignedEntry = (kg.assigned_profiles || []).find(a => a.id === profileId);
    const isAssigned = !!assignedEntry;
    const isAvailable = isOwner || isAssigned;

    let isActive = false;
    if (isOwner) isActive = !!kg.is_active_for_owner;
    else if (assignedEntry) isActive = !!assignedEntry.is_active;

    return { isOwner, isAssigned, isAvailable, isActive };
}

// ── Resource Panel — Card creation ─────────────────────────────────────────

// Agent-pack pill — the SAME chip the planner-repository card wears (`ui.js`,
// `.agent-pack-badge`): one per pack that ships this graph, from the caller's own
// ledger (`kg.agent_packs`, served by /v1/knowledge-graph/list). Owned or forked,
// a graph a pack ships says so; a subscribed copy carries its pack line below.
function _packPills(kg) {
    const packs = Array.isArray(kg.agent_packs) ? kg.agent_packs : [];
    return packs.map(p => `<span class="agent-pack-badge"><svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4"/></svg>${_esc(p.name || '')}</span>`).join('');
}

function createKnowledgeGraphCard(kg, kgState) {
    const ifoc = IFOC_CONFIG[kg.profile_type] || { label: kg.profile_type || 'Unknown', color: '#6b7280' };
    const isSubscribed = !!kg.is_subscribed;
    const syncAvailable = !!kg.is_sync_available;
    const syncError = kg.last_sync_error;
    // A copy made by an AGENT-PACK subscription follows the PACK: Sync is the pack's sync
    // (one act, the pack card's), and it is removed with the pack — never on its own here.
    const viaPack = isSubscribed && kg.source_kind === 'agent_pack';

    // Backend badge
    const backendType = kg.backend_type || 'sqlite';
    const backendLabel = backendType === 'neo4j' ? 'Neo4j' : 'SQLite';
    const backendColor = backendType === 'neo4j' ? '#3b82f6' : '#4ade80';
    const backendBadge = `<span class="text-[10px] px-2 py-0.5 rounded font-medium" style="background:${backendColor}22; color:${backendColor}; border:1px solid ${backendColor}44;">${backendLabel}</span>`;

    const detailsEl = document.createElement('details');
    detailsEl.className = 'resource-item kg-panel-card';
    detailsEl.dataset.profileId = kg.profile_id;
    detailsEl.dataset.kgId = kg.kg_id || '';   // a live refresh reopens the card the person had open
    if (isSubscribed) detailsEl.dataset.subscriptionId = kg.subscription_id || '';

    if (kgState.isActive) {
        detailsEl.style.borderLeftWidth = '4px';
        detailsEl.style.borderLeftColor = '#F15F22';
    }

    // Entity type pills
    const typePills = Object.entries(kg.entity_types || {})
        .map(([type, count]) =>
            `<span class="text-[10px] font-medium px-2 py-0.5 rounded whitespace-nowrap" style="background:var(--hover-bg,rgba(148,163,184,0.10));color:var(--text-muted,#9ca3af);border:1px solid var(--border-primary,rgba(148,163,184,0.18));">${type} (${count})</span>`
        ).join(' ');

    // Active badge (shown in summary line)
    const activeBadge = kgState.isActive
        ? '<span data-active-badge class="text-xs font-semibold px-2 py-0.5 rounded" style="color: #F15F22; background: rgba(241,95,34,0.15); border: 1px solid rgba(241,95,34,0.3);">ACTIVE</span>'
        : '';

    // IFOC badge
    const ifocBadge = `<span class="text-[10px] font-medium px-2 py-0.5 rounded whitespace-nowrap" style="color:${ifoc.color};background:${ifoc.color}20;border:1px solid ${ifoc.color}40;">${ifoc.label}</span>`;

    // Profile tag
    const tagLabel = kg.profile_tag
        ? `<span class="text-xs font-mono" style="color: var(--text-muted, #9ca3af);">@${kg.profile_tag}</span>`
        : '';

    // Owner label
    const ownerLabel = kgState.isOwner
        ? '<span class="text-xs" style="color:var(--text-muted,#6b7280);">(own)</span>'
        : '';

    // Subscription badge — locked, theme-aware indicator next to the title
    // when this KG is a live-tracking copy from the marketplace.  Replaces
    // the (own) label when both apply (subscriptions are never owned).
    const subscribedBadge = isSubscribed ? `
        <span class="text-[10px] font-medium px-2 py-0.5 rounded inline-flex items-center gap-1 whitespace-nowrap"
              style="color:#3b82f6;background:rgba(59,130,246,0.12);border:1px solid rgba(59,130,246,0.35);"
              title="${viaPack ? 'Copy made by your agent-pack subscription &mdash; follows the publisher\'s pack on sync' : 'Subscribed knowledge graph &mdash; rebuilt automatically when the publisher republishes'}">
            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"/></svg>
            Subscribed
        </span>` : '';

    const syncStatusBadge = (() => {
        if (!isSubscribed) return '';
        if (syncError) {
            return `<span class="text-[10px] font-medium px-2 py-0.5 rounded whitespace-nowrap"
                          style="color:#ef4444;background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.35);"
                          title="${_esc(syncError)}">Sync failed</span>`;
        }
        if (syncAvailable) {
            return `<span class="text-[10px] font-medium px-2 py-0.5 rounded whitespace-nowrap"
                          style="color:#f59e0b;background:rgba(245,158,11,0.12);border:1px solid rgba(245,158,11,0.35);"
                          title="Publisher has updates available">Update available</span>`;
        }
        return `<span class="text-[10px] font-medium px-2 py-0.5 rounded whitespace-nowrap"
                      style="color:var(--text-muted,#94a3b8);background:var(--hover-bg,rgba(148,163,184,0.10));border:1px solid var(--border-primary,rgba(148,163,184,0.18));">In sync</span>`;
    })();

    // Assigned profiles badges
    const assignedBadges = _renderAssignedProfilesBadges(kg);

    // Activate/deactivate button (works on both owned and subscribed KGs —
    // activation only flips kg_profile_assignments, not the read-only data)
    let activateBtn = '';
    if (kgState.isAvailable && !kgState.isActive) {
        activateBtn = `<button class="kg-activate-btn px-3 py-1 text-xs font-semibold rounded-md transition-colors"
                               style="background: rgba(241,95,34,0.15); color: #F15F22; border: 1px solid rgba(241,95,34,0.3);"
                               data-kg-id="${kg.kg_id || kg.profile_id}" data-kg-owner-id="${kg.profile_id}" title="Activate this KG for context enrichment">Activate</button>`;
    } else if (kgState.isActive) {
        activateBtn = `<button class="kg-deactivate-btn px-3 py-1 text-xs font-semibold rounded-md transition-colors"
                               style="background: var(--hover-bg, #4b5563); color: var(--text-muted, #9ca3af);"
                               data-kg-id="${kg.kg_id || kg.profile_id}" data-kg-owner-id="${kg.profile_id}" title="Deactivate this KG">Deactivate</button>`;
    }

    // Subscription-specific action buttons replace Delete with Unsubscribe
    // and add Sync.  Inspect + Export remain available (read-only operations).
    const syncBtn = viaPack ? `
        <button class="kg-pack-sync-btn px-3 py-1 text-xs font-semibold rounded-md transition-colors u-tip"
                style="${syncAvailable ? 'background: rgba(241,95,34,0.15); color: #F15F22; border: 1px solid rgba(241,95,34,0.35);' : 'background: rgba(59,130,246,0.15); color: #60a5fa; border: 1px solid rgba(59,130,246,0.3);'}"
                data-pack-id="${kg.source_marketplace_id || ''}"
                data-tip="${syncAvailable ? 'The publisher updated the pack — sync the whole pack subscription (every agent, repository and graph, in place)' : 'Sync the whole pack subscription from the publisher\'s live pack — a no-op when nothing moved'}">${syncAvailable ? 'Sync pack update' : 'Sync pack'}</button>`
      : isSubscribed && (syncAvailable || syncError) ? `
        <button class="kg-sync-btn px-3 py-1 text-xs font-semibold rounded-md transition-colors"
                style="background: rgba(59,130,246,0.15); color: #60a5fa; border: 1px solid rgba(59,130,246,0.3);"
                data-subscription-id="${kg.subscription_id}"
                data-marketplace-id="${kg.source_marketplace_id || ''}"
                data-kg-id="${kg.kg_id || ''}"
                title="${syncError ? 'Retry sync' : 'Sync with publisher'}">Sync now</button>` : '';

    const destructiveBtn = viaPack
        ? `<span class="kg-pack-managed-note px-2 py-1 text-[10px] u-tip" style="color:var(--text-muted,#6b7280);"
                 data-tip="This copy belongs to your agent-pack subscription: it is removed when you unsubscribe from the pack on its marketplace card">Removed with the pack</span>`
        : isSubscribed
        ? `<button class="kg-unsubscribe-btn px-3 py-1 text-xs font-semibold rounded-md transition-colors"
                   style="background: rgba(245,158,11,0.15); color: #f59e0b; border: 1px solid rgba(245,158,11,0.3);"
                   data-subscription-id="${kg.subscription_id}"
                   data-marketplace-id="${kg.source_marketplace_id || ''}"
                   data-kg-name="${_esc(kg.kg_name || kg.profile_name || kg.profile_id)}"
                   title="Stop tracking this publisher's KG and remove the local copy">Unsubscribe</button>`
        : `<button class="kg-delete-btn px-3 py-1 bg-red-600/80 text-white text-xs font-semibold rounded-md hover:bg-red-500 transition-colors"
                   data-profile-id="${kg.profile_id}" data-kg-id="${kg.kg_id || kg.profile_id}" data-profile-name="${kg.profile_name || kg.profile_id}" title="Delete this knowledge graph">Delete</button>`;

    // Subscribed-KG context line: "Subscribed from <publisher>, last synced X ago"
    const subscriptionLine = isSubscribed ? `
        <div class="flex flex-wrap items-center gap-3" style="color:var(--text-muted,#6b7280);">
            ${viaPack
                ? `<span>Subscribed via agent pack <strong style="color:var(--text-primary,#e5e7eb);">${_esc(kg.pack_name || '')}</strong>${kg.publisher_username ? ` from <strong style="color:var(--text-primary,#e5e7eb);">${_esc(kg.publisher_username)}</strong>` : ''}</span>`
                : kg.publisher_username ? `<span>Subscribed from <strong style="color:var(--text-primary,#e5e7eb);">${_esc(kg.publisher_username)}</strong></span>` : '<span>Subscribed</span>'}
            <span>${viaPack ? 'Pack last synced' : 'Last synced'} ${formatTimeAgo((viaPack && kg.pack_synced_at) || kg.last_synced_at || kg.last_updated)}</span>
            ${syncError ? `<span style="color:#ef4444;" title="${_esc(syncError)}">Last sync failed</span>` : ''}
        </div>` : '';

    // Governed-product lifecycle badges (Tier 4.3).
    const _lc = kg.lifecycle_status || 'draft';
    const _lcStyle = { draft: ['rgba(148,163,184,0.15)', '#94a3b8'],
                       published: ['rgba(34,197,94,0.15)', '#4ade80'],
                       deprecated: ['rgba(245,158,11,0.15)', '#f59e0b'] }[_lc] || ['rgba(148,163,184,0.15)', '#94a3b8'];
    const _lcLabel = _lc === 'published'
        ? `Published${kg.semantic_version ? ' v' + kg.semantic_version : ''}`
        : _lc.charAt(0).toUpperCase() + _lc.slice(1);
    const lifecycleBadge = _lc !== 'draft'
        ? `<span class="text-[10px] font-semibold px-2 py-0.5 rounded" style="background:${_lcStyle[0]};color:${_lcStyle[1]};" title="Ontology lifecycle">${_lcLabel}</span>`
        : '';
    const _confStyle = { conformant: ['rgba(34,197,94,0.12)', '#4ade80'],
                         partial: ['rgba(245,158,11,0.12)', '#f59e0b'],
                         nonconformant: ['rgba(239,68,68,0.12)', '#ef4444'] }[kg.conformance_status];
    const conformanceBadge = _confStyle
        ? `<span class="text-[10px] px-2 py-0.5 rounded" style="background:${_confStyle[0]};color:${_confStyle[1]};" title="Ontology conformance">${kg.conformance_status}</span>`
        : '';

    detailsEl.innerHTML = `
        <summary class="flex justify-between items-start gap-2 px-3 py-2.5">
            <div class="flex items-center gap-2 flex-wrap min-w-0">
                <span class="text-sm font-medium truncate" style="color:var(--text-primary,#e5e7eb);">${kg.kg_name || kg.profile_name || kg.profile_id}</span>
                ${_packPills(kg)}
                ${tagLabel}
                ${ifocBadge}
                ${isSubscribed ? subscribedBadge : ownerLabel}
                ${activeBadge}
                ${syncStatusBadge}
                ${backendBadge}${_sourceValidationBadge(kg)}
                ${lifecycleBadge}
                ${conformanceBadge}
                <span class="text-[10px]" style="color:var(--text-muted,#6b7280);">${sizeLabel(kg)}</span>
            </div>
            <span class="kg-card-seal-slot" aria-hidden="true"></span>
            <svg class="chevron w-5 h-5 flex-shrink-0" style="color:var(--text-muted,#9ca3af);" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"></path></svg>
        </summary>
        <div class="px-3 pb-3 pt-2 space-y-2 text-xs" style="color:var(--text-muted,#9ca3af);">
            ${subscriptionLine}
            <div class="flex gap-4">
                <span>Nodes: <strong style="color:var(--text-primary,#e5e7eb);">${kg.total_entities}</strong></span>${entityCountOf(kg) != null ? `<span>Entities: <strong style="color:var(--text-primary,#e5e7eb);">${entityCountOf(kg)}</strong></span>` : ''}
                <span>Relationships: <strong style="color:var(--text-primary,#e5e7eb);">${kg.total_relationships}</strong></span>
                <span>Backend: <strong style="color:${backendColor};">${backendLabel}</strong></span>
            </div>
            <div class="flex flex-wrap gap-1">${typePills || '<span style="color:var(--text-muted,#6b7280);">No nodes</span>'}</div>
            <div class="flex flex-wrap items-center gap-1">
                <span style="color:var(--text-muted,#6b7280);">Shared with:</span>
                ${assignedBadges}
            </div>
            <div style="color:var(--text-muted,#6b7280);">Last updated: ${formatTimeAgo(kg.last_updated)}</div>
            <div class="flex items-center gap-2 pt-2" style="border-top:1px solid var(--border-primary,rgba(148,163,184,0.18));">
                ${activateBtn}
                <button class="kg-inspect-btn px-3 py-1 text-xs font-semibold rounded-md transition-colors"
                        style="background: rgba(147,51,234,0.15); color: #a78bfa; border: 1px solid rgba(147,51,234,0.3);"
                        data-profile-id="${kg.profile_id}" data-profile-name="${kg.profile_name || kg.profile_id}" title="Inspect knowledge graph">Inspect</button>
                <button class="kg-export-btn inline-flex items-center gap-1 px-3 py-1 bg-blue-600 text-white text-xs font-semibold rounded-md hover:bg-blue-500 transition-colors"
                        data-profile-id="${kg.profile_id}" data-kg-id="${kg.kg_id || kg.profile_id}" title="Export — JSON or open standards (RDF: Turtle / JSON-LD / RDF-XML / N-Triples, OWL, SKOS)">Export<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7"/></svg></button>
                <button class="kg-govern-btn px-3 py-1 text-xs font-semibold rounded-md transition-colors"
                        style="background: rgba(16,185,129,0.15); color: #34d399; border: 1px solid rgba(16,185,129,0.3);"
                        data-kg-id="${kg.kg_id || kg.profile_id}" data-kg-name="${_esc(kg.kg_name || kg.profile_name || kg.profile_id)}" title="Govern — conformance & publish lifecycle">Govern</button>
                ${syncBtn}
                ${destructiveBtn}
            </div>
        </div>
    `;

    // The trust seal is a button (it opens the trust record, or sign-off). It may NOT sit
    // inside the <summary>: a summary is one toggle, and a control nested in it is
    // mis-announced or unreachable for keyboard and screen-reader users (Chrome flags it as
    // an error-level issue). So the card is a wrapper holding the <details> and the seal,
    // laid over the slot the summary reserves beside the chevron on the first line.
    _injectKgCardCss();
    const card = document.createElement('div');
    card.className = 'kg-card-wrap';
    card.appendChild(detailsEl);
    const seal = document.createElement('span');
    seal.className = 'kg-card-seal';
    seal.dataset.trustArtifactKind = 'ontology';
    seal.dataset.trustArtifactId = kg.kg_id || kg.profile_id;
    seal.dataset.trustArtifactName = kg.kg_name || kg.profile_name || kg.profile_id;
    card.appendChild(seal);
    if (window.TrustBadge) window.TrustBadge.mountArtifactBadges(card);
    return card;
}

function _injectKgCardCss() {
    if (document.getElementById('kg-card-seal-styles')) return;
    const el = document.createElement('style');
    el.id = 'kg-card-seal-styles';
    // summary: py-2.5 (10px) + the 1px card border; first line = the 20px chevron; the slot
    // and the seal are the seal's width only once a seal is mounted (a non-signer sees none
    // on an unsigned graph, and then no gap is reserved)
    el.textContent = `
.kg-card-wrap { position: relative; }
.kg-card-seal-slot { flex-shrink: 0; width: 0; height: 20px; }
.kg-card-wrap:has(> .kg-card-seal > *) .kg-card-seal-slot { width: 14px; }
.kg-card-seal { position: absolute; top: 11px; right: calc(1px + 0.75rem + 20px + 0.5rem); height: 20px;
                display: inline-flex; align-items: center; font-size: 14px; }
.kg-card-seal:empty { display: none; }`;
    document.head.appendChild(el);
}

function renderEmptyState(container) {
    container.innerHTML = `
        <div class="flex flex-col items-center justify-center py-12 text-center">
            <svg class="w-12 h-12 mb-4" style="color: var(--text-muted, #6b7280);" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
                <path stroke-linecap="round" stroke-linejoin="round" d="M12 21a9.004 9.004 0 008.716-6.747M12 21a9.004 9.004 0 01-8.716-6.747M12 21c2.485 0 4.5-4.03 4.5-9S14.485 3 12 3m0 18c-2.485 0-4.5-4.03-4.5-9S9.515 3 12 3m0 0a8.997 8.997 0 017.843 4.582M12 3a8.997 8.997 0 00-7.843 4.582m15.686 0A11.953 11.953 0 0112 10.5c-2.998 0-5.74-1.1-7.843-2.918m15.686 0A8.959 8.959 0 0121 12c0 .778-.099 1.533-.284 2.253m0 0A17.919 17.919 0 0112 16.5c-3.162 0-6.133-.815-8.716-2.247m0 0A9.015 9.015 0 013 12c0-1.605.42-3.113 1.157-4.418" />
            </svg>
            <p class="text-sm font-semibold mb-1" style="color: var(--text-primary, #e5e7eb);">No Knowledge Graphs</p>
            <p class="text-xs max-w-xs" style="color: var(--text-muted, #6b7280);">
                Knowledge graphs are built automatically when you use profiles with the KG component enabled.
                Entities and relationships are discovered during your conversations.
            </p>
        </div>
    `;
}

function renderLoadingState(container) {
    container.innerHTML = `
        <div class="flex items-center justify-center py-12">
            <div class="animate-spin rounded-full h-6 w-6 border-2 border-gray-500 border-t-[#F15F22]"></div>
            <span class="ml-3 text-sm" style="color: var(--text-muted, #9ca3af);">Loading knowledge graphs...</span>
        </div>
    `;
}

// ── Resource Panel — Main load function ────────────────────────────────────

export async function loadKnowledgeGraphsPanel() {
    const container = document.getElementById('knowledge-graphs-content');
    if (!container) return;
    // what the person had open, read BEFORE the loading state replaces the cards — a live
    // refresh must not collapse their view
    const wasOpen = new Set([...container.querySelectorAll('details.kg-panel-card[open]')]
        .map(d => d.dataset.kgId || d.dataset.profileId));
    // a refresh of cards already on screen keeps them while it reads (no flash to "loading")
    if (!container.querySelector('details.kg-panel-card')) renderLoadingState(container);

    try {
        const response = await API.loadKnowledgeGraphList();
        const graphs = response.knowledge_graphs || [];
        state.resourceData.knowledgeGraphs = graphs;

        const activeProfileId = state.currentResourcePanelProfileId;

        // Filter to only KGs that are owned by or assigned to the current profile
        const visibleGraphs = graphs.filter(kg => {
            const kgState = _getKgStateForProfile(kg, activeProfileId);
            return kgState.isAvailable;
        });

        // Update tab counter and visibility — hide tab when no graphs available
        const tabBtn = document.querySelector('.resource-tab[data-type="knowledge-graphs"]');
        if (tabBtn) {
            if (visibleGraphs.length === 0) {
                tabBtn.style.display = 'none';
            } else {
                tabBtn.style.display = 'inline-block';
                // enabled/available: the graph bound to this profile over the graphs assigned to it —
                // an assigned-but-unbound graph is listed, not enabled.
                tabBtn.textContent = tabLabel('Knowledge Graphs', visibleGraphs,
                                              kg => _getKgStateForProfile(kg, activeProfileId).isActive);
            }
        }

        if (visibleGraphs.length === 0) {
            renderEmptyState(container);
            return;
        }

        container.innerHTML = '';

        for (const kg of visibleGraphs) {
            const kgState = _getKgStateForProfile(kg, activeProfileId);
            const card = createKnowledgeGraphCard(kg, kgState);
            if (wasOpen.has(kg.kg_id) || wasOpen.has(kg.profile_id)) card.querySelector('details').open = true;
            container.appendChild(card);
        }
    } catch (err) {
        console.error('Failed to load knowledge graphs:', err);
        container.innerHTML = `
            <div class="flex items-center justify-center py-12 text-center">
                <p class="text-sm" style="color: var(--text-muted, #ef4444);">Failed to load knowledge graphs: ${err.message}</p>
            </div>
        `;
    }
}

// ── Live: the authored layer changed (a draft, a landing, a publish, a signature — from the
// answer, the Govern dialog or the Workspace) ⇒ every knowledge-graph card list on the page
// re-reads its figures in place (nodes · entities, lifecycle, trust). No page refresh.
let _kgListRefreshTimer = null;
if (typeof document !== 'undefined' && !window.__kgListAuthoredBound) {
    window.__kgListAuthoredBound = true;
    UderiaBus.onComponent('knowledge_graph', () => {
        clearTimeout(_kgListRefreshTimer);
        _kgListRefreshTimer = setTimeout(() => {
            try { if (document.getElementById('knowledge-graphs-content')) loadKnowledgeGraphsPanel(); } catch (_) { /* a refresh is a convenience */ }
            try { if (_kgIntelCfg && document.getElementById(_kgIntelCfg.gridId)) loadKnowledgeGraphsIntelligenceTab(); } catch (_) { /* idem */ }
        }, 250);
    });
}

// ── Another surface asks for a graph's Govern dialog (the Action Designer, when signing an
// action waits for the model it stands on) — the dialog lives here, so it opens here.
if (typeof document !== 'undefined' && !window.__kgOpenGovernBound) {
    window.__kgOpenGovernBound = true;
    UderiaBus.on('uderia:open-govern', (e) => {
        const d = (e && e.detail) || {};
        if (d.kgId) openKgGovernanceDialog(d.kgId, d.kgName || '');
    });
}

// ── Resource Panel — Refresh (re-fetch + re-render) ────────────────────────

export async function refreshKnowledgeGraphsPanel() {
    await loadKnowledgeGraphsPanel();
}

// ── Resource Panel — Dynamic highlight on kg_enrichment event ──────────────

export function highlightKnowledgeGraphCard(ownerProfileId) {
    // Switch to Knowledge Graphs tab
    document.querySelectorAll('.resource-tab').forEach(t => t.classList.remove('active'));
    const tabBtn = document.querySelector('.resource-tab[data-type="knowledge-graphs"]');
    if (tabBtn) tabBtn.classList.add('active');
    document.querySelectorAll('.resource-panel').forEach(p => {
        p.style.display = p.id === 'knowledge-graphs-panel' ? 'flex' : 'none';
    });

    const card = document.querySelector(`#knowledge-graphs-content [data-profile-id="${CSS.escape(ownerProfileId)}"]`);
    if (!card) {
        // Panel not yet populated — load it and let the indicator update handle visual state
        const content = document.getElementById('knowledge-graphs-content');
        if (!content || content.children.length === 0) loadKnowledgeGraphsPanel();
        return;
    }

    if (state?.currentlySelectedResource) state.currentlySelectedResource.classList.remove('resource-selected');
    card.open = true;
    card.classList.add('resource-selected');
    if (state) state.currentlySelectedResource = card;
    setTimeout(() => card.scrollIntoView({ behavior: 'smooth', block: 'start' }), 350);
}

// ── Resource Panel — Active indicator update (DOM-only, no re-fetch) ───────

export function updateKnowledgeGraphActiveIndicator() {
    const container = document.getElementById('knowledge-graphs-content');
    if (!container) return;

    const activeProfileId = state.currentResourcePanelProfileId;
    const cards = container.querySelectorAll('.kg-panel-card');
    const graphs = state.resourceData?.knowledgeGraphs || [];

    cards.forEach(card => {
        const profileId = card.dataset.profileId;
        const kg = graphs.find(g => g.profile_id === profileId);
        if (!kg) return;

        const kgState = _getKgStateForProfile(kg, activeProfileId);

        // Update border
        card.style.borderLeftWidth = kgState.isActive ? '4px' : '';
        card.style.borderLeftColor = kgState.isActive ? '#F15F22' : '';

        // Update ACTIVE badge visibility
        const existingBadge = card.querySelector('[data-active-badge]');
        if (existingBadge) existingBadge.remove();

        if (kgState.isActive) {
            const nameRow = card.querySelector('summary .flex.items-center.gap-2');
            if (nameRow) {
                const badge = document.createElement('span');
                badge.setAttribute('data-active-badge', '');
                badge.className = 'text-xs font-semibold px-2 py-0.5 rounded';
                badge.style.cssText = 'color: #F15F22; background: rgba(241,95,34,0.15); border: 1px solid rgba(241,95,34,0.3);';
                badge.textContent = 'ACTIVE';
                nameRow.appendChild(badge);
            }
        }
    });
}

// ── Resource Panel — Action handlers ───────────────────────────────────────

// Export format menu — JSON (native) + W3C open standards (RDF serializations,
// OWL ontology, SKOS concept scheme). Theme-aware popover anchored to the button.
const KG_EXPORT_FORMATS = [
    { label: 'JSON', sub: 'Uderia native', opts: { format: 'json' } },
    { divider: true },
    { label: 'Turtle', sub: 'RDF · .ttl', opts: { format: 'rdf', serialization: 'turtle' } },
    { label: 'JSON-LD', sub: 'RDF · .jsonld', opts: { format: 'rdf', serialization: 'jsonld' } },
    { label: 'RDF/XML', sub: 'RDF · .rdf', opts: { format: 'rdf', serialization: 'xml' } },
    { label: 'N-Triples', sub: 'RDF · .nt', opts: { format: 'rdf', serialization: 'nt' } },
    { divider: true },
    { label: 'Ontology only', sub: 'OWL · Turtle', opts: { format: 'owl', serialization: 'turtle' } },
    { label: 'Concepts only', sub: 'SKOS · Turtle', opts: { format: 'skos', serialization: 'turtle' } },
];

export function openKgExportMenu(anchorEl, profileId, kgId = null) {
    // Toggle off if already open from this anchor.
    const existing = document.getElementById('kg-export-menu');
    if (existing) {
        const sameAnchor = existing._anchor === anchorEl;
        existing.remove();
        document.removeEventListener('click', existing._dismiss, true);
        document.removeEventListener('keydown', existing._onKey, true);
        if (sameAnchor) return;
    }

    const menu = document.createElement('div');
    menu.id = 'kg-export-menu';
    menu._anchor = anchorEl;
    menu.style.cssText = [
        'position:fixed', 'z-index:10000', 'min-width:200px', 'padding:4px',
        'background:var(--card-bg,#1f2126)', 'border:1px solid var(--border-primary,rgba(148,163,184,0.25))',
        'border-radius:10px', 'box-shadow:0 10px 30px rgba(0,0,0,0.35)', 'font-size:12px',
    ].join(';');

    menu.innerHTML = KG_EXPORT_FORMATS.map((it, i) => {
        if (it.divider) return `<div style="height:1px;margin:4px 6px;background:var(--border-secondary,rgba(148,163,184,0.15));"></div>`;
        return `<button class="kg-export-opt" data-idx="${i}" role="menuitem"
            style="display:flex;align-items:baseline;justify-content:space-between;gap:12px;width:100%;
                   padding:7px 10px;border-radius:6px;background:transparent;border:none;cursor:pointer;
                   color:var(--text-primary,#e5e7eb);text-align:left;">
            <span style="font-weight:600;">${it.label}</span>
            <span style="color:var(--text-muted,#94a3b8);font-size:11px;">${it.sub}</span>
        </button>`;
    }).join('');

    document.body.appendChild(menu);
    // Position under the button (flip up if it would overflow the viewport).
    const r = anchorEl.getBoundingClientRect();
    const mh = menu.offsetHeight, mw = menu.offsetWidth;
    let top = r.bottom + 6, left = r.left;
    if (top + mh > window.innerHeight - 8) top = Math.max(8, r.top - mh - 6);
    if (left + mw > window.innerWidth - 8) left = Math.max(8, window.innerWidth - mw - 8);
    menu.style.top = `${top}px`;
    menu.style.left = `${left}px`;

    menu.querySelectorAll('.kg-export-opt').forEach(btn => {
        btn.addEventListener('mouseenter', () => { btn.style.background = 'var(--hover-bg-strong,rgba(148,163,184,0.12))'; });
        btn.addEventListener('mouseleave', () => { btn.style.background = 'transparent'; });
        btn.addEventListener('click', async (ev) => {
            ev.stopPropagation();
            const item = KG_EXPORT_FORMATS[Number(btn.dataset.idx)];
            closeMenu();
            try {
                await API.exportKnowledgeGraph(profileId, kgId, item.opts);
                showAppBanner(`Exported as ${item.label}${item.sub ? ' (' + item.sub + ')' : ''}`, 'success');
            } catch (err) {
                console.error('Export failed:', err);
                showAppBanner(`Export failed: ${err.message}`, 'error');
            }
        });
    });

    function closeMenu() {
        menu.remove();
        document.removeEventListener('click', dismiss, true);
        document.removeEventListener('keydown', onKey, true);
    }
    function dismiss(ev) { if (!menu.contains(ev.target) && ev.target !== anchorEl) closeMenu(); }
    function onKey(ev) { if (ev.key === 'Escape') closeMenu(); }
    menu._dismiss = dismiss;
    menu._onKey = onKey;
    // Defer so the opening click doesn't immediately dismiss.
    setTimeout(() => {
        document.addEventListener('click', dismiss, true);
        document.addEventListener('keydown', onKey, true);
    }, 0);
}

// ── Governed-product lifecycle dialog (Tier 4.3) ─────────────────────────────
const _GOV_LC_STYLE = { draft: ['rgba(148,163,184,0.15)', '#94a3b8'],
                        published: ['rgba(34,197,94,0.15)', '#4ade80'],
                        deprecated: ['rgba(245,158,11,0.15)', '#f59e0b'] };
const _GOV_CONF_STYLE = { conformant: ['rgba(34,197,94,0.15)', '#4ade80'],
                          partial: ['rgba(245,158,11,0.15)', '#f59e0b'],
                          nonconformant: ['rgba(239,68,68,0.15)', '#ef4444'] };
const _GOV_SEV = { high: '#ef4444', medium: '#f59e0b', low: '#94a3b8' };

// PROP-008 OBDA-MODEL — the drafts on the authored layer and where each stands on the
// four-step road drafted → validated → deployed → published. Every step is a state a node
// can hold (A1 drafts, A4 validates, A3 lands, A5 publishes); a step that is ahead is drawn
// as the road and names the act that reaches it, never as a button of its own.
// One audit row in words: WHEN · WHO · WHAT (· conformance). Two publishes of the same extension
// were rendered identically ("publish_extension → published · ok") — the row carried no time, no
// person and no subject, so a history read as a duplicate.
const _GOV_AUDIT_ACTS = { propose: 'proposed it for approval', approve: 'approved it', reject: 'rejected it',
                          deprecate: 'deprecated the model' };
const _govEsc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
function _govAuditWords(a) {
    const when = (() => {
        const d = new Date(a.created_at || '');
        return isNaN(d) ? '' : d.toLocaleString(undefined, { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
    })();
    const subj = a.subject || {};
    let what;
    if (a.action === 'publish_extension') {
        const parts = Object.entries(subj.nodes || {}).flatMap(([t, c]) => Array.from({ length: Number(c) || 0 }, () => ({ entity_type: t })));
        what = !a.allowed ? 'was refused publishing an extension'
            : a.to_status ? `published ${draftBreakdown(parts) || 'an extension'}`
            : 'found nothing new to publish';
    } else if (a.action === 'publish') {
        what = a.allowed ? `published the model${subj.version ? ` as v${subj.version}` : ''}` : 'was refused publishing the model';
    } else {
        what = (a.allowed ? '' : 'was refused: ') + (_GOV_AUDIT_ACTS[a.action] || String(a.action || '').replace(/_/g, ' '));
    }
    const why = !a.allowed && a.reason ? ` (${String(a.reason).replace(/^ext-[0-9a-f-]+:\s*/, '')})` : '';
    return [when, a.actor_label, what + why, a.conformance].filter(Boolean).join(' · ');
}

const _GOV_STEPS = ['drafted', 'validated', 'deployed', 'published'];
// the drivers a model delta can land on — mirrors dbdeploy/connection.py DEPLOYABLE_DRIVERS (gate-pinned)
const DEPLOY_DRIVERS = ['teradata', 'postgresql'];
const DEPLOY_DRIVER_LABEL = { teradata: 'Teradata', postgresql: 'PostgreSQL' };
// A node's road (drafted → validated → deployed → published) — ONE drawing, used by the dialog
// and by a preview's in-place update. `ctx`: {owed, actOn, publishOwed} (the acts offered here).
function _govStepperHtml(n, ctx) {
    const { owed, actOn, publishOwed } = ctx || {};
    const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const status = n.status || 'draft';
    const at = Math.max(0, _GOV_STEPS.indexOf(status === 'draft' ? 'drafted' : status));
    return `<div class="kg-gov-steps" role="list" aria-label="Authored node lifecycle">` + _GOV_STEPS.map((st, i) => {
        // the step a node is AT is where it stands (more is owed); the END of the road, once
        // reached, owes nothing — a published node reads done, never as a pending act
        let cls = i < at || (i === at && at === _GOV_STEPS.length - 1) ? 'done' : i === at ? 'now' : 'next';
        // a step is DONE only when it happened: a node landed before the standard's validation
        // existed was never validated — it reads skipped, never done (Rule 3.7: no vacuous clean)
        let note = '';
        if (st === 'validated' && i < at && !n.validated_at) { cls = 'skipped'; note = 'not validated — landed before the design standard validated drafts; the warehouse accepted the DDL'; }
        else if (st === 'validated' && n.validated_at && i <= at) { note = `validated against the design standard${n.standard ? ' ' + n.standard : ''} on ${String(n.validated_at).slice(0, 10)}`; }
        else if (st === 'published' && i <= at && n.published_at) { note = `published on ${String(n.published_at).slice(0, 10)}${n.published_by ? ' by ' + (n.published_by_label || n.published_by) : ''} — released agents read it; it travels with the graph`; }
        else if (i > at) {
            // the platform tooltip, never a native title (a trust surface); a step ahead names
            // the act that reaches it, and says so honestly when the act is not offered here
            note = st === 'deployed' && owed ? 'land it from Next, above'
                 : st === 'validated' && actOn ? 'Preview (Next, above) validates the draft against the design standard'
                 : st === 'published' && publishOwed ? 'published from Next, above, once it has landed — a person\'s act, never an agent\'s'
                 : st === 'published' ? 'publishing is an approver\'s act (the OBDA-MODEL · OBDA-ACT master must be on)'
                 : 'not yet — this act is offered once the OBDA-MODEL · OBDA-ACT master switch is on';
        }
        const tip = note ? ` data-tip="${esc(note)}"` : '';
        return `<span class="kg-gov-step kg-gov-step--${cls}${note ? ' u-tip' : ''}" role="listitem"${tip}>${esc(st)}</span>`;
    }).join('<span class="kg-gov-step-sep" aria-hidden="true"></span>') + `</div>`;
}

function _govDraftsHtml(authored) {
    const nodes = (authored && authored.nodes) || [];
    if (!nodes.length) return '';
    const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const actOn = !!(authored && authored.act_enabled);
    // the act that is OWED (Rule 8.5): a draft (or a validated node) can be landed; a landed
    // one waits on publish (the Extensions row) — so Deploy is offered only while something
    // is still unrealised, and only with the master on (the REST refuses it otherwise)
    const owed = actOn && nodes.some(n => ['draft', 'validated'].includes(n.status || 'draft'));
    const exts = (authored && authored.extensions) || [];
    // publish is a PERSON's act on the extension (§13): offered on the extender's own row
    // when something of it is still unpublished and the caller holds an approver tier
    const publishOwed = actOn && exts.some(x => x.mine && x.can_publish && (x.unpublished || 0) > 0);
    const stepper = (n) => _govStepperHtml(n, { owed, actOn, publishOwed });
    const attrsLine = (n) => {
        const kids = n.attributes || [];
        if (!kids.length) return '';
        return `<div class="kg-gov-draft-attrs">${kids.length} attribute${kids.length === 1 ? '' : 's'}: ${kids.map(a => `<code>${esc(a.leaf || String(a.name || '').split('.').pop())}</code>`).join(' · ')}</div>`;
    };
    const receiptLine = (n) => {
        const r = n.deploy_receipt;
        if (!r || !r.receipt_id) return '';
        const sealed = r.signed ? 'sealed' : 'unsealed (no provenance key)';
        return `<div class="kg-gov-draft-receipt">landed as <code>${esc(r.identifier || n.deployed_table || n.deployed_column || '')}</code> in ${esc(r.landing || '')} · receipt <code>${esc(r.receipt_id)}</code> · ${sealed}</div>`;
    };
    // C3: every OTHER warehouse the node landed on, with its own receipt and readback — after
    // the graph's own landing and its readback, so each readback sits under its own landing
    const otherLandings = (n) => {
        const own = (n.deploy_receipt || {}).receipt_id;
        return Object.entries(n.realisations || {}).filter(([inf, z]) => z && z.receipt && z.receipt.receipt_id !== own)
            .map(([inf, z]) => `<div class="kg-gov-draft-receipt">${own ? 'also on' : 'landed on'} ${esc(DEPLOY_DRIVER_LABEL[inf] || inf)}: <code>${esc(z.table || z.column || '')}</code> · receipt <code>${esc(z.receipt.receipt_id)}</code> · ${z.receipt.signed ? 'sealed' : 'unsealed (no provenance key)'}${z.readback && z.readback.status ? ` · readback ${esc(z.readback.status)}` : ''}</div>`).join('');
    };
    // the readback (A4): what the warehouse HOLDS of the standard that built this node — held ·
    // violated · unverified, the violated rules named; an unread dictionary says so, never a pass
    const readbackLine = (n) => {
        const rb = n.readback;
        if (!rb || typeof rb !== 'object') return '';
        const st = rb.status || 'unverified';
        const cls = st === 'conformant' ? 'held' : st === 'nonconformant' ? 'violated' : st === 'partial' ? 'partial' : 'unverified';
        const parts = [`${rb.held || 0} held`];
        if (rb.violated) parts.push(`${rb.violated} violated`);
        if (rb.unverified) parts.push(`${rb.unverified} unverified`);
        const viol = (rb.violations || []).map(v => `<div class="kg-gov-rb-viol">${esc(v)}</div>`).join('');
        return `<div class="kg-gov-draft-readback"><span class="kg-gov-rb kg-gov-rb--${cls}">readback ${esc(st)}</span> ${esc(parts.join(' · '))}${rb.standard ? ` · standard <code>${esc(rb.standard)}</code>` : ''}${viol}</div>`;
    };
    const rows = nodes.map(n => {
        const leaf = String(n.name || '').split('.').pop();
        const d = n.decision || {};
        // the advisor's reasons are why a DRAFT was proposed; once published they are history
        const why = (n.status || 'draft') !== 'published' ? (d.evidence || []).slice(0, 2).map(esc).join(' · ') : '';
        return `<div class="kg-gov-draft" data-authored-name="${esc(n.name)}" data-authored-status="${esc(n.status || 'draft')}">
            <div class="kg-gov-draft-head"><strong>${esc(leaf)}</strong> <span class="kg-gov-draft-kind">${esc(n.entity_type)}</span>
              ${d.rule ? `<span class="kg-gov-draft-rule">${esc(String(d.rule).replace(/_/g, ' '))}</span>` : ''}
              <span class="kg-gov-draft-by">by ${esc(n.authored_by_label || n.authored_by || '—')}</span></div>
            ${why ? `<div class="kg-gov-draft-why">${why}</div>` : ''}
            ${attrsLine(n)}
            ${stepper(n)}
            ${receiptLine(n)}
            ${readbackLine(n)}
            ${otherLandings(n)}
          </div>`;
    }).join('');
    // an extension's trust reading with the mover NAMED (a base re-sign reads `changed · base
    // moved`, never the base's own seal)
    const trustChip = (t) => {
        if (!t) return '';
        const st = t.state || 'unsigned';
        const cls = st === 'trusted' ? 'held' : st === 'changed' ? 'violated' : 'unverified';
        const word = st === 'trusted' ? 'signed' : st === 'changed' ? `changed · ${(t.drift || []).join(' · ') || 'content moved'}` : 'unsigned';
        return `<span class="kg-gov-rb kg-gov-rb--${cls} kg-gov-ext-trust">${esc(word)}</span>`;
    };
    // one extension, counted in WORDS (kgVocabulary), and the acts it is owed
    const extInfo = (x) => {
        const pub = x.published || 0, un = x.unpublished || 0;
        const mineNodes = nodes.filter(n => !x.extension_id || !n.extension_id || n.extension_id === x.extension_id);
        const unWords = draftBreakdown(mineNodes.filter(n => (n.status || 'draft') !== 'published')) || nodesLabel(un);
        const pubWords = pub ? (draftBreakdown(mineNodes.filter(n => n.status === 'published')) || nodesLabel(pub)) : 'nothing';
        // the road is propose → validate → LAND → publish: while a node of this extension is
        // not landed, Publish is not the act owed (published, agents would read a column the
        // warehouse does not have) — landing is
        const unlanded = (x.unlanded || 0) > 0;   // the server's publish-order rule (kgstore.types.awaits_landing)
        return { pub, un, unWords, pubWords,
                 counts: `${esc(pubWords)} published${un ? ` · ${esc(unWords)} in draft` : ''}`,
                 canPublish: actOn && x.mine && x.can_publish && un > 0 && !unlanded,
                 publishAfterLand: actOn && x.mine && x.can_publish && un > 0 && unlanded,
                 canSign: actOn && pub > 0 && (!x.trust || x.trust.state !== 'trusted') };
    };
    const signBtn = (x) => `<button type="button" class="kg-gov-ext-sign" data-extension-id="${esc(x.extension_id)}">Sign extension</button>`;
    // the Deploy box: a target (preselected by the server's rule when it decides), a preview IN
    // WORDS before anything runs, then Land — enabled only by a preview of THAT target, so nothing
    // lands on a single click and nothing lands unseen (the friction budget: Preview · Land · Publish)
    // C3: once every draft has landed, a deployed node may still land on ANOTHER warehouse —
    // offered only for the drivers some deployed node has not landed on (filtered once the
    // person's connections are read; the box removes itself when there is none)
    const landedNodes = nodes.filter(n => ['deployed', 'published'].includes(n.status || 'draft'));
    const landedEverywhere = DEPLOY_DRIVERS.filter(dr => landedNodes.length && landedNodes.every(n => (n.landed_on || []).includes(dr)));
    const another = actOn && !owed && landedNodes.length > 0 && landedEverywhere.length < DEPLOY_DRIVERS.length;
    const toLand = nodes.filter(n => ['draft', 'validated'].includes(n.status || 'draft')).map(n => String(n.name || '').split('.').pop());
    const landNames = toLand.length <= 3 ? toLand.map(l => `<code>${esc(l)}</code>`).join(', ') : `${toLand.slice(0, 2).map(l => `<code>${esc(l)}</code>`).join(', ')} and ${toLand.length - 2} more`;
    const deployRow = (owed || another) ? `<div id="kg-gov-deploy" class="kg-gov-deploy" data-mode="${owed ? 'owed' : 'another'}" data-landed="${owed ? '' : esc(landedEverywhere.join(','))}" data-default="${esc((authored.default_target || {}).id || '')}" data-default-why="${esc((authored.default_target || {}).why || '')}">
        <div class="kg-gov-deploy-head">${owed ? `Land ${landNames} on the warehouse` : 'Land on another warehouse'}</div>
        <div class="kg-gov-deploy-row">
          <select id="kg-gov-deploy-target" aria-label="Deployment target (a Database Connection)"><option value="">Choose a database connection…</option></select>
          <button type="button" class="kg-gov-deploy-preview" disabled>Preview</button>
          <button type="button" class="kg-gov-deploy-land" disabled>Land it</button>
        </div>
        <div id="kg-gov-deploy-words" class="kg-gov-deploy-words" aria-live="polite">Where it lands is decided by the platform — the model's own database when the connection's login owns it, otherwise a platform-owned sibling; the preview says where and how large, and both can be changed before landing.</div>
      </div>` : '';
    // the person's own extension: one line (counts + trust reading), then the ONE place its acts
    // are offered, in the road's order — land → publish → sign
    const mine = exts.find(x => x.mine);
    const mi = mine ? extInfo(mine) : null;
    // ONE act is offered at a time — the first the road owes; what follows it is named, never a
    // second button (a Sign beside an owed Publish signs content the Publish then moves)
    const steps = [], then = [];
    if (owed) steps.push(deployRow);
    if (mi && mi.publishAfterLand) then.push(`publish ${esc(mi.unWords)} — once it has landed`);
    if (mi && mi.canPublish) (steps.length ? then.push(`publish ${esc(mi.unWords)}`)
        : steps.push(`<div class="kg-gov-next-act"><button type="button" class="kg-gov-ext-publish" data-extension-id="${esc(mine.extension_id)}">Publish ${esc(mi.unWords)}</button><span class="kg-gov-next-why">released agents read it from their next turn and it travels with the graph — a person's act, never an agent's</span></div>`));
    // publishing moves the signed content, so a Sign is owed after any publish still to come
    if (mi && (mi.canSign || mi.canPublish || mi.publishAfterLand))
        (steps.length ? then.push('sign the extension')
            : steps.push(`<div class="kg-gov-next-act">${signBtn(mine)}<span class="kg-gov-next-why">binds what is published to the base's own seal — a re-signed base then reads "base moved"</span></div>`));
    if (then.length) steps.push(`<div class="kg-gov-next-then"><span class="kg-gov-ext-next">then ${then.join(', then ')}</span></div>`);
    const nextBox = !actOn ? ''
        : steps.length ? `<div class="kg-gov-next"><div class="kg-gov-next-head">Next</div>${steps.join('')}</div>`
        : mi ? `<div class="kg-gov-next kg-gov-next--done">Nothing owed — every node has landed and is published${mine.trust && mine.trust.state === 'trusted' ? ', and the extension is signed' : ''}.</div>` : '';
    const mineHead = mine ? `<div class="kg-gov-sec-head" data-extension-id="${esc(mine.extension_id)}"><span class="kg-gov-sec-title">Your extension</span><span class="kg-gov-ext-counts">${mi.counts}</span>${trustChip(mine.trust)}</div>` : '';
    // other extenders' layers: read-only here, except the owner's Sign when one is owed
    const others = exts.filter(x => !x.mine);
    const extRows = others.length ? `<div id="kg-gov-exts" class="kg-gov-exts">
        <div class="kg-gov-deploy-head">${mine ? 'Other extensions on this graph' : 'Extensions on this graph'} (${others.length})</div>
        ${others.map(x => { const i = extInfo(x); return `<div class="kg-gov-ext" data-extension-id="${esc(x.extension_id)}">
              <span class="kg-gov-ext-label">${esc(x.label || x.extension_id)}</span>
              <span class="kg-gov-ext-counts">${i.counts}</span>
              ${trustChip(x.trust)}
              ${i.canSign ? signBtn(x) : ''}
            </div>`; }).join('')}
      </div>` : '';
    return `<div id="kg-gov-drafts" class="kg-gov-sec" data-owed="${owed ? 1 : 0}" data-act="${actOn ? 1 : 0}" data-publish-owed="${publishOwed ? 1 : 0}">
      ${mineHead}
      ${nextBox}
      <div id="kg-gov-ext-words" class="kg-gov-deploy-words" aria-live="polite"></div>
      <div class="kg-gov-sub">Added to the model · ${esc(draftBreakdown(nodes) || nodesLabel(nodes.length))}</div>
      <div class="kg-gov-nodes">${rows}${another ? deployRow : ''}</div>
      ${extRows}
      <style>
        .kg-gov-draft { font-size: 12px; color: var(--text-primary, #e5e7eb); }
        .kg-gov-sec { display: flex; flex-direction: column; gap: 10px; }
        .kg-gov-sec-head { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
        .kg-gov-sec-title { font-size: 13px; font-weight: 700; color: var(--text-primary, #e5e7eb); }
        .kg-gov-next { border: 1px solid var(--teradata-orange, #F15F22); border-radius: 10px; padding: 10px 12px; display: flex; flex-direction: column; gap: 10px; background: rgba(241,95,34,0.04); }
        .kg-gov-next--done { border-color: var(--border-primary, rgba(148,163,184,0.25)); background: transparent; font-size: 12px; color: var(--text-muted, #94a3b8); }
        .kg-gov-next-head { font-size: 10.5px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--teradata-orange, #F15F22); }
        .kg-gov-next .kg-gov-deploy { border-top: 0; padding-top: 0; margin-top: 0; }
        .kg-gov-next-act { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
        .kg-gov-next-act button { padding: 5px 12px; font-size: 12px; font-weight: 600; border-radius: 7px; cursor: pointer; border: 1px solid var(--teradata-orange, #F15F22); background: var(--bg-overlay, rgba(0,0,0,0.2)); color: var(--teradata-orange, #F15F22); }
        .kg-gov-next-act button:disabled { opacity: .45; cursor: default; }
        .kg-gov-next-why { font-size: 11px; color: var(--text-muted, #94a3b8); flex: 1 1 220px; }
        .kg-gov-next-then { font-size: 11px; }
        .kg-gov-sub { font-size: 12px; font-weight: 600; color: var(--text-primary, #e5e7eb); margin-top: 2px; }
        .kg-gov-nodes { background: var(--bg-overlay, rgba(0,0,0,0.15)); border-radius: 8px; padding: 8px 12px; display: flex; flex-direction: column; gap: 10px; }
        #kg-gov-ext-words:empty { display: none; }
        .kg-gov-base { display: flex; flex-direction: column; gap: 10px; border-top: 1px solid var(--border-secondary, rgba(148,163,184,0.15)); padding-top: 14px; }
        .kg-gov-sec-note { font-size: 11px; color: var(--text-muted, #94a3b8); line-height: 1.45; }
        .kg-gov-draft-head { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
        .kg-gov-draft-kind, .kg-gov-draft-rule, .kg-gov-draft-by { font-size: 10.5px; color: var(--text-muted, #94a3b8); }
        .kg-gov-draft-rule { padding: 0 6px; border: 1px solid var(--border-primary, rgba(148,163,184,0.25)); border-radius: 999px; }
        .kg-gov-draft-why { font-size: 11px; color: var(--text-muted, #94a3b8); margin-top: 2px; }
        .kg-gov-steps { display: flex; align-items: center; gap: 6px; margin-top: 5px; font-size: 10.5px; letter-spacing: .04em; text-transform: uppercase; }
        .kg-gov-step { padding: 1px 7px; border-radius: 999px; border: 1px solid var(--border-primary, rgba(148,163,184,0.25)); color: var(--text-muted, #94a3b8); }
        .kg-gov-step--done { border-style: solid; color: var(--trust-pass, #10b981); border-color: var(--trust-pass, #10b981); }
        .kg-gov-step--now { border-style: dashed; color: var(--text-primary, #e5e7eb); border-color: var(--teradata-orange, #F15F22); }
        .kg-gov-step--next { opacity: .55; }
        .kg-gov-step--skipped { opacity: .7; border-style: dotted; }
        .kg-gov-draft-attrs { font-size: 11px; color: var(--text-muted, #94a3b8); margin-top: 2px; }
        .kg-gov-draft-attrs code { font-size: 10.5px; color: var(--text-primary, #e5e7eb); }
        .kg-gov-draft-receipt { font-size: 11px; color: var(--text-muted, #94a3b8); margin-top: 4px; }
        .kg-gov-draft-receipt code { font-size: 10.5px; color: var(--text-primary, #e5e7eb); }
        .kg-gov-draft-readback { font-size: 11px; color: var(--text-muted, #94a3b8); margin-top: 3px; }
        .kg-gov-draft-readback code { font-size: 10.5px; color: var(--text-primary, #e5e7eb); }
        .kg-gov-rb { padding: 0 6px; border-radius: 999px; border: 1px solid var(--border-primary, rgba(148,163,184,0.25)); font-size: 10.5px; letter-spacing: .03em; }
        .kg-gov-rb--held { color: var(--trust-pass, #10b981); border-color: var(--trust-pass, #10b981); }
        .kg-gov-rb--partial { color: var(--trust-warn, #f59e0b); border-color: var(--trust-warn, #f59e0b); }
        .kg-gov-rb--violated { color: var(--trust-danger, #ef4444); border-color: var(--trust-danger, #ef4444); }
        .kg-gov-rb--unverified { border-style: dashed; }
        .kg-gov-rb-viol { color: var(--trust-danger, #ef4444); margin-top: 2px; }
        .kg-gov-deploy { border-top: 1px solid var(--border-secondary, rgba(148,163,184,0.12)); padding-top: 8px; margin-top: 2px; }
        .kg-gov-deploy-head { font-size: 12px; font-weight: 600; color: var(--text-primary, #e5e7eb); margin-bottom: 6px; }
        .kg-gov-deploy-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
        .kg-gov-deploy-row select { font-size: 12px; padding: 5px 8px; border-radius: 6px; max-width: 260px; background: var(--bg-overlay, rgba(0,0,0,0.2)); border: 1px solid var(--border-primary, rgba(148,163,184,0.25)); color: var(--text-primary, #e5e7eb); }
        .kg-gov-deploy-row button { padding: 5px 12px; font-size: 12px; font-weight: 600; border-radius: 7px; cursor: pointer; border: 1px solid var(--border-primary, rgba(148,163,184,0.25)); background: var(--bg-overlay, rgba(0,0,0,0.2)); color: var(--text-primary, #e5e7eb); }
        .kg-gov-deploy-row button:disabled { opacity: .45; cursor: default; }
        .kg-gov-deploy-row .kg-gov-deploy-land:not(:disabled) { border-color: var(--teradata-orange, #F15F22); color: var(--teradata-orange, #F15F22); }
        .kg-gov-deploy-words { font-size: 11px; color: var(--text-muted, #94a3b8); margin-top: 6px; line-height: 1.45; }
        .kg-gov-deploy-words .warn { color: var(--trust-warn, #f59e0b); }
        .kg-gov-prov-edit { display: flex; flex-wrap: wrap; gap: 6px 14px; margin-top: 6px; font-size: 11px; color: var(--text-muted, #94a3b8); }
        .kg-gov-prov-edit input { font: inherit; color: var(--text-primary); background: var(--bg-primary, transparent);
            border: 1px solid var(--border-secondary, rgba(148,163,184,0.25)); border-radius: 4px; padding: 2px 6px; margin-left: 4px; }
        .kg-gov-prov-edit .kg-gov-prov-parent { width: 14em; } .kg-gov-prov-edit .kg-gov-prov-mb { width: 7em; }
        .kg-gov-step-sep { flex: 0 0 10px; height: 1px; background: var(--border-primary, rgba(148,163,184,0.25)); }
        .kg-gov-exts { border-top: 1px solid var(--border-secondary, rgba(148,163,184,0.12)); padding-top: 8px; margin-top: 2px; }
        .kg-gov-ext { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 12px; color: var(--text-primary, #e5e7eb); padding: 3px 0; }
        .kg-gov-ext-label { font-weight: 600; }
        .kg-gov-ext-counts { font-size: 11px; color: var(--text-muted, #94a3b8); }
        .kg-gov-ext-next { font-size: 11px; color: var(--teradata-orange, #F15F22); }
        .kg-gov-access { margin-top: 8px; padding-top: 6px; border-top: 1px solid var(--border-secondary, rgba(148,163,184,0.15)); }
        .kg-gov-access-head { font-size: 11px; font-weight: 600; color: var(--text-primary, #e5e7eb); margin: 4px 0; }
        .kg-gov-access-row { font-size: 11px; color: var(--text-muted, #94a3b8); margin: 2px 0; }
        .kg-gov-access-why { opacity: .85; }
        .kg-gov-acc { display: inline-block; min-width: 52px; text-align: center; font-size: 10px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; padding: 0 6px; border-radius: 999px; border: 1px solid; margin-right: 4px; }
        .kg-gov-acc--held { color: var(--trust-pass, #10b981); border-color: var(--trust-pass, #10b981); }
        .kg-gov-acc--missing { color: var(--trust-danger, #ef4444); border-color: var(--trust-danger, #ef4444); }
        .kg-gov-grants { font-family: ui-monospace, monospace; font-size: 11px; white-space: pre-wrap; margin: 4px 0 0; padding: 6px 8px; border-radius: 6px; background: var(--bg-overlay, rgba(0,0,0,0.2)); color: var(--text-primary, #e5e7eb); user-select: all; }
        .kg-gov-ext button { padding: 3px 10px; font-size: 11px; font-weight: 600; border-radius: 7px; cursor: pointer; border: 1px solid var(--border-primary, rgba(148,163,184,0.25)); background: var(--bg-overlay, rgba(0,0,0,0.2)); color: var(--text-primary, #e5e7eb); }
        .kg-gov-ext button:disabled { opacity: .45; cursor: default; }
        .kg-gov-ext .kg-gov-ext-publish:not(:disabled) { border-color: var(--teradata-orange, #F15F22); color: var(--teradata-orange, #F15F22); }
      </style>
    </div>`;
}

async function openKgGovernanceDialog(kgId, kgName) {
    const token = () => localStorage.getItem('tda_auth_token');
    const H = () => ({ 'Authorization': `Bearer ${token()}` });
    const base = `/api/v1/knowledge-graph/${encodeURIComponent(kgId)}`;

    const back = document.createElement('div');
    back.style.cssText = 'position:fixed;inset:0;z-index:10000;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.55);';
    back.innerHTML = `<div class="kg-gov-dialog" style="width:min(640px,94vw);max-height:88vh;overflow:auto;background-color:var(--bg-primary,#141619);background-image:linear-gradient(var(--modal-bg,transparent),var(--modal-bg,transparent));border:1px solid var(--border-primary,rgba(148,163,184,0.25));border-radius:14px;box-shadow:0 20px 60px rgba(0,0,0,0.45);"></div>`;
    document.body.appendChild(back);
    const card = back.querySelector('.kg-gov-dialog');   // never a *-card class: the global card rule made it translucent
    const close = () => { back.remove(); document.removeEventListener('keydown', onKey, true); };
    const onKey = (e) => { if (e.key === 'Escape') close(); };
    back.addEventListener('click', (e) => { if (e.target === back) close(); });
    document.addEventListener('keydown', onKey, true);

    const badge = (txt, style) => `<span style="font-size:11px;font-weight:600;padding:2px 8px;border-radius:6px;background:${style[0]};color:${style[1]};">${txt}</span>`;
    const btn = (cls, label, accent) => `<button class="${cls}" style="padding:6px 12px;font-size:12px;font-weight:600;border-radius:7px;cursor:pointer;background:${accent}22;color:${accent};border:1px solid ${accent}55;">${label}</button>`;

    async function load() {
        card.innerHTML = `<div style="padding:28px;text-align:center;color:var(--text-muted,#94a3b8);">Loading governance…</div>`;
        let lc = {}, cf = {}, authored = { nodes: [] };
        try {
            [lc, cf, authored] = await Promise.all([
                fetch(`${base}/lifecycle`, { headers: H() }).then(r => r.json()),
                fetch(`${base}/conformance`, { headers: H() }).then(r => r.json()),
                fetch(`/api/v1/knowledge-graph/authored?kg_id=${encodeURIComponent(kgId)}`, { headers: H() }).then(r => r.ok ? r.json() : { nodes: [] }).catch(() => ({ nodes: [] })),
            ]);
        } catch (_) {}
        render(lc || {}, cf || {}, authored || { nodes: [] });
    }

    function render(lc, cf, authored) {
        const life = lc.lifecycle || {};
        const status = life.lifecycle_status || 'draft';
        const policy = lc.policy || {};
        const conf = (cf.conformance) || {};
        const confStatus = conf.status || 'unknown';
        const issues = conf.issues || [];
        const audit = lc.audit || [];

        const issuesHtml = issues.length
            ? issues.map(i => `<div style="display:flex;gap:8px;align-items:baseline;padding:4px 0;">
                  <span style="font-size:10px;font-weight:700;color:${_GOV_SEV[i.severity] || '#94a3b8'};text-transform:uppercase;width:54px;flex-shrink:0;">${i.severity}</span>
                  <span style="font-size:12px;color:var(--text-primary,#e5e7eb);"><strong>${i.code}</strong> (${i.count}) — ${i.message}</span></div>`).join('')
            : `<div style="font-size:12px;color:#4ade80;">No issues — fully conformant.</div>`;

        // State-driven actions.
        let actions = '';
        if (status === 'draft') {
            if (policy.require_approval) {
                const ap = life.approval_status || 'none';
                if (ap === 'pending') actions += btn('kg-gov-act', 'Approve', '#4ade80') + btn('kg-gov-act-reject', 'Reject', '#ef4444');
                else if (ap !== 'approved') actions += btn('kg-gov-act-propose', 'Propose', '#3b82f6');
            }
            actions += btn('kg-gov-publish', 'Publish version', '#34d399');
        } else if (status === 'published') {
            actions += btn('kg-gov-publish', 'Publish a new version', '#34d399') + btn('kg-gov-act-deprecate', 'Deprecate', '#f59e0b');
        }

        const blockWarn = (confStatus === 'nonconformant' && policy.block_nonconformant_publish)
            ? `<label style="display:flex;gap:6px;align-items:center;font-size:11px;color:#ef4444;margin-top:6px;"><input type="checkbox" id="kg-gov-override"> Override — publish despite non-conformance</label>` : '';

        card.innerHTML = `
          <div style="display:flex;align-items:center;justify-content:space-between;padding:14px 18px;border-bottom:1px solid var(--border-secondary,rgba(148,163,184,0.15));">
            <div style="font-size:14px;font-weight:700;color:var(--text-primary,#e5e7eb);">Govern ontology · <span style="color:var(--text-muted,#94a3b8);">${kgName || ''}</span></div>
            <button class="kg-gov-close" style="background:none;border:none;color:var(--text-muted,#94a3b8);font-size:20px;cursor:pointer;line-height:1;">×</button>
          </div>
          <div style="padding:16px 18px;display:flex;flex-direction:column;gap:14px;">
            ${_govDraftsHtml(authored)}
            <div class="kg-gov-base">
              <div class="kg-gov-sec-head">
                <span class="kg-gov-sec-title">The model's own release</span>
                ${badge(status === 'published' && life.semantic_version ? `Published v${life.semantic_version}` : status.charAt(0).toUpperCase() + status.slice(1), _GOV_LC_STYLE[status] || _GOV_LC_STYLE.draft)}
                ${badge(confStatus, _GOV_CONF_STYLE[confStatus] || ['rgba(148,163,184,0.15)', '#94a3b8'])}
              </div>
              <div class="kg-gov-sec-note">A numbered version of the whole model, judged against the canonical ontology topology${(authored.nodes || []).length ? ' — your extension is published on its own, above' : ''}.
                Policy: approval ${policy.require_approval ? 'required' : 'not required'} · non-conformant publish ${policy.block_nonconformant_publish ? 'blocked' : 'allowed'}${policy.require_approval ? ` · approvers: ${(policy.approver_tiers || []).join(', ')}` : ''}.</div>
              ${issues.length ? `<div style="background:var(--bg-overlay,rgba(0,0,0,0.15));border-radius:8px;padding:8px 12px;">${issuesHtml}</div>` : ''}
              ${status !== 'deprecated' ? `
              <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
                <input id="kg-gov-semver" aria-label="Version of the model" placeholder="version e.g. 1.0.0" value="${life.semantic_version || ''}" style="font-size:12px;padding:6px 8px;width:130px;border-radius:6px;background:var(--bg-overlay,rgba(0,0,0,0.2));border:1px solid var(--border-primary,rgba(148,163,184,0.25));color:var(--text-primary,#e5e7eb);">
                ${actions}
              </div>${blockWarn}` : `<div style="font-size:12px;color:#f59e0b;">This ontology is deprecated (terminal state).</div>`}
              ${audit.length ? `<details style="font-size:11px;color:var(--text-muted,#94a3b8);"><summary style="cursor:pointer;">Lifecycle trail (${audit.length})</summary>
                <div style="margin-top:6px;display:flex;flex-direction:column;gap:3px;">${audit.map(a => `<div>${_govEsc(_govAuditWords(a))}</div>`).join('')}</div></details>` : ''}
            </div>
          </div>`;

        card.querySelector('.kg-gov-close').addEventListener('click', close);

        const doAction = async (action, body) => {
            try {
                const r = await fetch(`${base}/lifecycle/${action}`, {
                    method: 'POST', headers: { ...H(), 'Content-Type': 'application/json' },
                    body: JSON.stringify(body || {}),
                });
                const d = await r.json().catch(() => ({}));
                if (d.allowed) {
                    let msg = `${action} → ${d.status}`;
                    if (d.badge) msg += ` (${d.badge})`;
                    if (d.attestation && d.attestation.signed) msg += ' · signed attestation';
                    showAppBanner(msg, d.badge && d.badge.includes('nonconformant') ? 'warning' : 'success');
                } else {
                    showAppBanner(`${action} blocked: ${d.reason || 'not allowed'}`, 'warning');
                }
            } catch (err) {
                showAppBanner(`${action} failed: ${err.message}`, 'error');
            }
            await load();
            try { await loadKnowledgeGraphsIntelligenceTab(); } catch (_) {}
            if (state.resourceData.knowledgeGraphs !== null) { try { await loadKnowledgeGraphsPanel(); } catch (_) {} }
        };

        const semver = () => (card.querySelector('#kg-gov-semver')?.value || '').trim();
        card.querySelector('.kg-gov-publish')?.addEventListener('click', () => {
            const sv = semver();
            if (!sv) { showAppBanner('Enter a version (e.g. 1.0.0) to publish', 'warning'); return; }
            doAction('publish', { semantic_version: sv, override_nonconformant: !!card.querySelector('#kg-gov-override')?.checked });
        });
        card.querySelector('.kg-gov-act-propose')?.addEventListener('click', () => doAction('propose', {}));
        card.querySelector('.kg-gov-act')?.addEventListener('click', () => doAction('approve', {}));
        card.querySelector('.kg-gov-act-reject')?.addEventListener('click', () => doAction('reject', {}));
        card.querySelector('.kg-gov-act-deprecate')?.addEventListener('click', () => doAction('deprecate', {}));
        _wireGovDeploy(card, kgId, load);
        _wireGovPublish(card, kgId, load);
    }

    load();
}

/**
 * The Extensions row of the Govern dialog (PROP-008 A5): Publish stamps the extender's
 * unpublished nodes (a person's act — the REST refuses an agent actor and a non-approver),
 * Sign attests the published layer against the base's seal. Each act re-reads the dialog
 * and tells the Workspace the authored layer changed.
 */
function _wireGovPublish(card, kgId, reload) {
    // the acts live in Next (the caller's own extension) and on other extenders' rows
    const box = card;
    const words = box.querySelector('#kg-gov-ext-words');
    const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const H = () => ({ 'Authorization': `Bearer ${localStorage.getItem('tda_auth_token')}`, 'Content-Type': 'application/json' });
    const say = (html) => { if (words) words.innerHTML = html; };
    const after = async () => {
        UderiaBus.announce('knowledge_graph', 'updated', [kgId], { aspect: 'authored' });
        await reload();
    };
    box.querySelectorAll('.kg-gov-ext-publish').forEach(b => b.addEventListener('click', async () => {
        const ext = b.dataset.extensionId;
        const label = b.textContent; b.disabled = true; b.textContent = 'Publishing…';
        try {
            const r = await fetch('/api/v1/knowledge-graph/authored/publish', { method: 'POST', headers: H(), body: JSON.stringify({ kg_id: kgId, extension_id: ext }) });
            const d = await r.json().catch(() => ({}));
            if (!r.ok || !d.allowed) throw new Error(d.reason || d.message || `HTTP ${r.status}`);
            const n = (d.published || []).length;
            showAppBanner(`Published ${nodesLabel(n)} of ${ext}${d.badge ? ' (' + d.badge + ')' : ''}`, 'success');
            await after();
        } catch (err) { say(`<span class="warn">${esc(err.message)}</span>`); b.disabled = false; b.textContent = label; }
    }));
    box.querySelectorAll('.kg-gov-ext-sign').forEach(b => b.addEventListener('click', async () => {
        const ext = b.dataset.extensionId;
        b.disabled = true; b.textContent = 'Signing…';
        try {
            const r = await fetch(`/api/v1/trust/ontology-extension/${encodeURIComponent(kgId)}/${encodeURIComponent(ext)}/sign-off`, { method: 'POST', headers: H(), body: '{}' });
            const d = await r.json().catch(() => ({}));
            if (!r.ok || !d.signed) throw new Error(d.reason || d.error || d.message || `HTTP ${r.status}`);
            showAppBanner(`Extension ${ext} signed${d.verdict && d.verdict.status ? ' · ' + d.verdict.status : ''}`, 'success');
            await after();
        } catch (err) { say(`<span class="warn">${esc(err.message)}</span>`); b.disabled = false; b.textContent = 'Sign extension'; }
    }));
}

/**
 * The Deploy row of the Govern dialog (PROP-008 A3): the person picks a target, reads the
 * preview in words (a dry run — no DDL), then lands it. The preview IS the first of the two
 * steps: Land is enabled only by a preview of the target currently selected (changing the
 * target disables it again), so the person has always read the consequence before it lands —
 * an arm-then-confirm on top of that would be a third click showing nothing new (PROP-008's
 * friction budget: draft → published in three clicks). A lone deployable connection is
 * pre-selected. The platform decides where a table lands; this row never sends a database name.
 */
function _wireGovDeploy(card, kgId, reload) {
    const box = card.querySelector('#kg-gov-deploy');
    if (!box) return;
    const sel = box.querySelector('#kg-gov-deploy-target');
    const preview = box.querySelector('.kg-gov-deploy-preview');
    const land = box.querySelector('.kg-gov-deploy-land');
    const words = box.querySelector('#kg-gov-deploy-words');
    const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const H = () => ({ 'Authorization': `Bearer ${localStorage.getItem('tda_auth_token')}` });
    let previewed = null;   // the target the words on screen describe; Land lands only that
    const say = (html) => { words.innerHTML = html; };
    const post = async (body) => {
        const r = await fetch('/api/v1/knowledge-graph/authored/deploy', {
            method: 'POST', headers: { ...H(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ kg_id: kgId, ...body }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.message || `HTTP ${r.status}`);
        return d.deploy || {};
    };
    const describe = (info) => {
        const plan = info.plan || {};
        let out = `<strong>${esc(info.summary || plan.summary || '')}</strong>`;
        // a database to create: where and how large, and why — the person may change both
        const pv = info.provisioning;
        if (pv && pv.parent) out += `<div class="kg-gov-prov">creates <code>${esc((info.landing || {}).database || '')}</code> ${esc(pv.basis || '')}</div>`;
        const inf = info.infrastructure;
        if (inf && inf.primary === false) {
            out += `<div>lands on ${esc(DEPLOY_DRIVER_LABEL[inf.this_deploy] || inf.this_deploy)}; this graph's physical layer is ${esc(DEPLOY_DRIVER_LABEL[inf.graph] || inf.graph)}, so the realisation is recorded on the node, not as a second physical layer</div>`;
        }
        const pend = plan.pending || [];
        if (pend.length) out += `<div>${pend.map(c => `${esc(c.kind.replace(/_/g, ' '))} ${esc(c.identifier)}`).join(' · ')}</div>`;
        (plan.refused || []).forEach(c => { out += `<div class="warn">refused: ${esc(c.identifier)} — ${esc(c.reason)}</div>`; });
        (info.warnings || plan.warnings || []).forEach(w => { out += `<div class="warn">${esc(w)}</div>`; });
        // the design standard (A4): which rules are VERIFIED on this target, and per table whether the
        // plan conforms — the validation a preview performs, in words
        const d = info.design;
        if (d && d.rules) {
            const r = d.rules;
            out += `<div class="kg-gov-design">design standard <code>${esc(d.standard || r.standard || '')}</code> · ${r.verified || 0} of ${(r.rules || []).length} rules verified on ${esc(d.system || 'this target')}${r.unverified ? ` · ${r.unverified} unverified (never executed on this version)` : ''}</div>`;
            Object.entries(d.tables || {}).forEach(([ident, t]) => {
                const keyWord = (d.backend || 'teradata') === 'teradata' ? 'primary index' : 'key';
                const pi = t.pi && t.pi.rule ? ` · ${keyWord}: ${esc(String(t.pi.rule).replace(/_/g, ' '))}` : '';
                out += `<div class="${t.conformance === 'conformant' ? '' : 'warn'}">${esc(ident)} — ${esc(t.conformance)}${pi}${(t.findings || []).map(f => ` · ${esc(f.message)}`).join('')}</div>`;
            });
            if (Array.isArray(info.validated) && info.validated.length) out += `<div>validated: ${info.validated.map(esc).join(', ')}</div>`;
        }
        // ACCESS, before anything runs: what this landing needs of its login — held or missing,
        // why, and the exact GRANT (dbdeploy/access.py); Land waits until nothing is missing
        const acc = info.access;
        if (acc && Array.isArray(acc.rows)) {
            out += `<div class="kg-gov-access"><div class="kg-gov-access-head">Access · the landing login <code>${esc(acc.login)}</code> ${!acc.readable ? 'could not be checked — the dictionary was not readable' : acc.ok ? 'holds everything this landing needs' : 'lacks what this landing needs — nothing lands until it is granted'}</div>`
                + acc.rows.map(r => `<div class="kg-gov-access-row"><span class="kg-gov-acc kg-gov-acc--${r.held ? 'held' : 'missing'}">${r.held ? 'held' : 'missing'}</span> ${esc(r.rights.join(', '))} on <code>${esc(r.object)}</code> <span class="kg-gov-access-why">— ${esc(r.why)}</span></div>`).join('')
                + ((acc.grants || []).length ? `<div class="kg-gov-access-head">Grant exactly (as an administrator of the warehouse):</div><pre class="kg-gov-grants">${esc(acc.grants.join('\n'))}</pre>` : '')
                + `</div>`;
        }
        // the readback after a landing: what the warehouse holds, rule by rule
        const rb = info.readback;
        if (rb && rb.summary) {
            out += `<div class="kg-gov-readback ${rb.status === 'conformant' ? '' : 'warn'}">readback: ${esc(rb.summary)}</div>`;
            (rb.issues || []).forEach(i => { out += `<div class="warn">${esc(i.code.replace(/_/g, ' '))} (${i.count}): ${esc(i.message)}${(i.refs || []).slice(0, 3).map(x => ` · ${esc(x)}`).join('')}</div>`; });
        }
        return out;
    };
    // the targets: the caller's database connections a deployment backend exists for
    (async () => {
        try {
            const r = await fetch('/api/v1/database-connections', { headers: H() });
            const d = await r.json().catch(() => ({}));
            const landed = (box.dataset.landed || '').split(',').filter(Boolean);
            const items = (d.connections || d.items || (Array.isArray(d) ? d : [])).filter(c => DEPLOY_DRIVERS.includes(String(c.driver || '').toLowerCase())
                && !landed.includes(String(c.driver || '').toLowerCase()));
            if (!items.length && box.dataset.mode === 'another') { box.remove(); return; }   // nothing left to offer (Rule 8.5)
            if (!items.length) { say(`No ${DEPLOY_DRIVERS.map(x => DEPLOY_DRIVER_LABEL[x]).join(' or ')} database connection yet — add one under Components → Connectors → Database Connection, then come back.`); return; }
            items.forEach(c => { const o = document.createElement('option'); o.value = c.id; o.textContent = `${c.name || c.id} · ${DEPLOY_DRIVER_LABEL[String(c.driver).toLowerCase()]} (${c.username || '—'}@${c.host || '—'})`; sel.appendChild(o); });
            // nothing to choose, or the server's rule decides (action_types.default_connection: the
            // connection this graph's tables were landed through) — never a click owed, the why said
            const dflt = items.length === 1 ? items[0].id : (items.some(c => c.id === box.dataset.default) ? box.dataset.default : '');
            if (dflt) sel.value = dflt;
            if (dflt && items.length > 1 && box.dataset.defaultWhy) say(`Preselected: ${esc(box.dataset.defaultWhy)}.`);
            preview.disabled = false;
        } catch (err) { say(`Could not list database connections: ${esc(err.message)}`); }
    })();
    // A new database's parent and size: shown (and editable) only when a preview says one must
    // be created; Land sends exactly the values the last preview showed.
    let prov = null, previewedProv = null;
    const provBox = document.createElement('div');
    provBox.className = 'kg-gov-prov-edit';
    provBox.style.display = 'none';
    provBox.innerHTML = `<label>new database under <input class="kg-gov-prov-parent" type="text" spellcheck="false"></label>`
        + `<label>size <input class="kg-gov-prov-mb" type="number" min="1" step="1"> MB</label>`;
    words.after(provBox);
    const provIn = provBox.querySelector('.kg-gov-prov-parent'), mbIn = provBox.querySelector('.kg-gov-prov-mb');
    const provBody = () => (prov ? { database_parent: provIn.value.trim() || undefined,
                                     database_perm_bytes: mbIn.value ? Math.round(Number(mbIn.value) * 1000000) : undefined } : {});
    const unpreview = () => { previewed = null; land.disabled = true; };
    [provIn, mbIn].forEach(i => i.addEventListener('input', () => { unpreview(); say('Preview again — Land lands what the preview showed.'); }));
    sel.addEventListener('change', () => { unpreview(); prov = null; provBox.style.display = 'none'; });
    preview.addEventListener('click', async () => {
        if (!sel.value) { say('Choose a database connection first.'); return; }
        preview.disabled = true; say('Planning…');
        try {
            const body = provBody();
            const info = await post({ deployment_target_id: sel.value, dry_run: true, ...body });
            say(describe(info));
            // a preview IS the validation act: the nodes it validated move to VALIDATED on their
            // road in place (a reload would clear the preview's words and the armed Land)
            const root = card.querySelector('#kg-gov-drafts');
            const ctx = root ? { owed: root.dataset.owed === '1', actOn: root.dataset.act === '1', publishOwed: root.dataset.publishOwed === '1' } : {};
            const stamp = new Date().toISOString();
            (Array.isArray(info.validated) ? info.validated : []).forEach(name => {
                const row = [...card.querySelectorAll('.kg-gov-draft')].find(r => r.dataset.authoredName === name);
                const steps = row && row.querySelector('.kg-gov-steps');
                if (!steps || row.dataset.authoredStatus !== 'draft') return;
                row.dataset.authoredStatus = 'validated';
                steps.outerHTML = _govStepperHtml({ status: 'validated', validated_at: stamp, standard: (info.design || {}).standard }, ctx);
            });
            prov = info.provisioning || null;
            if (prov) {
                provIn.value = prov.parent || '';
                mbIn.value = prov.perm_bytes ? String(Math.round(prov.perm_bytes / 1000000)) : '';
                provBox.style.display = '';
            } else provBox.style.display = 'none';
            previewedProv = provBody();
            // a landing whose login lacks a right would fail half-way: Land waits for the grant
            const blocked = !!(info.access && info.access.readable && !info.access.ok);
            previewed = (info.plan && info.plan.is_noop) || blocked ? null : sel.value;
            land.disabled = !previewed;
        } catch (err) { say(`<span class="warn">${esc(err.message)}</span>`); }
        finally { preview.disabled = false; }
    });
    land.addEventListener('click', async () => {
        if (!previewed || previewed !== sel.value) { land.disabled = true; say('Preview this target first — Land lands what the preview showed.'); return; }
        land.disabled = true; preview.disabled = true; say('Landing…');
        try {
            const info = await post({ deployment_target_id: sel.value, dry_run: false, ...(previewedProv || {}) });
            const res = info.result || {};
            const n = (res.landed || []).length, e = (res.errors || []).length;
            const rc = info.receipt || {};
            const rbs = (info.readback || {}).status;
            showAppBanner(`Landed ${n} statement${n === 1 ? '' : 's'} in ${(info.landing || {}).database || 'the warehouse'}${e ? ` · ${e} failed` : ''}${rc.attestation && rc.attestation.signed ? ' · receipt sealed' : ''}${rbs ? ` · readback ${rbs}` : ''}`, e || (rbs && rbs !== 'conformant') ? 'warning' : 'success');
            UderiaBus.announce('knowledge_graph', 'updated', [kgId], { aspect: 'authored' });
            await reload();
            // the landing's own words survive the re-render: the readback is read on the fresh dialog
            const w2 = card.querySelector('#kg-gov-deploy-words');
            if (w2 && info.readback) w2.innerHTML = describe({ summary: `landed · ${(info.readback || {}).summary || ''}`, readback: info.readback, plan: {} });
        } catch (err) { say(`<span class="warn">${esc(err.message)}</span>`); land.disabled = false; preview.disabled = false; }
    });
}

function handleDelete(kgIdOrProfileId, profileName) {
    showConfirmation(
        'Delete Knowledge Graph',
        `<p>Are you sure you want to delete the knowledge graph for <strong>${profileName}</strong>?</p>
         <p class="mt-2 text-sm text-gray-400">This will permanently remove all entities and relationships. This action cannot be undone.</p>`,
        async () => {
            try {
                await API.deleteKnowledgeGraph(null, kgIdOrProfileId);
                showAppBanner('Knowledge graph deleted', 'success');
                await refreshKnowledgeGraphsPanel();
            } catch (err) {
                console.error('Delete failed:', err);
                showAppBanner(`Delete failed: ${err.message}`, 'error');
            }
        }
    );
}

async function handleActivate(kgId) {
    const activeProfileId = state.currentResourcePanelProfileId;
    if (!activeProfileId) return;

    try {
        await API.activateKgForProfile(kgId, activeProfileId);
        showAppBanner('Knowledge graph activated', 'success');
        await refreshKnowledgeGraphsPanel();
        // Also refresh Intelligence tab if visible
        const intelContainer = document.getElementById(_kgIntelCfg.gridId);
        if (intelContainer) await loadKnowledgeGraphsIntelligenceTab();
    } catch (err) {
        console.error('Activate failed:', err);
        showAppBanner(`Activate failed: ${err.message}`, 'error');
    }
}

async function handleDeactivate(kgOwnerProfileId) {
    const activeProfileId = state.currentResourcePanelProfileId;
    if (!activeProfileId) return;

    try {
        // Deactivate all — pass null for kgOwnerProfileId
        await API.activateKgForProfile(null, activeProfileId);
        showAppBanner('Knowledge graph deactivated', 'success');
        await refreshKnowledgeGraphsPanel();
        const intelContainer = document.getElementById(_kgIntelCfg.gridId);
        if (intelContainer) await loadKnowledgeGraphsIntelligenceTab();
    } catch (err) {
        console.error('Deactivate failed:', err);
        showAppBanner(`Deactivate failed: ${err.message}`, 'error');
    }
}

// ── Resource Panel — Delegated click handler ───────────────────────────────

export function handleKnowledgeGraphPanelClick(e) {
    const activateBtn = e.target.closest('.kg-activate-btn');
    if (activateBtn) {
        handleActivate(activateBtn.dataset.kgId || activateBtn.dataset.kgOwnerId);
        return;
    }

    const deactivateBtn = e.target.closest('.kg-deactivate-btn');
    if (deactivateBtn) {
        handleDeactivate(deactivateBtn.dataset.kgId || deactivateBtn.dataset.kgOwnerId);
        return;
    }

    const inspectBtn = e.target.closest('.kg-inspect-btn');
    if (inspectBtn) {
        openKnowledgeGraphInspection(inspectBtn.dataset.profileId, inspectBtn.dataset.profileName, inspectBtn.dataset.kgId);
        return;
    }

    const exportBtn = e.target.closest('.kg-export-btn');
    if (exportBtn) {
        openKgExportMenu(exportBtn, exportBtn.dataset.profileId, exportBtn.dataset.kgId);
        return;
    }

    const governBtn = e.target.closest('.kg-govern-btn');
    if (governBtn) {
        openKgGovernanceDialog(governBtn.dataset.kgId, governBtn.dataset.kgName);
        return;
    }

    const deleteBtn = e.target.closest('.kg-delete-btn');
    if (deleteBtn) {
        handleDelete(deleteBtn.dataset.kgId || deleteBtn.dataset.profileId, deleteBtn.dataset.profileName);
        return;
    }

    const syncBtn = e.target.closest('.kg-sync-btn');
    if (syncBtn) {
        handleKgPanelSync(syncBtn);
        return;
    }

    const packSyncBtn = e.target.closest('.kg-pack-sync-btn');
    if (packSyncBtn) {
        handleKgPanelPackSync(packSyncBtn);
        return;
    }

    const unsubscribeBtn = e.target.closest('.kg-unsubscribe-btn');
    if (unsubscribeBtn) {
        handleKgPanelUnsubscribe(unsubscribeBtn);
        return;
    }
}

/**
 * A pack-sourced copy is synced by the PACK's sync — the one act the pack card offers
 * (`window.syncAgentPackSubscription`, published by the marketplace module), so a graph
 * synced from this card and from the pack card cannot diverge. Both KG surfaces refresh
 * afterwards (the pack sync already refreshes the marketplace, the profiles and the
 * collections).
 */
async function handleKgPanelPackSync(btn) {
    const packId = btn.dataset.packId;
    if (!packId) { showAppBanner('Cannot sync — this copy names no agent pack', 'error'); return; }
    if (typeof window.syncAgentPackSubscription !== 'function') {
        showAppBanner('The marketplace module is not loaded — sync the pack from its marketplace card', 'error');
        return;
    }
    await window.syncAgentPackSubscription(packId, btn);
    await refreshKnowledgeGraphsPanel();
    try { await loadKnowledgeGraphsIntelligenceTab(); } catch (_) { /* tab not mounted */ }
}

async function handleKgPanelSync(btn) {
    const subscriptionId = btn.dataset.subscriptionId;
    const marketplaceId  = btn.dataset.marketplaceId;
    if (!subscriptionId || !marketplaceId) {
        showAppBanner('Cannot sync — missing subscription metadata', 'error');
        return;
    }
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Syncing...';
    try {
        const url = `/api/v1/marketplace/knowledge-graphs/${encodeURIComponent(marketplaceId)}/sync?subscription_id=${encodeURIComponent(subscriptionId)}`;
        const token = await window.authClient.getToken();
        const resp = await fetch(url, { method: 'POST', headers: { 'Authorization': `Bearer ${token}` } });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
        if (data.status === 'up_to_date') {
            showAppBanner('Already up to date with publisher', 'info');
        } else {
            showAppBanner(`Synced: ${addedLabel(data.entities_added, data.relationships_added)}`, 'success');
        }
        await refreshKnowledgeGraphsPanel();
    } catch (err) {
        console.error('Sync failed:', err);
        showAppBanner(`Sync failed: ${err.message}`, 'error');
        btn.disabled = false;
        btn.textContent = original;
    }
}

function handleKgPanelUnsubscribe(btn, onSuccess) {
    const subscriptionId = btn.dataset.subscriptionId;
    const marketplaceId  = btn.dataset.marketplaceId;
    const kgName         = btn.dataset.kgName || 'this knowledge graph';
    if (!subscriptionId || !marketplaceId) {
        showAppBanner('Cannot unsubscribe — missing subscription metadata', 'error');
        return;
    }
    const doUnsub = async () => {
        const original = btn.textContent;
        btn.disabled = true;
        btn.textContent = 'Removing...';
        try {
            const url = `/api/v1/marketplace/knowledge-graphs/${encodeURIComponent(marketplaceId)}/subscribe?subscription_id=${encodeURIComponent(subscriptionId)}`;
            const token = await window.authClient.getToken();
            const resp = await fetch(url, { method: 'DELETE', headers: { 'Authorization': `Bearer ${token}` } });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
            showAppBanner('Unsubscribed — local copy removed', 'success');
            await refreshKnowledgeGraphsPanel();
            if (onSuccess) await onSuccess();
        } catch (err) {
            console.error('Unsubscribe failed:', err);
            showAppBanner(`Unsubscribe failed: ${err.message}`, 'error');
            btn.disabled = false;
            btn.textContent = original;
        }
    };
    if (window.showConfirmBanner) {
        window.showConfirmBanner(
            `Unsubscribe from "${kgName}"? Your local copy and entities will be removed.`,
            doUnsub
        );
    } else {
        doUnsub();
    }
}

// ═══════════════════════════════════════════════════════════════════════════════
//  Intelligence Performance Page — Knowledge Graphs Tab
// ═══════════════════════════════════════════════════════════════════════════════

function _renderAssignedProfilesBadges(kg, showWarnings = false) {
    const assigned = kg.assigned_profiles || [];
    if (assigned.length === 0) return '<span class="text-xs text-gray-600 italic">No additional profiles</span>';
    return assigned.map(ap => {
        const aIfoc = IFOC_CONFIG[ap.profile_type] || { label: ap.profile_type || '?', color: '#6b7280' };
        const isActive = !!ap.is_active;
        const warn = showWarnings ? _componentDisabledWarning(ap.id) : '';

        // Active: full color + green dot; Inactive: dimmed outline
        if (isActive) {
            return `<span class="inline-flex items-center"><span class="kg-intel-activate-badge text-xs font-mono px-2 py-0.5 rounded cursor-pointer hover:opacity-80 transition-opacity"
                          style="color: ${aIfoc.color}; background: ${aIfoc.color}20; border: 1px solid ${aIfoc.color}50;"
                          data-kg-id="${kg.kg_id || kg.profile_id}" data-assigned-id="${ap.id}" data-is-active="1"
                          title="Active — click to deactivate">
                        <span class="inline-block w-1.5 h-1.5 rounded-full mr-1" style="background: #22c55e;"></span>@${ap.tag || ap.name}</span>${warn}</span>`;
        } else {
            return `<span class="inline-flex items-center"><span class="kg-intel-activate-badge text-xs font-mono px-2 py-0.5 rounded cursor-pointer hover:opacity-80 transition-opacity"
                          style="color: ${aIfoc.color}60; background: transparent; border: 1px dashed ${aIfoc.color}30;"
                          data-kg-id="${kg.kg_id || kg.profile_id}" data-assigned-id="${ap.id}" data-is-active="0"
                          title="Inactive — click to activate">@${ap.tag || ap.name}</span>${warn}</span>`;
        }
    }).join(' ');
}


// Source-validated badge — WHEN the graph was last validated against its source data
// and what that covered (from kg.source_validation on the graph's metadata). Absent when
// the graph was never validated: validation is optional, absence is not a warning.
function _sourceValidationBadge(kg) {
    const v = kg && kg.source_validation;
    if (!v || !v.validated_at) return '';
    let when = String(v.validated_at);
    try { when = new Date(v.validated_at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }); } catch (_) { /* keep raw */ }
    const parts = [];
    if (v.tables) parts.push(`${v.tables} table${v.tables === 1 ? '' : 's'} counted`);
    if (v.keys_checked) parts.push(`${v.keys_checked} key${v.keys_checked === 1 ? '' : 's'} checked${v.keys_not_unique ? ` (${v.keys_not_unique} not unique)` : ''}`);
    if (v.shapes) parts.push(`${v.shapes} text-date shape${v.shapes === 1 ? '' : 's'} sampled`);
    if (v.date_ranges) parts.push(`${v.date_ranges} date range${v.date_ranges === 1 ? '' : 's'}`);
    if (v.blank_ratios) parts.push(`${v.blank_ratios} blank ratio${v.blank_ratios === 1 ? '' : 's'}`);
    if (v.probe_errors) parts.push(`${v.probe_errors} probe${v.probe_errors === 1 ? '' : 's'} failed`);
    if (v.seconds) parts.push(`took ${v.seconds >= 90 ? Math.round(v.seconds / 60) + ' min' : Math.round(v.seconds) + ' s'}`);
    const tip = `Validated against the source data on ${when}` + (parts.length ? ` — ${parts.join(' · ')}` : '');
    return `<span class="text-[10px] px-2 py-0.5 rounded font-medium u-tip" data-tip="${_esc(tip)}"
                  style="background:rgba(16,185,129,0.12);color:#10b981;border:1px solid rgba(16,185,129,0.3);white-space:nowrap;">Source-validated · ${_esc(when)}</span>`;
}

function createIntelligenceKGCard(kg) {
    const ifoc = IFOC_CONFIG[kg.profile_type] || { label: kg.profile_type || 'Unknown', color: '#6b7280' };
    const isSubscribed = !!kg.is_subscribed;
    const syncAvailable = !!kg.is_sync_available;
    const syncError = kg.last_sync_error;
    const viaPack = isSubscribed && kg.source_kind === 'agent_pack';   // see createKnowledgeGraphCard

    // Backend badge (Intel tab)
    const backendType = kg.backend_type || 'sqlite';
    const backendLabel = backendType === 'neo4j' ? 'Neo4j' : 'SQLite';
    const backendColor = backendType === 'neo4j' ? '#3b82f6' : '#4ade80';
    const backendBadge = `<span class="text-[10px] px-2 py-0.5 rounded font-medium" style="background:${backendColor}22; color:${backendColor}; border:1px solid ${backendColor}44;">${backendLabel}</span>`;

    // Governed-product lifecycle badges (Tier 4.3) — same vocabulary as the resource panel.
    const _ilc = kg.lifecycle_status || 'draft';
    const _ilcStyle = { draft: ['rgba(148,163,184,0.15)', '#94a3b8'],
                        published: ['rgba(34,197,94,0.15)', '#4ade80'],
                        deprecated: ['rgba(245,158,11,0.15)', '#f59e0b'] }[_ilc] || ['rgba(148,163,184,0.15)', '#94a3b8'];
    const lifecycleBadge = _ilc !== 'draft'
        ? `<span class="text-[10px] font-semibold px-2 py-0.5 rounded" style="background:${_ilcStyle[0]};color:${_ilcStyle[1]};" title="Ontology lifecycle">${_ilc === 'published' ? 'Published' + (kg.semantic_version ? ' v' + kg.semantic_version : '') : _ilc.charAt(0).toUpperCase() + _ilc.slice(1)}</span>`
        : '';
    const _icfStyle = { conformant: ['rgba(34,197,94,0.12)', '#4ade80'],
                        partial: ['rgba(245,158,11,0.12)', '#f59e0b'],
                        nonconformant: ['rgba(239,68,68,0.12)', '#ef4444'] }[kg.conformance_status];
    const conformanceBadge = _icfStyle
        ? `<span class="text-[10px] px-2 py-0.5 rounded" style="background:${_icfStyle[0]};color:${_icfStyle[1]};" title="Ontology conformance">${kg.conformance_status}</span>`
        : '';

    // Owner badge — @TAG format matching assigned-profile badges
    const ownerActive = !!kg.is_active_for_owner;
    const ownerTag = kg.profile_tag || kg.profile_id;
    const ownerBadgeStyle = ownerActive
        ? `color: ${ifoc.color}; background: ${ifoc.color}20; border: 1px solid ${ifoc.color}50;`
        : `color: ${ifoc.color}60; background: transparent; border: 1px dashed ${ifoc.color}30;`;
    const ownerDot = ownerActive
        ? '<span class="inline-block w-1.5 h-1.5 rounded-full mr-1" style="background: #22c55e;"></span>'
        : '';

    // Subscribed indicator (lock icon + status chip)
    const subscribedBadge = isSubscribed ? `
        <span class="text-xs font-medium px-2 py-0.5 rounded inline-flex items-center gap-1"
              style="color:#3b82f6;background:rgba(59,130,246,0.12);border:1px solid rgba(59,130,246,0.35);"
              title="${viaPack ? 'Copy made by your agent-pack subscription &mdash; follows the publisher\'s pack on sync' : 'Subscribed knowledge graph &mdash; rebuilt automatically when the publisher republishes'}">
            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"/></svg>
            Subscribed
        </span>` : '';
    const syncStatusBadge = (() => {
        if (!isSubscribed) return '';
        if (syncError) {
            return `<span class="text-xs font-medium px-2 py-0.5 rounded"
                          style="color:#ef4444;background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.35);"
                          title="${_esc(syncError)}">Sync failed</span>`;
        }
        if (syncAvailable) {
            return `<span class="text-xs font-medium px-2 py-0.5 rounded"
                          style="color:#f59e0b;background:rgba(245,158,11,0.12);border:1px solid rgba(245,158,11,0.35);"
                          title="Publisher has updates available">Update available</span>`;
        }
        return `<span class="text-xs font-medium px-2 py-0.5 rounded"
                      style="color:var(--text-muted,#94a3b8);background:rgba(148,163,184,0.10);border:1px solid rgba(148,163,184,0.18);">In sync</span>`;
    })();

    // Entity type pills
    const typePills = Object.entries(kg.entity_types || {})
        .map(([type, count]) =>
            `<span class="text-xs px-2 py-0.5 rounded-full bg-white/5 text-gray-400">${type} (${count})</span>`
        ).join(' ');

    // Relationship type pills
    const relPills = Object.entries(kg.relationship_types || {})
        .map(([type, count]) =>
            `<span class="text-xs px-2 py-0.5 rounded-full bg-white/5 text-gray-400">${type} (${count})</span>`
        ).join(' ');

    // Assigned profiles section (management view shows component-disabled warnings)
    const assignedBadges = _renderAssignedProfilesBadges(kg, true);

    return `
        <div class="glass-panel p-4 rounded-lg hover:bg-white/5 transition-all" data-profile-id="${kg.profile_id}" data-kg-id="${kg.kg_id || kg.profile_id}">
            <div class="flex items-start gap-3 mb-3">
                <div class="p-2 bg-purple-500/20 rounded-lg relative">
                    <svg class="w-6 h-6 text-purple-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 21a9.004 9.004 0 008.716-6.747M12 21a9.004 9.004 0 01-8.716-6.747M12 21c2.485 0 4.5-4.03 4.5-9S14.485 3 12 3m0 18c-2.485 0-4.5-4.03-4.5-9S9.515 3 12 3m0 0a8.997 8.997 0 017.843 4.582M12 3a8.997 8.997 0 00-7.843 4.582m15.686 0A11.953 11.953 0 0112 10.5c-2.998 0-5.74-1.1-7.843-2.918m15.686 0A8.959 8.959 0 0121 12c0 .778-.099 1.533-.284 2.253m0 0A17.919 17.919 0 0112 16.5c-3.162 0-6.133-.815-8.716-2.247m0 0A9.015 9.015 0 013 12c0-1.605.42-3.113 1.157-4.418"/>
                    </svg>
                    <div class="w-2 h-2 rounded-full absolute top-1 right-1" style="background: ${ifoc.color};"></div>
                </div>
                <div class="flex-1 min-w-0">
                    <div class="flex items-center gap-2 mb-1 flex-wrap">
                        <h3 class="kg-intel-title text-base font-semibold text-white truncate">${kg.kg_name || kg.profile_name || kg.profile_id}</h3>
                        <span class="inline-flex items-center" data-trust-artifact-kind="ontology" data-trust-artifact-id="${kg.kg_id || kg.profile_id}"></span>
                        ${_packPills(kg)}
                        <span class="px-2 py-0.5 text-xs rounded-full font-semibold" style="color: ${ifoc.color}; background: ${ifoc.color}20; border: 1px solid ${ifoc.color}40;">${ifoc.label}</span>
                        ${subscribedBadge}
                        ${syncStatusBadge}
                        ${backendBadge}${_sourceValidationBadge(kg)}
                        ${lifecycleBadge}
                        ${conformanceBadge}
                        <span class="inline-flex items-center"><span class="kg-intel-owner-toggle text-xs font-mono px-2 py-0.5 rounded cursor-pointer hover:opacity-80 transition-opacity"
                              style="${ownerBadgeStyle}"
                              data-kg-id="${kg.kg_id || kg.profile_id}" data-kg-owner-id="${kg.profile_id}" data-is-active="${ownerActive ? '1' : '0'}"
                              title="${ownerActive ? 'Active — click to deactivate for owner' : 'Inactive — click to activate for owner'}">
                            ${ownerDot}@${ownerTag}<span class="opacity-60 font-sans ml-1">(owner)</span>
                        </span>${_componentDisabledWarning(kg.profile_id)}</span>
                    </div>
                    ${viaPack ? `
                        <p class="text-xs mt-0.5" style="color:var(--text-muted,#94a3b8);">
                            Subscribed via agent pack <strong style="color:var(--text-primary,#e5e7eb);">${_esc(kg.pack_name || '')}</strong>${kg.publisher_username ? ` from <strong style="color:var(--text-primary,#e5e7eb);">${_esc(kg.publisher_username)}</strong>` : ''}
                            &middot; Pack last synced ${formatTimeAgo(kg.pack_synced_at || kg.last_synced_at || kg.last_updated)}
                        </p>` : isSubscribed && kg.publisher_username ? `
                        <p class="text-xs mt-0.5" style="color:var(--text-muted,#94a3b8);">
                            Subscribed from <strong style="color:var(--text-primary,#e5e7eb);">${_esc(kg.publisher_username)}</strong>
                            &middot; Last synced ${formatTimeAgo(kg.last_synced_at || kg.last_updated)}
                        </p>` : ''}
                </div>
            </div>

            <!-- Assigned profiles -->
            <div class="mb-3 p-2 rounded-lg bg-white/[0.02] border border-white/5">
                <div class="flex items-center justify-between mb-1.5">
                    <span class="text-xs text-gray-500 font-medium">Assigned to profiles</span>
                    <button class="kg-intel-assign-btn text-xs font-semibold px-2 py-0.5 rounded bg-purple-500/20 text-purple-400 hover:bg-purple-500/30 transition-colors"
                            data-profile-id="${kg.profile_id}" data-kg-id="${kg.kg_id || kg.profile_id}" data-profile-name="${kg.profile_name || kg.profile_id}">
                        Manage
                    </button>
                </div>
                <div class="flex flex-wrap gap-1 kg-assigned-badges" data-owner-id="${kg.profile_id}">
                    ${assignedBadges}
                </div>
            </div>

            <div class="text-xs text-gray-500 mb-2 flex items-center gap-3">
                <span class="text-white font-medium">${sizeLabel(kg)}</span>
                <span>&bull;</span>
                <span><span class="text-white font-medium">${kg.total_relationships}</span> relationships</span>
            </div>

            ${typePills ? `<div class="flex flex-wrap gap-1 mb-2">${typePills}</div>` : ''}
            ${relPills ? `<div class="flex flex-wrap gap-1 mb-2">${relPills}</div>` : ''}

            <p class="text-xs text-gray-500 ${kg.description ? 'mb-1' : 'mb-3'}">Last updated: ${formatTimeAgo(kg.last_updated)}</p>
            ${kg.description ? `<p class="text-xs text-gray-400 mb-3">${kg.description}</p>` : ''}

            <div class="flex gap-2 flex-wrap">
                <button class="kg-intel-inspect-btn card-btn card-btn--primary" data-profile-id="${kg.profile_id}" data-kg-id="${kg.kg_id || kg.profile_id}" data-profile-name="${kg.profile_name || kg.profile_id}">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
                    </svg>
                    Inspect
                </button>
                <button class="kg-intel-export-btn card-btn card-btn--cyan" data-profile-id="${kg.profile_id}" data-kg-id="${kg.kg_id || kg.profile_id}">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"></path>
                    </svg>
                    Export
                </button>
                ${viaPack ? `
                    <button class="kg-intel-pack-sync-btn card-btn ${syncAvailable ? 'card-btn--primary' : 'card-btn--info'} u-tip"
                            data-pack-id="${kg.source_marketplace_id || ''}"
                            data-tip="${syncAvailable ? 'The publisher updated the pack — sync the whole pack subscription (every agent, repository and graph, in place)' : 'Sync the whole pack subscription from the publisher\'s live pack — a no-op when nothing moved'}">
                        ${syncAvailable ? 'Sync pack update' : 'Sync pack'}
                    </button>
                    <span class="kg-pack-managed-note card-btn u-tip" style="cursor:default;opacity:.7;"
                          data-tip="This copy belongs to your agent-pack subscription: it is removed when you unsubscribe from the pack on its marketplace card">Removed with the pack</span>
                ` : isSubscribed ? `
                    ${(syncAvailable || syncError) ? `
                        <button class="kg-intel-sync-btn card-btn card-btn--info"
                                data-subscription-id="${kg.subscription_id}"
                                data-marketplace-id="${kg.source_marketplace_id || ''}"
                                title="${syncError ? 'Retry sync' : 'Pull latest from publisher'}">
                            Sync now
                        </button>` : ''}
                    <button class="kg-intel-unsubscribe-btn card-btn card-btn--warning"
                            data-subscription-id="${kg.subscription_id}"
                            data-marketplace-id="${kg.source_marketplace_id || ''}"
                            data-kg-name="${_esc(kg.kg_name || kg.profile_name || kg.profile_id)}"
                            title="Stop tracking this publisher's KG and remove the local copy">
                        Unsubscribe
                    </button>
                ` : `
                    ${kg.is_resyncable ? `
                    <button class="kg-intel-update-btn card-btn card-btn--info"
                            data-kg-id="${kg.kg_id || kg.profile_id}"
                            data-kg-name="${_esc(kg.kg_name || kg.profile_name || '')}"
                            data-source-label="${_esc(kg.source_label || '')}"
                            title="Re-sync from source: ${_esc(kg.source_label || '')}">
                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
                        Update
                    </button>` : ''}
                    <button class="kg-intel-versions-btn card-btn card-btn--cyan"
                            data-kg-id="${kg.kg_id || kg.profile_id}"
                            data-profile-id="${kg.profile_id}"
                            data-kg-name="${_esc(kg.kg_name || kg.profile_name || '')}"
                            title="Version history — switch between saved versions">
                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                        Versions
                    </button>
                    <button class="kg-intel-rename-btn card-btn card-btn--info"
                            data-kg-id="${kg.kg_id || kg.profile_id}"
                            data-kg-name="${(kg.kg_name || kg.profile_name || '').replace(/"/g, '&quot;')}"
                            data-description="${(kg.description || '').replace(/"/g, '&quot;')}">
                        Edit
                    </button>
                    <button class="kg-intel-govern-btn card-btn card-btn--success"
                            data-kg-id="${kg.kg_id || kg.profile_id}"
                            data-kg-name="${_esc(kg.kg_name || kg.profile_name || kg.profile_id)}"
                            title="Govern — conformance & publish lifecycle">
                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                        Govern
                    </button>
                    ${_obdaOn ? `
                    <button class="kg-intel-products-btn card-btn"
                            style="color:#eab308;border-color:rgba(234,179,8,0.4);background:rgba(234,179,8,0.08);"
                            data-kg-id="${kg.kg_id || kg.profile_id}"
                            data-kg-name="${_esc(kg.kg_name || kg.profile_name || kg.profile_id)}"
                            title="Data products — signed, deterministic consumption contracts (OBDA-READ)">
                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4"/></svg>
                        Products
                    </button>` : ''}
                    ${_actOn ? `
                    <button class="kg-intel-actions-btn card-btn"
                            style="color:#dc2626;border-color:rgba(220,38,38,0.4);background:rgba(220,38,38,0.06);"
                            data-kg-id="${kg.kg_id || kg.profile_id}"
                            data-kg-name="${_esc(kg.kg_name || kg.profile_name || kg.profile_id)}"
                            title="Actions — signed write contracts on this graph (validate, sign, grant, try it)">
                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536M9 11l6.232-6.232a2.5 2.5 0 113.536 3.536L12.536 14.536 8 16l1.464-4.536z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 20h14"/></svg>
                        Actions
                    </button>` : ''}
                    <button class="kg-intel-delete-btn card-btn card-btn--danger" data-profile-id="${kg.profile_id}" data-kg-id="${kg.kg_id || kg.profile_id}" data-profile-name="${kg.profile_name || kg.profile_id}">
                        Delete
                    </button>
                `}
            </div>
        </div>
    `;
}

// ── Intelligence page — load & render ────────────────────────────────────────

export async function loadKnowledgeGraphsIntelligenceTab(target) {
    // `target` may be omitted (refresh of the current surface), a grid-container id
    // string, or a partial config object {gridId, filterId, tabBtnId}. When provided
    // it becomes the active surface for this and all subsequent internal refreshes.
    if (typeof target === 'string') {
        _kgIntelCfg = (target === _DEFAULT_KG_INTEL_CFG.gridId)
            ? { ..._DEFAULT_KG_INTEL_CFG }
            : { ..._DEFAULT_KG_INTEL_CFG, gridId: target, filterId: null, tabBtnId: null };
    } else if (target && typeof target === 'object') {
        _kgIntelCfg = { ..._DEFAULT_KG_INTEL_CFG, ...target };
    }

    const container = document.getElementById(_kgIntelCfg.gridId);
    if (!container) return;

    // Loading state
    container.innerHTML = `
        <div class="col-span-full flex items-center justify-center py-12">
            <div class="animate-spin rounded-full h-6 w-6 border-2 border-gray-500 border-t-[#F15F22]"></div>
            <span class="ml-3 text-sm text-gray-400">Loading knowledge graphs...</span>
        </div>
    `;

    try {
        // Fetch the KG list and the component-enabled map together (the latter
        // drives the "assignment inactive — component disabled" warnings).
        const [response] = await Promise.all([
            API.loadKnowledgeGraphList(),
            _refreshKgComponentEnabledMap(),
        ]);
        const graphs = response.knowledge_graphs || [];

        // Also keep cached for Resource Panel
        state.resourceData.knowledgeGraphs = graphs;

        // Update tab counter (always shows total, not filtered count)
        const tabBtn = _kgIntelCfg.tabBtnId ? document.getElementById(_kgIntelCfg.tabBtnId) : null;
        if (tabBtn) {
            tabBtn.textContent = `Knowledge Graphs (${graphs.length})`;
        }

        // Apply active-only filter if toggled
        const activeFilter = (_kgIntelCfg.filterId
            ? document.getElementById(_kgIntelCfg.filterId)?.checked
            : false) ?? false;
        const visibleGraphs = activeFilter
            ? graphs.filter(kg =>
                kg.is_active_for_owner ||
                (kg.assigned_profiles || []).some(a => a.is_active))
            : graphs;

        if (visibleGraphs.length === 0) {
            container.innerHTML = `
                <div class="col-span-full text-center py-12">
                    <svg class="w-16 h-16 mx-auto text-gray-600 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 21a9.004 9.004 0 008.716-6.747M12 21a9.004 9.004 0 01-8.716-6.747M12 21c2.485 0 4.5-4.03 4.5-9S14.485 3 12 3m0 18c-2.485 0-4.5-4.03-4.5-9S9.515 3 12 3m0 0a8.997 8.997 0 017.843 4.582M12 3a8.997 8.997 0 00-7.843 4.582m15.686 0A11.953 11.953 0 0112 10.5c-2.998 0-5.74-1.1-7.843-2.918m15.686 0A8.959 8.959 0 0121 12c0 .778-.099 1.533-.284 2.253m0 0A17.919 17.919 0 0112 16.5c-3.162 0-6.133-.815-8.716-2.247m0 0A9.015 9.015 0 013 12c0-1.605.42-3.113 1.157-4.418"/>
                    </svg>
                    <p class="text-gray-400 text-sm">No Knowledge Graphs yet.</p>
                    <p class="text-gray-500 text-xs mt-1">Knowledge graphs are built automatically when you use profiles with the KG component enabled.</p>
                </div>
            `;
            return;
        }

        await _probeObdaEnabled();   // decides whether the Products button renders
        await _probeActEnabled();    // …and whether the Actions button does (OBDA-ACT, PROP-008)
        container.innerHTML = visibleGraphs.map(kg => createIntelligenceKGCard(kg)).join('');
        attachIntelligenceKGHandlers(container);
        if (window.TrustBadge) window.TrustBadge.mountArtifactBadges(container);

        const kgFilterEl = _kgIntelCfg.filterId ? document.getElementById(_kgIntelCfg.filterId) : null;
        if (kgFilterEl && !kgFilterEl.dataset.wired) {
            kgFilterEl.dataset.wired = '1';
            kgFilterEl.addEventListener('change', () => loadKnowledgeGraphsIntelligenceTab());
        }
    } catch (err) {
        console.error('[KG Intelligence] Failed to load:', err);
        container.innerHTML = `
            <div class="col-span-full text-center text-red-400 text-sm py-12">
                Failed to load knowledge graphs: ${err.message}
            </div>
        `;
    }
}

// ── KG Edit Modal ─────────────────────────────────────────────────────────────

let _kgEditOverlay = null;

function _ensureKgEditModal() {
    if (_kgEditOverlay) return;

    _kgEditOverlay = document.createElement('div');
    _kgEditOverlay.id = 'kg-edit-modal-overlay';
    _kgEditOverlay.className = 'fixed inset-0 z-[9999] flex items-center justify-center bg-black/60 backdrop-blur-sm hidden opacity-0 transition-opacity duration-200';

    _kgEditOverlay.innerHTML = `
        <div id="kg-edit-modal-content"
             class="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-md p-6 transform scale-95 opacity-0 transition-all duration-200">
            <div class="flex items-center justify-between mb-5">
                <h3 class="text-lg font-bold text-white">Edit Knowledge Graph</h3>
                <button id="kg-edit-modal-close" class="text-gray-400 hover:text-white transition-colors p-1">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
                    </svg>
                </button>
            </div>
            <div class="space-y-4">
                <div>
                    <label class="block text-xs text-gray-400 mb-1">Name</label>
                    <input id="kg-edit-name" type="text"
                           class="w-full bg-gray-800 border border-gray-600 rounded-lg p-2 text-sm text-white focus:ring-2 focus:ring-purple-500 focus:border-purple-500 outline-none" />
                </div>
                <div>
                    <label class="block text-xs text-gray-400 mb-1">Description <span class="text-gray-600">(optional)</span></label>
                    <textarea id="kg-edit-description" rows="3" placeholder="Describe the purpose of this knowledge graph..."
                           class="w-full bg-gray-800 border border-gray-600 rounded-lg p-2 text-sm text-white focus:ring-2 focus:ring-purple-500 focus:border-purple-500 outline-none resize-none"></textarea>
                </div>
            </div>
            <div class="flex gap-2 mt-5">
                <button id="kg-edit-save" class="card-btn card-btn--primary flex-1">Save Changes</button>
                <button id="kg-edit-cancel" class="card-btn card-btn--neutral">Cancel</button>
            </div>
        </div>
    `;

    document.body.appendChild(_kgEditOverlay);

    const closeModal = () => {
        const content = _kgEditOverlay.querySelector('#kg-edit-modal-content');
        content.classList.add('scale-95', 'opacity-0');
        content.classList.remove('scale-100', 'opacity-100');
        _kgEditOverlay.classList.add('opacity-0');
        setTimeout(() => _kgEditOverlay.classList.add('hidden'), 200);
    };

    _kgEditOverlay.querySelector('#kg-edit-modal-close').addEventListener('click', closeModal);
    _kgEditOverlay.querySelector('#kg-edit-cancel').addEventListener('click', closeModal);
    _kgEditOverlay.addEventListener('click', (e) => { if (e.target === _kgEditOverlay) closeModal(); });

    _kgEditOverlay.querySelector('#kg-edit-save').addEventListener('click', async () => {
        const kgId = _kgEditOverlay.dataset.kgId;
        const newName = _kgEditOverlay.querySelector('#kg-edit-name').value.trim();
        const newDesc = _kgEditOverlay.querySelector('#kg-edit-description').value.trim();
        if (!newName) {
            showAppBanner('Name cannot be empty.', 'error');
            return;
        }
        const saveBtn = _kgEditOverlay.querySelector('#kg-edit-save');
        saveBtn.disabled = true;
        try {
            await API.renameKnowledgeGraph(kgId, newName, newDesc || null);
            showAppBanner('Knowledge graph updated.', 'success');
            closeModal();
            await loadKnowledgeGraphsIntelligenceTab();
        } catch (err) {
            showAppBanner(`Save failed: ${err.message}`, 'error');
        } finally {
            saveBtn.disabled = false;
        }
    });
}

function _openKgEditModal(kgId, currentName, currentDescription) {
    _ensureKgEditModal();
    _kgEditOverlay.dataset.kgId = kgId;
    _kgEditOverlay.querySelector('#kg-edit-name').value = currentName || '';
    _kgEditOverlay.querySelector('#kg-edit-description').value = currentDescription || '';
    _kgEditOverlay.classList.remove('hidden');
    requestAnimationFrame(() => {
        _kgEditOverlay.classList.remove('opacity-0');
        const content = _kgEditOverlay.querySelector('#kg-edit-modal-content');
        content.classList.remove('scale-95', 'opacity-0');
        content.classList.add('scale-100', 'opacity-100');
        _kgEditOverlay.querySelector('#kg-edit-name').focus();
        _kgEditOverlay.querySelector('#kg-edit-name').select();
    });
}

// ── Intelligence page — card action handlers ─────────────────────────────────

function attachIntelligenceKGHandlers(container) {
    // The click handler is delegated (reads dataset off the clicked badge at
    // click time), so it only needs to be attached ONCE per container. Setting
    // container.innerHTML on re-render swaps the children but not the container
    // itself, so re-running addEventListener here would stack a duplicate
    // listener every render — and because each activation success re-renders the
    // grid, the listener count would double on every click, producing hundreds
    // of identical activate calls from a single tag click. Guard against it.
    if (container.dataset.kgHandlersWired === '1') return;
    container.dataset.kgHandlersWired = '1';

    container.addEventListener('click', async (e) => {
        // Edit modal — Edit button click
        const renameBtn = e.target.closest('.kg-intel-rename-btn');
        if (renameBtn) {
            _openKgEditModal(
                renameBtn.dataset.kgId,
                renameBtn.dataset.kgName,
                renameBtn.dataset.description
            );
            return;
        }

        const inspectBtn = e.target.closest('.kg-intel-inspect-btn');
        if (inspectBtn) {
            openKnowledgeGraphInspection(inspectBtn.dataset.profileId, inspectBtn.dataset.profileName, inspectBtn.dataset.kgId);
            return;
        }

        const exportBtn = e.target.closest('.kg-intel-export-btn');
        if (exportBtn) {
            openKgExportMenu(exportBtn, exportBtn.dataset.profileId, exportBtn.dataset.kgId);
            return;
        }

        const governBtn = e.target.closest('.kg-intel-govern-btn');
        if (governBtn) {
            openKgGovernanceDialog(governBtn.dataset.kgId, governBtn.dataset.kgName);
            return;
        }

        const updateBtn = e.target.closest('.kg-intel-update-btn');
        if (updateBtn) {
            const { openKgUpdateDiff } = await import(`./rag/kgUpdateVersions.js?v=${Date.now()}`);
            openKgUpdateDiff(updateBtn.dataset.kgId, updateBtn.dataset.kgName,
                             updateBtn.dataset.sourceLabel, () => loadKnowledgeGraphsIntelligenceTab());
            return;
        }

        const versionsBtn = e.target.closest('.kg-intel-versions-btn');
        if (versionsBtn) {
            const { openKgVersions } = await import(`./rag/kgUpdateVersions.js?v=${Date.now()}`);
            openKgVersions(versionsBtn.dataset.kgId, versionsBtn.dataset.profileId,
                           versionsBtn.dataset.kgName, () => loadKnowledgeGraphsIntelligenceTab());
            return;
        }

        const actionsBtn = e.target.closest('.kg-intel-actions-btn');
        if (actionsBtn) {
            import('./actionDesigner.js?v=1.16')
                .then((m) => m.openActionDesigner(actionsBtn.dataset.kgId, actionsBtn.dataset.kgName))
                .catch((err) => console.warn('[actions] designer failed to open:', err));
            return;
        }

        const productsBtn = e.target.closest('.kg-intel-products-btn');
        if (productsBtn) {
            openKgProductsDialog(productsBtn.dataset.kgId, productsBtn.dataset.kgName);
            return;
        }

        const deleteBtn = e.target.closest('.kg-intel-delete-btn');
        if (deleteBtn) {
            handleIntelligenceDelete(deleteBtn.dataset.kgId || deleteBtn.dataset.profileId, deleteBtn.dataset.profileName);
            return;
        }

        const intelSyncBtn = e.target.closest('.kg-intel-sync-btn');
        if (intelSyncBtn) {
            await handleKgPanelSync(intelSyncBtn);
            // Reload Intelligence-tab card after sync — handleKgPanelSync
            // already refreshes the resource panel via refreshKnowledgeGraphsPanel
            await loadKnowledgeGraphsIntelligenceTab();
            return;
        }

        const intelUnsubBtn = e.target.closest('.kg-intel-unsubscribe-btn');
        if (intelUnsubBtn) {
            handleKgPanelUnsubscribe(intelUnsubBtn, () => loadKnowledgeGraphsIntelligenceTab());
            return;
        }

        const intelPackSyncBtn = e.target.closest('.kg-intel-pack-sync-btn');
        if (intelPackSyncBtn) {
            await handleKgPanelPackSync(intelPackSyncBtn);   // refreshes both KG surfaces itself
            return;
        }

        const assignBtn = e.target.closest('.kg-intel-assign-btn');
        if (assignBtn) {
            const ownerProfileId = assignBtn.dataset.profileId;
            const ownerName = assignBtn.dataset.profileName;
            const kgId = assignBtn.dataset.kgId;
            _openAssignmentPanel(ownerProfileId, ownerName, kgId, container);
            return;
        }

        // Toggle activation from assigned profile badges
        const activateBadge = e.target.closest('.kg-intel-activate-badge');
        if (activateBadge) {
            const kgId = activateBadge.dataset.kgId;
            const assignedId = activateBadge.dataset.assignedId;
            const isCurrentlyActive = activateBadge.dataset.isActive === '1';

            try {
                if (isCurrentlyActive) {
                    await API.activateKgForProfile(null, assignedId);
                    showAppBanner('Knowledge graph deactivated for profile', 'success');
                } else {
                    await API.activateKgForProfile(kgId, assignedId);
                    showAppBanner('Knowledge graph activated for profile', 'success');
                }
                await loadKnowledgeGraphsIntelligenceTab();
                if (state.resourceData.knowledgeGraphs !== null) {
                    await loadKnowledgeGraphsPanel();
                }
            } catch (err) {
                console.error('[KG] Toggle activation failed:', err);
                showAppBanner(`Activation failed: ${err.message}`, 'error');
            }
            return;
        }

        // Toggle owner's own activation
        const ownerToggle = e.target.closest('.kg-intel-owner-toggle');
        if (ownerToggle) {
            const kgId = ownerToggle.dataset.kgId;
            const kgOwnerId = ownerToggle.dataset.kgOwnerId;  // profile_id for deactivation
            const isCurrentlyActive = ownerToggle.dataset.isActive === '1';

            try {
                if (isCurrentlyActive) {
                    await API.activateKgForProfile(null, kgOwnerId);
                    showAppBanner('Knowledge graph deactivated for owner', 'success');
                } else {
                    await API.activateKgForProfile(kgId, kgOwnerId);
                    showAppBanner('Knowledge graph activated for owner', 'success');
                }
                await loadKnowledgeGraphsIntelligenceTab();
                if (state.resourceData.knowledgeGraphs !== null) {
                    await loadKnowledgeGraphsPanel();
                }
            } catch (err) {
                console.error('[KG] Toggle owner activation failed:', err);
                showAppBanner(`Activation failed: ${err.message}`, 'error');
            }
            return;
        }
    });
}

function handleIntelligenceDelete(kgIdOrProfileId, profileName) {
    showConfirmation(
        'Delete Knowledge Graph',
        `<p>Are you sure you want to delete the knowledge graph for <strong>${profileName}</strong>?</p>
         <p class="mt-2 text-sm text-gray-400">This will permanently remove all entities and relationships. This action cannot be undone.</p>`,
        async () => {
            try {
                await API.deleteKnowledgeGraph(null, kgIdOrProfileId);
                showAppBanner('Knowledge graph deleted', 'success');
                await loadKnowledgeGraphsIntelligenceTab();
                // Also refresh resource panel if it was loaded
                if (state.resourceData.knowledgeGraphs !== null) {
                    await loadKnowledgeGraphsPanel();
                }
            } catch (err) {
                console.error('Delete failed:', err);
                showAppBanner(`Delete failed: ${err.message}`, 'error');
            }
        }
    );
}

// ── Assignment panel ─────────────────────────────────────────────────────────

let _assignOverlay = null;

function _openAssignmentPanel(ownerProfileId, ownerName, kgId, parentContainer) {
    // Close any existing overlay
    if (_assignOverlay) {
        _assignOverlay.remove();
        _assignOverlay = null;
    }

    const allProfiles = window.configState?.profiles || [];
    // All profiles are candidates except the owner itself
    const candidates = allProfiles.filter(p => p.id !== ownerProfileId);

    if (candidates.length === 0) {
        showAppBanner('No other profiles available for assignment.', 'info');
        return;
    }

    // Find currently assigned profiles from the cached data — match by kg_id first
    const graphs = state.resourceData?.knowledgeGraphs || [];
    const thisKg = graphs.find(g => (g.kg_id || g.profile_id) === kgId)
                || graphs.find(g => g.profile_id === ownerProfileId);
    const currentlyAssigned = new Set((thisKg?.assigned_profiles || []).map(a => a.id));

    _assignOverlay = document.createElement('div');
    _assignOverlay.className = 'fixed inset-0 z-[9999] flex items-center justify-center bg-black/60 backdrop-blur-sm';
    _assignOverlay.style.opacity = '0';
    _assignOverlay.style.transition = 'opacity 200ms';

    const IFOC_ORDER = { llm_only: 0, rag_focused: 1, tool_enabled: 2, genie: 3 };
    candidates.sort((a, b) => {
        const ao = IFOC_ORDER[a.profile_type] ?? 99;
        const bo = IFOC_ORDER[b.profile_type] ?? 99;
        if (ao !== bo) return ao - bo;
        return (a.tag || a.name || '').localeCompare(b.tag || b.name || '');
    });

    const checkboxes = candidates.map(p => {
        const ifoc = IFOC_CONFIG[p.profile_type] || { label: p.profile_type || '?', color: '#6b7280' };
        const checked = currentlyAssigned.has(p.id) ? 'checked' : '';
        return `
            <label class="flex items-center gap-3 p-2 rounded-lg hover:bg-white/5 cursor-pointer transition-colors">
                <input type="checkbox" class="kg-assign-check w-4 h-4 rounded border-gray-600 bg-gray-800 text-purple-500 focus:ring-purple-500 focus:ring-offset-0"
                       data-profile-id="${p.id}" ${checked} />
                <span class="text-xs font-mono" style="color: ${ifoc.color};">@${p.tag || p.name}</span>
                <span class="text-xs text-gray-500">${p.name || p.id}</span>
                <span class="text-xs px-2 py-0.5 rounded-full ml-auto" style="color: ${ifoc.color}; background: ${ifoc.color}15; border: 1px solid ${ifoc.color}30;">${ifoc.label}</span>
            </label>
        `;
    }).join('');

    _assignOverlay.innerHTML = `
        <div class="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-md max-h-[70vh] overflow-hidden flex flex-col transform scale-95 transition-transform duration-200"
             id="kg-assign-modal-content">
            <div class="p-5 border-b border-white/10">
                <div class="flex items-center justify-between">
                    <div>
                        <h3 class="text-base font-bold text-white">Assign Profiles</h3>
                        <p class="text-xs text-gray-400 mt-1">Select which profiles can use the KG from <span class="text-purple-400 font-mono">@${thisKg?.profile_tag || ownerName}</span></p>
                    </div>
                    <button id="kg-assign-close" class="text-gray-400 hover:text-white transition-colors p-1">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
                        </svg>
                    </button>
                </div>
            </div>
            <div class="flex-1 overflow-y-auto p-4 space-y-1">
                ${checkboxes}
            </div>
            <div class="p-4 border-t border-white/10 flex items-center justify-end gap-3">
                <button id="kg-assign-cancel" class="card-btn card-btn--neutral px-4">Cancel</button>
                <button id="kg-assign-save" class="card-btn card-btn--primary px-4">Save Assignments</button>
            </div>
        </div>
    `;

    document.body.appendChild(_assignOverlay);

    // Animate in
    requestAnimationFrame(() => {
        _assignOverlay.style.opacity = '1';
        const content = _assignOverlay.querySelector('#kg-assign-modal-content');
        if (content) {
            content.style.transform = 'scale(1)';
        }
    });

    // Close handlers
    const closePanel = () => {
        if (!_assignOverlay) return;
        _assignOverlay.style.opacity = '0';
        setTimeout(() => {
            if (_assignOverlay) {
                _assignOverlay.remove();
                _assignOverlay = null;
            }
        }, 200);
    };

    _assignOverlay.querySelector('#kg-assign-close').addEventListener('click', closePanel);
    _assignOverlay.querySelector('#kg-assign-cancel').addEventListener('click', closePanel);
    _assignOverlay.addEventListener('click', (e) => {
        if (e.target === _assignOverlay) closePanel();
    });

    // Save handler
    _assignOverlay.querySelector('#kg-assign-save').addEventListener('click', async () => {
        const checked = _assignOverlay.querySelectorAll('.kg-assign-check:checked');
        const assignedIds = Array.from(checked).map(cb => cb.dataset.profileId);

        const saveBtn = _assignOverlay.querySelector('#kg-assign-save');
        saveBtn.disabled = true;
        saveBtn.textContent = 'Saving...';

        try {
            await API.updateKgAssignments(ownerProfileId, assignedIds, kgId);
            showAppBanner(`Assigned ${assignedIds.length} profile(s) to knowledge graph`, 'success');
            closePanel();

            // Refresh both views
            await loadKnowledgeGraphsIntelligenceTab();
            if (state.resourceData.knowledgeGraphs !== null) {
                await loadKnowledgeGraphsPanel();
            }
        } catch (err) {
            console.error('[KG Assignment] Save failed:', err);
            showAppBanner(`Failed to save assignments: ${err.message}`, 'error');
            saveBtn.disabled = false;
            saveBtn.textContent = 'Save Assignments';
        }
    });
}

// ── Knowledge Graph Inspection (full D3 graph view) ──────────────────────────

let _kgInspectFullscreen = false;
// Records which view the inspector was opened from, so the back button returns
// there instead of always landing on the Intelligence Performance page. Inspect
// can be launched from the conversation Resource Panel (conversation-view), the
// Intelligence tab (rag-maintenance-view), or the Knowledge Graph component tab
// (components-view).
let _kgInspectOrigin = 'rag-maintenance-view';

export async function openKnowledgeGraphInspection(profileId, profileName, kgId = null) {
    const { handleViewSwitch } = await import('../ui.js?v=1.287');
    const { renderKnowledgeGraph } = await import('/api/v1/components/knowledge_graph/renderer');

    // Capture the origin view BEFORE switching so the back button can return to it.
    const activeViewId = document.querySelector('.app-view.active')?.id;
    _kgInspectOrigin = (activeViewId && activeViewId !== 'kg-inspect-view')
        ? activeViewId
        : 'rag-maintenance-view';

    // Switch to inspect view
    handleViewSwitch('kg-inspect-view');

    // Update title
    const titleEl = document.getElementById('kg-inspect-title');
    if (titleEl) titleEl.textContent = `Inspect: ${profileName}`;

    // Show loading spinner
    const contentArea = document.getElementById('kg-inspect-content');
    contentArea.innerHTML = `
        <div class="flex items-center justify-center flex-1">
            <div class="animate-spin rounded-full h-8 w-8 border-2 border-gray-500 border-t-purple-500"></div>
            <span class="ml-3 text-sm text-gray-400">Loading knowledge graph...</span>
        </div>
    `;

    // Wire handlers immediately (back button should always work)
    _wireKGInspectHandlers();

    try {
        const response = await API.fetchKnowledgeGraphSpec(profileId, 500, kgId);
        const spec = response.spec;
        // Title with the actual KG name + honest "of M" when the view is capped.
        if (titleEl && spec) {
            const base = spec.title ? `Inspect: ${spec.title}` : `Inspect: ${profileName}`;
            titleEl.textContent = spec.truncated
                ? `${base} — showing ${(spec.shown_nodes || 0).toLocaleString()} of ${(spec.total_nodes || 0).toLocaleString()} nodes`
                : base;
        }

        if (!spec || !spec.nodes || spec.nodes.length === 0) {
            contentArea.innerHTML = `
                <div class="flex flex-col items-center justify-center flex-1 text-center">
                    <svg class="w-16 h-16 text-gray-600 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 21a9.004 9.004 0 008.716-6.747M12 21a9.004 9.004 0 01-8.716-6.747M12 21c2.485 0 4.5-4.03 4.5-9S14.485 3 12 3m0 18c-2.485 0-4.5-4.03-4.5-9S9.515 3 12 3m0 0a8.997 8.997 0 017.843 4.582M12 3a8.997 8.997 0 00-7.843 4.582m15.686 0A11.953 11.953 0 0112 10.5c-2.998 0-5.74-1.1-7.843-2.918m15.686 0A8.959 8.959 0 0121 12c0 .778-.099 1.533-.284 2.253m0 0A17.919 17.919 0 0112 16.5c-3.162 0-6.133-.815-8.716-2.247m0 0A9.015 9.015 0 013 12c0-1.605.42-3.113 1.157-4.418"/>
                    </svg>
                    <p class="text-gray-400 text-sm">This knowledge graph is empty.</p>
                    <p class="text-gray-500 text-xs mt-1">Entities and relationships will appear here once the graph is populated.</p>
                </div>
            `;
            return;
        }

        // Clear and create render target with kg-split- prefix (triggers renderKGFull in renderer)
        contentArea.innerHTML = '';
        const renderTarget = document.createElement('div');
        renderTarget.id = `kg-split-inspect-${Date.now()}`;
        renderTarget.style.cssText = 'flex:1;display:flex;flex-direction:column;min-height:0;';
        contentArea.appendChild(renderTarget);

        // Carry the KG identity into the spec so the renderer's detail-lens controls
        // (type pills + node-budget slider) can re-query the stratified subgraph.
        spec.kg_id = kgId || spec.kg_id;
        spec.profile_id = profileId || spec.profile_id;
        // Render full interactive D3 graph (toolbar + force graph + legend)
        renderKnowledgeGraph(renderTarget.id, spec);
    } catch (err) {
        console.error('[KG Inspect] Failed to load graph:', err);
        contentArea.innerHTML = `
            <div class="flex flex-col items-center justify-center flex-1 text-center">
                <p class="text-red-400 text-sm">Failed to load knowledge graph: ${err.message}</p>
                <button onclick="document.getElementById('kg-inspect-back')?.click()"
                        class="card-btn card-btn--neutral mt-4">Go Back</button>
            </div>
        `;
    }
}

function _wireKGInspectHandlers() {
    const backBtn = document.getElementById('kg-inspect-back');
    if (backBtn) {
        backBtn.onclick = async () => {
            // Exit fullscreen if active
            if (_kgInspectFullscreen) {
                _kgInspectFullscreen = false;
                const mainArea = document.getElementById('main-content-area');
                if (mainArea) mainArea.classList.remove('kg-fullscreen');
                document.documentElement.style.removeProperty('--kg-fullscreen-top');
            }

            // When opened from the Knowledge Graph component tab, restore that detail
            // panel with its management tab active rather than a bare view switch.
            if (_kgInspectOrigin === 'components-view') {
                try {
                    const mod = await import('./componentHandler.js?v=1.189');
                    if (typeof mod.openComponentDetail === 'function') {
                        await mod.openComponentDetail('knowledge_graph', { activateTabId: 'comp-tab-kgs' });
                        return;
                    }
                } catch (err) {
                    console.error('[KG Inspect] Failed to restore component panel:', err);
                }
            }

            const { handleViewSwitch } = await import('../ui.js?v=1.287');
            handleViewSwitch(_kgInspectOrigin);
        };
    }

    const fsBtn = document.getElementById('kg-inspect-fullscreen');
    if (fsBtn) {
        fsBtn.onclick = () => {
            _kgInspectFullscreen = !_kgInspectFullscreen;
            const mainArea = document.getElementById('main-content-area');
            if (_kgInspectFullscreen) {
                mainArea.classList.add('kg-fullscreen');
                // Offset for any top nav bar
                const nav = document.querySelector('nav, .top-nav');
                if (nav) {
                    document.documentElement.style.setProperty('--kg-fullscreen-top', nav.offsetHeight + 'px');
                }
            } else {
                mainArea.classList.remove('kg-fullscreen');
                document.documentElement.style.removeProperty('--kg-fullscreen-top');
            }
            // Update icon to reflect state
            fsBtn.title = _kgInspectFullscreen ? 'Exit fullscreen' : 'Toggle fullscreen';
        };
    }
}

// ── Component detail-panel management tab ─────────────────────────────────────

/**
 * Mount the full Knowledge Graph management view inside the Knowledge Graph
 * component's detail panel (Components → Knowledge Graph → "Knowledge Graphs" tab).
 *
 * Mirrors the management surface of the Intelligence Performance page (the same
 * cards, assignment, sync, edit, delete, export and import actions) by reusing
 * loadKnowledgeGraphsIntelligenceTab() with a component-scoped render target so
 * there is no duplicate-id collision with the Intelligence tab markup.
 *
 * Entry point: window.knowledgeGraphPanelHandler.initKgManagementTab(rootId) — but
 * called via dynamic import from componentHandler.js.
 */
export function initKgManagementTab(rootId) {
    const root = document.getElementById(rootId);
    if (!root) return;

    root.innerHTML = `
        <div class="flex items-center justify-between mb-4 flex-wrap gap-3">
            <div>
                <h3 class="text-lg font-semibold" style="color:var(--text-primary)">Knowledge Graphs</h3>
                <p class="text-sm" style="color:var(--text-muted)">Entity-relationship graphs built across your profiles</p>
            </div>
            <div class="flex items-center gap-3">
                <label class="ind-toggle ind-toggle--sm gap-2 cursor-pointer select-none text-sm"
                       style="color:var(--text-muted)">
                    <input type="checkbox" id="kg-comp-active-filter">
                    <span class="ind-track flex-shrink-0"></span>
                    <span>Active only</span>
                </label>
                <button id="kg-comp-import-btn" class="card-btn card-btn--cyan flex items-center gap-2">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"></path>
                    </svg>
                    Import
                </button>
            </div>
        </div>
        <div id="kg-comp-grid-container" class="grid gap-6 sm:grid-cols-2 lg:grid-cols-3"></div>
    `;

    // Reuse the existing management renderer + handlers, scoped to this surface.
    loadKnowledgeGraphsIntelligenceTab({
        gridId: 'kg-comp-grid-container',
        filterId: 'kg-comp-active-filter',
        tabBtnId: null,
    });
    initializeImportHandler('kg-comp-import-btn');
}

// ── Import handler ───────────────────────────────────────────────────────────

// RDF import (open standards) — Turtle/JSON-LD/RDF-XML/N-Triples → POST /import-rdf.
// Lossless for Uderia's own export; best-effort for a foreign graph (warnings surfaced).
// The button an import was started from shows it is busy, then returns to exactly what it
// was (a labelled Import button, or an icon-only one such as the Workspace's — which sets
// data-icon-only so its icon is never replaced by text).
function _importBusy(btn) {
    if (!btn) return;
    btn.dataset.kgOrigHtml = btn.innerHTML;
    btn.disabled = true;
    if (!btn.dataset.iconOnly) btn.textContent = 'Importing...';
}
function _importIdle(btn) {
    if (!btn) return;
    btn.disabled = false;
    if (btn.dataset.kgOrigHtml !== undefined) { btn.innerHTML = btn.dataset.kgOrigHtml; delete btn.dataset.kgOrigHtml; }
}

async function handleRdfImport(file, rdfText, serialization, importBtn, onDone) {
    let allUserKgs = [];
    try {
        const kgListData = await API.loadKnowledgeGraphList();
        allUserKgs = kgListData.knowledge_graphs || [];
    } catch (_) {}

    const selectStyle = `background: rgba(31, 33, 38, 0.8); border: 1px solid rgba(147, 51, 234, 0.3); background-image: url('data:image/svg+xml;charset=UTF-8,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%2212%22 height=%2212%22 viewBox=%220 0 12 12%22%3E%3Cpath fill=%22%239ca3af%22 d=%22M2 4l4 4 4-4%22/%3E%3C/svg%3E'); background-repeat: no-repeat; background-position: right 0.75rem center;`;

    const buildImportModeHtml = (profileId) => {
        const ownedKgs = allUserKgs.filter(kg => kg.profile_id === profileId);
        if (!ownedKgs.length) return '';
        const kgOptions = ownedKgs
            .map(kg => `<option value="${kg.kg_id || kg.profile_id}">${kg.kg_name || kg.name || kg.kg_id}</option>`)
            .join('');
        return `
            <div id="kg-import-mode-section">
                <label class="block text-xs text-gray-400 mb-1">Import mode</label>
                <select id="kg-import-mode-select" class="w-full p-2 text-sm text-white rounded-lg outline-none cursor-pointer appearance-none" style="${selectStyle}">
                    <option value="">Create new Knowledge Graph</option>
                    ${kgOptions.replace(/<option /g, '<option data-merge="1" ')}
                </select>
                <p id="kg-import-mode-hint" class="text-xs text-gray-500 mt-1">A new Knowledge Graph will be created for this profile.</p>
            </div>`;
    };

    const profiles = window.configState?.profiles || [];
    const ifocLabels = { llm_only: 'Ideate', rag_focused: 'Focus', tool_enabled: 'Optimize', genie: 'Coordinate' };
    const optionsHtml = profiles.map(p =>
        `<option value="${p.id}">@${p.tag || p.name} (${ifocLabels[p.profile_type] || p.profile_type})</option>`).join('');
    const initialProfileId = profiles[0]?.id || '';
    const serLabel = { turtle: 'Turtle', jsonld: 'JSON-LD', xml: 'RDF/XML', nt: 'N-Triples' }[serialization] || serialization;
    const sizeKb = (rdfText.length / 1024).toFixed(1);

    showConfirmation(
        'Import RDF Knowledge Graph',
        `<div class="space-y-3">
            <div>
                <p class="text-sm text-gray-300">File: <span class="text-white font-medium">${file.name}</span>
                   <span class="text-gray-400">— ${serLabel} · ${sizeKb} KB</span></p>
                <p class="text-xs text-gray-500 mt-1">Lossless for a Uderia export. A foreign graph is mapped best-effort; anything that can't be placed is reported.</p>
            </div>
            <div>
                <label class="block text-xs text-gray-400 mb-1">Ownership Profile</label>
                <select id="kg-import-profile-select" class="w-full p-2 text-sm text-white rounded-lg outline-none cursor-pointer appearance-none" style="${selectStyle}">
                    ${optionsHtml}
                </select>
            </div>
            <div id="kg-import-mode-container">${buildImportModeHtml(initialProfileId)}</div>
        </div>`,
        async () => {
            const selectedProfileId = document.getElementById('kg-import-profile-select')?.value;
            if (!selectedProfileId) { showAppBanner('Please select an ownership profile', 'error'); return; }
            const targetKgId = document.getElementById('kg-import-mode-select')?.value || null;

            _importBusy(importBtn);
            try {
                const meta = { kg_name: file.name.replace(/\.[^.]+$/, '') };
                const result = await API.importKnowledgeGraphRdf(selectedProfileId, rdfText, serialization, meta, targetKgId);
                const warns = result.warnings || [];
                showAppBanner(
                    `Imported ${addedLabel(result.entities_added, result.relationships_added)}`
                    + (warns.length ? ` · ${warns.length} warning(s)` : ''),
                    warns.length ? 'warning' : 'success'
                );
                if (warns.length) console.warn('[KG RDF Import] warnings:', warns);
                await loadKnowledgeGraphsIntelligenceTab();
                if (state.resourceData.knowledgeGraphs !== null) await loadKnowledgeGraphsPanel();
                if (typeof onDone === 'function') { try { onDone(); } catch (_) { /* a caller refresh is a bonus */ } }
            } catch (importErr) {
                console.error('[KG RDF Import] Failed:', importErr);
                showAppBanner(`Import failed: ${importErr.message}`, 'error');
            } finally {
                _importIdle(importBtn);
            }
        }
    );

    requestAnimationFrame(() => {
        const profileSel = document.getElementById('kg-import-profile-select');
        const modeContainer = document.getElementById('kg-import-mode-container');
        const wireHint = () => {
            const modeSel = document.getElementById('kg-import-mode-select');
            const hint = document.getElementById('kg-import-mode-hint');
            if (!modeSel || !hint) return;
            modeSel.addEventListener('change', () => {
                hint.textContent = modeSel.value
                    ? 'Entities and relationships will be merged into the selected Knowledge Graph.'
                    : 'A new Knowledge Graph will be created for this profile.';
            });
        };
        if (profileSel && modeContainer) {
            profileSel.addEventListener('change', () => {
                modeContainer.innerHTML = buildImportModeHtml(profileSel.value);
                wireHint();
            });
            wireHint();
        }
    });
}

export function initializeImportHandler(btnId = 'import-knowledge-graph-btn') {
    const importBtn = document.getElementById(btnId);
    if (!importBtn || importBtn.dataset.kgImportWired) return;
    importBtn.dataset.kgImportWired = '1';

    importBtn.addEventListener('click', () => startKgImport(importBtn));
}

/** Import a knowledge graph from a file (native JSON manifest or an RDF serialization):
 *  file picker → ownership/merge dialog → import. ONE flow for every Import button —
 *  the Intelligence tab's and the Workspace section's. `onDone` runs after a success. */
export function startKgImport(importBtn, onDone) {
    {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = '.json,.ttl,.jsonld,.rdf,.nt,.xml';

        // iPadOS file picker workaround
        if (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1) {
            input.removeAttribute('accept');
        }

        input.addEventListener('change', async (e) => {
            const file = e.target.files?.[0];
            if (!file) return;

            try {
                const text = await file.text();
                // Route RDF documents (open-standards import) to the RDF path;
                // everything else is the native JSON manifest.
                const ext = (file.name.split('.').pop() || '').toLowerCase();
                const rdfSer = { ttl: 'turtle', jsonld: 'jsonld', rdf: 'xml', xml: 'xml', nt: 'nt' }[ext];
                if (rdfSer) { await handleRdfImport(file, text, rdfSer, importBtn, onDone); return; }
                const data = JSON.parse(text);

                const entities = data.entities || [];
                const relationships = data.relationships || [];
                const meta = {
                    kg_name: data.kg_name || '',
                    kg_description: data.kg_description || '',
                    kg_database_name: data.kg_database_name || '',
                };

                if (!entities.length && !relationships.length) {
                    showAppBanner('Import file contains no entities or relationships', 'error');
                    return;
                }

                // Fetch existing KGs so we can offer merge option
                let allUserKgs = [];
                try {
                    const kgListData = await API.loadKnowledgeGraphList();
                    allUserKgs = kgListData.knowledge_graphs || [];
                } catch (_) {}

                const selectStyle = `background: rgba(31, 33, 38, 0.8); border: 1px solid rgba(147, 51, 234, 0.3); background-image: url('data:image/svg+xml;charset=UTF-8,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%2212%22 height=%2212%22 viewBox=%220 0 12 12%22%3E%3Cpath fill=%22%239ca3af%22 d=%22M2 4l4 4 4-4%22/%3E%3C/svg%3E'); background-repeat: no-repeat; background-position: right 0.75rem center;`;

                // Helper: build import-mode HTML for a given profile_id
                const buildImportModeHtml = (profileId) => {
                    const ownedKgs = allUserKgs.filter(kg => kg.profile_id === profileId);
                    if (!ownedKgs.length) return '';
                    const kgOptions = ownedKgs
                        .map(kg => `<option value="${kg.kg_id || kg.profile_id}">${kg.kg_name || kg.name || kg.kg_id}</option>`)
                        .join('');
                    return `
                        <div id="kg-import-mode-section">
                            <label class="block text-xs text-gray-400 mb-1">Import mode</label>
                            <select id="kg-import-mode-select" class="w-full p-2 text-sm text-white rounded-lg outline-none cursor-pointer appearance-none" style="${selectStyle}">
                                <option value="">Create new Knowledge Graph</option>
                                ${kgOptions.replace(/<option /g, '<option data-merge="1" ')}
                            </select>
                            <p id="kg-import-mode-hint" class="text-xs text-gray-500 mt-1">A new Knowledge Graph will be created for this profile.</p>
                        </div>`;
                };

                // Build profile dropdown options
                const profiles = window.configState?.profiles || [];
                const fileProfileId = data.profile_id || '';
                const ifocLabels = {
                    llm_only: 'Ideate', rag_focused: 'Focus',
                    tool_enabled: 'Optimize', genie: 'Coordinate'
                };

                const optionsHtml = profiles.map(p => {
                    const label = `@${p.tag || p.name} (${ifocLabels[p.profile_type] || p.profile_type})`;
                    const selected = p.id === fileProfileId ? ' selected' : '';
                    return `<option value="${p.id}"${selected}>${label}</option>`;
                }).join('');

                const initialProfileId = profiles.find(p => p.id === fileProfileId)?.id || profiles[0]?.id || '';
                const summary = `<span class="text-gray-400">${nodesLabel(entities.length)}, ${relationshipsLabel(relationships.length)}</span>`;
                const kgNameLine = meta.kg_name
                    ? `<p class="text-sm text-gray-400 mt-1">Knowledge Graph: <span class="text-white font-medium">${meta.kg_name}</span></p>`
                    : '';

                showConfirmation(
                    'Import Knowledge Graph',
                    `<div class="space-y-3">
                        <div>
                            <p class="text-sm text-gray-300">File: <span class="text-white font-medium">${file.name}</span> — ${summary}</p>
                            ${kgNameLine}
                        </div>
                        <div>
                            <label class="block text-xs text-gray-400 mb-1">Ownership Profile</label>
                            <select id="kg-import-profile-select"
                                    class="w-full p-2 text-sm text-white rounded-lg outline-none cursor-pointer appearance-none"
                                    style="${selectStyle}">
                                ${optionsHtml}
                            </select>
                        </div>
                        <div id="kg-import-mode-container">${buildImportModeHtml(initialProfileId)}</div>
                    </div>`,
                    async () => {
                        const selectedProfileId = document.getElementById('kg-import-profile-select')?.value;
                        if (!selectedProfileId) {
                            showAppBanner('Please select an ownership profile', 'error');
                            return;
                        }
                        const modeSelect = document.getElementById('kg-import-mode-select');
                        const targetKgId = modeSelect?.value || null;

                        _importBusy(importBtn);

                        try {
                            const result = await API.importKnowledgeGraph(selectedProfileId, entities, relationships, meta, targetKgId);
                            showAppBanner(
                                `Imported ${addedLabel(result.entities_added, result.relationships_added)}`,
                                'success'
                            );
                            await loadKnowledgeGraphsIntelligenceTab();
                            if (state.resourceData.knowledgeGraphs !== null) {
                                await loadKnowledgeGraphsPanel();
                            }
                            if (typeof onDone === 'function') { try { onDone(); } catch (_) { /* a caller refresh is a bonus */ } }
                        } catch (importErr) {
                            console.error('[KG Import] Failed:', importErr);
                            showAppBanner(`Import failed: ${importErr.message}`, 'error');
                        } finally {
                            _importIdle(importBtn);
                        }
                    }
                );

                // Wire profile-change → update import mode section
                requestAnimationFrame(() => {
                    const profileSel = document.getElementById('kg-import-profile-select');
                    const modeContainer = document.getElementById('kg-import-mode-container');
                    if (profileSel && modeContainer) {
                        profileSel.addEventListener('change', () => {
                            modeContainer.innerHTML = buildImportModeHtml(profileSel.value);
                            // Wire hint update for the newly rendered mode select
                            wireImportModeHint();
                        });
                        wireImportModeHint();
                    }

                    function wireImportModeHint() {
                        const modeSel = document.getElementById('kg-import-mode-select');
                        const hint = document.getElementById('kg-import-mode-hint');
                        if (!modeSel || !hint) return;
                        modeSel.addEventListener('change', () => {
                            hint.textContent = modeSel.value
                                ? `Entities and relationships will be merged into the selected Knowledge Graph.`
                                : `A new Knowledge Graph will be created for this profile.`;
                        });
                    }
                });
            } catch (err) {
                console.error('[KG Import] Parse failed:', err);
                showAppBanner(`Import failed: ${err.message}`, 'error');
            }
        });

        input.click();
    }
}


// ═══════════════════════════════════════════════════════════════════════════
// OBDA-READ Data-Product Designer v0 (Phase A4b — TIER_5.5_OBDA_SCOPE.md §10.4)
//
// The producer surface for signed, deterministic consumption contracts:
// catalog → author (manifest, with the REJECT codes rendered inline) →
// validate (the V1–V7 progressive checklist, U7) → the grain-safety grid
// (U3 — solid safe, dashed rejected with the offending hop on hover) →
// the sign-off ceremony showing exactly what is attested (U8).
//
// Renders ONLY when the OBDA-READ master switch is on (`_probeObdaEnabled`): the
// whole feature family switches off on demand, UI included — no dangling
// button for a disabled capability. Theme-aware (CSS vars), monochrome SVG,
// honest refusals (a 4xx's machine-readable code + message shown verbatim).
// ═══════════════════════════════════════════════════════════════════════════

let _obdaOn = null;   // null = not probed yet; boolean once probed (per page load)

let _actOn = null;
let _planesProbe = null;   // the one in-flight /obda/planes read both probes share

// Which OBDA planes this user can reach (component chain + KG tier gate), asked of the
// endpoint that answers exactly that — never inferred from a face's 400-vs-403.
function _probePlanes() {
    if (!_planesProbe) {
        _planesProbe = fetch('/api/v1/obda/planes', {
            headers: { 'Authorization': `Bearer ${localStorage.getItem('tda_auth_token')}` } })
            .then(r => (r.ok ? r.json() : null))
            .then(d => (d && d.planes) || {})
            .catch(() => ({}));
    }
    return _planesProbe;
}

async function _probeActEnabled() {
    if (_actOn !== null) return _actOn;
    _actOn = (await _probePlanes()).act === true;
    return _actOn;
}

async function _probeObdaEnabled() {
    if (_obdaOn !== null) return _obdaOn;
    _obdaOn = (await _probePlanes()).read === true;
    return _obdaOn;
}

const _DP_TRUST_STYLE = {
    trusted:   ['rgba(234,179,8,0.15)',  '#eab308', 'signed 🟢-equivalent: verified deterministic'],
    changed:   ['rgba(245,158,11,0.15)', '#f59e0b', 'signed but CHANGED since — re-validate + re-sign'],
    unsigned:  ['rgba(148,163,184,0.15)','#94a3b8', 'not signed yet'],
    unavailable: ['rgba(148,163,184,0.1)','#64748b', 'trust suite not installed here'],
};

const _DP_TEMPLATE = (kgName) => JSON.stringify({
    product: 'my_product', version: 1, backend: 'teradata',
    description: `Data product on ${kgName || 'this ontology'}`,
    entities: [{ name: '<logical entity name>', deployed: 'db.table',
                 key: ['id'], attributes: [{ name: 'col', type: 'string' }] }],
    joins: [], measures: [{ name: 'row_count', anchor: '<logical entity name>', expr: 'COUNT(*)' }],
    filters: {}, row_policy: null,
}, null, 2);

/**
 * The validate outcome, in words. PURE so it can be gate-executed.
 *
 * The subtle case this exists for: validation NEVER signs — but when edited
 * content returns to the signed bytes, the EXISTING attestation matches again
 * and the row goes 🟢 with no act by the operator. Reported from a real
 * walkthrough as "the revalidation automatically signed it", which is the worst
 * thing a trust surface can imply. So this names the signature that is applying
 * AND the act that did not happen.
 */
function _dpValidateNotice(report, trust, productName, liveNote) {
    const rep = report || {}, tr = trust || {};
    if (!rep.signable) {
        return { kind: 'err',
                 text: `Gate blockers: ${(rep.blockers || []).join(' · ') || 'see the checklist'}` };
    }
    const modeTxt = rep.mode === 'live'
        ? 'V1–V7 passed — empirically verified against the live target'
        : 'V1–V6 passed (structural — declarations trusted)';
    if (tr.state === 'trusted') {
        const when = tr.signed_at
            ? new Date(Number(tr.signed_at) * 1000).toLocaleString()
            : null;
        return { kind: 'ok', text: `${modeTxt}. Nothing was signed just now — the contract is `
            + `byte-identical to what was already signed${when ? ` on ${when}` : ''}, so that `
            + `existing signature applies again.` };
    }
    return { kind: 'ok',
             text: `${modeTxt} — ${productName} is signable.${liveNote ? ' ' + liveNote : ''}` };
}

// the OBDA-READ split panel opens the Designer by this name (a renderer module cannot import this handler)
if (typeof window !== 'undefined') window.openKgProductsDialog = (...a) => openKgProductsDialog(...a);
async function openKgProductsDialog(kgId, kgName) {
    const H = () => ({ 'Authorization': `Bearer ${localStorage.getItem('tda_auth_token')}`,
                       'Content-Type': 'application/json' });
    const esc = (s) => String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

    // In-progress affordance. A LIVE validation executes real probes at the
    // warehouse and can take seconds; without this the button is indistinguishable
    // from a click that never registered. currentColor keeps it theme-aware.
    if (!document.getElementById('kg-dp-busy-css')) {
        const st = document.createElement('style');
        st.id = 'kg-dp-busy-css';
        st.textContent = `
        .kg-dp-spin{display:inline-block;width:10px;height:10px;margin-right:6px;vertical-align:-1px;
            border:2px solid currentColor;border-right-color:transparent;border-radius:50%;
            animation:kg-dp-rot .6s linear infinite;}
        @keyframes kg-dp-rot{to{transform:rotate(360deg)}}
        @media (prefers-reduced-motion:reduce){.kg-dp-spin{animation:none;opacity:.55}}
        /* while one action runs the rest of the card is inert — except Close,
           which must stay reachable so a slow probe can always be abandoned */
        .kg-dp-card[aria-busy="true"] button:not([data-dp-busy]):not(.kg-dp-close){
            opacity:.45;pointer-events:none;}`;
        document.head.appendChild(st);
    }

    const back = document.createElement('div');
    back.style.cssText = 'position:fixed;inset:0;z-index:10000;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.55);';
    back.innerHTML = `<div class="kg-dp-card" style="width:min(760px,94vw);max-height:90vh;overflow:auto;background:var(--card-bg,#1f2126);border:1px solid var(--border-primary,rgba(148,163,184,0.25));border-radius:14px;box-shadow:0 20px 60px rgba(0,0,0,0.45);"></div>`;
    document.body.appendChild(back);
    const card = back.querySelector('.kg-dp-card');

    // ONE busy wrapper for EVERY async action in this dialog — never a per-button
    // copy. Names what is happening (not just "working"), and the re-entrancy
    // guard makes a double click a no-op instead of a second request.
    async function withBusy(btn, label, fn) {
        if (!btn || btn.dataset.dpBusy) return;
        const prevHtml = btn.innerHTML, prevDisabled = btn.disabled;
        btn.dataset.dpBusy = '1';
        btn.disabled = true;
        btn.innerHTML = `<span class="kg-dp-spin"></span>${esc(label)}`;
        card.setAttribute('aria-busy', 'true');
        try { await fn(); }
        finally {
            card.removeAttribute('aria-busy');
            // the happy path re-renders and replaces this node; restore anyway so
            // a throwing path can never strand a permanently busy button
            if (btn.isConnected) {
                delete btn.dataset.dpBusy; btn.disabled = prevDisabled; btn.innerHTML = prevHtml;
            }
        }
    }
    const close = () => { back.remove(); document.removeEventListener('keydown', onKey, true); };
    const onKey = (e) => { if (e.key === 'Escape') close(); };
    back.addEventListener('click', (e) => { if (e.target === back) close(); });
    document.addEventListener('keydown', onKey, true);

    let expanded = null;      // product name whose detail is open
    let editing = null;       // null | {name|null, text} — the manifest editor state
    let notice = null;        // {kind:'ok'|'err', text} — the last action's outcome
    let liveConn = '';        // V7: '' = structural; else a connection id → live probes
    let connections = [];     // the caller's DB connections (fetched once, best-effort)
    fetch('/api/v1/database-connections', { headers: H() })
        .then(r => (r.ok ? r.json() : {}))
        .then(j => { connections = j.connections || j.items || (Array.isArray(j) ? j : []); })
        .catch(() => {});

    const chip = (txt, bg, fg, tip) => `<span class="u-tip" data-tip="${esc(tip || '')}" style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:6px;background:${bg};color:${fg};">${esc(txt)}</span>`;

    function checklistHtml(report) {
        const rows = (report.checks || []).map(c => {
            const col = c.status === 'pass' ? '#4ade80' : c.status === 'fail' ? '#ef4444' : '#94a3b8';
            const glyph = c.status === 'pass'
                ? '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.4" d="M5 13l4 4L19 7"/>'
                : c.status === 'fail'
                    ? '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.4" d="M6 18L18 6M6 6l12 12"/>'
                    : '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.4" d="M5 12h14"/>';
            return `<div style="display:flex;gap:8px;align-items:baseline;padding:3px 0;">
                <svg style="width:12px;height:12px;flex-shrink:0;align-self:center;" fill="none" stroke="${col}" viewBox="0 0 24 24">${glyph}</svg>
                <span style="font-size:11px;font-weight:700;color:${col};width:26px;flex-shrink:0;">${esc(c.id)}</span>
                <span style="font-size:10px;color:var(--text-muted,#94a3b8);width:64px;flex-shrink:0;">${esc(c.mode)}</span>
                <span style="font-size:11px;color:var(--text-primary,#e5e7eb);">${esc(c.detail)}</span></div>`;
        }).join('');
        return `<div style="background:var(--bg-overlay,rgba(0,0,0,0.15));border-radius:8px;padding:8px 12px;">${rows || '<span style="font-size:11px;color:var(--text-muted);">no checks recorded</span>'}</div>`;
    }

    function grainGridHtml(report) {
        const gm = report.grain_matrix || {};
        const measures = Object.keys(gm);
        if (!measures.length) return '';
        const dims = Object.keys(gm[measures[0]] || {});
        if (!dims.length) return '';
        const head = dims.map(d => `<th style="font-size:9px;font-weight:600;color:var(--text-muted,#94a3b8);padding:3px 6px;text-align:left;white-space:nowrap;">${esc(d)}</th>`).join('');
        const body = measures.map(m => {
            const cells = dims.map(d => {
                const v = gm[m][d] || '';
                const safe = v === 'safe';
                const tip = safe ? `${m} × ${d}: grain-safe` : `${m} × ${d}: ${v.replace(/^rejected:/, '')}`;
                return `<td class="u-tip" data-tip="${esc(tip)}" style="padding:3px 6px;"><span style="display:inline-block;width:14px;height:14px;border-radius:3px;${safe ? 'background:rgba(74,222,128,0.35);border:1px solid #4ade80;' : 'background:transparent;border:1px dashed #f59e0b;'}"></span></td>`;
            }).join('');
            return `<tr><td style="font-size:10px;font-weight:600;color:var(--text-primary,#e5e7eb);padding:3px 6px;white-space:nowrap;">${esc(m)}</td>${cells}</tr>`;
        }).join('');
        return `<div style="margin-top:8px;">
            <div style="font-size:11px;font-weight:600;color:var(--text-primary,#e5e7eb);margin-bottom:4px;">Admissible map — measure × dimension grain safety</div>
            <div style="overflow-x:auto;background:var(--bg-overlay,rgba(0,0,0,0.15));border-radius:8px;padding:6px;"><table style="border-collapse:collapse;"><thead><tr><th></th>${head}</tr></thead><tbody>${body}</tbody></table></div>
            <div style="font-size:10px;color:var(--text-muted,#94a3b8);margin-top:3px;">solid = safe · dashed = rejected (hover names the offending 1:N hop) — a query outside this map is refused, never silently multiplied</div></div>`;
    }

    function ceremonyHtml(p, def) {
        const rep = def.validation_report || {};
        return `<div style="margin-top:8px;background:rgba(234,179,8,0.06);border:1px solid rgba(234,179,8,0.25);border-radius:8px;padding:8px 12px;">
            <div style="font-size:11px;font-weight:700;color:#eab308;margin-bottom:4px;">What signing attests</div>
            <div style="font-size:10px;color:var(--text-primary,#e5e7eb);line-height:1.7;">
                manifest hash <code style="color:var(--text-muted,#94a3b8);">${esc((rep.manifest_hash || '').slice(0, 16))}…</code><br>
                determinism battery <code style="color:var(--text-muted,#94a3b8);">${rep.battery_hash ? esc(rep.battery_hash.slice(0, 16)) + '…' : '(not run — unsignable)'}</code><br>
                evidence: ${(rep.checks || []).some(c => c.mode === 'schema') ? 'schema-backed' : 'structural'} (stated in the verdict — S2)
            </div>
            <div style="font-size:10px;color:var(--text-muted,#94a3b8);margin-top:4px;">"The producer approved this definition AND the platform mechanically verified deterministic, aggregation-safe compilation." Signing is impossible while the gate reports blockers.</div>
        </div>`;
    }

    async function load() {
        card.innerHTML = `<div style="padding:28px;text-align:center;color:var(--text-muted,#94a3b8);">Loading data products…</div>`;
        let cat = {};
        try {
            cat = await fetch(`/api/v1/data-products?kg_id=${encodeURIComponent(kgId)}`,
                              { headers: H() }).then(r => r.json());
        } catch (_) {}
        let details = {};
        if (expanded) {
            try {
                details = await fetch(`/api/v1/data-products/${encodeURIComponent(expanded)}?kg_id=${encodeURIComponent(kgId)}`,
                                      { headers: H() }).then(r => r.json());
            } catch (_) {}
        }
        render(cat.products || [], details);
        // the OBDA-READ split panel and the Workspace list the same catalog — tell them
        UderiaBus.announce('obda_read', 'updated', [], { aspect: 'catalog' });
    }

    function render(products, def) {
        const rows = products.map(p => {
            const t = p.trust || {};
            const st = _DP_TRUST_STYLE[t.state] || _DP_TRUST_STYLE.unavailable;
            const isOpen = expanded === p.product;
            const detail = isOpen && def && def.manifest ? `
                <div style="padding:10px 12px;border-top:1px dashed var(--border-secondary,rgba(148,163,184,0.15));">
                    ${def.validation_report ? `
                        <div style="font-size:11px;font-weight:600;color:var(--text-primary,#e5e7eb);margin-bottom:4px;">Validation gate V1–V7</div>
                        ${checklistHtml(def.validation_report)}
                        ${grainGridHtml(def.validation_report)}
                        ${ceremonyHtml(p, def)}`
                    : `<div style="font-size:11px;color:var(--text-muted,#94a3b8);">Not validated yet — the signature is earned: run the gate first.</div>`}
                </div>` : '';
            return `<div style="border:1px solid var(--border-secondary,rgba(148,163,184,0.15));border-radius:10px;overflow:hidden;">
                <div style="display:flex;align-items:center;gap:8px;padding:8px 12px;flex-wrap:wrap;">
                    <button class="kg-dp-open" data-name="${esc(p.product)}" style="background:none;border:none;cursor:pointer;font-size:13px;font-weight:700;color:var(--text-primary,#e5e7eb);padding:0;">${esc(p.product)}</button>
                    <span style="font-size:10px;color:var(--text-muted,#94a3b8);">v${esc(p.version)} · ${esc(p.backend)}</span>
                    ${chip(p.validated ? 'validated' : 'unvalidated',
                           p.validated ? 'rgba(74,222,128,0.15)' : 'rgba(148,163,184,0.15)',
                           p.validated ? '#4ade80' : '#94a3b8',
                           p.validated ? 'a validation report is stamped on the product' : 'the gate has not run for this manifest')}
                    ${p.validated ? chip(p.signable ? 'signable' : 'blocked',
                           p.signable ? 'rgba(74,222,128,0.15)' : 'rgba(239,68,68,0.15)',
                           p.signable ? '#4ade80' : '#ef4444',
                           p.signable ? 'the gate found no blockers' : 'the gate reported blockers — open the checklist') : ''}
                    ${chip(t.state || 'unavailable', st[0], st[1], st[2])}
                    <span style="flex:1;"></span>
                    <button class="kg-dp-edit" data-name="${esc(p.product)}" style="padding:4px 10px;font-size:11px;font-weight:600;border-radius:6px;cursor:pointer;background:rgba(59,130,246,0.12);color:#3b82f6;border:1px solid rgba(59,130,246,0.4);">Edit</button>
                    <button class="kg-dp-validate" data-name="${esc(p.product)}" style="padding:4px 10px;font-size:11px;font-weight:600;border-radius:6px;cursor:pointer;background:rgba(6,182,212,0.12);color:#06b6d4;border:1px solid rgba(6,182,212,0.4);">Validate</button>
                    <button class="kg-dp-delete" data-name="${esc(p.product)}" title="Delete this product from the ontology. Its trust attestation is revoked with it — a signature over a deleted contract attests nothing. The ontology's own seal is unaffected." style="padding:4px 10px;font-size:11px;font-weight:600;border-radius:6px;cursor:pointer;background:rgba(248,113,113,0.10);color:#f87171;border:1px solid rgba(248,113,113,0.35);">Delete</button>
                    <button class="kg-dp-sign" data-name="${esc(p.product)}" ${p.signable && t.state !== 'trusted' ? '' : 'disabled'} title="${esc(
                        t.state === 'trusted'
                            ? 'Already signed, and the contract still matches that signature — there is nothing to sign. Change it and this becomes available again once re-proven.'
                            : t.state === 'changed'
                                ? 'The contract no longer matches its signature. Re-validate to re-prove it before it can be signed again.'
                                : p.signable
                                    ? 'Sign this contract together with its machine-generated proof'
                                    : 'Not signable yet — run Validate first; a signature here always carries a proof')}" style="padding:4px 10px;font-size:11px;font-weight:600;border-radius:6px;cursor:${p.signable && t.state !== 'trusted' ? 'pointer' : 'not-allowed'};background:rgba(234,179,8,0.12);color:#eab308;border:1px solid rgba(234,179,8,0.4);opacity:${p.signable && t.state !== 'trusted' ? 1 : 0.45};">Sign</button>
                </div>${detail}</div>`;
        }).join('');

        const editor = editing != null ? `
            <div style="border:1px solid rgba(59,130,246,0.35);border-radius:10px;padding:10px 12px;">
                <div style="font-size:11px;font-weight:700;color:#3b82f6;margin-bottom:6px;">${editing.name ? `Edit manifest — ${esc(editing.name)}` : 'New data product — the manifest is the contract'}</div>
                <textarea class="kg-dp-manifest" spellcheck="false" style="width:100%;min-height:220px;font-family:ui-monospace,monospace;font-size:11px;line-height:1.5;background:var(--bg-overlay,rgba(0,0,0,0.25));color:var(--text-primary,#e5e7eb);border:1px solid var(--border-primary,rgba(148,163,184,0.25));border-radius:8px;padding:8px;">${esc(editing.text)}</textarea>
                <div style="display:flex;gap:8px;margin-top:6px;">
                    <button class="kg-dp-save" style="padding:5px 12px;font-size:11px;font-weight:600;border-radius:6px;cursor:pointer;background:rgba(74,222,128,0.12);color:#4ade80;border:1px solid rgba(74,222,128,0.4);">Save & link members</button>
                    <button class="kg-dp-cancel" style="padding:5px 12px;font-size:11px;font-weight:600;border-radius:6px;cursor:pointer;background:none;color:var(--text-muted,#94a3b8);border:1px solid var(--border-primary,rgba(148,163,184,0.25));">Cancel</button>
                    <span style="font-size:10px;color:var(--text-muted,#94a3b8);align-self:center;">Saving an edited manifest drops its validation report — re-validate before signing.</span>
                </div>
            </div>` : '';

        const noticeHtml = notice ? `<div style="font-size:11px;border-radius:8px;padding:7px 10px;background:${notice.kind === 'ok' ? 'rgba(74,222,128,0.1)' : 'rgba(239,68,68,0.1)'};color:${notice.kind === 'ok' ? '#4ade80' : '#ef4444'};border:1px solid ${notice.kind === 'ok' ? 'rgba(74,222,128,0.3)' : 'rgba(239,68,68,0.3)'};">${esc(notice.text)}</div>` : '';

        card.innerHTML = `
          <div style="display:flex;align-items:center;justify-content:space-between;padding:14px 18px;border-bottom:1px solid var(--border-secondary,rgba(148,163,184,0.15));">
            <div style="font-size:14px;font-weight:700;color:var(--text-primary,#e5e7eb);">
                Data products · <span style="color:var(--text-muted,#94a3b8);">${esc(kgName || '')}</span>
                <div style="font-size:10px;font-weight:400;color:var(--text-muted,#94a3b8);margin-top:2px;">Signed, deterministic consumption contracts over this ontology (OBDA-READ). Same request → same governed SQL → same rows.</div>
            </div>
            <button class="kg-dp-close" style="background:none;border:none;color:var(--text-muted,#94a3b8);font-size:20px;cursor:pointer;line-height:1;">×</button>
          </div>
          <div style="padding:14px 18px;display:flex;flex-direction:column;gap:10px;">
            <div style="display:flex;align-items:center;gap:8px;font-size:11px;color:var(--text-muted,#94a3b8);">
                <span style="text-transform:uppercase;letter-spacing:0.05em;font-size:10px;">Verification</span>
                <select class="kg-dp-conn" style="font-size:11px;padding:3px 6px;border-radius:6px;background:var(--bg-overlay,rgba(0,0,0,0.25));color:var(--text-primary,#e5e7eb);border:1px solid var(--border-primary,rgba(148,163,184,0.25));">
                    <option value="" ${liveConn ? '' : 'selected'}>Structural — declarations trusted (no connection)</option>
                    ${connections.map(c => `<option value="${esc(c.id)}" ${liveConn === c.id ? 'selected' : ''}>Live — probe against ${esc(c.name || c.id)}</option>`).join('')}
                </select>
                <span>V7: battery executes at the target; declared cardinalities &amp; keys spot-checked empirically. A contradiction blocks signing.</span>
            </div>
            ${noticeHtml}
            ${rows || `<div style="font-size:12px;color:var(--text-muted,#94a3b8);">No data products on this ontology yet. A product declares WHICH entities are exposed, the ONLY joins assembly may use, and the measures — then the platform proves it deterministic before it can be signed.</div>`}
            ${editor}
            ${editing == null ? `<div><button class="kg-dp-new" style="padding:6px 12px;font-size:12px;font-weight:600;border-radius:7px;cursor:pointer;background:rgba(234,179,8,0.1);color:#eab308;border:1px solid rgba(234,179,8,0.4);">+ New product</button></div>` : ''}
          </div>`;

        card.querySelector('.kg-dp-close').onclick = close;
        const connSel = card.querySelector('.kg-dp-conn');
        if (connSel) connSel.onchange = () => { liveConn = connSel.value; };
        const newBtn = card.querySelector('.kg-dp-new');
        if (newBtn) newBtn.onclick = () => { editing = { name: null, text: _DP_TEMPLATE(kgName) }; notice = null; render(products, def); };
        const cancelBtn = card.querySelector('.kg-dp-cancel');
        if (cancelBtn) cancelBtn.onclick = () => { editing = null; render(products, def); };
        const saveBtn = card.querySelector('.kg-dp-save');
        if (saveBtn) saveBtn.onclick = () => withBusy(saveBtn, 'Saving…', async () => {
            let manifest;
            try { manifest = JSON.parse(card.querySelector('.kg-dp-manifest').value); }
            catch (err) { notice = { kind: 'err', text: `Not valid JSON: ${err.message}` }; render(products, def); return; }
            const r = await fetch('/api/v1/data-products', { method: 'POST', headers: H(),
                                  body: JSON.stringify({ kg_id: kgId, manifest }) });
            const j = await r.json().catch(() => ({}));
            if (r.ok) {
                notice = { kind: 'ok', text: `${j.action === 'updated' ? 'Updated' : 'Created'} ${j.product} — members linked: ${(j.members_linked || []).join(', ') || 'none'}${(j.members_missing || []).length ? ` · NOT on the graph: ${j.members_missing.join(', ')}` : ''}. ${j.note || ''}` };
                editing = null; expanded = j.product;
            } else {
                // the REJECT taxonomy, verbatim — self-correction food
                notice = { kind: 'err', text: `${j.error || 'error'}: ${j.message || r.status}` };
            }
            await load();
        });
        card.querySelectorAll('.kg-dp-open').forEach(b => b.onclick = async () => {
            expanded = expanded === b.dataset.name ? null : b.dataset.name; notice = null; await load(); });
        card.querySelectorAll('.kg-dp-edit').forEach(b => b.onclick = () => withBusy(b, 'Loading…', async () => {
            const d = await fetch(`/api/v1/data-products/${encodeURIComponent(b.dataset.name)}?kg_id=${encodeURIComponent(kgId)}`,
                                  { headers: H() }).then(r => r.json()).catch(() => ({}));
            editing = { name: b.dataset.name, text: JSON.stringify(d.manifest || {}, null, 2) };
            notice = null; render(products, def); }));
        card.querySelectorAll('.kg-dp-delete').forEach(b => b.onclick = () => {
            // Two-step arm instead of a native confirm() (platform standard: no
            // native dialogs). First click arms the button; a second within 5s
            // deletes; anywhere else it disarms on re-render.
            if (!b.dataset.armed) {
                b.dataset.armed = '1';
                b.textContent = 'Really delete?';
                b.style.background = 'rgba(248,113,113,0.25)';
                setTimeout(() => {
                    if (b.isConnected) { delete b.dataset.armed; b.textContent = 'Delete';
                                         b.style.background = 'rgba(248,113,113,0.10)'; }
                }, 5000);
                return;
            }
            return withBusy(b, 'Deleting…', async () => {
                notice = null;
                const r = await fetch(`/api/v1/data-products/${encodeURIComponent(b.dataset.name)}?kg_id=${encodeURIComponent(kgId)}`,
                                      { method: 'DELETE', headers: H() });
                const j = await r.json().catch(() => ({}));
                if (r.ok) {
                    notice = { kind: 'ok', text: `Deleted ${b.dataset.name}. Its attestation is revoked with it; the ontology's seal is unaffected.` };
                    if (expanded === b.dataset.name) expanded = null;
                } else {
                    notice = { kind: 'err', text: `${j.error || 'error'}: ${j.message || r.status}` };
                }
                await load();
            });
        });
        card.querySelectorAll('.kg-dp-validate').forEach(b => b.onclick = () => {
            // name the work: a structural pass is instant, a live pass goes to the
            // warehouse — the label should tell the operator which one they started
            const conn = connections.find(c => String(c.id) === String(liveConn));
            const label = liveConn
                ? `Probing ${(conn && (conn.name || conn.id)) || 'the target'}…`
                : 'Validating…';
            return withBusy(b, label, async () => {
            notice = null;
            const body = { kg_id: kgId };
            if (liveConn) body.connection_id = liveConn;
            const r = await fetch(`/api/v1/data-products/${encodeURIComponent(b.dataset.name)}/validate`,
                                  { method: 'POST', headers: H(), body: JSON.stringify(body) });
            const j = await r.json().catch(() => ({}));
            if (r.ok) {
                const rep = j.report || {};
                notice = _dpValidateNotice(rep, j.trust, b.dataset.name, j.live_note);
                expanded = b.dataset.name;
            } else notice = { kind: 'err', text: `${j.error || 'error'}: ${j.message || r.status}` };
            await load(); }); });
        card.querySelectorAll('.kg-dp-sign').forEach(b => b.onclick = () => {
            if (b.disabled) return;
            return withBusy(b, 'Signing…', async () => {
            notice = null;
            const r = await fetch(`/api/v1/trust/data-product/${encodeURIComponent(kgId)}/${encodeURIComponent(b.dataset.name)}/sign-off`,
                                  { method: 'POST', headers: H() });
            const j = await r.json().catch(() => ({}));
            if (r.ok && j.signed) {
                notice = { kind: 'ok', text: `Signed — verified deterministic. ${(j.version_hash || '').slice(0, 16)}…` };
            } else {
                const v = j.validation || {};
                notice = { kind: 'err', text: `Sign refused (${v.status || j.reason || j.error || r.status}): ${v.note || j.message || ''}` };
            }
            expanded = b.dataset.name;
            await load(); }); });
    }

    await load();
}
