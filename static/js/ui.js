/**
 * ui.js
 * * This module handles all direct DOM manipulations and UI updates.
 * Functions here are responsible for rendering messages, modals, status updates, and other visual changes.
 */

import { nodesLabel, entitiesLabel, relationshipsLabel, sizeLabel, addedLabel, entityCountOf, draftBreakdown } from './kgVocabulary.js?v=1.0';
import * as DOM from './domElements.js';
import { state } from './state.js';
import { renderChart, isPrivilegedUser, isPromptCustomForModel, getNormalizedModelId } from './utils.js';
import { renderComponent, hasRenderer, loadRendererModule } from './componentRenderers.js?v=1.7';
import { tabLabel, fillCategoryTab } from './handlers/resourceBadge.js?v=1.3';
export { tabLabel, fillCategoryTab };
// We need access to the functions that will handle save/cancel from the input
import { handleSessionRenameSave, handleSessionRenameCancel } from './handlers/sessionManagement.js?v=3.209';
import { handleReplayQueryClick } from './eventHandlers.js?v=4.214';
import { checkServerStatus, loadSessions, fetchTurnDetails } from './api.js?v=1.2';
import { exportKnowledgeRepository, importKnowledgeRepository, fetchCorpusHealth } from './handlers/knowledgeRepositoryHandler.js?v=1.208';
import { groupByAgentPack, createPackContainerCardDOM, attachPackContainerHandlers, updatePackContainerDocCounts } from './handlers/agentPackGrouping.js?v=1.2';
import { PROFILE_CLASS_COLORS, getProfileColor, getProfileColorByTag, profileTypeForTag as getProfileTypeForTag } from './profileColors.js';

// 🔥 DEBUG: Module load detection (v1.0 - Feb 13, 2026)
console.log('%c🔥 UI.JS LOADED - VERSION 1.0 (NEW CODE)', 'background: #00ff00; color: #000; font-size: 16px; font-weight: bold; padding: 5px;');

// NOTE: This module no longer imports from eventHandlers.js, breaking a circular dependency.
// Instead, eventHandlers.js will import these UI functions.

// ============================================================================
// Profile Type Colors for Status Window Tags
// CORRECTED IFOC MAPPING: Coordinate=Orange (Teradata brand)
// ============================================================================

// Single source of truth — see static/js/profileColors.js.
const PROFILE_TYPE_COLORS = PROFILE_CLASS_COLORS;

// ============================================================================
// EVENT CATEGORY COLOR CODING SYSTEM (6 Categories)
// Maps event types to background color categories
// Left border = execution state (active=orange, completed=green)
// Background = event category (success, system, optimization, error, coordination)
// ============================================================================
// ============================================================================
// EVENT VISUAL CATEGORY OVERRIDES
// Only errors and lifecycle bookends need explicit overrides.
// All other events derive their visual category from getFilterCategory(),
// so event card colors match the filter pill dot colors exactly.
// ============================================================================
const EVENT_CATEGORY_OVERRIDES = {
    // Error Events (Red) - always override regardless of filter category
    'execution_error': 'error',
    'tool_error': 'error',
    'error': 'error',
    'execution_cancelled': 'error',

    // Lifecycle Bookend Events (Slate) - neutral visual framing
    'execution_start': 'lifecycle',
    'execution_complete': 'lifecycle',
    'conversation_agent_start': 'lifecycle',
    'conversation_agent_complete': 'lifecycle',
    'genie_coordination_start': 'lifecycle',
    'genie_coordination_complete': 'lifecycle',
};

/**
 * Get the visual category for an event (controls border-left color and background).
 * Unified with filter categories so event cards match filter pill colors.
 * @param {string} eventType - The event type
 * @param {string} [filterCategory] - Pre-computed filter category from eventData._filterCategory
 * @returns {string} The visual category (context, knowledge, planning, tool-execution, agent, coordination, system, error, lifecycle)
 */
/**
 * The category a type ALWAYS has, whatever list replayed it and whatever a caller passes.
 * Returns null when the type does not determine its own category.
 *
 * This is the authority, not a default. The reload path replays trust verdicts out of
 * `system_events` and passes `'system'` EXPLICITLY, so making the computed value a mere
 * fallback still left the card System yellow after a plan reload — an explicit wrong
 * answer beat a correct computed one. A trust card's category is a property of WHAT IT
 * IS; no caller may overrule that by naming the bucket it was stored in.
 */
/**
 * The Living Answer's key for the step this card renders (S1.1 depth 3). The receipt's reducer
 * writes it ONCE onto the event that produced a row — the SSE frame / its payload live, the
 * persisted record on a reload (`LivingAnswer.stampTurn`) — and the card rendered from that
 * same object wears it as `data-la`, so a row finds its card by identity, never by words.
 * Read at the card's creation, the way `_filterCategory` is; null when the step is no row.
 */
function _laKeyOf(eventData) {
    if (!eventData || typeof eventData !== 'object') return null;
    const k = eventData.__la || eventData._la
        || (eventData.details && typeof eventData.details === 'object' && eventData.details.__la)
        || (eventData.payload && typeof eventData.payload === 'object' && eventData.payload.__la);
    return k ? String(k) : null;
}
function _stampLa(stepEl, eventData) {
    const k = _laKeyOf(eventData); if (k) stepEl.dataset.la = k;
}

function categoryByType(type) {
    if (type === 'grounding_gate' || type === 'guardrail' || type === 'obda'
         || type === 'honesty' || type === 'compute_lineage' || type === 'llm_recovery' || type === 'turn_budget' || type === 'action_record' || type === 'tool_surface' || type === 'outcome_revision' || type === 'attachment_sources' || type === 'judge_evaluation_complete') return 'assurance';
    // A deterministic rewrite the tool wrapper applied is TOOL EXECUTION — the ReAct twin of
    // the Optimize `workaround` step — whichever source replays it: live it arrives on the
    // conversation-agent source (which files everything as `agent`), on reload it is
    // replayed out of `conversation_agent_events`; the same fact must not be filed two ways.
    if (type === 'conversation_tool_rewrite') return 'tool-execution';
    return null;
}

function getEventCategory(eventType, filterCategory) {
    // Errors and lifecycle bookends always use their override
    const override = EVENT_CATEGORY_OVERRIDES[eventType];
    if (override) return override;
    // Type authority beats any stamp — see categoryByType.
    const byType = categoryByType(eventType);
    if (byType) return byType;
    // COMPUTE when the stamp is absent — never default to `system`. Not every render
    // path stamps `_filterCategory`: the reload wrappers take it as an optional argument
    // and pass null, so a trust card that was fuchsia live came back System yellow after
    // a plan reload. The visual category and the filter category must be ONE decision,
    // reached the same way whoever is asking.
    return filterCategory || getFilterCategory(undefined, eventType);
}

/**
 * Get human-readable backend label from backend_type string
 * @param {string} backendType - Backend type (chromadb, qdrant, teradata)
 * @returns {string} - Formatted label (ChromaDB, Qdrant, Teradata)
 */
function _getBackendLabel(backendType) {
    const labels = {
        'chromadb': 'ChromaDB',
        'qdrant': 'Qdrant',
        'teradata': 'Teradata'
    };
    return labels[backendType] || backendType.charAt(0).toUpperCase() + backendType.slice(1);
}

/**
 * Create a styled backend badge DOM element matching knowledgeRepositoryHandler.js style.
 */
function _createBackendBadge(backendType) {
    const backends = {
        chromadb: { label: 'ChromaDB', color: '#4ade80', bg: 'rgba(74,222,128,0.12)', border: 'rgba(74,222,128,0.3)' },
        teradata: { label: 'Teradata', color: '#F15F22', bg: 'rgba(241,95,34,0.12)', border: 'rgba(241,95,34,0.3)' },
        qdrant: { label: 'Qdrant', color: '#a78bfa', bg: 'rgba(167,139,250,0.12)', border: 'rgba(167,139,250,0.3)' }
    };
    const c = backends[backendType] || backends.chromadb;
    const badge = document.createElement('span');
    badge.style.cssText = `display:inline-flex;align-items:center;padding:1px 5px;font-size:10px;font-weight:600;border-radius:3px;background:${c.bg};color:${c.color};border:1px solid ${c.border};margin-right:4px;`;
    badge.textContent = `${c.label}:`;
    return badge;
}

// ============================================================================
// EVENT FILTER CATEGORY SYSTEM
// Maps (source, type) to one of 7 filter categories for the header filter chips.
// This is separate from EVENT_CATEGORY_MAP which controls visual styling.
// ============================================================================

/**
 * Determine the filter category for an event based on its source and type.
 * @param {string} source - The event source (rest, genie, conversation_agent, etc.)
 * @param {string} type - The event type (phase_start, tool_intent, etc.)
 * @returns {string} The filter category name
 */
function getFilterCategory(source, type) {
    // A trust card's category is a property of WHAT IT IS, not of which list it was
    // replayed from. Live, a grounding verdict arrives on the `interactive` source and
    // routed to `knowledge`; on reload the same verdict is replayed out of
    // `system_events`, whose source short-circuits below before the type is ever
    // consulted — so the identical card came back as `system` and changed colour after
    // a plan reload. The same fact must not be filed two ways.
    const byType = categoryByType(type);
    if (byType) return byType;
    // Source-level routing (highest priority)
    if (source === 'context_window') return 'context';
    if (source === 'genie') return 'coordination';
    if (source === 'conversation_agent') return 'agent';
    if (source === 'knowledge_retrieval') return 'knowledge';
    if (source === 'session_name') return 'system';
    if (source === 'skills') return 'system';
    if (source === 'extension') return 'system';
    if (source === 'lifecycle') return 'system';

    // Type-level routing for rest/interactive sources
    if (source === 'rest' || source === 'interactive') {
        if (type === 'phase_start' || type === 'phase_end' ||
            type === 'plan_generated' || type === 'plan_optimization') {
            return 'planning';
        }
        if (type === 'tool_intent' || type === 'tool_result' ||
            type === 'tool_error' || type === 'workaround') {
            return 'tool-execution';
        }
        if (type === 'context_optimization' || type === 'context_window_snapshot') {
            return 'context';
        }
        if (type?.startsWith('knowledge_') || type === 'rag_llm_step' ||
            type === 'kg_enrichment' || type === 'grounding_gate' ||
            type === 'guardrail' || type === 'obda' || type === 'honesty' || type === 'compute_lineage' || type === 'llm_recovery' || type === 'turn_budget' || type === 'action_record' || type === 'tool_surface' || type === 'outcome_revision' || type === 'attachment_sources' ||
            type === 'llm_execution' ||
            type === 'llm_execution_complete') {
            return 'knowledge';
        }
        if (type?.startsWith('conversation_') || type === 'component_llm_resolution') {
            return 'agent';
        }
    }

    return 'system';
}

/**
 * Check if a given filter category is currently enabled (visible).
 * @param {string} category - The filter category name
 * @returns {boolean} True if the category is enabled
 */
function isFilterCategoryEnabled(category) {
    return state.eventFilterState[category] !== false;
}

/**
 * Toggle visibility of events matching a filter category.
 * Updates chip UI, persists to localStorage, and applies to DOM.
 * @param {string} category - The filter category to toggle
 */
export function toggleEventFilter(category) {
    const isNowEnabled = !state.eventFilterState[category];
    state.eventFilterState[category] = isNowEnabled;

    // Update chip UI
    const chip = document.querySelector(`.event-filter-chip[data-filter="${category}"]`);
    if (chip) {
        chip.classList.toggle('active', isNowEnabled);
    }

    // Persist to localStorage
    try {
        localStorage.setItem('uderia_event_filter_state', JSON.stringify(state.eventFilterState));
    } catch (e) { /* ignore storage errors */ }

    applyEventFilterToDOM();
}

/**
 * Apply the current filter state to all event steps in the status window.
 * Adds/removes 'event-filtered-hidden' CSS class based on data-filter-category.
 * Called after filter toggle AND after reload rendering completes.
 */
export function applyEventFilterToDOM() {
    const container = DOM.statusWindowContent;
    if (!container) return;

    const allTagged = container.querySelectorAll('[data-filter-category]');
    allTagged.forEach(el => {
        const category = el.dataset.filterCategory;
        if (category && state.eventFilterState[category] === false) {
            el.classList.add('event-filtered-hidden');
        } else {
            el.classList.remove('event-filtered-hidden');
        }
    });
}

/**
 * Sync filter chip UI with persisted state on page load.
 * Ensures chips reflect previously disabled categories.
 */
// Anything inserted into the trace after the last pass — the assurance band, mounted at
// turn start — asks to be filtered rather than arriving unfiltered until the next update.
UderiaBus.on('uderia:filterable-added', () => applyEventFilterToDOM());

export function syncFilterChipsWithState() {
    const chips = document.querySelectorAll('.event-filter-chip');
    chips.forEach(chip => {
        const category = chip.dataset.filter;
        if (category) {
            chip.classList.toggle('active', state.eventFilterState[category] !== false);
        }
    });
}

/**
 * Get the color for a profile tag based on its profile type.
 * @param {string} profileTag - The profile tag (e.g., 'OPTIM', 'GENIE')
 * @param {string} profileType - Optional profile type (llm_only, rag_focused, tool_enabled, genie)
 * @returns {string} The hex color for the profile type
 */
function getProfileTagColor(profileTag, profileType) {
    // Delegate to the single source of truth (profileColors.js).
    return profileType ? getProfileColor(profileType) : getProfileColorByTag(profileTag);
}

/**
 * Adjust color brightness by a percentage.
 * @param {string} hex - Hex color code (e.g., "#F15F22")
 * @param {number} percent - Percentage to adjust (-100 to 100). Positive = lighter, negative = darker.
 * @returns {string} Adjusted hex color code
 */
function adjustColorBrightness(hex, percent) {
    // Remove # if present
    hex = hex.replace('#', '');

    // Convert hex to RGB
    const r = parseInt(hex.slice(0, 2), 16);
    const g = parseInt(hex.slice(2, 4), 16);
    const b = parseInt(hex.slice(4, 6), 16);

    // Adjust brightness
    const factor = 1 + (percent / 100);
    const newR = Math.min(255, Math.max(0, Math.round(r * factor)));
    const newG = Math.min(255, Math.max(0, Math.round(g * factor)));
    const newB = Math.min(255, Math.max(0, Math.round(b * factor)));

    // Convert back to hex
    const toHex = (n) => n.toString(16).padStart(2, '0');
    return `#${toHex(newR)}${toHex(newG)}${toHex(newB)}`;
}

/**
 * Determine if a color is light (for text contrast calculation).
 * @param {string} hex - Hex color code (e.g., "#4ade80")
 * @returns {boolean} True if color is light (needs dark text)
 */
function isLightColor(hex) {
    // Remove # if present
    hex = hex.replace('#', '');

    // Convert hex to RGB
    const r = parseInt(hex.slice(0, 2), 16);
    const g = parseInt(hex.slice(2, 4), 16);
    const b = parseInt(hex.slice(4, 6), 16);

    // Calculate relative luminance (WCAG formula)
    const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;

    // Return true if luminance > 0.5 (light color needs dark text)
    return luminance > 0.5;
}

/**
 * Render a profile tag with the correct profile type color using PURE INDUSTRIAL SOLID STYLING.
 * @param {string} tag - The profile tag (without @)
 * @param {string} profileType - Optional profile type for color lookup
 * @param {string} additionalClasses - Optional additional CSS classes
 * @returns {string} HTML string for the colored profile tag
 */
export function renderProfileTag(tag, profileType, additionalClasses = '') {
    const color = getProfileTagColor(tag, profileType);

    // PURE INDUSTRIAL: Solid colors, flat monolithic design
    // Border matches background (no gradient depth effect)
    const hoverColor = adjustColorBrightness(color, 10);     // 10% lighter for hover

    return `<span class="profile-tag profile-tag--sm ${additionalClasses}" style="--profile-tag-bg: ${color}; --profile-tag-border: ${color}; --profile-tag-bg-hover: ${hoverColor}; --profile-tag-text: #FFFFFF;">@${tag}</span>`;
}

// Published for non-module consumers — the same dual-surface pattern
// `profileColors.js` uses. An IIFE handler (the Agents lens, component renderers)
// must render THE profile tag, not a lookalike: the chip's markup, its three CSS
// variables and its hover colour are one design, and a second copy is how ~10 of
// them came to exist across this file alone.
if (typeof window !== 'undefined') window.renderProfileTag = renderProfileTag;


// ============================================================================
// Genie Slave Session Collapse State Management
// ============================================================================

const GENIE_COLLAPSE_STATE_KEY = 'genie_slave_collapse_state';

/**
 * Get the collapse state for genie slave sessions from localStorage.
 * Returns an object mapping parent session IDs to boolean (true = collapsed).
 */
export function getGenieCollapseState() {
    try {
        const stored = localStorage.getItem(GENIE_COLLAPSE_STATE_KEY);
        return stored ? JSON.parse(stored) : {};
    } catch (e) {
        console.warn('[UI] Failed to read genie collapse state:', e);
        return {};
    }
}

/**
 * Save the collapse state for genie slave sessions to localStorage.
 */
function saveGenieCollapseState(state) {
    try {
        localStorage.setItem(GENIE_COLLAPSE_STATE_KEY, JSON.stringify(state));
    } catch (e) {
        console.warn('[UI] Failed to save genie collapse state:', e);
    }
}

/**
 * Toggle visibility of child sessions for a genie parent session.
 * @param {string} parentSessionId - The session ID of the genie parent
 */
export function toggleGenieSlaveVisibility(parentSessionId) {
    const collapseState = getGenieCollapseState();
    const isCurrentlyCollapsed = collapseState[parentSessionId] || false;
    const newState = !isCurrentlyCollapsed;

    // Update state
    collapseState[parentSessionId] = newState;
    saveGenieCollapseState(collapseState);

    // Recursively find all descendant sessions (children, grandchildren, etc.)
    function getAllDescendants(ancestorId) {
        const directChildren = Array.from(document.querySelectorAll(`[data-genie-parent-id="${ancestorId}"]`));
        let allDescendants = [...directChildren];

        // For each direct child, recursively get their descendants
        directChildren.forEach(childItem => {
            const childSessionId = childItem.dataset.sessionId;
            if (childSessionId) {
                const grandchildren = getAllDescendants(childSessionId);
                allDescendants = allDescendants.concat(grandchildren);
            }
        });

        return allDescendants;
    }

    // Find all descendants (including nested children)
    const slaveItems = getAllDescendants(parentSessionId);

    slaveItems.forEach((item, index) => {
        if (newState) {
            // Collapsing: apply class IMMEDIATELY like expand does (no setTimeout)
            item.classList.add('genie-slave-hidden');

            // Find wrapper and apply collapsed state via JavaScript (not CSS :has())
            const wrapper = item.closest('.genie-wrapper');
            if (wrapper) {
                wrapper.classList.add('genie-wrapper-collapsed');
            }

            // Force reflow before animation
            item.offsetHeight;

            // Use requestAnimationFrame for batched style updates (synchronized timeline)
            requestAnimationFrame(() => {
                item.style.transition = `opacity 0.3s cubic-bezier(0.4, 0, 0.2, 1) ${index * 0.05}s,
                                         max-height 0.3s cubic-bezier(0.4, 0, 0.2, 1) ${index * 0.05}s,
                                         margin 0.3s cubic-bezier(0.4, 0, 0.2, 1) ${index * 0.05}s`;
                item.style.opacity = '0';
                item.style.maxHeight = '0';
                item.style.marginTop = '0';
                item.style.marginBottom = '0';
                item.style.overflow = 'hidden';
            });

            // Clean up inline styles after animation completes
            item.addEventListener('transitionend', function cleanup(e) {
                // Only clean up on the final property (max-height typically finishes last)
                if (e.propertyName === 'max-height') {
                    item.style.transition = '';
                    item.style.opacity = '';
                    item.style.maxHeight = '';
                    item.style.margin = '';
                    item.style.marginTop = '';
                    item.style.marginBottom = '';
                    item.style.overflow = '';
                    item.removeEventListener('transitionend', cleanup);
                }
            });
        } else {
            // Expanding: remove classes first
            item.classList.remove('genie-slave-hidden');

            // Remove wrapper collapsed state
            const wrapper = item.closest('.genie-wrapper');
            if (wrapper) {
                wrapper.classList.remove('genie-wrapper-collapsed');
            }
            item.style.transition = `opacity 0.3s ease ${index * 0.05}s, max-height 0.3s ease ${index * 0.05}s`;
            item.style.overflow = 'hidden';
            item.style.maxHeight = '0';
            item.style.opacity = '0';

            // Trigger reflow to ensure transition works
            item.offsetHeight;

            // Animate to full visibility
            requestAnimationFrame(() => {
                item.style.opacity = '1';
                item.style.maxHeight = '200px'; // Enough for session item
                item.style.marginTop = '';
                item.style.marginBottom = '';
            });

            // Clean up inline styles after animation completes
            item.addEventListener('transitionend', function cleanup(e) {
                // Only clean up on the final property (max-height typically finishes last)
                if (e.propertyName === 'max-height') {
                    item.style.maxHeight = '';
                    item.style.overflow = '';
                    item.style.transition = '';
                    item.style.opacity = '';
                    item.removeEventListener('transitionend', cleanup);
                }
            });
        }
    });

    // Update the toggle icon in the parent with rotation animation
    const parentItem = document.getElementById(`session-${parentSessionId}`);
    if (parentItem) {
        const toggleIcon = parentItem.querySelector('.genie-collapse-toggle');
        if (toggleIcon) {
            // Smooth rotation animation
            toggleIcon.style.transform = newState ? 'rotate(-90deg)' : 'rotate(0deg)';
        }
    }

    console.log(`[UI] Toggled genie slave visibility for ${parentSessionId}: ${newState ? 'collapsed' : 'expanded'}`);
}

/**
 * Mark sessions that are genie parents (have child sessions).
 * Call this after loading all sessions to identify which sessions have children.
 * @param {Array} sessions - Array of session objects
 * @returns {Array} - Sessions with _isGenieMaster flag added (flag name preserved for compatibility)
 */
export function markGenieMasterSessions(sessions) {
    // Build a set of parent session IDs that have slaves
    const parentIds = new Set();
    sessions.forEach(session => {
        const genieMetadata = session.genie_metadata || {};
        if (genieMetadata.is_genie_slave && genieMetadata.parent_session_id) {
            parentIds.add(genieMetadata.parent_session_id);
        }
    });

    // Mark sessions that have children as genie parents
    sessions.forEach(session => {
        session._isGenieMaster = parentIds.has(session.id);
    });

    console.log(`[UI] Marked ${parentIds.size} sessions as genie parents`);
    return sessions;
}

/**
 * Sort sessions hierarchically so slave sessions always appear directly under their parent.
 * This ensures proper visual ordering even during Genie execution when slaves are created/updated.
 *
 * @param {Array} sessions - Array of session objects
 * @returns {Array} - Sessions sorted with slaves directly under their parents
 */
export function sortSessionsHierarchically(sessions) {
    // Build parent-to-children map for ALL sessions (supports multi-level nesting)
    const parentToChildren = new Map(); // parentId -> [child sessions]
    const topLevelSessions = []; // Sessions with no parent

    // First pass: catalog all sessions
    sessions.forEach(session => {
        const genieMetadata = session.genie_metadata || {};
        const parentId = genieMetadata.parent_session_id;

        if (genieMetadata.is_genie_slave && parentId) {
            // This session has a parent - add to parent's children list
            if (!parentToChildren.has(parentId)) {
                parentToChildren.set(parentId, []);
            }
            parentToChildren.get(parentId).push(session);
        } else {
            // This is a top-level session (no parent)
            topLevelSessions.push(session);
        }
    });

    // Recursive function to add session and all its descendants
    function addSessionWithChildren(session, sorted) {
        // Add the session itself
        sorted.push(session);

        // Get children of this session
        const children = parentToChildren.get(session.id);
        if (children && children.length > 0) {
            // Sort children by slave_sequence_number first (reflects coordinator's slaveProfiles
            // config order, assigned race-free on creation). Fall back to created_at for sessions
            // created before this fix (where all sequence numbers are 0 or 1).
            children.sort((a, b) => {
                const seqA = (a.genie_metadata || {}).slave_sequence_number || 0;
                const seqB = (b.genie_metadata || {}).slave_sequence_number || 0;
                if (seqA > 0 && seqB > 0 && seqA !== seqB) return seqA - seqB;
                const timeA = new Date(a.created_at || 0).getTime();
                const timeB = new Date(b.created_at || 0).getTime();
                return timeA - timeB;
            });

            // Recursively add each child and its descendants
            children.forEach(child => {
                addSessionWithChildren(child, sorted);
            });
        }
    }

    // Build the final sorted list starting with top-level sessions
    const sorted = [];

    // Sort top-level sessions by last_updated (NEWEST FIRST - most recently active at top)
    topLevelSessions.sort((a, b) => {
        const timeA = new Date(a.last_updated || a.created_at || 0).getTime();
        const timeB = new Date(b.last_updated || b.created_at || 0).getTime();
        return timeB - timeA;  // Reversed: newest first
    });

    // Add each top-level session and all its descendants recursively
    const processedParentIds = new Set(topLevelSessions.map(s => s.id));
    topLevelSessions.forEach(session => {
        addSessionWithChildren(session, sorted);
    });

    // Collect orphaned slaves: slaves whose parent is not in this batch.
    // This happens when paginating — the parent arrived on an earlier page.
    // Don't drop them silently; callers can use their genie_metadata to do
    // correct DOM insertion (after parent's existing children in the DOM).
    parentToChildren.forEach((children, parentId) => {
        if (!processedParentIds.has(parentId)) {
            children.sort((a, b) => {
                const seqA = (a.genie_metadata || {}).slave_sequence_number || 0;
                const seqB = (b.genie_metadata || {}).slave_sequence_number || 0;
                if (seqA > 0 && seqB > 0 && seqA !== seqB) return seqA - seqB;
                const timeA = new Date(a.created_at || 0).getTime();
                const timeB = new Date(b.created_at || 0).getTime();
                return timeA - timeB;
            });
            children.forEach(child => {
                child._isOrphanedSlave = true; // signal for DOM insertion
                sorted.push(child);
            });
        }
    });

    return sorted;
}

/**
 * Update the genie parent badge visibility after sessions are rendered.
 * Call this after the session list is populated to add parent badges to sessions
 * that weren't initially marked but have child sessions in the DOM.
 */
export function updateGenieMasterBadges() {
    // Find all slave sessions and their parent IDs
    const slaveElements = document.querySelectorAll('.genie-slave-session');
    const parentIds = new Set();

    slaveElements.forEach(el => {
        const parentId = el.dataset.genieParentId;
        if (parentId) {
            parentIds.add(parentId);
        }
    });

    // For each parent that doesn't have a parent badge, add one
    parentIds.forEach(parentId => {
        const parentItem = document.getElementById(`session-${parentId}`);
        if (parentItem && !parentItem.querySelector('.genie-master-badge')) {
            parentItem.classList.add('genie-master-session');
            parentItem.dataset.isGenieMaster = 'true';

            // Find the name container and add the badge with world-class styling
            const nameContainer = parentItem.querySelector('.flex-1.min-w-0');
            if (nameContainer) {
                const collapseState = getGenieCollapseState();
                const isCollapsed = collapseState[parentId] || false;

                // Create level badge matching child style
                const levelBadge = document.createElement('span');
                levelBadge.className = 'level-badge level-badge-l0';
                levelBadge.textContent = 'L0';
                levelBadge.title = 'Level 0 parent session';

                // Container for badge + collapse toggle
                const genieMasterBadge = document.createElement('span');
                genieMasterBadge.className = 'genie-master-badge inline-flex items-center gap-0.5 mt-1 text-xs cursor-pointer';
                genieMasterBadge.appendChild(levelBadge);

                // Toggle
                const toggle = document.createElement('span');
                toggle.style.cssText = `
                    display: inline-flex;
                    align-items: center;
                    justify-content: center;
                    width: 18px;
                    height: 18px;
                    color: #F15F22;
                    background: rgba(241, 95, 34, 0.15);
                    border-radius: 4px;
                    border: 1px solid rgba(241, 95, 34, 0.3);
                    transition: all 0.2s ease;
                    font-size: 10px;
                    transform: ${isCollapsed ? 'rotate(-90deg)' : 'rotate(0deg)'};
                `;
                toggle.textContent = '▼';
                genieMasterBadge.appendChild(toggle);

                genieMasterBadge.title = 'Click to collapse/expand child sessions';

                // Hover effects
                genieMasterBadge.addEventListener('mouseenter', () => {
                    genieMasterBadge.style.background = 'linear-gradient(135deg, rgba(241, 95, 34, 0.3), rgba(255, 140, 0, 0.15))';
                    genieMasterBadge.style.borderColor = 'rgba(241, 95, 34, 0.6)';
                    genieMasterBadge.style.boxShadow = '0 3px 8px rgba(241, 95, 34, 0.2)';
                });
                genieMasterBadge.addEventListener('mouseleave', () => {
                    genieMasterBadge.style.background = 'linear-gradient(135deg, rgba(241, 95, 34, 0.2), rgba(255, 140, 0, 0.1))';
                    genieMasterBadge.style.borderColor = 'rgba(241, 95, 34, 0.4)';
                    genieMasterBadge.style.boxShadow = '0 2px 4px rgba(241, 95, 34, 0.1)';
                });

                genieMasterBadge.addEventListener('click', (e) => {
                    e.stopPropagation();
                    toggleGenieSlaveVisibility(parentId);
                    // Animate toggle
                    const currentCollapsed = getGenieCollapseState()[parentId] || false;
                    toggle.style.transform = currentCollapsed ? 'rotate(-90deg)' : 'rotate(0deg)';
                });

                nameContainer.appendChild(genieMasterBadge);
            }
        }
    });

    // Apply collapse state to slave sessions
    const collapseState = getGenieCollapseState();
    slaveElements.forEach(el => {
        const parentId = el.dataset.genieParentId;
        if (parentId && collapseState[parentId]) {
            el.classList.add('genie-slave-hidden');
        }
    });
}

/**
 * Renders the historical plan and execution trace in the status window.
 * @param {Array<object>} originalPlan - The original plan array generated for the turn.
 * @param {Array<object>} executionTrace - The execution trace array (action/result pairs).
 * @param {string|number} turnId - The ID of the turn being displayed.
 * @param {string} userQuery - The original user query for this turn.
 * @param {Object} knowledgeRetrievalEvent - Optional knowledge retrieval event data.
 * @param {Object} turnTokens - Optional turn token data { turn_input_tokens, turn_output_tokens }.
 * @param {Array<object>} systemEvents - Optional system events (session name generation, etc.).
 * @param {number} durationMs - Optional execution duration in milliseconds (for tool_enabled profiles).
 */
function _addDefensivePhaseFooter(container) {
    const pNum = container.dataset.phaseNum;
    const pTotal = container.dataset.totalPhases;
    if (!pNum || !pTotal) return;
    const pDepth = parseInt(container.dataset.phaseDepth || '0', 10);
    const depthPrefix = pDepth > 0 ? '↳ '.repeat(pDepth) : '';
    const phaseFooter = document.createElement('div');
    phaseFooter.className = 'status-phase-header phase-end';
    phaseFooter.innerHTML = `<span class="font-bold">${depthPrefix}Phase ${pNum}/${pTotal} Completed</span>`;
    container.appendChild(phaseFooter);
}

// --- Flow run trace (reload view for a cohesive flow-run turn) -------------

function _flowStatusColor(status) {
    return {
        complete: 'var(--teradata-orange, #F15F22)',
        success: 'var(--teradata-orange, #F15F22)',
        error: '#ef4444',
        cancelled: 'var(--text-muted, #94a3b8)',
        skipped: 'var(--text-muted, #94a3b8)',
    }[status] || 'var(--text-muted, #94a3b8)';
}

function _flowEsc(s) {
    const d = document.createElement('div');
    d.textContent = (s == null ? '' : String(s));
    return d.innerHTML;
}


// ── Session primer — folded presentation ─────────────────────────────────────
// `session_primer.collapsed` on the profile the primer RAN under is the ONE rule
// (set on the profile editor's Session Primer card). Presentation only: the turn is
// persisted and billed exactly as before, and the two author lines — "You · Primer",
// "Assistant · Primer" — stay as they are; what folds is the body beneath them.
export function primerCollapsedFor(profileTag) {
    try {
        const profiles = (typeof window !== 'undefined' && window.configState?.profiles) || [];
        const tag = String(profileTag || '').replace(/^@/, '').toUpperCase();
        const p = tag ? profiles.find(pr => String(pr.tag || '').toUpperCase() === tag) : null;
        const sp = p && p.session_primer;
        return !!(sp && typeof sp === 'object' && sp.collapsed === true);
    } catch (e) {
        return false;
    }
}

// The assistant's primer answer belongs to the user primer directly above it, so it
// inherits that bubble's decision rather than resolving a tag it never carried —
// the pair can never disagree, live or on reload.
function _inheritPrimerCollapsed() {
    const log = DOM.chatLog || (typeof document !== 'undefined' && document.getElementById('chat-log'));
    if (!log || !log.querySelectorAll) return false;
    const bubbles = log.querySelectorAll('.message-bubble[data-primer="user"]');
    const last = bubbles[bubbles.length - 1];
    return !!(last && last.dataset.primerCollapsed === '1');
}

const _PRIMER_CHEVRON = '<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 6l4 4 4-4"/></svg>';

function _setPrimerFolded(bubble, folded) {
    bubble.classList.toggle('message--primer-collapsed', folded);
    const btn = bubble.querySelector('.primer-toggle');
    if (!btn) return;
    btn.setAttribute('aria-expanded', folded ? 'false' : 'true');
    btn.dataset.tip = folded ? 'Show the primer' : 'Hide the primer';
}

// One document-level listener: the bubble is static DOM that survives the session
// UI cache (an innerHTML snapshot), so a per-element listener would be lost on restore.
if (typeof document !== 'undefined' && !window.__primerToggleBound) {
    window.__primerToggleBound = true;
    document.addEventListener('click', (e) => {
        const btn = e.target.closest && e.target.closest('.primer-toggle');
        if (!btn) return;
        e.preventDefault();
        const bubble = btn.closest('.message--primer');
        if (bubble) _setPrimerFolded(bubble, !bubble.classList.contains('message--primer-collapsed'));
    });
}

// Tint a static component card in the chat log with the TARGET component's accent
// (window.__componentAccents — the single manifest-sourced map, same source the flow
// inline card and split panels use). Never hardcode the colour; the card's button
// reads var(--sp-accent), so setting it on the card propagates to the button. Each
// card class maps to the component it opens (the Flow Run card opens the flow → flow
// accent). Extensible: add new static cards here.
const _CARD_TARGET_COMPONENT = { 'flow-run-card': 'flow' };
function _tintComponentCards(root) {
    const accents = (typeof window !== 'undefined' && window.__componentAccents) || {};
    for (const [cls, comp] of Object.entries(_CARD_TARGET_COMPONENT)) {
        const accent = accents[comp];
        if (!accent) continue;
        if (root && root.classList && root.classList.contains(cls)) {
            root.style.setProperty('--sp-accent', accent);
        }
        const scope = (root && root.querySelectorAll) ? root : document;
        scope.querySelectorAll('.' + cls).forEach((el) => el.style.setProperty('--sp-accent', accent));
    }
    // A flow-run card persisted before the shared open control existed carries its
    // own button (.flow-run-open-btn); give it the shared one, which reads the live
    // panel state and is handled by the one delegated click (panelOpenControl.js).
    _upgradeLegacyOpenButtons(root);
}

// Replacing a node inside the observed chat log re-fires the observer, but the
// replacement no longer matches .flow-run-open-btn, so the second pass is a no-op.
function _upgradeLegacyOpenButtons(root) {
    const poc = (typeof window !== 'undefined') && window.PanelOpenControl;
    if (!poc) return;
    const scope = (root && root.querySelectorAll) ? root : document;
    const legacy = [];
    if (root && root.matches && root.matches('.flow-run-open-btn')) legacy.push(root);
    scope.querySelectorAll('.flow-run-open-btn').forEach((b) => legacy.push(b));
    for (const btn of legacy) {
        const card = btn.closest('.flow-run-card');
        const nameEl = card && card.querySelector('div > div:nth-child(2)');
        const holder = document.createElement('span');
        holder.innerHTML = poc.html({ component: 'flow', id: btn.dataset.flowId || '*',
                                      panelId: 'flow-split-panel', row: false,
                                      name: nameEl ? nameEl.textContent.trim() : 'Flow' });
        const ctl = holder.firstElementChild;
        if (ctl) btn.replaceWith(ctl);
    }
}
if (typeof document !== 'undefined' && !window.__compCardAccentBound) {
    window.__compCardAccentBound = true;
    const chatLog = document.getElementById('chat-log');
    if (chatLog) {
        new MutationObserver((muts) => {
            for (const m of muts) for (const n of m.addedNodes) {
                if (n.nodeType === 1) _tintComponentCards(n);
            }
        }).observe(chatLog, { childList: true, subtree: true });
    }
    // Initial sweeps — the accent map is populated by an async fetch at app init,
    // so re-apply shortly after in case cards rendered before it landed.
    const sweep = () => _tintComponentCards(document);
    sweep();
    setTimeout(sweep, 1200);
    setTimeout(sweep, 3000);
}

/**
 * Render a finished flow run's node-by-node trace into the Live Status panel.
 * Used by the flow branch of handleReloadPlanClick. Display-only; the run's
 * authoritative totals come from the flow_runs ledger (passed in `run`).
 */
export function renderFlowRunTrace({ container, run, nodeRuns = [], flowId, turnId, runMissing = false }) {
    if (!container) return;
    container.innerHTML = '';

    const status = (run && run.status) || 'complete';
    const tin = (run && run.tokens_input) || 0;
    const tout = (run && run.tokens_output) || 0;
    const cost = Number((run && run.cost_usd) || 0);

    const header = document.createElement('div');
    header.className = 'status-step';
    header.style.cssText = 'margin:8px;';
    header.innerHTML = `
        <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;">
          <div style="display:flex;align-items:center;gap:8px;">
            <span style="font-weight:600;color:var(--text-primary,#e2e8f0);">Flow Run</span>
            <span style="font-size:0.72rem;text-transform:uppercase;color:${_flowStatusColor(status)};">${_flowEsc(status)}</span>
          </div>
          ${flowId && !runMissing && window.PanelOpenControl
              ? window.PanelOpenControl.html({ component: 'flow', id: flowId, panelId: 'flow-split-panel', name: 'Flow', row: false }) : ''}
        </div>
        <div style="color:var(--text-muted,#94a3b8);font-size:0.8rem;margin-top:6px;">
          ${(tin + tout).toLocaleString()} tokens &middot; $${cost.toFixed(4)} &middot; ${nodeRuns.length} node${nodeRuns.length === 1 ? '' : 's'}
        </div>
        ${runMissing ? `<div style="color:var(--text-muted,#94a3b8);font-size:0.78rem;margin-top:6px;">This run is no longer stored — its flow may have been deleted. Showing the summary saved with the turn.</div>` : ''}`;
    container.appendChild(header);

    if (!nodeRuns.length) {
        const empty = document.createElement('div');
        empty.className = 'status-step';
        empty.style.cssText = 'margin:8px;color:var(--text-muted,#94a3b8);font-size:0.8rem;';
        empty.textContent = 'No node executions recorded for this run.';
        container.appendChild(empty);
        return;
    }

    nodeRuns.forEach((n) => {
        const nodeStatus = n.status || 'success';
        const card = document.createElement('div');
        card.className = 'status-step' + (nodeStatus === 'error' ? ' error' : '');
        card.style.cssText = 'margin:8px;';
        const ntin = n.tokens_input || 0;
        const ntout = n.tokens_output || 0;
        const meta = [_flowEsc(n.node_type || ''),
                      (ntin || ntout) ? `${(ntin + ntout).toLocaleString()} tokens` : '']
                     .filter(Boolean).join(' &middot; ');
        card.innerHTML = `
            <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;">
              <span style="font-weight:600;color:var(--text-primary,#e2e8f0);">${_flowEsc(n.node_id || 'node')}</span>
              <span style="font-size:0.7rem;text-transform:uppercase;color:${_flowStatusColor(nodeStatus)};">${_flowEsc(nodeStatus)}</span>
            </div>
            ${meta ? `<div style="color:var(--text-muted,#94a3b8);font-size:0.75rem;margin-top:4px;">${meta}</div>` : ''}
            ${n.error_msg ? `<div style="color:#ef4444;font-size:0.75rem;margin-top:4px;">${_flowEsc(n.error_msg)}</div>` : ''}`;
        container.appendChild(card);
    });
}

/**
 * WS3 — Trajectory verdict overlay (the Sessions ↔ Agents bridge).
 *
 * Prepends a compact "Quality" strip to the historical-trace view that scores the
 * viewed turn against its profile-class scorers (the same verdicts the Agent
 * Performance lens aggregates) and overlays them on the trajectory. Deterministic
 * by default (0 tokens); an opt-in checkbox adds the live LLM-judge panel.
 * Self-contained and defensive — never disturbs the trace it sits above.
 */
export function injectTrajectoryEvalOverlay(sessionId, turnId, turnData, prefetched) {
    try {
        if (!turnData || !sessionId || turnId == null) return;
        // Flow runs are graded in their own lens (phase 2); error/cancelled turns
        // have no trajectory to grade.
        if (turnData.profile_type === 'flow' || turnData.flow_run_id) return;
        if (turnData.status === 'cancelled' || turnData.status === 'error') return;
        const host = DOM.statusWindowContent;
        if (!host || host.querySelector('#trajectory-eval-overlay')) return;

        const wrap = document.createElement('div');
        wrap.id = 'trajectory-eval-overlay';
        // If the assurance band already exists, join it — the two are one
        // statement about the turn, and which one rendered first is an
        // implementation detail that must not decide the layout.
        const _band = host.querySelector('#assurance-band');
        wrap.className = 'mx-4 mt-3 mb-1 rounded-lg';
        wrap.style.cssText = 'background: var(--card-bg); border: 1px solid var(--border-primary);';
        wrap.innerHTML = `
            <div class="flex items-center justify-between gap-2 px-3 py-2 flex-wrap">
                <div class="flex items-center gap-2 flex-1 min-w-0">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 flex-shrink-0" style="color: var(--teradata-orange);" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                    <span class="text-xs font-semibold flex-shrink-0" style="color: var(--text-primary);">Scorecard</span>
                    <span class="text-[10px] truncate min-w-0 u-tip u-tip-left" style="color: var(--text-muted);" data-tip="scored against ${_ifocLabel(turnData.profile_type)} scorers">${_ifocLabel(turnData.profile_type)} scorers</span>
                </div>
                <div id="quality-ctl-cluster" class="quality-ctl-cluster flex items-center gap-1 flex-shrink-0">
                    <!-- Small monochrome icons + hover tooltips (the adopted band header
                         has no room for text pills); state changes NEVER write
                         textContent — the icons are the content. -->
                    <button type="button" class="qc-icon-btn u-tip u-tip-left" data-dim-guide
                        aria-label="Open the assurance guide" data-tip="Dimension Guide — what every check means.">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 16v-5"/><path d="M12 8h.01"/></svg>
                    </button>
                    <button id="trajectory-eval-score" type="button" class="qc-icon-btn u-tip u-tip-left"
                        aria-label="Score this answer" data-tip="Score this answer — code checks, no tokens.">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="0.8" fill="currentColor"/></svg>
                    </button>
                </div>
            </div>
            <div id="trajectory-eval-results" class="px-3 pb-2 hidden"></div>`;
        if (_band) _band.appendChild(wrap);
        else host.insertBefore(wrap, host.firstChild);
        // Genie expert (slave) sub-turn: clarify it is scored on demand (not
        // auto-scored) because its quality verdicts stay isolated, while its cost is
        // attributed to (rolled up into) the coordinator turn. Honest UX — the empty
        // "Score this answer" state is intentional, not a missing score.
        if (turnData.is_genie_slave) {
            const slaveNote = document.createElement('div');
            slaveNote.className = 'px-3 pb-2 text-[10px]';
            slaveNote.style.color = 'var(--text-muted)';
            slaveNote.innerHTML = 'Expert sub-turn of a coordinator — scored on demand (its quality is isolated; its cost is rolled up into the coordinator’s turn).';
            wrap.appendChild(slaveNote);
        }

        const btn = wrap.querySelector('#trajectory-eval-score');
        const out = wrap.querySelector('#trajectory-eval-results');
        // ONE scoring button. Every answer is already scored by code automatically after
        // it is delivered, so a second code-only score rarely adds anything; the act worth
        // offering on a scored answer is the AI judges' review. The button's state decides
        // which act it performs, and its icon, tooltip and label say so — a separate judges
        // switch that did nothing until a second button was pressed was the alternative,
        // and it read as two controls for one act.
        //   unscored → "Score this answer"            code checks, no tokens (target icon)
        //   scored   → "Ask the AI judges"            code checks + judges, uses tokens (scales)
        //   judged   → "Ask the AI judges again"      same, for a fresh opinion (scales)
        // Icon-button state machine: NEVER writes textContent (the icon is the content).
        const _ICON = {
            target: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="0.8" fill="currentColor"/></svg>',
            scales: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v18"/><path d="M5 7h14"/><path d="M5 7l-2.5 5a3 3 0 0 0 5 0L5 7z"/><path d="M19 7l-2.5 5a3 3 0 0 0 5 0L19 7z"/></svg>',
        };
        const _SCORE_STATES = {
            unscored: { judges: false, icon: 'target', label: 'Score this answer',
                        tip: 'Score this answer — code checks, no tokens.' },
            scored:   { judges: true,  icon: 'scales', label: 'Ask the AI judges',
                        tip: 'Ask the AI judges to review this answer — uses tokens.' },
            judged:   { judges: true,  icon: 'scales', label: 'Ask the AI judges again',
                        tip: 'Ask the AI judges to review this answer again — uses tokens.' },
        };
        let _scoreMode = 'unscored';
        const _scoreState = (s) => {
            if (s === 'busy') {
                btn.disabled = true;
                btn.setAttribute('data-tip', _SCORE_STATES[_scoreMode].judges ? 'The judges are reviewing…' : 'Scoring…');
                const svg = btn.querySelector('svg');
                if (svg) svg.classList.add('qc-spin');
                return;
            }
            _scoreMode = _SCORE_STATES[s] ? s : 'unscored';
            const st = _SCORE_STATES[_scoreMode];
            btn.disabled = false;
            btn.innerHTML = _ICON[st.icon];
            btn.setAttribute('aria-label', st.label);
            btn.setAttribute('data-tip', st.tip);
            btn.dataset.scoreMode = _scoreMode;
        };
        const _hasJudgeVerdicts = (vs) => (vs || []).some((v) => v && v.kind === 'judge');
        btn.addEventListener('click', async () => {
            const judges = _SCORE_STATES[_scoreMode].judges;
            const priorMode = _scoreMode;
            _scoreState('busy');
            out.classList.remove('hidden');
            out.innerHTML = `<div class="text-xs" style="color: var(--text-muted);">Scoring turn ${turnId}…</div>`;
            try {
                const token = localStorage.getItem('tda_auth_token');
                const resp = await fetch('/api/v1/evals/score-turn', {
                    method: 'POST',
                    headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: sessionId, turn: parseInt(turnId, 10),
                                           include_judges: judges }),
                });
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                const data = await resp.json();
                let note = '';
                if (judges && data.judges_available === false) {
                    note = `<div class="text-[11px] mb-2 px-2 py-1 rounded" style="color:#f59e0b; background:rgba(245,158,11,0.1); border:1px solid rgba(245,158,11,0.25);">No judge model has usable credentials — showing deterministic scores only. Add LLM credentials in Anatomy → LLMs to enable judges.</div>`;
                }
                _paintQualityResults(out, note, data.verdicts || [], wrap, turnData);
                // Judges that could not be reached leave the answer scored, not judged —
                // the button keeps offering the judges, and the note above says why.
                _scoreState(_hasJudgeVerdicts(data.verdicts) ? 'judged' : 'scored');
            } catch (e) {
                out.innerHTML = `<div class="text-xs" style="color: #ef4444;">Scoring failed: ${e.message}</div>`;
                _scoreState(priorMode);
            }
        });

        // Auto-display an EXISTING score for this turn (scored automatically by
        // online eval, or manually before) — read-only, no re-score, no tokens.
        (async () => {
            try {
                let d = prefetched;
                if (!d) {
                    const token = localStorage.getItem('tda_auth_token');
                    const r = await fetch(`/api/v1/evals/turn-score?session_id=${encodeURIComponent(sessionId)}&turn=${parseInt(turnId, 10)}`,
                        { headers: { 'Authorization': `Bearer ${token}` } });
                    if (!r.ok) return;
                    d = await r.json();
                    if (!d.scored) return;
                }
                const verdicts = d.verdicts || [];
                if (!verdicts.length) return;
                out.classList.remove('hidden');
                _paintQualityResults(out, _qualityScoreNote(d, turnData), verdicts, wrap, turnData);
                _scoreState((d.has_judges || _hasJudgeVerdicts(verdicts)) ? 'judged' : 'scored');
            } catch (_) { /* no existing score — the button stays "Score this answer" */ }
        })();

        // R-6 — the scoring controls (Guide · the scoring button) belong on the ASSURANCE
        // band header: scoring is a turn-level act, so parking it on the Quality card
        // claimed a station scope it does not have. Adoption runs from BOTH sides (whichever renders second wins —
        // the dial's render() does the same after its innerHTML wipe); the nodes move
        // WITH their wired handlers, IDs unchanged. No dial (legacy strip) ⇒ the
        // controls simply stay on the card.
        //
        // ORDER MATTERS: this must run AFTER the buttons are queried and wired above.
        // It first ran right after the wrap was inserted — and on the LIVE path the
        // band (mounted at stream start) already had a slot, so the cluster moved out
        // of `wrap` BEFORE `wrap.querySelector('#trajectory-eval-score')` ran → null →
        // TypeError → the outer catch swallowed the whole tail: no listeners, no
        // auto-display, an empty unopenable Quality card (reload worked because the
        // band is only injected at the END of this function on that path).
        const _slot = host.querySelector('#assurance-band .ad-ctl-slot');
        const _ctl = wrap.querySelector('#quality-ctl-cluster');
        if (_slot && _ctl) _slot.appendChild(_ctl);

        // The SCORECARD itself is adopted into JUDGED's station panel — same mechanism,
        // one level deeper. JUDGED is now defined as "where measurement is counted", and
        // the scorecard IS the measurements: rendering them once in the panel and again
        // in a card beside it is the duplication this rework exists to remove, and the
        // panel's own flat chip list was the WORSE of the two (same population, no order,
        // no grouping). So the card's rendering wins and moves; the flat list retires.
        //
        // Whichever side renders second performs the move (the dial's render() does the
        // same after its innerHTML wipe). No dial, or JUDGED not pinned ⇒ the results
        // stay on the card, which is the honest fallback rather than a hidden surface.
        _adoptScorecard(host, wrap);

        // Trust Journey strip — the turn's assurance pipeline (inbound guard →
        // authority → scope → tools → outbound guard → grounding → seal), rendered
        // above the Quality card. Guarded: absent script = no-op, trace untouched.
        // R-2 — the dial replaces the strip. Both renderers ship present and each owns
        // its own mount, so the flag is a real revert rather than a code path to unpick.
        if (window.AssuranceDial && window.AssuranceDial.enabled()) {
            window.AssuranceDial.inject(sessionId, turnId, turnData);
        } else if (window.TrustJourney) {
            window.TrustJourney.inject(sessionId, turnId, turnData);
        }
    } catch (e) {
        console.warn('[eval-overlay] inject failed (trace unaffected):', e);
    }
}

/** Place the scorecard through the dial's ONE rule — never a second copy of it here.
 *
 *  The rule is readability, not existence: JUDGED's slot is in the DOM whenever the dial
 *  rendered, but a station section is hidden unless it is pinned. Deciding from
 *  `querySelector` alone posted the scores into a hidden container and left an empty,
 *  unopenable card behind. `AssuranceDial.placeScorecard` owns that decision because the
 *  dial owns the pin.
 *
 *  Absence-tolerant: no dial (the legacy strip path) leaves the results on the card,
 *  which is their home and needs no move. */
function _adoptScorecard(host, wrap) {
    try {
        const D = (typeof window !== 'undefined') && window.AssuranceDial;
        if (D && typeof D.placeScorecard === 'function') D.placeScorecard(host || document);
    } catch (_) { /* results stay on the card — never break the trace */ }
}

function _qualityScoreNote(d, turnData) {
    const esc = (s) => String(s == null ? '' : s);
    const parts = [];
    // SCOPE — which scorer suite was applicable. Dropped first if the line ever runs
    // long: it is recoverable from the checks themselves (the moments and their counts
    // ARE the suite), whereas nothing below is recoverable from anything on screen.
    if (turnData && turnData.profile_type) parts.push(`${_ifocLabel(turnData.profile_type)} scorers`);
    // WHEN — the panel now states that nothing RUNS at JUDGED, which provokes the
    // obvious next question. This is the only place left that answers it: the
    // "graded after" sub-head it used to ride on is replaced by the moment groups.
    parts.push('taken after the answer shipped');
    // HOW it was triggered.
    parts.push(d.source === 'manual' ? 'scored manually' : 'scored automatically');
    // WHETHER JUDGES RAN — NAMED, never implied. "deterministic" said "no judges" by
    // omission; an absence must be stated, and this is the one an operator asks about
    // because judges are the part that costs money.
    if (d.judged) {
        const i = Number(d.input_tokens || 0).toLocaleString();
        const o = Number(d.output_tokens || 0).toLocaleString();
        parts.push((d.input_tokens || d.output_tokens)
            ? `judges ran (${i} in / ${o} out)` : 'judges ran');
    } else {
        parts.push('no judges ran');
    }
    // COST — LABELLED. `Cost ·metric $0.0007` (the TURN's cost) sits a few groups below
    // in the same panel; two costs on one surface with one of them unlabelled is a
    // misreading waiting to happen, and a figure must carry what it is OF.
    parts.push(`scoring cost $${Number(d.cost_usd || 0).toFixed(6)}`);
    return `<div class="text-[10px] mb-1.5" style="color: var(--text-muted);">${
        esc(parts.join(' · '))}</div>`;
}

function _ifocLabel(t) {
    return ({ llm_only: 'Ideate', rag_focused: 'Focus', tool_enabled: 'Optimize',
              genie: 'Coordinate', conversation_with_tools: 'Ideate+Tools' })[t] || 'agent';
}

// Format a metric score for its dimension's natural unit so a count shows "1"
// (not "1.00"), a rate shows "0%", cost shows "$…", latency shows time, etc.
const _PCT_METRICS = new Set(['tool_success_rate', 'numeric_fidelity', 'grounded_overlap', 'plan_execution']);
const _COUNT_METRICS = new Set(['self_correction', 'context_health', 'no_tool_loops', 'error_recovery', 'steps', 'tasks', 'tool_calls', 'scope_adherence']);
function _fmtMetricScore(dim, score) {
    const n = Number(score);
    if (!isFinite(n)) return '';
    if (dim === 'cost') return '$' + n.toFixed(4);
    if (dim === 'tokens') return n.toLocaleString();
    if (dim === 'latency') return n >= 1000 ? (n / 1000).toFixed(1) + 's' : Math.round(n) + 'ms';
    if (dim === 'parallel_efficiency') return n.toFixed(2) + '×';  // speedup, e.g. 1.50×
    if (_PCT_METRICS.has(dim)) return Math.round(n * 100) + '%';
    if (_COUNT_METRICS.has(dim)) return String(Math.round(n));
    return Number.isInteger(n) ? String(n) : n.toFixed(2);
}

// THE PARTITION is the card's grouping — one grammar for the whole Live Status
// panel: measurements group by the STAGE OF THE TURN they measure, in the order the
// turn ran (eval/stages.py, served through the assurance vocabulary).
//
// Two taxonomies were retired on this surface, for the same underlying reason — each
// grouped by something other than WHAT IS MEASURED:
//   * the KPI tiers (Core/Resource/Knowledge/Quality/…) filed partly by mechanism — a
//     group literally called "Quality" meant "graded by an LLM panel";
//   * the station partition filed by OWNERSHIP, which the lane cut ended: nothing owns
//     a measurement now, every scored dimension is counted at JUDGED, and station names
//     on this surface kept implying an ownership the code no longer has.
// A process model has a canonical ORDER by construction, so this panel reads in the
// sequence the turn actually ran rather than in a hand-picked list order.
/** Group verdicts by the STAGE they measure, via QualityDimensions.groupByStage — the
 *  served model, so this file declares no taxonomy of its own. Pure over its inputs so
 *  the gate can execute it.
 *
 *  Absence-tolerant in the honest direction: without QualityDimensions, or before the
 *  vocabulary lands, EVERYTHING falls into one unstaged group — a flat list, never a
 *  resurrected parallel taxonomy and never a confident wrong grouping. `_paintQualityResults`
 *  repaints once the model arrives (the same readiness pattern the station grouping used).
 */
function _stageGroupsForCard(verdicts, qd) {
    const vs = verdicts || [];
    const byKey = new Map(vs.map((v) => [v.dimension, v]));
    let groups = [];
    try {
        if (qd && typeof qd.groupByStage === 'function' && qd.stagesReady && qd.stagesReady()) {
            groups = qd.groupByStage(vs.map((v) => v.dimension));
        }
    } catch (_) { groups = []; }
    const placed = new Set(groups.flatMap((g) => g.keys));
    const loose = vs.filter((v) => !placed.has(v.dimension));
    const out = groups.map((g) => ({
        stage: g.stage, label: g.label, desc: g.desc,
        verdicts: g.keys.map((k) => byKey.get(k)).filter(Boolean),
    }));
    // Unstaged checks are SHOWN, under no heading — a check the model cannot place is a
    // registry gap the backend gate fails on, and hiding it here would make the surface
    // quietly disagree with the count above it.
    if (loose.length) out.push({ stage: '', label: '', desc: '', verdicts: loose });
    return out;
}
// Dimensions whose `evidence` array is a composition breakdown rendered inline on
// the chip (muted): tasks (work mix) + genie cost/tokens (coordinator-vs-experts split).
const _INLINE_EVIDENCE = new Set(['tasks', 'cost', 'tokens']);

// Shared quality-dimension presentation (logical labels + styled tooltip + Dimension
// Guide glossary) lives in qualityDimensions.js — single source of truth shared with the
// Agent Roster. Internal scorer keys (e.g. "tool_success_rate") never reach the consumer.
const _QD = () => window.QualityDimensions || {};
const _dimLabel = (k) => (_QD().label ? _QD().label(k) : String(k == null ? '' : k));
// [data-dim] attributes: hover → styled tooltip, click → the guide at that dimension.
// `note` carries the per-verdict runtime explanation (e.g. "turn completed without error").
function _dimAttrs(k, note) {
    const e = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
        (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    return `data-dim="${e(k)}"${note ? ` data-dim-note="${e(note)}"` : ''} tabindex="0"`;
}
// Scope the Assurance Guide to this turn: stamp the scored verdicts (+ their live band)
// as context on the overlay, so opening the guide shows exactly the dimensions used and
// their Healthy/Review/Danger/Not-applicable status. The band mirrors the chip bands.
function _setQualityGuideContext(wrap, verdicts, turnData) {
    try {
        const QD = _QD();
        if (!QD.setContext || !wrap) return;
        const items = (verdicts || []).map((v) => ({
            key: v.dimension,
            band: v.band || (v.passed === false ? 'danger' : v.passed === true ? 'healthy' : 'na'),
            score: v.score,
            threshold: v.threshold,
            explanation: v.explanation,
        }));
        const label = `${_ifocLabel(turnData && turnData.profile_type)} · this turn`;
        QD.setContext(wrap, items, label, turnData && turnData.profile_type);
        // The Guide button may have been ADOPTED onto the assurance band header (R-6) —
        // outside `wrap`, so `closest('[data-dim-context]')` would miss the turn scope
        // and open the whole catalogue. The context travels with the cluster.
        const cluster = document.getElementById('quality-ctl-cluster');
        if (cluster && !wrap.contains(cluster)) {
            QD.setContext(cluster, items, label, turnData && turnData.profile_type);
        }
    } catch (_) { /* guide context is best-effort — never break the trace */ }
}

// Vocabulary readiness for the station grouping: `stationOf` needs the mechanism
// links the assurance vocabulary serves. Warmed at overlay injection; if a card
// rendered before the links landed (every dim would sit in the JUDGED residue),
// repaint when they arrive — the accepted fetch-and-repaint transient the Guide
// itself uses.
//
// Readiness is asked of the OWNER (`QD.stationsReady()`), never latched here. A local
// boolean was a second latch over one fact and recorded "we called it", not "it
// worked": `ensureVocabulary` resolves even when the fetch 401s or returns nothing,
// so the first card set the flag, repainted over an empty vocabulary, and every card
// after it skipped the warm entirely — the JUDGED-residue card, permanently. Two
// latches for one fact is the defect; deleting this one is the fix.
function _paintQualityResults(out, prefixHtml, verdicts, wrap, turnData) {
    const paint = () => {
        out.innerHTML = _renderVerdictChips(verdicts, prefixHtml);
        _setQualityGuideContext(wrap, verdicts, turnData);
    };
    paint();
    const QD = _QD();
    // Already partitioned → nothing to wait for. Otherwise warm and repaint, but only
    // if the warm actually delivered a partition: repainting the same residue twice
    // just spends a frame to redraw the wrong answer.
    // Readiness is asked of the STAGE model now, because that is what this card groups
    // by. Asking `stationsReady()` would wait on a different fact and could return early
    // while the grouping the card actually needs had not landed.
    const staged = () => !QD.stagesReady || QD.stagesReady();
    if (!QD.ensureVocabulary || staged()) return;
    QD.ensureVocabulary().then(() => {
        // `isConnected` — the trace can rebuild while the fetch is open.
        if (out.isConnected && staged()) paint();
    }).catch(() => { /* grouping degrades to a flat list — never breaks the card */ });
}

/** @param noteHtml provenance, rendered INSIDE the collapsed content.
 *
 *  It used to be prepended by the caller, so the qualifier printed ABOVE the anchor it
 *  qualifies. Invisible while the scorecard had its own title bar; wrong the moment the
 *  headline became the first thing in JUDGED's panel. Anchor first, qualifier after —
 *  and collapsed, the headline IS the answer, so the provenance travels with the detail
 *  rather than doubling the height of a one-line summary. */
function _renderVerdictChips(verdicts, noteHtml) {
    if (!verdicts.length) return `<div class="text-xs" style="color: var(--text-muted);">No applicable scorers for this turn.</div>`;
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    // Defensive dedup by dimension+kind (prefer the decided verdict over an abstention).
    const seen = new Map();
    verdicts.forEach((v) => {
        const k = `${v.dimension}|${v.kind}`;
        const prev = seen.get(k);
        if (!prev || (prev.passed == null && v.passed != null)) seen.set(k, v);
    });
    verdicts = [...seen.values()];
    // 4-class band drives the chip color when a banded metric set it; otherwise
    // fall back to pass/fail/abstain. Review (amber) = passed-but-flagged.
    const bandOf = (v) => v.band || (v.passed === false ? 'danger' : v.passed === true ? 'healthy' : null);
    // ONE colour vocabulary (PROP-002): the band colours are the trust tokens —
    // healthy IS pass-green, review IS worth-a-look amber, danger IS danger-red.
    // Hardcoded theme-invariant hexes here gave the Quality card its own private
    // amber beside the ring's (Rule 9.5 violation). TrustGrammar.bandColor is the
    // source; the literal is the absence-tolerant fallback.
    const _bandColor = (window.TrustGrammar && window.TrustGrammar.bandColor)
        || ((b) => ({ healthy: 'var(--trust-pass, #10b981)', review: 'var(--trust-warn, #f59e0b)',
                      danger: 'var(--trust-danger, #ef4444)' })[b] || 'var(--text-muted)');
    // TC-45: monochrome SVGs — the ✓/✕ dingbats render as colour emoji on some
    // platforms, and the review mark was a literal "!" text glyph (the one non-SVG
    // icon in the set).
    const BAND_ICON = { healthy: '<svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-0.5px"><path d="M5 13l4 4L19 7"/></svg>', review: '<svg width="8" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" style="vertical-align:-0.5px"><path d="M12 4v10"/><path d="M12 19v.5"/></svg>', danger: '<svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" style="vertical-align:-0.5px"><path d="M6 6l12 12M18 6 6 18"/></svg>' };
    const order = (v) => { const b = bandOf(v); return b === 'danger' ? 0 : b === 'review' ? 1 : b === 'healthy' ? 2 : 3; };
    const chipHtml = (v) => {
        const band = bandOf(v);
        const color = band ? _bandColor(band) : 'var(--text-muted)';
        const neutral = !band;
        const icon = band ? BAND_ICON[band] : '–';
        // Judged-vs-deterministic transparency rides the TEXT tag alone (·judge /
        // ·metric). The old dashed border collided with the grammar's absence
        // family — dashed means na/tentative on every other trust surface, so a
        // dashed GREEN chip read as a contradiction (PROP-002: dashes belong to
        // absence, provenance belongs to the word).
        const kindTag = v.kind === 'judge' ? ' ·judge' : v.kind === 'metric' ? ' ·metric' : '';
        const scoreTxt = (v.score != null && v.kind === 'metric') ? ` ${_fmtMetricScore(v.dimension, v.score)}` : '';
        // Inline transparency split, rendered in muted text on the chips that carry
        // a composition breakdown in their evidence: `tasks` (work mix, e.g.
        // "1 tool · 2 reasoning") and — for genie coordinator turns — `cost`/`tokens`
        // (the rolled-up coordinator-vs-experts split, e.g. "coordinator $0.00 · 1 expert $0.0013").
        const breakdownTxt = (_INLINE_EVIDENCE.has(v.dimension) && Array.isArray(v.evidence) && v.evidence.length)
            ? `<span style="opacity:0.7; font-weight:400;"> · ${esc(v.evidence.join(' · '))}</span>` : '';
        return `<span class="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full" ${_dimAttrs(v.dimension, v.explanation)}
            style="color:${color}; background:${neutral ? 'rgba(127,127,127,0.12)' : `color-mix(in srgb, ${color} 10%, transparent)`}; border:1px solid ${neutral ? 'var(--border-secondary)' : `color-mix(in srgb, ${color} 33%, transparent)`};">
            <span style="font-weight:700;">${icon}</span>${esc(_dimLabel(v.dimension))}${esc(kindTag)}${esc(scoreTxt)}${breakdownTxt}</span>`;
    };
    // Group chips by the STAGE of the turn each check measures, in TURN ORDER — so the
    // card reads as the turn ran: what it planned, retrieved, did, composed, delivered,
    // and what the whole run cost. The header carries the stage's own one-line
    // description as its tooltip, so a reader never has to guess what a group means.
    const _qd = (typeof window !== 'undefined' && window.QualityDimensions) || null;
    const groups = _stageGroupsForCard(verdicts, _qd);
    // ── ONE shelf for every station — JUDGED included ─────────────────────────
    // The other stations shelve their members by class as fold lines (upheld by ·
    // controls · 6 · all quiet), folded by STATE. JUDGED has no members: its population
    // is the measures, and the one placement that varies between them is the MOMENT of
    // the turn they grade. So its shelf is one fold line per moment — taken at ·
    // Compose · 14 · 1 failing — open where a measure failed or is flagged, folded
    // where every measure is quiet. The chips keep their routes inside the fold.
    // A failure CONVICTS the turn only if its dimension is gate-eligible; an advisory
    // one (a coarse pre-signal, an uncalibrated judge) informs without convicting. The
    // flag is DERIVED server-side from the scorer registry and shipped on every verdict
    // — the card must never re-derive a policy fact, which is how it came to call an
    // advisory `numeric_fidelity` a "gating failure" while the span correctly said 0.
    // Absent flag (a payload older than the serialization) resolves to gating, matching
    // the backend's own unknown→gating rule.
    // Declared BEFORE the moment shelves: `stateOf` calls it for every failing measure, and
    // declared after them it threw "Cannot access 'isGating' before initialization" on any
    // turn with a red measure — the scorecard and JUDGED's members came up empty.
    const isGating = (v) => v.gating !== false;
    const stateOf = (v) => (v.passed === false ? (isGating(v) ? 'danger' : 'warn')
                            : (bandOf(v) === 'review' ? 'warn' : 'pass'));
    const GRANK = { pass: 1, warn: 2, danger: 3 };
    const chips = groups.map((g) => {
        const inner = g.verdicts.slice().sort((a, b) => order(a) - order(b)).map(chipHtml).join(' ');
        const n = g.verdicts.length;
        const states = g.verdicts.map(stateOf);
        const worst = states.reduce((w, st) => (GRANK[st] > GRANK[w] ? st : w), 'pass');
        const nWorst = states.filter((st) => st === worst).length;
        const sum = worst === 'danger' ? `${nWorst} failing`
                  : worst === 'warn' ? `${nWorst} flagged for review` : 'all quiet';
        // A group the model could not place renders its chips with NO heading rather
        // than under an invented one — see _stageGroupsForCard.
        const kind = g.label
            ? `<span class="ad-grp-kind u-tip" data-tip="${esc(g.desc)}"><span data-dim="stage_${esc(g.stage)}" role="button" tabindex="0" style="cursor:pointer; text-decoration:underline dotted; text-underline-offset:2px;">${esc(g.label)}</span> · ${n}</span>`
            : `<span class="ad-grp-kind">${n}</span>`;
        return `<details class="ad-grp"${worst === 'pass' ? '' : ' open'}><summary class="ad-grp-h ad-gate-${worst}"><span class="ad-gate-dot" aria-hidden="true"></span><span class="ad-grp-lead">taken at</span>${kind}<span class="ad-grp-sum">${esc(sum)}</span></summary><div class="flex flex-wrap gap-1.5 pt-1.5 pb-1">${inner}</div></details>`;
    }).join('');
    const gatingFails = verdicts.filter((v) => v.passed === false && isGating(v)).length;
    // NOTHING IS DROPPED: an advisory failure is not a conviction, but it is not silence
    // either — it joins the review-band checks in one "flagged" count, so every verdict
    // that isn't clean is represented in the headline.
    const flagged = verdicts.filter((v) => (v.passed === false && !isGating(v))
                                        || (v.passed !== false && bandOf(v) === 'review')).length;
    const flaggedTxt = flagged
        ? `<span class="text-[11px]" style="color:var(--trust-warn, #f59e0b);">${flagged} flagged for review</span>` : '';
    // Recovered-but-not-clean caveats: non-gating health signals (a tool errored and
    // self-correction recovered it) must keep the headline OUT of green — a turn that
    // recovered from an error is amber (Review), never "all checks passed".
    const byDim = {};
    verdicts.forEach((v) => { byDim[v.dimension] = v; });
    const sc = (byDim.self_correction && byDim.self_correction.score) ? Math.round(byDim.self_correction.score) : 0;
    const tr = byDim.tool_success_rate ? byDim.tool_success_rate.score : null;
    const caveats = [];
    if (tr != null && tr < 1) caveats.push('recovered from a tool error');
    if (sc > 0) caveats.push(`${sc} self-correction${sc === 1 ? '' : 's'}`);
    const summary = gatingFails
        ? `<span class="text-[11px]" style="color:var(--trust-danger, #ef4444);">${gatingFails} gating failure${gatingFails === 1 ? '' : 's'}</span>${flaggedTxt ? ' · ' + flaggedTxt : ''}`
        : (flagged
            ? `<span class="text-[11px]" style="color:var(--trust-warn, #f59e0b);">passing — ${flagged} flagged for review</span>`
            : (caveats.length
                ? `<span class="text-[11px]" style="color:var(--trust-warn, #f59e0b);">passed · ${esc(caveats.join(' · '))}</span>`
                : `<span class="text-[11px]" style="color:var(--trust-pass, #10b981);">all gating measures passed</span>`));
    // The headline is the first thing in JUDGED's panel: the verdict, then the count.
    // The moment shelves follow, each folded by its own state. The provenance (how the
    // turn was scored) is the LAST fold, closed: it qualifies the headline and must sit
    // beneath it, never above it.
    const total = verdicts.length;
    return `<div class="quality-chip-toggle flex items-center gap-1.5 mb-1">${summary}<span class="text-[11px] qc-total" style="color: var(--text-muted);">· ${total} measure${total === 1 ? '' : 's'}</span></div>
    <div class="flex flex-col">${chips}${noteHtml ? `<details class="ad-grp"><summary class="ad-grp-h"><span class="ad-grp-lead">scoring run</span><span class="ad-grp-sum">how this turn was scored</span></summary><div class="pt-1.5">${noteHtml}</div></details>` : ''}</div>`;
}

// The scorecard's ONE renderer, for a dial that shows a turn's measures without holding the
// scorecard element (an answer's capsule; Live Status before the stored score arrives):
// the same headline and the same moment shelves, open only where a measure failed or is flagged.
try { window.UderiaScorecard = Object.assign(window.UderiaScorecard || {}, { renderMeasures: (verdicts) => _renderVerdictChips(verdicts, '') }); } catch (_) { /* no window */ }

export function renderHistoricalTrace(originalPlan = [], executionTrace = [], turnId, userQuery = 'N/A', knowledgeRetrievalEvent = null, kgEnrichmentEvent = null, turnTokens = null, systemEvents = [], durationMs = 0, toolEnabledEvents = [], contextWindowSnapshotHtml = null, strategicSnapshotHtml = null) {
    // Attribution is derived by replaying an ordered list; a reload replays from
    // scratch, so the accumulator must start empty or a previous turn's sections
    // would be attributed to this turn's first LLM step.
    _pdReset();
    DOM.statusWindowContent.innerHTML = ''; // Clear previous content
    state.currentStatusId = 0; // Reset status ID counter for this rendering
    state.isInFastPath = false; // Reset fast path flag
    state.currentPhaseContainerEl = null; // Reset phase container reference
    state.pendingSubtaskPlanningEvents = []; // Clear any pending events

    // Hide knowledge banner by default when loading a turn
    const knowledgeBanner = document.getElementById('knowledge-banner');
    if (knowledgeBanner && !knowledgeRetrievalEvent) {
        knowledgeBanner.classList.add('hidden');
    }

    // --- Separate lifecycle events by depth for correct positioning ---
    // Depth=0 events render as bookends (top/bottom). Depth>0 events render inline within trace.
    const depth0StartEvents = (toolEnabledEvents || []).filter(e => e.type === 'execution_start' && !(e.metadata?.execution_depth > 0));
    const subStartEvents = (toolEnabledEvents || []).filter(e => e.type === 'execution_start' && e.metadata?.execution_depth > 0);
    const subCompleteEvents = (toolEnabledEvents || []).filter(e => e.type === 'execution_complete' && e.metadata?.execution_depth > 0);

    // Render budget allocation context window snapshot at the top (before execution_start)
    if (contextWindowSnapshotHtml) {
        _renderStandardStep({
            step: 'Context Window Assembly',
            details: contextWindowSnapshotHtml,
            type: 'context_window_snapshot',
            _filterCategory: 'context'
        }, DOM.statusWindowContent, true);
    }



    // Render depth=0 execution_start at the top (parent executor bookend)
    for (const startEvent of depth0StartEvents) {
        const stepTitle = window.EventHandlers && typeof window.EventHandlers.getLifecycleTitle === 'function'
            ? window.EventHandlers.getLifecycleTitle('execution_start', startEvent.payload, startEvent.payload?.profile_type || 'tool_enabled')
            : 'Efficiency Focused Started';
        _renderStandardStep({
            step: stepTitle,
            details: startEvent.payload,
            type: 'execution_start',
            metadata: startEvent.metadata,
            _filterCategory: 'system'
        }, DOM.statusWindowContent, true);
    }

    // Render strategic per-call context snapshot (after execution_start, before planning events)
    if (strategicSnapshotHtml) {
        _renderStandardStep({
            step: 'Context Assembly (Strategic)',
            details: strategicSnapshotHtml,
            type: 'context_window_snapshot',
            _filterCategory: 'context'
        }, DOM.statusWindowContent, true);
    }

    // --- PHASE 2: Render knowledge retrieval event FIRST if present ---
    if (knowledgeRetrievalEvent) {
        // Use knowledge_retrieval_complete type to show duration if available
        const eventType = knowledgeRetrievalEvent.duration_ms ? 'knowledge_retrieval_complete' : 'knowledge_retrieval';
        const docCount = knowledgeRetrievalEvent.document_count || 0;
        const duration = knowledgeRetrievalEvent.duration_ms || 0;
        const stepTitle = (eventType === 'knowledge_retrieval_complete'
            ? `Knowledge Retrieved (${docCount} ${docCount === 1 ? 'chunk' : 'chunks'} in ${duration}ms)`
            : `Knowledge Retrieved (${docCount} chunks)`)
            + (knowledgeRetrievalEvent.answered_from_attachments ? ' — answered from the attachment' : '');
        // Render directly via _renderConversationAgentStep to avoid title override.
        // (updateStatusWindow with 'knowledge_retrieval' source would reset the title
        // that handleReloadPlanClick already set to "<Branded Agent> - Turn X".)
        _renderConversationAgentStep({
            step: stepTitle,
            details: knowledgeRetrievalEvent,
            type: eventType,
            _filterCategory: 'knowledge'
        }, DOM.statusWindowContent, false);
    }
    // --- PHASE 2 END ---

    // --- KG ENRICHMENT: Render knowledge graph enrichment event if present ---
    if (kgEnrichmentEvent) {
        _renderConversationAgentStep({
            step: kgEnrichmentTitle(kgEnrichmentEvent),
            details: kgEnrichmentEvent,
            type: 'kg_enrichment',
            _filterCategory: 'knowledge'
        }, DOM.statusWindowContent, true);
    }

    // 2. Iterate through the execution trace with collapsible phase containers
    // Phase container stack mirrors the live execution's phaseContainerStack
    const _phaseContainerStack = [];
    let _prevTraceDepth = 0;
    let _subStartRendered = false;
    let _subCompleteRendered = false;

    // Helper: get the current render target (innermost phase content or root)
    const _getPhaseTarget = () => {
        if (_phaseContainerStack.length > 0) {
            return _phaseContainerStack[_phaseContainerStack.length - 1]
                .querySelector('.status-phase-content') || DOM.statusWindowContent;
        }
        return DOM.statusWindowContent;
    };

    executionTrace.forEach(traceEntry => {
        const _traceMetadata = traceEntry.action?.metadata || {};
        const _currentTraceDepth = _traceMetadata.execution_depth || 0;

        // --- Intercept phase_start: create collapsible container ---
        if (traceEntry.action?.tool_name === 'TDA_SystemLog' && _traceMetadata.type === 'phase_start') {
            const details = traceEntry.action.arguments?.details || {};
            const depth = _traceMetadata.execution_depth || 0;

            // Close any containers at same or deeper depth (handles re-planning)
            while (_phaseContainerStack.length > depth) {
                const old = _phaseContainerStack.pop();
                if (old) {
                    old.classList.add('completed');
                    _addDefensivePhaseFooter(old);
                }
            }

            const phaseContainer = document.createElement('details');
            phaseContainer.className = 'status-phase-container';
            phaseContainer.dataset.filterCategory = 'planning';
            phaseContainer.dataset.phaseNum = details.phase_num || '';
            phaseContainer.dataset.totalPhases = details.total_phases || '';
            phaseContainer.dataset.phaseDepth = depth;
            if (!isFilterCategoryEnabled('planning')) {
                phaseContainer.classList.add('event-filtered-hidden');
            }
            // Collapsed by default — user can expand to inspect details
            phaseContainer.open = false;

            const phaseHeader = document.createElement('summary');
            phaseHeader.className = 'status-phase-header phase-start';
            const depthPrefix = depth > 0 ? '↳ '.repeat(depth) : '';
            phaseHeader.innerHTML = `
                <span class="font-bold flex-shrink-0">${depthPrefix}Phase ${details.phase_num}/${details.total_phases}</span>
                <span class="text-gray-400 text-xs truncate ml-2">${details.goal || ''}</span>
            `;
            phaseContainer.appendChild(phaseHeader);

            const phaseContent = document.createElement('div');
            phaseContent.className = 'status-phase-content';
            phaseContainer.appendChild(phaseContent);

            _getPhaseTarget().appendChild(phaseContainer);
            _phaseContainerStack.push(phaseContainer);

            _prevTraceDepth = _currentTraceDepth;
            return; // Consumed — don't render as a flat step
        }

        // --- Intercept phase_end: close container with footer ---
        if (traceEntry.action?.tool_name === 'TDA_SystemLog' && _traceMetadata.type === 'phase_end') {
            const containerToEnd = _phaseContainerStack.pop();
            if (containerToEnd) {
                const details = traceEntry.action.arguments?.details || {};
                const depth = details.execution_depth ?? (_traceMetadata.execution_depth || 0);
                const depthPrefix = depth > 0 ? '↳ '.repeat(depth) : '';

                const phaseFooter = document.createElement('div');
                phaseFooter.className = 'status-phase-header phase-end';

                if (details.status === 'skipped') {
                    phaseFooter.classList.add('skipped');
                    phaseFooter.innerHTML = `<span class="font-bold">${depthPrefix}Phase ${details.phase_num}/${details.total_phases} Skipped</span>`;
                } else {
                    phaseFooter.innerHTML = `<span class="font-bold">${depthPrefix}Phase ${details.phase_num}/${details.total_phases} Completed</span>`;
                    containerToEnd.classList.add('completed');
                }
                containerToEnd.appendChild(phaseFooter);
            }

            _prevTraceDepth = _currentTraceDepth;
            return; // Consumed — don't render as a flat step
        }

        // Transition depth=0 → depth>0: insert sub-executor's execution_start
        if (_currentTraceDepth > 0 && _prevTraceDepth === 0 && !_subStartRendered) {
            for (const startEvent of subStartEvents) {
                const stepTitle = window.EventHandlers && typeof window.EventHandlers.getLifecycleTitle === 'function'
                    ? window.EventHandlers.getLifecycleTitle('execution_start', startEvent.payload, startEvent.payload?.profile_type || 'tool_enabled')
                    : 'Efficiency Focused Started';
                _renderStandardStep({
                    step: stepTitle,
                    details: startEvent.payload,
                    type: 'execution_start',
                    metadata: startEvent.metadata,
                    _filterCategory: 'system'
                }, _getPhaseTarget(), true);
            }
            _subStartRendered = true;
        }

        // Transition depth>0 → depth=0: insert sub-executor's execution_complete
        if (_currentTraceDepth === 0 && _prevTraceDepth > 0 && !_subCompleteRendered) {
            for (const completeEvent of subCompleteEvents) {
                const stepTitle = window.EventHandlers && typeof window.EventHandlers.getLifecycleTitle === 'function'
                    ? window.EventHandlers.getLifecycleTitle('execution_complete', completeEvent.payload, completeEvent.payload?.profile_type || 'tool_enabled')
                    : 'Execution Complete';
                _renderStandardStep({
                    step: stepTitle,
                    details: completeEvent.payload,
                    type: 'execution_complete',
                    metadata: completeEvent.metadata,
                    _filterCategory: 'system'
                }, _getPhaseTarget(), true);
            }
            _subCompleteRendered = true;
        }

        _prevTraceDepth = _currentTraceDepth;

        let eventData = {};

        if (traceEntry.action && traceEntry.action.tool_name === 'TDA_SystemLog') {
            // Skip context_window_snapshot — handled separately by _renderContextWindowSnapshotForReload()
            // which uses the structured payload for rich visualization (trace only has plain text details)
            const metadata = traceEntry.action.metadata || {};
            if (metadata.type === 'context_window_snapshot') return;

            // This is a system event (plan_generated, system_message, workaround, etc.)
            eventData = {
                step: traceEntry.action.arguments.message,
                details: traceEntry.action.arguments.details,
                type: metadata.type,
                _filterCategory: getFilterCategory('rest', metadata.type),
                metadata: {
                    execution_depth: metadata.execution_depth
                }
            };
        } else if (traceEntry.action && traceEntry.result) {
            // This is a regular tool call, render as two steps
            const metadata = traceEntry.action.metadata || {};

            // Render intent
            const intentEventData = {
                step: `Tool Execution Intent`,
                details: traceEntry.action,
                type: 'tool_intent',
                _filterCategory: 'tool-execution',
                metadata: {
                    execution_depth: metadata.execution_depth
                }
            };
            _renderStandardStep(intentEventData, _getPhaseTarget(), true);

            // Render result
            const resultType = traceEntry.result.status === 'error' ? 'tool_error' : 'tool_result';
            const resultEventData = {
                step: `Tool Execution Result`,
                details: traceEntry.result,
                type: resultType,
                _filterCategory: 'tool-execution',
                metadata: {
                    execution_depth: metadata.execution_depth
                }
            };
            _renderStandardStep(resultEventData, _getPhaseTarget(), true);
            return; // We've handled this entry completely
        } else {
            // Skip unknown trace entry formats
            return;
        }

        // Render system event into current phase container
        _renderStandardStep(eventData, _getPhaseTarget(), true);
    });

    // If trace ended at depth>0, render sub-executor execution_complete after trace
    if (_prevTraceDepth > 0 && !_subCompleteRendered) {
        for (const completeEvent of subCompleteEvents) {
            const stepTitle = window.EventHandlers && typeof window.EventHandlers.getLifecycleTitle === 'function'
                ? window.EventHandlers.getLifecycleTitle('execution_complete', completeEvent.payload, completeEvent.payload?.profile_type || 'tool_enabled')
                : 'Execution Complete';
            _renderStandardStep({
                step: stepTitle,
                details: completeEvent.payload,
                type: 'execution_complete',
                metadata: completeEvent.metadata,
                _filterCategory: 'system'
            }, _getPhaseTarget(), true);
        }
    }

    // Close any remaining open phase containers (edge case: trace ends mid-phase)
    while (_phaseContainerStack.length > 0) {
        const remaining = _phaseContainerStack.pop();
        if (remaining) {
            remaining.classList.add('completed');
            _addDefensivePhaseFooter(remaining);
        }
    }

    // Render system events (session name generation, etc.) after execution trace
    if (systemEvents && systemEvents.length > 0) {
        systemEvents.forEach((event, index) => {
            const isFinal = index === systemEvents.length - 1;
            // Payload now contains the complete event_dict (step, details, type); an event stored
            // bare ({type, step, details} — older post-turn bookings) is read as its own payload
            const eventData = event.payload || { type: event.type, step: event.step, details: event.details || {} };
            // Add type from parent event structure (stored as {type: "...", payload: {...}})
            if (!eventData.type && event.type) {
                eventData.type = event.type;
            }

            // Skip context_window_snapshot — handled separately by _renderContextWindowSnapshotForReload()
            if (eventData.type === 'context_window_snapshot') return;

            // --- SKIP REDUNDANT RAG SYNTHESIS PREVIEW EVENT ---
            // Skip the system_message that fires BEFORE LLM call (no data)
            // The rag_llm_step event will follow with correct token/model data
            if (eventData.type === 'system_message' && eventData.details?.summary === 'Synthesizing answer from retrieved knowledge') {
                console.log('[Event Reload] Skipping redundant system_message preview event');
                return; // Skip this event
            }
            // --- END SKIP ---

            // --- HARMONIZATION: Regenerate title for knowledge events during reload ---
            // Import harmonization functions from eventHandlers.js
            if (window.EventHandlers && eventData.type) {
                let harmonizedTitle = null;

                // Detect profile type from event context (RAG profile uses knowledge events)
                const isRagProfile = eventData.type.includes('knowledge') || eventData.type === 'rag_llm_step' || eventData.type === 'tool_result';

                if (isRagProfile && typeof window.EventHandlers.getRagFocusedTitle === 'function') {
                    harmonizedTitle = window.EventHandlers.getRagFocusedTitle(eventData.type, eventData.details || {});
                } else if (typeof window.EventHandlers.getToolEnabledTitle === 'function') {
                    harmonizedTitle = window.EventHandlers.getToolEnabledTitle(eventData.type, eventData.details || {});
                }

                // Override the stored title with the harmonized one only where the title map
                // KNOWS the type — its fallback is the raw type string, which must never replace
                // a step the producer named ("Proposal Suggested", "Memory Updated", …)
                if (harmonizedTitle && (harmonizedTitle !== eventData.type || !eventData.step)) {
                    eventData.step = harmonizedTitle;
                }
            }
            // --- END HARMONIZATION ---

            // Tag with filter category for event filter chips
            if (!eventData._filterCategory) {
                eventData._filterCategory = getFilterCategory('rest', eventData.type);
            }

            // Use standard renderer for system events
            _renderStandardStep(eventData, DOM.statusWindowContent, isFinal);
        });
    }

    // Finalize the last step
    const finalStepElement = document.getElementById(`status-step-${state.currentStatusId}`);
    if (finalStepElement && finalStepElement.classList.contains('active')) {
        finalStepElement.classList.remove('active');
        if (!finalStepElement.classList.contains('error') && !finalStepElement.classList.contains('cancelled')) {
            finalStepElement.classList.add('completed');
        }
    }

    // Display turn tokens in the token counter if available
    if (turnTokens) {
        updateTokenDisplay({
            statement_input: 0,  // No single statement for historical view
            statement_output: 0,
            turn_input: turnTokens.turn_input_tokens || 0,
            turn_output: turnTokens.turn_output_tokens || 0,
            total_input: 0,  // Session totals not shown for historical turn
            total_output: 0
        });
    }

    // --- Execution Summary Card for tool_enabled profiles ---
    // Use execution_complete from tool_enabled_events if available (includes token counts),
    // otherwise fall back to manually constructed summary card for backwards compatibility.
    // Only depth=0 events render here (sub-executor events already rendered inline in trace).
    const depth0CompleteEvents = (toolEnabledEvents || []).filter(e => e.type === 'execution_complete' && !(e.metadata?.execution_depth > 0));
    if (depth0CompleteEvents.length > 0) {
        for (const completeEvent of depth0CompleteEvents) {
            const stepTitle = window.EventHandlers && typeof window.EventHandlers.getLifecycleTitle === 'function'
                ? window.EventHandlers.getLifecycleTitle('execution_complete', completeEvent.payload, completeEvent.payload?.profile_type || 'tool_enabled')
                : 'Execution Complete';
            _renderStandardStep({
                step: stepTitle,
                details: completeEvent.payload,
                type: 'execution_complete',
                metadata: completeEvent.metadata,
                _filterCategory: 'system'
            }, DOM.statusWindowContent, true);
        }
    } else if (durationMs > 0) {
        // Fallback for old sessions without tool_enabled_events
        const summaryCard = document.createElement('div');
        summaryCard.className = 'status-step p-3 rounded-md mb-2 completed lifecycle';
        summaryCard.dataset.filterCategory = 'system';

        const durationSec = (durationMs / 1000).toFixed(1);
        const phaseCount = (originalPlan || []).length;
        const toolCount = (executionTrace || []).reduce((acc, entry) => {
            if (entry.action && entry.action.tool_name && entry.action.tool_name !== 'TDA_SystemLog') {
                return acc + 1;
            }
            return acc;
        }, 0);
        const inputTokens = turnTokens?.turn_input_tokens || 0;
        const outputTokens = turnTokens?.turn_output_tokens || 0;

        summaryCard.innerHTML = `
            <div class="flex items-center gap-2 mb-2">
                <h4 class="font-bold text-sm text-white">Execution Complete</h4>
            </div>
            <div class="status-details text-xs text-gray-300">
                <div class="status-kv-grid">
                    <div class="status-kv-key">Phases</div>
                    <div class="status-kv-value">${phaseCount} completed</div>
                    <div class="status-kv-key">Tools</div>
                    <div class="status-kv-value">${toolCount} executed</div>
                    <div class="status-kv-key">Tokens</div>
                    <div class="status-kv-value">${inputTokens.toLocaleString()} in / ${outputTokens.toLocaleString()} out</div>
                    <div class="status-kv-key">Duration</div>
                    <div class="status-kv-value">${durationSec}s</div>
                </div>
            </div>
        `;
        DOM.statusWindowContent.appendChild(summaryCard);
    }
    // --- End Execution Summary Card ---

    // Auto-scroll logic
    if (!state.isMouseOverStatus) {
        DOM.statusWindowContent.scrollTop = 0; // Scroll to top for reloaded view
    }
}


/**
 * Show extension result when clicking an extension tag on a user message.
 * For chat_append extensions: scrolls to and highlights the existing output block.
 * For silent/status_panel extensions: fetches turn details and shows an inline popover.
 */
function _showExtensionResult(tagEl, messageBubble) {
    const extName = tagEl.dataset.extName;

    // Toggle off: remove existing popover if already shown
    const existingPopover = messageBubble.querySelector(`.ext-result-popover[data-ext-name="${extName}"]`);
    if (existingPopover) {
        existingPopover.remove();
        return;
    }

    // Look for chat_append output in the adjacent assistant message
    const nextBubble = messageBubble.nextElementSibling;
    if (nextBubble) {
        const chatAppendBlock = nextBubble.querySelector(`.extension-output[data-ext-name="${extName}"]`);
        if (chatAppendBlock) {
            chatAppendBlock.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            chatAppendBlock.classList.add('ext-highlight');
            setTimeout(() => chatAppendBlock.classList.remove('ext-highlight'), 1500);
            return;
        }
    }

    // For silent/status_panel extensions: fetch turn details and show inline popover
    // turnId may be on the user avatar (set retroactively), user/assistant badge, or assistant avatar
    const turnId = messageBubble.querySelector('[data-turn-id]')?.dataset?.turnId
        || nextBubble?.querySelector('[data-turn-id]')?.dataset?.turnId;
    if (!turnId) return;

    fetchTurnDetails(state.currentSessionId, turnId).then(turnData => {
        const result = turnData?.extension_results?.[extName];
        if (!result) return;

        const contentStr = typeof result.content === 'object'
            ? JSON.stringify(result.content, null, 2)
            : String(result.content || result.error || 'No content');
        const escapedContent = contentStr.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

        const popover = document.createElement('div');
        popover.className = 'ext-result-popover';
        popover.dataset.extName = extName;
        popover.innerHTML = `
            <div class="text-xs font-medium text-amber-300 mb-1">!${extName} result
                <span class="text-gray-500 font-normal">${result.content_type || 'json'}</span>
            </div>
            <pre class="text-xs text-gray-400 whitespace-pre-wrap overflow-auto max-h-48" style="font-family: 'JetBrains Mono', monospace;">${escapedContent}</pre>
        `;
        messageBubble.appendChild(popover);
    }).catch(err => {
        console.warn(`[ExtTag] Failed to fetch result for !${extName}:`, err);
    });
}


// Flow self-healing diagnostic prompt — the exact template emitted by
// components/builtin/flow/renderer.js `_handleFixWithAI`. Anchored strictly so it
// only ever matches that machine-generated text, never a real user message.
const _FLOW_ERROR_RE = /^The flow node "(.+?)" \(type: [^)]*\) failed with: ([\s\S]+?)\. Please diagnose and fix it using TDA_Flow\.\s*$/;

function _detectFlowErrorPrompt(content) {
    if (typeof content !== 'string') return null;
    const m = content.match(_FLOW_ERROR_RE);
    return m ? { node: m[1], reason: (m[2] || '').trim() } : null;
}

// Render the flow failure as a calm, structured card (monochrome glyph, theme
// vars) instead of the raw machine prompt. The node chip carries data-flow-node
// as the substrate for cross-panel highlighting (Phase 5 provenance).
function _buildFlowErrorCard({ node, reason }) {
    const card = document.createElement('div');
    card.className = 'flow-error-card';

    const head = document.createElement('div');
    head.className = 'flow-error-head';
    head.innerHTML = `
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z"/>
            <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
        </svg><span>Flow execution failed</span>`;
    card.appendChild(head);

    const body = document.createElement('div');
    body.className = 'flow-error-body';
    const chip = document.createElement('span');
    chip.className = 'flow-error-node-chip';
    chip.dataset.flowNode = node;
    chip.textContent = node;
    chip.title = `Failed node: ${node}`;
    body.appendChild(chip);
    if (reason) {
        const r = document.createElement('span');
        r.className = 'flow-error-reason';
        r.textContent = reason;
        body.appendChild(r);
    }
    card.appendChild(body);

    const foot = document.createElement('div');
    foot.className = 'flow-error-foot';
    foot.textContent = 'Asked the agent to diagnose and fix';
    card.appendChild(foot);
    return card;
}

// ── Vote-button tooltips (docs/roadmap/VOTE_SIGNAL_PLAN.md §11a) ─────────────
// What a vote will actually DO, per engine class. The engine line states only
// SHIPPED, default-on consequences — a tooltip that promises unbuilt behaviour is
// worse than no tooltip, which is why this landed after the mechanisms it describes.
//
// The memory consequence is universal, but it is stated only on `llm_only`, where it
// is the PRIMARY consequence. Elsewhere the engine-specific line is the more
// decision-relevant fact, and two consequence lines per tip is over budget.
const VOTE_TIP = {
    // Fallback lead ONLY — used where the engine has no shipped consequence to name.
    // Never shown beside one: the button already reads "Helpful"/"Unhelpful", so
    // leading with "Mark this answer as helpful" restates the control's own label
    // (Rule 3.0 — no slot restates another) and buries the fact underneath it.
    base_up: 'Mark this answer as helpful.',
    base_down: 'Mark this answer as unhelpful.',
    // The boundary, rendered as the quieter second tier.
    note: 'Recorded beside the quality scores — never changes them.',
    tool_enabled: {
        up: 'Promotes the plan behind this answer, so similar questions reuse it.',
        down: 'Retires the plan behind this answer, so it stops guiding similar questions.',
    },
    rag_focused: {
        up: 'Records these documents as good sources for questions like this.',
        down: 'Flags the documents behind this answer for review in Corpus Health.',
    },
    genie: {
        // The MECHANISM is direction-symmetric (propagation carries either rating),
        // but the wording must not be: two buttons showing identical text is worse
        // than no tooltip — it reads as a bug.
        up: 'Passes your approval through to the expert that produced this answer.',
        down: 'Passes your rejection through to the expert that produced this answer.',
    },
    llm_only: {
        up: '',
        down: 'Stops facts the agent auto-learned from this answer being recalled.',
    },
    default: { up: '', down: '' },
};

/** One vote button's tooltip as `{ lead, note }` — the consequence, and the boundary
 *  on it. Pure. `profileType` unknown ⇒ the lead falls back to the plain act rather
 *  than naming a consequence that would not happen. */
export function composeVoteTip(dir, profileType) {
    const up = dir === 'up';
    const engine = (VOTE_TIP[profileType] || VOTE_TIP.default)[up ? 'up' : 'down'] || '';
    return {
        lead: engine || (up ? VOTE_TIP.base_up : VOTE_TIP.base_down),
        note: VOTE_TIP.note,
    };
}

/** The engine class of the turn currently being appended.
 *
 *  Read from the LAST profile badge in the chat log: at the moment an assistant
 *  message is appended that badge is its own turn's user message (the server-resolved
 *  tag is stamped there during the stream, and history renders user-then-assistant),
 *  so this is an ordered read, not a hunt for an arbitrary badge. Unresolvable ⇒ null,
 *  and the tooltip degrades to the base reading rather than naming a wrong engine.
 */
function _currentTurnEngine() {
    try {
        const badges = DOM.chatLog ? DOM.chatLog.querySelectorAll('.message-profile-badge') : [];
        const last = badges.length ? badges[badges.length - 1] : null;
        if (!last) return null;
        return getProfileTypeForTag((last.textContent || '').trim());
    } catch (e) {
        return null;
    }
}

/** Render a spec-route component into its container; a renderer that hid its container
 *  (every silent branch sets display:none synchronously — an act with nothing to show)
 *  hides the evidence row around it, so an act never reads as an empty row. */
function _renderComponentInRow(componentId, container) {
    try { renderComponent(componentId, container.id, container.dataset.spec); } catch (e) { console.warn('[AnswerSurface] render failed', componentId, e); }
    const row = container.closest('details.ans-ev[data-component]');
    if (row && container.style.display === 'none') row.hidden = true;
}
// A collapsed row renders its component on its FIRST open (`toggle` does not bubble —
// capture reaches it). One listener for every row, live and reloaded alike.
if (typeof document !== 'undefined' && !document.__ansDeferredRender) {
    document.__ansDeferredRender = true;
    document.addEventListener('toggle', (e) => {
        const row = e.target;
        if (!row || row.tagName !== 'DETAILS' || !row.open || !row.classList.contains('ans-ev')) return;
        row.querySelectorAll('[data-component-id][data-ans-deferred]').forEach(c => {
            delete c.dataset.ansDeferred;
            if (c.dataset.componentId && c.dataset.spec && hasRenderer(c.dataset.componentId)) _renderComponentInRow(c.dataset.componentId, c);
        });
    }, true);
}

export function addMessage(role, content, turnId = null, isValid = true, source = null, profileTag = null, isSessionPrimer = false, extensionSpecs = null, skillSpecs = null, origin = null) { // eslint-disable-line no-unused-vars
    // Normalize content - Google Gemini may return content as an array of parts
    if (Array.isArray(content)) {
        content = content.map(part => {
            if (typeof part === 'string') return part;
            if (part && typeof part === 'object') {
                // Handle Google Gemini content parts (text, thinking, etc.)
                return part.text || part.thinking || JSON.stringify(part);
            }
            return String(part);
        }).join(' ');
    } else if (content && typeof content !== 'string') {
        content = String(content);
    }
    content = content || '';

    // Hide welcome screen when adding a message
    if (window.hideWelcomeScreen) {
        window.hideWelcomeScreen();
    }
    
    const wrapper = document.createElement('div');
    wrapper.className = `message-bubble group flex items-start gap-4 ${role === 'user' ? 'justify-end' : ''}`;
    const icon = document.createElement('div');
    icon.className = 'flex-shrink-0 w-10 h-10 rounded-full flex items-center justify-center text-white font-bold shadow-lg';
    icon.textContent = role === 'user' ? 'U' : 'A';
    icon.style.background = role === 'user' ? 'var(--avatar-bg, #4b5563)' : 'var(--teradata-orange, #F15F22)';
    // Add role-specific class for selector compatibility
    icon.classList.add(role === 'user' ? 'user-avatar' : 'assistant-avatar-icon');

    if (role === 'assistant' && turnId) {
        icon.classList.add('assistant-avatar');
        icon.classList.add('clickable-avatar');
        if (state.showTooltips) {
            icon.title = 'Long-press to replay original query';
        }
        let pressTimer;

        const startPress = (e) => {
            e.preventDefault();
            icon.classList.add('long-press');
            pressTimer = setTimeout(() => {
                icon.classList.remove('long-press');
                handleReplayQueryClick({ dataset: { turnId } });
            }, 1500);
        };

        const cancelPress = () => {
            clearTimeout(pressTimer);
            icon.classList.remove('long-press');
        };

        icon.addEventListener('mousedown', startPress);
        icon.addEventListener('mouseup', cancelPress);
        icon.addEventListener('mouseleave', cancelPress);
        icon.addEventListener('touchstart', startPress, { passive: true });
        icon.addEventListener('touchend', cancelPress, { passive: true });
    }

    // This logic is now handled by the badge.

    const messageContainer = document.createElement('div');
    messageContainer.className = 'p-4 rounded-xl shadow-lg max-w-3xl glass-panel relative flex-1 min-w-0';
    // Translucent card surface — same as the component panels' tiles (--card-bg),
    // so the body gradient shows through and the conversation reads as one surface
    // with the other components. Sender is distinguished by avatar + label + side.
    messageContainer.style.background = 'var(--card-bg, rgba(20, 22, 25, 0.6))';

    // Add profile badge for user messages if profileTag is provided
    let profileBadge = null;
    if (role === 'user' && profileTag) {
        profileBadge = document.createElement('div');
        profileBadge.className = 'profile-tag profile-tag--md message-profile-badge flex-shrink-0';
        profileBadge.textContent = `@${profileTag}`;
        profileBadge.title = `Executed with profile: ${profileTag}`;

        // Apply profile-specific colors via CSS custom properties
        if (window.configState?.profiles) {
            const profile = window.configState.profiles.find(p => p.tag === profileTag);
            if (profile && profile.color) {
                // PURE INDUSTRIAL SOLID COLORS
                const adjustBrightness = (hex, percent) => {
                    hex = hex.replace('#', '');
                    const r = parseInt(hex.slice(0, 2), 16);
                    const g = parseInt(hex.slice(2, 4), 16);
                    const b = parseInt(hex.slice(4, 6), 16);
                    const factor = 1 + (percent / 100);
                    const newR = Math.min(255, Math.max(0, Math.round(r * factor)));
                    const newG = Math.min(255, Math.max(0, Math.round(g * factor)));
                    const newB = Math.min(255, Math.max(0, Math.round(b * factor)));
                    const toHex = (n) => n.toString(16).padStart(2, '0');
                    return `#${toHex(newR)}${toHex(newG)}${toHex(newB)}`;
                };

                // PURE INDUSTRIAL: Flat monolithic design
                profileBadge.style.setProperty('--profile-tag-bg', profile.color);
                // Flat monolithic: Border matches background (no gradient depth)
                profileBadge.style.setProperty('--profile-tag-border', profile.color);
                // Pure industrial: Always white text on solid color backgrounds
                profileBadge.style.setProperty('--profile-tag-text', '#FFFFFF');
            }
        }
        profileBadge.style.alignSelf = 'flex-start';
        profileBadge.style.marginRight = '12px';
    }

    const author = document.createElement('p');
    author.className = 'font-bold mb-2 text-sm';
    author.textContent = role === 'user' ? 'You' : 'Assistant';
    author.style.color = role === 'user' ? 'var(--text-secondary, #d1d5db)' : 'var(--teradata-orange, #F15F22)';
    messageContainer.appendChild(author);

    // Provenance tags. `source` = transport (Rest Call / Scheduled); `origin` =
    // orchestration driver (Flow / Genie). They are independent, so a flow turn
    // shows BOTH "Rest Call" and "Flow", and a genie-via-REST turn shows both
    // "Rest Call" and "Genie". All driven by the same server-stamped facts.
    if (role === 'user' && source === 'rest') {
        const restTag = document.createElement('span');
        restTag.className = 'rest-call-tag';
        restTag.textContent = 'Rest Call';
        author.appendChild(restTag);
    }

    if (role === 'user' && source === 'scheduler') {
        const schedTag = document.createElement('span');
        schedTag.className = 'scheduled-task-tag';
        schedTag.textContent = 'Scheduled';
        author.appendChild(schedTag);
    }

    if (role === 'user' && origin === 'flow') {
        const flowTag = document.createElement('span');
        flowTag.className = 'flow-task-tag';
        flowTag.textContent = 'Flow';
        author.appendChild(flowTag);
    }

    if (role === 'user' && origin === 'genie_parent') {
        const genieTag = document.createElement('span');
        genieTag.className = 'genie-parent-tag';
        genieTag.textContent = 'Genie Parent';
        author.appendChild(genieTag);
    }

    if (role === 'user' && origin === 'genie_child') {
        const genieTag = document.createElement('span');
        genieTag.className = 'genie-child-tag';
        genieTag.textContent = 'Genie Child';
        author.appendChild(genieTag);
    }

    // S4 — an answer the SIGNED fallback served wears the exception on its sleeve: a small
    // `served by` chip (marks the exception, never the rule — a turn served by the configured
    // model carries nothing). Read from the turn-keyed map the session load / the live
    // recovery notice filled, exactly as the vote pill reads feedbackByTurn.
    if (role === 'assistant' && turnId != null && state.servedByTurn && state.servedByTurn[turnId]) {
        const sb = state.servedByTurn[turnId];
        const tag = document.createElement('span');
        tag.className = 'served-by-tag u-tip';
        tag.textContent = `served by ${sb.model || sb.provider || 'fallback'}`;
        tag.setAttribute('data-tip', `The configured model could not serve this turn; the profile's signed fallback (${sb.provider || '?'} / ${sb.model || '?'}) served it instead. Cost was billed at the model that served. Details: the "Served by Fallback" step in Live Status.`);
        author.appendChild(tag);
    }

    // Add Session Primer badge for primer messages (both user and assistant)
    if (isSessionPrimer) {
        const primerTag = document.createElement('span');
        primerTag.className = 'primer-tag text-xs px-2 py-0.5 rounded ml-2';

        // Detect light mode for proper contrast
        const isLightMode = document.documentElement.getAttribute('data-theme') === 'light';

        if (isLightMode) {
            // Light mode: use nearly opaque colors with proper !important syntax
            primerTag.style.setProperty('background', 'rgba(6, 182, 212, 0.95)', 'important');
            primerTag.style.setProperty('color', '#000000', 'important');
            primerTag.style.setProperty('border', '1px solid rgba(6, 182, 212, 1.0)', 'important');
            primerTag.style.padding = '0.125rem 0.5rem';
            primerTag.style.borderRadius = '0.25rem';
            primerTag.style.marginLeft = '0.5rem';
            primerTag.style.fontSize = '0.75rem';
            primerTag.style.lineHeight = '1rem';
        } else {
            // Dark mode: use lighter colors
            primerTag.style.background = 'rgba(6, 182, 212, 0.3)';
            primerTag.style.color = 'rgb(103, 232, 249)';
            primerTag.style.border = '1px solid rgba(6, 182, 212, 0.4)';
            primerTag.style.padding = '0.125rem 0.5rem';
            primerTag.style.borderRadius = '0.25rem';
            primerTag.style.marginLeft = '0.5rem';
            primerTag.style.fontSize = '0.75rem';
            primerTag.style.lineHeight = '1rem';
        }

        primerTag.textContent = 'Primer';
        primerTag.title = 'Session initialization message';
        author.appendChild(primerTag);

        // Fold decided ONCE per pair: the user bubble from the profile it ran under,
        // the assistant bubble from the user bubble above it. Marked on the wrapper so
        // the next bubble (and the delegated toggle) can read it back from the DOM.
        const folded = role === 'user' ? primerCollapsedFor(profileTag) : _inheritPrimerCollapsed();
        wrapper.dataset.primer = role;
        wrapper.dataset.primerCollapsed = folded ? '1' : '0';
        messageContainer.classList.add('message--primer');
        author.classList.add('primer-author');
        const toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'primer-toggle u-tip';
        toggle.setAttribute('aria-label', 'Toggle the primer text');
        toggle.innerHTML = _PRIMER_CHEVRON;
        author.appendChild(toggle);
        _setPrimerFolded(messageContainer, folded);
    }

    // Render skill tags for user messages (emerald badges like #sql-expert, #concise)
    if (role === 'user' && skillSpecs && skillSpecs.length > 0) {
        skillSpecs.forEach(spec => {
            const skillTag = document.createElement('span');
            skillTag.className = 'skill-tag';
            skillTag.textContent = `#${spec.name}${spec.param ? ':' + spec.param : ''}`;
            skillTag.title = `Skill: ${spec.name}${spec.param ? ' (' + spec.param + ')' : ''}`;
            author.appendChild(skillTag);
        });
    }

    // Render extension tags for user messages (amber badges like !json, !decision:critical)
    if (role === 'user' && extensionSpecs && extensionSpecs.length > 0) {
        extensionSpecs.forEach(spec => {
            const extTag = document.createElement('span');
            extTag.className = 'extension-tag';
            extTag.textContent = `!${spec.name}${spec.param ? ':' + spec.param : ''}`;
            extTag.title = `Extension: ${spec.name}${spec.param ? ' (' + spec.param + ')' : ''} — click to view result`;
            extTag.dataset.extName = spec.name;
            extTag.dataset.extParam = spec.param || '';

            // Click handler: show extension result
            extTag.addEventListener('click', () => {
                _showExtensionResult(extTag, wrapper);
            });

            author.appendChild(extTag);
        });
    }

    const messageContent = document.createElement('div');
    messageContent.className = 'message-content';
    // A session primer is authored markdown (numbered standing rules, backticked
    // identifiers) and used to land in the transcript as one wall of raw source.
    // Marked ONLY on the primer so the styling below cannot reach any other message.
    if (isSessionPrimer) messageContent.classList.add('message-content--primer');

    // Flow self-healing turns arrive as the flow engine's exact auto-diagnostic
    // prompt. Render a calm, structured error card instead of the raw machine text.
    // Matches consistently on live render and on history reload (same content in).
    const flowError = role === 'user' ? _detectFlowErrorPrompt(content) : null;
    if (flowError) {
        messageContainer.classList.add('message--error');
        messageContent.appendChild(_buildFlowErrorCard(flowError));
        messageContainer.appendChild(messageContent);
    } else {
    // Render markdown for assistant messages if marked.js is available
    // Skip markdown parsing for content that is already HTML (e.g., RAG responses, formatted tool outputs)
    const isAlreadyHtml = content && (
        content.trim().startsWith('<div') ||
        content.trim().startsWith('<p') ||
        content.includes('class="rag-') ||
        content.includes('class="sql-') ||
        content.includes('class="tool-')
    );

    // Primers render as markdown whichever role carries them; every other user
    // message keeps its existing raw path exactly as before.
    if ((role === 'assistant' || isSessionPrimer) && typeof marked !== 'undefined' && !isAlreadyHtml) {
        try {
            messageContent.innerHTML = marked.parse(content);
        } catch (e) {
            console.warn('[UI] Failed to parse markdown, falling back to raw content:', e);
            messageContent.innerHTML = content;
        }
    } else {
        messageContent.innerHTML = content;
    }
    messageContainer.appendChild(messageContent);
    }

    if (role === 'user') {
        if (profileBadge) {
            wrapper.appendChild(profileBadge);
        }
        wrapper.appendChild(messageContainer);
    } else {
        wrapper.appendChild(icon);
    }
    wrapper.appendChild(role === 'user' ? icon : messageContainer);

    // --- MODIFICATION START: Add vertical feedback stack for assistant answers ---
    if (role === 'assistant' && turnId) {
        // ONE action row per answer. The vote pill and "Why trust this answer?" are the
        // same kind of affordance — what you do WITH the answer — and stacking them cost
        // a row while splitting them across two alignments. The capsule mounts its
        // toggle into the left slot when this row exists (absence-tolerant: without it
        // the capsule falls back to its own line, exactly as before) and keeps its
        // expandable body full-width BELOW the row, where it has room to be read.
        const feedbackWrapper = document.createElement('div');
        feedbackWrapper.className = 'msg-actions-row mt-2 flex items-center justify-between gap-3';
        feedbackWrapper.dataset.turnId = turnId;
        const actionsSlot = document.createElement('div');
        actionsSlot.className = 'msg-actions-slot flex items-center min-w-0';
        feedbackWrapper.appendChild(actionsSlot);

        // The vote group is one control of the acts strip (answer.css `.ans-ctl-group`):
        // the same 24px baseline, type and hairline as the verdict chip and Regenerate.
        const pill = document.createElement('div');
        pill.className = 'ans-ctl-group';
        pill.setAttribute('role', 'group'); pill.setAttribute('aria-label', 'Rate this answer');

        const voteEngine = _currentTurnEngine();

        const makeBtn = (dir) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            // Removed 'group' so hover only affects this button, not sibling
            // `u-tip-above`: the pill sits directly on top of "Why trust this answer?",
            // so a tooltip below would cover the affordance it is anchored to.
            btn.className = 'feedback-btn ans-ctl u-tip u-tip-above';
            // What this vote will DO, per engine, with the boundary as the quieter
            // second tier. The platform tooltip (uTip) — never a native `title`, which
            // renders an unthemed grey box and ignores focus.
            const tip = composeVoteTip(dir, voteEngine);
            btn.setAttribute('data-tip', tip.lead);
            btn.setAttribute('data-tip-note', tip.note);
            btn.setAttribute('aria-label', dir === 'up' ? 'Mark answer as helpful' : 'Mark answer as unhelpful');
            btn.setAttribute('aria-pressed', 'false');
            btn.dataset.vote = dir;
            const svg = dir === 'up'
                ? `<svg class='w-4 h-4' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'>
                     <path d='M14 9V5a3 3 0 00-3-3l-4 9v11h11a3 3 0 003-3v-5a3 3 0 00-3-3h-7'/>
                   </svg>`
                : `<svg class='w-4 h-4' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'>
                     <path d='M10 15v4a3 3 0 003 3l4-9V2H6a3 3 0 00-3 3v5a3 3 0 003 3h7'/>
                   </svg>`;
            const labelText = dir === 'up' ? 'Helpful' : 'Unhelpful';
            const labelSpan = document.createElement('span');
            labelSpan.className = 'vote-label';
            labelSpan.textContent = labelText;
            btn.innerHTML = svg; // set icon first
            btn.appendChild(labelSpan);
            // hover / pressed are the stylesheet's (`.ans-ctl`, `[aria-pressed]`) — no inline colour classes
            return btn;
        };

        const upBtn = makeBtn('up');
        const downBtn = makeBtn('down');

        const applyState = () => {
            const current = state.feedbackByTurn[turnId] || 'none';
            upBtn.classList.toggle('active', current === 'up');
            downBtn.classList.toggle('active', current === 'down');
            // pressed = `aria-pressed` (the stylesheet paints it); no inline colour classes
            upBtn.setAttribute('aria-pressed', current === 'up' ? 'true' : 'false');
            downBtn.setAttribute('aria-pressed', current === 'down' ? 'true' : 'false');
        };

        const toggleVote = (dir) => {
            const current = state.feedbackByTurn[turnId] || 'none';
            state.feedbackByTurn[turnId] = current === dir ? null : dir;
            applyState();
            
            // --- MODIFICATION START: Save feedback to backend ---
            const vote = state.feedbackByTurn[turnId];
            if (state.currentSessionId) {
                import('./api.js?v=1.2').then(({ updateTurnFeedback }) => {
                    updateTurnFeedback(state.currentSessionId, turnId, vote)
                        .catch(err => {
                            console.error(`Failed to save feedback for turn ${turnId}:`, err);
                        });
                });
            }
            // --- MODIFICATION END ---
        };

        upBtn.addEventListener('click', () => toggleVote('up'));
        downBtn.addEventListener('click', () => toggleVote('down'));

        pill.appendChild(upBtn);
        pill.appendChild(downBtn);
        feedbackWrapper.appendChild(pill);
        messageContainer.appendChild(feedbackWrapper);
        applyState();
    }
    // --- MODIFICATION END ---

    DOM.chatLog.appendChild(wrapper);

    // Logic for avatar click, header buttons, and turn badges
    if (role === 'assistant' && turnId) {
        // --- Add Turn Badge to Assistant Avatar ---
        const assistantBadge = document.createElement('span');
        assistantBadge.className = 'turn-badge clickable-badge';
        assistantBadge.dataset.turnId = turnId;
        if (state.showTooltips) {
            assistantBadge.title = 'Click to toggle context validity';
        }
        assistantBadge.addEventListener('mousedown', (e) => e.stopPropagation());
        assistantBadge.textContent = turnId;
        assistantBadge.classList.add('assistant-badge');
        wrapper.appendChild(assistantBadge); // Append badge to wrapper, not icon

        if (isValid === false) {
            assistantBadge.classList.add('context-invalid');
        }


        // --- Find previous User message and add badge/click handler ---
        const userBubbles = DOM.chatLog.querySelectorAll('.message-bubble:has(.user-avatar)');
        const lastUserBubble = userBubbles.length > 0 ? userBubbles[userBubbles.length - 1] : null;

        if (lastUserBubble) {
            const userAvatarIcon = lastUserBubble.querySelector('.user-avatar');
            if (userAvatarIcon) {
                // 1. Make the User Avatar clickable (REGARDLESS of context)
                userAvatarIcon.classList.add('clickable-avatar');
                userAvatarIcon.dataset.turnId = turnId;

                // --- Add Turn Badge to User Avatar ---
                const userBadge = document.createElement('span');
                userBadge.className = 'turn-badge clickable-badge';
                userBadge.dataset.turnId = turnId;
                if (state.showTooltips) {
                    userBadge.title = 'Click to toggle context validity';
                }
                userBadge.addEventListener('mousedown', (e) => e.stopPropagation());
                userBadge.textContent = turnId;
                userBadge.classList.add('user-badge');
                lastUserBubble.appendChild(userBadge); // Append badge to wrapper, not icon

                if (isValid === false) {
                    userBadge.classList.add('context-invalid');
                }
                if (state.showTooltips) {
                    if (isValid === false) {
                        userAvatarIcon.title = `Reload Details for Turn ${turnId} (Archived Context)`;
                    } else {
                        userAvatarIcon.title = `Reload Plan & Details for Turn ${turnId}`;
                    }
                }
            }
        }

        // --- Show and update the header replay buttons ---
        if (DOM.headerReplayPlannedButton) {
            DOM.headerReplayPlannedButton.classList.remove('hidden');
            DOM.headerReplayPlannedButton.dataset.turnId = turnId;
        }
        if (DOM.headerReplayOptimizedButton) {
            DOM.headerReplayOptimizedButton.classList.remove('hidden');
            DOM.headerReplayOptimizedButton.dataset.turnId = turnId;
        }
    }

    // --- Component rendering: generalized [data-component-id] + legacy .chart-render-target ---
    const componentContainers = messageContent.querySelectorAll('[data-component-id]');
    componentContainers.forEach(container => {
        const componentId = container.dataset.componentId;
        const spec = container.dataset.spec;
        if (!(componentId && spec && hasRenderer(componentId))) return;
        // Answer Surface (docs/roadmap/ANSWER_SURFACE_GRAMMAR.md §3.3): a spec-route
        // component is the body of an evidence row. A COLLAPSED row has no width, so a
        // renderer that measures its container (a chart, the graph) would size at zero —
        // the render is deferred to the row's first open (see the `toggle` listener below).
        const row = container.closest('details.ans-ev[data-component]');
        if (row && !row.open) { container.dataset.ansDeferred = '1'; return; }
        _renderComponentInRow(componentId, container);
    });

    // Legacy: .chart-render-target without data-component-id (backward compat)
    const chartContainers = messageContent.querySelectorAll('.chart-render-target:not([data-component-id])');
    chartContainers.forEach(container => {
        if (container.dataset.spec) {
            renderChart(container.id, container.dataset.spec);
        }
    });

    // --- Table interception: wrap plain <table> elements in the dashboard viz card framing ---
    // Only for assistant messages; skip tables already inside a component container.
    if (role === 'assistant' && hasRenderer('dashboard')) {
        const allTables = Array.from(messageContent.querySelectorAll('table'))
            .filter(t => !t.closest('[data-component-id]'));

        // A table that already sits in an Answer-Surface evidence row (`.ans-ev`) is
        // never re-wrapped in the viz card — the row IS its frame, and a second frame
        // with its own header inside it is the double framing the grammar exists to
        // end. Every table the formatter draws (a row's, or a prose table) wears the
        // grammar's grid (`.ans-tw`) and its own footer acts; only a table from outside
        // the grammar (foreign markup) still takes the legacy viz-card wrap.
        const plainTables = allTables.filter(t => !t.closest('.ans-tw'));

        // ONE place for "Pin to dashboard" in an answer: the footer act of a row — a data
        // row's table and every pinnable component (manifest `answer_surface.pin`). Revealed
        // here under the Dashboard enablement gate; the click is the one delegated handler
        // below (_onPinAct), so live and reloaded answers take the same path.
        _wirePinActs(messageContent);
        _wireProposalActs(messageContent);

        plainTables.forEach((table, idx) => {
            // ── Gate on the Dashboard component being ENABLED for the profile that
            // produced this turn. `hasRenderer` above only says the renderer module is
            // registered — it is unconditionally true for every user, so on its own it
            // offered Pin-to-Dashboard on every assistant table regardless of the
            // profile's componentConfig. See
            // docs/assessments/COMPONENT_ENABLEMENT_ASSESSMENT_2026-08.md.
            // Derived from `table` BEFORE it is replaced (same parent as the wrapper, so
            // the origin walk is identical) and reused below — one derivation, not two.
            const _origin = _deriveTableOrigin(table);
            const _gate = window.ComponentGate
                ? window.ComponentGate.isEnabled('dashboard', _origin.profileId)
                : Promise.resolve(false);   // no gate module -> no claim of capability
            _gate.then((_dashEnabled) => {
            if (!_dashEnabled || !table.isConnected) return;
            const tableHtml = table.outerHTML;

            // Derive a readable title from the column headers
            const headers = Array.from(table.querySelectorAll('thead th, thead td'))
                .map(th => th.textContent.trim()).filter(Boolean);
            const tileTitle = headers.length
                ? headers.join(' · ').slice(0, 60)
                : (table.querySelector('caption')?.textContent.trim() || 'Data Table');

            // Replace the table element with a wrapper div.
            //
            // The id must be UNIQUE ACROSS MESSAGES, not merely across the tables of
            // one message: a session reload replays every turn in one tick, so two
            // table answers used to receive the SAME `dash-tbl-<ms>-0` id — and
            // renderDashboard, which resolves its container by id, painted the second
            // card into the FIRST wrapper and left the second turn's table container
            // empty (prod session 0aa3a650, turn 2: a bordered box holding nothing;
            // reproduced in headless Chrome with two same-tick addMessage calls). A
            // monotonic counter makes the id unique whatever the clock says.
            const wrapperId = `dash-tbl-${Date.now()}-${idx}-${++_dashTableSeq}`;
            const wrapper = document.createElement('div');
            wrapper.id = wrapperId;
            wrapper.style.cssText = 'margin:8px 0;';
            const _tableHome = table.parentNode;
            table.parentNode.replaceChild(wrapper, table);

            // Session/profile/turn context for the Pin tile metadata comes from
            // `_deriveTableOrigin` above — computed once, before the table was replaced.
            const { sessionId: _sessionId, sourcePrompt: _sourcePrompt,
                    profileTag: _profileTag, profileId: _profileId,
                    turnId: _sourceTurnId } = _origin;

            // Render as a viz card with the table content (force_table = no chart)
            renderComponent('dashboard', wrapperId, {
                view: 'visualization',
                force_table: true,
                tile_title: tileTitle,
                content: tableHtml,
                query: _sourcePrompt,
                source_prompt: _sourcePrompt,
                chart_type: '',
                session_id: _sessionId,
                source_turn_id: _sourceTurnId,
                profile_id: _profileId,
                profile_tag: _profileTag,
            });
            // The table is the ANSWER; the card is an affordance around it. If the
            // renderer produced no card (a lost container, a renderer that bailed),
            // put the table back rather than leave an empty box where the data was.
            if (!wrapper.querySelector('.dash-viz-card') && _tableHome && wrapper.parentNode === _tableHome) {
                _tableHome.replaceChild(table, wrapper);
            }
            });
        });
    }

    wrapper.scrollIntoView({ behavior: 'smooth', block: 'end' });
}


/**
 * Derive the origin of a rendered table: which session, which prompt, which profile,
 * which turn. Extracted verbatim from the table-interception loop so it can be called
 * BEFORE the table is replaced — the enablement gate needs the profile id up front, and
 * deriving it twice would be a second copy of the rule this refactor exists to remove.
 *
 * Takes the table (or its wrapper): both share the same parent, so the walk is identical.
 */
// Monotonic id source for intercepted-table wrappers (see the interception above).
let _dashTableSeq = 0;

// ── "Pin to dashboard" — one act, one place, one rule ────────────────────────
// The act is the footer button of an answer row: `.ans-pin-table` on a data row's table
// (formatter._data_table_html) and `.ans-pin[data-pin-component]` on a pinnable component
// row (answer_surface.pin_footer, from the manifest's `answer_surface.pin`). Both are drawn
// hidden; _wirePinActs reveals them only when the Dashboard component is enabled for the
// agent that answered, and _onPinAct (one delegated click) builds the tile.
// Rows persisted before a component declared `pin` carry no footer — they are given one.
const _PINNABLE_COMPONENTS = ['chart', 'knowledge_graph', 'flow', 'explain_plan', 'action'];   // = manifests with answer_surface.pin (gate-asserted)
function _wirePinActs(root) {
    if (!root || !root.querySelectorAll) return;
    root.querySelectorAll('.ans-ev[data-component]').forEach((row) => {
        const cid = row.getAttribute('data-component');
        if (!_PINNABLE_COMPONENTS.includes(cid) || row.querySelector('.ans-pin')) return;
        const body = row.querySelector('.ans-ev-b');
        if (!body) return;
        body.insertAdjacentHTML('beforeend', '<div class="ans-tf"><div class="ans-tf-l"></div><div class="ans-tf-r">'
            + `<button type="button" class="ans-act ans-pin" data-pin-component="${cid}" hidden>Pin to dashboard</button></div></div>`);
    });
    root.querySelectorAll('.ans-pin-table, .ans-pin').forEach((btn) => {
        if (btn.dataset.wired) return;
        btn.dataset.wired = '1';
        const gate = window.ComponentGate
            ? window.ComponentGate.isEnabled('dashboard', _deriveTableOrigin(btn).profileId)
            : Promise.resolve(false);   // no gate module -> no claim of capability
        gate.then((ok) => { if (ok && btn.isConnected) btn.hidden = false; });
    });
    // a card that drew its button row before the acts were wired takes its row acts now
    if (window.PanelOpenControl && window.PanelOpenControl.adoptRowActs) window.PanelOpenControl.adoptRowActs(root);
}

/** The table a data row's footer act belongs to: the grid just before the footer. */
function _pinTableOf(btn) {
    const tf = btn.closest('.ans-tf');
    let el = tf ? tf.previousElementSibling : null;
    while (el && !el.classList.contains('ans-tw')) el = el.previousElementSibling;
    return (el && el.querySelector('table'))
        || btn.closest('.ans-ev-b')?.querySelector('.ans-tw table') || null;
}

function _onPinAct(btn) {
    // The turn and profile are read NOW, at the click: on a live answer the turn id is
    // stamped when the turn finalizes, after the act was drawn.
    const o = _deriveTableOrigin(btn);
    const meta = { source_prompt: o.sourcePrompt, source_session_id: o.sessionId,
                   source_turn_id: o.turnId, profile_id: o.profileId, profile_tag: o.profileTag };
    let payload = null;
    if (btn.classList.contains('ans-pin-table')) {
        const table = _pinTableOf(btn);
        if (!table) return;
        const headers = Array.from(table.querySelectorAll('thead th')).map(th => th.textContent.trim()).filter(Boolean);
        payload = { component_id: 'table', spec: {}, html: table.outerHTML,
                    title: headers.length ? headers.join(' · ').slice(0, 60) : 'Data Table' };
    } else {
        const cid = btn.dataset.pinComponent;
        const row = btn.closest('.ans-ev');
        // OBDA-ACT — an action row is server-drawn (no data-spec container): the tile carries the
        // row itself, so it shows the same consequence and the same Confirm, reconciled
        // against the turn's confirmations (an act is never performed twice).
        if (cid === 'action') {
            const ar = row ? row.querySelector('.act-row') : null;
            if (!ar) return;
            let pend = {};
            try { pend = JSON.parse(ar.getAttribute('data-act-pending') || '{}') || {}; } catch (_) { pend = {}; }
            const summary = row.querySelector('.ans-ev-t')?.textContent?.trim();
            payload = { component_id: 'action', spec: { pending: pend, state: ar.getAttribute('data-act-state') },
                        html: ar.outerHTML, title: summary || `Action · ${pend.action || ''}` };
        }
        const container = (payload || !row) ? null : row.querySelector(`[data-component-id="${cid}"]`);
        if (!payload && !container) return;
        if (!payload) {
            let spec = {};
            try { spec = JSON.parse(container.dataset.spec || '{}'); } catch (_) { spec = {}; }
            // the spec's own turn (embedded by the backend at render time) wins over the page
            if (spec && spec.source_turn_id != null) meta.source_turn_id = String(spec.source_turn_id);
            const summary = row.querySelector('.ans-ev-t')?.textContent?.trim();
            const title = spec?.options?.title?.text || spec?.title || spec?.flow?.name || summary
                || ({ chart: 'Chart', flow: 'Flow', knowledge_graph: 'Knowledge Graph', explain_plan: 'Query plan' }[cid] || 'Visual');
            payload = { component_id: cid, spec, html: container.innerHTML, title };
        }
    }
    if (typeof window.addTileFromComponent === 'function') window.addTileFromComponent(payload, meta);
    else loadRendererModule('dashboard').then(m => { if (!m) throw new Error('dashboard is not installed in this deployment'); return m; }).then(m => m.addTileFromComponent(payload, meta))
        .catch(err => console.error('[Pin to dashboard] could not load the dashboard renderer', err));
}

// ── PROP-008 OBDA-MODEL: the proposal row's one-click Draft ─────────────────────
// The row (components/builtin/knowledge_graph: _act_proposal_html) carries the act only when
// a draft is OWED (the verdict allows it, nothing drafted yet); the click runs the SAME
// handler path a turn runs, through POST /v1/knowledge-graph/propose, and the row is
// replaced IN PLACE by the handler's own re-render (drafted ⇒ no button: Rule 8.5).
function _wireProposalActs(root) {
    if (!root || !root.querySelectorAll) return;
    root.querySelectorAll('.kg-proposal-draft').forEach((btn) => {
        if (btn.dataset.wired) return;
        btn.dataset.wired = '1';
    });
    _reconcileProposalRows(root);
    // a proposal the AGENT drafted in its answer changed the authored layer: say so once, so
    // the Workspace's Drafts and an open graph update without a page refresh
    root.querySelectorAll('.kg-proposal[data-kg-drafted]').forEach((row) => {
        if (row.dataset.announced) return;
        row.dataset.announced = '1';
        UderiaBus.announce('knowledge_graph', 'updated', [row.dataset.kgDrafted], { aspect: 'authored' });
    });
}
// A row is a record of what was proposed THEN; its act must reflect the state NOW. A turn
// answered before the person clicked Draft it was re-drawn on reload with the button again
// (the in-place replacement is not part of the stored turn) — and a click then refused with
// "already exists". So every row with a Draft button asks the authored layer once (one read
// per graph) and, when its node exists, says so with its stage and offers nothing.
const _PROPOSAL_STAGE = { draft: 'not yet published', validated: 'validated, not yet published',
                          deployed: 'landed, not yet published', published: 'published on the model' };
let _authoredReads = new Map();   // kg_id → Promise<Map<name, status>>, dropped on every change
function _authoredStates(kgId) {
    if (!_authoredReads.has(kgId)) {
        const token = localStorage.getItem('tda_auth_token');
        _authoredReads.set(kgId, fetch(`/api/v1/knowledge-graph/authored?kg_id=${encodeURIComponent(kgId)}`,
            { headers: { 'Authorization': `Bearer ${token}` } })
            .then(r => (r.ok ? r.json() : { nodes: [] }))
            .then((d) => {
                const m = new Map();
                for (const n of (d.nodes || [])) {
                    m.set(String(n.name), n.status || 'draft');
                    for (const a of (n.attributes || [])) m.set(String(a.name), a.status || n.status || 'draft');
                }
                return m;
            })
            .catch(() => new Map()));
    }
    return _authoredReads.get(kgId);
}
function _proposalTarget(row, btn) {
    if (row.dataset.kgNode) return row.dataset.kgNode;
    // a row stored before the server named its node: the same rule the handler drafts by
    let a = {};
    try { a = JSON.parse(btn.dataset.kgArgs || '{}'); } catch (_) { a = {}; }
    if (a.action === 'propose_attribute' && a.entity && a.attribute_name) return `${a.entity}.${a.attribute_name}`;
    if (a.action === 'propose_entity' && a.entity_name) {
        const p = String(a.relates_to || a.entity || '');
        const root = p.includes('.') ? p.split('.')[0] : '';
        return root && !String(a.entity_name).includes('.') ? `${root}.${a.entity_name}` : String(a.entity_name);
    }
    return '';
}
function _reconcileProposalRows(root) {
    if (!root || !root.querySelectorAll) return Promise.resolve();
    const rows = [...root.querySelectorAll('.kg-proposal')].filter(r => r.querySelector('.kg-proposal-draft'));
    return Promise.all(rows.map(async (row) => {
        const btn = row.querySelector('.kg-proposal-draft');
        const kgId = btn && btn.dataset.kgId;
        const target = btn && _proposalTarget(row, btn);
        if (!kgId || !target) return;
        const status = (await _authoredStates(kgId)).get(target);
        if (!status || !row.querySelector('.kg-proposal-draft')) return;   // a row built before it is attached settles too
        const state = row.querySelector('.kg-proposal-state');
        if (state) state.textContent = `Drafted on the authored layer — ${_PROPOSAL_STAGE[status] || status}.`;
        const foot = row.querySelector('.kg-proposal-foot');
        if (foot) foot.remove();
        row.dataset.kgDrafted = kgId;
        row.dataset.announced = '1';      // the state is read, not a change to announce
    }));
}
if (typeof document !== 'undefined' && !window.__proposalReconcileBound) {
    window.__proposalReconcileBound = true;
    // the authored layer changed (a Draft it, a land, a publish): read it again, re-settle the page
    UderiaBus.onComponent('knowledge_graph', () => {
        _authoredReads = new Map();
        _reconcileProposalRows(document);
    });
}

function _onProposalAct(btn) {
    const row = btn.closest('.kg-proposal');
    if (!row || btn.disabled) return;
    let args = {};
    try { args = JSON.parse(btn.dataset.kgArgs || '{}'); } catch (_) { args = {}; }
    const kgId = btn.dataset.kgId || '';
    if (!kgId || !args.action) return;
    btn.disabled = true;
    const was = btn.textContent;
    btn.textContent = 'Drafting…';
    const token = localStorage.getItem('tda_auth_token');
    return fetch('/api/v1/knowledge-graph/propose', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
        body: JSON.stringify({ ...args, kg_id: kgId, draft: true }),
    }).then(r => r.json().then(d => ({ ok: r.ok, d })))
      .then(({ ok, d }) => {
          if (ok && d && d.html) {
              const tmp = document.createElement('div');
              tmp.innerHTML = d.html;
              const fresh = tmp.querySelector('.kg-proposal') || tmp.firstElementChild;
              if (fresh) row.replaceWith(fresh);
              if (typeof window.showAppBanner === 'function' && d.drafted) window.showAppBanner(d.note || 'Drafted on the authored layer.', 'success');
              UderiaBus.announce('knowledge_graph', 'updated', [kgId], { aspect: 'authored' });
          } else {
              btn.disabled = false; btn.textContent = was;
              if (typeof window.showAppBanner === 'function') window.showAppBanner((d && d.message) || 'The draft was refused.', 'warning');
          }
      })
      .catch((err) => { btn.disabled = false; btn.textContent = was; console.error('[OBDA-MODEL] draft failed', err); });
}
if (typeof document !== 'undefined' && !window.__proposalActBound) {
    window.__proposalActBound = true;
    document.addEventListener('click', (e) => {
        const btn = e.target && e.target.closest ? e.target.closest('.kg-proposal-draft') : null;
        if (!btn) return;
        e.preventDefault(); e.stopPropagation();
        _onProposalAct(btn);
    });
}

// ── PROP-008 OBDA-MODEL: the model-gap note's one act — the proposal form ─────────
// The note (agent/model_gap.py) is drawn under the answer of EVERY engine when the question's
// subject is not in the bound model and the agent may author. Its act opens a small form in
// place: the person names what the concept records (one per line, `name` or `name: type`; the
// first one identifies a row) and what it belongs to (the model's own entities, carried on the
// act), and the form asks the normalisation advisor DIRECTLY — the same handler path a turn
// runs (POST /v1/knowledge-graph/propose). The advisor's verdict row, with its Draft it, takes
// the form's place. No model in between: the proposal is made because the person asked.
function _gapEl(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
}
function _gapField(form, label, control) {
    const row = _gapEl('label', 'ans-gap-row');
    row.appendChild(_gapEl('span', 'ans-gap-l', label));
    row.appendChild(control);
    form.appendChild(row);
    return control;
}
// the model-gap form's suggestions already fetched this page, by (graph, concept, session, turn)
const _gapSuggestions = new Map();
function _onGapProposeAct(btn) {
    const note = btn.closest('.ans-note');
    if (!note || btn.disabled) return;
    const open = note.nextElementSibling;
    if (open && open.classList && open.classList.contains('ans-gap-form')) { open.remove(); return; }   // the act toggles
    const name = btn.dataset.gapName || 'Concept';
    let ents = [];
    try { ents = JSON.parse(btn.dataset.gapEntities || '[]') || []; } catch (_) { ents = []; }
    const form = _gapEl('div', 'ans-gap-form');
    form.setAttribute('role', 'group');
    form.setAttribute('aria-label', `Propose ${name}`);
    const nameIn = _gapField(form, 'Name', _gapEl('input', 'ans-gap-name'));
    nameIn.value = name;
    const art = /^[aeiou]/i.test(name) ? 'an' : 'a';
    const attrs = _gapField(form, 'What it records', _gapEl('textarea', 'ans-gap-attrs'));
    attrs.setAttribute('rows', '7');
    const placeholder = 'in plain words, one per line — e.g. Summary';
    attrs.setAttribute('placeholder', `Suggesting what ${art} ${name.toLowerCase()} records…`);
    // the person writes business terms; the identifier and the link are the platform's
    const hint = _gapEl('div', 'ans-gap-hint', 'The platform adds the identifier and the link to what it belongs to.');
    form.appendChild(hint);
    let parent;
    if (ents.length) {
        parent = _gapEl('select', 'ans-gap-parent');
        const none = _gapEl('option', null, '— not tied to an existing entity —');
        none.setAttribute('value', '');
        parent.appendChild(none);
        ents.forEach((full) => {
            const o = _gapEl('option', null, String(full).split('.').pop());
            o.setAttribute('value', full);
            parent.appendChild(o);
        });
        parent.value = '';
    } else {
        parent = _gapEl('input', 'ans-gap-parent');
        parent.setAttribute('placeholder', 'an entity of the model (optional)');
    }
    _gapField(form, 'Belongs to', parent);
    const desc = _gapField(form, `What is ${art} ${name.toLowerCase()}?`, _gapEl('input', 'ans-gap-desc'));
    desc.setAttribute('placeholder', 'optional — one sentence');
    const foot = _gapEl('div', 'ans-gap-foot');
    const msg = _gapEl('span', 'ans-gap-msg');
    msg.setAttribute('role', 'alert');
    const cancel = _gapEl('button', 'ans-act ans-gap-cancel', 'Cancel');
    cancel.setAttribute('type', 'button');
    const go = _gapEl('button', 'ans-act ans-gap-submit', 'Check the proposal');
    go.setAttribute('type', 'button');
    foot.appendChild(msg); foot.appendChild(cancel); foot.appendChild(go);
    form.appendChild(foot);
    cancel.addEventListener('click', (e) => { if (e && e.preventDefault) e.preventDefault(); form.remove(); btn.focus(); });
    go.addEventListener('click', (e) => { if (e && e.preventDefault) e.preventDefault(); _submitGapProposal(btn, form); });
    note.after(form);
    attrs.focus();
    // a starting point from the answering agent's own model: what such a concept records, and
    // which entity it belongs to — filled in only where the person has not written yet
    const o = _deriveTableOrigin(btn);
    const token = localStorage.getItem('tda_auth_token');
    // one suggestion per (graph, concept, turn): a form opened again reuses it — the call is paid
    // and booked to the turn, so asking twice for the same thing would bill twice
    const sugKey = [btn.dataset.kgId, name, o.sessionId || '', o.turnId || ''].join('|');
    let pending = _gapSuggestions.get(sugKey);
    if (!pending) {
        pending = fetch('/api/v1/knowledge-graph/propose/suggest', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
            body: JSON.stringify({ kg_id: btn.dataset.kgId, name, question: o.sourcePrompt || '', profile_id: o.profileId || '',
                                   session_id: o.sessionId || '', turn_id: o.turnId || '' }),
        }).then(r => r.json()).then((d) => {
            if (!(d && Array.isArray(d.records) && d.records.length)) _gapSuggestions.delete(sugKey);
            return d;
        }, (err) => { _gapSuggestions.delete(sugKey); throw err; });
        _gapSuggestions.set(sugKey, pending);
    }
    return pending.then((d) => {
        attrs.setAttribute('placeholder', placeholder);
        if (!form.isConnected) return;
        const recs = Array.isArray(d && d.records) ? d.records : [];
        if (recs.length && !String(attrs.value || '').trim()) attrs.value = recs.join('\n');
        if (d && d.belongs_to && !parent.value) parent.value = d.belongs_to;
        if (recs.length) {
            hint.textContent = `Suggested by ${d.profile_tag ? '@' + d.profile_tag + '\u2019s' : 'the agent\u2019s'} model — edit freely. `
                + 'The platform adds the identifier and the link to what it belongs to.';
        }
    }).catch(() => { attrs.setAttribute('placeholder', placeholder); });
}
function _submitGapProposal(btn, form) {
    const q = (sel) => form.querySelector(sel);
    const go = q('.ans-gap-submit'), msg = q('.ans-gap-msg');
    if (!go || go.disabled) return Promise.resolve();
    const name = String(q('.ans-gap-name').value || '').trim();
    const attributes = String(q('.ans-gap-attrs').value || '').split('\n').map(l => l.trim()).filter(Boolean);
    const parent = String(q('.ans-gap-parent').value || '').trim();
    const description = String(q('.ans-gap-desc').value || '').trim();
    if (!name) { msg.textContent = 'Give the concept a name.'; return Promise.resolve(); }
    if (!attributes.length) { msg.textContent = `Name at least one thing ${/^[aeiou]/i.test(name) ? 'an' : 'a'} ${name} records.`; return Promise.resolve(); }
    const body = { kg_id: btn.dataset.kgId, action: 'propose_entity', entity_name: name, attributes };
    if (parent) body.relates_to = parent;
    if (description) body.description = description;
    go.disabled = true;
    const was = go.textContent;
    go.textContent = 'Checking…';
    msg.textContent = '';
    const token = localStorage.getItem('tda_auth_token');
    return fetch('/api/v1/knowledge-graph/propose', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
        body: JSON.stringify(body),
    }).then(r => r.json().then(d => ({ ok: r.ok, d })))
      .then(({ ok, d }) => {
          if (ok && d && d.html) {
              const tmp = document.createElement('div');
              tmp.innerHTML = d.html;
              const row = tmp.querySelector('.kg-proposal') || tmp.firstElementChild;
              // the verdict takes the form's place, in the form's frame
              if (row) { const wrap = _gapEl('div', 'ans-gap-result'); wrap.appendChild(row); form.replaceWith(wrap); } else form.remove();
              // the act that was owed is done; the verdict row now offers the next one (Draft it)
              btn.disabled = true;
              btn.textContent = 'Proposed below';
          } else {
              go.disabled = false; go.textContent = was;
              msg.textContent = (d && d.message) || 'The proposal could not be checked.';
          }
      })
      .catch((err) => { go.disabled = false; go.textContent = was; msg.textContent = 'The proposal could not be sent.'; console.error('[OBDA-MODEL] proposal failed', err); });
}
if (typeof document !== 'undefined' && !window.__gapProposeBound) {
    window.__gapProposeBound = true;
    document.addEventListener('click', (e) => {
        const btn = e.target && e.target.closest ? e.target.closest('.ans-gap-propose') : null;
        if (!btn) return;
        e.preventDefault(); e.stopPropagation();
        _onGapProposeAct(btn);
    });
}

if (typeof document !== 'undefined' && !window.__pinActBound) {
    window.__pinActBound = true;
    document.addEventListener('click', (e) => {
        const btn = e.target && e.target.closest ? e.target.closest('.ans-pin-table, .ans-pin') : null;
        if (!btn || btn.hidden) return;
        e.preventDefault();
        e.stopPropagation();
        _onPinAct(btn);
    });
}

function _deriveTableOrigin(el) {
    // Session ID: ancestor data attribute (set by history replay) or live state.
    // NOTE: state is a module import — it is NOT on window. window.__currentSessionId
    // is the reliable global set by setCurrentSessionId().
    const _msgAnchor = el.closest('[data-session-id]');
    const sessionId = _msgAnchor?.dataset?.sessionId
        || state.currentSessionId
        || window.__currentSessionId
        || '';

    // Derive the source prompt + profile + turn from the ORIGINATING user
    // message (the .justify-end bubble preceding this assistant message).
    // That message's green @TAG chip is the single source of truth for the
    // profile that produced THIS result (override-aware).
    //
    // BUG (fixed): the old code set the tag from the LAST
    // `.message-profile-badge, .profile-tag` in the WHOLE document. That
    // broad selector also matches profile chips in the config panel,
    // profile pickers and other sessions, so a stray @FOCUS chip elsewhere
    // leaked onto an @IDGGT result's pinned table tile — while the profile id
    // (from session state) stayed correct, producing an inconsistent badge.
    let sourcePrompt = '';
    let _originProfileTag = '';
    let turnId = '';
    const _msgBubble = el.closest('.message-bubble');
    if (_msgBubble) {
        turnId = _msgBubble.querySelector('[data-turn-id]')?.dataset?.turnId
            || _msgBubble.nextElementSibling?.querySelector?.('[data-turn-id]')?.dataset?.turnId
            || '';
        let cur = _msgBubble.previousElementSibling;
        while (cur) {
            if (cur.classList.contains('justify-end')) {
                // Try the dedicated content div first; fall back to any <p> that
                // is NOT the author label (author label contains only "You" / "Assistant").
                const contentEl = cur.querySelector('.message-content');
                if (contentEl) {
                    sourcePrompt = (contentEl.textContent || '').trim();
                } else {
                    const pEls = Array.from(cur.querySelectorAll('p'));
                    const bodyP = pEls.find(p => p.textContent.trim() !== 'You' && p.textContent.trim() !== 'Assistant');
                    sourcePrompt = (bodyP?.textContent || '').trim();
                }
                const _badgeEl = cur.querySelector('.message-profile-badge');
                if (_badgeEl) _originProfileTag = (_badgeEl.textContent || '').replace(/^@/, '').trim();
                break;
            }
            cur = cur.previousElementSibling;
        }
    }

    // Resolve identity from the origin tag FIRST (override-aware), keeping
    // profile_id and profile_tag CONSISTENT; fall back to session state.
    const _profilesList = (window?.configState?.profiles) || [];
    let profileTag = _originProfileTag || '';
    let profileId = '';
    if (profileTag) {
        const _byTag = _profilesList.find(p => (p.tag || '') === profileTag);
        if (_byTag) profileId = _byTag.id || _byTag.profile_id || '';
    }
    if (!profileId) profileId = state.currentSessionProfileId || window.__currentProfileId || '';
    if (!profileTag && profileId) {
        profileTag = _profilesList.find(p => (p.id || p.profile_id) === profileId)?.tag || '';
    }
    return { sessionId, sourcePrompt, profileTag, profileId, turnId };
}
// The ONE derivation of "which session, turn and agent produced this part of an answer" —
// shared with the Files "Keep" acts (static/js/handlers/filesKeep.js), which must gate and stamp
// exactly as Pin does.
if (typeof window !== 'undefined') window.__answerOrigin = _deriveTableOrigin;

/**
 * Updates the profile badge of the most recent user message.
 * Used when the backend resolves the correct profile tag for session primers.
 * @param {string} profileTag - The profile tag to display
 */
/**
 * Stamp the orchestration tag (Flow / Genie) onto the last user message, driven
 * by the server-sent `origin` on the user_message_profile_tag event. Keeps the
 * live UI render consistent with the persisted turn without re-deriving anything.
 * @param {string} origin - 'flow' | 'genie_parent' | 'genie_child' | null
 */
export function updateLastUserMessageOrigin(origin) {
    const cfg = origin === 'flow'
        ? { cls: 'flow-task-tag', label: 'Flow' }
        : origin === 'genie_parent'
            ? { cls: 'genie-parent-tag', label: 'Genie Parent' }
            : origin === 'genie_child'
                ? { cls: 'genie-child-tag', label: 'Genie Child' }
                : null;
    if (!cfg) return;

    const chatContainer = DOM.chatLog;
    if (!chatContainer) return;

    const messageWrappers = chatContainer.querySelectorAll('.message-bubble');
    let lastUserWrapper = null;
    for (let i = messageWrappers.length - 1; i >= 0; i--) {
        if (messageWrappers[i].classList.contains('justify-end')) {
            lastUserWrapper = messageWrappers[i];
            break;
        }
    }
    if (!lastUserWrapper) return;

    // The author line ("You") is where provenance tags live (see addMessage).
    const author = lastUserWrapper.querySelector('p.font-bold');
    if (!author) return;
    if (author.querySelector('.' + cfg.cls)) return;  // already stamped — idempotent

    const tag = document.createElement('span');
    tag.className = cfg.cls;
    tag.textContent = cfg.label;
    author.appendChild(tag);
}

export function updateLastUserMessageProfileTag(profileTag) {
    if (!profileTag) return;

    // Find the last user message wrapper
    const chatContainer = DOM.chatLog;
    if (!chatContainer) return;

    const messageWrappers = chatContainer.querySelectorAll('.message-bubble');
    let lastUserWrapper = null;

    // Find the last user message (has justify-end class)
    for (let i = messageWrappers.length - 1; i >= 0; i--) {
        if (messageWrappers[i].classList.contains('justify-end')) {
            lastUserWrapper = messageWrappers[i];
            break;
        }
    }

    if (!lastUserWrapper) return;

    // Check if there's already a profile badge
    let existingBadge = lastUserWrapper.querySelector('[title^="Executed with profile"]');

    // Create or update the badge
    // PURE INDUSTRIAL SOLID COLORS
    const adjustBrightness = (hex, percent) => {
        hex = hex.replace('#', '');
        const r = parseInt(hex.slice(0, 2), 16);
        const g = parseInt(hex.slice(2, 4), 16);
        const b = parseInt(hex.slice(4, 6), 16);
        const factor = 1 + (percent / 100);
        const newR = Math.min(255, Math.max(0, Math.round(r * factor)));
        const newG = Math.min(255, Math.max(0, Math.round(g * factor)));
        const newB = Math.min(255, Math.max(0, Math.round(b * factor)));
        const toHex = (n) => n.toString(16).padStart(2, '0');
        return `#${toHex(newR)}${toHex(newG)}${toHex(newB)}`;
    };

    // Get profile colors if available - use solid colors
    let bgStyleValue = null;
    let borderColorValue = null;
    let textColorValue = '#FFFFFF';  // Pure industrial: Always white text
    if (window.configState?.profiles) {
        const profile = window.configState.profiles.find(p => p.tag === profileTag);
        if (profile && profile.color) {
            bgStyleValue = profile.color;  // Solid color
            borderColorValue = profile.color;  // Flat monolithic: border matches background
        }
    }

    if (existingBadge) {
        // Update existing badge - ensure it has unified classes
        if (!existingBadge.classList.contains('profile-tag')) {
            existingBadge.className = 'profile-tag profile-tag--md message-profile-badge flex-shrink-0';
        }
        existingBadge.textContent = `@${profileTag}`;
        existingBadge.title = `Executed with profile: ${profileTag}`;
        if (bgStyleValue) {
            existingBadge.style.setProperty('--profile-tag-bg', bgStyleValue);
            existingBadge.style.setProperty('--profile-tag-border', borderColorValue);
            existingBadge.style.setProperty('--profile-tag-text', textColorValue);
        }
    } else {
        // Create new badge with unified classes
        const profileBadge = document.createElement('div');
        profileBadge.className = 'profile-tag profile-tag--md message-profile-badge flex-shrink-0';
        profileBadge.textContent = `@${profileTag}`;
        profileBadge.title = `Executed with profile: ${profileTag}`;
        if (bgStyleValue) {
            profileBadge.style.setProperty('--profile-tag-bg', bgStyleValue);
            profileBadge.style.setProperty('--profile-tag-border', borderColorValue);
            profileBadge.style.setProperty('--profile-tag-text', textColorValue);
        }
        profileBadge.style.alignSelf = 'flex-start';
        profileBadge.style.marginRight = '12px';

        // Insert at the beginning of the wrapper (before the message container)
        const messageContainer = lastUserWrapper.querySelector('.glass-panel');
        if (messageContainer) {
            lastUserWrapper.insertBefore(profileBadge, messageContainer);
        }
    }
}


/**
 * Sets the UI state for active execution (disables input, shows stop button, etc.).
 * @param {boolean} isActive - True if execution is starting, false if ending.
 */
export function setExecutionState(isActive) {
    // S10 — in steer mode the composer stays LIVE while a turn runs (its placeholder says what
    // a sent message does); the mode is armed by the stream's task_start and released at its end
    const _steerLive = !!(isActive && window.SteerReattach && window.SteerReattach.shouldSteer && window.SteerReattach.shouldSteer(state.currentSessionId));
    DOM.userInput.disabled = isActive && !_steerLive;
    if (!isActive && window.SteerReattach && window.SteerReattach.disarmComposer) { try { window.SteerReattach.disarmComposer(); } catch (_) { /* no-op */ } }
    DOM.newChatButton.disabled = isActive;
    if (DOM.headerNewSessionButton) DOM.headerNewSessionButton.disabled = isActive;
    // S1 — Stop is where Send was: while a turn runs the composer's own control stops it
    // (the Live Status header's Stop is unreachable when that panel is collapsed). The
    // button stays ENABLED in stop mode; the form's submit path checks `data-mode`.
    DOM.submitButton.disabled = false;
    DOM.submitButton.dataset.mode = isActive ? 'stop' : 'send';
    DOM.submitButton.setAttribute('aria-label', isActive ? 'Stop the answer' : 'Send');
    DOM.submitButton.title = isActive ? 'Stop the answer (keeps what has arrived)' : '';
    DOM.submitButton.classList.toggle('composer-stop', isActive);
    if (!isActive) DOM.submitButton.classList.remove('is-stopping');
    DOM.sendIcon.classList.toggle('hidden', isActive);
    DOM.loadingSpinner.classList.add('hidden');
    if (DOM.stopIcon) DOM.stopIcon.classList.toggle('hidden', !isActive);
    else DOM.loadingSpinner.classList.toggle('hidden', !isActive);
    if (!isActive) updateHintAndIndicatorState && setTimeout(() => { try { updateHintAndIndicatorState(); } catch (_) {} }, 0);
    // The composer's own states (Oct 2026): the hairline + ring while running, the steering tag
    // and the shortcut line — decided here, the one place the running state is decided.
    try { if (window.Composer && window.Composer.setRunning) window.Composer.setRunning(isActive, _steerLive); } catch (_) { /* decoration only */ }

    if (DOM.stopExecutionButton) {
        DOM.stopExecutionButton.classList.toggle('hidden', !isActive);
        DOM.stopExecutionButton.disabled = !isActive;
    }

    if (isActive) {
        // Hide and disable buttons when execution starts
        if (DOM.headerReplayPlannedButton) {
            DOM.headerReplayPlannedButton.classList.add('hidden');
            DOM.headerReplayPlannedButton.disabled = true;
        }
        if (DOM.headerReplayOptimizedButton) {
            DOM.headerReplayOptimizedButton.classList.add('hidden');
            DOM.headerReplayOptimizedButton.disabled = true;
        }
    } else {
        // Enable buttons when execution ends (they will be shown by addMessage if applicable)
        if (DOM.headerReplayPlannedButton) {
            DOM.headerReplayPlannedButton.disabled = false;
        }
        if (DOM.headerReplayOptimizedButton) {
            DOM.headerReplayOptimizedButton.disabled = false;
        }
        // Don't explicitly show them here, addMessage handles showing them when a turn completes
    }

    if (!isActive) {
        setThinkingIndicator(false);
        DOM.mcpStatusDot.classList.remove('pulsing', 'busy');
        DOM.llmStatusDot.classList.remove('pulsing', 'busy');

        if (!DOM.mcpStatusDot.classList.contains('disconnected')) {
            DOM.mcpStatusDot.classList.add('connected');
        }
        if (!DOM.llmStatusDot.classList.contains('disconnected')) {
            DOM.llmStatusDot.classList.add('idle');
        }
    }
}


/**
 * Auto-grows the chat composer (#user-input) to fit its content, capped by the
 * CSS max-height. The vertical scrollbar is only shown once the cap is reached,
 * so short messages stay on a single line and long ones become a scrollable box
 * — the user always sees their full text. Safe no-op if the element is still a
 * plain <input> (defensive during rollout) or absent.
 */
export function autoResizeChatInput() {
    const el = DOM.userInput;
    if (!el || el.tagName !== 'TEXTAREA') return;
    // Collapse first so scrollHeight reflects the true content height (also lets
    // the box shrink back down when text is deleted).
    el.style.height = 'auto';
    const scrollHeight = el.scrollHeight;
    if (!scrollHeight) {
        // Not laid out yet (e.g. the chat view is hidden) — clear the inline
        // height so the rows="1" attribute governs, and bail. The next focus/
        // input event will size it correctly once it's visible.
        el.style.height = '';
        return;
    }
    // The CSS cap is 40vh — on iOS the LARGEST viewport, not the shell: with the keyboard up in
    // landscape (~350 px shown) it let the field grow past the bottom of the screen. The field also
    // grows only into room the conversation can give up (composerRoom).
    const maxHeight = Math.min(parseFloat(getComputedStyle(el).maxHeight) || Infinity, composerRoom(el));
    el.style.height = Math.min(scrollHeight, maxHeight) + 'px';
    el.style.overflowY = scrollHeight > maxHeight ? 'auto' : 'hidden';
    publishComposerHeight();
}

// The conversation keeps at least this much of itself above a growing composer.
const CHAT_MIN_PX = 72;

/** What in #main-content above the conversation column does not yield to it: every in-flow child
 *  but the column, and of the resource panel only its border — the panel itself shrinks and
 *  scrolls its drawer (main.css #tool-header). */
function heightAboveColumn(main, column) {
    let h = 0;
    for (const c of main.children) {
        if (c === column) continue;
        const cs = getComputedStyle(c);
        if (cs.display === 'none' || cs.position === 'absolute' || cs.position === 'fixed') continue;
        h += c.id === 'tool-header' ? c.offsetHeight - c.clientHeight : c.offsetHeight;
    }
    return h;
}

/** The conversation column's parts that do not yield: all of it but the chat itself (the
 *  conversation header split-view moves in beside it, the composer). Null before layout. */
function columnFixedHeight() {
    const column = document.getElementById('conversation-column');
    const chat = document.getElementById('chat-container');
    if (!column || !chat || !column.offsetHeight) return null;
    return column.offsetHeight - chat.offsetHeight;
}

/** The tallest the composer's field may be: all the room the column can have, less what does not
 *  yield besides the field itself, less a few lines of conversation. */
function composerRoom(el) {
    const main = document.getElementById('main-content');
    const column = document.getElementById('conversation-column');
    const fixed = columnFixedHeight();
    if (!main || !column || fixed === null || !main.clientHeight) return Infinity;
    const minField = parseFloat(getComputedStyle(el).minHeight) || 0;
    return Math.max(minField, main.clientHeight - heightAboveColumn(main, column) - (fixed - el.offsetHeight) - CHAT_MIN_PX);
}

/** --column-min on #main-content: the conversation column's minimum (main.css) — all of it that
 *  does not yield plus a few lines of conversation, never more than the room the column can have. */
export function publishComposerHeight() {
    const main = document.getElementById('main-content');
    const column = document.getElementById('conversation-column');
    const fixed = columnFixedHeight();
    if (!main || !column || fixed === null || !main.clientHeight) return;
    const room = Math.max(0, main.clientHeight - heightAboveColumn(main, column));
    const h = Math.min(fixed + CHAT_MIN_PX, room) + 'px';
    if (main.style.getPropertyValue('--column-min') !== h) main.style.setProperty('--column-min', h);
}

/**
 * Updates the visual status of the Server-Sent Events (SSE) connection indicator.
 * @param {('connected'|'reconnecting'|'disconnected')} status - The current status of the connection.
 */
export function updateSSEStatus(status) {
    if (!DOM.sseStatusDot) return;

    DOM.sseStatusDot.classList.remove('pending', 'connected', 'reconnecting', 'disconnected');
    DOM.sseStatusDot.classList.add(status);

    switch (status) {
        case 'connected':
            DOM.sseStatusDot.title = 'Real-time connection active.';
            break;
        case 'reconnecting':
            DOM.sseStatusDot.title = 'Connection lost. Attempting to reconnect...';
            break;
        case 'disconnected':
            DOM.sseStatusDot.title = 'Real-time connection failed. Please refresh the page.';
            break;
    }
}


function _renderKnowledgeRetrievalDetails(details) {
    if (!details.collections || !details.chunks) return null;

    const collections = details.collections || [];
    const chunks = details.chunks || [];
    const documentCount = details.document_count || chunks.length;

    let html = `
        <div class="status-kv-grid">
            <div class="status-kv-key">Collections</div>
            <div class="status-kv-value">${collections.join(', ')}</div>
            <div class="status-kv-key">Documents</div>
            <div class="status-kv-value">${documentCount} chunks retrieved</div>
        </div>
    `;

    if (chunks.length > 0) {
        html += `
            <details class="text-xs mt-2">
                <summary class="cursor-pointer text-gray-400 hover:text-white">View Retrieved Chunks (${chunks.length})</summary>
                <div class="space-y-2 mt-2">
        `;

        chunks.forEach((chunk, idx) => {
            const similarity = (chunk.similarity_score * 100).toFixed(1);
            const content = chunk.content || '';
            const previewLength = 150;
            const preview = content.length > previewLength ? content.substring(0, previewLength) + '...' : content;
            
            html += `
                <div class="p-2 bg-gray-900/50 rounded border border-indigo-500/30">
                    <div class="flex justify-between items-start mb-1">
                        <span class="text-indigo-400 font-semibold text-xs">Chunk ${idx + 1}</span>
                        <span class="text-gray-500 text-xs">Relevance: ${similarity}%</span>
                    </div>
                    <div class="text-gray-400 text-xs mb-1">
                        <span class="text-gray-500">Collection:</span> ${chunk.collection_name || 'Unknown'}
                    </div>
            `;

            if (content.length > previewLength) {
                html += `
                    <details class="text-xs">
                        <summary class="cursor-pointer text-gray-400 hover:text-white">${preview}</summary>
                        <div class="mt-2 p-2 status-text-block whitespace-pre-wrap text-gray-300">${content}</div>
                    </details>
                `;
            } else {
                html += `<div class="text-gray-300 text-xs whitespace-pre-wrap">${content}</div>`;
            }

            html += `</div>`;
        });

        html += `
                </div>
            </details>
        `;
    }

    return html;
}

function _renderSessionNameStartDetails(details) {
    const summary = details.summary || 'Generating session name...';
    return `
        <div class="status-kv-grid">
            <div class="status-kv-key">Status</div>
            <div class="status-kv-value text-amber-400">In Progress...</div>
        </div>
    `;
}

function _renderSessionNameCompleteDetails(details) {
    const sessionName = details.session_name || 'Unknown';
    const inputTokens = details.input_tokens || 0;
    const outputTokens = details.output_tokens || 0;
    const costUsd = details.cost_usd;

    // Build cost HTML if cost is available
    let costHtml = '';
    if (costUsd !== null && costUsd !== undefined && costUsd > 0) {
        costHtml = `
            <div class="status-kv-key">Cost</div>
            <div class="status-kv-value text-amber-300">$${costUsd.toFixed(6)}</div>
        `;
    }

    return `
        <div class="status-kv-grid">
            <div class="status-kv-key">Name</div>
            <div class="status-kv-value"><code class="status-code text-emerald-300">${sessionName}</code></div>
            <div class="status-kv-key">Tokens</div>
            <div class="status-kv-value">${inputTokens} in / ${outputTokens} out</div>
            ${costHtml}
        </div>
    `;
}

function _renderNextStepCompleteDetails(details) {
    const count = (details.suggestion_count !== undefined && details.suggestion_count !== null)
        ? details.suggestion_count : 0;
    const inputTokens = details.input_tokens || 0;
    const outputTokens = details.output_tokens || 0;
    const costUsd = details.cost_usd;

    let costHtml = '';
    if (costUsd !== null && costUsd !== undefined && costUsd > 0) {
        costHtml = `
            <div class="status-kv-key">Cost</div>
            <div class="status-kv-value text-amber-300">$${costUsd.toFixed(6)}</div>
        `;
    }

    return `
        <div class="status-kv-grid">
            <div class="status-kv-key">Suggestions</div>
            <div class="status-kv-value"><code class="status-code text-emerald-300">${count} generated</code></div>
            <div class="status-kv-key">Tokens</div>
            <div class="status-kv-value">${inputTokens} in / ${outputTokens} out</div>
            ${costHtml}
        </div>
    `;
}

// ── Shared trust-card grammar ────────────────────────────────────────────────
// One color/shape language for the whole trust-card family (grounding, guardrail,
// obda, honesty, judge): headline verdicts render as the shared `trust-chip`
// (static/css/trust.css — token-driven, colorblind-safe shape+fill, correct in
// light AND dark), secondary values color via the same --trust-* tokens instead
// of hardcoded Tailwind classes.
const _TRUST_LEVEL_COLOR = {
    pass: 'var(--trust-pass, #34d399)',
    warn: 'var(--trust-warn, #fbbf24)',
    danger: 'var(--trust-danger, #f87171)',
    muted: 'var(--text-muted, #94a3b8)',
};

// The post-turn LLM calls booked through core/post_turn_usage (the model-gap proposal
// suggestion, the agent-memory extraction): what the call produced, on which model, and its
// tokens and cost — the same rows as the session-name and next-step calls, one renderer for all.
const POST_TURN_LLM_TYPES = ['proposal_suggestion_complete', 'proposal_suggestion', 'memory_write'];
function _renderPostTurnLlmCallDetails(details) {
    const d = details || {};
    const what = String(d.summary || '').replace(/\s*\([^()]*\bin \/[^()]*\)\s*$/, '');
    const model = [d.provider, d.model].filter(Boolean).join(' · ');
    const costUsd = Number(d.cost_usd);
    const costHtml = Number.isFinite(costUsd) && costUsd > 0 ? `
            <div class="status-kv-key">Cost</div>
            <div class="status-kv-value text-amber-300">$${costUsd.toFixed(6)}</div>` : '';
    return `
        <div class="status-kv-grid">
            ${what ? `<div class="status-kv-key">Result</div><div class="status-kv-value">${escapeHtml(what)}</div>` : ''}
            ${model ? `<div class="status-kv-key">Model</div><div class="status-kv-value"><code class="status-code">${escapeHtml(model)}</code></div>` : ''}
            <div class="status-kv-key">Tokens</div>
            <div class="status-kv-value">${d.input_tokens || 0} in / ${d.output_tokens || 0} out</div>
            ${costHtml}
        </div>
    `;
}

function _trustChip(level, label) {
    const cls = level === 'pass' ? 'tc-pass' : (level === 'warn' ? 'tc-warn'
        : (level === 'danger' ? 'tc-danger' : ''));
    return `<span class="trust-chip ${cls}">${label}</span>`;
}
function _trustVal(level) {
    return `style="color: ${_TRUST_LEVEL_COLOR[level] || _TRUST_LEVEL_COLOR.muted};"`;
}

function _renderJudgeEvalCompleteDetails(details) {
    // The LLM-judge evaluation of a turn (online sampling, grounding-gate judge, or
    // the manual "Score this turn" button). This step exists so the judge's token
    // cost is a VISIBLE, discrete event in Live Status that sums into the turn total
    // — cost transparency is a core USP. Monochrome status-kv card, reload-safe.
    const inputTokens = details.input_tokens || 0;
    const outputTokens = details.output_tokens || 0;
    const costUsd = details.cost_usd;
    const models = Array.isArray(details.judge_models) ? details.judge_models : [];

    let costHtml = '';
    if (costUsd !== null && costUsd !== undefined && costUsd > 0) {
        costHtml = `
            <div class="status-kv-key">Cost</div>
            <div class="status-kv-value" ${_trustVal('warn')}>$${costUsd.toFixed(6)}</div>
        `;
    }
    // WHICH judge layer this was. One event type serves both, and only one of them
    // could change what the user received — an in-path escalation's ruling can withhold
    // the answer, a sampled panel's cannot. Without this the two were indistinguishable
    // in the trace, though their consequence is opposite. Older events carry no layer:
    // they read as the norm rather than claiming a certainty the record does not have.
    const layer = details.layer === 'in_flight' ? 'in_flight'
        : details.layer === 'post_hoc' ? 'post_hoc' : null;
    const layerHtml = layer ? `
            <div class="status-kv-key">When</div>
            <div class="status-kv-value" ${_trustVal(layer === 'in_flight' ? 'warn' : 'na')}>${
                layer === 'in_flight'
                    ? 'during the turn — a gate consulted it; its ruling could withhold the answer'
                    : 'after the turn — measurement only; it changed nothing that shipped'
            }</div>
        ` : '';
    // #46 — the panel's COMPOSITION and its per-model spend, disclosed.
    //
    // "2 models" discarded the names at exactly the point composition starts to matter,
    // and the aggregate cost hid which member spent it. #39 is the same omission seen
    // from the other side: a panel of ONE renders identically to a real panel, so a
    // single small model's un-seconded opinion looks like a consensus.
    //
    // `judge_usage` rides the SAME persisted event as the aggregate, so this renders
    // identically live and on a plan reload; it is display detail INSIDE the one
    // cost-bearing event, never a second one (a second would double-count in
    // `_getTurnCost`). Absent (older turns) ⇒ the summary row alone, unchanged.
    const usage = Array.isArray(details.judge_usage) ? details.judge_usage : [];
    let modelsHtml = '';
    if (models.length > 0 || usage.length > 0) {
        const n = usage.length || models.length;
        const single = n === 1;
        const label = single
            ? String((usage[0] && (usage[0].model)) || models[0])
            : `${n} models`;
        // A single judge is not a panel. Say so, rather than let one opinion read as
        // a consensus (#39) — the drill-down would show it, but the summary must not
        // imply otherwise.
        const soloNote = single
            ? ` <span class="u-tip" data-tip="Only one judge was available, so there was no second opinion and no consensus to reach.\nAdd credentials for another provider family to widen the panel." style="opacity:.75;">· single judge</span>`
            : '';
        const rows = usage.map((u) => {
            const name = escapeHtml(String(u.model || u.provider || 'unknown'));
            const prov = u.provider && u.model ? `<span style="opacity:.6">${escapeHtml(String(u.provider))} · </span>` : '';
            const toks = `${(u.input_tokens || 0).toLocaleString()} in / ${(u.output_tokens || 0).toLocaleString()} out`;
            const cost = (typeof u.cost_usd === 'number')
                ? `$${u.cost_usd.toFixed(6)}`
                : '<span style="opacity:.6">cost unavailable</span>';
            const calls = (u.calls || 0) > 1 ? ` <span style="opacity:.6">×${u.calls}</span>` : '';
            return `<div style="display:flex;gap:8px;align-items:baseline;justify-content:space-between;padding:2px 0;">
                        <span>${prov}${name}${calls}</span>
                        <span style="opacity:.8;white-space:nowrap;">${toks} · ${cost}</span>
                    </div>`;
        }).join('');
        const disclosure = usage.length
            ? `<details style="margin-top:2px;">
                   <summary style="cursor:pointer;opacity:.8;font-size:.72rem;">per-model breakdown</summary>
                   <div style="margin-top:4px;font-size:.72rem;border-left:2px solid var(--border-secondary);padding-left:8px;">${rows}</div>
               </details>`
            : '';
        modelsHtml = `
            <div class="status-kv-key">Judge Panel</div>
            <div class="status-kv-value">${escapeHtml(String(label))}${soloNote}${disclosure}</div>
        `;
    }

    // The RESULTS face — the ruling this run actually produced, with the per-model
    // votes behind each consensus. Without it the card showed the panel and its bill
    // but not what it decided: a judgement call whose judgement was invisible.
    //
    // Structure (top-down, so the card reads in one pass):
    //   1. the TALLY — how the run came out overall ("2 pass · 1 fail"), first;
    //   2. one row per dimension — label left, then the FINAL consensus chip
    //      (abstained = honest na, never folded into pass), then one vote chip per
    //      model (✓ pass · ✗ fail · ? abstain — an abstaining judge is shown, never
    //      dropped: it is part of the composition the consensus rests on);
    //   3. the REASONING behind one disclosure (the same idiom as the per-model cost
    //      breakdown above), so the default card stays scannable.
    // `verdicts` rides the SAME persisted event → identical live and on reload;
    // absent (older turns) ⇒ section absent, card unchanged. Dimension names route
    // through QualityDimensions (one label source) and each row carries the Guide's
    // data-dim affordance, so a click explains what the dimension measures.
    const verdicts = Array.isArray(details.verdicts) ? details.verdicts : [];
    let verdictsHtml = '';
    if (verdicts.length) {
        const QD = (typeof window !== 'undefined') && window.QualityDimensions;
        const dimLabel = (k) => (QD && QD.label ? QD.label(k) : String(k || '?'));
        const nPass = verdicts.filter((v) => v.passed === true).length;
        const nFail = verdicts.filter((v) => v.passed === false).length;
        const nAbst = verdicts.length - nPass - nFail;
        // SUMMARY first: the tally alone is the default view. A FAIL is the one
        // thing that must be visible without a click, so failing dimensions are
        // NAMED on the summary line; everything else waits behind the disclosure.
        const failNames = verdicts.filter((v) => v.passed === false)
            .map((v) => dimLabel(v.dimension));
        const tally = [
            nPass ? _trustChip('pass', `${nPass} pass`) : '',
            nFail ? _trustChip('danger', `${nFail} fail`) : '',
            nAbst ? _trustChip('na', `${nAbst} abstained`) : '',
        ].filter(Boolean).join(' ')
            + (failNames.length
                ? ` <span style="opacity:.75;font-size:.72rem;">— ${escapeHtml(failNames.join(', '))}</span>`
                : '');
        // DETAILS on demand: one disclosure, compact fixed-layout rows that cannot
        // wrap into soup — the vote cluster is glyphs only (✓ ✗ ?), one per model,
        // each tooltip carrying the model's name, ruling and score. The consensus
        // reasoning rides the final chip's tooltip; the full per-judge reasoning
        // text sits in a nested disclosure for whoever wants the prose.
        const voteGlyph = (v) => {
            const glyph = v.verdict === 'pass' ? '✓' : v.verdict === 'fail' ? '✗' : '?';
            const cls = v.verdict === 'pass' ? 'tc-pass' : v.verdict === 'fail' ? 'tc-danger' : '';
            const tip = `${String(v.model || 'judge')} ruled ${v.verdict}`
                + (typeof v.score === 'number' ? ` (score ${v.score})` : '')
                + (v.verdict === 'unknown' ? ' — abstained (no ruling, not a pass)' : '');
            return `<span class="trust-chip ${cls} u-tip u-tip-left" data-tip="${escapeHtml(tip)}"
                style="font-size:.68rem;padding:0 5px;min-width:18px;text-align:center;">${glyph}</span>`;
        };
        const rows = verdicts.map((v) => {
            const finalTip = v.explanation ? escapeHtml(String(v.explanation)) : '';
            const final = v.passed === true ? _trustChip('pass', 'pass')
                : v.passed === false ? _trustChip('danger', 'fail')
                : _trustChip('na', 'abstained');
            const finalCell = finalTip
                ? `<span class="u-tip u-tip-left" data-tip="${finalTip}">${final}</span>` : final;
            const votes = Array.isArray(v.votes) && v.votes.length
                ? `<span style="display:inline-flex;gap:3px;">${v.votes.map(voteGlyph).join('')}</span>`
                : '';
            return `<div style="display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:8px;align-items:center;padding:3px 0;">
                        <span class="u-tip" data-dim="${escapeHtml(String(v.dimension || ''))}"
                              data-tip="Click to read what this dimension measures."
                              style="opacity:.85;cursor:pointer;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escapeHtml(dimLabel(v.dimension))}</span>
                        ${votes || '<span></span>'}
                        ${finalCell}
                    </div>`;
        }).join('');
        const reasons = verdicts.filter((v) => v.explanation).map((v) =>
            `<div style="padding:2px 0;"><span style="opacity:.85;">${escapeHtml(dimLabel(v.dimension))}</span>
                 <span style="opacity:.65;"> — ${escapeHtml(String(v.explanation))}</span></div>`).join('');
        const reasoning = reasons
            ? `<details style="margin-top:3px;">
                   <summary style="cursor:pointer;opacity:.75;font-size:.7rem;">per-judge reasoning</summary>
                   <div style="margin-top:3px;font-size:.7rem;">${reasons}</div>
               </details>`
            : '';
        verdictsHtml = `
            <div class="status-kv-key">Verdicts</div>
            <div class="status-kv-value" style="font-size:.74rem;">
                <div>${tally}</div>
                <details style="margin-top:2px;">
                    <summary style="cursor:pointer;opacity:.8;font-size:.72rem;">per-dimension verdicts &amp; votes</summary>
                    <div style="margin-top:4px;font-size:.72rem;border-left:2px solid var(--border-secondary);padding-left:8px;">
                        ${rows}${reasoning}
                    </div>
                </details>
            </div>
        `;
    } else if (inputTokens > 0 || outputTokens > 0 || (costUsd > 0)) {
        // A panel that SPENT but produced no recorded ruling. The card used to omit the
        // Verdicts row entirely here, so it announced "LLM judge panel · N models" over a
        // real bill with nothing behind it — a spend wearing a judgement's clothes, and an
        // empty per-model disclosure as the only hint. Absence is a finding: say it.
        verdictsHtml = `
            <div class="status-kv-key">Verdicts</div>
            <div class="status-kv-value" style="font-size:.74rem;">
                ${_trustChip('na', 'no ruling recorded')}
                <span style="opacity:.75;font-size:.72rem;"> — the panel did not return a verdict before the evaluation window closed. The tokens above were still spent.</span>
            </div>
        `;
    }

    // The headline follows the same rule: it may claim a panel RULED only when the
    // record holds a ruling. Otherwise it names what actually happened.
    const ruled = verdicts.length > 0;
    const headline = ruled
        ? _trustChip('pass', 'LLM judge panel')
        : _trustChip('na', 'LLM judge panel — no ruling');

    return `
        <div class="status-kv-grid">
            <div class="status-kv-key">Evaluation</div>
            <div class="status-kv-value">${headline}</div>
            ${layerHtml}
            ${modelsHtml}
            ${verdictsHtml}
            <div class="status-kv-key">Tokens</div>
            <div class="status-kv-value">${inputTokens} in / ${outputTokens} out</div>
            ${costHtml}
        </div>
    `;
}


// A trust card names a MECHANISM — "Grounding Gate", "Guardrail", "Honesty Check". Under
// the rule that any item an instrument lists must be lookupable, each of those headlines
// has to reach the Assurance Guide. They did not: only the Quality card's dimension chips
// did, so a reader could look up `answer_groundedness` but not the gate that uses it.
//
// Uses the SAME `data-dim` affordance as a dimension chip, because the Guide now holds
// mechanisms too — one channel, no second route (contract Rule 3b.3).
function _mechKey(details, fallback) {
    // The guardrail card renders BOTH directions; the P1 stamp says which.
    if (fallback === 'guardrail') {
        return (details && details.direction) === 'inbound' ? 'inbound_guard' : 'outbound_guard';
    }
    return fallback;
}
// The station a mechanism serves. A card that reports a gate must say WHICH GUARANTEE
// that gate upholds — otherwise the reader has a finding with no frame, and the ring
// above it is a separate picture rather than the same story at another scale.
const _MECH_STATION = {
    grounding_gate: 'grounded', honesty_gate: 'grounded', preflight_sql: 'grounded',
    corpus_quarantine: 'grounded', trusted_scope: 'grounded',
    inbound_guard: 'guarded', outbound_guard: 'guarded', retrieval_firewall: 'guarded',
    authority: 'bounded', tool_enforcement: 'bounded', sensitivity_ceiling: 'bounded',
    // `governed_data` is GROUNDED — its AssurancePoint, its dimension partition and
    // the dial all say so; this map alone said JUDGED (found by the W5 survey).
    governed_data: 'grounded', evaluation: 'scored',
    review_parking: 'reviewed', needs_input: 'reviewed', provenance_seal: 'proven', agent_integrity: 'signed',
};
const _stationLink = (mechKey) => {
    const st = _MECH_STATION[mechKey];
    if (!st) return '';
    // Display label for the station — `scored` reads JUDGED (see trustCircle.js
    // STATIONS; the wire key never moves). No native `title`: a [data-dim] element
    // already gets the Guide's own hover, and two tooltips on one chip was the exact
    // defect fixed on the gate rows.
    const stLabel = (window.TrustGrammar && window.TrustGrammar.stationLabel)
        ? window.TrustGrammar.stationLabel(st) : st.toUpperCase();
    return ` <span class="status-kv-station" ${_dimAttrs(`station_${st}`)} role="button"
        >${stLabel}</span>`;
};

// A trust card's links opened the Guide unscoped — all 20 entries, with no sign of which
// one the card was about. The card knows its own subject, so it stamps it: the mechanism,
// the station it serves, and the dimensions that measure it, carrying whatever live bands
// this turn already recorded on the Quality card (one source for those, never a second
// reading). Best-effort throughout — a scope is an affordance, never a precondition.
function _stampTrustGuideContext(stepEl, type, details) {
    try {
        const QD = _QD();
        if (!QD.setContext || !stepEl) return;
        const mech = _mechKey(details, type);
        if (!mech) return;
        const station = _MECH_STATION[mech];
        // The turn's scored bands, read from the Quality card's own context if it has
        // rendered — so the card can never disagree with the card above it.
        let bands = {};
        try {
            const raw = (document.querySelector('#trajectory-eval-overlay[data-dim-context]')
                || {}).getAttribute
                ? document.querySelector('#trajectory-eval-overlay[data-dim-context]')
                    .getAttribute('data-dim-context') : '';
            (JSON.parse(raw || '[]') || []).forEach((r) => { if (r && r.k) bands[r.k] = r.b; });
        } catch (_) { bands = {}; }
        const items = [{ key: mech }];
        if (station) items.push({ key: `station_${station}` });
        (QD.relatedOf ? QD.relatedOf(mech) : []).forEach((d) => {
            // Only dimensions this turn actually scored. A dimension with no verdict would
            // land in the "Not applicable" bucket and read as a finding about this answer.
            if (bands[d]) items.push({ key: d, band: bands[d] });
        });
        QD.setContext(stepEl, items, `${QD.label ? QD.label(mech) : mech} · this turn`);
    } catch (_) { /* scoping is best-effort — never break the trace */ }
}

const _mechLink = (key) => `<span class="status-kv-mech" ${_dimAttrs(key)} role="button"
    title="What this is, and how it is derived — opens the Assurance Guide">${_QD().label
        ? _QD().label(key) : key}</span>${_stationLink(key)}`;

// An unsupported "claim" is a span lifted verbatim from the answer, so it is usually a
// sentence — often a mid-sentence fragment carrying the markdown that surrounded it. It
// was rendered in <code>, which told the reader to look for a schema name that was never
// there ("technologies. Just as the **Uderia Platform** uses a"). Present it as the quoted
// prose it is: emphasis stripped, whitespace collapsed, ellipsised — and monospace ONLY
// when the span really is a single identifier.
function _claimHtml(raw) {
    const txt = String(raw == null ? '' : raw)
        .replace(/\*\*|__|`/g, '')
        .replace(/\s+/g, ' ')
        .trim();
    if (!txt) return '';
    const ident = /^[^\s]+$/.test(txt);
    const shown = txt.length > 110 ? `${txt.slice(0, 110).trimEnd()}…` : txt;
    return ident
        ? `<span class="trust-claim trust-claim-ident">${escapeHtml(shown)}</span>`
        : `<span class="trust-claim">\u201c${escapeHtml(shown)}\u201d</span>`;
}

/**
 * Should this trust card be OMITTED from the trace? (Operator-approved R-2 closeout.)
 * Principle: the trace records what HAPPENED; the dial reports what IS.
 *   · `obda` — always: the governed-data figure is a state reading, the dial's JUDGED
 *     station carries it; a per-turn card restated it as noise.
 *   · `grounding_gate` on a CLEAN pass — nothing happened: no finding, no judge, no
 *     act. The stamp (and the dial's GROUNDED segment) still record the clean run;
 *     a card saying "nothing to report" was the dial's job wearing a step's clothes.
 * A judge escalation, any finding, or any acting mode keeps the card — those are
 * events. The dial note MUST still fire for suppressed cards (the instrument moves
 * even when the trace stays quiet), so callers suppress AFTER noting.
 */
function _assuranceCardSuppressed(type, details) {
    if (type === 'obda') return true;
    if (type !== 'grounding_gate') return false;
    const d = details || {};
    const clean = (d.decision || 'pass') === 'pass'
        && !d.judged && !d.needs_judge
        && !(d.unsupported_claims || []).length
        && !(d.out_of_scope_dbs || []).length
        && !(d.answer_oos_refs || []).length
        && !(d.second_chance_cleared || []).length;
    return clean;
}

function _renderGroundingGateDetails(details) {
    // Tier 1.1 grounding gate verdict — live + reload. Monochrome status-kv card,
    // matching the sibling status renderers. decision: pass | annotate | block.
    // Judge-backed enforce: in enforce the scope-faithfulness LLM judge makes the call,
    // recorded in `reason` (drifted/faithful/unavailable); `answer_oos_refs` are out-of-scope
    // identifiers the recall-first trigger found in the ANSWER (distinct from tool calls).
    const decision = (details.decision || 'pass').toLowerCase();
    const reason = (details.reason || '').toLowerCase();
    const judged = reason.includes('judge');
    const judgeDrifted = reason.includes('drifted');
    const judgeFaithful = reason.includes('faithful');
    const judgeUnavailable = reason.includes('unavailable');
    const ground = (details.ground || 'scope');
    const corpusOnly = ground === 'corpus';
    // In observe mode the decision is `pass` even when nothing was supported — the gate
    // measured and chose not to act. Labelling the chip from the decision alone therefore
    // printed a green "Corpus-supported" directly above "0/1 claim(s) supported": the
    // headline and its own evidence contradicting each other, which is the defect this
    // card family exists to prevent. The headline now reflects the FINDING, and the
    // decision is what it says about acting.
    const _unsupported = (details.unsupported_claims || []).length;
    const decLevel = decision === 'block' ? 'danger'
                   : (decision === 'annotate' || _unsupported ? 'warn' : 'pass');
    const decLabel = decision === 'block'
                   ? (corpusOnly ? 'Withheld — unsupported by the knowledge base' : 'Withheld — out of scope')
                   : (decision === 'annotate' ? 'Flagged'
                   : (_unsupported
                      // Why the gate did not act on what it found. "observed" was printed
                      // for every pass, which read as observe mode on an agent set to
                      // disclose — when the real reason was the trigger's threshold.
                      ? (reason.startsWith('observe')
                         ? `${_unsupported} claim(s) unsupported — observe mode, not disclosed`
                         : `${_unsupported} claim(s) unsupported — below the trigger threshold, not disclosed`)
                      : (corpusOnly ? 'Corpus-supported' : 'In scope')));
    const isReview = (details.mode || '') === 'review';
    const scope = (details.scope_databases || []).map(escapeHtml).join(', ') || '—';
    const oos = (details.out_of_scope_dbs || []).map(escapeHtml);
    const answerRefs = (details.answer_oos_refs || []).map(escapeHtml);
    const nf = details.numeric_fidelity;
    const groundLabel = ground === 'both' ? 'scope + corpus' : ground;
    let rows = `
            <div class="status-kv-key">${_mechLink('grounding_gate')}</div>
            <div class="status-kv-value"><span class="trust-chip-row">${_trustChip(decLevel, decLabel)}<span class="trust-chip u-tip" data-tip="Which trigger family produced findings this turn:\nscope = KG-declared data scope · corpus = retrieved knowledge support">${escapeHtml(groundLabel)}</span>${isReview && decision === 'block' ? _trustChip('warn', 'parked for review') : ''}</span></div>`;
    if (!corpusOnly) {
        rows += `
            <div class="status-kv-key">Declared scope</div>
            <div class="status-kv-value text-gray-300">${scope}</div>`;
    }
    if (oos.length) {
        rows += `
            <div class="status-kv-key">Out of scope (tools)</div>
            <div class="status-kv-value" ${_trustVal('warn')}>${oos.join(', ')}</div>`;
    }
    if (answerRefs.length) {
        rows += `
            <div class="status-kv-key">Out of scope (answer)</div>
            <div class="status-kv-value" ${_trustVal('warn')}>${answerRefs.join(', ')}</div>`;
    }
    // ── corpus-support family (RAG Trust Hardening Wave 1, U1): unsupported spans
    // named verbatim + second-chance status — never invisible annotations.
    const claims = (details.unsupported_claims || []).map(escapeHtml);
    const cleared = (details.second_chance_cleared || []).map(escapeHtml);
    const claimsChecked = details.claims_checked || 0;
    if (claimsChecked > 0 || claims.length || cleared.length) {
        const supported = Math.max(0, claimsChecked - claims.length);
        rows += `
            <div class="status-kv-key">Corpus support</div>
            <div class="status-kv-value" ${_trustVal(claims.length ? 'warn' : 'pass')}>${supported}/${claimsChecked} claim(s) supported by the retrieved knowledge</div>`;
        if (claims.length) {
            rows += `
            <div class="status-kv-key">Unsupported claims</div>
            <div class="status-kv-value u-tip" data-tip="Unsupported by the retrieved knowledge AFTER a targeted second-chance search of the corpus.\nUnsupported ≠ false — it means the knowledge base does not carry it." ${_trustVal('warn')}><span class="trust-claim-list">${claims.slice(0, 6).map(_claimHtml).join('')}</span>${claims.length > 6 ? `<span class="trust-claim">+${claims.length - 6} more</span>` : ''}</div>`;
        }
        if (cleared.length) {
            rows += `
            <div class="status-kv-key">Second-chance cleared</div>
            <div class="status-kv-value u-tip" data-tip="These spans were NOT in the first retrieval but a targeted corpus search found support — cleared, not flagged (honest generosity)." ${_trustVal('pass')}><span class="trust-claim-list">${cleared.slice(0, 6).map(_claimHtml).join('')}</span></div>`;
        }
    }
    if (judged) {
        const isCorpusJudge = (details.judge_family || '') === 'groundedness'
            || reason.includes('unsupported by the knowledge base');
        const jLabel = judgeDrifted ? 'Drifted — answer withheld'
                     : (reason.includes('unsupported by the knowledge base') ? 'Unsupported — answer withheld'
                     : (judgeFaithful ? 'Faithful — delivered'
                     : (judgeUnavailable ? 'Unavailable — annotated (fail-safe)' : 'Adjudicated')));
        const jLevel = (judgeDrifted || reason.includes('unsupported by the knowledge base'))
            ? 'danger' : (judgeFaithful ? 'pass' : 'warn');
        rows += `
            <div class="status-kv-key">${isCorpusJudge ? 'Groundedness judge' : 'Scope judge'}</div>
            <div class="status-kv-value" ${_trustVal(jLevel)}>${jLabel}</div>`;
    }
    if (nf !== null && nf !== undefined) {
        const pct = Math.round(nf * 100);
        const nfLevel = pct >= 90 ? 'pass' : (pct >= 70 ? 'warn' : 'danger');
        rows += `
            <div class="status-kv-key">Numeric fidelity</div>
            <div class="status-kv-value" ${_trustVal(nfLevel)}>${pct}%</div>`;
    }
    return `<div class="status-kv-grid">${rows}</div>`;
}

// ── trust vocabulary: guardrail finding categories ───────────────────────────
// ONE source for the human name of a guardrail category. The Live-Status card and the
// Assurance Inbox both render these, and the inbox is a plain script that cannot import
// this module synchronously — so it is published on `window` rather than duplicated
// (#52: the inbox printed the raw wire token `prompt_injection` at users).
export const TRUST_CATEGORY_LABEL = {
    pii: 'PII', toxicity: 'Toxicity', prompt_injection: 'Prompt injection',
    exfil_url: 'Exfiltration-shaped URL',
};
try { window.TrustVocab = Object.assign(window.TrustVocab || {}, { category: TRUST_CATEGORY_LABEL }); }
catch (_) { /* non-browser context (the grammar gate) — the export is enough */ }

function _renderGuardrailDetails(details) {
    // Tier 1.3 P2 guardrail verdict — live + reload. Monochrome status-kv card, sibling of
    // the grounding card. action: pass | annotate | redact | block. In observe, action stays
    // `pass` but findings are present (detected, not altered). findings carry category LABELS
    // only — never raw PII values (privacy invariant). categories: pii | toxicity | prompt_injection.
    const action = (details.action || 'pass').toLowerCase();
    const findings = Array.isArray(details.findings) ? details.findings : [];
    const cats = Array.isArray(details.categories) ? details.categories : [];
    const observeDetected = action === 'pass' && findings.length > 0;
    const actLevel = action === 'block' ? 'danger'
                   : (action === 'redact' || action === 'annotate' || observeDetected ? 'warn' : 'pass');
    const actLabel = action === 'block' ? 'Withheld'
                   : action === 'redact' ? 'PII redacted'
                   : action === 'annotate' ? 'Flagged'
                   : (observeDetected ? 'Detected (observe)' : 'Clean');
    const isReview = (details.mode || '') === 'review';
    // Group finding labels by category for a compact, value-free summary.
    const byCat = {};
    findings.forEach(f => {
        const c = (f.category || 'other'); (byCat[c] = byCat[c] || []).push(escapeHtml(f.label || ''));
    });
    const catLabel = TRUST_CATEGORY_LABEL;
    let rows = `
            <div class="status-kv-key">${_mechLink(_mechKey(details, 'guardrail'))}</div>
            <div class="status-kv-value">${_trustChip(actLevel, actLabel)}${isReview && (action === 'redact' || action === 'block') ? ' ' + _trustChip('warn', 'parked for review') : ''}</div>`;
    (cats.length ? cats : Object.keys(byCat)).forEach(c => {
        const labels = (byCat[c] || []);
        if (!labels.length) return;
        rows += `
            <div class="status-kv-key">${escapeHtml(catLabel[c] || c)}</div>
            <div class="status-kv-value" ${_trustVal('warn')}>${[...new Set(labels)].join(', ')}</div>`;
    });
    return `<div class="status-kv-grid">${rows}</div>`;
}

function _renderObdaDetails(details) {
    // OBDA-READ — governed-data lane (OBDA_VISION.md §5). Monochrome status-kv card, sibling of the
    // grounding/guardrail cards. Shows which of the turn's data operations went through the
    // GOVERNED, deterministic lane (a metric compiled to ratio-safe SQL) vs FREE-FORMED raw SQL
    // the agent wrote itself. Shows BOTH lanes — never only the good path.
    const gov = details.governed_count || 0;
    const free = details.freeform_count || 0;
    const pct = (typeof details.governed_pct === 'number') ? details.governed_pct : 0;
    const lane = (details.lane || 'mixed').toLowerCase();
    const ops = Array.isArray(details.operations) ? details.operations : [];
    const laneLevel = lane === 'governed' ? 'pass' : (lane === 'free-form' ? 'muted' : 'warn');
    const laneLabel = lane === 'governed' ? 'Governed (deterministic)'
                    : lane === 'free-form' ? 'Free-formed' : 'Mixed';
    let rows = `
            <div class="status-kv-key">${_mechLink('governed_data')}</div>
            <div class="status-kv-value">${_trustChip(laneLevel, `${laneLabel} · ${pct}% governed`)}</div>
            <div class="status-kv-key">Operations</div>
            <div class="status-kv-value text-gray-300">${gov} governed · ${free} free-formed</div>`;
    ops.forEach(op => {
        const metric = escapeHtml(op.metric || 'metric');
        const target = escapeHtml(op.deployed || op.realization || '');
        rows += `
            <div class="status-kv-key">Compiled</div>
            <div class="status-kv-value" ${_trustVal('pass')}>${metric}${target ? ` <span class="text-gray-400">&rarr; ${target}</span>` : ''}</div>`;
    });
    return `<div class="status-kv-grid">${rows}</div>`;
}

// PROP-006 — the turn's sources on the Number Lineage card: which statement came from which
// data source (by name) and when, and what each combine read. `sources` is present ONLY on a
// turn that read more than one source or combined — a single-source card is unchanged.
function _computeSourcesRows(src) {
    if (!src || typeof src !== 'object') return '';
    const when = (ts) => { if (!ts) return ''; const t = new Date(ts); return isNaN(t) ? String(ts) : t.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' }); };
    const stList = (ns) => (ns || []).map(n => `statement ${escapeHtml(String(n))}`).join(', ');
    let rows = '';
    (Array.isArray(src.sources) ? src.sources : []).forEach(x => {
        rows += `
            <div class="status-kv-key">Source</div>
            <div class="status-kv-value" style="color:var(--text-primary);">${escapeHtml(String(x.name || x.source_id || 'not recorded'))} <span style="color:var(--text-muted);">— ${stList(x.statements)} · ${escapeHtml(Number(x.rows || 0).toLocaleString('en-US'))} rows</span></div>`;
    });
    (Array.isArray(src.combines) ? src.combines : []).forEach(c => {
        const ins = (c.inputs || []).map(i => `statement ${escapeHtml(String(i.n))} (${escapeHtml(String(i.source_name || i.source_id || 'combined earlier'))}${i.ts ? `, read ${escapeHtml(when(i.ts))}` : ''})`).join(' + ');
        rows += `
            <div class="status-kv-key">Combined results</div>
            <div class="status-kv-value" ${_trustVal('pass')}>statement ${escapeHtml(String(c.n))} · ${escapeHtml(Number(c.row_count || 0).toLocaleString('en-US'))} rows <span style="color:var(--text-muted);">&larr; ${ins || 'no inputs recorded'} — joined by code, not by the model</span></div>`;
    });
    return rows;
}

function _renderComputeLineageDetails(details) {
    // S3 — Number lineage. ONE assessment of the delivered answer's figures: computed with
    // a receipt, quoted from a result row, or neither (the agent's own arithmetic). The
    // card names the receipts (SUM(miles) · 149 rows · statement 2) and the figures they
    // vouch for; an unreceipted figure is named, never called wrong. Status-kv idiom.
    const d = details || {};
    const rec = Array.isArray(d.receipted) ? d.receipted : [];
    const quo = Array.isArray(d.quoted) ? d.quoted : [];
    // a figure the agent wrote INTO a statement (a filter literal, a bound) or a code the
    // graph declares is traceable — the turn's input, never its arithmetic (Sep 2026)
    const lit = Array.isArray(d.literals) ? d.literals : [];
    const dec = Array.isArray(d.declared) ? d.declared : [];
    // a figure DERIVED from a result column (its sum or average over the complete rows) is
    // traced; a near miss of one stays unreceipted and names the value the rows give
    const der = Array.isArray(d.derived) ? d.derived : [];
    const near = Array.isArray(d.misderived) ? d.misderived : [];
    const unr = Array.isArray(d.unreceipted) ? d.unreceipted : [];
    const figs = d.figures || 0;
    let level = 'muted', word = 'nothing to trace';
    if (d.decision === 'pass') { level = 'pass'; word = figs ? 'every figure traced' : 'no figures stated'; }
    else if (d.decision === 'note') { level = 'warn'; word = `${unr.length} of ${figs} figure(s) unreceipted`; }
    let rows = `
            <div class="status-kv-key">${_mechLink('compute_lineage')}</div>
            <div class="status-kv-value">${_trustChip(level, word)}</div>
            <div class="status-kv-key">Figures</div>
            <div class="status-kv-value text-gray-300">${rec.length} computed with a receipt · ${quo.length} quoted from the data${lit.length ? ` · ${lit.length} from the statement` : ''}${dec.length ? ` · ${dec.length} declared in the graph` : ''}${der.length ? ` · ${der.length} derived from a column` : ''}${unr.length ? ` · ${unr.length} unreceipted` : ''}${d.sealed ? ` · ${d.sealed} receipt(s) sealed` : ''}</div>`;
    const seen = new Set();
    rec.forEach(r => {
        const key = String(r.lineage || '');
        if (seen.has(key)) return; seen.add(key);
        rows += `
            <div class="status-kv-key">Computed</div>
            <div class="status-kv-value" ${_trustVal('pass')}>${escapeHtml(String(r.figure))} <span class="text-gray-400">&larr; ${escapeHtml(key)}</span></div>`;
    });
    lit.concat(dec).forEach(l => {
        rows += `
            <div class="status-kv-key">${lit.includes(l) ? 'From the statement' : 'Declared'}</div>
            <div class="status-kv-value" ${_trustVal('pass')}>${escapeHtml(String(l.figure))} <span class="text-gray-400">&larr; ${escapeHtml(String(l.lineage || ''))}</span></div>`;
    });
    der.forEach(g => {
        rows += `
            <div class="status-kv-key">Derived</div>
            <div class="status-kv-value" ${_trustVal('pass')}>${escapeHtml(String(g.figure))} <span class="text-gray-400">&larr; ${escapeHtml(String(g.lineage || ''))}</span></div>`;
    });
    near.forEach(m => {
        rows += `
            <div class="status-kv-key">Close, not equal</div>
            <div class="status-kv-value" ${_trustVal('warn')}>${escapeHtml(String(m.figure))} <span class="text-gray-400">&mdash; ${escapeHtml(String(m.lineage || ''))} is ${escapeHtml(Number(m.value).toLocaleString('en-US', { maximumFractionDigits: 2 }))}</span></div>`;
    });
    if (unr.length) {
        rows += `
            <div class="status-kv-key">Unreceipted</div>
            <div class="status-kv-value" ${_trustVal('warn')}>${escapeHtml(unr.slice(0, 6).join(', '))}${unr.length > 6 ? ' …' : ''} <span class="text-gray-400">— the agent's own arithmetic${d.disclosure_appended ? ', disclosed in the answer' : ' (recorded only)'}</span></div>`;
    }
    rows += _computeSourcesRows(d.sources);
    return `<div class="status-kv-grid">${rows}</div>`;
}

function _renderLlmRecoveryDetails(details) {
    // S4 — one recovery act (a retry, a continuation, a fallback, a refusal) as a Live
    // Status step: what failed, what was done, who served — and, for a fallback, that the
    // cost was billed at the model that served. Facts, never a verdict; status-kv idiom.
    const d = details || {};
    const kind = d.kind || 'recovery';
    const level = kind === 'fallback' ? 'warn' : (kind === 'refusal' ? 'warn' : 'pass');
    const word = kind === 'retry' ? `retried (${d.reason || '?'})`
        : kind === 'continuation' ? `continued (${d.reason || 'output limit'})`
        : kind === 'fallback' ? `served by the signed fallback`
        : kind === 'refusal' ? `provider refused (${d.reason || '?'})` : kind;
    let rows = `
            <div class="status-kv-key">${_mechLink('llm_recovery')}</div>
            <div class="status-kv-value">${_trustChip(level, word)}</div>
            <div class="status-kv-key">What happened</div>
            <div class="status-kv-value text-gray-300">${escapeHtml(String(d.summary || ''))}</div>`;
    if (kind === 'fallback') {
        rows += `
            <div class="status-kv-key">Served by</div>
            <div class="status-kv-value" ${_trustVal('warn')}>${escapeHtml(String(d.to || '?'))} <span class="text-gray-400">— instead of ${escapeHtml(String(d.from || '?'))}; a declared, temporary deployment drift for this turn</span></div>`;
    } else if (kind === 'retry') {
        rows += `
            <div class="status-kv-key">Wait</div>
            <div class="status-kv-value text-gray-300">${Number(d.delay || 0).toFixed(1)}s before attempt ${escapeHtml(String(d.attempt || '?'))}${d.status ? ` · HTTP ${escapeHtml(String(d.status))}` : ''}</div>`;
    }
    if (d.detail) {
        rows += `
            <div class="status-kv-key">Provider said</div>
            <div class="status-kv-value text-gray-400">${escapeHtml(String(d.detail).slice(0, 200))}</div>`;
    }
    return `<div class="status-kv-grid">${rows}</div>`;
}

function _renderOutcomeRevisionDetails(details) {
    // S7 — one revision of the draft: what the gate found, whether the revision cleared it, the
    // revision's own cost. Cleared = delivered clean (pass); not cleared = the honest annotated
    // first draft was delivered (warn) — never a second silent try.
    const d = details || {};
    const clearedFlag = !!d.cleared;
    const before = Array.isArray(d.findings_before) ? d.findings_before : [];
    const after = Array.isArray(d.findings_after) ? d.findings_after : [];
    const fmt = (fs) => fs.map(f => `<span class="ts-chip ${f.kind === 'out_of_scope_ref' ? 'ts-denied' : 'ts-held'}">${escapeHtml(String(f.kind || '').replace(/_/g, ' '))}: ${escapeHtml((f.spans || []).slice(0, 3).join(' · '))}${(f.spans || []).length > 3 ? ` +${f.spans.length - 3}` : ''}</span>`).join(' ');
    let rows = `
            <div class="status-kv-key">${_mechLink('outcome_loop')}</div>
            <div class="status-kv-value">${_trustChip(clearedFlag ? 'pass' : 'warn', clearedFlag ? `revision ${d.revision || 1} cleared the finding` : `revision ${d.revision || 1} did not clear it`)}</div>
            <div class="status-kv-key">What happened</div>
            <div class="status-kv-value text-gray-300">${escapeHtml(String(d.summary || ''))}</div>`;
    if (before.length) {
        rows += `
            <div class="status-kv-key">Found in the draft</div>
            <div class="status-kv-value ts-chips">${fmt(before)}</div>`;
    }
    rows += `
            <div class="status-kv-key">After the revision</div>
            <div class="status-kv-value ${after.length ? 'ts-chips' : 'text-gray-300'}">${after.length ? fmt(after) : 'no revisable finding remains'}</div>`;
    if (d.tokens_in != null || d.cost_usd != null) {
        rows += `
            <div class="status-kv-key">Revision cost</div>
            <div class="status-kv-value text-gray-300">${escapeHtml(String(d.tokens_in || 0))} in · ${escapeHtml(String(d.tokens_out || 0))} out${d.cost_usd != null ? ` · $${Number(d.cost_usd || 0).toFixed(5)}` : ''}</div>`;
    }
    return `<div class="status-kv-grid">${rows}</div>`;
}

function _renderSteerConsumedDetails(details) {
    // S10 — a steer the operator sent while the agent worked, and where the agent read it.
    const d = details || {};
    const list = Array.isArray(d.steers) ? d.steers : [];
    const when = d.carried ? 'arrived after the last step — delivered at the start of the next turn'
        : (d.at === 'turn_start' ? 'considered at the start of this turn' : `considered with the result of ${d.at || 'the next step'}`);
    return `<div class="status-kv-grid">
            <div class="status-kv-key">Steer</div>
            <div class="status-kv-value text-gray-300">${escapeHtml(when)}</div>
            ${list.map(x => `<div class="status-kv-key">Said</div><div class="status-kv-value steer-said"><em>${escapeHtml(String(x.text || ''))}</em></div>`).join('')}
        </div>`;
}

function _renderAttachmentSourcesDetails(details) {
    // S9 — what the platform READ from each attachment: verified (OCR / PDF tables / CSV /
    // text — the grounding gate holds the answer to it), unverified (the model's own
    // transcription — recorded, never evidence), or unread (the model saw it natively only).
    const d = details || {};
    const srcs = Array.isArray(d.sources) ? d.sources : [];
    const usable = (x) => (Array.isArray(x.tables) && x.tables.length) || Number(x.text_chars || 0) > 0;
    const verified = srcs.filter(x => x.verified && usable(x));
    const unverified = srcs.filter(x => !x.verified && usable(x));
    const unread = srcs.filter(x => !usable(x));
    const level = unverified.length ? 'warn' : (verified.length ? 'pass' : 'na');
    const word = unverified.length ? `${unverified.length} transcribed, unverified`
        : (verified.length ? `${verified.length} read by the platform, verified` : 'nothing could be read');
    const what = (x) => {
        const t = Array.isArray(x.tables) ? x.tables : [];
        const rows = t.reduce((a, tt) => a + Number(tt.row_count || 0), 0);
        return t.length ? `${t.length} table${t.length === 1 ? '' : 's'} · ${rows} row${rows === 1 ? '' : 's'}` : `${Number(x.text_chars || 0)} chars of text`;
    };
    const chip = (x, cls) => {
        const m = String(x.method || 'none').replace(/_/g, ' ');
        const eng = x.engine && x.engine !== 'model' ? ` (${x.engine})` : '';
        const tip = usable(x) ? `${m}${eng} · ${what(x)}${x.statement_ns && x.statement_ns.length ? ` · result set ${x.statement_ns.join(', ')}` : ''}` : String(x.note || 'not readable');
        return `<span class="ts-chip ${cls} u-tip" data-tip="${escapeHtml(tip)}">${escapeHtml(String(x.filename || x.file_id || ''))}</span>`;
    };
    let rows = `
            <div class="status-kv-key">${_mechLink('attachment_sources')}</div>
            <div class="status-kv-value">${_trustChip(level, word)}</div>`;
    if (verified.length) {
        rows += `
            <div class="status-kv-key">Verified</div>
            <div class="status-kv-value ts-chips">${verified.map(x => chip(x, 'ts-offered')).join('')}</div>`;
    }
    if (unverified.length) {
        rows += `
            <div class="status-kv-key">Transcribed by the model</div>
            <div class="status-kv-value ts-chips">${unverified.map(x => chip(x, 'ts-held')).join('')}</div>`;
    }
    if (unread.length) {
        rows += `
            <div class="status-kv-key">Not readable</div>
            <div class="status-kv-value ts-chips">${unread.map(x => chip(x, 'ts-denied')).join('')}</div>`;
    }
    rows += `
            <div class="status-kv-key">What it means</div>
            <div class="status-kv-value text-gray-300">${escapeHtml(unverified.length
                ? 'A transcription the model wrote is not evidence for the model\'s own answer — the grounding check does not hold the answer to it, and the answer may rest on it.'
                : (verified.length ? 'The grounding check holds the answer to what was read; a figure quoted from a table traces to the file and its region; the compute tool can operate on the rows.'
                    : 'No extractor could read the attachment here; the model saw it natively and nothing in it is vouched for.'))}</div>`;
    return `<div class="status-kv-grid">${rows}</div>`;
}

function _renderToolSurfaceDetails(details) {
    // S6 — the Governed Tool Surface: offered · held · denied · found, each name hoverable for
    // the reason it is where it is. A denied tool the model asked for is the named finding.
    const d = details || {};
    const c = d.counts || {};
    const reasons = d.reasons || {};
    const asked = Array.isArray(d.asked_denied) ? d.asked_denied : [];
    const level = asked.length ? 'warn' : 'pass';
    const word = asked.length ? `asked for a denied tool` : `offered ${c.offered || 0} of ${c.total || 0}`;
    const chips = (names, cls) => (names || []).map(n =>
        `<span class="ts-chip ${cls} u-tip" tabindex="0" data-tip="${escapeHtml(String(reasons[n] || ''))}" aria-label="${escapeHtml(String(n))}: ${escapeHtml(String(reasons[n] || ''))}">${escapeHtml(String(n))}</span>`).join('');
    let rows = `
            <div class="status-kv-key">${_mechLink('tool_surface')}</div>
            <div class="status-kv-value">${_trustChip(level, word)}</div>
            <div class="status-kv-key">What happened</div>
            <div class="status-kv-value text-gray-300">${escapeHtml(String(d.summary || ''))}</div>`;
    const rk = d.ranker || null;
    if (rk && typeof rk === 'object' && d.mode === 'governed') {
        // what decided: the words always; meaning when the model was ready; the finder present or not
        const added = Array.isArray(rk.semantic_added) ? rk.semantic_added.length : 0;
        const sem = rk.semantic ? `meaning (${escapeHtml(String(rk.semantic_model || 'embedding'))}: ${added} added)`
                  : (rk.semantic_state === 'warming' ? 'meaning: warming up — words only this turn'
                     : (rk.semantic_state === 'unavailable' ? 'meaning: unavailable — words only' : 'words only'));
        const finder = rk.finder === false ? '; no Tool Finder on this agent — nothing held' : '';
        rows += `
            <div class="status-kv-key">Ranked by</div>
            <div class="status-kv-value text-gray-300">words · ${sem}${escapeHtml(finder)}</div>`;
    }
    if (Array.isArray(d.offered) && d.offered.length) {
        rows += `
            <div class="status-kv-key">Offered</div>
            <div class="status-kv-value ts-chips">${chips(d.offered, 'ts-offered')}</div>`;
    }
    if (Array.isArray(d.widened) && d.widened.length) {
        rows += `
            <div class="status-kv-key">Found by the model</div>
            <div class="status-kv-value ts-chips">${chips(d.widened, 'ts-widened')}</div>`;
    }
    if (Array.isArray(d.held) && d.held.length) {
        rows += `
            <div class="status-kv-key">Held</div>
            <div class="status-kv-value ts-chips">${chips(d.held, 'ts-held')}</div>`;
    }
    if (Array.isArray(d.denied) && d.denied.length) {
        rows += `
            <div class="status-kv-key">Denied by authority</div>
            <div class="status-kv-value ts-chips">${chips(d.denied, 'ts-denied')}</div>`;
    }
    if (asked.length) {
        rows += `
            <div class="status-kv-key">Asked for, denied</div>
            <div class="status-kv-value" ${_trustVal('warn')}>${escapeHtml(asked.join(', '))} <span class="text-gray-400">— never offered, never found</span></div>`;
    }
    return `<div class="status-kv-grid">${rows}</div>`;
}

function _renderActionRecordDetails(details) {
    // OBDA-ACT — one act in words: what the agent did with a signed action (prepared, performed,
    // parked, observed or refused), the change it makes (the database's own before → after),
    // and the refusal named. Facts, the same line the capsule row speaks.
    const d = details || {};
    const level = { performed: 'pass', prepared: 'pass', observed: 'na', parked: 'warn', refused: 'warn' }[d.outcome] || 'na';
    const word = { performed: `done · ${d.rows_affected || 0} row${Number(d.rows_affected) === 1 ? '' : 's'}`,
                   prepared: 'prepared — awaiting your confirmation, nothing written yet',
                   parked: "parked for the owner's review — nothing written yet",
                   observed: 'observe — nothing changed',
                   refused: 'refused — nothing written' }[d.outcome] || String(d.outcome || '');
    let rows = `
            <div class="status-kv-key">${_mechLink('actions')}</div>
            <div class="status-kv-value">${_trustChip(level, `${escapeHtml(String(d.action || 'action'))}${d.version ? ' v' + escapeHtml(String(d.version)) : ''} · ${escapeHtml(word)}`)}</div>`;
    const lines = (d.consequence && Array.isArray(d.consequence.lines)) ? d.consequence.lines : [];
    if (lines.length) {
        rows += `
            <div class="status-kv-key">Change</div>
            <div class="status-kv-value text-gray-300">${lines.map(l => escapeHtml(String(l))).join('<br>')}</div>`;
    }
    if (d.outcome === 'refused' && (d.refusal || d.refused_check)) {
        rows += `
            <div class="status-kv-key">Why</div>
            <div class="status-kv-value" ${_trustVal('warn')}>${escapeHtml(String(d.refusal || d.refused_check))}</div>`;
    }
    if (d.posture) {
        rows += `
            <div class="status-kv-key">Posture</div>
            <div class="status-kv-value text-gray-300">${escapeHtml(String(d.posture))}</div>`;
    }
    if (d.confirmed_by) {
        rows += `
            <div class="status-kv-key">Confirmed by</div>
            <div class="status-kv-value text-gray-300">a person — ${escapeHtml(String(d.confirmed_by))}</div>`;
    }
    if (d.invocation_hash) {
        rows += `
            <div class="status-kv-key">Receipt</div>
            <div class="status-kv-value text-gray-300 font-mono">${escapeHtml(String(d.invocation_hash).slice(0, 12))}${d.outcome === 'performed' ? ' · sealed' : ''}</div>`;
    }
    return `<div class="status-kv-grid">${rows}</div>`;
}

function _renderTurnBudgetDetails(details) {
    // S5 — the budget ran out: which unit, how the turn ended (gracefully — the answer says
    // what is left — or stopped by a hard limit), what was skipped or refused. Facts.
    const d = details || {};
    const killed = !!d.killed;
    const level = killed ? 'danger' : 'warn';
    const word = killed ? `stopped by a hard limit (${d.killed})` : 'finished gracefully';
    let rows = `
            <div class="status-kv-key">${_mechLink('turn_budget')}</div>
            <div class="status-kv-value">${_trustChip(level, `${escapeHtml(String(d.exhausted || '?'))} used up · ${escapeHtml(word)}`)}</div>`;
    if (d.summary) {
        rows += `
            <div class="status-kv-key">What happened</div>
            <div class="status-kv-value text-gray-300">${escapeHtml(String(d.summary))}</div>`;
    }
    if (Array.isArray(d.units) && d.units.length) {
        rows += `
            <div class="status-kv-key">Used</div>
            <div class="status-kv-value text-gray-300">${d.units.map(u => `${escapeHtml(String(u.used))} of ${escapeHtml(String(u.total))} ${escapeHtml(String(u.unit))}`).join(' · ')}</div>`;
    }
    if (Array.isArray(d.skipped_phases) && d.skipped_phases.length) {
        rows += `
            <div class="status-kv-key">Skipped</div>
            <div class="status-kv-value text-gray-300">phase${d.skipped_phases.length === 1 ? '' : 's'} ${escapeHtml(d.skipped_phases.join(', '))}${Array.isArray(d.goals) && d.goals.length ? ` — ${escapeHtml(d.goals.join(' · '))}` : ''}</div>`;
    }
    if (Number(d.refusals) > 0) {
        rows += `
            <div class="status-kv-key">Refused</div>
            <div class="status-kv-value text-gray-300">${escapeHtml(String(d.refusals))} further tool call${Number(d.refusals) === 1 ? '' : 's'} — the model was told to answer with what it has</div>`;
    }
    return `<div class="status-kv-grid">${rows}</div>`;
}

function _renderKgEnrichmentDetails(details) {
    // Knowledge-graph enrichment — what the planner was actually shown, and HOW the
    // question reached it. Sibling of the grounding/guardrail/obda cards; before this
    // the event fell through to a raw JSON dump of the whole payload (table_columns,
    // join_keys, scope...), which is where a reader stopped reading.
    //
    // Seeding has two recall channels answering different questions: LEXICAL matches
    // the words the question used, SEMANTIC matches what it meant. The card leads with
    // that split because it is the one fact nothing else on the turn carries — and it
    // names the three ways semantics can contribute nothing (off / still warming /
    // ran and found nothing), which read identically as a bare absence.
    const d = details || {};
    const enr = Array.isArray(d.enrichments) ? d.enrichments : [];
    const first = enr[0] || {};
    const entities = d.total_entities || first.entity_count || 0;
    const rels = d.total_relationships || first.relationship_count || 0;

    // Merge the per-KG seeding reports (a turn reads one graph today; a future
    // multi-graph turn must not silently report only the first).
    const reports = enr.map(e => e && e.semantic_seeding).filter(Boolean);
    const bridged = [];
    let hits = 0, dropped = 0;
    const states = [];
    reports.forEach(r => {
        hits += (r.hits || 0);
        dropped += (r.bridged_dropped || 0);
        if (r.state) states.push(r.state);
        (r.bridged || []).forEach(b => bridged.push(b));
    });
    const state = states.includes('ready') ? 'ready'
                : states.includes('building') ? 'building'
                : states.includes('error') ? 'error'
                : states.includes('cold') ? 'cold'
                : (states[0] || 'off');

    let semLevel, semText;
    if (!reports.length || state === 'off') {
        semLevel = 'muted'; semText = 'Lexical only — semantic seeding is off';
    } else if (state === 'building' || state === 'cold') {
        semLevel = 'warn';
        semText = 'Lexical only — the semantic index is still building for this graph';
    } else if (state === 'error') {
        semLevel = 'warn'; semText = 'Lexical only — semantic seeding did not run';
    } else if (bridged.length) {
        semLevel = 'pass';
        semText = `Lexical + semantic — ${nodesLabel(bridged.length)} `
                + `reached only by meaning`;
    } else if (hits) {
        semLevel = 'pass';
        semText = 'Lexical + semantic — semantics agreed, adding nothing the words missed';
    } else {
        semLevel = 'muted';
        semText = 'Lexical + semantic — nothing cleared the similarity threshold';
    }

    // WHICH graph enriched the turn — omitted (never invented) on an event that predates
    // the stamp; a consumer's pack copy sits beside the publisher's, and the two are
    // indistinguishable by their owner profile alone.
    const graphNames = enr.map(e => e && e.kg_name).filter(Boolean);
    let rows = '';
    if (graphNames.length) {
        rows += `
            <div class="status-kv-key">Graph</div>
            <div class="status-kv-value">${escapeHtml(graphNames.join(', '))}`
             + ` <span class="text-gray-400">${nodesLabel(entities)} · ${relationshipsLabel(rels)}</span></div>`;
    }
    // Model B: WHICH version the agent grounded on. Omitted on an older event (nothing
    // invented); a released-mode agent falling back to the draft is a WARNING — the
    // owner asked for release-only grounding and did not get it.
    const gnd = enr.map(e => e && e.grounding).filter(g => g && typeof g === 'object')[0];
    if (gnd && gnd.mode === 'released') {
        if (gnd.kg_id) {
            const lbl = escapeHtml(String(gnd.label || gnd.version_id || 'release'));
            const dep = gnd.lifecycle === 'deprecated';
            rows += `
            <div class="status-kv-key">Grounded on</div>
            <div class="status-kv-value">${_trustChip(dep ? 'warn' : 'pass',
                `released ${lbl}` + (dep ? ' — this release is deprecated' : ''))}</div>`;
        } else {
            rows += `
            <div class="status-kv-key">Grounded on</div>
            <div class="status-kv-value">${_trustChip('warn',
                'live draft — release-only grounding requested but the ontology has no release yet')}</div>`;
        }
    } else if (gnd && gnd.mode === 'live') {
        rows += `
            <div class="status-kv-key">Grounded on</div>
            <div class="status-kv-value text-gray-400">live draft</div>`;
    }
    rows += `
            <div class="status-kv-key">Seeded by</div>
            <div class="status-kv-value">${_trustChip(semLevel, semText)}</div>`;

    // Conversational carry-over: the previous turn's entities seeded this turn, so a
    // follow-up that names nothing the graph holds ("the names of those customers")
    // still reaches the tables the conversation is about. Omitted when nothing was
    // carried — a row on every first turn would be furniture.
    const carry = enr.map(e => e && e.carry_over).filter(c => c && Array.isArray(c.entities) && c.entities.length)[0];
    if (carry) {
        const shown = carry.entities.slice(0, 6).map(n => escapeHtml(String(n))).join(', ')
                    + (carry.entities.length > 6 ? ` <span class="text-gray-400">+${carry.entities.length - 6} more</span>` : '');
        const why = carry.source === 'used' ? 'read by the previous turn' : 'seeded on the previous turn';
        rows += `
            <div class="status-kv-key">Carried over</div>
            <div class="status-kv-value">${shown}`
             + ` <span class="text-gray-400">— ${why}${carry.from_turn ? ` (turn ${escapeHtml(String(carry.from_turn))})` : ''}</span></div>`;
    }

    bridged.slice(0, 6).forEach(b => {
        const nm = escapeHtml(String(b.name || ''));
        const sc = (typeof b.score === 'number') ? b.score.toFixed(2) : '';
        rows += `
            <div class="status-kv-key">By meaning</div>
            <div class="status-kv-value" ${_trustVal('pass')}>${nm}`
             + (sc ? ` <span class="text-gray-400">similarity ${sc}</span>` : '') + `</div>`;
    });
    if (bridged.length > 6) {
        rows += `
            <div class="status-kv-key"></div>
            <div class="status-kv-value text-gray-400">+${bridged.length - 6} more</div>`;
    }
    // A semantic hit that ranking or the node budget dropped contributed nothing to the
    // prompt. Stated rather than hidden: it is the honest reading of a channel that
    // found something the words missed and still did not get it in front of the planner.
    if (dropped) {
        rows += `
            <div class="status-kv-key">Not injected</div>
            <div class="status-kv-value text-gray-400">${dropped} semantic hit${dropped === 1 ? '' : 's'} `
             + `ranked out or over budget</div>`;
    }

    const surfaceOnly = d.surface_only === true || enr.some(e => e && e.surface_only === true);
    const products = [];
    enr.forEach(e => (e && e.data_products || []).forEach(n => {
        if (!products.includes(n)) products.push(n);
    }));

    rows += `
            <div class="status-kv-key">${surfaceOnly ? 'Analysed' : 'Injected'}</div>
            <div class="status-kv-value text-gray-300">${nodesLabel(entities)} · `
         + `${rels} relationship${rels === 1 ? '' : 's'}</div>`;
    if (surfaceOnly) {
        rows += `
            <div class="status-kv-key">Withheld</div>
            <div class="status-kv-value text-gray-400">table schemas — the agent's data surface is `
             + `the product contract alone</div>`;
    }
    if (products.length) {
        rows += `
            <div class="status-kv-key">Data products</div>
            <div class="status-kv-value" ${_trustVal('pass')}>${escapeHtml(products.join(', '))}</div>`;
    }
    const scope = [];
    enr.forEach(e => (e && e.scope_databases || []).forEach(n => {
        if (!scope.includes(n)) scope.push(n);
    }));
    if (scope.length) {
        rows += `
            <div class="status-kv-key">Trusted scope</div>
            <div class="status-kv-value text-gray-300">${escapeHtml(scope.join(', '))}</div>`;
    }
    return `<div class="status-kv-grid">${rows}</div>`;
}

function _renderHonestyDetails(details) {
    // Honesty post-condition — live + reload. Monochrome status-kv card, sibling of the
    // grounding/guardrail/obda cards. Fires only when the answer asserted NON-EXISTENCE while
    // every raw-SQL data query this turn FAILED. decision: flag (observe/annotate) | block
    // (enforce rewrote the false negative into an honest retrieval-failure).
    const contained = details.answer_contained === true || (details.decision || '') === 'block';
    const failed = details.failed_query_count || 0;
    const mode = (details.mode || 'observe');
    const isRag = (details.kind || '') === 'rag';
    // Grain substitution: a data product REFUSED the asked member (deliberate
    // contract limit) and the answer used a coarser grain. The act APPENDS a
    // disclosure — never a block — so it carries its own labels; "false
    // negative / N failed queries" would misdescribe a turn where every query
    // SUCCEEDED and only the framing was dishonest.
    const isSub = (details.kind || '') === 'grain_substitution';
    // F-001 cited-but-unread: the answer named a library section as consulted that the
    // turn never opened. Like grain substitution the act APPENDS a disclosure, so it
    // carries its own labels — "false negative" would misdescribe a turn whose claim was
    // about the agent's OWN process rather than about the data.
    const isCite = (details.kind || '') === 'unread_citation';
    // F-002 — the citation branch's other kind: an identifier the answer presents as
    // established that the skill's library does not contain.
    const isFab = (details.kind || '') === 'fabricated_identifier';
    // An act the answer claims (prepared · awaits confirmation · parked) while the turn
    // performed none — no action record exists, so no Confirm and no review either.
    const isAct = (details.kind || '') === 'unbacked_act';
    // A record presented with no query behind it: a tool-enabled agent ran no data call and
    // still showed a row whose values are in nothing it was given (it wrote the record).
    const isRec = (details.kind || '') === 'unsourced_record';
    // A write the answer reports as done, which the agent's authority DENIED this turn.
    const isDenied = (details.kind || '') === 'claimed_effect_after_denial';
    const disclosed = details.disclosure_appended === true;
    const actLabel = isDenied
        ? (contained ? 'Denied write reported as done — replaced'
                     : disclosed ? 'Denied write reported as done — disclosed in the answer' : 'Denied write reported as done — flagged')
        : isRec
        ? (contained ? 'Unsourced record replaced'
                     : disclosed ? 'Unsourced record disclosed in the answer' : 'Unsourced record flagged')
        : isAct
        ? (contained ? 'Unbacked act replaced'
                     : disclosed ? 'Unbacked act disclosed in the answer' : 'Unbacked act flagged')
        : isFab
        ? (disclosed ? 'Invented identifier disclosed in the answer'
                     : 'Invented identifier flagged')
        : isCite
        ? (disclosed ? 'Unbacked citation disclosed in the answer'
                     : 'Unbacked citation flagged')
        : isSub
        ? (disclosed ? 'Grain substitution disclosed in the answer'
                     : 'Silent grain substitution flagged')
        : isRag
        ? (contained ? 'Not covered — honest refusal delivered' : 'Weak retrieval support flagged')
        : (contained ? 'False negative contained' : 'False negative flagged');
    // `review` = enforce WITH retention: the original was rewritten AND parked for the
    // owner. Same chip the grounding and guardrail cards show, so the trust-card family
    // reads consistently now that review reaches the honesty gate too.
    const isReview = (details.mode || '') === 'review';
    let rows = `
            <div class="status-kv-key">${_mechLink('honesty_gate')}</div>
            <div class="status-kv-value">${_trustChip(contained ? 'danger' : 'warn', actLabel)}${
                isReview && contained ? ' ' + _trustChip('warn', 'parked for review') : ''}</div>`;
    if (isRec) {
        const vals = Array.isArray(details.unsupported) ? details.unsupported : [];
        rows += `
            <div class="status-kv-key">What happened</div>
            <div class="status-kv-value text-gray-300 u-tip" data-tip="No data query ran in this turn. Every value of the record was checked against everything the agent was given (the knowledge block, the conversation, any tool result) — these appear in none of it.">a record shown · no query ran this turn</div>
            <div class="status-kv-key">Values from nowhere</div>
            <div class="status-kv-value" ${_trustVal('warn')}>${
                vals.slice(0, 6).map(v => escapeHtml(String(v))).join(', ') || '—'}${vals.length > 6 ? ` +${vals.length - 6} more` : ''}</div>`;
    } else if (isAct) {
        rows += `
            <div class="status-kv-key">What happened</div>
            <div class="status-kv-value text-gray-300 u-tip" data-tip="Whether a turn prepared, performed or parked a change is read from the turn's own action record, not from the answer's words.">the answer claims a change · this turn performed none</div>`;
    } else if (isFab) {
        const bad = Array.isArray(details.fabricated) ? details.fabricated : [];
        rows += `
            <div class="status-kv-key">Not in the library</div>
            <div class="status-kv-value u-tip" data-tip="The skill declares a closed vocabulary; these identifiers match its pattern but appear nowhere in the corpus.\nThey were composed here, not read from the source." ${_trustVal('warn')}>${
                bad.map(b => escapeHtml(String(b))).join(', ') || '—'}</div>`;
    } else if (isCite) {
        const cited = Array.isArray(details.cited) ? details.cited : [];
        const unread = Array.isArray(details.unread) ? details.unread : [];
        const fid = (details.fidelity === null || details.fidelity === undefined)
            ? null : Number(details.fidelity);
        rows += `
            <div class="status-kv-key">Citations backed</div>
            <div class="status-kv-value" ${_trustVal(fid === 1 ? 'pass' : 'warn')}>${
                cited.length - unread.length} of ${cited.length}${
                fid === null ? '' : ` · fidelity ${fid.toFixed(2)}`}</div>
            <div class="status-kv-key">Never opened</div>
            <div class="status-kv-value u-tip" data-tip="The skill library was not consulted for these sections this turn.\nStatements attributed to them are unverified against the source." ${_trustVal('warn')}>${
                unread.map(a => escapeHtml(String(a))).join(', ') || '—'}</div>`;
    } else if (isRag) {
        // RAG Trust Hardening Wave 1 §1.3 (U4 refusal-as-guidance): the knowledge base
        // was searched and does not sufficiently cover the question.
        const q = details.qualifying_chunks || 0;
        const ts = details.top_score;
        rows += `
            <div class="status-kv-key">Retrieval support</div>
            <div class="status-kv-value u-tip" data-tip="'Not covered by the knowledge base' — never 'does not exist'.\nConsider rephrasing or adding the documentation to the repository." ${_trustVal('warn')}>${q} qualifying chunk(s)${(ts !== null && ts !== undefined) ? ` · top relevance ${Number(ts).toFixed(2)}` : ''}</div>`;
    } else if (isSub) {
        rows += `
            <div class="status-kv-key">What happened</div>
            <div class="status-kv-value text-gray-300 u-tip" data-tip="The product's allowlist is deliberate data minimisation — the refusal is the contract working. What the gate ${disclosed ? 'fixed' : 'flags'} is presenting the coarser figure AS IF it answered the asked question.">product refused the asked grain · answer used a coarser one</div>`;
    } else {
        rows += `
            <div class="status-kv-key">Failed data queries</div>
            <div class="status-kv-value text-gray-300">${failed} · none succeeded</div>`;
    }
    if (details.reason) {
        rows += `
            <div class="status-kv-key">Reason</div>
            <div class="status-kv-value text-gray-400">${escapeHtml(details.reason)}</div>`;
    }
    rows += `
            <div class="status-kv-key">Mode</div>
            <div class="status-kv-value text-gray-400">${escapeHtml(mode)}</div>`;
    return `<div class="status-kv-grid">${rows}</div>`;
}

function _renderReviewDecisionDetails(details) {
    // HITL review disposition — the human decision on a withheld answer, as a
    // first-class trust card (live via review_decision_notice + persisted on the
    // turn's system_events for reload). Same shared trust-chip grammar as the
    // sibling grounding/guardrail/honesty cards.
    const decision = (details.decision || '').toLowerCase();
    const approved = decision === 'approved';
    const released = !!details.released_to_context;
    const src = details.source === 'grounding' ? 'Grounding gate' : 'Outbound guardrail';
    let rows = `
            <div class="status-kv-key">Human review</div>
            <div class="status-kv-value">${_trustChip(approved ? 'pass' : 'muted',
                approved ? (released ? 'Released into the conversation' : 'Released by the owner (record only)')
                         : 'Withholding upheld')}</div>
            <div class="status-kv-key">Withheld by</div>
            <div class="status-kv-value text-gray-300">${escapeHtml(src)}</div>`;
    if (details.note) {
        rows += `
            <div class="status-kv-key">Note</div>
            <div class="status-kv-value text-gray-400">${escapeHtml(details.note)}</div>`;
    }
    if (approved && released) {
        rows += `
            <div class="status-kv-key">Release scope</div>
            <div class="status-kv-value text-gray-400">forward-only — subsequent turns receive the original (clearly labelled as a human decision) and its values stop being re-redacted in this session; the sealed turn itself is never rewritten</div>`;
    } else if (approved) {
        rows += `
            <div class="status-kv-key">Where to read it</div>
            <div class="status-kv-value text-gray-400">the released original is in this turn's “Why trust this answer?” capsule — delivered history is never rewritten</div>`;
    }
    return `<div class="status-kv-grid">${rows}</div>`;
}

function _renderExecutionStartDetails(details) {
    const profileTag = details.profile_tag || 'Unknown';
    const profileType = details.profile_type || 'tool_enabled';
    const query = details.query || '';
    const queryPreview = query.length > 100 ? query.substring(0, 100) + '...' : query;

    return `
        <div class="status-kv-grid">
            <div class="status-kv-key">Profile</div>
            <div class="status-kv-value">${renderProfileTag(profileTag, profileType)}</div>
            <div class="status-kv-key">Type</div>
            <div class="status-kv-value">${profileType}</div>
            ${queryPreview ? `
            <div class="status-kv-key">Query</div>
            <div class="status-kv-value text-gray-300">"${queryPreview}"</div>
            ` : ''}
        </div>
    `;
}

function _renderExecutionCompleteDetails(details) {
    const profileTag = details.profile_tag || 'Unknown';
    const profileType = details.profile_type;
    const phasesExecuted = details.phases_executed || 0;
    const inputTokens = details.total_input_tokens || 0;
    const outputTokens = details.total_output_tokens || 0;
    const durationMs = details.duration_ms || 0;
    const durationSec = (durationMs / 1000).toFixed(1);
    const costUsd = details.cost_usd != null ? parseFloat(details.cost_usd) : null;

    return `
        <div class="status-kv-grid">
            <div class="status-kv-key">Profile</div>
            <div class="status-kv-value">${renderProfileTag(profileTag, profileType)}</div>
            <div class="status-kv-key">Phases</div>
            <div class="status-kv-value">${phasesExecuted} executed</div>
            <div class="status-kv-key">Tokens</div>
            <div class="status-kv-value">${inputTokens.toLocaleString()} in / ${outputTokens.toLocaleString()} out</div>
            ${costUsd !== null ? `
            <div class="status-kv-key">Cost</div>
            <div class="status-kv-value text-amber-300">$${costUsd.toFixed(6)}</div>
            ` : ''}
            <div class="status-kv-key">Duration</div>
            <div class="status-kv-value">${durationSec}s</div>
        </div>
    `;
}

function _renderPlanningDetails(details) {
    if (!details.summary || !details.full_text) return null;

    let fullTextHtml = '';
    const text = details.full_text;
    const characterThreshold = 150;

    if (text.length > characterThreshold) {
        fullTextHtml = `
            <details class="text-xs">
                <summary class="cursor-pointer text-gray-400 hover:text-white">Full Text (${text.length} chars)</summary>
                <div class="mt-2 p-2 status-text-block whitespace-pre-wrap">${text}</div>
            </details>
        `;
    } else {
        fullTextHtml = `<div class="status-text-block whitespace-pre-wrap">${text}</div>`;
    }

    return `
        <div class="status-kv-grid">
            <div class="status-kv-key">Summary</div>
            <div class="status-kv-value">${details.summary}</div>
            <div class="status-kv-key">Full Text</div>
            <div class="status-kv-value">${fullTextHtml}</div>
        </div>
    `;
}

function _renderMetaPlanDetails(details) {
    if (!Array.isArray(details)) return null;

    let html = `<details class="text-xs" open>
                    <summary class="cursor-pointer text-gray-400 hover:text-white">Generated Plan (${details.length} steps)</summary>
                    <div class="space-y-3 mt-2">`;

    details.forEach(phase => {
        let argsHtml = '';
        if (phase.arguments) {
            const argsString = JSON.stringify(phase.arguments, null, 2);
            argsHtml = `
                <div class="status-kv-item">
                    <div class="status-kv-key">Args</div>
                    <div class="status-kv-value">
                        <details class="text-xs">
                            <summary class="cursor-pointer text-gray-400 hover:text-white">View Arguments</summary>
                            <div class="mt-2 p-2 status-text-block whitespace-pre-wrap">${argsString}</div>
                        </details>
                    </div>
                </div>
            `;
        }

        let structuralKeysHtml = '';
        if (phase.type) {
            structuralKeysHtml += `<div class="status-kv-item"><div class="status-kv-key">Type</div><div class="status-kv-value"><code class="status-code font-bold text-yellow-300">${phase.type}</code></div></div>`;
        }
        if (phase.loop_over) {
            structuralKeysHtml += `<div class="status-kv-item"><div class="status-kv-key">Loop Over</div><div class="status-kv-value"><code class="status-code">${phase.loop_over}</code></div></div>`;
        }

        html += `<div class="status-phase-card">
                    <div class="font-bold text-gray-300 mb-2">Step ${phase.phase}</div>
                    <div class="status-kv-item"><div class="status-kv-key">Goal</div><div class="status-kv-value">${phase.goal}</div></div>`;

        html += structuralKeysHtml;

        if (phase.relevant_tools) {
            html += `<div class="status-kv-item"><div class="status-kv-key">Tools</div><div class="status-kv-value"><code class="status-code">${phase.relevant_tools.join(', ')}</code></div></div>`;
        }
        if (phase.executable_prompt) {
            html += `<div class="status-kv-item"><div class="status-kv-key">Prompt</div><div class="status-kv-value"><code class="status-code">${phase.executable_prompt}</code></div></div>`;
        }
        html += `${argsHtml}</div>`;
    });
    html += '</div></details>';
    return html;
}

function _renderToolIntentDetails(details) {
    if (!details || (!details.tool_name && !details.prompt_name)) return null;

    const name = details.tool_name || details.prompt_name;
    const type = details.tool_name ? 'Tool' : 'Prompt';

    let argsHtml = '';
    if (details.arguments) {
        try {
             const argsString = JSON.stringify(details.arguments, null, 2);
             argsHtml = `
                <div class="status-kv-item">
                    <div class="status-kv-key">Args</div>
                    <div class="status-kv-value">
                        <details class="text-xs">
                            <summary class="cursor-pointer text-gray-400 hover:text-white">View Arguments</summary>
                            <div class="mt-2 p-2 status-text-block whitespace-pre-wrap">${argsString}</div>
                        </details>
                    </div>
                </div>
            `;
        } catch (e) {
            argsHtml = `<div class="status-kv-item"><div class="status-kv-key">Args</div><div class="status-kv-value text-rose-400">[Error displaying arguments]</div></div>`;
        }
    }

    return `
        <div class="status-kv-item"><div class="status-kv-key">${type}</div><div class="status-kv-value"><code class="status-code">${name}</code></div></div>
        ${argsHtml}
    `;
}

/**
 * Render Genie coordination events in the status window.
 * Uses purple-themed styling for world-class visual feedback.
 *
 * ARCHITECTURE: All coordination visualization is in Live Status window only.
 * The chat pane stays clean with just Q&A - this panel shows the "how".
 */
function _renderGenieStep(eventData, parentContainer, isFinal = false) {
    if (_consumeAssuranceProgress(eventData)) return;
    const { step, details, type } = eventData;

    // Progressive disclosure attribution — the coordinator can fetch library
    // sections itself (genie_coordinator binds component tools), so the same
    // derivation runs here. Reset on the coordinator's own turn boundary.
    if (type === 'genie_coordination_start' || type === 'execution_start') _pdReset();
    if (type === 'skill_section_loaded') _pdNoteSection(details || {});

    // Skip obsolete events - genie_coordination_start provides the same info
    if (type === 'genie_start' || type === 'genie_routing') {
        return;
    }

    // Skip transient UI-only events — these are for indicator dots only, not status cards
    if (type === 'status_indicator_update' || type === 'token_update') {
        return;
    }

    // Mark previous active step as completed
    const lastStep = parentContainer.querySelector('.status-step.active');
    if (lastStep) {
        lastStep.classList.remove('active');
        lastStep.classList.add('completed');
    }

    const stepEl = document.createElement('div');
    stepEl.className = 'status-step p-3 rounded-md mb-2 genie-status-step';
    _stampLa(stepEl, eventData);

    // Tag with filter category for event filter chips
    if (eventData._filterCategory) {
        stepEl.dataset.filterCategory = eventData._filterCategory;
        if (!isFilterCategoryEnabled(eventData._filterCategory)) {
            stepEl.classList.add('event-filtered-hidden');
        }
    }

    if (isFinal) {
        stepEl.classList.add('completed');
    } else {
        stepEl.classList.add('active');
    }

    // Add category class for background color (aligned with filter pill colors)
    const category = getEventCategory(type, eventData._filterCategory);
    stepEl.classList.add(category);
    _stampTrustGuideContext(stepEl, type, details);

    // Color-code based on event type
    if (type === 'genie_coordination_complete') {
        stepEl.classList.add(details?.success ? 'genie-success' : 'genie-error');
    } else if (type === 'genie_slave_completed') {
        stepEl.classList.add(details?.success ? 'genie-slave-success' : 'genie-slave-error');
    }

    // Step header (text-only, matching Optimizer style)
    const stepHeader = document.createElement('div');
    stepHeader.className = 'flex items-center gap-2 mb-2';

    const titleEl = document.createElement('h4');
    titleEl.className = 'font-bold text-sm text-white';
    titleEl.textContent = step || 'Genie Coordination';
    stepHeader.appendChild(titleEl);

    stepEl.appendChild(stepHeader);

    // Render details based on event type
    if (details && typeof details === 'object') {
        const detailsEl = document.createElement('div');
        detailsEl.className = 'text-xs';

        switch (type) {
            case 'genie_start':
            case 'genie_routing':
            case 'genie_coordination_start': {
                const profileCount = details.slave_profiles?.length || 0;
                const profileTag = details.profile_tag || 'GENIE';
                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Profile</div>
                        <div class="status-kv-value">${renderProfileTag(profileTag, 'genie')}</div>
                        <div class="status-kv-key">Experts</div>
                        <div class="status-kv-value">${profileCount} available</div>
                    </div>
                    ${details.slave_profiles && details.slave_profiles.length > 0 ? `
                        <details class="mt-2">
                            <summary class="cursor-pointer text-gray-400 hover:text-white">View Available Profiles</summary>
                            <div class="genie-profile-tags mt-2 flex flex-wrap gap-1">
                                ${details.slave_profiles.map(p => {
                                    return renderProfileTag(p.tag, p.profile_type);
                                }).join('')}
                            </div>
                        </details>
                    ` : ''}
                `;
                break;
            }

            case 'genie_llm_step': {
                const stepNumber = details.step_number || '?';
                const stepName = details.step_name || `Step ${stepNumber}`;
                const inputTokens = details.input_tokens || 0;
                const outputTokens = details.output_tokens || 0;
                const costUsd = details.cost_usd != null ? parseFloat(details.cost_usd) : null;

                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Step</div>
                        <div class="status-kv-value"><code class="status-code text-indigo-300">#${stepNumber}: ${stepName}</code></div>
                        <div class="status-kv-key">Status</div>
                        <div class="status-kv-value text-emerald-400">✓ Complete</div>
                        <div class="status-kv-key">Tokens</div>
                        <div class="status-kv-value">${inputTokens.toLocaleString()} in / ${outputTokens.toLocaleString()} out</div>
                        ${_cachedTokensHtml(details)}
                        ${costUsd !== null ? `
                        <div class="status-kv-key">Cost</div>
                        <div class="status-kv-value text-amber-300">$${costUsd.toFixed(6)}</div>
                        ` : ''}
                    </div>
                `;
                break;
            }

            case 'genie_routing_decision': {
                const selectedCount = details.selected_profiles?.length || 0;
                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Decision</div>
                        <div class="status-kv-value">${selectedCount} profile${selectedCount > 1 ? 's' : ''} selected</div>
                    </div>
                    ${details.decision_text ? `
                        <div class="mt-2 p-2 bg-slate-900/30 rounded border border-slate-500/30 text-gray-300 text-xs">
                            ${details.decision_text}
                        </div>
                    ` : ''}
                    ${details.selected_profiles && details.selected_profiles.length > 0 ? `
                        <div class="mt-2 flex flex-wrap gap-1">
                            ${details.selected_profiles.map(tag => renderProfileTag(tag, null, 'profile-tag--status-selected')).join('')}
                        </div>
                    ` : ''}
                `;
                break;
            }

            case 'genie_slave_invoked': {
                const profileTag = details.profile_tag || 'UNKNOWN';
                const profileType = details.profile_type;
                const slaveTagColor = getProfileTagColor(profileTag, profileType);
                const query = details.query || details.query_preview || '';

                // Build query display - show full query in expandable section
                let queryHtml = '';
                if (query) {
                    const queryPreview = query.length > 100 ? query.slice(0, 100) + '...' : query;
                    const needsExpand = query.length > 100;
                    queryHtml = needsExpand ? `
                        <details class="mt-2">
                            <summary class="cursor-pointer text-slate-400 hover:text-white text-xs font-medium">Question to <span class="profile-tag profile-tag--sm" style="--profile-tag-bg: ${slaveTagColor}; --profile-tag-border: ${slaveTagColor}; --profile-tag-text: #FFFFFF;">@${profileTag}</span></summary>
                            <div class="mt-1 p-2 bg-slate-900/50 rounded border border-slate-600/30 text-slate-200 text-sm whitespace-pre-wrap">${escapeHtml(query)}</div>
                        </details>
                    ` : `
                        <div class="mt-2">
                            <div class="text-slate-400 text-xs font-medium mb-1">Question to <span class="profile-tag profile-tag--sm" style="--profile-tag-bg: ${slaveTagColor}; --profile-tag-border: ${slaveTagColor}; --profile-tag-text: #FFFFFF;">@${profileTag}</span></div>
                            <div class="p-2 bg-slate-900/50 rounded border border-slate-600/30 text-slate-200 text-sm whitespace-pre-wrap">${escapeHtml(query)}</div>
                        </div>
                    `;
                }

                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Profile</div>
                        <div class="status-kv-value">${renderProfileTag(profileTag, profileType)}</div>
                        <div class="status-kv-key">Status</div>
                        <div class="status-kv-value text-amber-400">Processing...</div>
                    </div>
                    ${queryHtml}
                    ${details.slave_session_id ? `
                        <div class="mt-1 text-gray-500 text-xs">Session: ${details.slave_session_id.slice(0, 8)}...</div>
                    ` : ''}
                `;
                break;
            }

            case 'genie_slave_progress': {
                const profileTag = details.profile_tag || 'UNKNOWN';
                const profileType = details.profile_type;
                const message = details.message || 'Working...';
                const progress = details.progress_pct !== undefined ? details.progress_pct : null;
                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Profile</div>
                        <div class="status-kv-value">${renderProfileTag(profileTag, profileType)}</div>
                        <div class="status-kv-key">Status</div>
                        <div class="status-kv-value text-amber-400">${message}</div>
                        ${progress !== null ? `
                            <div class="status-kv-key">Progress</div>
                            <div class="status-kv-value">${progress}%</div>
                        ` : ''}
                    </div>
                `;
                break;
            }

            case 'genie_slave_completed': {
                const profileTag = details.profile_tag || 'UNKNOWN';
                const profileType = details.profile_type;
                const slaveTagColor = getProfileTagColor(profileTag, profileType);
                const duration = details.duration_ms ? `${(details.duration_ms / 1000).toFixed(2)}s` : 'N/A';
                const statusText = details.success ? '✓ Success' : '✗ Failed';
                const statusClass = details.success ? 'text-emerald-400' : 'text-rose-400';

                // Use full result if available, otherwise fall back to preview
                const result = details.result || details.result_preview || '';
                let resultHtml = '';
                if (result) {
                    // Show result collapsed by default - user can expand to see details
                    resultHtml = `
                        <details class="mt-2">
                            <summary class="cursor-pointer text-emerald-400 hover:text-emerald-300 text-xs font-medium">Response from <span class="profile-tag profile-tag--sm" style="--profile-tag-bg: ${slaveTagColor}; --profile-tag-border: ${slaveTagColor}; --profile-tag-text: #FFFFFF;">@${profileTag}</span></summary>
                            <div class="mt-1 p-2 bg-emerald-900/20 rounded border border-emerald-600/30 text-slate-200 text-sm whitespace-pre-wrap max-h-64 overflow-y-auto">${escapeHtml(result)}</div>
                        </details>
                    `;
                }

                let errorHtml = '';
                if (details.error) {
                    errorHtml = `
                        <div class="mt-2">
                            <div class="text-rose-400 text-xs font-medium mb-1">Error from <span class="profile-tag profile-tag--sm" style="--profile-tag-bg: ${slaveTagColor}; --profile-tag-border: ${slaveTagColor}; --profile-tag-text: #FFFFFF;">@${profileTag}</span></div>
                            <div class="p-2 bg-rose-900/30 rounded border border-rose-500/30 text-rose-300">${escapeHtml(details.error)}</div>
                        </div>
                    `;
                }

                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Profile</div>
                        <div class="status-kv-value">${renderProfileTag(profileTag, profileType)}</div>
                        <div class="status-kv-key">Status</div>
                        <div class="status-kv-value ${statusClass}">${statusText}</div>
                        <div class="status-kv-key">Duration</div>
                        <div class="status-kv-value">${duration}</div>
                    </div>
                    ${resultHtml}
                    ${errorHtml}
                `;
                break;
            }

            case 'genie_synthesis_start': {
                const consulted = details.profiles_consulted?.length || 0;
                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Status</div>
                        <div class="status-kv-value text-indigo-400">Synthesizing response...</div>
                        <div class="status-kv-key">Profiles</div>
                        <div class="status-kv-value">${consulted} consulted</div>
                    </div>
                    ${details.profiles_consulted && details.profiles_consulted.length > 0 ? `
                        <div class="mt-2 flex flex-wrap gap-1">
                            ${details.profiles_consulted.map(tag => {
                                const tagColor = getProfileTagColor(tag);
                                return `<span class="profile-tag profile-tag--sm" style="--profile-tag-bg: ${tagColor}; --profile-tag-border: ${tagColor}; --profile-tag-text: #FFFFFF;">@${tag}</span>`;
                            }).join('')}
                        </div>
                    ` : ''}
                `;
                break;
            }

            case 'genie_synthesis_complete': {
                // Show synthesized response (matches "Response from @TAG" pattern in other profiles)
                const consulted = details.profiles_consulted?.length || 0;
                let responseHtml = '';
                if (details.synthesized_response && details.success !== false) {
                    responseHtml = `
                        <details class="mt-2">
                            <summary class="cursor-pointer text-emerald-400 hover:text-emerald-300 text-xs font-medium">Synthesized Response</summary>
                            <div class="mt-1 p-2 bg-emerald-900/20 rounded border border-emerald-600/30 text-slate-200 text-sm whitespace-pre-wrap max-h-64 overflow-y-auto">${escapeHtml(details.synthesized_response)}</div>
                        </details>
                    `;
                }
                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Status</div>
                        <div class="status-kv-value text-emerald-400">✓ Complete</div>
                        <div class="status-kv-key">Profiles</div>
                        <div class="status-kv-value">${consulted} consulted</div>
                    </div>
                    ${responseHtml}
                `;
                break;
            }

            case 'genie_synthesis_skipped': {
                const tag = details.profile_tag ? `@${details.profile_tag}` : 'expert';
                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Status</div>
                        <div class="status-kv-value text-slate-400">⚡ Skipped</div>
                        <div class="status-kv-key">Expert</div>
                        <div class="status-kv-value">${escapeHtml(tag)}</div>
                        <div class="status-kv-key">Reason</div>
                        <div class="status-kv-value text-slate-400">${escapeHtml(details.reason || 'Single knowledge expert, no prior context')}</div>
                    </div>
                `;
                break;
            }

            case 'genie_coordination_complete': {
                const totalDuration = details.total_duration_ms ? `${(details.total_duration_ms / 1000).toFixed(2)}s` : 'N/A';
                const usedCount = details.profiles_used?.length || 0;
                const statusText = details.success ? '✓ Complete' : '✗ Failed';
                const statusClass = details.success ? 'text-emerald-400' : 'text-rose-400';

                // Token counts (if available)
                const inputTokens = details.input_tokens;
                const outputTokens = details.output_tokens;
                let tokensHtml = '';
                if (inputTokens !== undefined && outputTokens !== undefined && (inputTokens > 0 || outputTokens > 0)) {
                    tokensHtml = `
                        <div class="status-kv-key">Tokens</div>
                        <div class="status-kv-value">${inputTokens.toLocaleString()} in / ${outputTokens.toLocaleString()} out</div>
                    `;
                }

                // Cost (if available)
                const costUsd = details.cost_usd != null ? parseFloat(details.cost_usd) : null;
                let costHtml = '';
                if (costUsd !== null) {
                    costHtml = `
                        <div class="status-kv-key">Cost</div>
                        <div class="status-kv-value text-amber-300">$${costUsd.toFixed(6)}</div>
                    `;
                }

                let profilesHtml = '';
                if (details.profiles_used && details.profiles_used.length > 0) {
                    profilesHtml = `
                        <div class="mt-2">
                            <div class="text-gray-500 text-xs mb-1">Profiles Consulted:</div>
                            <div class="genie-profile-tags flex flex-wrap gap-1">
                                ${details.profiles_used.map(tag => {
                                    const tagColor = getProfileTagColor(tag);
                                    return `<span class="profile-tag profile-tag--sm" style="--profile-tag-bg: ${tagColor}; --profile-tag-border: ${tagColor}; --profile-tag-text: #FFFFFF;">@${tag}</span>`;
                                }).join('')}
                            </div>
                        </div>
                    `;
                }

                let errorHtml = '';
                if (details.error) {
                    errorHtml = `<div class="mt-2 p-2 bg-rose-900/30 rounded border border-rose-500/30 text-rose-300">${escapeHtml(details.error)}</div>`;
                }

                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Status</div>
                        <div class="status-kv-value ${statusClass} font-semibold">${statusText}</div>
                        <div class="status-kv-key">Profiles Used</div>
                        <div class="status-kv-value">${usedCount}</div>
                        <div class="status-kv-key">Total Time</div>
                        <div class="status-kv-value">${totalDuration}</div>
                        ${tokensHtml}
                        ${costHtml}
                    </div>
                    ${profilesHtml}
                    ${errorHtml}
                `;
                break;
            }

            case 'session_name_generation_start': {
                const summary = details.summary || 'Generating session name...';
                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Status</div>
                        <div class="status-kv-value text-amber-400">In Progress...</div>
                    </div>
                `;
                break;
            }

            case 'session_name_generation_complete': {
                const sessionName = details.session_name || 'Unknown';
                const inputTokens = details.input_tokens || 0;
                const outputTokens = details.output_tokens || 0;
                const costUsd = details.cost_usd || null;

                let costHtml = '';
                if (costUsd !== null && costUsd !== undefined) {
                    costHtml = `
                        <div class="status-kv-key">Cost</div>
                        <div class="status-kv-value text-amber-300">$${costUsd.toFixed(6)}</div>
                    `;
                }

                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Name</div>
                        <div class="status-kv-value"><code class="status-code text-emerald-300">${sessionName}</code></div>
                        <div class="status-kv-key">Tokens</div>
                        <div class="status-kv-value">${inputTokens} in / ${outputTokens} out</div>
                        ${costHtml}
                    </div>
                `;
                break;
            }

            case 'next_step_generation_complete': {
                detailsEl.innerHTML = _renderNextStepCompleteDetails(details);
                break;
            }

            case 'proposal_suggestion_complete':
            case 'proposal_suggestion':
            case 'memory_write': {
                detailsEl.innerHTML = _renderPostTurnLlmCallDetails(details);
                break;
            }


            case 'grounding_gate': {
                // Light this station the moment the gate reports (R-2 live dial).
                // Both render paths get it — a gate that draws a card must also
                // move the instrument, or the two disagree about what happened.
                try {
                    if (window.AssuranceDial && window.AssuranceDial.note) {
                        window.AssuranceDial.note(_adSession(), 'grounding_gate', details);
                    }
                } catch (_) { /* a live update must never break the trace */ }
                if (_assuranceCardSuppressed(type, details)) { stepEl.remove(); return; }
                detailsEl.innerHTML = _renderGroundingGateDetails(details);
                break;
            }

            case 'guardrail': {
                // Light this station the moment the gate reports (R-2 live dial).
                // Both render paths get it — a gate that draws a card must also
                // move the instrument, or the two disagree about what happened.
                try {
                    if (window.AssuranceDial && window.AssuranceDial.note) {
                        window.AssuranceDial.note(_adSession(), 'guardrail', details);
                    }
                } catch (_) { /* a live update must never break the trace */ }
                detailsEl.innerHTML = _renderGuardrailDetails(details);
                break;
            }

            case 'obda': {
                // Light this station the moment the gate reports (R-2 live dial).
                // Both render paths get it — a gate that draws a card must also
                // move the instrument, or the two disagree about what happened.
                try {
                    if (window.AssuranceDial && window.AssuranceDial.note) {
                        window.AssuranceDial.note(_adSession(), 'obda', details);
                    }
                } catch (_) { /* a live update must never break the trace */ }
                if (_assuranceCardSuppressed(type, details)) { stepEl.remove(); return; }
                detailsEl.innerHTML = _renderObdaDetails(details);
                break;
            }

            case 'compute_lineage': {
                try {
                    if (window.AssuranceDial && window.AssuranceDial.note) {
                        window.AssuranceDial.note(_adSession(), 'compute_lineage', details);
                    }
                } catch (_) { /* a live update must never break the trace */ }
                detailsEl.innerHTML = _renderComputeLineageDetails(details);
                break;
            }

            case 'llm_recovery': {
                try {
                    if (window.AssuranceDial && window.AssuranceDial.note) {
                        window.AssuranceDial.note(_adSession(), 'llm_recovery', details);
                    }
                } catch (_) { /* a live update must never break the trace */ }
                detailsEl.innerHTML = _renderLlmRecoveryDetails(details);
                break;
            }

            case 'turn_budget': {
                try {
                    if (window.AssuranceDial && window.AssuranceDial.note) {
                        window.AssuranceDial.note(_adSession(), 'turn_budget', details);
                    }
                } catch (_) { /* a live update must never break the trace */ }
                detailsEl.innerHTML = _renderTurnBudgetDetails(details);
                break;
            }

            case 'action_record': {
                try {
                    if (window.AssuranceDial && window.AssuranceDial.note) {
                        window.AssuranceDial.note(_adSession(), 'action_record', details);
                    }
                } catch (_) { /* a live update must never break the trace */ }
                detailsEl.innerHTML = _renderActionRecordDetails(details);
                break;
            }

            case 'tool_surface': {
                try {
                    if (window.AssuranceDial && window.AssuranceDial.note) {
                        window.AssuranceDial.note(_adSession(), 'tool_surface', details);
                    }
                } catch (_) { /* a live update must never break the trace */ }
                detailsEl.innerHTML = _renderToolSurfaceDetails(details);
                break;
            }

            case 'steer_consumed': {
                detailsEl.innerHTML = _renderSteerConsumedDetails(details);
                try { if (window.SteerReattach && window.SteerReattach.consumed) window.SteerReattach.consumed({ ...details, session_id: _adSession() }); } catch (_) { /* never breaks the trace */ }
                break;
            }

            case 'attachment_sources': {
                try {
                    if (window.AssuranceDial && window.AssuranceDial.note) {
                        window.AssuranceDial.note(_adSession(), 'attachment_sources', details);
                    }
                } catch (_) { /* a live update must never break the trace */ }
                detailsEl.innerHTML = _renderAttachmentSourcesDetails(details);
                try { if (window.AttachmentSources && window.AttachmentSources.markUsed) window.AttachmentSources.markUsed(_adSession(), details); } catch (_) { /* never breaks the trace */ }
                break;
            }

            case 'outcome_revision': {
                try {
                    if (window.AssuranceDial && window.AssuranceDial.note) {
                        window.AssuranceDial.note(_adSession(), 'outcome_loop', details);
                    }
                } catch (_) { /* a live update must never break the trace */ }
                detailsEl.innerHTML = _renderOutcomeRevisionDetails(details);
                break;
            }

            case 'honesty': {
                // Light this station the moment the gate reports (R-2 live dial).
                // Both render paths get it — a gate that draws a card must also
                // move the instrument, or the two disagree about what happened.
                try {
                    if (window.AssuranceDial && window.AssuranceDial.note) {
                        window.AssuranceDial.note(_adSession(), 'honesty', details);
                    }
                } catch (_) { /* a live update must never break the trace */ }
                detailsEl.innerHTML = _renderHonestyDetails(details);
                break;
            }

            case 'kg_enrichment': {
                detailsEl.innerHTML = _renderKgEnrichmentDetails(details);
                break;
            }

            case 'judge_evaluation_complete': {
                detailsEl.innerHTML = _renderJudgeEvalCompleteDetails(details);
                break;
            }

            case 'review_decision': {
                detailsEl.innerHTML = _renderReviewDecisionDetails(details);
                break;
            }

            case 'genie_component_invoked': {
                const compToolName = details.tool_name || 'Component Tool';
                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Tool</div>
                        <div class="status-kv-value"><code class="status-code text-cyan-300">${compToolName}</code></div>
                        <div class="status-kv-key">Status</div>
                        <div class="status-kv-value text-amber-400">Executing...</div>
                    </div>
                `;
                break;
            }

            case 'genie_component_completed': {
                const compToolNameDone = details.tool_name || 'Component Tool';
                const componentId = details.component_id || 'unknown';
                const compStatusText = details.success ? '✓ Success' : '✗ Failed';
                const compStatusClass = details.success ? 'text-emerald-400' : 'text-rose-400';
                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Tool</div>
                        <div class="status-kv-value"><code class="status-code text-cyan-300">${compToolNameDone}</code></div>
                        <div class="status-kv-key">Component</div>
                        <div class="status-kv-value">${componentId}</div>
                        <div class="status-kv-key">Status</div>
                        <div class="status-kv-value ${compStatusClass}">${compStatusText}</div>
                    </div>
                `;
                break;
            }

            case 'execution_start':
            case 'execution_complete':
            case 'execution_error':
                // Skip rendering - these lifecycle events are redundant for genie profiles
                // genie_coordination_start and genie_coordination_complete already show this info
                return;

            default:
                // Generic fallback with JSON display
                try {
                    const jsonStr = JSON.stringify(details, null, 2);
                    detailsEl.innerHTML = `
                        <details>
                            <summary class="cursor-pointer text-gray-400 hover:text-white">View Details</summary>
                            <pre class="mt-1 p-2 bg-gray-900/50 rounded text-gray-400 overflow-x-auto">${jsonStr}</pre>
                        </details>
                    `;
                } catch (e) {
                    detailsEl.innerHTML = `<span class="text-gray-500">[Unable to display details]</span>`;
                }
        }

        stepEl.appendChild(detailsEl);
    }

    parentContainer.appendChild(stepEl);

    // Auto-scroll to latest step (skip during historical replay to keep scroll at top)
    if (!state.isViewingHistoricalTurn) {
        stepEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    // Reset genie coordination state on completion
    if (type === 'genie_coordination_complete') {
        state.isGenieCoordinationActive = false;
    }
}

/**
 * Exported wrapper for rendering genie events during plan reload.
 * Uses the same rendering logic as live execution for consistent UX.
 */
export function renderGenieStepForReload(eventData, parentContainer, isFinal = false) {
    if (!eventData._filterCategory) eventData._filterCategory = 'coordination';
    _renderGenieStep(eventData, parentContainer, isFinal);
}

/**
 * Render Conversation Agent tool execution events in the status window.
 * Uses blue/cyan-themed styling to differentiate from genie (purple) events.
 */
/**
 * Render conversation agent events in the Live Status window.
 * Uses consistent styling with tool_enabled profiles for a world-class UX.
 *
 * ARCHITECTURE: All tool execution visualization is in Live Status window only.
 * The chat pane stays clean with just Q&A - this panel shows the "how".
 */

// ---------------------------------------------------------------------------
// Progressive disclosure — cost attribution (PROP-005).
//
// A library fetch is a TOOL call, so its cost does not land on the fetch: it
// lands on the NEXT LLM call, where the section is now part of the prompt (and
// on every later call of the turn, since context is cumulative). The trace
// therefore showed input jumping 6,397 -> 11,006 with nothing saying why.
//
// The attribution is DERIVED from the ordered event list rather than plumbed
// through the backend, for two reasons:
//   * live and reload replay the SAME ordered list through the same renderers,
//     so a derivation cannot drift between them — it stands a plan reload by
//     construction rather than by a second persistence path;
//   * a contextvar written by the component and drained by the emitter would be
//     invisible across sibling tasks when Optimize runs phases in parallel —
//     the exact hazard the governed-SQL registry hit.
//
// Sections loaded since the previous LLM step are attributed to that step.
// ---------------------------------------------------------------------------
let _pdPendingSections = [];

function _pdReset() { _pdPendingSections = []; }

function _pdNoteSection(details) {
    if (!details) return;
    const chars = Number(details.chars) || 0;
    _pdPendingSections.push({
        anchor: details.anchor || details.skill_id || 'section',
        chars,
        tokens: Math.round(chars / 4),      // the platform's own len/4 heuristic
    });
}

function _pdTagHtmlGrid() {
    const inner = _pdTagHtml();
    return inner ? `<div class="status-kv-grid">${inner}</div>` : '';
}

/** S2 — the cached share of a call's prompt, shown ONLY when something was served from
 *  cache (a warm cache is invisible; the exception is marked). */
function _cachedTokensHtml(details) {
    const c = Number((details && details.cached_tokens) || 0);
    if (!(c > 0)) return '';
    const inp = Number((details && details.input_tokens) || 0);
    const share = inp > 0 ? ` (${Math.round(100 * c / inp)}%)` : '';
    return `<div class="status-kv-key">Cached</div>
            <div class="status-kv-value" title="Prompt tokens the provider served from its prompt cache — billed at the cached rate">${c.toLocaleString()} of ${inp.toLocaleString()} prompt tokens served from cache${share}</div>`;
}

function _pdTagHtml() {
    if (!_pdPendingSections.length) return '';
    const loaded = _pdPendingSections;
    _pdPendingSections = [];
    const tokens = loaded.reduce((a, s) => a + s.tokens, 0);
    const names = loaded.map(s => escapeHtml(String(s.anchor))).join(', ');
    return `
        <div class="status-kv-key">Progressive disclosure</div>
        <div class="status-kv-value" style="color: var(--text-muted);">
            includes ${loaded.length} newly-loaded section${loaded.length === 1 ? '' : 's'}
            (~${tokens.toLocaleString()} tokens): <span style="color: var(--text-primary);">${names}</span>
        </div>
    `;
}

function _renderConversationAgentStep(eventData, parentContainer, isFinal = false) {
    if (_consumeAssuranceProgress(eventData)) return;
    const { step, details, type } = eventData;

    // EARLY SKIP: execution_complete for genie profiles is redundant
    // genie_coordination_complete already shows this info with synthesized response
    // Also check for case variations just in case
    const profileType = details?.profile_type?.toLowerCase?.() || details?.profile_type || '';
    if (type === 'execution_complete' && profileType === 'genie') {
        console.log('[_renderConversationAgentStep] Skipping redundant execution_complete for genie profile');
        return;  // Early exit before any DOM creation
    }

    // CRITICAL FIX: For session name generation complete, UPDATE the existing "Generating..." step
    // Use deterministic data-event-type selector (set by START event) with fallback to .active
    if (type === 'session_name_generation_complete') {
        const lastStep = parentContainer.querySelector('.status-step[data-event-type="session_name_generation_start"]')
                      || parentContainer.querySelector('.status-step.active');

        if (lastStep) {
            // Update the existing step as completed
            lastStep.classList.remove('active');
            lastStep.classList.add('completed');

            // Update details section with session name and tokens
            // Session name events use .text-xs instead of .status-details
            const detailsEl = lastStep.querySelector('.text-xs') || lastStep.querySelector('.status-details');

            if (detailsEl) {
                const sessionName = details.session_name || details.name || 'Unknown';
                const inputTokens = details.input_tokens || 0;
                const outputTokens = details.output_tokens || 0;
                const costUsd = details.cost_usd;

                // Build cost HTML if cost is available
                let costHtml = '';
                if (costUsd !== null && costUsd !== undefined && costUsd > 0) {
                    costHtml = `
                        <div class="status-kv-key">Cost</div>
                        <div class="status-kv-value text-amber-300">$${costUsd.toFixed(6)}</div>
                    `;
                }

                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Name</div>
                        <div class="status-kv-value"><code class="status-code text-emerald-300">${sessionName}</code></div>
                        <div class="status-kv-key">Tokens</div>
                        <div class="status-kv-value">${inputTokens} in / ${outputTokens} out</div>
                        ${costHtml}
                    </div>
                `;
            }

            // Scroll to updated step (skip during historical replay)
            if (!state.isViewingHistoricalTurn) {
                lastStep.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }
            return; // Exit early - we updated existing step, no need to create new one
        }
    }

    // CRITICAL FIX: For tool completion, UPDATE the existing "Executing..." step instead of creating a new one
    // When multiple tools run in parallel, we match by tool_name to find the correct step
    if (type === 'conversation_tool_completed') {
        const toolName = details?.tool_name;
        console.log('[LiveStatus] Received conversation_tool_completed:', {toolName, details, hasResultPreview: !!details?.result_preview});

        // Find the executing step for THIS specific tool (not just any active step)
        // Use data-tool-status="executing" to distinguish from already-completed tools
        let lastStep = null;
        if (toolName) {
            lastStep = parentContainer.querySelector(
                `.status-step[data-tool-name="${toolName}"][data-tool-status="executing"]`
            );
            console.log('[LiveStatus] querySelector result for tool:', toolName, 'found:', !!lastStep);
        }

        // Fallback to any active step (backwards compatibility)
        // CRITICAL: Only use fallback if it's actually a tool execution step, not an LLM step
        if (!lastStep) {
            const activeStep = parentContainer.querySelector('.status-step.active');
            // Check if the active step is a tool step (has data-tool-name) or an LLM step
            // We should NOT overwrite LLM synthesis steps with tool completion data
            if (activeStep && activeStep.dataset.toolName) {
                lastStep = activeStep;
                console.log('[LiveStatus] Fallback to .active tool step, found:', !!lastStep);
            } else {
                console.log('[LiveStatus] Fallback skipped - active step is not a tool step (likely LLM synthesis)');
            }
        }

        if (lastStep) {
            console.log('[LiveStatus] Updating existing step for tool:', toolName);
            // Mark as completed so next completion for same tool finds a different element
            lastStep.dataset.toolStatus = 'completed';

            // Update the existing step as completed
            lastStep.classList.remove('active');
            lastStep.classList.add('completed');

            // Update details section with completion status
            const detailsEl = lastStep.querySelector('.status-details');
            if (detailsEl) {
                const displayToolName = toolName || 'Unknown';
                const duration = details.duration_ms ? `${(details.duration_ms / 1000).toFixed(2)}s` : 'N/A';
                const statusText = details.success ? '✓ Success' : '✗ Failed';
                const statusClass = details.success ? 'text-emerald-400' : 'text-rose-400';

                let resultHtml = '';
                if (details.result_preview) {
                    resultHtml = `
                        <details class="mt-2">
                            <summary class="cursor-pointer text-gray-400 hover:text-white">View Result Preview</summary>
                            <div class="mt-1 p-2 bg-gray-900/50 rounded text-gray-300 whitespace-pre-wrap">${escapeHtml(details.result_preview)}</div>
                        </details>
                    `;
                }

                let errorHtml = '';
                if (details.error) {
                    errorHtml = `<div class="mt-2 p-2 bg-rose-900/30 rounded border border-rose-500/30 text-rose-300">${escapeHtml(details.error)}</div>`;
                }

                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Tool</div>
                        <div class="status-kv-value"><code class="status-code ${details.success ? 'text-emerald-300' : 'text-rose-300'}">${displayToolName}</code></div>
                        ${mcpSourceKv(details.source)}
                        <div class="status-kv-key">Status</div>
                        <div class="status-kv-value ${statusClass}">${statusText}</div>
                        <div class="status-kv-key">Duration</div>
                        <div class="status-kv-value">${duration}</div>
                    </div>
                    ${resultHtml}
                    ${errorHtml}
                `;
            }

            // Scroll to updated step (skip during historical replay)
            if (!state.isViewingHistoricalTurn) {
                lastStep.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }
            return; // Exit early - we updated existing step, no need to create new one
        } else {
            // During historical replay this is expected — completed events arrive without prior started steps
            if (state.isViewingHistoricalTurn) {
                console.debug('[LiveStatus] Historical replay: creating new step for tool_completed:', toolName);
            } else {
                console.warn('[LiveStatus] conversation_tool_completed did NOT find executing step for tool:', toolName, '- will create new step instead');
            }
        }
    }

    // Mark previous active step as completed and stop icon animations
    // Mark previous active step as completed
    const lastStep = parentContainer.querySelector('.status-step.active');
    if (lastStep) {
        lastStep.classList.remove('active');
        lastStep.classList.add('completed');

        // Remove progress indicator when stage completes
        const progressIndicator = lastStep.querySelector('.progress-indicator');
        if (progressIndicator) {
            progressIndicator.remove();
        }
    }

    const stepEl = document.createElement('div');
    stepEl.className = 'status-step p-3 rounded-md mb-2 conversation-agent-status-step';
    _stampLa(stepEl, eventData);

    // Tag with filter category for event filter chips
    if (eventData._filterCategory) {
        stepEl.dataset.filterCategory = eventData._filterCategory;
        if (!isFilterCategoryEnabled(eventData._filterCategory)) {
            stepEl.classList.add('event-filtered-hidden');
        }
    }

    // Add data attributes for tool matching during live execution
    // This allows completion events to find the correct executing step when multiple tools run in parallel
    if (type === 'conversation_tool_invoked' && details?.tool_name) {
        stepEl.dataset.toolName = details.tool_name;
        stepEl.dataset.toolStatus = 'executing';
        console.log('[LiveStatus] Set data attributes on invoked step:', {toolName: details.tool_name, toolStatus: 'executing'});
    }
    // Tag session name START card so the COMPLETE handler can find it deterministically
    if (type === 'session_name_generation_start') {
        stepEl.dataset.eventType = 'session_name_generation_start';
    }

    if (isFinal) {
        stepEl.classList.add('completed');
    } else {
        stepEl.classList.add('active');
    }

    // Add category class for background color (aligned with filter pill colors)
    const category = getEventCategory(type, eventData._filterCategory);
    stepEl.classList.add(category);
    if (type === 'conversation_tool_rewrite') stepEl.classList.add('workaround');   // the Optimize System Self-Correction look
    _stampTrustGuideContext(stepEl, type, details);

    // Note: conversation_agent_complete is a 'lifecycle' bookend event
    // Its styling is handled by the category class (lifecycle = green sidebar, no background)
    // Do NOT add conv-agent-success class as it would override the transparent background

    // Step header (text-only, matching Optimizer style)
    const stepHeader = document.createElement('div');
    stepHeader.className = 'flex items-center gap-2 mb-2';

    const titleEl = document.createElement('h4');
    titleEl.className = 'font-bold text-sm text-white';
    titleEl.textContent = step || 'Tool Execution';

    // Add progress indicator for active rag_focused stages
    if ((type === 'knowledge_retrieval_start' || type === 'rag_llm_step' ||
         type === 'knowledge_reranking_start') && !isFinal) {
        const progressLabel = document.createElement('span');
        progressLabel.className = 'text-amber-400 text-xs ml-2 progress-indicator';
        progressLabel.textContent = 'Processing...';
        titleEl.appendChild(progressLabel);
    }

    stepHeader.appendChild(titleEl);

    stepEl.appendChild(stepHeader);

    // Render details based on event type
    if (details && typeof details === 'object') {
        const detailsEl = document.createElement('div');
        detailsEl.className = 'status-details text-xs';

        switch (type) {
            case 'conversation_agent_start': {
                _pdReset();
                const toolCount = details.available_tools?.length || 0;
                const profileTag = details.profile_tag || 'CONV';
                const profileType = details.profile_type || 'conversation_with_tools';
                const query = details.query || '';
                const queryPreview = query.length > 100 ? query.substring(0, 100) + '...' : query;
                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Profile</div>
                        <div class="status-kv-value">${renderProfileTag(profileTag, profileType)}</div>
                        <div class="status-kv-key">Type</div>
                        <div class="status-kv-value">${profileType}</div>
                        ${queryPreview ? `
                        <div class="status-kv-key">Query</div>
                        <div class="status-kv-value text-gray-300">"${queryPreview}"</div>
                        ` : ''}
                        <div class="status-kv-key">Tools</div>
                        <div class="status-kv-value">${toolCount} available</div>
                    </div>
                    ${details.available_tools && details.available_tools.length > 0 ? `
                        <details class="mt-2">
                            <summary class="cursor-pointer text-gray-400 hover:text-white">View Available Tools</summary>
                            <div class="conv-agent-tool-tags mt-2 flex flex-wrap gap-1">
                                ${details.available_tools.map(t => `<span class="conv-agent-tag">${t}</span>`).join('')}
                            </div>
                        </details>
                    ` : ''}
                `;
                break;
            }

            case 'skill_section_loaded': {
                _pdNoteSection(details);
                const _sTok = Math.round((Number(details.chars) || 0) / 4);
                // `detailsEl.innerHTML`, which is THIS function's convention (29 other
                // cases). An earlier version wrote a bare `detailsHtml` — a name borrowed
                // from a different renderer — and threw ReferenceError on every reload of
                // a turn that loaded a section. §J below executes every case to stop the
                // whole class, not this instance.
                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Section</div>
                        <div class="status-kv-value">${escapeHtml(String(details.anchor || ''))}</div>
                        <div class="status-kv-key">Size</div>
                        <div class="status-kv-value">${(Number(details.chars) || 0).toLocaleString()} chars (~${_sTok.toLocaleString()} tokens)</div>
                        <div class="status-kv-key">Cost</div>
                        <div class="status-kv-value" style="color: var(--text-muted);">none here — it lands on the next LLM call, which now carries this section</div>
                    </div>
                `;
                break;
            }

            case 'conversation_llm_step': {
                const stepNumber = details.step_number || '?';
                const stepName = details.step_name || `Step ${stepNumber}`;
                const inputTokens = details.input_tokens || 0;
                const outputTokens = details.output_tokens || 0;
                const durationMs = details.duration_ms || 0;
                const duration = durationMs > 0 ? `${(durationMs / 1000).toFixed(2)}s` : 'N/A';
                const provider = details.provider || state.currentProvider || 'Unknown';
                const model = details.model || state.currentModel || 'Unknown';
                const costUsd = details.cost_usd || null;

                let costHtml = '';
                if (costUsd !== null && costUsd !== undefined) {
                    costHtml = `
                        <div class="status-kv-key">Cost</div>
                        <div class="status-kv-value text-amber-300">$${costUsd.toFixed(6)}</div>
                    `;
                }

                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Step</div>
                        <div class="status-kv-value"><code class="status-code text-indigo-300">#${stepNumber}: ${stepName}</code></div>
                        <div class="status-kv-key">Provider</div>
                        <div class="status-kv-value"><code class="status-code text-blue-300">${provider}</code></div>
                        <div class="status-kv-key">Model</div>
                        <div class="status-kv-value"><code class="status-code text-emerald-300">${model}</code></div>
                        <div class="status-kv-key">Status</div>
                        <div class="status-kv-value text-emerald-400">✓ Complete</div>
                        <div class="status-kv-key">Tokens</div>
                        <div class="status-kv-value">${inputTokens.toLocaleString()} in / ${outputTokens.toLocaleString()} out</div>
                        ${_cachedTokensHtml(details)}
                        ${costHtml}
                        ${_pdTagHtml()}
                        <div class="status-kv-key">Duration</div>
                        <div class="status-kv-value">${duration}</div>
                    </div>
                `;
                break;
            }

            case 'conversation_tool_invoked': {
                const toolName = details.tool_name || 'Unknown';
                console.log('[LiveStatus] Rendering conversation_tool_invoked for tool:', toolName, 'details:', details);
                let argsHtml = '';
                if (details.arguments) {
                    try {
                        // Clean up runtime noise from arguments if present
                        let cleanArgs = details.arguments;
                        if (typeof cleanArgs === 'object' && cleanArgs.runtime) {
                            // Extract just the meaningful parts, not the runtime internals
                            cleanArgs = { ...cleanArgs };
                            delete cleanArgs.runtime;
                        }
                        const argsStr = typeof cleanArgs === 'string' ? cleanArgs : JSON.stringify(cleanArgs, null, 2);
                        if (argsStr && argsStr !== '{}') {
                            argsHtml = `
                                <details class="mt-2">
                                    <summary class="cursor-pointer text-gray-400 hover:text-white">View Arguments</summary>
                                    <div class="mt-1 p-2 bg-gray-900/50 rounded status-text-block whitespace-pre-wrap text-gray-300">${argsStr}</div>
                                </details>
                            `;
                        }
                    } catch (e) {
                        argsHtml = '';
                    }
                }
                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Tool</div>
                        <div class="status-kv-value"><code class="status-code text-amber-300">${toolName}</code></div>
                        ${mcpSourceKv(details.source)}
                        <div class="status-kv-key">Status</div>
                        <div class="status-kv-value text-amber-400">Executing...</div>
                    </div>
                    ${argsHtml}
                `;
                break;
            }

            case 'conversation_tool_completed': {
                const toolName = details.tool_name || 'Unknown';
                const duration = details.duration_ms ? `${(details.duration_ms / 1000).toFixed(2)}s` : 'N/A';
                const statusText = details.success ? '✓ Success' : '✗ Failed';
                const statusClass = details.success ? 'text-emerald-400' : 'text-rose-400';

                let resultHtml = '';
                if (details.result_preview) {
                    resultHtml = `
                        <details class="mt-2">
                            <summary class="cursor-pointer text-gray-400 hover:text-white">View Result Preview</summary>
                            <div class="mt-1 p-2 bg-gray-900/50 rounded text-gray-300 whitespace-pre-wrap">${escapeHtml(details.result_preview)}</div>
                        </details>
                    `;
                }

                let errorHtml = '';
                if (details.error) {
                    errorHtml = `<div class="mt-2 p-2 bg-rose-900/30 rounded border border-rose-500/30 text-rose-300">${escapeHtml(details.error)}</div>`;
                }

                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Tool</div>
                        <div class="status-kv-value"><code class="status-code ${details.success ? 'text-emerald-300' : 'text-rose-300'}">${toolName}</code></div>
                        ${mcpSourceKv(details.source)}
                        <div class="status-kv-key">Status</div>
                        <div class="status-kv-value ${statusClass}">${statusText}</div>
                        <div class="status-kv-key">Duration</div>
                        <div class="status-kv-value">${duration}</div>
                    </div>
                    ${resultHtml}
                    ${errorHtml}
                `;
                break;
            }

            case 'conversation_tool_rewrite': {
                // A statement the model wrote was rewritten deterministically before it ran —
                // the executed SQL is the fact this card exists for (the tool result's preview
                // is bounded, so on a long result the rewrite would otherwise be invisible here).
                const kindLabel = escapeHtml(String(details.label || details.kind || 'rewrite'));
                const summary = details.summary ? escapeHtml(String(details.summary)) : '';
                const changes = Array.isArray(details.changes) && details.changes.length
                    ? `<ul class="mt-1 ml-4 list-disc text-gray-300">${details.changes.map(c => `<li>${escapeHtml(String(c))}</li>`).join('')}</ul>` : '';
                const grain = details.grain ? `<div class="status-kv-key">Result grain</div><div class="status-kv-value">${escapeHtml(String(details.grain))}</div>` : '';
                const sqlBlock = (label, sql, open) => sql ? `
                        <details class="mt-2"${open ? ' open' : ''}>
                            <summary class="cursor-pointer text-gray-400 hover:text-white">${label}</summary>
                            <pre class="mt-1 p-2 bg-gray-900/50 rounded text-gray-300 whitespace-pre-wrap text-xs">${escapeHtml(String(sql))}</pre>
                        </details>` : '';
                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Correction</div>
                        <div class="status-kv-value"><code class="status-code text-amber-300">${kindLabel}</code></div>
                        <div class="status-kv-key">Tool</div>
                        <div class="status-kv-value"><code class="status-code">${escapeHtml(String(details.tool_name || 'tool'))}</code></div>
                        <div class="status-kv-key">LLM rounds</div>
                        <div class="status-kv-value">0 — applied deterministically before the statement ran</div>
                        ${grain}
                    </div>
                    ${summary ? `<div class="mt-2 text-gray-300">${summary}</div>` : ''}
                    ${changes}
                    ${sqlBlock('Executed SQL (what actually ran)', details.executed_sql, true)}
                    ${sqlBlock('Original statement (as the model wrote it)', details.original_sql, false)}
                `;
                break;
            }

            case 'conversation_agent_complete': {
                const totalDuration = details.total_duration_ms ? `${(details.total_duration_ms / 1000).toFixed(2)}s` : 'N/A';
                const toolCount = details.tools_used?.length || 0;
                const statusText = details.success ? '✓ Complete' : '✗ Failed';
                const statusClass = details.success ? 'text-emerald-400' : 'text-rose-400';
                const completeProfileTag = details.profile_tag || 'CONV';
                const completeProfileType = details.profile_type || 'conversation_with_tools';

                // Token counts (if available)
                const inputTokens = details.input_tokens;
                const outputTokens = details.output_tokens;
                let tokensHtml = '';
                if (inputTokens !== undefined && outputTokens !== undefined && (inputTokens > 0 || outputTokens > 0)) {
                    tokensHtml = `
                        <div class="status-kv-key">Tokens</div>
                        <div class="status-kv-value">${inputTokens.toLocaleString()} in / ${outputTokens.toLocaleString()} out</div>
                    `;
                }

                // Cost (if available)
                const costUsd = details.cost_usd != null ? parseFloat(details.cost_usd) : null;
                let costHtml = '';
                if (costUsd !== null) {
                    costHtml = `
                        <div class="status-kv-key">Cost</div>
                        <div class="status-kv-value text-amber-300">$${costUsd.toFixed(6)}</div>
                    `;
                }

                let toolsHtml = '';
                if (details.tools_used && details.tools_used.length > 0) {
                    toolsHtml = `
                        <div class="mt-2">
                            <div class="text-gray-500 text-xs mb-1">Tools Executed:</div>
                            <div class="conv-agent-tool-tags flex flex-wrap gap-1">
                                ${details.tools_used.map(t => `<span class="conv-agent-tag completed">${t}</span>`).join('')}
                            </div>
                        </div>
                    `;
                }

                let errorHtml = '';
                if (details.error) {
                    errorHtml = `<div class="mt-2 p-2 bg-rose-900/30 rounded border border-rose-500/30 text-rose-300">${escapeHtml(details.error)}</div>`;
                }

                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Status</div>
                        <div class="status-kv-value ${statusClass} font-semibold">${statusText}</div>
                        <div class="status-kv-key">Profile</div>
                        <div class="status-kv-value">${renderProfileTag(completeProfileTag, completeProfileType)}</div>
                        <div class="status-kv-key">Tools Used</div>
                        <div class="status-kv-value">${toolCount}</div>
                        <div class="status-kv-key">Total Time</div>
                        <div class="status-kv-value">${totalDuration}</div>
                        ${tokensHtml}
                        ${costHtml}
                    </div>
                    ${toolsHtml}
                    ${errorHtml}
                `;
                break;
            }

            case 'knowledge_retrieval': {
                // Knowledge retrieval event - use blue theme (Focus profile color)
                stepEl.classList.remove('conversation-agent-status-step');
                stepEl.classList.add('knowledge-retrieval-status-step');

                const collections = details.collections || [];
                const chunks = details.chunks || [];
                const documentCount = details.document_count || chunks.length;

                let chunksHtml = '';
                if (chunks.length > 0) {
                    const chunkPreviews = chunks.map((chunk, idx) => {
                        const similarity = chunk.similarity_score ? (chunk.similarity_score * 100).toFixed(1) : 'N/A';
                        const content = chunk.content || '';
                        const preview = content.length > 150 ? content.substring(0, 147) + '...' : content;
                        const source = chunk.source || 'Unknown';
                        return `
                            <div class="p-2 bg-gray-900/50 rounded border border-indigo-500/30">
                                <div class="flex justify-between items-start mb-1">
                                    <span class="text-indigo-400 font-semibold">Chunk ${idx + 1}</span>
                                    <span class="text-gray-500">Relevance: ${similarity}%</span>
                                </div>
                                <div class="text-gray-500 text-xs mb-1">Source: ${source}</div>
                                <div class="text-gray-300 whitespace-pre-wrap">${preview}</div>
                            </div>
                        `;
                    }).join('');

                    chunksHtml = `
                        <details class="mt-2">
                            <summary class="cursor-pointer text-gray-400 hover:text-white">
                                View Retrieved Chunks (${chunks.length})
                            </summary>
                            <div class="space-y-2 mt-2">${chunkPreviews}</div>
                        </details>
                    `;
                }

                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Collections</div>
                        <div class="status-kv-value">${collections.length > 0 ? collections.join(', ') : 'N/A'}</div>
                        <div class="status-kv-key">Documents</div>
                        <div class="status-kv-value">${documentCount} chunks retrieved</div>
                    </div>
                    ${chunksHtml}
                `;

                // Trigger the knowledge indicator
                if (collections.length > 0) {
                    // Only blink during live execution, not when viewing historical turns
                    if (!state.isViewingHistoricalTurn) {
                        blinkKnowledgeDot();
                    }
                    updateKnowledgeIndicator(collections, documentCount);
                }
                break;
            }

            case 'knowledge_retrieval_start': {
                stepEl.classList.add('knowledge-retrieval-status-step');
                const collections = details.collections || [];
                const maxDocs = details.max_docs || 0;

                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Collections</div>
                        <div class="status-kv-value">${collections.length > 0 ? collections.join(', ') : 'N/A'}</div>
                        <div class="status-kv-key">Target</div>
                        <div class="status-kv-value">Up to ${maxDocs} documents</div>
                    </div>
                `;
                break;
            }

            case 'knowledge_reranking_start': {
                stepEl.classList.add('knowledge-retrieval-status-step');
                const collection = details.collection || 'Unknown';
                const docCount = details.document_count || 0;

                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Collection</div>
                        <div class="status-kv-value">${collection}</div>
                        <div class="status-kv-key">Documents</div>
                        <div class="status-kv-value">${docCount} documents to rerank</div>
                    </div>
                `;
                break;
            }

            case 'knowledge_reranking_complete': {
                stepEl.classList.add('knowledge-retrieval-status-step');
                const collection = details.collection || 'Unknown';
                const rerankedCount = details.reranked_count || 0;

                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Collection</div>
                        <div class="status-kv-value">${collection}</div>
                        <div class="status-kv-key">Result</div>
                        <div class="status-kv-value text-emerald-400">${rerankedCount} documents reranked</div>
                    </div>
                `;
                break;
            }

            case 'knowledge_retrieval_complete': {
                // Same styling as knowledge_retrieval
                stepEl.classList.remove('conversation-agent-status-step');
                stepEl.classList.add('knowledge-retrieval-status-step');

                const collections = details.collections || [];
                const chunks = details.chunks || [];
                const documentCount = details.document_count || chunks.length;
                const durationMs = details.duration_ms || 0;

                let chunksHtml = '';
                if (chunks.length > 0) {
                    const chunkPreviews = chunks.map((chunk, idx) => {
                        const similarity = chunk.similarity_score ? (chunk.similarity_score * 100).toFixed(1) : 'N/A';
                        const content = chunk.content || '';
                        const preview = content.length > 150 ? content.substring(0, 147) + '...' : content;
                        const source = chunk.source || 'Unknown';
                        return `
                            <div class="p-2 bg-gray-900/50 rounded border border-indigo-500/30">
                                <div class="flex justify-between items-start mb-1">
                                    <span class="text-indigo-400 font-semibold">Chunk ${idx + 1}</span>
                                    <span class="text-gray-500">Relevance: ${similarity}%</span>
                                </div>
                                <div class="text-gray-500 text-xs mb-1">Source: ${source}</div>
                                <div class="text-gray-300 whitespace-pre-wrap">${preview}</div>
                            </div>
                        `;
                    }).join('');

                    chunksHtml = `
                        <details class="mt-2">
                            <summary class="cursor-pointer text-gray-400 hover:text-white">
                                View Retrieved Chunks (${chunks.length})
                            </summary>
                            <div class="space-y-2 mt-2">${chunkPreviews}</div>
                        </details>
                    `;
                }

                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Collections</div>
                        <div class="status-kv-value">${collections.length > 0 ? collections.join(', ') : 'N/A'}</div>
                        <div class="status-kv-key">Documents</div>
                        <div class="status-kv-value">${documentCount} chunks retrieved</div>
                        <div class="status-kv-key">Duration</div>
                        <div class="status-kv-value">${durationMs}ms</div>
                        ${details.answered_from_attachments ? `
                        <div class="status-kv-key">Answered from</div>
                        <div class="status-kv-value">The attached document — no repository held anything on this question</div>` : ''}
                    </div>
                    ${chunksHtml}
                `;

                // Trigger the knowledge indicator
                if (collections.length > 0 && !details.answered_from_attachments) {
                    // Only blink during live execution, not when viewing historical turns
                    if (!state.isViewingHistoricalTurn) {
                        blinkKnowledgeDot();
                    }
                    updateKnowledgeIndicator(collections, documentCount);
                }
                break;
            }

            case 'rag_llm_step': {
                stepEl.classList.add('knowledge-retrieval-status-step');
                const stepName = details.step_name || 'Knowledge Synthesis';
                const inputTokens = details.input_tokens || 0;
                const outputTokens = details.output_tokens || 0;
                const durationMs = details.duration_ms || 0;
                const model = details.model || 'Unknown';
                const costUsd = details.cost_usd != null ? parseFloat(details.cost_usd) : null;

                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Step</div>
                        <div class="status-kv-value"><code class="status-code text-indigo-300">${stepName}</code></div>
                        <div class="status-kv-key">Model</div>
                        <div class="status-kv-value">${model}</div>
                        <div class="status-kv-key">Tokens</div>
                        <div class="status-kv-value">${inputTokens.toLocaleString()} in / ${outputTokens.toLocaleString()} out</div>
                        ${costUsd !== null ? `
                        <div class="status-kv-key">Cost</div>
                        <div class="status-kv-value text-amber-300">$${costUsd.toFixed(6)}</div>
                        ` : ''}
                        <div class="status-kv-key">Duration</div>
                        <div class="status-kv-value">${durationMs}ms</div>
                    </div>
                `;
                break;
            }

            case 'knowledge_search_complete': {
                stepEl.classList.add('knowledge-retrieval-status-step');
                const status = details.status || 'complete';
                const collectionsSearched = details.collections_searched || 0;
                const collectionNames = details.collection_names || [];
                const docsRetrieved = details.documents_retrieved || 0;
                const totalTimeMs = details.total_time_ms || 0;
                const retrievalTimeMs = details.retrieval_time_ms || 0;
                const synthesisTimeMs = details.synthesis_time_ms || 0;
                const tokensIn = details.synthesis_tokens_in || 0;
                const tokensOut = details.synthesis_tokens_out || 0;
                const totalTimeSec = (totalTimeMs / 1000).toFixed(1);

                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Status</div>
                        <div class="status-kv-value text-emerald-400">✓ Complete</div>
                        <div class="status-kv-key">Collections</div>
                        <div class="status-kv-value">${collectionNames.join(', ')}</div>
                        <div class="status-kv-key">Documents</div>
                        <div class="status-kv-value">${docsRetrieved} retrieved</div>
                        <div class="status-kv-key">Total Time</div>
                        <div class="status-kv-value">${totalTimeSec}s</div>
                        <div class="status-kv-key">Tokens</div>
                        <div class="status-kv-value">${tokensIn.toLocaleString()} in / ${tokensOut.toLocaleString()} out</div>
                    </div>
                    <details class="mt-2">
                        <summary class="cursor-pointer text-gray-400 hover:text-white">Timing Breakdown</summary>
                        <div class="status-kv-grid mt-2">
                            <div class="status-kv-key">Retrieval</div>
                            <div class="status-kv-value">${retrievalTimeMs}ms</div>
                            <div class="status-kv-key">Synthesis</div>
                            <div class="status-kv-value">${synthesisTimeMs}ms</div>
                        </div>
                    </details>
                `;
                break;
            }

            case 'session_name_generation_start': {
                const summary = details.summary || 'Generating session name...';
                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Status</div>
                        <div class="status-kv-value text-amber-400">In Progress...</div>
                    </div>
                `;
                break;
            }

            case 'session_name_generation_complete': {
                const sessionName = details.session_name || 'Unknown';
                const inputTokens = details.input_tokens || 0;
                const outputTokens = details.output_tokens || 0;
                const costUsd = details.cost_usd || null;

                let costHtml = '';
                if (costUsd !== null && costUsd !== undefined) {
                    costHtml = `
                        <div class="status-kv-key">Cost</div>
                        <div class="status-kv-value text-amber-300">$${costUsd.toFixed(6)}</div>
                    `;
                }

                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Name</div>
                        <div class="status-kv-value"><code class="status-code text-emerald-300">${sessionName}</code></div>
                        <div class="status-kv-key">Tokens</div>
                        <div class="status-kv-value">${inputTokens} in / ${outputTokens} out</div>
                        ${costHtml}
                    </div>
                `;
                break;
            }

            case 'next_step_generation_complete': {
                detailsEl.innerHTML = _renderNextStepCompleteDetails(details);
                break;
            }

            case 'proposal_suggestion_complete':
            case 'proposal_suggestion':
            case 'memory_write': {
                detailsEl.innerHTML = _renderPostTurnLlmCallDetails(details);
                break;
            }


            case 'grounding_gate': {
                // Light this station the moment the gate reports (R-2 live dial).
                // Both render paths get it — a gate that draws a card must also
                // move the instrument, or the two disagree about what happened.
                try {
                    if (window.AssuranceDial && window.AssuranceDial.note) {
                        window.AssuranceDial.note(_adSession(), 'grounding_gate', details);
                    }
                } catch (_) { /* a live update must never break the trace */ }
                if (_assuranceCardSuppressed(type, details)) { stepEl.remove(); return; }
                detailsEl.innerHTML = _renderGroundingGateDetails(details);
                break;
            }

            case 'guardrail': {
                // Light this station the moment the gate reports (R-2 live dial).
                // Both render paths get it — a gate that draws a card must also
                // move the instrument, or the two disagree about what happened.
                try {
                    if (window.AssuranceDial && window.AssuranceDial.note) {
                        window.AssuranceDial.note(_adSession(), 'guardrail', details);
                    }
                } catch (_) { /* a live update must never break the trace */ }
                detailsEl.innerHTML = _renderGuardrailDetails(details);
                break;
            }

            case 'obda': {
                // Light this station the moment the gate reports (R-2 live dial).
                // Both render paths get it — a gate that draws a card must also
                // move the instrument, or the two disagree about what happened.
                try {
                    if (window.AssuranceDial && window.AssuranceDial.note) {
                        window.AssuranceDial.note(_adSession(), 'obda', details);
                    }
                } catch (_) { /* a live update must never break the trace */ }
                if (_assuranceCardSuppressed(type, details)) { stepEl.remove(); return; }
                detailsEl.innerHTML = _renderObdaDetails(details);
                break;
            }

            case 'compute_lineage': {
                try {
                    if (window.AssuranceDial && window.AssuranceDial.note) {
                        window.AssuranceDial.note(_adSession(), 'compute_lineage', details);
                    }
                } catch (_) { /* a live update must never break the trace */ }
                detailsEl.innerHTML = _renderComputeLineageDetails(details);
                break;
            }

            case 'llm_recovery': {
                try {
                    if (window.AssuranceDial && window.AssuranceDial.note) {
                        window.AssuranceDial.note(_adSession(), 'llm_recovery', details);
                    }
                } catch (_) { /* a live update must never break the trace */ }
                detailsEl.innerHTML = _renderLlmRecoveryDetails(details);
                break;
            }

            case 'turn_budget': {
                try {
                    if (window.AssuranceDial && window.AssuranceDial.note) {
                        window.AssuranceDial.note(_adSession(), 'turn_budget', details);
                    }
                } catch (_) { /* a live update must never break the trace */ }
                detailsEl.innerHTML = _renderTurnBudgetDetails(details);
                break;
            }

            case 'action_record': {
                try {
                    if (window.AssuranceDial && window.AssuranceDial.note) {
                        window.AssuranceDial.note(_adSession(), 'action_record', details);
                    }
                } catch (_) { /* a live update must never break the trace */ }
                detailsEl.innerHTML = _renderActionRecordDetails(details);
                break;
            }

            case 'tool_surface': {
                try {
                    if (window.AssuranceDial && window.AssuranceDial.note) {
                        window.AssuranceDial.note(_adSession(), 'tool_surface', details);
                    }
                } catch (_) { /* a live update must never break the trace */ }
                detailsEl.innerHTML = _renderToolSurfaceDetails(details);
                break;
            }

            case 'steer_consumed': {
                detailsEl.innerHTML = _renderSteerConsumedDetails(details);
                try { if (window.SteerReattach && window.SteerReattach.consumed) window.SteerReattach.consumed({ ...details, session_id: _adSession() }); } catch (_) { /* never breaks the trace */ }
                break;
            }

            case 'attachment_sources': {
                try {
                    if (window.AssuranceDial && window.AssuranceDial.note) {
                        window.AssuranceDial.note(_adSession(), 'attachment_sources', details);
                    }
                } catch (_) { /* a live update must never break the trace */ }
                detailsEl.innerHTML = _renderAttachmentSourcesDetails(details);
                try { if (window.AttachmentSources && window.AttachmentSources.markUsed) window.AttachmentSources.markUsed(_adSession(), details); } catch (_) { /* never breaks the trace */ }
                break;
            }

            case 'outcome_revision': {
                try {
                    if (window.AssuranceDial && window.AssuranceDial.note) {
                        window.AssuranceDial.note(_adSession(), 'outcome_loop', details);
                    }
                } catch (_) { /* a live update must never break the trace */ }
                detailsEl.innerHTML = _renderOutcomeRevisionDetails(details);
                break;
            }

            case 'honesty': {
                // Light this station the moment the gate reports (R-2 live dial).
                // Both render paths get it — a gate that draws a card must also
                // move the instrument, or the two disagree about what happened.
                try {
                    if (window.AssuranceDial && window.AssuranceDial.note) {
                        window.AssuranceDial.note(_adSession(), 'honesty', details);
                    }
                } catch (_) { /* a live update must never break the trace */ }
                detailsEl.innerHTML = _renderHonestyDetails(details);
                break;
            }

            case 'kg_enrichment': {
                detailsEl.innerHTML = _renderKgEnrichmentDetails(details);
                break;
            }

            case 'judge_evaluation_complete': {
                detailsEl.innerHTML = _renderJudgeEvalCompleteDetails(details);
                break;
            }

            case 'review_decision': {
                detailsEl.innerHTML = _renderReviewDecisionDetails(details);
                break;
            }

            case 'llm_execution': {
                const profileTag = details.profile_tag || 'CHAT';
                const profileType = details.profile_type;
                const profileName = details.profile_name || 'Conversation';
                const turnNumber = details.turn_number || 1;
                const historyLength = details.history_length || 0;
                const knowledgeEnabled = details.knowledge_enabled || false;
                const knowledgeCollections = details.knowledge_collections || [];
                const model = details.model || 'Unknown';
                const userMessage = details.user_message || '';

                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Profile</div>
                        <div class="status-kv-value">${renderProfileTag(profileTag, profileType)}</div>

                        <div class="status-kv-key">Mode</div>
                        <div class="status-kv-value">Direct LLM Execution</div>

                        <div class="status-kv-key">Model</div>
                        <div class="status-kv-value"><code class="status-code text-emerald-300">${model}</code></div>

                        <div class="status-kv-key">Turn</div>
                        <div class="status-kv-value">${turnNumber} (${historyLength} messages in history)</div>

                        ${knowledgeEnabled ? `
                            <div class="status-kv-key">Knowledge</div>
                            <div class="status-kv-value">Enabled (${knowledgeCollections.length} ${knowledgeCollections.length === 1 ? 'collection' : 'collections'})</div>
                        ` : ''}
                    </div>

                    ${knowledgeEnabled && knowledgeCollections.length > 0 ? `
                        <details class="mt-2">
                            <summary class="cursor-pointer text-gray-400 hover:text-white">View Knowledge Collections</summary>
                            <div class="conv-agent-tool-tags mt-2 flex flex-wrap gap-1">
                                ${knowledgeCollections.map(c => `<span class="conv-agent-tag">${c}</span>`).join('')}
                            </div>
                        </details>
                    ` : ''}

                    ${userMessage ? `
                        <details class="mt-2">
                            <summary class="cursor-pointer text-gray-400 hover:text-white">View Context Sent to LLM</summary>
                            <pre class="mt-2 p-2 bg-gray-900/50 rounded text-gray-300 whitespace-pre-wrap overflow-x-auto text-sm">${userMessage}</pre>
                        </details>
                    ` : ''}
                `;
                break;
            }

            case 'llm_execution_complete': {
                const summary = details.summary || 'LLM execution completed';
                const inputTokens = details.input_tokens || 0;
                const outputTokens = details.output_tokens || 0;
                const provider = details.provider || 'Unknown';
                const modelName = details.model_name || 'Unknown';
                const responseLength = details.response_length || 0;
                const knowledgeUsed = details.knowledge_used || 0;
                const responseText = details.response_text || '';
                const costUsd = details.cost_usd != null ? parseFloat(details.cost_usd) : null;

                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Provider</div>
                        <div class="status-kv-value"><code class="status-code text-blue-300">${provider}</code></div>

                        <div class="status-kv-key">Model</div>
                        <div class="status-kv-value"><code class="status-code text-emerald-300">${modelName}</code></div>

                        <div class="status-kv-key">Input Tokens</div>
                        <div class="status-kv-value">${inputTokens.toLocaleString()}</div>

                        <div class="status-kv-key">Output Tokens</div>
                        <div class="status-kv-value">${outputTokens.toLocaleString()}</div>
                        ${_cachedTokensHtml(details)}

                        ${costUsd !== null ? `
                            <div class="status-kv-key">Cost</div>
                            <div class="status-kv-value text-amber-300">$${costUsd.toFixed(6)}</div>
                        ` : ''}

                        <div class="status-kv-key">Response</div>
                        <div class="status-kv-value">${responseLength} characters</div>

                        ${knowledgeUsed > 0 ? `
                            <div class="status-kv-key">Knowledge Used</div>
                            <div class="status-kv-value">${knowledgeUsed} document(s)</div>
                        ` : ''}
                    </div>

                    ${responseText ? `
                        <details class="mt-2">
                            <summary class="cursor-pointer text-gray-400 hover:text-white">View Response Text</summary>
                            <pre class="mt-2 p-2 bg-gray-900/50 rounded text-gray-300 whitespace-pre-wrap overflow-x-auto text-sm">${responseText}</pre>
                        </details>
                    ` : ''}
                `;
                break;
            }

            // --- Lifecycle Events for RAG-focused and other profiles ---
            case 'execution_start': {
                stepEl.classList.add('info');

                const profileType = details.profile_type || 'unknown';
                const profileTag = details.profile_tag || 'N/A';
                const query = details.query || '';
                const queryPreview = query.length > 100 ? query.substring(0, 100) + '...' : query;
                const knowledgeCollections = details.knowledge_collections || 0;

                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Profile</div>
                        <div class="status-kv-value">${renderProfileTag(profileTag, profileType)}</div>
                        <div class="status-kv-key">Type</div>
                        <div class="status-kv-value">${profileType}</div>
                        ${knowledgeCollections > 0 ? `
                        <div class="status-kv-key">Collections</div>
                        <div class="status-kv-value">${knowledgeCollections}</div>
                        ` : ''}
                        ${queryPreview ? `
                        <div class="status-kv-key">Query</div>
                        <div class="status-kv-value text-gray-300">"${queryPreview}"</div>
                        ` : ''}
                    </div>
                `;
                break;
            }

            case 'execution_complete': {
                const profileTypeRaw = details.profile_type || 'unknown';
                const profileTypeLower = profileTypeRaw.toLowerCase?.() || profileTypeRaw;

                // Skip rendering for genie profiles - genie_coordination_complete already shows this info
                if (profileTypeLower === 'genie') {
                    console.log('[_renderConversationAgentStep:switch] Skipping redundant execution_complete for genie profile');
                    return null;
                }

                const profileTag = details.profile_tag || 'N/A';
                const success = details.success !== false;
                const statusText = success ? 'Success' : 'Failed';
                const statusClass = success ? 'text-emerald-400' : 'text-rose-400';

                // Profile-specific details
                let profileDetailsHtml = '';
                if (details.collections_searched !== undefined) {
                    // RAG focused profile
                    const docsRetrieved = details.documents_retrieved || 0;
                    const collectionsSearched = details.collections_searched || 0;
                    // Use total_duration_ms if available, otherwise calculate from retrieval + synthesis
                    const totalDuration = details.total_duration_ms ||
                        ((details.retrieval_duration_ms || 0) + (details.synthesis_duration_ms || 0));
                    const totalSeconds = (totalDuration / 1000).toFixed(1);
                    profileDetailsHtml = `
                        <div class="status-kv-key">Collections</div>
                        <div class="status-kv-value">${collectionsSearched} searched</div>
                        <div class="status-kv-key">Documents</div>
                        <div class="status-kv-value">${docsRetrieved} retrieved</div>
                        <div class="status-kv-key">Duration</div>
                        <div class="status-kv-value">${totalSeconds}s</div>
                    `;
                } else if (details.phases_executed !== undefined || details.duration_ms !== undefined) {
                    // Tool enabled (Optimizer) profile
                    const phasesExecuted = details.phases_executed || 0;
                    const durationMs = details.duration_ms || 0;
                    const durationSec = (durationMs / 1000).toFixed(1);
                    profileDetailsHtml = `
                        <div class="status-kv-key">Phases</div>
                        <div class="status-kv-value">${phasesExecuted} executed</div>
                        <div class="status-kv-key">Duration</div>
                        <div class="status-kv-value">${durationSec}s</div>
                    `;
                }

                const inputTokens = details.total_input_tokens || 0;
                const outputTokens = details.total_output_tokens || 0;

                // Cost (if available)
                const costUsd = details.cost_usd != null ? parseFloat(details.cost_usd) : null;
                let costHtml = '';
                if (costUsd !== null) {
                    costHtml = `
                        <div class="status-kv-key">Cost</div>
                        <div class="status-kv-value text-amber-300">$${costUsd.toFixed(6)}</div>
                    `;
                }

                detailsEl.innerHTML = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Status</div>
                        <div class="status-kv-value ${statusClass}">${statusText}</div>
                        <div class="status-kv-key">Profile</div>
                        <div class="status-kv-value">${renderProfileTag(profileTag, profileType)}</div>
                        ${profileDetailsHtml}
                        <div class="status-kv-key">Tokens</div>
                        <div class="status-kv-value">${inputTokens.toLocaleString()} in / ${outputTokens.toLocaleString()} out</div>
                        ${costHtml}
                    </div>
                `;
                break;
            }

            default:
                // Generic fallback with JSON display
                try {
                    const jsonStr = JSON.stringify(details, null, 2);
                    detailsEl.innerHTML = `
                        <details>
                            <summary class="cursor-pointer text-gray-400 hover:text-white">View Details</summary>
                            <pre class="mt-1 p-2 bg-gray-900/50 rounded text-gray-400 overflow-x-auto">${jsonStr}</pre>
                        </details>
                    `;
                } catch (e) {
                    detailsEl.innerHTML = `<span class="text-gray-500">[Unable to display details]</span>`;
                }
        }

        stepEl.appendChild(detailsEl);
    }

    parentContainer.appendChild(stepEl);

    // Auto-scroll to latest step (skip during historical replay to keep scroll at top)
    if (!state.isViewingHistoricalTurn) {
        stepEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    // Reset conversation agent state on completion
    if (type === 'conversation_agent_complete') {
        state.isConversationAgentActive = false;
    }
}

/**
 * Exported wrapper for rendering conversation agent events during session reload.
 * Uses the same rendering logic as live execution for consistent UX.
 */
export function renderConversationAgentStepForReload(eventData, parentContainer, isFinal = false, filterCategory = null) {
    if (filterCategory) eventData._filterCategory = filterCategory;
    _renderConversationAgentStep(eventData, parentContainer, isFinal);
}

/**
 * Renders details for system_message events.
 * Parallel execution events get phase badges; other messages render as plain text.
 */
function _renderSystemMessageDetails(details) {
    if (typeof details !== 'object' || details === null) {
        return details ? `<div class="text-xs text-gray-300 mt-1">${details}</div>` : null;
    }

    const parallelPhases = details.parallel_phases;
    if (Array.isArray(parallelPhases) && parallelPhases.length > 0) {
        const badges = parallelPhases
            .map(n => `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-violet-900/50 text-violet-300 border border-violet-700/50">Phase ${n}</span>`)
            .join('');
        return `
            <div class="flex items-center gap-2 mt-1 flex-wrap">
                <span class="text-xs text-gray-400">Running concurrently:</span>
                ${badges}
            </div>`;
    }

    // Generic system message — summary text only
    const summary = details.summary || details.message || '';
    return summary ? `<div class="text-xs text-gray-300 mt-1">${summary}</div>` : null;
}

/**
 * Renders details for optimization events (workaround, plan_optimization).
 * Handles both string payloads and structured objects with summary/correction_type/etc.
 */
function _renderOptimizationDetails(details) {
    // String details — display as clean text
    if (typeof details === 'string') {
        return `<div class="text-xs text-gray-300 mt-1">${details}</div>`;
    }

    if (typeof details !== 'object' || details === null) return null;

    const summary = details.summary || '';
    let html = '';

    // Primary: summary text
    if (summary) {
        html += `<div class="text-xs text-gray-300 mt-1">${summary}</div>`;
    }

    // Metadata KV pairs
    const kvPairs = [];

    if (details.correction_type) {
        kvPairs.push(['Type', `<code class="status-code">${details.correction_type}</code>`]);
    }
    if (details.stripped_arguments) {
        kvPairs.push(['Stripped', details.stripped_arguments.map(a => `<code class="status-code">${a}</code>`).join(', ')]);
    }
    if (details.pruned_arguments) {
        const pruned = Array.isArray(details.pruned_arguments) ? details.pruned_arguments : [details.pruned_arguments];
        kvPairs.push(['Pruned', pruned.map(a => `<code class="status-code">${a}</code>`).join(', ')]);
    }
    if (details.collapsed_phases) {
        kvPairs.push(['Collapsed Phases', details.collapsed_phases.join(', ')]);
    }
    if (details.from !== undefined && details.to !== undefined) {
        const fmtVal = (v) => typeof v === 'object' ? JSON.stringify(v) : String(v);
        kvPairs.push(['From', `<code class="status-code">${fmtVal(details.from)}</code>`]);
        kvPairs.push(['To', `<code class="status-code">${fmtVal(details.to)}</code>`]);
    }
    if (details.phase_number !== undefined) {
        kvPairs.push(['Phase', String(details.phase_number)]);
    }

    if (kvPairs.length > 0) {
        html += '<div class="status-kv-grid mt-1">';
        for (const [key, value] of kvPairs) {
            html += `<div class="status-kv-key">${key}</div><div class="status-kv-value">${value}</div>`;
        }
        html += '</div>';
    }

    // Complex nested objects in collapsible sections
    const complexFields = [];
    if (details.original_plan) complexFields.push(['Original Plan', details.original_plan]);
    if (details.correction?.added_phase) complexFields.push(['Added Phase', details.correction.added_phase]);
    // Self-correction events have nested `details` object (different from outer `details`)
    if (details.details && typeof details.details === 'object') complexFields.push(['Details', details.details]);

    for (const [label, obj] of complexFields) {
        const jsonStr = JSON.stringify(obj, null, 2);
        html += `
            <details class="text-xs mt-1">
                <summary class="cursor-pointer text-gray-400 hover:text-white">View ${label}</summary>
                <div class="mt-2 p-2 status-text-block whitespace-pre-wrap">${jsonStr}</div>
            </details>
        `;
    }

    return html || null;
}

/**
 * Renders details for context optimization events (document_truncation, context_distillation, report_distillation).
 */
function _renderContextOptimizationDetails(details) {
    if (typeof details === 'string') return `<div class="text-xs text-gray-300 mt-1">${details}</div>`;
    if (typeof details !== 'object' || details === null) return null;

    const summary = details.summary || '';
    let html = '';
    if (summary) html += `<div class="text-xs text-gray-300 mt-1">${summary}</div>`;

    const kvPairs = [];
    if (details.subtype === 'document_truncation') {
        if (details.filename) kvPairs.push(['File', `<code class="status-code">${details.filename}</code>`]);
        if (details.original_chars) kvPairs.push(['Original', `${details.original_chars.toLocaleString()} chars`]);
        if (details.truncated_to_chars) kvPairs.push(['Truncated to', `${details.truncated_to_chars.toLocaleString()} chars`]);
        if (details.reason === 'total_context_limit') {
            kvPairs.push(['Documents', `${details.documents_loaded} of ${details.documents_total} loaded`]);
            kvPairs.push(['Limit', `${(details.limit_chars || 0).toLocaleString()} chars`]);
        }
    } else if (details.subtype === 'context_distillation') {
        kvPairs.push(['Rows', (details.row_count || 0).toLocaleString()]);
        kvPairs.push(['Size', `${(details.char_count || 0).toLocaleString()} chars`]);
        if (details.columns && details.columns.length) {
            kvPairs.push(['Columns', details.columns.slice(0, 5).map(c => `<code class="status-code">${c}</code>`).join(', ')
                + (details.columns.length > 5 ? ` +${details.columns.length - 5} more` : '')]);
        }
    } else if (details.subtype === 'report_distillation') {
        kvPairs.push(['Original', `${(details.original_chars || 0).toLocaleString()} chars`]);
        kvPairs.push(['Distilled', `${(details.distilled_chars || 0).toLocaleString()} chars`]);
        kvPairs.push(['Reduction', `${details.reduction_pct || 0}%`]);
        kvPairs.push(['Level', String(details.level || 1)]);
    }

    if (kvPairs.length > 0) {
        html += '<div class="status-kv-grid mt-1">';
        for (const [key, value] of kvPairs) {
            html += `<div class="status-kv-key">${key}</div><div class="status-kv-value">${value}</div>`;
        }
        html += '</div>';
    }
    return html || null;
}

// ── Extension Renderer (compact, amber-accented) ────────────────────────────
// Matches the historical reload style from _renderExtensionEventsForReload().
// Extension events are visually distinct from the main execution pipeline.

let _extensionDividerAdded = false;
let _skillDividerAdded = false;

function _ensureExtensionDivider(parentContainer) {
    if (_extensionDividerAdded) return;
    const divider = document.createElement('div');
    divider.className = 'px-4 py-2 mt-2 border-t';
    divider.style.borderColor = 'rgba(251, 191, 36, 0.3)';
    divider.dataset.filterCategory = 'system';
    if (!isFilterCategoryEnabled('system')) {
        divider.classList.add('event-filtered-hidden');
    }
    divider.innerHTML = `<span class="text-xs font-semibold uppercase tracking-wider" style="color: #fbbf24;">Extensions</span>`;
    parentContainer.appendChild(divider);
    _extensionDividerAdded = true;
}

function _renderExtensionStep(eventData, parentContainer) {
    const { step, details, type } = eventData;

    _ensureExtensionDivider(parentContainer);

    // Helper: tag extension step elements with filter category
    const _tagFilterCategory = (el) => {
        if (eventData._filterCategory) {
            el.dataset.filterCategory = eventData._filterCategory;
            if (!isFilterCategoryEnabled(eventData._filterCategory)) {
                el.classList.add('event-filtered-hidden');
            }
        }
    };

    if (type === 'extension_running') {
        // Start event — amber play icon
        const payload = typeof details === 'string' ? {} : (details || {});
        const stepEl = document.createElement('div');
        stepEl.className = 'px-4 py-2 status-step';
        _tagFilterCategory(stepEl);
        stepEl.innerHTML = `
            <div class="flex items-center gap-2">
                <span class="text-xs" style="color: #fbbf24;">&#9654;</span>
                <span class="text-xs text-gray-300">${step || 'Running extension'}</span>
            </div>`;
        parentContainer.appendChild(stepEl);
    } else if (type === 'extension_complete') {
        // Complete event — green check or red X, with optional token/cost metrics
        const payload = typeof details === 'object' ? (details || {}) : {};
        const success = typeof details === 'string' ? details !== 'Failed' : true;
        const icon = success ? '&#10003;' : '&#10007;';
        const color = success ? 'text-green-400' : 'text-red-400';

        // Build metrics string for LLM extensions (tokens, cost, time)
        let metricsHtml = '';
        const inputTokens = payload.input_tokens;
        const outputTokens = payload.output_tokens;
        const costUsd = payload.cost_usd;
        const execTimeMs = payload.execution_time_ms;

        if (inputTokens || outputTokens) {
            const parts = [];
            parts.push(`${(inputTokens || 0).toLocaleString()} in / ${(outputTokens || 0).toLocaleString()} out`);
            if (costUsd !== undefined && costUsd > 0) {
                parts.push(`$${parseFloat(costUsd).toFixed(6)}`);
            }
            if (execTimeMs) {
                parts.push(`${execTimeMs}ms`);
            }
            metricsHtml = `<span class="text-xs text-gray-500 ml-auto">${parts.join(' · ')}</span>`;
        } else if (execTimeMs) {
            metricsHtml = `<span class="text-xs text-gray-500 ml-auto">${execTimeMs}ms</span>`;
        }

        const stepEl = document.createElement('div');
        stepEl.className = 'px-4 py-2 status-step';
        _tagFilterCategory(stepEl);
        stepEl.innerHTML = `
            <div class="flex items-center gap-2">
                <span class="${color} text-xs">${icon}</span>
                <span class="text-xs text-gray-300">${step || 'Extension'}</span>
                ${metricsHtml}
            </div>`;
        parentContainer.appendChild(stepEl);
    } else if (type === 'extension_results') {
        // Combined results — show output previews for silent extensions
        const results = typeof details === 'object' ? details : {};
        for (const [name, result] of Object.entries(results)) {
            const target = result?.output_target || 'silent';
            if (target === 'chat_append') continue; // already visible in chat

            if (result?.success && result?.content) {
                const contentPreview = typeof result.content === 'object'
                    ? JSON.stringify(result.content, null, 2).substring(0, 300)
                    : String(result.content).substring(0, 300);

                const resultEl = document.createElement('div');
                resultEl.className = 'px-4 py-2';
                resultEl.innerHTML = `
                    <details class="group">
                        <summary class="text-xs cursor-pointer hover:text-amber-200" style="color: #fbbf24;">
                            &#9660; !${name} output <span class="text-gray-500">(${result.content_type || 'application/json'})</span>
                        </summary>
                        <pre class="mt-1 text-xs text-gray-400 bg-gray-800/50 rounded p-2 overflow-auto max-h-48 whitespace-pre-wrap">${contentPreview.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')}</pre>
                    </details>`;
                parentContainer.appendChild(resultEl);
            }
        }
    }
}

// ── Skill Step Renderer (emerald green, pre-processing transparency) ──────────

function _ensureSkillDivider(parentContainer) {
    if (_skillDividerAdded) return;
    const divider = document.createElement('div');
    divider.className = 'px-4 py-2 mt-2 border-t';
    divider.style.borderColor = 'rgba(52, 211, 153, 0.3)';
    divider.dataset.filterCategory = 'system';
    if (!isFilterCategoryEnabled('system')) {
        divider.classList.add('event-filtered-hidden');
    }
    divider.innerHTML = `<span class="text-xs font-semibold uppercase tracking-wider" style="color: #34d399;">Skills</span>`;
    parentContainer.appendChild(divider);
    _skillDividerAdded = true;
}

function _renderSkillStep(eventData, parentContainer) {
    const { details } = eventData;
    const payload = typeof details === 'object' ? (details || {}) : {};
    const skills = payload.skills || [];
    const totalTokens = payload.total_estimated_tokens || 0;

    if (skills.length === 0) return;

    _ensureSkillDivider(parentContainer);

    // Render each skill as a compact row
    skills.forEach(skill => {
        const nameDisplay = skill.param ? `#${skill.name}:${skill.param}` : `#${skill.name}`;
        const target = skill.injection_target === 'user_context' ? 'user context' : 'system prompt';
        const tokens = skill.estimated_tokens || 0;

        const stepEl = document.createElement('div');
        stepEl.className = 'px-4 py-2 status-step';
        // Tag with filter category for event filter chips
        if (eventData._filterCategory) {
            stepEl.dataset.filterCategory = eventData._filterCategory;
            if (!isFilterCategoryEnabled(eventData._filterCategory)) {
                stepEl.classList.add('event-filtered-hidden');
            }
        }
        stepEl.innerHTML = `
            <div class="flex items-center gap-2">
                <span class="text-xs" style="color: #34d399;">&#10038;</span>
                <span class="text-xs text-gray-300"><span class="font-medium" style="color: #34d399;">${nameDisplay}</span> &rarr; ${target}</span>
                <span class="text-xs text-gray-500 ml-auto">~${tokens.toLocaleString()} tokens</span>
            </div>`;
        parentContainer.appendChild(stepEl);
    });

    // Total overhead summary
    if (skills.length > 1) {
        const totalEl = document.createElement('div');
        totalEl.className = 'px-4 py-1';
        totalEl.innerHTML = `<span class="text-xs text-gray-500">Skill context: ~${totalTokens.toLocaleString()} tokens added to prompt</span>`;
        parentContainer.appendChild(totalEl);
    }
}

/** Words that never tell one MCP server from another (mirrors dataSources.js GENERIC_WORDS). */
const _MCP_GENERIC_WORDS = new Set(['mcp', 'server', 'servers', 'srv', 'the']);

/**
 * PROP-006 phase 2 — the short label a server's chip carries: its alias when it has one (an
 * additional server's tools read `<alias>__<name>`); for the first server, the words of its name
 * that the other servers' names do not share (`Teradata MCP - fitness_manager` beside
 * `Teradata MCP - nibrs_source_agent` → `fitness_manager`), else the part after the last ` - `,
 * else the name itself, cut to 18 characters.
 */
export function mcpServerShortLabel(server, peers) {
    if (!server) return '';
    if (server.alias) return String(server.alias);
    const name = String(server.name || server.server_id || '');
    const words = (x) => String(x || '').toLowerCase().split(/[^a-z0-9_]+/).filter(Boolean);
    const others = new Set();
    for (const p of (peers || [])) if (p && p !== server && String(p.server_id) !== String(server.server_id)) words(p.name).forEach(w => others.add(w));
    let label = '';
    if (others.size) label = words(name).filter(w => !others.has(w) && !_MCP_GENERIC_WORDS.has(w)).join(' ');
    if (!label && name.includes(' - ')) label = name.slice(name.lastIndexOf(' - ') + 3).trim();
    if (!label) label = name;
    return label.length > 18 ? label.slice(0, 17) + '…' : label;
}

/**
 * PROP-006 phase 2 — ONE chip for an MCP server, used wherever a surface says which server a
 * tool, prompt or call belongs to (the resource panel's category tabs, the Live Status Server
 * row). Monochrome, theme variables only; the platform tooltip carries the full name, the role
 * and the classification state. A server not classified on this instance is drawn dashed — its
 * tools are never shown without saying so. '' for no server.
 */
export function mcpServerChip(server, peers) {
    if (!server || typeof server !== 'object' || !server.server_id) return '';
    _ensureMcpChipCss();
    const label = escapeHtml(mcpServerShortLabel(server, peers));
    const name = String(server.name || server.server_id);
    const role = server.alias
        ? `its tools read ${server.alias}__name`
        : 'the first server — its tools keep their names';
    const notClassified = server.state === 'not_classified';
    const stateLine = notClassified
        ? `\nNot classified on this instance${server.reason ? ' (' + server.reason + ')' : ''} — run the profile test to list its tools.`
        : '';
    const tip = escapeHtml(`MCP server: ${name}\n${role}${stateLine}`);
    const glyph = '<svg viewBox="0 0 24 24" width="10" height="10" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="4" width="18" height="6" rx="1.5"/><rect x="3" y="14" width="18" height="6" rx="1.5"/></svg>';
    return `<span class="mcp-chip u-tip${notClassified ? ' is-unclassified' : ''}" data-mcp-chip="${escapeHtml(String(server.server_id))}" data-tip="${tip}" aria-label="${escapeHtml(`MCP server ${name}${notClassified ? ', not classified' : ''}`)}">${glyph}<span>${label}</span></span>`;
}

/**
 * PROP-006 phase 2 — which MCP server a resource-panel category belongs to, and the label its tab
 * shows beside the chip (an additional server's `<alias>: ` prefix gives way to the chip).
 * `srvInfo` = `{servers, categoryServers}` from the profile resources endpoint; null for a
 * single-server agent. Returns null when the category is not a server's (injected categories).
 */
export function mcpCategoryServer(category, srvInfo) {
    if (!srvInfo || typeof category !== 'string') return null;
    const sid = (srvInfo.categoryServers || {})[category];
    if (!sid) return null;
    const server = (srvInfo.servers || []).find(sv => String(sv.server_id) === String(sid));
    if (!server) return null;
    const prefix = server.alias ? `${server.alias}: ` : null;
    const label = mcpCategoryLabel(prefix && category.startsWith(prefix) ? category.slice(prefix.length) : category);
    return { server, label };
}

/**
 * The text a resource-panel category tab shows. Light classification files a server's whole
 * catalogue under ONE generic category whose stored key is "All Tools" / "All Prompts" /
 * "All Resources" — a name that is false beside the injected "System Tools" and "Component
 * Tools" tabs (it is not all the tools, it is the MCP server's), and doubly so for a
 * multi-server agent, where each server's group has one. The tab says what the group is:
 * "MCP Tools" (the server's chip, when drawn, says WHICH server). Display only: the stored
 * key is part of the classification record, which the deployment attestation hashes, so it
 * is never renamed in the record — a renamed key would flip every signed agent's reading.
 * Any other category (an LLM-classified one, an injected one) reads as stored.
 */
export function mcpCategoryLabel(category) {
    const generic = { 'All Tools': 'MCP Tools', 'All Prompts': 'MCP Prompts', 'All Resources': 'MCP Resources' };
    return (typeof category === 'string' && generic[category]) || category;
}

/**
 * PROP-006 phase 2 — the resource panel's category order for a multi-server agent: categories
 * that belong to no server first (in their own order), then each server's categories as one
 * group, the servers in the agent's order (the first server first). The first entry of a group
 * carries `groupStart` — where the panel draws that server's chip ONCE. A server not classified
 * on this instance becomes a group of its own with a single `unclassified` entry, so it is never
 * omitted. `srvInfo` null (a single-server agent) → the categories unchanged, no groups.
 */
export function mcpGroupCategories(categories, srvInfo) {
    const cats = Array.isArray(categories) ? categories : [];
    if (!srvInfo || !Array.isArray(srvInfo.servers) || !srvInfo.servers.length) {
        return cats.map(c => ({ category: c, label: mcpCategoryLabel(c), server: null, groupStart: false }));
    }
    const out = [];
    const byServer = new Map();
    for (const c of cats) {
        const cs = mcpCategoryServer(c, srvInfo);
        if (!cs) { out.push({ category: c, label: mcpCategoryLabel(c), server: null, groupStart: false }); continue; }
        const k = String(cs.server.server_id);
        if (!byServer.has(k)) byServer.set(k, []);
        byServer.get(k).push({ category: c, label: cs.label, server: cs.server });
    }
    for (const sv of srvInfo.servers) {
        const k = String(sv.server_id);
        const group = byServer.get(k) || [];
        if (!group.length && sv.state === 'not_classified') {
            group.push({ category: `__unclassified__${k}`, label: 'not classified', server: sv, unclassified: true });
        }
        group.forEach((g, i) => out.push({ ...g, groupStart: i === 0 }));
    }
    return out;
}

function _ensureMcpChipCss() {
    if (typeof document === 'undefined' || !document.head || document.getElementById('mcp-chip-css')) return;
    const st = document.createElement('style');
    st.id = 'mcp-chip-css';
    st.textContent = `.mcp-chip{display:inline-flex;align-items:center;gap:4px;padding:0 6px;height:18px;border:1px solid var(--border-primary);border-radius:4px;color:var(--text-primary);background:var(--card-bg);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:10px;line-height:1;white-space:nowrap;vertical-align:middle;max-width:12rem;overflow:hidden;}
.mcp-chip>span{overflow:hidden;text-overflow:ellipsis;}
.mcp-chip svg{flex:0 0 auto;color:var(--text-muted);}
.mcp-chip.is-unclassified{border-style:dashed;color:var(--text-muted);}
.mcp-group{display:inline-flex;align-items:center;padding:0 4px 0 10px;margin-left:6px;border-left:1px solid var(--border-primary);}
.mcp-group:first-child{margin-left:0;border-left:0;padding-left:0;}`;
    document.head.appendChild(st);
}

/**
 * PROP-006 phase 2 — the MCP server a multi-server agent's tool step went to, and why, as two
 * `status-kv-grid` cells: the server's chip, its full name, and the reason. The fact is stamped
 * on the persisted call (`result.mcp_source` on the Optimize lane, the event's `source` on the
 * ReAct lane), so the live card and the reloaded card read the same record. '' when the step
 * names no server — every single-server agent's steps.
 */
export function mcpSourceKv(src) {
    if (!src || typeof src !== 'object' || !src.server_id) return '';
    const name = escapeHtml(String(src.server || src.server_id));
    let why;
    if (src.reason === 'routed') {
        // B10: Uderia sent the call here because this server can read the data the model's
        // chosen server cannot — both named, never a silent move.
        const dbs = Array.isArray(src.databases) && src.databases.length
            ? escapeHtml(src.databases.join(', ')) : 'the data it reads';
        const called = src.called ? `<code class="status-code">${escapeHtml(String(src.called))}</code>` : 'another server\'s tool';
        const from = src.from_server ? ` (server ${escapeHtml(String(src.from_server))})` : '';
        why = `routed here by Uderia — this server can read ${dbs}; the model called ${called}${from}, which cannot`;
    } else if (src.reason === 'qualified' && src.alias) {
        why = `the model called this server's tool (<code class="status-code">${escapeHtml(String(src.alias))}__…</code>)`;
    } else {
        why = 'the first server — the tool name carries no short name';
    }
    const chip = mcpServerChip({ server_id: src.server_id, name: src.server || src.server_id, alias: src.alias || null });
    return `<div class="status-kv-key">Server</div><div class="status-kv-value" data-mcp-server="${escapeHtml(String(src.server_id))}">${chip} ${name}<span style="color:var(--text-muted);"> · ${why}</span></div>`;
}

/** The Optimize lane's tool result / error step: the server row above the result (see
 *  `mcpSourceKv`). Nothing for any other step or a result that names no server. */
export function appendMcpSource(stepEl, step, details) {
    if (!stepEl || !details || typeof details !== 'object' || !details.mcp_source) return;
    if (!/Tool Execution (Result|Error)/.test(step || '')) return;
    const srcHtml = mcpSourceKv(details.mcp_source);
    if (!srcHtml) return;
    const srcEl = document.createElement('div');
    srcEl.className = 'status-kv-grid mb-2';
    srcEl.innerHTML = srcHtml;
    stepEl.appendChild(srcEl);
}

function _renderStandardStep(eventData, parentContainer, isFinal = false) {
    if (_consumeAssuranceProgress(eventData)) return;
    const { step, details, type } = eventData;

    // --- UX ENHANCEMENT ---
    // The "Calling LLM" step is transient; it shouldn't stay active.
    // We mark it as 'final' so it renders as completed immediately.
    // The subsequent "Plan Generated" step will become the active one.
    if (step?.startsWith("Calling LLM for")) {
        isFinal = true;
    }
    // --- END ENHANCEMENT ---

    let lastStep = document.getElementById(`status-step-${state.currentStatusId}`);

    // FALLBACK: If no ID-based step found, try class selector
    // This handles steps created by _renderConversationAgentStep which don't use ID system
    // (e.g., execution_start lifecycle events that don't have numbered IDs)
    if (!lastStep && parentContainer) {
        lastStep = parentContainer.querySelector('.status-step.active');
    }

    if (lastStep && lastStep.classList.contains('active') && parentContainer && parentContainer.contains(lastStep)) {
        lastStep.classList.remove('active');
        lastStep.classList.add('completed');
        // Remove progress indicator when phase completes (Phase 1: Terminology Harmonization)
        const progressIndicator = lastStep.querySelector('.progress-indicator');
        if (progressIndicator) {
            progressIndicator.remove();
        }
        if (state.isInFastPath && !lastStep.classList.contains('plan-optimization')) {
            lastStep.classList.add('plan-optimization');
        }
    }

    if (type !== 'plan_optimization') {
        state.isInFastPath = false;
    } else {
        state.isInFastPath = true;
    }

    state.currentStatusId++;
    const stepEl = document.createElement('div');
    stepEl.id = `status-step-${state.currentStatusId}`;
    stepEl.className = 'status-step p-3 rounded-md mb-2'; // Added mb-2 for spacing
    _stampLa(stepEl, eventData);

    // Tag with filter category for event filter chips
    if (eventData._filterCategory) {
        stepEl.dataset.filterCategory = eventData._filterCategory;
        if (!isFilterCategoryEnabled(eventData._filterCategory)) {
            stepEl.classList.add('event-filtered-hidden');
        }
    }

    // Apply depth-based indentation for sub-executor events
    const depth = eventData.metadata?.execution_depth || eventData.details?.execution_depth || 0;
    if (depth > 0) {
        stepEl.style.marginLeft = `${depth * 16}px`;
        stepEl.style.borderLeft = '2px solid rgba(139, 92, 246, 0.3)';
    }

    const depthPrefix = depth > 0 ? '↳ '.repeat(depth) : '';
    const stepTitle = document.createElement('h4');
    stepTitle.className = 'font-bold text-sm text-white mb-2';
    stepTitle.textContent = depthPrefix + (step || (type === 'tool_result' ? 'Result' : (type === 'error' ? 'Error' : 'Details')));

    // Add progress indicator for active phases (Phase 1: Terminology Harmonization)
    if (type === 'phase_start' && !isFinal) {
        const progressLabel = document.createElement('span');
        progressLabel.className = 'text-amber-400 text-xs ml-2 progress-indicator';
        progressLabel.textContent = 'Processing...';
        stepTitle.appendChild(progressLabel);
    }

    stepEl.appendChild(stepTitle);

    const metricsEl = document.createElement('div');
    metricsEl.className = 'per-call-metrics text-xs text-gray-400 mb-2 hidden';

    if (typeof details === 'object' && details !== null && details.call_id) {
        metricsEl.dataset.callId = details.call_id;
    }
    
    if (typeof details === 'object' && details !== null) {
        // Check for planner tokens (at the root of 'details')
        const planner_input = details.input_tokens;
        const planner_output = details.output_tokens;
        
        // Check for tool tokens (inside 'details.metadata')
        const tool_input = details.metadata?.input_tokens;
        const tool_output = details.metadata?.output_tokens;

        if (planner_input !== undefined && planner_output !== undefined) {
            let metricsText = `(LLM Call: ${planner_input.toLocaleString()} in / ${planner_output.toLocaleString()} out`;
            if (details.cost_usd !== undefined && details.cost_usd !== null) {
                metricsText += ` · $${parseFloat(details.cost_usd).toFixed(6)}`;
            }
            metricsText += ')';
            metricsEl.innerHTML = metricsText;
            metricsEl.classList.remove('hidden');
        } else if (tool_input !== undefined && tool_output !== undefined) {
            let metricsText = `(LLM Call: ${tool_input.toLocaleString()} in / ${tool_output.toLocaleString()} out`;
            const toolCost = details.metadata?.cost_usd;
            if (toolCost !== undefined && toolCost !== null) {
                metricsText += ` · $${parseFloat(toolCost).toFixed(6)}`;
            }
            metricsText += ')';
            metricsEl.innerHTML = metricsText;
            metricsEl.classList.remove('hidden');
        }
    }

    stepEl.appendChild(metricsEl);

    if (details) {
        let customRenderedHtml = null;
        let detailsString = '';

        if (type === 'context_window_snapshot' && typeof details === 'string') {
            // Context window snapshot details are pre-rendered HTML from renderContextWindowSnapshot()
            customRenderedHtml = details;
        } else if (typeof details === 'object' && details !== null) {
            if (type === "execution_start") {
                _pdReset();     // universal turn boundary: every engine emits it
                customRenderedHtml = customRenderedHtml || '';
            } else if (type === "skill_section_loaded") {
                // Optimize logs the fetch into the SAME ordered system_events list as its
                // LLM steps, so the identical derivation applies (see _pdNoteSection).
                _pdNoteSection(details);
                const _oTok = Math.round((Number(details.chars) || 0) / 4);
                customRenderedHtml = `
                    <div class="status-kv-grid">
                        <div class="status-kv-key">Section</div>
                        <div class="status-kv-value">${escapeHtml(String(details.anchor || ''))}</div>
                        <div class="status-kv-key">Size</div>
                        <div class="status-kv-value">${(Number(details.chars) || 0).toLocaleString()} chars (~${_oTok.toLocaleString()} tokens)</div>
                        <div class="status-kv-key">Cost</div>
                        <div class="status-kv-value" style="color: var(--text-muted);">none here — it lands on the next LLM call, which now carries this section</div>
                    </div>`;
            } else if (step?.startsWith("Calling LLM for")) {
                customRenderedHtml = _renderPlanningDetails(details) + _pdTagHtmlGrid();
            } else if (type === "plan_generated") {
                customRenderedHtml = _renderMetaPlanDetails(details);
            } else if (type === "tool_intent") {
                customRenderedHtml = _renderToolIntentDetails(details);
            } else if (type === "knowledge_retrieval") {
                customRenderedHtml = _renderKnowledgeRetrievalDetails(details);
                // Trigger the indicator blink when rendering knowledge retrieval event
                if (details.collections && details.collections.length > 0) {
                    // Only blink during live execution, not when viewing historical turns
                    if (!state.isViewingHistoricalTurn) {
                        blinkKnowledgeDot();
                    }
                    updateKnowledgeIndicator(details.collections, details.document_count || 0);
                }
            } else if (type === "session_name_generation_start") {
                customRenderedHtml = _renderSessionNameStartDetails(details);
            } else if (type === "session_name_generation_complete") {
                customRenderedHtml = _renderSessionNameCompleteDetails(details);
            } else if (type === "next_step_generation_complete") {
                customRenderedHtml = _renderNextStepCompleteDetails(details);
            } else if (POST_TURN_LLM_TYPES.includes(type)) {
                customRenderedHtml = _renderPostTurnLlmCallDetails(details);
            } else if (type === "judge_evaluation_complete") {
                customRenderedHtml = _renderJudgeEvalCompleteDetails(details);
            } else if (type === "review_decision") {
                customRenderedHtml = _renderReviewDecisionDetails(details);
            } else if (type === "grounding_gate") {
                if (_assuranceCardSuppressed(type, details)) return;
                customRenderedHtml = _renderGroundingGateDetails(details);
            } else if (type === "guardrail") {
                customRenderedHtml = _renderGuardrailDetails(details);
            } else if (type === "obda") {
                if (_assuranceCardSuppressed(type, details)) return;
                customRenderedHtml = _renderObdaDetails(details);
            } else if (type === "compute_lineage") {
                customRenderedHtml = _renderComputeLineageDetails(details);
            } else if (type === "llm_recovery") {
                customRenderedHtml = _renderLlmRecoveryDetails(details);
            } else if (type === "turn_budget") {
                customRenderedHtml = _renderTurnBudgetDetails(details);
            } else if (type === "action_record") {
                customRenderedHtml = _renderActionRecordDetails(details);
            } else if (type === "tool_surface") {
                customRenderedHtml = _renderToolSurfaceDetails(details);
            } else if (type === "outcome_revision") {
                customRenderedHtml = _renderOutcomeRevisionDetails(details);
            } else if (type === "steer_consumed") {
                customRenderedHtml = _renderSteerConsumedDetails(details);
                try { if (window.SteerReattach && window.SteerReattach.consumed) window.SteerReattach.consumed({ ...details, session_id: _adSession() }); } catch (_) { /* never breaks the trace */ }
            } else if (type === "attachment_sources") {
                // the sources load BEFORE the engine is dispatched, so on every lane this
                // step reaches the standard renderer: move the dial + mark the bubble here too
                try {
                    if (window.AssuranceDial && window.AssuranceDial.note) window.AssuranceDial.note(_adSession(), 'attachment_sources', details);
                    if (window.AttachmentSources && window.AttachmentSources.markUsed) window.AttachmentSources.markUsed(_adSession(), details);
                } catch (_) { /* never breaks the trace */ }
                customRenderedHtml = _renderAttachmentSourcesDetails(details);
            } else if (type === "honesty") {
                customRenderedHtml = _renderHonestyDetails(details);
            } else if (type === "kg_enrichment") {
                customRenderedHtml = _renderKgEnrichmentDetails(details);
            } else if (type === "execution_start") {
                customRenderedHtml = _renderExecutionStartDetails(details);
            } else if (type === "execution_complete") {
                customRenderedHtml = _renderExecutionCompleteDetails(details);
            } else if (type === "system_message") {
                customRenderedHtml = _renderSystemMessageDetails(details);
            } else if (type === "workaround" || type === "plan_optimization") {
                customRenderedHtml = _renderOptimizationDetails(details);
            } else if (type === "context_optimization") {
                customRenderedHtml = _renderContextOptimizationDetails(details);
            } else {
                try {
                    const cache = new Set();
                    detailsString = JSON.stringify(details, (key, value) => {
                        if (typeof value === 'object' && value !== null) {
                            if (cache.has(value)) {
                                return '[Circular Reference]';
                            }
                            cache.add(value);
                        }
                        return value;
                    }, 2);
                } catch (e) {
                    detailsString = "[Could not stringify details]";
                    console.error("Error stringifying details:", e, details);
                }
            }
        } else {
            detailsString = String(details);
        }

        appendMcpSource(stepEl, step, details);
        if (customRenderedHtml) {
            const detailsContainer = document.createElement('div');
            detailsContainer.innerHTML = customRenderedHtml;
            stepEl.appendChild(detailsContainer);
        } else if (detailsString) {
            const characterThreshold = 300;
            if (detailsString.length > characterThreshold) {
                const detailsEl = document.createElement('details');
                detailsEl.className = 'text-xs';

                const summaryEl = document.createElement('summary');
                summaryEl.className = 'cursor-pointer text-gray-400 hover:text-white';

                let summaryText = `Details (${detailsString.length} chars)`;
                if ((step?.includes('Tool Execution Result') || step?.includes('Tool Execution Error') || step?.includes('LLM Synthesis Results')) && typeof details === 'object' && details !== null) {
                    if (details.results) {
                        const itemCount = Array.isArray(details.results) ? details.results.length : (details.results ? 1 : 0);
                        const status = details.status || 'unknown';
                        summaryText = `Result (${status}, ${itemCount} items)`;
                    } else if (details.type === 'chart') {
                        summaryText = 'Chart Specification';
                    }
                } else if (step?.includes('Final Answer')) {
                    summaryText = `Final Answer Summary`;
                } else if (type === 'cancelled') {
                    summaryText = 'Cancellation Details';
                } else if (type === 'error') {
                    summaryText = 'Error Details';
                }

                summaryEl.textContent = `${summaryText} - Click to expand`;
                detailsEl.appendChild(summaryEl);

                const pre = document.createElement('pre');
                pre.className = 'mt-2 p-2 bg-gray-900/70 rounded-md text-gray-300 overflow-x-auto whitespace-pre-wrap';
                pre.textContent = detailsString;
                detailsEl.appendChild(pre);
                stepEl.appendChild(detailsEl);
            } else {
                const pre = document.createElement('pre');
                pre.className = 'p-2 bg-gray-900/70 rounded-md text-xs text-gray-300 overflow-x-auto whitespace-pre-wrap';
                pre.textContent = detailsString;
                stepEl.appendChild(pre);
            }
        }
    }

    parentContainer.appendChild(stepEl);

    if (type === 'workaround') {
        stepEl.classList.add('workaround');
    } else if (type === 'error') {
        stepEl.classList.add('error');
    } else if (type === 'cancelled') {
        stepEl.classList.add('cancelled');
    } else if (state.isInFastPath) {
        stepEl.classList.add('plan-optimization');
    }

    if (!isFinal) {
        stepEl.classList.add('active');
    } else {
        stepEl.classList.remove('active');
        if (!stepEl.classList.contains('error') && !stepEl.classList.contains('cancelled')) {
            stepEl.classList.add('completed');
        }
    }

    // Add category class for background color (aligned with filter pill colors)
    const category = getEventCategory(type, eventData._filterCategory);
    stepEl.classList.add(category);
    _stampTrustGuideContext(stepEl, type, details);
}

/**
 * Resets the status window and its related state variables for a new execution.
 */
export function resetStatusWindowForNewTask() {
    DOM.statusWindowContent.innerHTML = '';
    _extensionDividerAdded = false; // Reset extension divider flag for new task
    _skillDividerAdded = false; // Reset skill divider flag for new task
    // Remove any existing scroll spacer
    const existingSpacer = document.getElementById('status-scroll-spacer');
    if (existingSpacer) existingSpacer.remove();

    // Hide knowledge banner when starting a new task
    const knowledgeBanner = document.getElementById('knowledge-banner');
    if (knowledgeBanner) {
        knowledgeBanner.classList.add('hidden');
    }

    state.currentStatusId = 0;
    state.isRestTaskActive = false;
    state.activeRestTaskId = null;
    state.currentPhaseContainerEl = null;
    state.phaseContainerStack = [];
    state.pendingSubtaskPlanningEvents = [];
    state.isInFastPath = false;
    setThinkingIndicator(false);
    // Title is NOT reset here — it is managed by lifecycle events (execution_start)
    // and profile-specific source handlers (genie, conversation_agent, rest).
    // Each caller sets the appropriate branded title after calling this function.
}

/**
 * Map profile types to branded agent names (IFOC methodology)
 */
function _getBrandedAgentName(profileType) {
    switch (profileType) {
        case 'conversation_with_tools':
        case 'llm_only':
            return 'Ideate Engine';
        case 'rag_focused':
            return 'Focus Engine';
        case 'tool_enabled':
            return 'Optimize Engine';
        case 'genie':
            return 'Coordinate Engine';
        default:
            return 'Engine';
    }
}

/**
 * Format the Live Status title with branded agent name and turn number.
 * @param {string} profileType - Profile type for branding
 * @param {boolean} isLive - true for live execution prefix, false for completed
 * @returns {string} Formatted title
 */
function _formatStatusTitle(profileType, isLive = true) {
    const brandedName = _getBrandedAgentName(profileType);
    const turnSuffix = state.currentTurnNumber ? ` - Turn ${state.currentTurnNumber}` : '';
    if (isLive) {
        return `Live Status - ${brandedName}${turnSuffix}`;
    }
    return `${brandedName}${turnSuffix}`;
}


// The session a live trust step belongs to. The dial ignores steps from another session
// — the platform runs turns concurrently and a reading must never be painted onto the
// wrong one.
// A mechanism RAN and found nothing (`assurance_progress`). That fact belongs on the
// dial, never in the trace: a card per clean pass would bury the steps that describe
// what the turn did. ONE rule, asked by the live chokepoint AND every step renderer
// (the standard renderer once lacked a branch and dumped the event as raw JSON).
function _consumeAssuranceProgress(eventData) {
    if (!eventData || eventData.type !== 'assurance_progress') return false;
    try {
        const d = eventData.details || {};
        if (window.AssuranceDial && window.AssuranceDial.note) {
            window.AssuranceDial.note(_adSession(), d.mechanism || 'guardrail', d);
        }
    } catch (_) { /* a live update must never break the trace */ }
    return true;
}

function _adSession() {
    const el = document.getElementById('assurance-dial');
    return el ? el.getAttribute('data-session-id') : null;
}


// The kg_enrichment step title, built in ONE place for the live stream AND the
// reload path. Reported from a walkthrough: live showed the backend's bare
// "Knowledge Graph Enrichment" while a refresh showed the counts + products —
// two paths, two builders, one of them missing. A title carrying WHICH data
// products the planner was shown is the fact the whole event exists for.
export function kgEnrichmentTitle(payload) {
    const p = payload || {};
    const first = (p.enrichments || [])[0] || {};
    const entities = p.total_entities || first.entity_count || 0;
    const rels = p.total_relationships || first.relationship_count || 0;
    const names = [];
    for (const e of (p.enrichments || [])) {
        for (const n of (e?.data_products || [])) if (!names.includes(n)) names.push(n);
    }
    const shown = names.slice(0, 3).join(', ')
        + (names.length > 3 ? ` +${names.length - 3}` : '');
    // ANALYSED vs INJECTED (operator finding): under an exclusive data surface
    // the subgraph is still analysed (the grounding gate's scope derives from
    // it) but the prompt receives the product contract ALONE — a title reading
    // "51 entities" over that turn claims an injection that did not happen.
    const surfaceOnly = p.surface_only === true
        || (p.enrichments || []).some(e => e?.surface_only === true);
    // How many of those entities the question reached only by MEANING — the second
    // recall channel's contribution, on the collapsed step line, because the title is
    // what an operator reads without expanding. Silent when it contributed nothing: a
    // clause on every turn is furniture, and this one is only news when it fires.
    // Inline on purpose — this builder is executed in isolation by its own gate, so a
    // helper it cannot see is a helper that breaks it.
    let semantic = '';
    try {
        const reports = (p.enrichments || []).map(e => e && e.semantic_seeding).filter(Boolean);
        if (reports.length) {
            const bridged = reports.reduce((n, r) => n + ((r.bridged || []).length), 0);
            if (bridged) semantic = `, ${bridged} by meaning`;
            // A graph still embedding is NOT a graph whose words simply matched; saying
            // so is what stops "no semantic hits" reading as a finding about the question.
            else if (reports.some(r => r.state === 'building' || r.state === 'cold')) {
                semantic = ', semantic index warming';
            }
        }
    } catch (e) { /* a title must never break a render */ }
    // …and how many came from the CONVERSATION rather than the question (the
    // carry-over channel). Same rule as the semantic clause: silent when zero.
    let carried = '';
    try {
        const n = (p.enrichments || []).reduce((acc, e) => acc + (((e && e.carry_over && e.carry_over.entities) || []).length), 0);
        if (n) carried = `, ${n} carried from the previous turn`;
    } catch (e) { /* a title must never break a render */ }
    const base = `Knowledge Graph Enrichment (${nodesLabel(entities)}, ${relationshipsLabel(rels)}`;
    const prods = names.length
        ? `, ${names.length} data product${names.length === 1 ? '' : 's'}: ${shown}` : '';
    if (surfaceOnly && names.length) {
        return `Knowledge Graph Enrichment (surface-only — injected ${names.length} `
             + `data product contract${names.length === 1 ? '' : 's'}: ${shown}; `
             + `schemas withheld, ${nodesLabel(entities)} analysed for scope${semantic}${carried})`;
    }
    return `${base}${prods}${semantic}${carried})`;
}

export function updateStatusWindow(eventData, isFinal = false, source = 'interactive', taskId = null) {
    // Live kg_enrichment arrives with the backend's bare step string; rebuild the
    // title here — the ONE chokepoint every live render path passes through — so
    // the live stream and the reload cannot show different titles for one event.
    if (eventData && eventData.type === 'kg_enrichment') {
        try { eventData = { ...eventData, step: kgEnrichmentTitle(eventData.details || eventData.payload || eventData) }; }
        catch (e) { /* a title must never break a render */ }
    }
    if (_consumeAssuranceProgress(eventData)) return;
    const { step, details, type, metadata } = eventData;

    // Compute and attach filter category for DOM tagging by renderers
    eventData._filterCategory = getFilterCategory(source, type);

    const statusTitle = DOM.statusTitle || document.getElementById('status-title');

    if (source === 'rest' && taskId) {
        // Check if this is the first event for a new or different REST task
        if (!state.isRestTaskActive || taskId !== state.activeRestTaskId) {
            // Don't clear if there are existing steps (e.g., session name generation) UNLESS switching tasks
            const hasExistingSteps = DOM.statusWindowContent.querySelector('.status-step');
            const isSwitchingTasks = state.isRestTaskActive && taskId !== state.activeRestTaskId;
            if (!hasExistingSteps || isSwitchingTasks) {
                resetStatusWindowForNewTask(); // Use the centralized reset function
            }
            state.isRestTaskActive = true; // Set REST-specific state after reset
            state.activeRestTaskId = taskId;
            updateTaskIdDisplay(taskId); // Display the task ID
        }
        statusTitle.textContent = _formatStatusTitle('tool_enabled');
    } else if (source === 'genie') {
        // Genie coordination events
        if (!state.isGenieCoordinationActive) {
            // Don't clear status window if there are already steps (e.g., session name generation)
            // Only reset if window is empty or has placeholder text
            const hasExistingSteps = DOM.statusWindowContent.querySelector('.status-step');
            if (!hasExistingSteps) {
                resetStatusWindowForNewTask();
            }
            state.isGenieCoordinationActive = true;
            state.isRestTaskActive = false;
            updateTaskIdDisplay(null);
            // Re-assert cancel button visibility: if the SSE stream ended early (e.g. reconnect)
            // and setExecutionState(false) was already called, genie events arriving via the
            // notification channel must restore the button so the user can still cancel.
            setExecutionState(true);
        }
        statusTitle.textContent = _formatStatusTitle('genie');
        // Render genie events using custom renderer
        _renderGenieStep(eventData, DOM.statusWindowContent, isFinal);
        if (isFinal) {
            // genie_coordination_complete: mark coordination done
            state.isGenieCoordinationActive = false;
            // Only hide the cancel button here when there is NO active SSE stream — the
            // processStream path will call setExecutionState(false) on final_answer instead.
            if (!state.activeStreamSessions || !state.activeStreamSessions.has(state.currentSessionId)) {
                setExecutionState(false);
            }
        }
        if (!state.isMouseOverStatus && !state.isViewingHistoricalTurn) {
            DOM.statusWindowContent.scrollTop = DOM.statusWindowContent.scrollHeight;
        }
        return;
    } else if (source === 'conversation_agent') {
        // Conversation agent tool execution events
        if (!state.isConversationAgentActive) {
            // Only reset if there's no pending knowledge retrieval event AND no existing steps (e.g., session name generation)
            // (knowledge_retrieval arrives before conversation_agent_start for conversation_with_tools)
            const hasExistingSteps = DOM.statusWindowContent.querySelector('.status-step');
            if (!state.pendingKnowledgeRetrievalEvent && !hasExistingSteps) {
                resetStatusWindowForNewTask();
            }
            state.isConversationAgentActive = true;
            state.isRestTaskActive = false;
            state.isGenieCoordinationActive = false;
            updateTaskIdDisplay(null);
        }
        // Extract profile_type from event details if available (conversation_agent_start event)
        const profileType = details?.profile_type || 'conversation_with_tools';
        // Capture turn number from conversation_agent_start (replaces lifecycle execution_start)
        if (type === 'conversation_agent_start' && details?.turn_id) {
            state.currentTurnNumber = details.turn_id;
        }
        // On completion, transition title from "Live Status - ..." to "<Brand> - Turn X"
        // (replaces lifecycle execution_complete title transition)
        const isLive = type !== 'conversation_agent_complete';
        statusTitle.textContent = _formatStatusTitle(profileType, isLive);
        // Render conversation agent events using custom renderer
        _renderConversationAgentStep(eventData, DOM.statusWindowContent, isFinal);
        if (!state.isMouseOverStatus && !state.isViewingHistoricalTurn) {
            DOM.statusWindowContent.scrollTop = DOM.statusWindowContent.scrollHeight;
        }
        return;
    } else if (source === 'knowledge_retrieval') {
        // Knowledge retrieval events (arrives before conversation_agent_start for conversation_with_tools)
        // Reset the status window if no agent execution is active yet AND no existing steps (e.g., session name generation)
        if (!state.isConversationAgentActive && !state.isGenieCoordinationActive && !state.isRestTaskActive) {
            const hasExistingSteps = DOM.statusWindowContent.querySelector('.status-step');
            if (!hasExistingSteps) {
                resetStatusWindowForNewTask();
                updateTaskIdDisplay(null);
            }
        }
        statusTitle.textContent = _formatStatusTitle('rag_focused');
        // Render knowledge retrieval using the conversation agent renderer (same styling)
        _renderConversationAgentStep(eventData, DOM.statusWindowContent, false);
        if (!state.isMouseOverStatus && !state.isViewingHistoricalTurn) {
            DOM.statusWindowContent.scrollTop = DOM.statusWindowContent.scrollHeight;
        }
        return;
    } else if (source === 'session_name') {
        // Session name generation events - route to conversation agent renderer
        // Don't change status title — session name generation is a background activity
        // that shouldn't override the current execution context title
        _renderConversationAgentStep(eventData, DOM.statusWindowContent, isFinal);
        if (!state.isMouseOverStatus && !state.isViewingHistoricalTurn) {
            DOM.statusWindowContent.scrollTop = DOM.statusWindowContent.scrollHeight;
        }
        return;
    } else if (source === 'skills') {
        // Skill events — emerald green renderer for pre-processing transparency
        _renderSkillStep(eventData, DOM.statusWindowContent);
        if (!state.isMouseOverStatus && !state.isViewingHistoricalTurn) {
            DOM.statusWindowContent.scrollTop = DOM.statusWindowContent.scrollHeight;
        }
        return;
    } else if (source === 'extension') {
        // Extension events — use dedicated compact renderer (amber-accented, matches reload style)
        _renderExtensionStep(eventData, DOM.statusWindowContent);
        if (!state.isMouseOverStatus && !state.isViewingHistoricalTurn) {
            DOM.statusWindowContent.scrollTop = DOM.statusWindowContent.scrollHeight;
        }
        return;
    } else if (source === 'lifecycle') {
        // Lifecycle events (execution_start, execution_complete, etc.) for all profile types

        // Capture turn number from execution_start for title display
        if (type === 'execution_start' && details?.turn_id) {
            state.currentTurnNumber = details.turn_id;
        }

        // Reset status window if no active execution
        if (!state.isConversationAgentActive && !state.isGenieCoordinationActive && !state.isRestTaskActive) {
            const hasExistingSteps = DOM.statusWindowContent.querySelector('.status-step');
            if (!hasExistingSteps) {
                resetStatusWindowForNewTask();
                updateTaskIdDisplay(null);
                // Use branded title from execution_start payload if available,
                // so the correct agent name appears immediately (no brief "Live Status" gap)
                if (type === 'execution_start' && details?.profile_type) {
                    statusTitle.textContent = _formatStatusTitle(details.profile_type, true);
                } else {
                    statusTitle.textContent = 'Live Status';
                }
            }
            // When existing steps are present, preserve the current title —
            // the profile-specific execution (Genie, Conversation, etc.) already set it.
        }

        // On execution_complete: transition from "Live Status - ..." to "<Brand> - Turn X"
        if (type === 'execution_complete' && details?.profile_type) {
            statusTitle.textContent = _formatStatusTitle(details.profile_type, false);
        }

        // For tool_enabled profiles during live execution, route lifecycle events
        // through _renderStandardStep so they integrate visually with optimizer steps.
        // Detect optimizer context by checking for existing standard (non-conversation-agent) steps.
        const hasStandardSteps = DOM.statusWindowContent.querySelector(
            '.status-step:not(.conversation-agent-status-step):not(.knowledge-retrieval-status-step)'
        );

        if (hasStandardSteps && (type === 'execution_start' || type === 'execution_complete' ||
            type === 'execution_error' || type === 'execution_cancelled')) {
            // Sub-executor lifecycle events render inside the active phase container
            // so they appear nested within the parent's phase (not at top level).
            const lifecycleDepth = metadata?.execution_depth || 0;
            const lifecycleTarget = lifecycleDepth > 0 && state.currentPhaseContainerEl
                ? state.currentPhaseContainerEl.querySelector('.status-phase-content') || DOM.statusWindowContent
                : DOM.statusWindowContent;
            _renderStandardStep(eventData, lifecycleTarget, isFinal);
        } else {
            _renderConversationAgentStep(eventData, DOM.statusWindowContent, isFinal);
        }

        if (!state.isMouseOverStatus && !state.isViewingHistoricalTurn) {
            DOM.statusWindowContent.scrollTop = DOM.statusWindowContent.scrollHeight;
        }
        return;
    } else if (source === 'interactive') {
        // If the last active view was a REST task or agent execution, reset the view
        // BUT: Don't reset if genie/conversation agent is CURRENTLY active - let them finish!
        // Only reset when switching FROM those modes TO interactive mode.
        if ((state.isRestTaskActive || state.isConversationAgentActive) && !state.isGenieCoordinationActive) {
            resetStatusWindowForNewTask();
            updateTaskIdDisplay(null); // Hide the task ID
        }
        // Title is NOT reset here — it is managed by lifecycle events (execution_start)
        // and profile-specific source handlers (genie, conversation_agent, rest).
        // Resetting here would overwrite the branded title on every tool_enabled event.
    }

    const execution_depth_from_details = details?.execution_depth ?? 0;
    const execution_depth_from_metadata = metadata?.execution_depth ?? 0;
    const execution_depth = Math.max(execution_depth_from_details, execution_depth_from_metadata);

    const isPlanningEvent = step?.startsWith("Calling LLM for") || type === "plan_generated";

    if (isPlanningEvent && execution_depth > 0 && type !== 'phase_start') {
        state.pendingSubtaskPlanningEvents.push(eventData);
        return;
    }

    if (!step && type !== 'phase_start' && type !== 'phase_end') {
        if (type !== 'tool_result' && type !== 'tool_error') {
            return;
        }
    }

    if (type === 'phase_start') {
        const { phase_num, total_phases, goal } = details;

        while (state.phaseContainerStack.length > execution_depth) {
            const oldContainer = state.phaseContainerStack.pop();
            if (oldContainer) {
                oldContainer.classList.add('completed');
            }
        }

        const parentContainer = state.phaseContainerStack.length > 0 ? state.phaseContainerStack[state.phaseContainerStack.length - 1].querySelector('.status-phase-content') : DOM.statusWindowContent;

        const lastStep = Array.from(parentContainer.querySelectorAll(':scope > .status-step')).pop();
        if (lastStep && lastStep.classList.contains('active')) {
            lastStep.classList.remove('active');
            if (!lastStep.classList.contains('plan-optimization')) {
                 lastStep.classList.add('completed');
            }
        }

        const phaseContainer = document.createElement('details');
        phaseContainer.className = 'status-phase-container';
        phaseContainer.dataset.filterCategory = 'planning';
        if (!isFilterCategoryEnabled('planning')) {
            phaseContainer.classList.add('event-filtered-hidden');
        }
        // Collapsed by default — user can expand to inspect details
        phaseContainer.open = false;

        const phaseHeader = document.createElement('summary');
        phaseHeader.className = 'status-phase-header phase-start';

        let depthIndicator = '';
        if (execution_depth > 0) {
            depthIndicator = '↳ '.repeat(execution_depth);
        }

        phaseHeader.innerHTML = `
            <span class="font-bold flex-shrink-0">${depthIndicator}Phase ${phase_num}/${total_phases}</span>
            <span class="text-gray-400 text-xs truncate ml-2">${goal}</span>
        `;

        phaseContainer.appendChild(phaseHeader);

        const phaseContent = document.createElement('div');
        phaseContent.className = 'status-phase-content';
        phaseContainer.appendChild(phaseContent);

        if (execution_depth > 0 && state.pendingSubtaskPlanningEvents.length > 0) {
            state.pendingSubtaskPlanningEvents.forEach(event => {
                _renderStandardStep(event, phaseContent, false);
            });
            state.pendingSubtaskPlanningEvents = [];
        }

        parentContainer.appendChild(phaseContainer);
        state.phaseContainerStack.push(phaseContainer);
        state.currentPhaseContainerEl = phaseContainer;

        if (!state.isMouseOverStatus && !state.isViewingHistoricalTurn) {
            DOM.statusWindowContent.scrollTop = DOM.statusWindowContent.scrollHeight;
        }
        return;
    }

    if (type === 'phase_end') {
        const containerToEnd = state.phaseContainerStack.pop();
        if (containerToEnd) {
            const phaseContent = containerToEnd.querySelector('.status-phase-content');
            if (phaseContent) {
                const lastStepInPhase = Array.from(phaseContent.childNodes).filter(node => node.classList && node.classList.contains('status-step')).pop();
                if (lastStepInPhase && lastStepInPhase.classList.contains('active')) {
                    lastStepInPhase.classList.remove('active');
                    lastStepInPhase.classList.add('completed');
                }
            }

            const { phase_num, total_phases, status, execution_depth } = details;
            const depthFromDetails = execution_depth ?? 0;

            let depthIndicator = '';
            if (depthFromDetails > 0) {
                depthIndicator = '↳ '.repeat(depthFromDetails);
            }

            const phaseFooter = document.createElement('div');
            phaseFooter.className = 'status-phase-header phase-end';
            phaseFooter.innerHTML = `<span class="font-bold">${depthIndicator}Phase ${phase_num}/${total_phases} Completed</span>`;

            if (status === 'skipped') {
                phaseFooter.classList.add('skipped');
                phaseFooter.innerHTML = `<span class="font-bold">${depthIndicator}Phase ${phase_num}/${total_phases} Skipped</span>`;
            } else {
                containerToEnd.classList.add('completed');
            }

            containerToEnd.appendChild(phaseFooter);
        }
        state.currentPhaseContainerEl = state.phaseContainerStack.length > 0 ? state.phaseContainerStack[state.phaseContainerStack.length - 1] : null;
        state.isInFastPath = false;
        if (!state.isMouseOverStatus && !state.isViewingHistoricalTurn) {
            DOM.statusWindowContent.scrollTop = DOM.statusWindowContent.scrollHeight;
        }
        return;
    }

    const parentEl = state.currentPhaseContainerEl ? state.currentPhaseContainerEl.querySelector('.status-phase-content') : DOM.statusWindowContent;
    _renderStandardStep(eventData, parentEl, isFinal);

    // Breathing space handled via CSS padding-bottom; avoid DOM spacers to prevent layout jumps.

    // Simplified and user-friendly auto-scroll:
    // - Attach a scroll listener once to detect manual scroll-up and pause auto-stick.
    // - Only stick to bottom when auto-scroll is enabled (user near bottom).
    const container = DOM.statusWindowContent;
    if (!state._statusScrollInitialized) {
        state._statusAutoScrollEnabled = true; // assume start near bottom
        container.addEventListener('scroll', () => {
            const dist = container.scrollHeight - container.scrollTop - container.clientHeight;
            // If user moves away from bottom beyond threshold, disable auto-scroll
            if (dist > 40) {
                state._statusAutoScrollEnabled = false;
            } else {
                // Near bottom again -> re-enable
                state._statusAutoScrollEnabled = true;
            }
        }, { passive: true });
        state._statusScrollInitialized = true;
    }

    // If the container is at the very top (e.g., after plan reload), do not auto-stick.
    if (container.scrollTop === 0) {
        state._statusAutoScrollEnabled = false;
    }

    if (state._statusAutoScrollEnabled && !state.isViewingHistoricalTurn) {
        // Use rAF to ensure layout finalized before scrolling
        requestAnimationFrame(() => {
            const lastStep = parentEl.querySelector(':scope > .status-step:last-of-type')
                || container.querySelector('.status-step:last-of-type');
            if (lastStep && typeof lastStep.scrollIntoView === 'function') {
                lastStep.scrollIntoView({ behavior: 'auto', block: 'end' });
            } else {
                container.scrollTop = container.scrollHeight;
            }
        });
    }
}

/**
 * Updates the display of the current task ID in the status window header.
 * Includes functionality to copy the task ID to the clipboard.
 * @param {string|null} taskId - The task ID to display, or null to hide the display.
 */
export function updateTaskIdDisplay(taskId) {
    if (DOM.taskIdDisplay && DOM.taskIdValue && DOM.copyTaskIdButton) {
        if (taskId) {
            DOM.taskIdValue.textContent = `Task ID: ${taskId.substring(0, 8)}...`;
            DOM.taskIdDisplay.classList.remove('hidden');

            // Remove any existing event listener to prevent duplicates
            DOM.copyTaskIdButton.onclick = null; 
            DOM.copyTaskIdButton.onclick = () => {
                navigator.clipboard.writeText(taskId).then(() => {
                    // Provide visual feedback
                    const originalIcon = DOM.copyTaskIdButton.innerHTML;
                    DOM.copyTaskIdButton.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 text-green-500" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd" /></svg>`;
                    setTimeout(() => {
                        DOM.copyTaskIdButton.innerHTML = originalIcon;
                    }, 2000);
                }).catch(err => {
                    console.error('Failed to copy task ID: ', err);
                });
            };
        } else {
            DOM.taskIdDisplay.classList.add('hidden');
            DOM.taskIdValue.textContent = '';
            DOM.copyTaskIdButton.onclick = null; // Clear event listener
        }
    }
}

/**
 * Provides a visual cue on a session in the list to show it has new, unseen activity.
 * @param {string} sessionId The ID of the session to highlight.
 */
export function highlightSession(sessionId) {
    const sessionItem = document.getElementById(`session-${sessionId}`);
    if (sessionItem && !sessionItem.classList.contains('active')) {
        const nameSpan = sessionItem.querySelector('.session-name-span');
        if (nameSpan) {
            nameSpan.classList.add('font-bold', 'text-teradata-orange');
        }
        // Add a blinking dot or similar indicator
        let indicator = sessionItem.querySelector('.new-activity-indicator');
        if (!indicator) {
            indicator = document.createElement('span');
            indicator.className = 'new-activity-indicator';
            sessionItem.querySelector('.flex').prepend(indicator);
        }
    }
}

/**
 * Moves a session item to the top of the session list.
 * For Genie slave sessions, maintains hierarchical order by moving after the last sibling slave.
 * @param {string} sessionId - The ID of the session to move.
 */
export function moveSessionToTop(sessionId) {
    const sessionItem = document.getElementById(`session-${sessionId}`);
    if (!sessionItem || !DOM.sessionList) return;

    // CRITICAL: Check if session has a wrapper (for tree structure)
    // If it does, move the wrapper instead of the session item
    const wrapper = sessionItem.closest('.genie-wrapper');
    const elementToMove = wrapper || sessionItem;

    // Check if this is a Genie slave session (check wrapper first, fallback to item)
    const isGenieSlave = wrapper ? wrapper.dataset.parentId : sessionItem.dataset.genieParentId;
    const parentSessionId = wrapper ? wrapper.dataset.parentId : sessionItem.dataset.genieParentId;

    if (isGenieSlave && parentSessionId) {
        // For slave sessions: do NOT reorder the slave within its siblings.
        // Instead, bubble the parent (coordinator) session to the top — this carries
        // all slaves with it in their current stable DOM order.
        // This prevents SSE session_model_update events for individual slaves from
        // scrambling the sibling display order.
        moveSessionToTop(parentSessionId);
        return;
    } else {
        // Regular session (not a slave) - move to top
        // For Genie master sessions, also collect and move all slave wrappers so the
        // visual hierarchy stays intact (slaves immediately follow their master).
        const slaveWrappers = Array.from(
            DOM.sessionList.querySelectorAll(`.genie-wrapper[data-parent-id="${sessionId}"]`)
        );

        elementToMove.remove();
        slaveWrappers.forEach(w => w.remove());

        DOM.sessionList.prepend(elementToMove);

        // Re-insert slaves in their original relative order, directly after the master
        let insertAfter = elementToMove;
        slaveWrappers.forEach(slaveWrapper => {
            insertAfter.insertAdjacentElement('afterend', slaveWrapper);
            insertAfter = slaveWrapper;
        });

        // Recalculate last-child connectors after any rearrangement
        if (slaveWrappers.length > 0) {
            refreshGenieConnectors(sessionId);
        }
    }
}

/**
 * Recalculates which Genie slave wrapper is the last child for a given parent
 * and updates the `last-child` CSS class accordingly (controls └ vs ├ connector).
 * Must be called after any DOM reordering of slave wrappers.
 * @param {string} parentSessionId
 */
export function refreshGenieConnectors(parentSessionId) {
    if (!DOM.sessionList) return;
    const siblings = Array.from(
        DOM.sessionList.querySelectorAll(`.genie-wrapper[data-parent-id="${parentSessionId}"]`)
    );
    siblings.forEach((wrapper, index) => {
        wrapper.classList.toggle('last-child', index === siblings.length - 1);
    });
}


/**
 * Recomputes every derived live-cost figure from the idempotent per-call ledgers.
 * Turn cost = sum over turnCalls; session cost = reload base + sum over sessionCalls.
 * Strategic/tactical breakdowns are derived the same way so the dual-model tooltip is
 * consistent with the headline numbers. Because the ledgers are keyed by call_id, this
 * is immune to multi-channel re-delivery and per-step/token_update dual-emission.
 *
 * @param {Object} acc - window.sessionCostAccumulator
 */
function _recomputeLiveCost(acc) {
    let turn = 0, sTurnIn = 0, sTurnOut = 0, sTurnCost = 0, tTurnIn = 0, tTurnOut = 0, tTurnCost = 0;
    for (const id in acc.turnCalls) {
        const c = acc.turnCalls[id];
        turn += c.cost;
        if (c.phase === 'strategic') { sTurnIn += c.inTok; sTurnOut += c.outTok; sTurnCost += c.cost; }
        else { tTurnIn += c.inTok; tTurnOut += c.outTok; tTurnCost += c.cost; }
    }
    let sessionLive = 0, sSessIn = 0, sSessOut = 0, tSessIn = 0, tSessOut = 0;
    for (const id in acc.sessionCalls) {
        const c = acc.sessionCalls[id];
        sessionLive += c.cost;
        if (c.phase === 'strategic') { sSessIn += c.inTok; sSessOut += c.outTok; }
        else { tSessIn += c.inTok; tSessOut += c.outTok; }
    }
    acc.turn = turn;
    acc.session = (acc.sessionBaseCost || 0) + sessionLive;
    acc.strategic = sTurnCost;
    acc.tactical = tTurnCost;
    acc.strategicTurnIn = sTurnIn; acc.strategicTurnOut = sTurnOut; acc.strategicTurnCost = sTurnCost;
    acc.tacticalTurnIn = tTurnIn;  acc.tacticalTurnOut = tTurnOut;  acc.tacticalTurnCost = tTurnCost;
    acc.strategicSessionIn = sSessIn; acc.strategicSessionOut = sSessOut;
    acc.tacticalSessionIn = tSessIn;  acc.tacticalSessionOut = tSessOut;
}

export function updateTokenDisplay(data, isHistorical = false) {
    const normalDisplay = document.getElementById('token-normal-display');
    const awsMessage = document.getElementById('token-aws-message');

    // Update turn label based on whether this is a historical view
    const turnLabel = document.getElementById('turn-token-label');
    if (turnLabel) {
        turnLabel.textContent = isHistorical ? 'Reloaded Turn' : 'Last Turn';
    }

    // Check if we have token data (for any provider including Amazon)
    const hasTokenData = (data.statement_input > 0 || data.statement_output > 0 ||
                         data.turn_input > 0 || data.turn_output > 0 ||
                         data.total_input > 0 || data.total_output > 0);

    // Check if this is an AWS model that doesn't support token counting
    // Only Cohere, Mistral, and AI21 don't provide token counts
    const isUnsupportedAwsModel = state.currentProvider === 'Amazon' &&
        state.currentModel && (
            state.currentModel.toLowerCase().includes('cohere') ||
            state.currentModel.toLowerCase().includes('mistral') ||
            state.currentModel.toLowerCase().includes('ai21')
        );

    if (isUnsupportedAwsModel && !hasTokenData) {
        // Only show warning message for Cohere/Mistral/AI21 without token data
        normalDisplay.classList.add('hidden');
        awsMessage.classList.remove('hidden');
        return;
    }

    // Show normal display for all other cases (including Nova, Titan, Llama, Claude)
    normalDisplay.classList.remove('hidden');
    awsMessage.classList.add('hidden');

    // ========== TOKEN DISPLAY LOGIC WITH TERMINAL EVENT PROTECTION ==========
    // Get token display elements
    const stmtInEl = document.getElementById('statement-input-tokens');
    const stmtOutEl = document.getElementById('statement-output-tokens');
    const turnInEl = document.getElementById('turn-input-tokens');
    const turnOutEl = document.getElementById('turn-output-tokens');
    const totalInEl = document.getElementById('total-input-tokens');
    const totalOutEl = document.getElementById('total-output-tokens');

    // CRITICAL FIX: Skip token updates if ALL token fields are undefined
    // Events like genie_llm_step and session_name_generation_complete only have cost data, no tokens
    // If we process these with undefined tokens, they get converted to 0 and reset the display
    const allTokenFieldsUndefined = (
        data.statement_input === undefined &&
        data.statement_output === undefined &&
        data.turn_input === undefined &&
        data.turn_output === undefined &&
        data.total_input === undefined &&
        data.total_output === undefined
    );

    // Debug logging for ALL token updates (live and historical)
    console.log(`%c[Token Display] 🎯 updateTokenDisplay called`, 'background: #00ffff; color: #000; font-weight: bold;');
    console.log(`  isHistorical: ${isHistorical}`);
    console.log(`  allTokenFieldsUndefined: ${allTokenFieldsUndefined}`);
    console.log(`  data:`, {
        statement: `${data.statement_input} in / ${data.statement_output} out`,
        turn: `${data.turn_input} in / ${data.turn_output} out`,
        total: `${data.total_input} in / ${data.total_output} out`,
        cost: data.cost_usd || 0
    });

    if (allTokenFieldsUndefined && !isHistorical) {
        console.log('%c[Token Display] ⚠️ SKIPPING TOKEN UPDATE - All token fields undefined (cost-only event)', 'background: #ff6600; color: #fff; font-weight: bold;');
        // Skip to cost processing only (don't reset tokens to 0)
        // Fall through to cost updates below line 3978
    } else {
        // CRITICAL: Detect terminal token_update events (statement tokens are 0 but turn/total tokens present)
        // These events are sent by the backend to update cumulative turn/total tokens without new statement data
        // If we overwrite statement tokens with 0, users see blank counters even though LLM calls happened
        const isTerminalEvent = (
            !isHistorical &&  // Only applies to live execution
            (data.statement_input === 0 || data.statement_input === undefined) &&
            (data.statement_output === 0 || data.statement_output === undefined) &&
            (data.turn_input !== undefined || data.total_input !== undefined)  // Has turn/total data
        );

        console.log(`  isTerminalEvent: ${isTerminalEvent}`);

    if (isTerminalEvent) {
        console.log('%c[Token Display] ⚠️ TERMINAL EVENT - Will preserve statement tokens, only update turn/total if > 0', 'background: #ffff00; color: #000;');

        // Preserve statement tokens (don't overwrite with 0)
        // Only update turn and total tokens if they're NON-ZERO (preserve existing display if 0)
        // This prevents terminal events with all zeros (e.g., session load initialization) from resetting display to 0
        if (data.turn_input !== undefined && data.turn_input > 0 && turnInEl) {
            turnInEl.textContent = data.turn_input.toLocaleString();
        }
        if (data.turn_output !== undefined && data.turn_output > 0 && turnOutEl) {
            turnOutEl.textContent = data.turn_output.toLocaleString();
        }
        if (data.total_input !== undefined && data.total_input > 0 && totalInEl) {
            totalInEl.textContent = data.total_input.toLocaleString();
        }
        if (data.total_output !== undefined && data.total_output > 0 && totalOutEl) {
            totalOutEl.textContent = data.total_output.toLocaleString();
        }

        // Skip statement token update - preserve existing display
        // Continue with cost updates below...
    } else {
        // Normal update — write ONLY the fields the event carries; absent means
        // "leave alone", never 0. The old `|| 0` writes made every PARTIAL emitter
        // a wiper: session_name_generation_complete maps statement fields only,
        // and because name generation runs LAST its statement-only update was the
        // final state — an operator's screenshot showed LAST STMT 242/4 with LAST
        // TURN 0/0 and SESSION TOTAL 0/0 after a 13k-token turn. An event that
        // wants to RESET a field must send an explicit 0.
        console.log('%c[Token Display] ✅ NORMAL UPDATE - Setting carried token fields', 'background: #00ff00; color: #000;');

        const _setIf = (el, v) => { if (el && v !== undefined && v !== null) el.textContent = (v || 0).toLocaleString(); };
        _setIf(stmtInEl, data.statement_input);
        _setIf(stmtOutEl, data.statement_output);
        _setIf(turnInEl, data.turn_input);
        _setIf(turnOutEl, data.turn_output);
        _setIf(totalInEl, data.total_input);
        _setIf(totalOutEl, data.total_output);
    }
    }  // Close outer else block (allTokenFieldsUndefined check)

    // ========== COST TRACKING (Dual-Model Feature) ==========
    // Initialize cost accumulator if not exists
    if (!window.sessionCostAccumulator) {
        window.sessionCostAccumulator = {
            turn: 0,
            session: 0,
            strategic: 0,
            tactical: 0,
            // Per-model turn-level tracking
            strategicTurnIn: 0, strategicTurnOut: 0, strategicTurnCost: 0,
            tacticalTurnIn: 0,  tacticalTurnOut: 0,  tacticalTurnCost: 0,
            // Per-model session-level token tracking
            strategicSessionIn: 0, strategicSessionOut: 0,
            tacticalSessionIn: 0,  tacticalSessionOut: 0,
            // Last statement model info
            lastStmtPhase: null,
            // Turn/session tracking for proper reset detection
            lastTurnNumber: null,     // Track last turn number to detect new turns
            sessionId: null,           // Track session ID to validate accumulator
            // --- Idempotent per-call cost ledgers (keyed by call_id) ---
            // Cost is attributed PER LLM CALL, not summed per event. Re-delivery of the
            // same call (multi-channel SSE: per-request stream + notification channel +
            // REST dispatch) or dual-emission (a per-step event AND its token_update)
            // overwrites the same key instead of inflating the total — mirroring how
            // cumulative token counts are SET rather than summed. Single source of truth
            // for live cost; see _recomputeLiveCost().
            turnCalls: {},        // call_id -> {cost,inTok,outTok,phase}; reset on new turn
            sessionCalls: {},     // call_id -> {cost,inTok,outTok,phase}; reset on session change
            sessionBaseCost: 0,   // cost of turns completed before this live view (from reload)
        };
    }

    // ========== COST DISPLAY LOGIC ==========
    const acc = window.sessionCostAccumulator;

    if (isHistorical) {
        // Historical reload - use backend-provided costs (authoritative)
        if (data.turn_cost !== undefined && data.turn_cost !== null) {
            acc.turn = parseFloat(data.turn_cost);
            console.log(`[Historical] Turn cost from backend: $${acc.turn.toFixed(6)}`);
        } else if (data.cost_usd !== undefined && data.cost_usd !== null) {
            // Fallback: old field name (for backwards compatibility)
            acc.turn = parseFloat(data.cost_usd);
            console.warn('[Historical] Using legacy cost_usd field for turn cost');
        } else {
            // Fallback: Calculate from tokens (should rarely happen if backend works correctly)
            console.warn('[Historical] Backend did not provide turn_cost, estimating from tokens');
            acc.turn = _estimateCostFromTokens(data.turn_input || 0, data.turn_output || 0);
        }

        if (data.session_cost_usd !== undefined && data.session_cost_usd !== null) {
            acc.session = parseFloat(data.session_cost_usd);
            console.log(`[Historical] Session cost from backend: $${acc.session.toFixed(6)}`);
        } else {
            // Fallback: Calculate from session token totals
            console.warn('[Historical] Backend did not provide session_cost_usd, estimating from tokens');
            acc.session = _estimateCostFromTokens(data.total_input || 0, data.total_output || 0);
        }

        // Populate per-model breakdown if provided
        if (data.perModelBreakdown) {
            const pm = data.perModelBreakdown;
            acc.strategicTurnIn = pm.strategicIn || 0;
            acc.strategicTurnOut = pm.strategicOut || 0;
            acc.strategicTurnCost = pm.strategicCost || 0;
            acc.tacticalTurnIn = pm.tacticalIn || 0;
            acc.tacticalTurnOut = pm.tacticalOut || 0;
            acc.tacticalTurnCost = pm.tacticalCost || 0;
            acc.strategic = pm.strategicCost || 0;
            acc.tactical = pm.tacticalCost || 0;
            acc.strategicSessionIn = pm.strategicIn || 0;
            acc.strategicSessionOut = pm.strategicOut || 0;
            acc.tacticalSessionIn = pm.tacticalIn || 0;
            acc.tacticalSessionOut = pm.tacticalOut || 0;
            acc.lastStmtPhase = pm.strategicCost > 0 ? 'strategic' : (pm.tacticalCost > 0 ? 'tactical' : null);
        }

        // Re-baseline the live ledgers against the authoritative reloaded session cost so
        // any subsequent live turn builds on top of it without double-counting.
        acc.sessionBaseCost = acc.session;
        acc.turnCalls = {};
        acc.sessionCalls = {};

    } else {
        // Live execution — attribute cost PER CALL (idempotent), never sum per event.
        if (data.cost_usd !== undefined && data.cost_usd !== null) {
            const callCost = parseFloat(data.cost_usd) || 0;
            const stmtIn = data.statement_input || 0;
            const stmtOut = data.statement_output || 0;
            const eventType = data.type || '';

            // Aggregate "complete" events carry a SUMMED cost that duplicates the
            // individual per-call steps — never fold them into the per-call ledger.
            const AGGREGATE_COST_EVENTS = [
                'conversation_llm_complete', 'conversation_agent_complete',
                'genie_coordinator_complete', 'knowledge_retrieval_complete'
            ];

            if (callCost > 0 && !AGGREGATE_COST_EVENTS.includes(eventType)) {
                // Detect a new turn by turn_id change and reset ONLY the per-turn ledger.
                // (Cost events may lack turn_id, so fall back to the captured turn number.)
                const currentTurnNumber = data.turn_id || data.turn_number || data.turn
                    || (typeof state !== 'undefined' ? state.currentTurnNumber : null) || null;
                const isNewTurn = (
                    currentTurnNumber !== null &&
                    acc.lastTurnNumber !== null &&
                    currentTurnNumber > acc.lastTurnNumber
                );
                if (isNewTurn) {
                    acc.turnCalls = {};
                }
                if (currentTurnNumber !== null) {
                    acc.lastTurnNumber = currentTurnNumber;
                }

                // Stable per-call identity: prefer the backend call_id (every LLM call
                // stamps one); otherwise a signature that is stable across re-delivery of
                // the identical payload, so multi-channel duplicates still collapse.
                const callId = data.call_id || `${eventType}|${stmtIn}|${stmtOut}|${callCost}`;
                const phase = (data.planning_phase === 'strategic') ? 'strategic' : 'tactical';
                const entry = { cost: callCost, inTok: stmtIn, outTok: stmtOut, phase };

                // Idempotent write: re-delivery / dual-emission overwrites the same key.
                acc.turnCalls[callId] = entry;
                acc.sessionCalls[callId] = entry;
                acc.lastStmtPhase = data.planning_phase ? phase : null;

                _recomputeLiveCost(acc);
            }
        }
    }

    // Display costs (always show current accumulator values)
    const costSection = document.getElementById('cost-display-section');
    const turnCostEl = document.getElementById('turn-cost-value');
    const sessionCostEl = document.getElementById('session-cost-value');

    if (costSection && turnCostEl && sessionCostEl) {
        costSection.classList.remove('hidden');
        turnCostEl.textContent = `$${acc.turn.toFixed(6)}`;
        sessionCostEl.textContent = `$${acc.session.toFixed(6)}`;
    }

    // ========== DUAL-MODEL TOOLTIPS ==========
    _updateDualModelTooltips();

    // the composer's receipt shows these same figures — keep it in step (post-turn costs land late)
    if (!isHistorical) { try { if (window.Composer && window.Composer.refresh) window.Composer.refresh(); } catch (_) { /* decoration only */ } }
}

/**
 * Estimates cost from token counts using fallback default pricing.
 * This is only used as fallback when backend doesn't provide costs.
 * Actual pricing varies by provider/model - this is a rough estimate.
 *
 * @param {number} inputTokens - Number of input tokens
 * @param {number} outputTokens - Number of output tokens
 * @returns {number} Estimated cost in USD
 */
function _estimateCostFromTokens(inputTokens, outputTokens) {
    // Fallback default pricing (rough average across providers)
    // Only used when backend doesn't provide costs
    const INPUT_COST_PER_1M = 0.10;   // $0.10 per 1M input tokens
    const OUTPUT_COST_PER_1M = 0.40;  // $0.40 per 1M output tokens

    const inputCost = (inputTokens / 1_000_000) * INPUT_COST_PER_1M;
    const outputCost = (outputTokens / 1_000_000) * OUTPUT_COST_PER_1M;
    const totalCost = inputCost + outputCost;

    console.warn(`[Fallback] Estimated cost from ${inputTokens} in / ${outputTokens} out: $${totalCost.toFixed(6)}`);
    return totalCost;
}

/**
 * Updates dual-model breakdown tooltips on the 5 header metric cards.
 * Only shows tooltips when dual-model mode is active (state.currentDualModelInfo set).
 */
function _updateDualModelTooltips() {
    const cardIds = ['metric-card-statement', 'metric-card-turn', 'metric-card-session',
                     'metric-card-turn-cost', 'metric-card-session-cost'];

    if (!state.currentDualModelInfo || !window.sessionCostAccumulator) {
        // Clear tooltips when not in dual-model mode
        cardIds.forEach(id => {
            const el = document.getElementById(id);
            if (el) el.removeAttribute('data-tooltip-html');
        });
        return;
    }

    const dmi = state.currentDualModelInfo;
    const acc = window.sessionCostAccumulator;
    const sModel = dmi.strategicModel || 'Strategic';
    const tModel = dmi.tacticalModel || 'Tactical';

    // Helper: build a tooltip row for one model
    // showCost=false for token cards, showCost=true for cost cards
    const row = (label, cssClass, inTk, outTk, cost, showCost) =>
        `<div class="dm-tooltip-row">` +
        `<span class="dm-tooltip-label ${cssClass}">${label}</span>` +
        `<span class="dm-tooltip-values">${inTk.toLocaleString()} in / ${outTk.toLocaleString()} out` +
        (showCost && cost > 0 ? ` &middot; $${cost.toFixed(6)}` : '') +
        `</span></div>`;

    // Last Stmt: which model was used for this call
    const stmtCard = document.getElementById('metric-card-statement');
    if (stmtCard) {
        if (acc.lastStmtPhase) {
            const label = acc.lastStmtPhase === 'strategic' ? sModel : tModel;
            const cls = acc.lastStmtPhase === 'strategic' ? 'dm-tooltip-strategic' : 'dm-tooltip-tactical';
            stmtCard.setAttribute('data-tooltip-html',
                `<span class="${cls}" style="font-weight:600">${label}</span>`);
        } else {
            stmtCard.removeAttribute('data-tooltip-html');
        }
    }

    // Last Turn: strategic + tactical token split (no cost)
    const turnCard = document.getElementById('metric-card-turn');
    if (turnCard) {
        if (acc.strategicTurnIn > 0 || acc.tacticalTurnIn > 0) {
            turnCard.setAttribute('data-tooltip-html',
                row(sModel, 'dm-tooltip-strategic', acc.strategicTurnIn, acc.strategicTurnOut, 0, false) +
                row(tModel, 'dm-tooltip-tactical', acc.tacticalTurnIn, acc.tacticalTurnOut, 0, false));
        } else {
            turnCard.removeAttribute('data-tooltip-html');
        }
    }

    // Session Total: strategic + tactical token split (no cost)
    const sessCard = document.getElementById('metric-card-session');
    if (sessCard) {
        if (acc.strategicSessionIn > 0 || acc.tacticalSessionIn > 0) {
            sessCard.setAttribute('data-tooltip-html',
                row(sModel, 'dm-tooltip-strategic', acc.strategicSessionIn, acc.strategicSessionOut, 0, false) +
                row(tModel, 'dm-tooltip-tactical', acc.tacticalSessionIn, acc.tacticalSessionOut, 0, false));
        } else {
            sessCard.removeAttribute('data-tooltip-html');
        }
    }

    // Turn Cost: strategic + tactical cost split (with cost)
    const turnCostCard = document.getElementById('metric-card-turn-cost');
    if (turnCostCard) {
        if (acc.strategicTurnCost > 0 || acc.tacticalTurnCost > 0) {
            turnCostCard.setAttribute('data-tooltip-html',
                row(sModel, 'dm-tooltip-strategic', acc.strategicTurnIn, acc.strategicTurnOut, acc.strategicTurnCost, true) +
                row(tModel, 'dm-tooltip-tactical', acc.tacticalTurnIn, acc.tacticalTurnOut, acc.tacticalTurnCost, true));
        } else {
            turnCostCard.removeAttribute('data-tooltip-html');
        }
    }

    // Session Cost: strategic + tactical cost split (with cost)
    const sessCostCard = document.getElementById('metric-card-session-cost');
    if (sessCostCard) {
        if (acc.strategic > 0 || acc.tactical > 0) {
            sessCostCard.setAttribute('data-tooltip-html',
                row(sModel, 'dm-tooltip-strategic', acc.strategicSessionIn, acc.strategicSessionOut, acc.strategic, true) +
                row(tModel, 'dm-tooltip-tactical', acc.tacticalSessionIn, acc.tacticalSessionOut, acc.tactical, true));
        } else {
            sessCostCard.removeAttribute('data-tooltip-html');
        }
    }
}

// --- MODIFICATION START: Add function to refresh all feedback button states ---
/**
 * Refreshes all feedback button states based on current state.feedbackByTurn.
 * Should be called after loading a session to restore visual feedback state.
 */
export function refreshFeedbackButtons() {
    // Find all feedback wrappers in the chat
    const feedbackWrappers = document.querySelectorAll('[data-turn-id]');
    
    feedbackWrappers.forEach(wrapper => {
        const turnId = parseInt(wrapper.dataset.turnId);
        if (isNaN(turnId)) return;
        
        const upBtn = wrapper.querySelector('[data-vote="up"]');
        const downBtn = wrapper.querySelector('[data-vote="down"]');
        
        if (!upBtn || !downBtn) return;
        
        const current = state.feedbackByTurn[turnId] || 'none';
        
        // Update active states
        upBtn.classList.toggle('active', current === 'up');
        downBtn.classList.toggle('active', current === 'down');
        
        // Update aria attributes
        upBtn.setAttribute('aria-pressed', current === 'up' ? 'true' : 'false');
        downBtn.setAttribute('aria-pressed', current === 'down' ? 'true' : 'false');
        
        // Update visual styling
        if (current === 'up') {
            upBtn.classList.remove('text-gray-300');
            upBtn.classList.add('text-[#F15F22]', 'bg-gray-800/60');
            downBtn.classList.add('text-gray-300');
            downBtn.classList.remove('text-[#F15F22]', 'bg-gray-800/60');
        } else if (current === 'down') {
            downBtn.classList.remove('text-gray-300');
            downBtn.classList.add('text-[#F15F22]', 'bg-gray-800/60');
            upBtn.classList.add('text-gray-300');
            upBtn.classList.remove('text-[#F15F22]', 'bg-gray-800/60');
        } else {
            upBtn.classList.add('text-gray-300');
            upBtn.classList.remove('text-[#F15F22]', 'bg-gray-800/60');
            downBtn.classList.add('text-gray-300');
            downBtn.classList.remove('text-[#F15F22]', 'bg-gray-800/60');
        }
    });
}
// --- MODIFICATION END ---

export function setThinkingIndicator(isThinking) {
    // Just toggle the small spinning gear icon next to "Configuration" label
    // Keep the model configuration display always visible
    if (isThinking) {
        DOM.thinkingIndicator.classList.remove('hidden');
    } else {
        DOM.thinkingIndicator.classList.add('hidden');
    }
}

export function updateStatusPromptName(provider = null, model = null, isHistorical = false, dualModelInfo = null) {
    const promptNameDiv = document.getElementById('prompt-name-display');

    // Use the provided args, or fall back to the global state
    const providerToShow = provider || state.currentProvider;
    const modelToShow = model || state.currentModel;
    const dualModelInfoToShow = dualModelInfo || state.currentDualModelInfo;

    if (providerToShow && modelToShow) {
        const isCustom = isPromptCustomForModel(providerToShow, modelToShow);
        const promptType = isPrivilegedUser() ? (isCustom ? 'Custom' : 'Default') : 'Default (Server-Side)';

        // Add a visual indicator if we are showing a *historical* turn's model
        const historicalIndicator = isHistorical ? ' (History)' : '';

        // Check if dual-model is active (strategic != tactical)
        console.log('[updateStatusPromptName] dualModelInfo:', dualModelInfoToShow);
        const isDualModel = dualModelInfoToShow &&
                          dualModelInfoToShow.strategicProvider &&
                          dualModelInfoToShow.tacticalProvider &&
                          (dualModelInfoToShow.strategicProvider !== dualModelInfoToShow.tacticalProvider ||
                           dualModelInfoToShow.strategicModel !== dualModelInfoToShow.tacticalModel);
        console.log('[updateStatusPromptName] isDualModel:', isDualModel);

        let modelDisplay = '';
        if (isDualModel) {
            // Show dual-model badge with strategic/tactical breakdown (compact vertical layout)
            const strategicShort = `${dualModelInfoToShow.strategicProvider}/${getNormalizedModelId(dualModelInfoToShow.strategicModel)}`;
            const tacticalShort = `${dualModelInfoToShow.tacticalProvider}/${getNormalizedModelId(dualModelInfoToShow.tacticalModel)}`;
            modelDisplay = `
                <div class="flex flex-col gap-0.5">
                    <div class="flex items-center gap-1.5">
                        <svg class="w-3 h-3 text-blue-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"></path>
                        </svg>
                        <span class="text-xs text-gray-400">Strategic:</span>
                        <span class="font-mono text-blue-300 text-xs truncate">${strategicShort}</span>
                    </div>
                    <div class="flex items-center gap-1.5 pl-4">
                        <span class="text-xs text-gray-400">Tactical:</span>
                        <span class="font-mono text-emerald-300 text-xs truncate">${tacticalShort}</span>
                    </div>
                </div>
            `;
        } else {
            // Standard single-model display
            modelDisplay = `<span class="font-mono text-teradata-orange text-xs">${providerToShow}/${getNormalizedModelId(modelToShow)}</span>`;
        }

        promptNameDiv.innerHTML = `
            <span class="font-semibold text-gray-300">${promptType} Prompt${historicalIndicator}</span>
            <span class="text-gray-500">/</span>
            ${modelDisplay}
        `;
    } else {
        promptNameDiv.innerHTML = '<span>No Model/Prompt Loaded</span>';
    }
}


export function createResourceItem(resource, type) {
    // ── Type config: left accent color + type badge styling ──
    const TYPE_CFG = {
        tools:     { label: 'tool',     color: '#a78bfa', bg: 'rgba(167,139,250,0.10)', border: 'rgba(167,139,250,0.2)', accentActive: '#F15F22', accentOff: '#64748b' },
        prompts:   { label: 'prompt',   color: '#60a5fa', bg: 'rgba(96,165,250,0.10)',  border: 'rgba(96,165,250,0.2)',  accentActive: '#3b82f6', accentOff: '#64748b' },
        resources: { label: 'resource', color: '#94a3b8', bg: 'rgba(148,163,184,0.10)', border: 'rgba(148,163,184,0.2)', accentActive: '#34d399', accentOff: '#64748b' },
    };
    const cfg = TYPE_CFG[type] || TYPE_CFG.resources;
    const accent = resource.disabled ? cfg.accentOff : cfg.accentActive;

    const detailsEl = document.createElement('details');
    detailsEl.id = `resource-${type}-${resource.name}`;
    detailsEl.className = 'resource-item';
    detailsEl.style.cssText = [
        `border-left-width:4px`,
        `border-left-color:${accent}`,
        resource.disabled ? 'opacity:0.6' : '',
    ].filter(Boolean).join(';');

    if (resource.disabled) {
        detailsEl.title = `This ${type === 'tools' ? 'tool' : type === 'prompts' ? 'prompt' : 'resource'} is disabled.`;
    }

    // ── Toggle icon (kept for event handler compatibility) ──
    const toggleIcon = resource.disabled
        ? `<svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M3.707 2.293a1 1 0 00-1.414 1.414l14 14a1 1 0 001.414-1.414l-1.473-1.473A10.014 10.014 0 0019.542 10C18.268 5.943 14.478 3 10 3a9.958 9.958 0 00-4.512 1.074L3.707 2.293zM10 12a2 2 0 110-4 2 2 0 010 4z" clip-rule="evenodd" /><path d="M2 10s3.939 4 8 4 8-4 8-4-3.939-4-8-4-8 4-8 4zm13.707 4.293a1 1 0 00-1.414-1.414L12.586 14.6A8.007 8.007 0 0110 16c-4.478 0-8.268-2.943-9.542-7 .946-2.317 2.83-4.224 5.166-5.447L2.293 1.293A1 1 0 00.879 2.707l14 14a1 1 0 001.414 0z" /></svg>`
        : `<svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path d="M10 12a2 2 0 100-4 2 2 0 000 4z" /><path fill-rule="evenodd" d="M.458 10C1.732 5.943 5.522 3 10 3s8.268 2.943 9.542 7c-1.274 4.057-5.022 7-9.542 7S1.732 14.057.458 10zM14 10a4 4 0 11-8 0 4 4 0 018 0z" clip-rule="evenodd" /></svg>`;

    // ── Inline badge helper ──
    const badge = (text, color, bg, border, mono = false) =>
        `<span class="text-[10px] ${mono ? 'font-mono' : 'font-medium'} px-2 py-0.5 rounded whitespace-nowrap" style="background:${bg};color:${color};border:1px solid ${border};">${text}</span>`;

    // Type badge
    const typeBadge = badge(cfg.label, cfg.color, cfg.bg, cfg.border);

    // Arg count badge (tools/prompts only)
    let argsBadge = '';
    if (resource.arguments && resource.arguments.length > 0) {
        const req = resource.arguments.filter(a => a.required).length;
        const lbl = req > 0 ? `${resource.arguments.length} params, ${req} req` : `${resource.arguments.length} params`;
        argsBadge = badge(lbl, '#34d399', 'rgba(16,185,129,0.12)', 'rgba(16,185,129,0.25)', true);
    }

    // Status badge
    const statusBadge = resource.disabled
        ? badge('DISABLED', '#94a3b8', 'rgba(148,163,184,0.10)', 'rgba(148,163,184,0.2)')
        : badge('ACTIVE',   '#4ade80', 'rgba(74,222,128,0.10)',  'rgba(74,222,128,0.2)');

    // ── Parameters section (theme-aware inline styles) ──
    let argsHTML = '';
    if (resource.arguments && resource.arguments.length > 0) {
        const rows = resource.arguments.map(arg => {
            const reqBadge = arg.required
                ? `<span style="font-size:10px;font-weight:600;color:#f87171;">Required</span>`
                : `<span style="font-size:10px;color:var(--text-muted,#94a3b8);">Optional</span>`;
            const typeSpan = arg.type && arg.type !== 'unknown'
                ? `<span style="font-family:monospace;font-size:10px;color:#22d3ee;background:rgba(34,211,238,0.10);border:1px solid rgba(34,211,238,0.2);padding:0 4px;border-radius:3px;">${arg.type}</span>`
                : '';
            return `<li style="padding:6px 8px;background:rgba(0,0,0,0.12);border-radius:6px;margin-bottom:4px;">
                <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
                    <div style="display:flex;align-items:center;gap:6px;">
                        <code style="font-weight:600;color:var(--teradata-orange,#F15F22);">${arg.name}</code>${typeSpan}
                    </div>${reqBadge}
                </div>
                ${arg.description ? `<p style="color:var(--text-muted,#94a3b8);font-size:11px;margin:4px 0 0;">${arg.description}</p>` : ''}
            </li>`;
        }).join('');
        argsHTML = `<div class="pt-2" style="border-top:1px solid var(--border-primary,rgba(148,163,184,0.18));">
            <h5 class="text-[10px] font-semibold mb-1.5" style="color:var(--text-primary,#e5e7eb);">Parameters</h5>
            <ul style="list-style:none;margin:0;padding:0;">${rows}</ul>
        </div>`;
    }

    // ── Body content ──
    const borderTopStyle = `border-top:1px solid var(--border-primary,rgba(148,163,184,0.18));`;

    let contentHTML = '';
    if (type === 'prompts') {
        contentHTML = `<div class="px-3 pb-3 pt-2 space-y-2 text-xs" style="color:var(--text-muted,#94a3b8);">
            ${resource.description ? `<p>${resource.description}</p>` : ''}
            ${argsHTML}
            <div class="flex justify-end items-center gap-2 pt-2" style="${borderTopStyle}">
                <button class="prompt-toggle-button" style="padding:4px;color:var(--text-muted,#9ca3af);background:none;border:none;cursor:pointer;border-radius:6px;" title="${resource.disabled ? 'Enable' : 'Disable'} prompt">${toggleIcon}</button>
                <button class="view-prompt-button" style="padding:3px 10px;font-size:11px;font-weight:600;border-radius:6px;background:var(--hover-bg-strong,rgba(148,163,184,0.15));color:var(--text-primary,#e5e7eb);border:none;cursor:pointer;">Prompt</button>
                <button class="run-prompt-button" style="padding:3px 10px;font-size:11px;font-weight:600;border-radius:6px;background:#F15F22;color:#fff;border:none;cursor:pointer;" title="Run this prompt.">Run</button>
            </div>
        </div>`;
    } else if (type === 'tools') {
        contentHTML = `<div class="px-3 pb-3 pt-2 space-y-2 text-xs" style="color:var(--text-muted,#94a3b8);">
            ${resource.description ? `<p>${resource.description}</p>` : ''}
            ${argsHTML}
            <div class="flex justify-end pt-2" style="${borderTopStyle}">
                <button class="tool-toggle-button" style="padding:4px;color:var(--text-muted,#9ca3af);background:none;border:none;cursor:pointer;border-radius:6px;" title="${resource.disabled ? 'Enable' : 'Disable'} tool">${toggleIcon}</button>
            </div>
        </div>`;
    } else {
        contentHTML = resource.description
            ? `<div class="px-3 pb-3 pt-2 text-xs" style="color:var(--text-muted,#94a3b8);${borderTopStyle}">${resource.description}</div>`
            : '';
    }

    detailsEl.innerHTML = `
        <summary class="flex justify-between items-center px-3 py-2.5">
            <div class="flex items-center gap-2 flex-wrap min-w-0">
                <span class="text-sm font-medium truncate" style="color:var(--text-primary,#e5e7eb);">${resource.name}</span>
                ${typeBadge}${argsBadge}${statusBadge}
            </div>
            <svg class="chevron w-5 h-5 flex-shrink-0" style="color:var(--text-muted,#9ca3af);" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
            </svg>
        </summary>
        ${contentHTML}`;

    return detailsEl;
}

// Every resource-panel tab badge reads the same way (resourceBadge.js): enabled/available
// — what the agent may use over what its list holds (an enabled tool is active every turn).
export function updateResourceTabCounter(type, name) {
    const tabButton = document.querySelector(`.resource-tab[data-type="${type}"]`);
    const data = state.resourceData[type];
    if (!tabButton || !data) return;
    const items = Object.values(data).flat();
    tabButton.textContent = tabLabel(name, items, item => !item.disabled);
}

export function updatePromptsTabCounter() { updateResourceTabCounter('prompts', 'Prompts'); }

export function updateToolsTabCounter() { updateResourceTabCounter('tools', 'Tools'); }

export function highlightResource(resourceName, type) {
    if (state.currentlySelectedResource) {
        state.currentlySelectedResource.classList.remove('resource-selected');
    }

    let resourceCategory = null;
    if (state.resourceData[type]) {
        for (const category in state.resourceData[type]) {
            if (state.resourceData[type][category].some(r => r.name === resourceName)) {
                resourceCategory = category;
                break;
            }
        }
    }

    if (resourceCategory) {
        const resourceTab = document.querySelector(`.resource-tab[data-type="${type}"]`);
        if (resourceTab) resourceTab.click();

        const categoryTab = document.querySelector(`.category-tab[data-type="${type}"][data-category="${resourceCategory}"]`);
        if(categoryTab) categoryTab.click();

        const resourceElement = document.getElementById(`resource-${type}-${resourceName}`);
        if (resourceElement) {
            resourceElement.open = true;
            resourceElement.classList.add('resource-selected');
            state.currentlySelectedResource = resourceElement;

            setTimeout(() => {
                resourceElement.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }, 350);
        }
        return true;
    }
    return false;
}

export function addSessionToList(session, isActive = false, isLastChild = false) {
    const sessionItem = document.createElement('div');
    sessionItem.id = `session-${session.id}`;
    sessionItem.dataset.sessionId = session.id;
    sessionItem.dataset.isTemporary = session.is_temporary ? 'true' : 'false';
    sessionItem.dataset.archived = session.archived ? 'true' : 'false';
    sessionItem.className = 'session-item w-full text-left p-3 rounded-lg hover:bg-white/10 transition-colors cursor-pointer';
    
    // Add purple left border for utility sessions
    if (session.is_temporary) {
        sessionItem.style.borderLeft = '3px solid rgba(139, 92, 246, 0.6)';
        sessionItem.style.paddingLeft = '0.625rem'; // Adjust padding to compensate for border
    }

    // Add gray styling for archived sessions
    if (session.archived) {
        sessionItem.style.opacity = '0.5';
        sessionItem.style.borderLeft = '3px solid rgba(156, 163, 175, 0.4)';
        sessionItem.style.paddingLeft = '0.625rem'; // Adjust padding to compensate for border
    }

    // Check for Genie slave session metadata
    const genieMetadata = session.genie_metadata || {};
    const isGenieSlave = genieMetadata.is_genie_slave || false;
    const genieParentSessionId = genieMetadata.parent_session_id;

    // Extract hierarchy metadata (needed for badges even if not slave)
    const nestingLevel = genieMetadata.nesting_level || 0;
    const slaveSequence = genieMetadata.slave_sequence_number || 0;

    // Prefer metadata value over parameter (backend now sets this)
    isLastChild = genieMetadata.is_last_child || isLastChild;

    // Check if this session is a genie master (has slave sessions pointing to it)
    // This is set by markGenieMasterSessions() after all sessions are loaded
    const isGenieMaster = session._isGenieMaster || false;

    // Simplified: Use wrapper div for indentation (guaranteed to work with any CSS)
    // Wrapper approach bypasses overflow:hidden and Tailwind conflicts
    if (isGenieSlave) {
        sessionItem.classList.add('genie-slave-session');
        sessionItem.dataset.genieParentId = genieParentSessionId || '';

        // Create wrapper div using data-level attribute (CSS handles spacing and colors)
        const wrapper = document.createElement('div');
        wrapper.className = 'genie-wrapper';
        wrapper.dataset.level = nestingLevel;              // Used by CSS for indentation and color
        wrapper.dataset.sessionId = session.id;            // For path highlighting
        wrapper.dataset.parentId = genieParentSessionId;   // For lineage traversal
        wrapper.dataset.sequence = slaveSequence;          // For stagger animation

        // Add last-child class for connector styling (└ vs ├)
        if (isLastChild) {
            wrapper.classList.add('last-child');
        }

        // Check if slaves should be hidden (parent is collapsed)
        const collapseState = getGenieCollapseState();
        if (collapseState[genieParentSessionId]) {
            sessionItem.classList.add('genie-slave-hidden');
        }

        // Store original sessionItem for later unwrapping if needed
        wrapper._originalSession = sessionItem;
        sessionItem._wrapper = wrapper;
    }

    // Mark genie master sessions for later identification
    if (isGenieMaster) {
        sessionItem.classList.add('genie-master-session');
        sessionItem.dataset.isGenieMaster = 'true';
    }

    if (isActive) {
        document.querySelectorAll('.session-item').forEach(item => item.classList.remove('active'));
        sessionItem.classList.add('active');
    }

    const contentWrapper = document.createElement('div');
    contentWrapper.className = 'w-full flex flex-col';

    const topRow = document.createElement('div');
    topRow.className = 'flex justify-between items-center';

    const nameContainer = document.createElement('div');
    nameContainer.className = 'flex-1 min-w-0 flex items-center gap-2';
    
    const nameSpan = document.createElement('span');
    nameSpan.className = 'session-name-span font-semibold text-sm text-white truncate block';
    nameSpan.textContent = session.name;
    nameSpan.dataset.fullName = session.name;  // For tooltip on truncation

    // Add text level badge for child sessions (industrial blueprint style)
    if (isGenieSlave) {
        const levelBadge = document.createElement('span');
        levelBadge.className = `level-badge level-badge-l${Math.min(nestingLevel, 3)}`;
        levelBadge.textContent = `L${nestingLevel}`;
        levelBadge.title = `Level ${nestingLevel} child session`;

        // Insert badge before name span
        nameContainer.appendChild(levelBadge);
    }

    nameContainer.appendChild(nameSpan);
    
    // Add utility indicator badge for temporary sessions
    if (session.is_temporary) {
        const utilityBadge = document.createElement('span');
        utilityBadge.className = 'inline-flex items-center gap-1 mt-0.5 text-xs text-indigo-400';
        utilityBadge.innerHTML = `
            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
            <span class="truncate">${session.temporary_purpose || 'utility'}</span>
        `;
        utilityBadge.title = session.temporary_purpose || 'Utility session';
        nameContainer.appendChild(utilityBadge);
    }

    // REMOVED: Genie child indicator badge - using CSS-only tree visualization for minimal screen estate
    // CSS classes (genie-child-l0/l1/l2) are still applied above for tree connector styling
    // Tree structure rendered via CSS ::before and ::after pseudo-elements in main.css

    // Add Genie master indicator badge - L0 (consistent with child badges L1/L2/L3)
    if (isGenieMaster) {
        // Check current collapse state
        const collapseState = getGenieCollapseState();
        const isCollapsed = collapseState[session.id] || false;

        // Create level badge matching child style
        const levelBadge = document.createElement('span');
        levelBadge.className = 'level-badge level-badge-l0';
        levelBadge.textContent = 'L0';
        levelBadge.title = 'Level 0 parent session';

        // Container for badge + collapse toggle
        const genieMasterBadge = document.createElement('span');
        genieMasterBadge.className = 'genie-master-badge inline-flex items-center gap-2 mt-1 cursor-pointer';
        genieMasterBadge.appendChild(levelBadge);

        // Collapse toggle with smooth animation
        const toggle = document.createElement('span');
        toggle.className = 'genie-collapse-toggle';
        toggle.style.cssText = `
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 18px;
            height: 18px;
            color: #F15F22;
            background: rgba(241, 95, 34, 0.15);
            border-radius: 4px;
            border: 1px solid rgba(241, 95, 34, 0.3);
            transition: all 0.2s ease;
            font-size: 10px;
            transform: ${isCollapsed ? 'rotate(-90deg)' : 'rotate(0deg)'};
        `;
        toggle.textContent = '▼';

        toggle.addEventListener('mouseenter', () => {
            toggle.style.background = 'rgba(241, 95, 34, 0.3)';
            toggle.style.borderColor = '#F15F22';
        });
        toggle.addEventListener('mouseleave', () => {
            toggle.style.background = 'rgba(241, 95, 34, 0.15)';
            toggle.style.borderColor = 'rgba(241, 95, 34, 0.3)';
        });

        genieMasterBadge.appendChild(toggle);

        genieMasterBadge.title = 'Click to collapse/expand child sessions';

        // Add click handler to toggle child sessions visibility with animation
        genieMasterBadge.addEventListener('click', (e) => {
            e.stopPropagation();
            toggleGenieSlaveVisibility(session.id);

            // Animate the toggle icon
            const newState = !isCollapsed;
            toggle.style.transform = newState ? 'rotate(-90deg)' : 'rotate(0deg)';
        });

        nameContainer.appendChild(genieMasterBadge);
    }

    topRow.appendChild(nameContainer);
    contentWrapper.appendChild(topRow);

    const lastUpdatedSpan = document.createElement('span');
    lastUpdatedSpan.className = 'session-last-updated text-xs text-gray-300 mt-1';
    if (session.last_updated && session.last_updated !== "Unknown") {
        lastUpdatedSpan.textContent = `Updated: ${new Date(session.last_updated).toLocaleString()}`;
    } else {
        lastUpdatedSpan.textContent = 'Updated: Unknown';
    }
    contentWrapper.appendChild(lastUpdatedSpan);

    // Display profile tags (preferred) or fall back to models_used
    const tagsDiv = document.createElement('div');
    tagsDiv.className = 'session-models text-xs text-gray-400 mt-1 flex flex-wrap gap-1';

    // Render profile tag badges with proper colors
    if (session.profile_tags_used && Array.isArray(session.profile_tags_used) && session.profile_tags_used.length > 0) {
        // Use badge components with profile type colors
        const badgesHtml = session.profile_tags_used.map(tag => {
            return renderProfileTag(tag, session.profile_type);
        }).join('');
        tagsDiv.innerHTML = badgesHtml;
    } else if (session.models_used && Array.isArray(session.models_used) && session.models_used.length > 0) {
        // Fallback to models_used for backwards compatibility (plain text)
        const textSpan = document.createElement('span');
        textSpan.className = 'text-xs font-mono text-muted';
        textSpan.textContent = session.models_used.join(' · ');
        tagsDiv.appendChild(textSpan);
    }
    contentWrapper.appendChild(tagsDiv);

    sessionItem.appendChild(contentWrapper);

    // Create action buttons toolbar (positioned absolutely at bottom right)
    const actionsDiv = document.createElement('div');
    actionsDiv.className = 'session-actions';
    actionsDiv.style.position = 'absolute';
    actionsDiv.style.bottom = '0.5rem';
    actionsDiv.style.right = '0.5rem';
    actionsDiv.style.display = 'flex';
    actionsDiv.style.gap = '0.5rem';
    actionsDiv.style.alignItems = 'center';
    actionsDiv.style.zIndex = '10';

    const editButton = document.createElement('button');
    editButton.type = 'button';
    editButton.className = 'session-action-button session-edit-button';
    editButton.title = 'Rename session';
    editButton.dataset.action = 'edit';
    editButton.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" viewBox="0 0 20 20" fill="currentColor"><path d="M13.586 3.586a2 2 0 112.828 2.828l-11.202 11.202a.5.5 0 01-.293.146H3.5a.5.5 0 01-.5-.5v-1.414a.5.5 0 01.146-.293l11.202-11.202zM15.707 2.293a1 1 0 010 1.414L5.414 14H4v-1.414L14.293 2.293a1 1 0 011.414 0z" /></svg>`;
    actionsDiv.appendChild(editButton);

    const deleteButton = document.createElement('button');
    deleteButton.type = 'button';
    deleteButton.className = 'session-action-button session-delete-button';
    deleteButton.title = 'Delete session';
    deleteButton.dataset.action = 'delete';
    deleteButton.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M6 2a2 2 0 00-2 2v12a2 2 0 002 2h8a2 2 0 002-2V4a2 2 0 00-2-2H6zm2 3a1 1 0 11-2 0 1 1 0 012 0zm4 0a1 1 0 11-2 0 1 1 0 012 0z" clip-rule="evenodd" /></svg>`;
    actionsDiv.appendChild(deleteButton);

    const copyButton = document.createElement('button');
    copyButton.type = 'button';
    copyButton.className = 'session-action-button session-copy-button';
    copyButton.title = 'Copy session ID';
    copyButton.dataset.action = 'copy';
    copyButton.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>`;
    actionsDiv.appendChild(copyButton);

    const analyticsButton = document.createElement('button');
    analyticsButton.type = 'button';
    analyticsButton.className = 'session-action-button session-analytics-button';
    analyticsButton.title = 'Context Window Analytics';
    analyticsButton.dataset.action = 'analytics';
    analyticsButton.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" /></svg>`;
    actionsDiv.appendChild(analyticsButton);

    sessionItem.appendChild(actionsDiv);

    // If this is a child session with a wrapper, append sessionItem to wrapper and return wrapper
    if (sessionItem._wrapper) {
        const wrapper = sessionItem._wrapper;
        wrapper.appendChild(sessionItem);
        return wrapper;
    }

    return sessionItem;
}

/**
 * Updates the displayed name of a session item in the history list.
 * @param {string} sessionId - The ID of the session to update.
 * @param {string} newName - The new name for the session.
 */
export function updateSessionListItemName(sessionId, newName) {
    const sessionItem = document.getElementById(`session-${sessionId}`);
    if (sessionItem) {
        const nameSpan = sessionItem.querySelector('.session-name-span');
        if (nameSpan) {
            nameSpan.textContent = newName;
        } else {
        }
    } else {
    }
}

export function updateSessionModels(sessionId, models_used, profile_tags_used = null) {
    const sessionItem = document.getElementById(`session-${sessionId}`);
    if (sessionItem) {
        let tagsDiv = sessionItem.querySelector('.session-models');
        if (!tagsDiv) {
            tagsDiv = document.createElement('div');
            tagsDiv.className = 'session-models text-xs text-gray-400 mt-1 flex flex-wrap gap-1';
            const contentWrapper = sessionItem.querySelector('.w-full');
            if(contentWrapper) {
                contentWrapper.appendChild(tagsDiv);
            }
        }
        tagsDiv.innerHTML = ''; // Clear existing tags/models

        // Render profile tag badges with proper colors (matching addSessionToList)
        if (profile_tags_used && Array.isArray(profile_tags_used) && profile_tags_used.length > 0) {
            const badgesHtml = profile_tags_used.map(tag => {
                return renderProfileTag(tag);
            }).join('');
            tagsDiv.innerHTML = badgesHtml;
        } else if (models_used && Array.isArray(models_used) && models_used.length > 0) {
            // Fallback to models_used for backwards compatibility (plain text)
            const textSpan = document.createElement('span');
            textSpan.className = 'text-xs font-mono text-muted';
            textSpan.textContent = models_used.join(' · ');
            tagsDiv.appendChild(textSpan);
        }

        if (state.currentSessionId === sessionId) {
            document.querySelectorAll('.session-item').forEach(item => item.classList.remove('active'));
            sessionItem.classList.add('active');
        }
    }
}

// --- NEW: Update the active session title in the header ---
export function updateActiveSessionTitle(newName) {
    const titleEl = document.getElementById('active-session-title');
    const wrapperEl = document.getElementById('active-session-title-wrapper');
    
    if (titleEl) {
        titleEl.textContent = newName || 'Unnamed Session';
    }
    
    // Show the wrapper when we have a session loaded (after configuration)
    if (wrapperEl) {
        if (newName) {
            wrapperEl.classList.remove('hidden');
        } else {
            wrapperEl.classList.add('hidden');
        }
    }
}

// --- NEW: Enter edit mode for active session title ---
export function enterActiveSessionTitleEdit() {
    const titleEl = document.getElementById('active-session-title');
    if (!titleEl) return;
    // Prevent multiple edit inputs
    if (document.getElementById('active-session-title-input')) return;

    const currentName = titleEl.textContent.trim();
    const input = document.createElement('input');
    input.type = 'text';
    input.id = 'active-session-title-input';
    input.className = 'bg-gray-700 text-white px-2 py-1 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-teradata-orange w-64';
    input.value = currentName;
    input.dataset.originalName = currentName;

    // Replace span with input
    titleEl.replaceWith(input);
    if (DOM.activeSessionTitleEditingHint) {
        DOM.activeSessionTitleEditingHint.classList.remove('hidden');
    }
    input.focus();

    // Only allow one handler to run (blur or keydown)
    let finished = false;
    function finishEdit(action, value) {
        if (finished) return;
        finished = true;
        if (action === 'save') {
            saveActiveSessionTitleEdit(value);
        } else {
            cancelActiveSessionTitleEdit();
        }
    }

    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            const newName = input.value.trim();
            if (!newName) {
                finishEdit('cancel');
                return;
            }
            finishEdit('save', newName);
        } else if (e.key === 'Escape') {
            finishEdit('cancel');
        }
    });

    input.addEventListener('blur', () => {
        const newName = input.value.trim();
        if (newName && newName !== input.dataset.originalName) {
            finishEdit('save', newName);
        } else {
            finishEdit('cancel');
        }
    });
}

// --- NEW: Cancel editing of active session title ---
export function cancelActiveSessionTitleEdit() {
    const input = document.getElementById('active-session-title-input');
    if (!input || !input.parentNode) return;
    const originalName = input.dataset.originalName || 'Unnamed Session';
    const span = document.createElement('span');
    span.id = 'active-session-title';
    span.className = 'text-lg font-semibold text-white cursor-pointer';
    span.textContent = originalName;
    input.replaceWith(span);
    if (DOM.activeSessionTitleEditingHint) {
        DOM.activeSessionTitleEditingHint.classList.add('hidden');
    }
}

// --- NEW: Save editing of active session title ---
export function saveActiveSessionTitleEdit(newName) {
    const input = document.getElementById('active-session-title-input');
    if (!input || !input.parentNode) return;
    const span = document.createElement('span');
    span.id = 'active-session-title';
    span.className = 'text-lg font-semibold text-white cursor-pointer';
    span.textContent = newName;
    input.replaceWith(span);
    if (DOM.activeSessionTitleEditingHint) {
        DOM.activeSessionTitleEditingHint.classList.add('hidden');
    }
    // Fire custom event to trigger rename logic elsewhere (decouple UI and logic)
    UderiaBus.emit('uderia:session-title-renamed', { newName });
}

export function updateSessionTimestamp(sessionId, last_updated) {
    const sessionItem = document.getElementById(`session-${sessionId}`);
    if (sessionItem) {
        let lastUpdatedSpan = sessionItem.querySelector('.session-last-updated');
        if (!lastUpdatedSpan) {
            lastUpdatedSpan = document.createElement('span');
            lastUpdatedSpan.className = 'session-last-updated text-xs text-gray-300';
            const infoDiv = sessionItem.querySelector('.flex-col');
            if (infoDiv) {
                const nameSpan = infoDiv.querySelector('.session-name-span');
                if (nameSpan) {
                    nameSpan.after(lastUpdatedSpan);
                } else {
                    infoDiv.prepend(lastUpdatedSpan);
                }
            }
        }
        lastUpdatedSpan.textContent = `Updated: ${new Date(last_updated).toLocaleString()}`;
    }
}

/**
 * Removes a session item from the history list in the DOM.
 * @param {string} sessionId - The ID of the session to remove.
 */
export function removeSessionFromList(sessionId) {
    const sessionItem = document.getElementById(`session-${sessionId}`);
    if (sessionItem) {
        // If wrapped in a genie-wrapper, remove the wrapper instead
        const wrapper = sessionItem.closest('.genie-wrapper');
        if (wrapper && wrapper.dataset.sessionId === sessionId) {
            wrapper.remove();
        } else {
            sessionItem.remove();
        }
    }
}

/**
 * Removes a session and all its descendant child sessions from the DOM.
 * Used when deleting a Genie parent to cascade-remove children.
 * @param {string} sessionId - The parent session ID to remove.
 * @param {string[]} childIds - Array of child session IDs to remove.
 */
export function removeSessionAndDescendantsFromList(sessionId, childIds) {
    // Remove child sessions first
    if (childIds && childIds.length > 0) {
        for (const childId of childIds) {
            removeSessionFromList(childId);
        }
    }
    // Remove the parent session
    removeSessionFromList(sessionId);
}


/**
 * Switches a session list item's name span to an input field for editing.
 * @param {HTMLButtonElement} editButton - The edit button element that was clicked.
 */
export function enterSessionEditMode(editButton) {
    const sessionItem = editButton.closest('.session-item');
    if (!sessionItem || sessionItem.querySelector('.session-edit-input')) {
        return;
    }

    const spanElement = sessionItem.querySelector('.session-name-span');
    const actionsDiv = sessionItem.querySelector('.session-actions');
    if (!spanElement || !actionsDiv) return;

    const currentName = spanElement.textContent;
    const inputElement = document.createElement('input');
    inputElement.type = 'text';
    inputElement.value = currentName;
    inputElement.dataset.originalName = currentName;
    inputElement.className = 'session-edit-input flex-grow min-w-0 w-full p-1 bg-gray-800 border border-gray-600 rounded-md text-sm text-white'; // Added flex-grow and adjusted colors

    spanElement.style.display = 'none';
    actionsDiv.style.display = 'none';

    const topRow = sessionItem.querySelector('.flex.justify-between.items-center');
    if (!topRow) return; // Should not happen if addSessionToList creates it correctly

    // Ensure the topRow is a flex container that allows the input to grow
    topRow.classList.add('flex', 'items-center'); // Ensure flex properties are present
    spanElement.replaceWith(inputElement);

    inputElement.focus();
    inputElement.select();

    inputElement.addEventListener('blur', handleSessionRenameSave);
    inputElement.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            handleSessionRenameSave(e);
        } else if (e.key === 'Escape') {
            handleSessionRenameCancel(e);
        }
    });
}

/**
 * Switches a session list item back from input mode to display mode.
 * @param {HTMLInputElement} inputElement - The input element currently being edited.
 * @param {string} finalName - The name to display in the span (either original or new).
 */
export function exitSessionEditMode(inputElement, finalName) {
    const sessionItem = inputElement.closest('.session-item');
    if (!sessionItem) return;

    // Create a new span element to replace the input field
    const newSpanElement = document.createElement('span');
    newSpanElement.className = 'session-name-span text-white text-sm font-semibold truncate'; // Ensure original classes are applied
    newSpanElement.textContent = finalName;

    // Replace the input element with the new span element
    inputElement.replaceWith(newSpanElement);

    const actionsDiv = sessionItem.querySelector('.session-actions');
    if (actionsDiv) {
        actionsDiv.style.display = ''; // Show actions div again
    }

    inputElement.removeEventListener('blur', handleSessionRenameSave);
}


export function updateConfigButtonState() {
    // This function is obsolete with the new configuration UI
    // The old config modal elements (configActionButton, configActionButtonText, configForm) no longer exist
    // Keeping the function for backwards compatibility but making it a no-op
    return;
}

export function showConfirmation(title, body, onConfirm) {
    DOM.confirmModalTitle.textContent = title;
    DOM.confirmModalBody.innerHTML = body;

    DOM.confirmModalOverlay.classList.remove('hidden', 'opacity-0');
    DOM.confirmModalContent.classList.remove('scale-95', 'opacity-0');

    // If no confirm callback, this is an alert - hide Confirm button and change Cancel to "OK"
    const isAlertMode = !onConfirm;
    if (isAlertMode) {
        DOM.confirmModalConfirm.classList.add('hidden');
        DOM.confirmModalCancel.textContent = 'OK';
    } else {
        DOM.confirmModalConfirm.classList.remove('hidden');
        DOM.confirmModalCancel.textContent = 'Cancel';
    }

    const confirmHandler = () => {
        if (onConfirm) onConfirm();
        closeConfirmation();
    };

    const cancelHandler = () => {
        closeConfirmation();
    };

    const closeConfirmation = () => {
        DOM.confirmModalOverlay.classList.add('opacity-0');
        DOM.confirmModalContent.classList.add('scale-95', 'opacity-0');
        setTimeout(() => DOM.confirmModalOverlay.classList.add('hidden'), 300);
        DOM.confirmModalConfirm.removeEventListener('click', confirmHandler);
        DOM.confirmModalCancel.removeEventListener('click', cancelHandler);
        // Reset button states for next use
        DOM.confirmModalConfirm.classList.remove('hidden');
        DOM.confirmModalCancel.textContent = 'Cancel';
    };

    DOM.confirmModalConfirm.addEventListener('click', confirmHandler, { once: true });
    DOM.confirmModalCancel.addEventListener('click', cancelHandler, { once: true });
}

export function updatePromptEditorState() {
    const hasChanged = DOM.promptEditorTextarea.value.trim() !== DOM.promptEditorTextarea.dataset.initialValue.trim();
    DOM.promptEditorSave.disabled = !hasChanged;

    let statusText = isPromptCustomForModel(state.currentProvider, state.currentModel) ? 'Custom' : 'Default';
    let statusClass = 'text-sm text-gray-400';

    if (hasChanged) {
        statusText = 'Unsaved Changes';
        statusClass = 'text-sm text-yellow-400';
    }

    DOM.promptEditorStatus.textContent = statusText;
    DOM.promptEditorStatus.className = statusClass;
}

export function addMessageToModal(role, content) {
    const messageEl = document.createElement('div');
    messageEl.className = `p-3 rounded-lg max-w-xs ${role === 'user' ? 'bg-blue-600 self-end' : 'bg-gray-600 self-start'}`;
    messageEl.textContent = content;
    DOM.chatModalLog.appendChild(messageEl);
    DOM.chatModalLog.scrollTop = DOM.chatModalLog.scrollHeight;
}

export function updateHintAndIndicatorState() {
    // The context mode is a chip on the composer (Oct 2026): its words, its tooltip and its
    // colour (data-mode) say which history the next turn reads. The status dot keeps its class.
    const chip = DOM.inputHint;
    const label = chip ? chip.querySelector('.composer-ctx-label') : null;
    let mode, text, tip;
    if (state.isLastTurnModeLocked) {
        mode = 'locked'; text = 'Turn summaries · locked';
        tip = "'Turn summaries' is locked on: the next turn reads only the workflow summaries. Click, or press Shift + Alt, to return to full session context.";
        DOM.contextStatusDot.className = 'connection-dot context-last-turn-locked';
        DOM.sendIcon.classList.remove('flipped');
    } else if (state.isTempLastTurnMode) {
        mode = 'temp'; text = 'Turn summaries';
        tip = "Using 'Turn summaries' for this query only (while Alt is held).";
        DOM.contextStatusDot.className = 'connection-dot context-last-turn-temp';
        DOM.sendIcon.classList.remove('flipped');
    } else {
        mode = 'full'; text = 'Full context';
        tip = "The next turn reads the full session history. Hold Alt to use 'Turn summaries' for one query; click, or press Shift + Alt, to lock it on.";
        DOM.contextStatusDot.className = 'connection-dot idle';
        DOM.sendIcon.classList.add('flipped');
    }
    if (chip) {
        chip.dataset.mode = mode;
        chip.setAttribute('data-tip', tip);
        chip.setAttribute('aria-label', `Context: ${text}. ${tip}`);
        chip.setAttribute('aria-pressed', String(mode === 'locked'));
    }
    if (label) label.textContent = text;
}

export function updateVoiceModeUI() {
    const isActive = state.isVoiceModeLocked || state.isTempVoiceMode || state.ttsState === 'AWAITING_OBSERVATION_CONFIRMATION';

    DOM.voiceInputButton.classList.toggle('is-active', !!isActive);
    DOM.voiceInputButton.setAttribute('aria-pressed', String(!!isActive));

    let tooltipText = "Voice Conversation";
    if (state.ttsState === 'AWAITING_OBSERVATION_CONFIRMATION') {
        tooltipText += " (Listening for confirmation)";
    } else if (state.isVoiceModeLocked) {
        tooltipText += " (Locked On)";
    } else if (state.isTempVoiceMode) {
        tooltipText += " (Active)";
    } else {
        tooltipText += " (Hold Ctrl)";
    }
    DOM.voiceInputButton.title = tooltipText;

    if (isActive) {
    } else {
    }
}

export function updateKeyObservationsModeUI() {
    DOM.observationsAutoplayOffIcon.classList.add('hidden');
    DOM.observationsAutoplayOnIcon.classList.add('hidden');
    DOM.observationsOffIcon.classList.add('hidden');
    DOM.keyObservationsToggleButton.classList.remove('autoplay-on', 'off');

    switch (state.keyObservationsMode) {
        case 'autoplay-on':
            DOM.observationsAutoplayOnIcon.classList.remove('hidden');
            DOM.keyObservationsToggleButton.title = 'Key Observations - Autoplay On';
            DOM.keyObservationsToggleButton.classList.add('autoplay-on');
            break;
        case 'off':
            DOM.observationsOffIcon.classList.remove('hidden');
            DOM.keyObservationsToggleButton.title = 'Key Observations - Off';
            DOM.keyObservationsToggleButton.classList.add('off');
            break;
        case 'autoplay-off':
        default:
            DOM.observationsAutoplayOffIcon.classList.remove('hidden');
            DOM.keyObservationsToggleButton.title = 'Key Observations - Autoplay Off';
            break;
    }
}

// export function closeConfigModal() {
//     DOM.configModalOverlay.classList.add('opacity-0');
//     DOM.configModalContent.classList.add('scale-95', 'opacity-0');
//     setTimeout(() => DOM.configModalOverlay.classList.add('hidden'), 300);
// }

export function closePromptModal() {
    DOM.promptModalOverlay.classList.add('opacity-0');
    DOM.promptModalContent.classList.add('scale-95', 'opacity-0');
    setTimeout(() => DOM.promptModalOverlay.classList.add('hidden'), 300);
}

export function closeViewPromptModal() {
    DOM.viewPromptModalOverlay.classList.add('opacity-0');
    DOM.viewPromptModalContent.classList.add('scale-95', 'opacity-0');
    setTimeout(() => DOM.viewPromptModalOverlay.classList.add('hidden'), 300);
}

/**
 * Displays the RAG Case modal with the provided case data.
 * @param {object} caseData - The full RAG case data to display.
 */
export function showRagCaseModal(caseData) {
    if (!DOM.ragCaseModalOverlay || !DOM.ragCaseModalContent || !DOM.ragCaseIdDisplay || !DOM.ragCaseModalJson) {
        console.error("RAG Case modal DOM elements not found.");
        return;
    }

    DOM.ragCaseIdDisplay.textContent = caseData.case_id || 'N/A';
    DOM.ragCaseModalJson.textContent = JSON.stringify(caseData.full_case_data, null, 2);

    DOM.ragCaseModalOverlay.classList.remove('hidden');
    // Trigger reflow to ensure transition plays
    void DOM.ragCaseModalOverlay.offsetWidth;
    DOM.ragCaseModalOverlay.classList.remove('opacity-0');
    DOM.ragCaseModalContent.classList.remove('scale-95', 'opacity-0');
}

/**
 * Hides the RAG Case modal.
 */
export function closeRagCaseModal() {
    if (!DOM.ragCaseModalOverlay || !DOM.ragCaseModalContent) {
        console.error("RAG Case modal DOM elements not found.");
        return;
    }

    DOM.ragCaseModalOverlay.classList.add('opacity-0');
    DOM.ragCaseModalContent.classList.add('scale-95', 'opacity-0');
    setTimeout(() => DOM.ragCaseModalOverlay.classList.add('hidden'), 300);
}

/**
 * Provides brief visual feedback on the context status dot by making it blink.
 */
export function blinkContextDot() {
    if (!DOM.contextStatusDot) return;

    DOM.contextStatusDot.classList.add('blinking-white');

    setTimeout(() => {
        DOM.contextStatusDot.classList.remove('blinking-white');
    }, 1500); // 0.5s animation * 3 iterations
}

/**
 * Pulses the CTX dot busy (amber + pulsing) while RAG-offload context retrieval is active,
 * then restores the prior state (idle / locked / temp).
 */
export function pulseContextDotRagBusy() {
    if (!DOM.contextStatusDot) return;

    const dot = DOM.contextStatusDot;
    const restoreClass = dot.classList.contains('context-last-turn-locked') ? 'context-last-turn-locked'
        : dot.classList.contains('context-last-turn-temp') ? 'context-last-turn-temp'
        : 'idle';

    dot.classList.remove('idle', 'context-last-turn-locked', 'context-last-turn-temp', 'blinking-white');
    dot.classList.add('busy', 'pulsing');

    setTimeout(() => {
        dot.classList.remove('busy', 'pulsing');
        dot.classList.add(restoreClass);
    }, 1500);
}

/**
 * Provides brief visual feedback on the CCR (Champion Case Retrieval) status dot by making it blink yellow.
 */
export function blinkCcrDot() {
    if (!DOM.ccrStatusDot) return;

    // Ensure it's in the 'connected' state before blinking
    if (!DOM.ccrStatusDot.classList.contains('connected')) {
        DOM.ccrStatusDot.classList.add('connected');
    }

    DOM.ccrStatusDot.classList.add('blinking-yellow');

    // The animation runs for 1.5s (0.5s * 3 iterations)
    setTimeout(() => {
        DOM.ccrStatusDot.classList.remove('blinking-yellow');
    }, 1500);
}

/**
 * Provides brief visual feedback on the Knowledge status dot by making it blink yellow.
 */
export function blinkKnowledgeDot() {
    const knowledgeDot = document.getElementById('knowledge-status-dot');
    if (!knowledgeDot) return;

    // Ensure it's in the 'knowledge-configured' state before blinking
    if (!knowledgeDot.classList.contains('knowledge-configured')) {
        knowledgeDot.classList.remove('knowledge-idle', 'knowledge-active');
        knowledgeDot.classList.add('knowledge-configured');
    }

    knowledgeDot.classList.add('blinking-yellow');

    // The animation runs for 1.5s (0.5s * 3 iterations)
    setTimeout(() => {
        knowledgeDot.classList.remove('blinking-yellow');
    }, 1500);
}

/**
 * Updates knowledge indicator and banner when knowledge repositories are accessed.
 * @param {Array<string>} collections - List of collection names accessed
 * @param {number} documentCount - Number of documents retrieved
 */
export function updateKnowledgeIndicator(collections, documentCount) {
    const knowledgeDot = document.getElementById('knowledge-status-dot');
    const knowledgeBanner = document.getElementById('knowledge-banner');
    const collectionsList = document.getElementById('knowledge-collections-list');

    if (!knowledgeDot || !knowledgeBanner || !collectionsList) {
        console.warn('[Knowledge] UI elements not found');
        return;
    }

    console.log('[Knowledge] Updating indicator with:', { collections, documentCount });

    // Show banner with collection names
    if (collections && collections.length > 0) {
        collectionsList.textContent = collections.join(', ') + ` (${documentCount} docs)`;
        knowledgeBanner.classList.remove('hidden');
    }

    // Reset indicator to configured state (green) after successful retrieval
    knowledgeDot.classList.remove('knowledge-idle', 'knowledge-active');
    knowledgeDot.classList.add('knowledge-configured');

    // Keep banner visible (removed auto-hide to maintain visibility throughout session)
}

export function closeChatModal() {
    DOM.chatModalOverlay.classList.add('opacity-0');
    DOM.chatModalContent.classList.add('scale-95', 'opacity-0');
    setTimeout(() => DOM.chatModalOverlay.classList.add('hidden'), 300);
}

/**
 * Removes the visual highlight from a session item.
 * @param {string} sessionId The ID of the session to un-highlight.
 */
export function removeHighlight(sessionId) {
    const sessionItem = document.getElementById(`session-${sessionId}`);
    if (sessionItem) {
        const nameSpan = sessionItem.querySelector('.session-name-span');
        if (nameSpan) {
            nameSpan.classList.remove('font-bold', 'text-teradata-orange');
        }
        const indicator = sessionItem.querySelector('.new-activity-indicator');
        if (indicator) {
            indicator.remove();
        }
    }
}

/**
 * Toggles the collapsed state of the main side navigation bar.
 */
export function toggleSideNav() {
    // Simply toggle the 'collapsed' class - CSS handles everything
    DOM.appSideNav.classList.toggle('collapsed');
}

/**
 * Handles switching the main application view.
 * @param {string} viewId - The ID of the view to switch to (e.g., 'conversation-view').
 */
/**
 * Return context for cross-view deep links — ONE slot, deliberately not a stack.
 *
 * A purposeful jump (e.g. the SIGNED station sending you to the corpus that blocks
 * sign-off) registers where you came from and how to get back; the destination's exit
 * control consumes it and names it. Deep links here are one hop, and a stack invites
 * ambiguity about when to pop, so depth is added only when a real two-hop case exists.
 *
 * The clear-on-omit rule below is the load-bearing half: a plain sidebar click calls
 * handleViewSwitch() with no options and therefore WIPES any pending context. Without
 * that, a stale return teleports the user somewhere they never asked to go — worse
 * than having no return at all.
 */
const _navReturn = { ctx: null };
export const NavReturn = {
    // { label, restore } — restore() re-applies sub-state (section, focused agent),
    // because landing on the right view is not the same as landing where you were.
    set(ctx) { _navReturn.ctx = (ctx && ctx.restore) ? ctx : null; },
    peek() { return _navReturn.ctx; },
    clear() { _navReturn.ctx = null; },
    consume() { const c = _navReturn.ctx; _navReturn.ctx = null; return c; },
};
if (typeof window !== 'undefined') window.NavReturn = NavReturn;

export function handleViewSwitch(viewId, opts) {
    // Store on a purposeful jump; clear on every ordinary navigation.
    NavReturn.set(opts && opts.returnTo);
    console.log('[handleViewSwitch] Switching to view:', viewId);
    
    // CRITICAL: Check if user is navigating away from configuration with unsaved changes
    const currentView = document.querySelector('.app-view.active');
    const isLeavingConfiguration = currentView?.id === 'credentials-view' && viewId !== 'credentials-view';
    
    if (isLeavingConfiguration) {
        // Import dirty state tracker dynamically to avoid circular dependencies
        import('./configDirtyState.js?v=1.189').then(({ canNavigateAway }) => {
            if (!canNavigateAway(viewId)) {
                console.log('[handleViewSwitch] ❌ Navigation blocked - unsaved configuration changes');
                return; // Block navigation
            }
            
            // Navigation allowed, proceed
            performViewSwitch(viewId);
        });
        return; // Exit early, will be called again if navigation is allowed
    }
    
    // Not leaving configuration or no unsaved changes - proceed normally
    performViewSwitch(viewId);
}

/**
 * Internal function to perform the actual view switch
 * Separated to allow dirty state checking to interrupt the flow
 */
function performViewSwitch(viewId) {
    // 1. Log all app views found
    const appViews = document.querySelectorAll('.app-view');

    // 2. Hide all views
    appViews.forEach(view => {
        view.classList.remove('active');
    });

    // 3. Show the selected view
    const viewToShow = document.getElementById(viewId);
    if (viewToShow) {
        viewToShow.classList.add('active');
    } else {
        console.error(`[UI DEBUG] View with ID '${viewId}' not found!`);
    }
    
    // 4a. Lazy-load components when entering Platform Components view
    if (viewId === 'components-view') {
        import('./handlers/componentHandler.js?v=1.189').then(({ loadComponentsView }) => {
            loadComponentsView();
        });
        // If the MCP Servers sub-tab is active, also load it
        const activePlatformTab = document.querySelector('.platform-component-tab.active');
        if (activePlatformTab && activePlatformTab.dataset.tab === 'mcp-servers') {
            if (typeof window.loadPlatformConnectorPanel === 'function') {
                window.loadPlatformConnectorPanel();
            }
        }
    }

    // 4. Initialize dirty tracking when entering configuration view
    if (viewId === 'credentials-view') {
        import('./configDirtyState.js?v=1.189').then(({ initializeConfigDirtyTracking }) => {
            initializeConfigDirtyTracking();
        });
        // The Anatomy tab is the view's landing pane — build it on entry (idempotent;
        // it self-guards when another tab was left active). The composition KPIs sit
        // ABOVE the tab bar and fill on every entry regardless of the active tab.
        const anatomyPane = document.getElementById('anatomy-tab');
        if (anatomyPane && !anatomyPane.classList.contains('hidden')
            && window.AgentAnatomy && window.AgentAnatomy.init) {
            window.AgentAnatomy.init();
        } else if (window.AgentAnatomy && window.AgentAnatomy.refreshKpis) {
            window.AgentAnatomy.refreshKpis();
        }
    }
    
    // 5. Reset dirty tracking when leaving configuration view (after successful save)
    const previousView = document.querySelector('.app-view.active');
    if (previousView?.id === 'credentials-view' && viewId !== 'credentials-view') {
        import('./configDirtyState.js?v=1.189').then(({ resetConfigDirtyTracking }) => {
            resetConfigDirtyTracking();
        });
    }

    // 4. Update the active state of the menu buttons
    if (DOM.viewSwitchButtons) {
        DOM.viewSwitchButtons.forEach(button => {
            const isActive = button.dataset.view === viewId;
            button.classList.toggle('active', isActive);
        });
    } else {
    }
    
    // 4b. Ensure Planner Repository navigation state is maintained
    if (typeof window.updatePlannerRepositoryNavigation === 'function') {
        console.log('[handleViewSwitch] Calling updatePlannerRepositoryNavigation after view switch to:', viewId);
        window.updatePlannerRepositoryNavigation();
    } else {
        console.warn('[handleViewSwitch] updatePlannerRepositoryNavigation function not available');
    }

    // 5. Final check
    setTimeout(() => {
        const activeViews = document.querySelectorAll('.app-view.active');
        if (activeViews.length > 1) {
        } else if (activeViews.length === 0) {
        } else if (activeViews[0].id !== viewId) {
            console.error(`[UI DEBUG] WRONG VIEW IS ACTIVE! Expected ${viewId}, but found ${activeViews[0].id}`);
        }
    }, 100); // Use a small delay to catch any subsequent changes

    // 6. If switching to Intelligence, load collections and calculate KPIs
    if (viewId === 'rag-maintenance-view') {
        loadRagCollections();
        // Calculate and display RAG impact KPIs
        if (window.ragCollectionManagement && window.ragCollectionManagement.calculateRagImpactKPIs) {
            window.ragCollectionManagement.calculateRagImpactKPIs();
            
            // Start auto-refresh for Intelligence KPIs
            if (window.ragCollectionManagement.startAutoRefresh) {
                window.ragCollectionManagement.startAutoRefresh();
            }
        }
    } else {
        // Stop Intelligence auto-refresh when leaving the view
        if (window.ragCollectionManagement && window.ragCollectionManagement.stopAutoRefresh) {
            window.ragCollectionManagement.stopAutoRefresh();
        }
    }
    
    // 6b. If switching to Marketplace, refresh marketplace collections
    if (viewId === 'rag-marketplace-view') {
        if (window.refreshMarketplace) {
            window.refreshMarketplace();
        }
    }
    
    // 6c. If switching to Agents view, load the agent-performance lens
    if (viewId === 'agents-view') {
        if (window.agentPerformance && window.agentPerformance.load) {
            window.agentPerformance.load();
        }
    }

    // 6d. If switching to Ontology view, build + render the user's segment
    if (viewId === 'ontology-view') {
        import('./handlers/ontologyPanelHandler.js?v=1.190').then(({ loadOntologyView }) => {
            loadOntologyView();
        }).catch(err => console.error('[Ontology] failed to load handler', err));
    }

    // 7. If switching to Executions view, initialize dashboard
    if (viewId === 'executions-view') {
        if (window.executionDashboard) {
            // Initialize once, then always refresh when entering the view
            if (!window.executionDashboard._isInitialized) {
                window.executionDashboard.initialize();
                window.executionDashboard._isInitialized = true;
            } else {
                // Refresh data and restart auto-refresh when re-entering the view
                window.executionDashboard.refreshDashboard();
                window.executionDashboard.startAutoRefresh();
            }
        } else {
            console.error('[UI DEBUG] ExecutionDashboard not initialized');
        }
    } else {
        // Stop Executions auto-refresh when leaving the view
        if (window.executionDashboard && window.executionDashboard.stopAutoRefresh) {
            window.executionDashboard.stopAutoRefresh();
        }
    }
    
    // 8. If switching to Admin view, load users and features
    if (viewId === 'admin-view') {
        if (window.AdminManager) {
            window.AdminManager.loadUsers();
            window.AdminManager.loadFeatures();
        } else {
            console.error('[UI DEBUG] AdminManager not initialized');
        }
    }
    
    // 9. If switching to Conversation view, initialize panels based on admin settings
    if (viewId === 'conversation-view') {
        console.log('[handleViewSwitch] Conversation view detected, checking for initializePanels:', typeof window.initializePanels);
        if (window.initializePanels) {
            console.log('[handleViewSwitch] Calling initializePanels()');
            window.initializePanels();
        } else {
            console.error('[UI DEBUG] initializePanels not available yet - will retry');
            // Retry after a short delay to allow main.js to finish loading
            setTimeout(() => {
                if (window.initializePanels) {
                    console.log('[handleViewSwitch] Calling initializePanels() after delay');
                    window.initializePanels();
                } else {
                    console.error('[UI DEBUG] initializePanels still not available after delay');
                }
            }, 500);
        }
        
        // Check if application is initialized before loading sessions
        // SKIP this check if we're already in the middle of initialization (reconnectAndLoad handles it)
        if (state.sessionLoaded) {
            console.log('[handleViewSwitch] Session already loaded, skipping initialization check');
            return;
        }
        
        checkServerStatus().then(async (status) => {
            if (!status.isConfigured) {
                // Application not configured - show welcome screen (unless a split panel is occupying the view)
                const _flowPanelOpen = document.getElementById('flow-split-panel')?.classList.contains('flow-split--open');
                if (!_flowPanelOpen) {
                    console.log('[handleViewSwitch] Application not configured, showing welcome screen');
                    if (window.showWelcomeScreen) {
                        window.showWelcomeScreen();
                    }
                }
                return;
            }
            
            // Application is configured - but check if conversation mode has been initialized
            // Import conversationInitializer to check initialization state
            const { getInitializationState } = await import('./conversationInitializer.js?v=1.190');
            const initState = getInitializationState();
            
            if (!initState.initialized) {
                // Not yet initialized - show welcome screen unless a split panel (e.g. flow canvas) is occupying the view
                const _flowPanelOpen = document.getElementById('flow-split-panel')?.classList.contains('flow-split--open');
                if (!_flowPanelOpen) {
                    console.log('[handleViewSwitch] Application configured but conversation not initialized, showing welcome screen');
                    if (window.showWelcomeScreen) {
                        window.showWelcomeScreen();
                    }
                }
                return;
            }
            
            // Application is configured AND initialized - proceed with session loading if needed
            console.log('[handleViewSwitch] Application configured and initialized, no session loaded, checking for session to load');
            
            // Dynamically import sessionManagement to load session
            const { handleLoadSession } = await import('./handlers/sessionManagement.js?v=3.209');
            try {
                // If we have a session ID in state (restored from localStorage), load it
                if (state.currentSessionId) {
                    console.log('[handleViewSwitch] Session ID exists in state, loading session:', state.currentSessionId);
                    await handleLoadSession(state.currentSessionId);
                } else {
                    // No session ID in state - fetch sessions and load most recent
                    console.log('[handleViewSwitch] No session ID in state, fetching sessions');
                    const result = await loadSessions(0, 0); // Load all sessions (limit=0)
                    const sessions = result.sessions || [];
                    const activeSessions = sessions ? sessions.filter(s => !s.archived) : [];

                    if (activeSessions && activeSessions.length > 0) {
                        // Sessions exist - load the most recent one (first in array)
                        console.log('[handleViewSwitch] Found', activeSessions.length, 'existing sessions, loading most recent');
                        await handleLoadSession(activeSessions[0].id);
                    } else {
                        // No sessions exist - show welcome screen
                        console.log('[handleViewSwitch] No existing sessions, showing welcome screen');
                        if (window.showWelcomeScreen) {
                            window.showWelcomeScreen();
                        }
                    }
                }
            } catch (error) {
                console.error('[handleViewSwitch] Error loading sessions:', error);
                // Fallback to welcome screen on error
                if (window.showWelcomeScreen) {
                    window.showWelcomeScreen();
                }
            }
        }).catch(error => {
            console.error('[handleViewSwitch] Error checking server status:', error);
            // On error, show welcome screen as fallback
            if (window.showWelcomeScreen) {
                window.showWelcomeScreen();
            }
        });
    }
}

/**
 * Fetches ChromaDB collections from the backend and renders them as cards.
 */
export async function loadRagCollections() {
    if (!DOM.ragMaintenanceCollectionsContainer) return;
    try {
        if (DOM.ragMaintenanceEmptyHint) {
            DOM.ragMaintenanceEmptyHint.textContent = 'Loading collections...';
        }
        // Get authentication token
        const token = localStorage.getItem('tda_auth_token');
        const res = await fetch('/api/v1/rag/collections?light=true', {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });
        const data = await res.json();
        let collections = (data && data.collections) ? data.collections : [];

        // Separate collections by repository type, applying active-only filter if toggled
        const plannerFilter = document.getElementById('planner-active-filter')?.checked ?? false;
        const knowledgeFilter = document.getElementById('knowledge-active-filter')?.checked ?? false;
        const plannerCollections = collections.filter(c => c.repository_type === 'planner' && (!plannerFilter || c.enabled));
        const knowledgeCollections = collections.filter(c => c.repository_type === 'knowledge' && (!knowledgeFilter || c.enabled));
        
        // Sort collections: Default Collection first, then by ID
        const sortCollections = (arr) => {
            return arr.sort((a, b) => {
                const aIsDefault = a.name.includes('Default Collection');
                const bIsDefault = b.name.includes('Default Collection');
                if (aIsDefault && !bIsDefault) return -1;
                if (!aIsDefault && bIsDefault) return 1;
                return a.id - b.id;
            });
        };
        
        sortCollections(plannerCollections);
        sortCollections(knowledgeCollections);

        // Update tab counters
        const plannerTabBtn = document.getElementById('planner-repo-tab');
        if (plannerTabBtn) {
            plannerTabBtn.textContent = `Planner Repositories (${plannerCollections.length})`;
        }
        const knowledgeTabBtn = document.getElementById('knowledge-repo-tab');
        if (knowledgeTabBtn) {
            knowledgeTabBtn.textContent = `Knowledge Repositories (${knowledgeCollections.length})`;
        }

        // Update Knowledge Graphs tab counter (load in background — don't block rendering)
        import('./handlers/knowledgeGraphPanelHandler.js?v=1.189').then(mod => {
            if (typeof mod.loadKnowledgeGraphsIntelligenceTab === 'function') {
                // Pass the default grid id so this resets the active surface to the
                // Intelligence tab (in case the KG component tab was opened earlier).
                mod.loadKnowledgeGraphsIntelligenceTab('knowledge-graphs-grid-container');
            }
        }).catch(() => { /* non-critical */ });

        // Render Planner repositories (grouped by agent pack)
        DOM.ragMaintenanceCollectionsContainer.innerHTML = '';
        if (!plannerCollections.length) {
            const emptyMsg = document.createElement('div');
            emptyMsg.className = 'col-span-full text-gray-400 text-sm';
            emptyMsg.textContent = 'No planner repositories found.';
            DOM.ragMaintenanceCollectionsContainer.appendChild(emptyMsg);
        } else {
            const { packGroups: plannerPacks, ungrouped: plannerUngrouped } = groupByAgentPack(plannerCollections);
            for (const [, group] of plannerPacks) {
                if (group.resources.length === 1) {
                    DOM.ragMaintenanceCollectionsContainer.appendChild(createCollectionCard(group.resources[0]));
                } else {
                    const containerEl = createPackContainerCardDOM(group.pack, group.resources, 'planner');
                    const childGrid = containerEl.querySelector('.pack-children-grid');
                    group.resources.forEach(col => childGrid.appendChild(createCollectionCard(col)));
                    DOM.ragMaintenanceCollectionsContainer.appendChild(containerEl);
                }
            }
            plannerUngrouped.forEach(col => DOM.ragMaintenanceCollectionsContainer.appendChild(createCollectionCard(col)));
            attachPackContainerHandlers(DOM.ragMaintenanceCollectionsContainer);
        }
        
        // Render Knowledge repositories (grouped by agent pack)
        if (DOM.knowledgeRepositoriesContainer) {
            DOM.knowledgeRepositoriesContainer.innerHTML = '';
            if (!knowledgeCollections.length) {
                const emptyMsg = document.createElement('div');
                emptyMsg.className = 'col-span-full text-gray-400 text-sm';
                emptyMsg.textContent = 'No knowledge repositories found.';
                DOM.knowledgeRepositoriesContainer.appendChild(emptyMsg);
            } else {
                const { packGroups: knowledgePacks, ungrouped: knowledgeUngrouped } = groupByAgentPack(knowledgeCollections);
                for (const [, group] of knowledgePacks) {
                    if (group.resources.length === 1) {
                        DOM.knowledgeRepositoriesContainer.appendChild(createKnowledgeRepositoryCard(group.resources[0]));
                    } else {
                        const containerEl = createPackContainerCardDOM(group.pack, group.resources, 'knowledge');
                        const childGrid = containerEl.querySelector('.pack-children-grid');
                        group.resources.forEach(col => childGrid.appendChild(createKnowledgeRepositoryCard(col)));
                        DOM.knowledgeRepositoriesContainer.appendChild(containerEl);
                    }
                }
                knowledgeUngrouped.forEach(col => DOM.knowledgeRepositoriesContainer.appendChild(createKnowledgeRepositoryCard(col)));
                attachPackContainerHandlers(DOM.knowledgeRepositoriesContainer);
                updatePackContainerDocCounts(DOM.knowledgeRepositoriesContainer);
            }
        }
    } catch (err) {
        console.error('Failed to load RAG collections:', err);
        DOM.ragMaintenanceCollectionsContainer.innerHTML = '';
        const errMsg = document.createElement('div');
        errMsg.className = 'col-span-full text-rose-400 text-sm';
        errMsg.textContent = `Error loading collections: ${err.message}`;
        DOM.ragMaintenanceCollectionsContainer.appendChild(errMsg);
    }

    // Wire active-filter toggles (idempotent via data-wired guard)
    ['planner-active-filter', 'knowledge-active-filter'].forEach(id => {
        const el = document.getElementById(id);
        if (el && !el.dataset.wired) {
            el.dataset.wired = '1';
            el.addEventListener('change', () => loadRagCollections());
        }
    });
}

// Make loadRagCollections globally available for marketplace handler
window.loadRagCollections = loadRagCollections;

function createKnowledgeRepositoryCard(col) {
    const card = document.createElement('div');
    card.className = 'glass-panel rounded-xl p-4 flex flex-col gap-3 border border-white/10 hover:border-teradata-orange transition-colors';
    card.dataset.repoId = col.id;

    // Header with title and status badge
    const header = document.createElement('div');
    header.className = 'flex items-start justify-between gap-2';
    
    const titleSection = document.createElement('div');
    titleSection.className = 'flex-1';
    
    const title = document.createElement('h2');
    title.className = 'text-lg font-semibold text-white';
    title.textContent = col.name || col.collection_name;
    
    const collectionId = document.createElement('p');
    collectionId.className = 'text-xs text-gray-500';
    collectionId.textContent = `Collection ID: ${col.id}`;
    
    titleSection.appendChild(title);
    titleSection.appendChild(collectionId);
    
    // Status and indicator badges container
    const badgesContainer = document.createElement('div');
    badgesContainer.className = 'flex flex-col gap-1 items-end';
    
    // Active/Disabled badge
    const statusBadge = document.createElement('span');
    statusBadge.className = col.enabled 
        ? 'px-2 py-1 text-xs rounded-full bg-green-500/20 text-emerald-400' 
        : 'px-2 py-1 text-xs rounded-full bg-gray-500/20 text-gray-400';
    statusBadge.textContent = col.enabled ? 'Active' : 'Disabled';
    badgesContainer.appendChild(statusBadge);
    
    // Ownership/Subscription indicators
    const indicatorsRow = document.createElement('div');
    indicatorsRow.className = 'flex gap-1';
    
    // Owner indicator (if user owns this collection)
    if (col.is_owned) {
        const ownerBadge = document.createElement('span');
        ownerBadge.className = 'px-2 py-1 text-xs rounded-full bg-blue-500/20 text-blue-400 flex items-center gap-1';
        ownerBadge.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" /></svg> Owner`;
        ownerBadge.title = 'You own this collection';
        indicatorsRow.appendChild(ownerBadge);
    }

    // Subscribed indicator (if user is subscribed but doesn't own it)
    if (col.is_subscribed && !col.is_owned) {
        const subscribedBadge = document.createElement('span');
        subscribedBadge.className = 'px-2 py-1 text-xs rounded-full bg-blue-500/20 text-blue-400 flex items-center gap-1';
        subscribedBadge.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" /></svg> Subscribed`;
        subscribedBadge.title = 'You are subscribed to this collection';
        indicatorsRow.appendChild(subscribedBadge);
    }

    // Published to marketplace indicator
    if (col.is_marketplace_listed) {
        const publishedBadge = document.createElement('span');
        publishedBadge.className = 'px-2 py-1 text-xs rounded-full bg-orange-500/20 text-amber-400 flex items-center gap-1';
        const visibilityIcon = col.visibility === 'public'
            ? '<svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M3.055 11H5a2 2 0 012 2v1a2 2 0 002 2 2 2 0 012 2v2.945M8 3.935V5.5A2.5 2.5 0 0010.5 8h.5a2 2 0 012 2 2 2 0 104 0 2 2 0 012-2h1.064M15 20.488V18a2 2 0 012-2h3.064M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>'
            : '<svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" /></svg>';
        publishedBadge.innerHTML = `${visibilityIcon} ${col.visibility === 'public' ? 'Public' : 'Unlisted'}`;
        publishedBadge.title = `Published to marketplace (${col.visibility})`;
        indicatorsRow.appendChild(publishedBadge);
    }
    
    if (indicatorsRow.children.length > 0) {
        badgesContainer.appendChild(indicatorsRow);
    }
    
    header.appendChild(titleSection);
    header.appendChild(badgesContainer);
    
    // Description (if exists)
    const desc = col.description ? (() => {
        const d = document.createElement('p');
        d.className = 'text-xs text-gray-400';
        d.textContent = col.description;
        return d;
    })() : null;
    
    // Metadata (backend badge and chunking info)
    const meta = document.createElement('p');
    meta.className = 'text-xs text-gray-500';
    const chunkInfo = col.chunking_strategy ? ` | ${col.chunking_strategy} chunking` : '';
    meta.appendChild(_createBackendBadge(col.backend_type || 'chromadb'));
    meta.appendChild(document.createTextNode(`${col.collection_name}${chunkInfo}`));

    // Embedding model info
    const embeddingInfo = document.createElement('p');
    embeddingInfo.className = 'text-xs text-gray-400';
    const embeddingModel = col.embedding_model || 'all-MiniLM-L6-v2';
    embeddingInfo.innerHTML = `<span class="text-gray-500">Embedding Model:</span> ${embeddingModel}`;

    // Document and chunk counts (persisted in database)
    const countsInfo = document.createElement('p');
    countsInfo.className = 'text-xs text-gray-400';
    const docCount = col.document_count || 0;
    const chunkCount = col.count || col.chunk_count || 0;
    countsInfo.textContent = `${docCount} documents • ${chunkCount} chunks`;

    card.appendChild(header);
    if (desc) card.appendChild(desc);
    card.appendChild(meta);
    card.appendChild(embeddingInfo);
    card.appendChild(countsInfo);
    
    // Actions
    const actions = document.createElement('div');
    actions.className = 'mt-2 flex gap-2 flex-wrap';
    
    // Toggle enable/disable button
    const toggleBtn = document.createElement('button');
    toggleBtn.type = 'button';
    toggleBtn.className = col.enabled
        ? 'toggle-knowledge-btn card-btn card-btn--warning'
        : 'toggle-knowledge-btn card-btn card-btn--success';
    toggleBtn.dataset.enabled = col.enabled ? 'true' : 'false';
    toggleBtn.textContent = col.enabled ? 'Disable' : 'Enable';
    toggleBtn.addEventListener('click', () => {
        if (window.ragCollectionManagement) {
            window.ragCollectionManagement.toggleRagCollection(col.id, col.enabled);
        }
    });

    // Inspect button
    const inspectBtn = document.createElement('button');
    inspectBtn.type = 'button';
    inspectBtn.className = 'card-btn card-btn--primary';
    inspectBtn.textContent = 'Inspect';
    inspectBtn.addEventListener('click', () => {
        // Use the shared collection inspection view
        openCollectionInspection(col.id, col.name, 'knowledge', col);
    });

    // Edit button
    const isManaged = col.is_subscribed && !col.is_owned;
    const managedBy = (col.agent_packs || []).map(p => p.name).join(', ') || 'external source';
    const editBtn = document.createElement('button');
    editBtn.type = 'button';
    if (isManaged) {
        editBtn.className = 'card-btn';
        editBtn.disabled = true;
        editBtn.title = `Managed by: ${managedBy} — uninstall the pack(s) to edit`;
    } else {
        editBtn.className = 'card-btn card-btn--info';
    }
    editBtn.textContent = 'Edit';
    if (!isManaged) {
        editBtn.addEventListener('click', () => {
            if (window.ragCollectionManagement) {
                window.ragCollectionManagement.openEditCollectionModal(col);
            }
        });
    }

    // Upload button (Knowledge repositories only)
    const uploadBtn = document.createElement('button');
    uploadBtn.type = 'button';
    if (isManaged) {
        uploadBtn.className = 'card-btn';
        uploadBtn.disabled = true;
        uploadBtn.title = `Managed by: ${managedBy} — uninstall the pack(s) to upload`;
    } else {
        uploadBtn.className = 'card-btn card-btn--info';
    }
    uploadBtn.innerHTML = '<span style="font-size: 14px;">+</span> Upload';
    if (!isManaged) {
        uploadBtn.addEventListener('click', () => {
            if (window.knowledgeRepositoryHandler && window.knowledgeRepositoryHandler.openUploadDocumentsModal) {
                window.knowledgeRepositoryHandler.openUploadDocumentsModal(col.id, col.name, col);
            }
        });
    }

    // Export button (Knowledge repositories only)
    const exportBtn = document.createElement('button');
    exportBtn.type = 'button';
    exportBtn.className = 'card-btn card-btn--cyan';
    exportBtn.innerHTML = `<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"></path>
    </svg>Export`;
    exportBtn.addEventListener('click', () => {
        if (exportKnowledgeRepository) {
            exportKnowledgeRepository(col.id, col.name || col.collection_name);
        }
    });

    // Reset button (only for owned knowledge repositories)
    if (col.is_owned) {
        const resetBtn = document.createElement('button');
        resetBtn.type = 'button';
        if (isManaged) {
            resetBtn.className = 'card-btn';
            resetBtn.disabled = true;
            resetBtn.title = `Managed by: ${managedBy} — uninstall the pack(s) to reset`;
        } else {
            resetBtn.className = 'card-btn card-btn--warning';
        }
        resetBtn.textContent = 'Reset';
        if (!isManaged) {
            resetBtn.addEventListener('click', () => {
                if (window.knowledgeRepositoryHandler && window.knowledgeRepositoryHandler.resetKnowledgeRepository) {
                    window.knowledgeRepositoryHandler.resetKnowledgeRepository(col.id, col.name);
                }
            });
        }
        actions.appendChild(resetBtn);
    }

    // Delete/Unsubscribe button for Knowledge repositories
    if (col.is_subscribed && !col.is_owned && col.agent_packs?.length) {
        // A reference made by an AGENT-PACK subscription follows the pack: the pack
        // unsubscribe removes it (and the endpoint refuses a lone unsubscribe with
        // 409 managed_by_agent_pack) — never an Unsubscribe of its own here.
        const managedNote = document.createElement('span');
        managedNote.className = 'coll-pack-managed-note card-btn u-tip';
        managedNote.style.cssText = 'cursor:default;opacity:.7;';
        managedNote.dataset.tip = `This reference belongs to your agent-pack subscription (${col.agent_packs.map(p => p.name).join(', ')}): it is removed when you unsubscribe from the pack on its marketplace card`;
        managedNote.textContent = 'Removed with the pack';
        actions.appendChild(managedNote);
    } else if (col.is_subscribed && !col.is_owned) {
        // Subscribed collection - show Unsubscribe button
        const unsubscribeBtn = document.createElement('button');
        unsubscribeBtn.type = 'button';
        unsubscribeBtn.className = 'card-btn card-btn--warning';
        unsubscribeBtn.textContent = 'Unsubscribe';
        unsubscribeBtn.addEventListener('click', async () => {
            if (window.marketplaceHandler && window.marketplaceHandler.unsubscribeFromCollection) {
                await window.marketplaceHandler.unsubscribeFromCollection(col.subscription_id, col.name);
            } else {
                console.error('Marketplace handler not available for unsubscribe');
            }
        });
        actions.appendChild(unsubscribeBtn);
    } else {
        // Owned collection - show Delete button
        const deleteBtn = document.createElement('button');
        deleteBtn.type = 'button';
        deleteBtn.className = 'card-btn card-btn--danger';
        deleteBtn.textContent = 'Delete';
        deleteBtn.addEventListener('click', () => {
            if (window.knowledgeRepositoryHandler) {
                window.knowledgeRepositoryHandler.deleteKnowledgeRepository(col.id, col.name);
            }
        });
        actions.appendChild(deleteBtn);
    }
    
    actions.appendChild(toggleBtn);
    actions.appendChild(inspectBtn);
    // no lock toggle here: `trust_locked` is a PLANNER opt-in (the API refuses it for a knowledge
    // repository) — the toggle lives on createCollectionCard, and a stray reference here threw
    // `lockBtn is not defined` for every user, blanking the whole knowledge-repositories tab.
    actions.appendChild(editBtn);
    actions.appendChild(uploadBtn);
    actions.appendChild(exportBtn);
    // Delete/Unsubscribe button already appended above
    
    card.appendChild(actions);
    
    // Load document count asynchronously
    loadKnowledgeRepositoryDocCount(col.id);
    
    return card;
}

function createCollectionCard(col) {
    const card = document.createElement('div');
    card.className = 'glass-panel rounded-xl p-4 flex flex-col gap-3 border border-white/10 hover:border-teradata-orange transition-colors';
    card.dataset.repoId = col.id;
            
            // Header with title and status badge
            const header = document.createElement('div');
            header.className = 'flex items-start justify-between gap-2';
            
            const titleSection = document.createElement('div');
            titleSection.className = 'flex-1';
            
            const title = document.createElement('h2');
            title.className = 'text-lg font-semibold text-white';
            title.textContent = col.name;

            // Agent pack badges
            if (col.agent_packs?.length) {
                col.agent_packs.forEach(p => {
                    const badge = document.createElement('span');
                    badge.className = 'agent-pack-badge';
                    badge.innerHTML = `<svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4"/></svg>${escapeHtml(p.name)}`;
                    title.appendChild(document.createTextNode(' '));
                    title.appendChild(badge);
                });
            }

            const collectionId = document.createElement('p');
            collectionId.className = 'text-xs text-gray-500';
            collectionId.textContent = `Collection ID: ${col.id}`;

            titleSection.appendChild(title);
            titleSection.appendChild(collectionId);
            
            // Status and indicator badges container
            const badgesContainer = document.createElement('div');
            badgesContainer.className = 'flex flex-col gap-1 items-end';
            
            // Active/Disabled badge
            const statusBadge = document.createElement('span');
            statusBadge.className = col.enabled 
                ? 'px-2 py-1 text-xs rounded-full bg-green-500/20 text-emerald-400' 
                : 'px-2 py-1 text-xs rounded-full bg-gray-500/20 text-gray-400';
            statusBadge.textContent = col.enabled ? 'Active' : 'Disabled';
            badgesContainer.appendChild(statusBadge);
            
            // Ownership/Subscription indicators
            const indicatorsRow = document.createElement('div');
            indicatorsRow.className = 'flex gap-1';
            
            // Owner indicator (if user owns this collection)
            if (col.is_owned) {
                const ownerBadge = document.createElement('span');
                ownerBadge.className = 'px-2 py-1 text-xs rounded-full bg-blue-500/20 text-blue-400 flex items-center gap-1';
                ownerBadge.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" /></svg> Owner`;
                ownerBadge.title = 'You own this collection';
                indicatorsRow.appendChild(ownerBadge);
            }

            // Subscribed indicator (if user is subscribed but doesn't own it)
            if (col.is_subscribed && !col.is_owned) {
                const subscribedBadge = document.createElement('span');
                subscribedBadge.className = 'px-2 py-1 text-xs rounded-full bg-blue-500/20 text-blue-400 flex items-center gap-1';
                subscribedBadge.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" /></svg> Subscribed`;
                subscribedBadge.title = 'You are subscribed to this collection';
                indicatorsRow.appendChild(subscribedBadge);
            }

            // Locked indicator (planner): the trust seal covers EVERY case and the
            // learning loop never writes here — the owner's opt-in to a frozen repository.
            if (col.repository_type === 'planner' && col.trust_locked) {
                const lockedBadge = document.createElement('span');
                lockedBadge.className = 'px-2 py-1 text-xs rounded-full bg-purple-500/20 text-purple-300 flex items-center gap-1';
                lockedBadge.dataset.trustLocked = '1';
                lockedBadge.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" /></svg> Locked`;
                lockedBadge.title = 'Locked: the trust seal covers every case (learning included) and the agent never writes learned cases here';
                indicatorsRow.appendChild(lockedBadge);
            }

            // Published to marketplace indicator
            if (col.is_marketplace_listed) {
                const publishedBadge = document.createElement('span');
                publishedBadge.className = 'px-2 py-1 text-xs rounded-full bg-orange-500/20 text-amber-400 flex items-center gap-1';
                const visibilityIcon = col.visibility === 'public'
                    ? '<svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M3.055 11H5a2 2 0 012 2v1a2 2 0 002 2 2 2 0 012 2v2.945M8 3.935V5.5A2.5 2.5 0 0010.5 8h.5a2 2 0 012 2 2 2 0 104 0 2 2 0 012-2h1.064M15 20.488V18a2 2 0 012-2h3.064M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>'
                    : '<svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" /></svg>';
                publishedBadge.innerHTML = `${visibilityIcon} ${col.visibility === 'public' ? 'Public' : 'Unlisted'}`;
                publishedBadge.title = `Published to marketplace (${col.visibility})`;
                indicatorsRow.appendChild(publishedBadge);
            }
            
            if (indicatorsRow.children.length > 0) {
                badgesContainer.appendChild(indicatorsRow);
            }
            
            header.appendChild(titleSection);
            header.appendChild(badgesContainer);
            
            // MCP Server info - look up name from ID using window.configState
            const mcpInfo = document.createElement('p');
            mcpInfo.className = 'text-sm text-gray-300';
            let mcpServerDisplay = 'None';
            // A SUBSCRIBED planner is the publisher's collection: its own mcp_server_id is
            // the publisher's server, which this account cannot resolve ("Unknown").
            // What matters to the subscriber is the server the reference is keyed to
            // FOR THEM (collection_subscriptions.mcp_server_id) — the one their
            // sessions run on, and the only one the repository loads for.
            const keyedHere = (col.is_subscribed && !col.is_owned) ? (col.subscription_mcp_server_id || null) : null;
            const serverIdToShow = keyedHere || col.mcp_server_id;
            if (serverIdToShow && window.configState && window.configState.mcpServers) {
                const mcpServer = window.configState.mcpServers.find(s => s.id === serverIdToShow);
                mcpServerDisplay = mcpServer ? mcpServer.name : 'Unknown';
            }
            const mcpLabel = keyedHere ? 'Keyed to your server:' : 'MCP Server:';
            mcpInfo.innerHTML = `<span class="text-gray-500">${mcpLabel}</span> ${escapeHtml(mcpServerDisplay)}`;
            if (col.is_subscribed && !col.is_owned && !keyedHere) {
                mcpInfo.innerHTML += ` <span class="text-xs text-amber-400" title="This subscription predates per-subscriber keying; re-subscribe to key it to one of your servers.">not keyed to any of your servers</span>`;
            }
            
            // Description (if exists)
            const desc = col.description ? (() => {
                const d = document.createElement('p');
                d.className = 'text-xs text-gray-400';
                d.textContent = col.description;
                return d;
            })() : null;
            
            // Metadata (collection_name from backend)
            const meta = document.createElement('p');
            meta.className = 'text-xs text-gray-500';
            meta.appendChild(_createBackendBadge(col.backend_type || 'chromadb'));
            meta.appendChild(document.createTextNode(col.collection_name));

            // Embedding model info
            const embeddingInfo = document.createElement('p');
            embeddingInfo.className = 'text-xs text-gray-400';
            const embeddingModel = col.embedding_model || 'all-MiniLM-L6-v2';
            embeddingInfo.innerHTML = `<span class="text-gray-500">Embedding Model:</span> ${embeddingModel}`;

            // Case count (persisted in database)
            const countsInfo = document.createElement('p');
            countsInfo.className = 'text-xs text-gray-400';
            const caseCount = col.count || col.chunk_count || 0;
            countsInfo.textContent = `${caseCount} cases`;

            // Actions
            const actions = document.createElement('div');
            actions.className = 'mt-2 flex gap-2 flex-wrap';
            
            // Toggle enable/disable button
            const toggleBtn = document.createElement('button');
            toggleBtn.type = 'button';
            toggleBtn.className = col.enabled
                ? 'toggle-knowledge-btn card-btn card-btn--warning'
                : 'toggle-knowledge-btn card-btn card-btn--success';
            toggleBtn.dataset.enabled = col.enabled ? 'true' : 'false';
            toggleBtn.textContent = col.enabled ? 'Disable' : 'Enable';
            toggleBtn.addEventListener('click', () => {
                if (window.ragCollectionManagement) {
                    window.ragCollectionManagement.toggleRagCollection(col.id, col.enabled);
                }
            });

            // Inspect button
            const inspectBtn = document.createElement('button');
            inspectBtn.type = 'button';
            inspectBtn.className = 'card-btn card-btn--primary';
            inspectBtn.textContent = 'Inspect';
            inspectBtn.addEventListener('click', () => {
                openCollectionInspection(col.id, col.name, col.repository_type);
            });

            // Lock / Unlock (owner, planner): Model B opt-in. The seal then covers every
            // case and the learning loop never writes here; unlocking returns to the
            // default (pack-owned cases sealed, learning outside the seal).
            let lockBtn = null;
            if (col.repository_type === 'planner' && col.is_owned) {
                lockBtn = document.createElement('button');
                lockBtn.type = 'button';
                lockBtn.className = col.trust_locked ? 'card-btn card-btn--warning' : 'card-btn';
                lockBtn.dataset.action = 'toggle-trust-lock';
                lockBtn.textContent = col.trust_locked ? 'Unlock' : 'Lock';
                lockBtn.title = col.trust_locked
                    ? 'Unlock: seal only the pack-owned cases again; learned cases may be written here'
                    : 'Lock: seal every case (learning included) and stop the agent writing learned cases here';
                lockBtn.addEventListener('click', async () => {
                    lockBtn.disabled = true;
                    try {
                        const res = await fetch(`/api/v1/rag/collections/${col.id}`, {
                            method: 'PUT',
                            headers: { 'Content-Type': 'application/json',
                                       'Authorization': `Bearer ${localStorage.getItem('tda_auth_token')}` },
                            body: JSON.stringify({ trust_locked: !col.trust_locked }),
                        });
                        const j = await res.json().catch(() => ({}));
                        if (!res.ok || j.status === 'error') throw new Error(j.message || 'Could not change the lock');
                        if (typeof window.showAppBanner === 'function') {
                            window.showAppBanner(col.trust_locked ? `"${col.name}" unlocked` : `"${col.name}" locked — every case is now under the seal`, 'success', 4000);
                        }
                        await loadRagCollections();
                    } catch (e) {
                        if (typeof window.showAppBanner === 'function') window.showAppBanner(e.message, 'error', 6000);
                        lockBtn.disabled = false;
                    }
                });
            }

            // Edit button
            const isPlannerManaged = col.is_subscribed && !col.is_owned;
            const plannerManagedBy = (col.agent_packs || []).map(p => p.name).join(', ') || 'external source';
            const editBtn = document.createElement('button');
            editBtn.type = 'button';
            if (isPlannerManaged) {
                editBtn.className = 'card-btn';
                editBtn.disabled = true;
                editBtn.title = `Managed by: ${plannerManagedBy} — uninstall the pack(s) to edit`;
            } else {
                editBtn.className = 'card-btn card-btn--info';
            }
            editBtn.textContent = 'Edit';
            if (!isPlannerManaged) {
                editBtn.addEventListener('click', () => {
                    if (window.ragCollectionManagement) {
                        window.ragCollectionManagement.openEditCollectionModal(col);
                    }
                });
            }
            
            // Export button (only for owned collections)
            if (col.is_owned) {
                const exportBtn = document.createElement('button');
                exportBtn.type = 'button';
                exportBtn.className = 'card-btn card-btn--cyan';
                exportBtn.innerHTML = `
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"></path>
                    </svg>
                    Export
                `;
                exportBtn.addEventListener('click', async () => {
                    try {
                        await exportKnowledgeRepository(col.id, col.name);
                    } catch (error) {
                        console.error('Export failed:', error);
                    }
                });
                actions.appendChild(exportBtn);
            }

            // Reset button (only for owned collections)
            if (col.is_owned) {
                const isResetPackManaged = col.agent_packs?.length > 0;
                const resetPackNames = isResetPackManaged ? col.agent_packs.map(p => p.name).join(', ') : '';
                const resetBtn = document.createElement('button');
                resetBtn.type = 'button';
                if (isResetPackManaged) {
                    resetBtn.className = 'card-btn';
                    resetBtn.disabled = true;
                    resetBtn.title = `Managed by: ${resetPackNames} — uninstall the pack(s) to reset`;
                } else if (col.id === 0) {
                    resetBtn.className = 'card-btn';
                    resetBtn.disabled = true;
                } else {
                    resetBtn.className = 'card-btn card-btn--warning';
                }
                resetBtn.textContent = 'Reset';
                if (!isResetPackManaged && col.id !== 0) {
                    resetBtn.addEventListener('click', () => {
                        if (window.ragCollectionManagement && window.ragCollectionManagement.resetRagCollection) {
                            window.ragCollectionManagement.resetRagCollection(col.id, col.name);
                        }
                    });
                }
                actions.appendChild(resetBtn);
            }

            // Delete/Unsubscribe button
            if (col.is_subscribed && !col.is_owned && col.agent_packs?.length) {
                // A reference made by an AGENT-PACK subscription follows the pack: the pack
                // unsubscribe removes it (and the endpoint refuses a lone unsubscribe with
                // 409 managed_by_agent_pack) — never an Unsubscribe of its own here.
                const managedNote = document.createElement('span');
                managedNote.className = 'coll-pack-managed-note card-btn u-tip';
                managedNote.style.cssText = 'cursor:default;opacity:.7;';
                managedNote.dataset.tip = `This reference belongs to your agent-pack subscription (${col.agent_packs.map(p => p.name).join(', ')}): it is removed when you unsubscribe from the pack on its marketplace card`;
                managedNote.textContent = 'Removed with the pack';
                actions.appendChild(managedNote);
            } else if (col.is_subscribed && !col.is_owned) {
                // Subscribed collection - show Unsubscribe button
                const unsubscribeBtn = document.createElement('button');
                unsubscribeBtn.type = 'button';
                unsubscribeBtn.className = 'card-btn card-btn--warning';
                unsubscribeBtn.textContent = 'Unsubscribe';
                unsubscribeBtn.addEventListener('click', async () => {
                    if (window.marketplaceHandler && window.marketplaceHandler.unsubscribeFromCollection) {
                        await window.marketplaceHandler.unsubscribeFromCollection(col.subscription_id, col.name);
                    } else {
                        console.error('Marketplace handler not available for unsubscribe');
                    }
                });
                actions.appendChild(unsubscribeBtn);
            } else {
                // Owned collection - show Delete button (disabled for ID 0 or pack-managed)
                const isPackManaged = col.agent_packs?.length > 0;
                const packNames = isPackManaged ? col.agent_packs.map(p => p.name).join(', ') : '';
                const deleteBtn = document.createElement('button');
                deleteBtn.type = 'button';
                if (isPackManaged) {
                    deleteBtn.className = 'card-btn';
                    deleteBtn.disabled = true;
                    deleteBtn.title = `Managed by: ${packNames} — uninstall the pack(s) to remove`;
                } else if (col.id === 0) {
                    deleteBtn.className = 'card-btn';
                    deleteBtn.disabled = true;
                } else {
                    deleteBtn.className = 'card-btn card-btn--danger';
                }
                deleteBtn.textContent = 'Delete';
                if (!isPackManaged && col.id !== 0) {
                    deleteBtn.addEventListener('click', () => {
                        if (window.ragCollectionManagement) {
                            window.ragCollectionManagement.deleteRagCollection(col.id, col.name);
                        }
                    });
                }
                actions.appendChild(deleteBtn);
            }
            
            actions.appendChild(toggleBtn);
            actions.appendChild(inspectBtn);
            actions.appendChild(editBtn);
            // Delete/Unsubscribe button already appended above

    card.appendChild(header);
    if (desc) card.appendChild(desc);
    card.appendChild(mcpInfo);
    card.appendChild(meta);
    card.appendChild(embeddingInfo);
    card.appendChild(countsInfo);
    card.appendChild(actions);
    
    return card;
}

// --- RAG Collection Inspection Logic ---
export async function openCollectionInspection(collectionId, collectionName, repositoryType = 'planner', repositoryMeta = null) {
    if (collectionId === undefined) return;
    state.currentInspectedCollectionId = collectionId;
    state.currentInspectedCollectionName = collectionName;
    state.currentInspectedRepositoryType = repositoryType;
    state.currentInspectedRepositoryMeta = repositoryMeta;
    
    if (DOM.ragCollectionInspectTitle) {
        DOM.ragCollectionInspectTitle.textContent = `Inspect: ${collectionName}`;
    }

    // Screen filter (corpus screening): reset per inspection; the control reveals itself
    // after the first fetch confirms screening is enabled for this repository.
    state.ragScreenFilter = 'all';
    state.ragSuspiciousOnly = false;   // legacy mirror, kept in step by _screenFilter()
    const suspToggle = document.getElementById('rag-collection-screen-filter');
    const suspExport = document.getElementById('rag-collection-suspicious-export');
    if (suspToggle) { suspToggle.classList.add('hidden'); suspToggle.style.display = 'none'; }
    _setSuspiciousToggleVisual();
    if (suspExport) suspExport.classList.add('hidden');

    // Show repository metadata for knowledge repositories
    if (repositoryType === 'knowledge' && repositoryMeta) {
        displayKnowledgeRepositoryMetadata(repositoryMeta);
        // Corpus Health (RAG Trust Hardening U6): scan coverage, findings, PII
        // manifest, quarantine — rendered into the inspect view's live container.
        const ch = document.getElementById('corpus-health-live');
        if (ch) { ch.textContent = 'Loading…'; fetchCorpusHealth(collectionId); }
    } else {
        // Hide metadata section for planner repositories
        hideKnowledgeRepositoryMetadata();
    }
    // Planner Health — the planner twin of Corpus Health, in the same place: the cases
    // the signing gate refuses, named, with Remove / Acknowledge. Planner repositories
    // only; the module is loaded on first use, exactly like the knowledge handler.
    const ph = document.getElementById('planner-health');
    const isPlanner = repositoryType !== 'knowledge';
    if (ph) ph.classList.toggle('hidden', !isPlanner);
    if (isPlanner) {
        const live = document.getElementById('planner-health-live');
        if (live) live.textContent = 'Loading…';
        import('./handlers/plannerHealth.js?v=1.0')
            .then((m) => m.fetchPlannerHealth(collectionId))
            .catch((e) => { console.error('[PlannerHealth]', e); if (live) live.textContent = 'Planner health unavailable.'; });
    }
    
    handleViewSwitch('rag-collection-inspect-view');
    // Wire BEFORE the fetch. The fetch is what reveals the suspicious-chunk controls, so
    // wiring afterwards left a window in which those buttons were visible but dead — on a
    // large corpus (7,773 chunks here) that window is long enough to click through.
    wireCollectionInspectionEvents();
    await fetchAndRenderCollectionRows({ collectionId: collectionId, refresh: true });
}

/** Turn on the flagged-chunk filter and bring the table into view.
 *
 *  The toolbar toggle sits in the inspect header, which the metadata + Corpus Health
 *  block pushes above the fold on a knowledge repository — so the affordance is also
 *  offered from Corpus Health itself, where the findings are actually being read. Same
 *  state, same fetch: one filter, two entry points, no second implementation.
 */
export function showFlaggedChunks(which = 'flagged') {
    state.ragScreenFilter = (which === 'blocking' || which === 'all') ? which : 'flagged';
    state.ragSuspiciousOnly = state.ragScreenFilter !== 'all';
    const grp = document.getElementById('rag-collection-screen-filter');
    const exp = document.getElementById('rag-collection-suspicious-export');
    if (grp) { grp.classList.remove('hidden'); grp.style.display = 'inline-flex'; }
    _setSuspiciousToggleVisual();
    if (exp) exp.classList.remove('hidden');
    const id = state.currentInspectedCollectionId;
    if (id === null || id === undefined) return;
    fetchAndRenderCollectionRows({
        collectionId: id, query: state.ragCollectionSearchTerm || '', refresh: true,
    });
    const wrap = document.getElementById('rag-collection-table-wrapper');
    if (wrap) wrap.scrollIntoView({ behavior: 'smooth', block: 'start' });
}
if (typeof window !== 'undefined') window.ragShowFlaggedChunks = showFlaggedChunks;
// Planner Health's two hooks into the rows view: re-read the table after a case is
// removed or stamped, and select a case the panel names. Same fetch, same selection —
// never a second implementation.
if (typeof window !== 'undefined') {
    window.ragRefreshInspectedRows = () => {
        const id = state.currentInspectedCollectionId;
        if (id === null || id === undefined) return;
        fetchAndRenderCollectionRows({ collectionId: id, query: state.ragCollectionSearchTerm || '', refresh: true });
    };
    window.ragSelectCase = (caseId) => { try { selectCaseRow(caseId); } catch (_) { /* a case not in the loaded page */ } };
}

function wireCollectionInspectionEvents() {
    if (DOM.ragCollectionInspectBack) {
        DOM.ragCollectionInspectBack.onclick = () => {
            state.currentInspectedCollectionId = null;
            state.currentInspectedCollectionName = null;
            handleViewSwitch('rag-maintenance-view');
        };
    }
    if (DOM.ragCollectionRefreshButton) {
        DOM.ragCollectionRefreshButton.onclick = () => {
            if (state.currentInspectedCollectionId !== null && state.currentInspectedCollectionId !== undefined) {
                fetchAndRenderCollectionRows({ collectionId: state.currentInspectedCollectionId, refresh: true });
            }
        };
    }
    const sfGroup = document.getElementById('rag-collection-screen-filter');
    if (sfGroup && !sfGroup.dataset._wired) {
        sfGroup.addEventListener('click', (ev) => {
            const b = ev.target.closest('.rag-sf-btn');
            if (!b || !b.dataset.sf) return;
            state.ragScreenFilter = b.dataset.sf;
            state.ragSuspiciousOnly = state.ragScreenFilter !== 'all';
            _setSuspiciousToggleVisual();
            if (state.currentInspectedCollectionId !== null && state.currentInspectedCollectionId !== undefined) {
                fetchAndRenderCollectionRows({
                    collectionId: state.currentInspectedCollectionId,
                    query: state.ragCollectionSearchTerm || '',
                    refresh: true
                });
            }
        });
        sfGroup.dataset._wired = 'true';
    }
    const suspExportBtn = document.getElementById('rag-collection-suspicious-export');
    if (suspExportBtn && !suspExportBtn.dataset._wired) {
        suspExportBtn.addEventListener('click', () => exportSuspiciousChunks());
        suspExportBtn.dataset._wired = 'true';
    }
    if (DOM.ragCollectionSearchInput && !DOM.ragCollectionSearchInput.dataset._wired) {
        let debounceTimer = null;
        DOM.ragCollectionSearchInput.addEventListener('input', () => {
            const term = DOM.ragCollectionSearchInput.value.trim();
            state.ragCollectionSearchTerm = term;
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => {
                if (state.currentInspectedCollectionId !== null && state.currentInspectedCollectionId !== undefined) {
                    fetchAndRenderCollectionRows({ collectionId: state.currentInspectedCollectionId, query: term });
                }
            }, 300);
        });
        DOM.ragCollectionSearchInput.dataset._wired = 'true';
    }
    
    // Wire infinite scroll pagination on table
    const tableContainer = document.querySelector('#rag-collection-table-body')?.closest('.overflow-auto');
    if (tableContainer && !tableContainer.dataset._scrollWired) {
        let isLoading = false;
        tableContainer.addEventListener('scroll', () => {
            // Check if scrolled near bottom (within 200px)
            if (tableContainer.scrollHeight - tableContainer.scrollTop - tableContainer.clientHeight < 200) {
                // Only load more if we haven't already and there are more rows
                if (!isLoading && state.ragCollectionHasMore && state.currentInspectedCollectionId !== null) {
                    isLoading = true;
                    console.log('[InfiniteScroll] Loading next segment...');
                    fetchAndRenderCollectionRows({ 
                        collectionId: state.currentInspectedCollectionId, 
                        query: state.ragCollectionSearchTerm || ''
                    }).finally(() => {
                        isLoading = false;
                    });
                }
            }
        });
        tableContainer.dataset._scrollWired = 'true';
    }
    
    // Wire table header sorting
    const thead = document.querySelector('#rag-collection-table-body')?.closest('table')?.querySelector('thead');
    if (thead && !thead.dataset._sortingWired) {
        thead.querySelectorAll('th[data-sort-key]').forEach(th => {
            th.addEventListener('click', () => {
                const sortKey = th.dataset.sortKey;
                // Toggle sort direction if clicking same column
                if (state.ragCollectionSortKey === sortKey) {
                    state.ragCollectionSortDirection = state.ragCollectionSortDirection === 'asc' ? 'desc' : 'asc';
                } else {
                    state.ragCollectionSortKey = sortKey;
                    state.ragCollectionSortDirection = 'asc';
                }
                // Fetch sorted data from server (server-side sorting) - reset pagination
                if (state.currentInspectedCollectionId !== null && state.currentInspectedCollectionId !== undefined) {
                    fetchAndRenderCollectionRows({ 
                        collectionId: state.currentInspectedCollectionId, 
                        query: state.ragCollectionSearchTerm || '',
                        refresh: true
                    });
                }
                updateSortIndicators();
            });
        });
        thead.dataset._sortingWired = 'true';
    }
}

async function fetchAndRenderCollectionRows({ collectionId, query = '', refresh = false }) {
    if (collectionId === undefined || collectionId === null) return;
    
    // Reset pagination on refresh or new search
    if (refresh || query !== state.ragCollectionSearchTerm) {
        state.ragCollectionOffset = 0;
        state.ragCollectionHasMore = true;
    }
    
    // Store the search term for next comparison
    state.ragCollectionSearchTerm = query;
    
    if (DOM.ragCollectionLoading) {
        if (state.ragCollectionOffset === 0) {
            // Initial load
            DOM.ragCollectionLoading.textContent = query && query.length >= 3 ? 'Searching...' : 'Loading rows...';
        } else {
            // Pagination load
            DOM.ragCollectionLoading.textContent = 'Loading next segment...';
        }
        DOM.ragCollectionLoading.style.display = 'block';
    }
    try {
        const params = new URLSearchParams();
        const pageSize = 50;  // Reduced from 100 to enable proper pagination batching
        params.set('limit', pageSize.toString());
        params.set('offset', state.ragCollectionOffset.toString());
        params.set('light', 'true');
        if (query && query.length >= 3) {
            params.set('q', query);
        }
        // Add sorting parameters
        if (state.ragCollectionSortKey) {
            params.set('sort_by', state.ragCollectionSortKey);
            params.set('sort_order', state.ragCollectionSortDirection || 'asc');
        }
        // Get authentication token
        const token = localStorage.getItem('tda_auth_token');
        
        // Determine API endpoint based on repository type
        const repositoryType = state.currentInspectedRepositoryType || 'planner';
        if (repositoryType === 'knowledge' && _screenFilter() !== 'all') {
            params.set('screen_filter', _screenFilter());
        }
        let apiUrl;
        if (repositoryType === 'knowledge') {
            apiUrl = `/api/v1/knowledge/collections/${collectionId}/chunks?${params.toString()}`;
        } else {
            apiUrl = `/api/v1/rag/collections/${collectionId}/rows?${params.toString()}`;
        }
        
        const res = await fetch(apiUrl, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });
        
        if (!res.ok) {
            throw new Error(`HTTP error! status: ${res.status}`);
        }
        
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        
        // Handle different response structures: rows (planner) vs chunks (knowledge)
        const newRows = repositoryType === 'knowledge' ? (data.chunks || []) : (data.rows || []);
        const totalAvailable = data.total || 0;

        // Reveal the suspicious-chunk controls only when the server confirms the
        // screening layer is enabled (guardrails master switch) for knowledge repos.
        const suspToggle = document.getElementById('rag-collection-screen-filter');
        const suspExport = document.getElementById('rag-collection-suspicious-export');
        const screeningOn = repositoryType === 'knowledge' && data.screening_enabled === true;
        if (suspToggle) {
            suspToggle.classList.toggle('hidden', !screeningOn);
            suspToggle.style.display = screeningOn ? 'inline-flex' : 'none';
            _setSuspiciousToggleVisual();
            // Counts once per inspection: they are what makes the three populations legible
            // (7,773 / 235 / 4 here), and they were the missing unit.
            if (screeningOn && _screenFilter() === 'all') _loadScreenFilterCounts(collectionId, data.total || 0);
        }
        if (suspExport) suspExport.classList.toggle('hidden', !screeningOn);
        state.ragScanCapped = data.scan_capped === true;
        
        // On first load/refresh, replace rows. On pagination, append rows.
        if (state.ragCollectionOffset === 0) {
            state.currentCollectionRows = newRows;
        } else {
            state.currentCollectionRows = (state.currentCollectionRows || []).concat(newRows);
        }
        
        state.currentCollectionTotal = totalAvailable;
        state.currentCollectionName = data.collection_name || `Collection ${collectionId}`;
        
        // Update offset for next fetch
        state.ragCollectionOffset += newRows.length;
        state.ragCollectionHasMore = state.ragCollectionOffset < totalAvailable;
        
        // Re-render the table based on repository type
        if (state.ragCollectionOffset === newRows.length) {
            // First load - full render
            if (repositoryType === 'knowledge') {
                renderKnowledgeChunks(state.currentCollectionRows, state.currentCollectionTotal, query, state.currentCollectionName);
            } else {
                renderCollectionRows(state.currentCollectionRows, state.currentCollectionTotal, query, state.currentCollectionName);
            }
        } else {
            // Append new rows - append to table
            if (repositoryType === 'knowledge') {
                appendKnowledgeChunks(newRows);
            } else {
                appendCollectionRows(newRows);
            }
        }
        
        updateSortIndicators();
    } catch (e) {
        console.error('Failed to fetch collection rows', e);
        if (DOM.ragCollectionTableBody) {
            // Check if it's actually an error or just an empty collection
            const isEmptyCollection = e.message && e.message.includes('not found');
            const message = isEmptyCollection 
                ? 'No rows available.' 
                : `Error loading rows: ${e.message}`;
            const colorClass = isEmptyCollection ? 'text-gray-400' : 'text-rose-400';
            DOM.ragCollectionTableBody.innerHTML = `<tr><td colspan="7" class="px-2 py-2 ${colorClass}">${message}</td></tr>`;
        }
    } finally {
        if (DOM.ragCollectionLoading) {
            DOM.ragCollectionLoading.style.display = 'none';
        }
    }
}

// Export fetchAndRenderCollectionRows so it can be called from other modules (e.g., eventHandlers.js)
export { fetchAndRenderCollectionRows };

/**
 * Append new rows to the table (for infinite scroll pagination)
 */
function appendCollectionRows(newRows) {
    if (!DOM.ragCollectionTableBody || !newRows || newRows.length === 0) return;
    
    newRows.forEach(r => {
        const tr = document.createElement('tr');
        const isSelected = state.currentSelectedCaseId === r.id;
        const selectedClass = isSelected ? 'bg-orange-500/30 ring-1 ring-orange-400/50' : 'border-b border-gray-700';
        tr.className = `${selectedClass} hover:bg-gray-800/40 cursor-pointer transition-colors`;
        
        const efficientBadge = r.is_most_efficient ? '<span class="px-2 py-0.5 rounded bg-green-600 text-white text-xs">Yes</span>' : '<span class="px-2 py-0.5 rounded bg-gray-600 text-white text-xs">No</span>';

        // Convert to number to handle both string and number types from API/ChromaDB
        const feedbackScore = Number(r.user_feedback_score) || 0;
        let feedbackBadge = '<span class="px-2 py-0.5 rounded bg-gray-600 text-white text-xs">—</span>';
        if (feedbackScore === 1) {
            feedbackBadge = '<span class="px-2 py-0.5 rounded bg-green-600 text-white text-xs">👍</span>';
        } else if (feedbackScore === -1) {
            feedbackBadge = '<span class="px-2 py-0.5 rounded bg-red-600 text-white text-xs">👎</span>';
        }
        
        tr.innerHTML = `
            <td class="px-2 py-2 font-mono text-xs text-gray-300 whitespace-nowrap overflow-hidden text-ellipsis" title="${escapeHtml(r.id)}">${escapeHtml(r.id)}</td>
            <td class="px-2 py-2 text-gray-200 whitespace-nowrap overflow-hidden text-ellipsis u-tip" data-tip="${escapeHtml(r.user_query || '')}">${escapeHtml(r.user_query || '')}</td>
            <td class="px-2 py-2 text-gray-300 whitespace-nowrap">${r.strategy_type || ''}</td>
            <td class="px-2 py-2 whitespace-nowrap">${efficientBadge}</td>
            <td class="px-2 py-2 whitespace-nowrap">${feedbackBadge}</td>
            <td class="px-2 py-2 text-gray-300 whitespace-nowrap text-right">${r.output_tokens ?? ''}</td>
            <td class="px-2 py-2 text-gray-400 whitespace-nowrap">${r.timestamp || ''}</td>
        `;
        tr.dataset.caseId = r.id;
        tr.addEventListener('click', () => selectCaseRow(r.id));
        DOM.ragCollectionTableBody.appendChild(tr);
    });
}

function renderCollectionRows(rows, total, query, collectionName) {
    if (!DOM.ragCollectionTableBody) return;
    
    // Restore planner table header
    updateTableHeaderForPlanner();
    
    DOM.ragCollectionTableBody.innerHTML = '';
    
    // Apply sorting to rows
    const sortedRows = [...rows].sort((a, b) => {
        const key = state.ragCollectionSortKey;
        let aVal = a[key];
        let bVal = b[key];
        
        // Handle null/undefined values
        if (aVal == null && bVal == null) return 0;
        if (aVal == null) return state.ragCollectionSortDirection === 'asc' ? 1 : -1;
        if (bVal == null) return state.ragCollectionSortDirection === 'asc' ? -1 : 1;
        
        // Sort logic
        let comparison = 0;
        if (typeof aVal === 'string' && typeof bVal === 'string') {
            comparison = aVal.localeCompare(bVal);
        } else if (typeof aVal === 'number' && typeof bVal === 'number') {
            comparison = aVal - bVal;
        } else if (typeof aVal === 'boolean' && typeof bVal === 'boolean') {
            comparison = aVal === bVal ? 0 : aVal ? 1 : -1;
        } else {
            comparison = String(aVal).localeCompare(String(bVal));
        }
        
        return state.ragCollectionSortDirection === 'asc' ? comparison : -comparison;
    });
    
    if (!sortedRows.length) {
        const message = query && query.length >= 3 ? 'No matches found.' : 'No rows available.';
        DOM.ragCollectionTableBody.innerHTML = `<tr><td colspan="7" class="px-2 py-2 text-gray-400">${message}</td></tr>`;
    } else {
        sortedRows.forEach(r => {
            const tr = document.createElement('tr');
            // Check if this row is currently selected and apply highlight
            const isSelected = state.currentSelectedCaseId === r.id;
            const selectedClass = isSelected ? 'bg-orange-500/30 ring-1 ring-orange-400/50' : 'border-b border-gray-700';
            tr.className = `${selectedClass} hover:bg-gray-800/40 cursor-pointer transition-colors`;
            
            const efficientBadge = r.is_most_efficient ? '<span class="px-2 py-0.5 rounded bg-green-600 text-white text-xs">Yes</span>' : '<span class="px-2 py-0.5 rounded bg-gray-600 text-white text-xs">No</span>';

            // Render feedback badge - convert to number to handle both string and number types from API/ChromaDB
            const feedbackScore = Number(r.user_feedback_score) || 0;
            let feedbackBadge = '<span class="px-2 py-0.5 rounded bg-gray-600 text-white text-xs">—</span>';
            if (feedbackScore === 1) {
                feedbackBadge = '<span class="px-2 py-0.5 rounded bg-green-600 text-white text-xs">👍</span>';
            } else if (feedbackScore === -1) {
                feedbackBadge = '<span class="px-2 py-0.5 rounded bg-red-600 text-white text-xs">👎</span>';
            }
            
            tr.innerHTML = `
                <td class="px-2 py-2 font-mono text-xs text-gray-300 whitespace-nowrap overflow-hidden text-ellipsis" title="${escapeHtml(r.id)}">${escapeHtml(r.id)}</td>
                <td class="px-2 py-2 text-gray-200 whitespace-nowrap overflow-hidden text-ellipsis u-tip" data-tip="${escapeHtml(r.user_query || '')}">${escapeHtml(r.user_query || '')}</td>
                <td class="px-2 py-2 text-gray-300 whitespace-nowrap">${r.strategy_type || ''}</td>
                <td class="px-2 py-2 whitespace-nowrap">${efficientBadge}</td>
                <td class="px-2 py-2 whitespace-nowrap">${feedbackBadge}</td>
                <td class="px-2 py-2 text-gray-300 whitespace-nowrap text-right">${r.output_tokens ?? ''}</td>
                <td class="px-2 py-2 text-gray-400 whitespace-nowrap">${r.timestamp || ''}</td>
            `;
            // Set data attribute AFTER innerHTML so it doesn't get overwritten
            tr.dataset.caseId = r.id;
            tr.addEventListener('click', () => selectCaseRow(r.id));
            DOM.ragCollectionTableBody.appendChild(tr);
        });
    }
    if (DOM.ragCollectionFooter) {
        const mode = query && query.length >= 3 ? 'search results' : 'sample';
        DOM.ragCollectionFooter.textContent = `Showing ${rows.length} ${mode} (collection has ${total} total entries).`;
    }
    
    // Auto-select first row if available
    if (sortedRows.length > 0) {
        selectCaseRow(sortedRows[0].id);
    }
}

export function escapeHtml(str) {
    // Handle non-string input (e.g., arrays from Google Gemini)
    if (Array.isArray(str)) {
        str = str.map(part => {
            if (typeof part === 'string') return part;
            if (part && typeof part === 'object') return part.text || part.thinking || JSON.stringify(part);
            return String(part);
        }).join(' ');
    } else if (str && typeof str !== 'string') {
        str = String(str);
    }
    if (!str) return '';
    return str.replace(/[&<>"]+/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

// ==================== KNOWLEDGE REPOSITORY RENDERING ====================

/**
 * Display knowledge repository metadata in the inspection view
 */
function displayKnowledgeRepositoryMetadata(repo) {
    const metadataSection = document.getElementById('knowledge-repo-metadata');
    if (!metadataSection) return;
    
    // Populate metadata fields
    const descriptionEl = document.getElementById('knowledge-meta-description');
    const docCountEl = document.getElementById('knowledge-meta-doc-count');
    const chunkingEl = document.getElementById('knowledge-meta-chunking');
    const embeddingEl = document.getElementById('knowledge-meta-embedding');
    const chunkSizeEl = document.getElementById('knowledge-meta-chunk-size');
    const overlapEl = document.getElementById('knowledge-meta-overlap');
    const createdEl = document.getElementById('knowledge-meta-created');
    const idEl = document.getElementById('knowledge-meta-id');
    
    if (descriptionEl) {
        descriptionEl.textContent = repo.description || 'Knowledge repository for document storage and retrieval';
    }
    if (chunkingEl) chunkingEl.textContent = repo.chunking_strategy || 'semantic';
    if (embeddingEl) embeddingEl.textContent = repo.embedding_model || 'default';
    if (chunkSizeEl) chunkSizeEl.textContent = repo.chunk_size || 'N/A';
    if (overlapEl) overlapEl.textContent = repo.chunk_overlap || 'N/A';
    if (createdEl) {
        const createdDate = repo.created_at ? new Date(repo.created_at).toLocaleDateString() : 'N/A';
        createdEl.textContent = createdDate;
    }
    if (idEl) {
        idEl.textContent = repo.id || repo.collection_id || 'N/A';
    }
    
    // Fetch document count asynchronously
    if (docCountEl) {
        fetchKnowledgeDocumentCount(repo.id || repo.collection_id, docCountEl);
    }
    
    // Show the metadata section
    metadataSection.classList.remove('hidden');
}

/**
 * Fetch and display document count for knowledge repository
 */
async function fetchKnowledgeDocumentCount(collectionId, targetElement) {
    try {
        const token = localStorage.getItem('tda_auth_token');
        const response = await fetch(`/api/v1/knowledge/repositories/${collectionId}/documents`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });
        
        if (response.ok) {
            const data = await response.json();
            const count = data.documents ? data.documents.length : 0;
            targetElement.textContent = count;
        } else {
            targetElement.textContent = 'N/A';
        }
    } catch (error) {
        console.error('[Knowledge] Failed to fetch document count:', error);
        targetElement.textContent = 'N/A';
    }
}

/**
 * Hide knowledge repository metadata section
 */
function hideKnowledgeRepositoryMetadata() {
    const metadataSection = document.getElementById('knowledge-repo-metadata');
    if (metadataSection) {
        metadataSection.classList.add('hidden');
    }
}

/**
 * Update table header for knowledge repository view
 */
function updateTableHeaderForKnowledge() {
    // Use same selector pattern as wireSortListeners for consistency
    const thead = document.querySelector('#rag-collection-table-body')?.closest('table')?.querySelector('thead');
    const tr = thead?.querySelector('tr');
    if (!tr) return;

    tr.innerHTML = `
        <th class="px-2 py-2 text-left text-gray-300 font-semibold cursor-pointer hover:text-white hover:bg-gray-700/50 transition-colors" data-sort-key="id" style="max-width: 150px;">Chunk ID <span class="text-xs text-gray-500">↕</span></th>
        <th class="px-2 py-2 text-left text-gray-300 font-semibold cursor-pointer hover:text-white hover:bg-gray-700/50 transition-colors" data-sort-key="content" style="max-width: 400px;">Content Preview <span class="text-xs text-gray-500">↕</span></th>
        <th class="px-2 py-2 text-left text-gray-300 font-semibold cursor-pointer hover:text-white hover:bg-gray-700/50 transition-colors" data-sort-key="document_id" style="max-width: 200px;">Source Document <span class="text-xs text-gray-500">↕</span></th>
        <th class="px-2 py-2 text-center text-gray-300 font-semibold cursor-pointer hover:text-white hover:bg-gray-700/50 transition-colors" data-sort-key="chunk_index">Chunk Index <span class="text-xs text-gray-500">↕</span></th>
        <th class="px-2 py-2 text-right text-gray-300 font-semibold cursor-pointer hover:text-white hover:bg-gray-700/50 transition-colors" data-sort-key="token_count">Tokens <span class="text-xs text-gray-500">↕</span></th>
    `;

    // Re-wire sort listeners after replacing innerHTML
    wireSortListeners();
    updateSortIndicators();
}

/**
 * Restore table header for planner repository view
 */
function updateTableHeaderForPlanner() {
    // Use same selector pattern as wireSortListeners for consistency
    const thead = document.querySelector('#rag-collection-table-body')?.closest('table')?.querySelector('thead');
    const tr = thead?.querySelector('tr');
    if (!tr) return;

    tr.innerHTML = `
        <th class="px-2 py-2 text-left text-gray-300 font-semibold cursor-pointer hover:text-white hover:bg-gray-700/50 transition-colors" data-sort-key="id" style="width: 200px;">ID <span class="text-xs text-gray-500">↕</span></th>
        <th class="px-2 py-2 text-left text-gray-300 font-semibold cursor-pointer hover:text-white hover:bg-gray-700/50 transition-colors" data-sort-key="user_query" style="width: 300px;">User Query <span class="text-xs text-gray-500">↕</span></th>
        <th class="px-2 py-2 text-left text-gray-300 font-semibold cursor-pointer hover:text-white hover:bg-gray-700/50 transition-colors" data-sort-key="strategy_type">Strategy <span class="text-xs text-gray-500">↕</span></th>
        <th class="px-2 py-2 text-center text-gray-300 font-semibold cursor-pointer hover:text-white hover:bg-gray-700/50 transition-colors" data-sort-key="is_most_efficient">Efficient <span class="text-xs text-gray-500">↕</span></th>
        <th class="px-2 py-2 text-center text-gray-300 font-semibold cursor-pointer hover:text-white hover:bg-gray-700/50 transition-colors" data-sort-key="user_feedback_score">Feedback <span class="text-xs text-gray-500">↕</span></th>
        <th class="px-2 py-2 text-right text-gray-300 font-semibold cursor-pointer hover:text-white hover:bg-gray-700/50 transition-colors" data-sort-key="output_tokens">Output Tokens <span class="text-xs text-gray-500">↕</span></th>
        <th class="px-2 py-2 text-left text-gray-300 font-semibold cursor-pointer hover:text-white hover:bg-gray-700/50 transition-colors" data-sort-key="timestamp">Timestamp <span class="text-xs text-gray-500">↕</span></th>
    `;

    // Re-wire sort listeners after replacing innerHTML
    wireSortListeners();
    updateSortIndicators();
}

/**
 * Append new knowledge chunks to the table (for infinite scroll pagination)
 */
/**
 * Corpus-screening problem tags for one chunk (labels/counts only — the detectors
 * never return matched values). `screen` = {injection: [labels], pii: {label: n},
 * exfil_urls: n} as attached by the chunks endpoint when the guardrails master is on.
 */
function _screenTagsHtml(screen) {
    if (!screen) return '';
    const chips = [];
    // Red is reserved for what actually GATES a signature. Only injection findings block
    // (knowledge_validator: "PII findings never block signing"), so PII and exfil-shaped
    // URLs are amber however alarming they sound — 55 red exfil chips beside 3 real
    // blockers teaches an operator to discount red, which is the one colour that must
    // keep its meaning. Each tooltip states the consequence, not just the detection.
    (screen.injection || []).forEach(label => chips.push({
        text: label, cls: 'danger',
        tip: `Prompt-injection pattern “${label}” — the same detector the ingest firewall and inbound guardrail use.\nBLOCKS sign-off until this document is quarantined or the finding is acknowledged.`
    }));
    Object.entries(screen.pii || {}).forEach(([label, n]) => chips.push({
        text: `pii:${label}${n > 1 ? ` ×${n}` : ''}`, cls: 'warn',
        tip: 'PII-shaped content (category + count only — matched values are never stored or returned).\nInformational: does NOT block sign-off.'
    }));
    if (screen.exfil_urls) chips.push({
        text: `exfil_url${screen.exfil_urls > 1 ? ` ×${screen.exfil_urls}` : ''}`, cls: 'warn',
        tip: 'Exfiltration-shaped URL (parameterized / data-URI / IP-literal / userinfo) — the outbound guard would defang this if echoed.\nInformational: does NOT block sign-off.'
    });
    return chips.map(c =>
        `<span class="chunk-screen-chip chunk-screen-chip--${c.cls} u-tip" data-tip="${escapeHtml(c.tip)}">${escapeHtml(c.text)}</span>`
    ).join('');
}

/** Severity marker for a flagged chunk row: the red edge means THIS ROW BLOCKS SIGN-OFF.
 *  Only injection does; PII and exfil-shaped URLs are informational (see _screenTagsHtml).
 *  With 235 flagged chunks over 30 documents and 3 real blockers, a red edge that does not
 *  mean "act on this" makes the browser unusable for the one job it is opened for. */
function _markSuspiciousRow(tr, screen) {
    if (!screen) return;
    const danger = (screen.injection || []).length > 0;
    tr.style.boxShadow = `inset 3px 0 0 var(--trust-${danger ? 'danger' : 'warn'}, ${danger ? '#f87171' : '#fbbf24'})`;
}

/** The active screen filter: 'all' | 'flagged' | 'blocking'. */
function _screenFilter() {
    return state.ragScreenFilter || (state.ragSuspiciousOnly ? 'flagged' : 'all');
}

/** Reflect the active segment. Kept as the ONE place the control's state is written, so
 *  the toolbar and the Corpus Health entry point can never disagree about what is shown. */
function _setSuspiciousToggleVisual() {
    const grp = document.getElementById('rag-collection-screen-filter');
    if (!grp) return;
    const active = _screenFilter();
    grp.querySelectorAll('.rag-sf-btn').forEach((b) => {
        b.setAttribute('aria-pressed', b.dataset.sf === active ? 'true' : 'false');
    });
}

/** Stamp live counts into the segments. The three populations were previously reported in
 *  three different units with none stated — the counts ARE the explanation, so they belong
 *  in the control rather than in prose beneath it. */
function _setScreenFilterCounts(counts) {
    const grp = document.getElementById('rag-collection-screen-filter');
    if (!grp || !counts) return;
    grp.querySelectorAll('.rag-sf-btn').forEach((b) => {
        const n = counts[b.dataset.sf];
        let el = b.querySelector('.rag-sf-count');
        if (n === undefined || n === null) { if (el) el.remove(); return; }
        if (!el) { el = document.createElement('span'); el.className = 'rag-sf-count'; b.appendChild(el); }
        el.textContent = Number(n).toLocaleString();
        el.classList.toggle('rag-sf-count--zero', Number(n) === 0);
    });
}

/** Fetch the flagged + blocking counts once per inspection (cheap: totals only, limit=1).
 *  Fail-soft — a missing count leaves the segment unlabelled rather than showing a guess. */
async function _loadScreenFilterCounts(collectionId, allTotal) {
    const counts = { all: allTotal };
    const token = localStorage.getItem('tda_auth_token');
    await Promise.all(['flagged', 'blocking'].map(async (f) => {
        try {
            const r = await fetch(
                `/api/v1/knowledge/collections/${collectionId}/chunks?limit=1&screen_filter=${f}`,
                { headers: { 'Authorization': `Bearer ${token}` } });
            if (!r.ok) return;
            const d = await r.json();
            if (typeof d.total === 'number') counts[f] = d.total;
        } catch (_) { /* leave unlabelled */ }
    }));
    _setScreenFilterCounts(counts);
}

/**
 * Export every flagged chunk of the inspected knowledge repository as a JSON
 * findings report (problem tags + full chunk text — the owner's own corpus).
 */
async function exportSuspiciousChunks() {
    const collectionId = state.currentInspectedCollectionId;
    if (collectionId === null || collectionId === undefined) return;
    const btn = document.getElementById('rag-collection-suspicious-export');
    const prevHtml = btn ? btn.innerHTML : '';
    try {
        if (btn) { btn.disabled = true; btn.textContent = 'Exporting…'; }
        const token = localStorage.getItem('tda_auth_token');
        // Export what is on screen. Exporting "everything flagged" while the operator was
        // looking at the 4 blocking chunks would hand them a 235-row file they then have to
        // re-filter by hand.
        const _sf = _screenFilter() === 'all' ? 'flagged' : _screenFilter();
        const res = await fetch(`/api/v1/knowledge/collections/${collectionId}/chunks?screen_filter=${_sf}&light=false&limit=20000&offset=0`, {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        const report = {
            export: _sf === 'blocking' ? 'blocking-chunks' : 'flagged-chunks',
            screen_filter: _sf,
            collection_id: collectionId,
            collection_name: data.collection_name || state.currentInspectedCollectionName || '',
            exported_at: new Date().toISOString(),
            detectors: 'deterministic corpus screen (prompt-injection patterns, PII shapes, exfiltration-shaped URLs) — same as the ingest firewall',
            total_flagged: data.total || 0,
            scan_capped: data.scan_capped === true,
            chunks: (data.chunks || []).map(c => ({
                id: c.id,
                document_id: c.document_id,
                source_filename: c.metadata?.source_filename || null,
                chunk_index: c.chunk_index,
                screen: c.screen || null,
                content: c.content,
            })),
        };
        const safeName = String(report.collection_name || collectionId).replace(/[^\w.-]+/g, '_').slice(0, 60);
        const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
        window.FilesKeep.deliver(blob, `suspicious-chunks_${safeName}_${new Date().toISOString().slice(0, 10)}.json`,
                                 { what: 'flagged chunks report' });
    } catch (e) {
        console.error('Suspicious-chunk export failed', e);
        if (typeof window.showAppBanner === 'function') {
            window.showAppBanner(`Export failed: ${e.message}`, 'error', 6000);
        }
    } finally {
        if (btn) { btn.disabled = false; btn.innerHTML = prevHtml; }
    }
}

function appendKnowledgeChunks(newChunks) {
    if (!DOM.ragCollectionTableBody || !newChunks || newChunks.length === 0) return;
    
    newChunks.forEach(chunk => {
        const tr = document.createElement('tr');
        const isSelected = state.currentSelectedCaseId === chunk.id;
        const selectedClass = isSelected ? 'bg-orange-500/30 ring-1 ring-orange-400/50' : 'border-b border-gray-700';
        tr.className = `${selectedClass} hover:bg-gray-800/40 cursor-pointer transition-colors`;
        
        // Truncate content for preview
        const contentPreview = chunk.content.length > 100 
            ? chunk.content.substring(0, 100) + '...' 
            : chunk.content;
        
        // Get source filename from metadata
        const sourceDoc = chunk.metadata?.source_filename || chunk.document_id || 'Unknown';
        const screenTags = _screenTagsHtml(chunk.screen);

        tr.innerHTML = `
            <td class="px-2 py-2 font-mono text-xs text-gray-300 whitespace-nowrap overflow-hidden text-ellipsis" style="max-width: 150px;">${escapeHtml(chunk.id)}</td>
            <td class="px-2 py-2 text-gray-200 overflow-hidden text-ellipsis" style="max-width: 400px;">${escapeHtml(contentPreview)}${screenTags ? `<div class="chunk-screen-tags">${screenTags}</div>` : ''}</td>
            <td class="px-2 py-2 text-gray-300 whitespace-nowrap overflow-hidden text-ellipsis" style="max-width: 200px;">${escapeHtml(sourceDoc)}</td>
            <td class="px-2 py-2 text-gray-300 whitespace-nowrap text-center">${chunk.chunk_index ?? '-'}</td>
            <td class="px-2 py-2 text-gray-300 whitespace-nowrap text-right">${chunk.token_count ?? '-'}</td>
        `;
        _markSuspiciousRow(tr, chunk.screen);
        tr.dataset.chunkId = chunk.id;
        tr.addEventListener('click', () => selectKnowledgeChunk(chunk.id));
        DOM.ragCollectionTableBody.appendChild(tr);
    });
}

/**
 * Render knowledge chunks in table (full render, not append)
 */
function renderKnowledgeChunks(chunks, total, query, collectionName) {
    if (!DOM.ragCollectionTableBody) return;
    
    // Update table header for knowledge view
    updateTableHeaderForKnowledge();
    
    DOM.ragCollectionTableBody.innerHTML = '';
    
    if (!chunks.length) {
        const _sf = _screenFilter();
        const message = _sf === 'blocking'
            ? 'Nothing blocks sign-off — no chunk carries a prompt-injection finding.'
            : _sf === 'flagged'
            ? 'No flagged chunks — every scanned chunk passes the deterministic screen.'
            : (query && query.length >= 3 ? 'No matches found.' : 'No chunks available.');
        DOM.ragCollectionTableBody.innerHTML = `<tr><td colspan="5" class="px-2 py-2 text-gray-400">${message}</td></tr>`;
    } else {
        chunks.forEach(chunk => {
            const tr = document.createElement('tr');
            const isSelected = state.currentSelectedCaseId === chunk.id;
            const selectedClass = isSelected ? 'bg-orange-500/30 ring-1 ring-orange-400/50' : 'border-b border-gray-700';
            tr.className = `${selectedClass} hover:bg-gray-800/40 cursor-pointer transition-colors`;
            
            // Truncate content for preview
            const contentPreview = chunk.content.length > 100 
                ? chunk.content.substring(0, 100) + '...' 
                : chunk.content;
            
            // Get source filename from metadata
            const sourceDoc = chunk.metadata?.source_filename || chunk.document_id || 'Unknown';
            const screenTags = _screenTagsHtml(chunk.screen);

            tr.innerHTML = `
                <td class="px-2 py-2 font-mono text-xs text-gray-300 whitespace-nowrap overflow-hidden text-ellipsis" style="max-width: 150px;">${escapeHtml(chunk.id)}</td>
                <td class="px-2 py-2 text-gray-200 overflow-hidden text-ellipsis" style="max-width: 400px;">${escapeHtml(contentPreview)}${screenTags ? `<div class="chunk-screen-tags">${screenTags}</div>` : ''}</td>
                <td class="px-2 py-2 text-gray-300 whitespace-nowrap overflow-hidden text-ellipsis" style="max-width: 200px;">${escapeHtml(sourceDoc)}</td>
                <td class="px-2 py-2 text-gray-300 whitespace-nowrap text-center">${chunk.chunk_index ?? '-'}</td>
                <td class="px-2 py-2 text-gray-300 whitespace-nowrap text-right">${chunk.token_count ?? '-'}</td>
            `;
            _markSuspiciousRow(tr, chunk.screen);
            tr.dataset.chunkId = chunk.id;
            tr.addEventListener('click', () => selectKnowledgeChunk(chunk.id));
            DOM.ragCollectionTableBody.appendChild(tr);
        });
    }
    
    if (DOM.ragCollectionFooter) {
        const _sfF = _screenFilter();
        if (_sfF !== 'all') {
            const capNote = state.ragScanCapped ? ' — scan capped at 20,000 chunks' : '';
            DOM.ragCollectionFooter.textContent = _sfF === 'blocking'
                ? `Showing ${chunks.length} of ${total} chunk(s) that block sign-off (prompt-injection findings${capNote}).`
                : `Showing ${chunks.length} of ${total} flagged chunk(s) — injection, PII and exfil-shaped URLs; only injection blocks sign-off${capNote}.`;
        } else {
            const mode = query && query.length >= 3 ? 'search results' : 'chunks';
            DOM.ragCollectionFooter.textContent = `Showing ${chunks.length} ${mode} (collection has ${total} total chunks).`;
        }
    }

    // Auto-select first chunk if available
    if (chunks.length > 0) {
        selectKnowledgeChunk(chunks[0].id);
    }
}

/**
 * Handle selection of a knowledge chunk row
 */
async function selectKnowledgeChunk(chunkId) {
    if (!chunkId) return;
    
    // Update selected chunk ID (reuse case ID state)
    state.currentSelectedCaseId = chunkId;
    
    // Highlight the selected row
    const allRows = document.querySelectorAll('#rag-collection-table-body tr[data-chunk-id]');
    allRows.forEach(row => {
        if (row.dataset.chunkId === chunkId) {
            row.classList.remove('border-b', 'border-gray-700');
            row.classList.add('bg-orange-500/30', 'ring-1', 'ring-orange-400/50');
        } else {
            row.classList.remove('bg-orange-500/30', 'ring-1', 'ring-orange-400/50');
            row.classList.add('border-b', 'border-gray-700');
        }
    });
    
    // Show the details panel
    if (DOM.ragSelectedCaseDetails) {
        DOM.ragSelectedCaseDetails.classList.remove('hidden');
        if (DOM.ragSelectedCaseSeparator) {
            DOM.ragSelectedCaseSeparator.classList.remove('hidden');
        }
    }
    
    // Show loading state
    if (DOM.ragSelectedCaseMetadata) {
        DOM.ragSelectedCaseMetadata.innerHTML = '<span class="text-gray-400">Loading chunk details...</span>';
    }
    if (DOM.ragSelectedCasePlan) {
        DOM.ragSelectedCasePlan.innerHTML = '<div class="text-gray-400">Loading full content...</div>';
    }
    
    try {
        // Fetch FULL chunk data from dedicated endpoint
        const token = localStorage.getItem('tda_auth_token');
        const collectionId = state.currentInspectedCollectionId;
        const apiUrl = `/api/v1/knowledge/collections/${collectionId}/chunks/${encodeURIComponent(chunkId)}`;
        
        console.log('Fetching full chunk from:', apiUrl);
        
        const res = await fetch(apiUrl, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });
        
        if (!res.ok) {
            throw new Error(`HTTP error! status: ${res.status}`);
        }
        
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        
        const chunk = data.chunk;
        console.log('Received chunk with content length:', chunk?.content?.length);
        
        if (!chunk) {
            if (DOM.ragSelectedCaseMetadata) {
                DOM.ragSelectedCaseMetadata.innerHTML = '<span class="text-rose-400">Chunk not found</span>';
            }
            return;
        }
        
        // Display chunk details
        if (DOM.ragSelectedCaseMetadata) {
            const metadata = chunk.metadata || {};
            const metadataEntries = Object.entries(metadata)
                .filter(([key, value]) => value != null && value !== '')
                .map(([key, value]) => `<strong>${escapeHtml(key)}:</strong> ${escapeHtml(String(value))}`)
                .join(' | ');
            
            const detailScreenTags = _screenTagsHtml(chunk.screen);
            DOM.ragSelectedCaseMetadata.innerHTML = `
                <div class="space-y-2">
                    <div><strong>Chunk ID:</strong> <span class="font-mono text-xs">${escapeHtml(chunkId)}</span></div>
                    <div><strong>Document ID:</strong> <span class="font-mono text-xs">${escapeHtml(chunk.document_id || 'N/A')}</span></div>
                    <div><strong>Chunk Index:</strong> ${chunk.chunk_index ?? 'N/A'}</div>
                    <div><strong>Token Count:</strong> ${chunk.token_count ?? 'N/A'}</div>
                    ${detailScreenTags ? `<div><strong>Screening:</strong> <span class="chunk-screen-tags" style="display:inline-flex;vertical-align:middle;">${detailScreenTags}</span></div>` : ''}
                    ${metadataEntries ? `<div class="text-xs text-gray-500">${metadataEntries}</div>` : ''}
                </div>
            `;
        }
        
        // Display full chunk content
        if (DOM.ragSelectedCasePlan) {
            const contentLength = chunk.content ? chunk.content.length : 0;
            DOM.ragSelectedCasePlan.innerHTML = `
                <h3 class="text-sm font-semibold text-gray-300 mb-2">Full Content (${contentLength} characters)</h3>
                <div class="bg-gray-900/50 rounded-lg p-4 border border-white/10" style="height: 300px; overflow-y: scroll; overflow-x: hidden;">
                    <pre class="text-sm text-gray-200" style="white-space: pre-wrap; word-wrap: break-word; margin: 0; font-family: inherit;">${escapeHtml(chunk.content)}</pre>
                </div>
            `;
        }
        
        // Clear trace section (not applicable for knowledge chunks)
        if (DOM.ragSelectedCaseTrace) {
            DOM.ragSelectedCaseTrace.innerHTML = '';
        }
        
    } catch (error) {
        console.error('Error fetching full chunk:', error);
        if (DOM.ragSelectedCaseMetadata) {
            DOM.ragSelectedCaseMetadata.innerHTML = `<span class="text-rose-400">Error loading chunk: ${escapeHtml(error.message)}</span>`;
        }
    }
}

// ==================== END KNOWLEDGE REPOSITORY RENDERING ====================

async function selectCaseRow(caseId) {
    if (!caseId) return;
    // Update the selected case ID and highlight the row
    updateSelectedCaseId(caseId);
    if (DOM.ragSelectedCaseDetails) {
        DOM.ragSelectedCaseDetails.classList.remove('hidden');
        DOM.ragSelectedCaseMetadata.textContent = 'Loading case details...';
        DOM.ragSelectedCasePlan.innerHTML = '';
        DOM.ragSelectedCaseTrace.innerHTML = '';
        if (DOM.ragSelectedCaseSeparator) {
            DOM.ragSelectedCaseSeparator.classList.remove('hidden');
        }
    }
    try {
        // Get authentication token
        const token = localStorage.getItem('tda_auth_token');
        let caseData = {};
        let turnSummary = null;

        // The collection is part of the address. The same case id exists in every
        // repository the pack was installed into (plus any directory a removed
        // collection left behind), so an unscoped read resolves to whichever copy
        // the filesystem returned first -- which is how inspecting a repository the
        // caller owns could be refused against a copy it does not.
        const inspectedCollectionId = state.currentInspectedCollectionId;
        const caseUrl = (inspectedCollectionId !== null && inspectedCollectionId !== undefined)
            ? `/rag/cases/${encodeURIComponent(caseId)}?collection_id=${encodeURIComponent(inspectedCollectionId)}`
            : `/rag/cases/${encodeURIComponent(caseId)}`;
        // The endpoint reads the collection's own store as well as its case files,
        // so it covers imported collections too. There is deliberately no row-based
        // fallback here: the rows are loaded with `light=true`, which omits
        // `full_case_data`, so the fallback that used to sit here could never fire
        // and turned an honest refusal into an unattributable error.
        const res = await fetch(caseUrl, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });
        const data = await res.json();
        if (!data.error && res.ok) {
            caseData = data.case || {};
            turnSummary = data.session_turn_summary || null;
        } else {
            throw new Error(data.error || `HTTP ${res.status}`);
        }
        if (DOM.ragSelectedCaseMetadata) {
            const meta = caseData.metadata || {};
            
            // --- MODIFICATION START: Create interactive feedback selector ---
            // Use RAG case feedback score (not session turn feedback)
            const ragFeedbackScore = meta.user_feedback_score ?? 0;
            const sessionId = meta.session_id;
            const turnId = meta.turn_id;
            
            let feedbackHtml = '<span class="text-gray-400">N/A (no session data)</span>';
            if (sessionId && turnId !== undefined && turnId !== null) {
                const upActive = ragFeedbackScore === 1 ? 'text-[#F15F22] bg-gray-800/60' : 'text-gray-300';
                const downActive = ragFeedbackScore === -1 ? 'text-[#F15F22] bg-gray-800/60' : 'text-gray-300';
                feedbackHtml = `
                    <div class="inline-flex items-stretch rounded-md bg-gray-900/50 border border-white/10 overflow-hidden backdrop-blur-sm">
                        <button type="button" 
                                class="case-feedback-btn px-2 py-0.5 flex items-center gap-1 ${upActive} hover:text-white hover:bg-gray-800/60 transition text-xs"
                                data-case-id="${escapeHtml(caseData.case_id || caseId)}"
                                data-session-id="${escapeHtml(sessionId)}" 
                                data-turn-id="${turnId}" 
                                data-vote="up"
                                title="Mark as helpful">
                            <svg class='w-3 h-3' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2'>
                                <path d='M14 9V5a3 3 0 00-3-3l-4 9v11h11a3 3 0 003-3v-5a3 3 0 00-3-3h-7'/>
                            </svg>
                            <span class="text-[10px]">Helpful</span>
                        </button>
                        <div class="w-px bg-white/10"></div>
                        <button type="button" 
                                class="case-feedback-btn px-2 py-0.5 flex items-center gap-1 ${downActive} hover:text-white hover:bg-gray-800/60 transition text-xs"
                                data-case-id="${escapeHtml(caseData.case_id || caseId)}"
                                data-session-id="${escapeHtml(sessionId)}" 
                                data-turn-id="${turnId}" 
                                data-vote="down"
                                title="Mark as unhelpful">
                            <svg class='w-3 h-3' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2'>
                                <path d='M10 15v4a3 3 0 003 3l4-9V2H6a3 3 0 00-3 3v5a3 3 0 003 3h7'/>
                            </svg>
                            <span class="text-[10px]">Unhelpful</span>
                        </button>
                    </div>`;
            } else if (ragFeedbackScore === 1) {
                feedbackHtml = '<span class="text-[#F15F22]">👍 Helpful</span>';
            } else if (ragFeedbackScore === -1) {
                feedbackHtml = '<span class="text-[#F15F22]">👎 Unhelpful</span>';
            } else {
                feedbackHtml = '<span class="text-gray-400">None</span>';
            }
            // --- MODIFICATION END ---
            
            // Compute total execution time from trace timestamps (first to last)
            let totalExecutionTime = 'N/A';
            if (turnSummary && Array.isArray(turnSummary.execution_trace) && turnSummary.execution_trace.length > 0) {
                const parseTs = (entry) => {
                    try {
                        const ts = entry?.action?.metadata?.timestamp || entry?.result?.metadata?.timestamp;
                        return ts ? Date.parse(ts) : null;
                    } catch { return null; }
                };
                // Find first and last valid timestamps
                const firstEntry = turnSummary.execution_trace.find(e => parseTs(e) !== null);
                const lastEntry = [...turnSummary.execution_trace].reverse().find(e => parseTs(e) !== null);
                const firstTs = parseTs(firstEntry);
                const lastTs = parseTs(lastEntry);
                if (firstTs !== null && lastTs !== null && lastTs >= firstTs) {
                    totalExecutionTime = ((lastTs - firstTs) / 1000).toFixed(2) + 's';
                }
            }
            // The query this case was seeded from. `intent.user_query` is the case's
            // own copy; the row's value is the same string from ChromaDB metadata and
            // is the fallback when a case document predates the intent block.
            const rowRecord = (state.currentCollectionRows || []).find(r => r.id === caseId) || {};
            const seededQuery = (caseData.intent && caseData.intent.user_query)
                || rowRecord.user_query || '';
            const queryHtml = seededQuery
                ? `<div class='mb-3'>
                       <span class='text-gray-500'>User Query:</span>
                       <div class='mt-1 text-sm text-gray-200 whitespace-pre-wrap break-words'>${escapeHtml(seededQuery)}</div>
                   </div>`
                : `<div class='mb-3'><span class='text-gray-500'>User Query:</span>
                       <span class='text-gray-400'>not recorded on this case</span></div>`;

            DOM.ragSelectedCaseMetadata.innerHTML = `
                ${queryHtml}
                <div class='grid grid-cols-2 md:grid-cols-4 gap-2'>
                    <div><span class='text-gray-500'>Case ID:</span> <code>${escapeHtml(caseData.case_id || caseId)}</code></div>
                    <div><span class='text-gray-500'>Task ID:</span> <code>${escapeHtml(meta.task_id || '')}</code></div>
                    <div><span class='text-gray-500'>Session:</span> <code>${escapeHtml(meta.session_id || '')}</code></div>
                    <div><span class='text-gray-500'>Turn:</span> <code>${escapeHtml(String(meta.turn_id ?? ''))}</code></div>
                    <div><span class='text-gray-500'>Provider:</span> ${escapeHtml(meta.llm_config?.provider || '')}</div>
                    <div><span class='text-gray-500'>Model:</span> ${escapeHtml(meta.llm_config?.model || '')}</div>
                    <div><span class='text-gray-500'>Output Tokens:</span> ${escapeHtml(String(meta.llm_config?.output_tokens ?? ''))}</div>
                    <div><span class='text-gray-500'>Efficient:</span> ${meta.is_most_efficient ? '<span class="text-emerald-400">Yes</span>' : '<span class="text-gray-400">No</span>'}</div>
                    <div><span class='text-gray-500'>User Feedback:</span> ${feedbackHtml}</div>
                </div>
                <div class='mt-2 text-xs text-gray-400'>Total Execution Time: <span class='font-mono text-gray-200'>${totalExecutionTime}</span></div>`;
        }
        if (DOM.ragSelectedCasePlan) {
            const phases = caseData.successful_strategy?.phases || [];
            if (phases.length) {
                const phaseHtml = phases.map(p => `
                    <div class='p-2 rounded-md bg-gray-800/40 border border-white/10'>
                        <div class='text-xs font-semibold text-teradata-orange mb-1'>Phase ${p.phase}</div>
                        <div class='text-sm text-gray-200 mb-1'>Goal: ${escapeHtml(p.goal || '')}</div>
                        <div class='text-xs text-gray-400'>Tools: ${(p.relevant_tools || []).map(t => `<code>${escapeHtml(t)}</code>`).join(', ')}</div>
                    </div>`).join('');
                DOM.ragSelectedCasePlan.innerHTML = `<h3 class='text-white font-semibold mb-2'>Successful Strategy</h3><div class='grid md:grid-cols-2 gap-2'>${phaseHtml}</div>`;
            } else {
                DOM.ragSelectedCasePlan.innerHTML = '<div class="text-gray-400 text-sm">No successful strategy phases.</div>';
            }
        }
        if (DOM.ragSelectedCaseTrace) {
            if (turnSummary && Array.isArray(turnSummary.execution_trace)) {
                // Store raw trace and render via helper for toggling & collapsing
                state.currentCaseTrace = turnSummary.execution_trace.slice();
                renderCaseTrace();
            } else {
                DOM.ragSelectedCaseTrace.innerHTML = '<div class="text-gray-400 text-sm">No execution trace available.</div>';
            }
        }
    } catch (e) {
        console.error('Failed to load case details', e);
        if (DOM.ragSelectedCaseMetadata) {
            // Carry the server's own reason: "Error loading case details." on its own
            // is unattributable, and that is what hid a scoping refusal for months.
            const reason = (e && e.message) ? String(e.message) : '';
            DOM.ragSelectedCaseMetadata.textContent = reason
                ? `Could not load this case: ${reason}`
                : 'Could not load this case.';
        }
    }
}

// Export selectCaseRow so it can be called from other modules (e.g., eventHandlers.js)
export { selectCaseRow };

/**
 * Updates the selected case ID in state and refreshes row highlighting.
 * Called when a case row is clicked or when feedback is updated.
 * @param {string} caseId - The ID of the selected case
 */
function updateSelectedCaseId(caseId) {
    state.currentSelectedCaseId = caseId;
    // Highlight all rows - remove highlighting from all, add to selected
    const allRows = document.querySelectorAll('#rag-collection-table-body tr[data-case-id]');
    allRows.forEach(row => {
        if (row.dataset.caseId === caseId) {
            row.classList.remove('border-b', 'border-gray-700');
            row.classList.add('bg-orange-500/30', 'ring-1', 'ring-orange-400/50');
        } else {
            row.classList.remove('bg-orange-500/30', 'ring-1', 'ring-orange-400/50');
            row.classList.add('border-b', 'border-gray-700');
        }
    });
}

// Export updateSelectedCaseId so it can be called from event handlers
export { updateSelectedCaseId };

/**
 * Updates the feedback badge for a specific row in the table without refreshing the entire table
 * @param {string} caseId - The case ID to update
 * @param {number} feedbackScore - The new feedback score (-1, 0, or 1)
 */
function updateTableRowFeedback(caseId, feedbackScore) {
    const allRows = document.querySelectorAll('#rag-collection-table-body tr');
    let row = null;
    
    // Try exact match first
    for (const r of allRows) {
        if (r.dataset.caseId === caseId) {
            row = r;
            break;
        }
    }
    
    // If not found, try with/without "case_" prefix
    if (!row) {
        const altCaseId = caseId.startsWith('case_') ? caseId.substring(5) : `case_${caseId}`;
        for (const r of allRows) {
            if (r.dataset.caseId === altCaseId) {
                row = r;
                break;
            }
        }
    }
    
    if (!row) {
        console.warn('[updateTableRowFeedback] Row not found for caseId:', caseId);
        return;
    }
    
    console.log('[updateTableRowFeedback] Row found, updating feedback badge');

    // Find the feedback badge cell (5th column, index 4)
    const feedbackCell = row.cells[4];
    if (!feedbackCell) {
        console.warn('[updateTableRowFeedback] Feedback cell not found');
        return;
    }

    // Generate new feedback badge - convert to number to handle both string and number types
    const score = Number(feedbackScore) || 0;
    let feedbackBadge = '<span class="px-2 py-0.5 rounded bg-gray-600 text-white text-xs">—</span>';
    if (score === 1) {
        feedbackBadge = '<span class="px-2 py-0.5 rounded bg-green-600 text-white text-xs">👍</span>';
    } else if (score === -1) {
        feedbackBadge = '<span class="px-2 py-0.5 rounded bg-red-600 text-white text-xs">👎</span>';
    }
    
    // Update the cell
    feedbackCell.innerHTML = feedbackBadge;
    console.log('[updateTableRowFeedback] Feedback badge updated successfully');
}

// Export updateTableRowFeedback so it can be called from event handlers
export { updateTableRowFeedback };

/**
 * Wire up sort click listeners on table headers.
 * Called after thead innerHTML is replaced to restore event listeners.
 */
function wireSortListeners() {
    const thead = document.querySelector('#rag-collection-table-body')?.closest('table')?.querySelector('thead');
    if (!thead) return;

    // After innerHTML replacement, th elements are brand new and have no listeners
    thead.querySelectorAll('th[data-sort-key]').forEach(th => {
        th.addEventListener('click', () => {
            const sortKey = th.dataset.sortKey;
            // Toggle sort direction if clicking same column
            if (state.ragCollectionSortKey === sortKey) {
                state.ragCollectionSortDirection = state.ragCollectionSortDirection === 'asc' ? 'desc' : 'asc';
            } else {
                state.ragCollectionSortKey = sortKey;
                state.ragCollectionSortDirection = 'asc';
            }
            // Fetch sorted data from server (server-side sorting) - reset pagination
            if (state.currentInspectedCollectionId !== null && state.currentInspectedCollectionId !== undefined) {
                fetchAndRenderCollectionRows({
                    collectionId: state.currentInspectedCollectionId,
                    query: state.ragCollectionSearchTerm || '',
                    refresh: true
                });
            }
            updateSortIndicators();
        });
    });
}

/**
 * Updates visual indicators on table headers to show current sort state
 */
function updateSortIndicators() {
    const thead = document.querySelector('#rag-collection-table-body')?.closest('table')?.querySelector('thead');
    if (!thead) return;
    
    thead.querySelectorAll('th[data-sort-key]').forEach(th => {
        const sortKey = th.dataset.sortKey;
        const span = th.querySelector('span');
        if (!span) return;
        
        if (sortKey === state.ragCollectionSortKey) {
            // Show active sort indicator
            const arrow = state.ragCollectionSortDirection === 'asc' ? '↑' : '↓';
            span.textContent = arrow;
            span.className = 'text-xs text-amber-400 font-bold';
            th.classList.add('bg-orange-500/20');
        } else {
            // Show default indicator
            span.textContent = '↕';
            span.className = 'text-xs text-gray-500';
            th.classList.remove('bg-orange-500/20');
        }
    });
}

function renderCaseTrace() {
    if (!DOM.ragSelectedCaseTrace) return;
    const rawTrace = Array.isArray(state.currentCaseTrace) ? state.currentCaseTrace : [];
    let workingTrace = rawTrace;
    if (!state.showSystemLogsInCaseTrace) {
        workingTrace = workingTrace.filter(entry => !(entry.action && entry.action.tool_name === 'TDA_SystemLog'));
    }
    // Collapse duplicates if enabled
    let collapsed = [];
    if (state.collapseDuplicateTraceEntries) {
        for (let i = 0; i < workingTrace.length; i++) {
            const entry = workingTrace[i];
            const prev = collapsed.length ? collapsed[collapsed.length - 1] : null;
            const toolName = entry.action && entry.action.tool_name;
            const message = entry.action && entry.action.arguments && entry.action.arguments.message;
            const status = entry.result && entry.result.status;
            if (prev && prev.__toolName === toolName && prev.__message === message && prev.__status === status) {
                prev.__count += 1;
                prev.entries.push(entry);
            } else {
                const wrapper = { ...entry, __toolName: toolName, __message: message, __status: status, __count: 1, entries: [entry] };
                collapsed.push(wrapper);
            }
        }
    } else {
        collapsed = workingTrace.map(e => ({ ...e, __toolName: e.action && e.action.tool_name, __message: e.action && e.action.arguments && e.action.arguments.message, __status: e.result && e.result.status, __count: 1, entries: [e] }));
    }

    // Timing calculations
    const parseTs = ts => {
        try { return ts ? Date.parse(ts) : null; } catch { return null; }
    };
    const firstTs = collapsed.length ? parseTs(collapsed[0].action && collapsed[0].action.metadata && collapsed[0].action.metadata.timestamp) : null;

    const html = collapsed.map(block => {
        const action = block.action || {}; const result = block.result || {};
        const tool = block.__toolName || 'unknown';
        const status = block.__status || 'info';
        const msg = block.__message || '';
        const tsRaw = action.metadata && action.metadata.timestamp;
        const currentTs = parseTs(tsRaw);
        let elapsed = '-';
        let delta = '-';
        if (currentTs && firstTs) {
            elapsed = ((currentTs - firstTs) / 1000).toFixed(2) + 's';
        }
        // Find previous distinct block timestamp for delta
        const idx = collapsed.indexOf(block);
        if (idx > 0) {
            const prevTsRaw = collapsed[idx - 1].action && collapsed[idx - 1].action.metadata && collapsed[idx - 1].action.metadata.timestamp;
            const prevTs = parseTs(prevTsRaw);
            if (currentTs && prevTs) {
                delta = ((currentTs - prevTs) / 1000).toFixed(2) + 's';
            }
        }
        const repeatBadge = block.__count > 1 ? `<span class='ml-2 px-2 py-0.5 rounded bg-blue-600/60 text-[10px]'>x${block.__count}</span>` : '';
        const timingBadge = `<span class='text-[10px] text-gray-500'>t=${elapsed} Δ=${delta}</span>`;
        return `<div class='text-xs p-2 rounded bg-gray-900/40 border border-gray-700/40'>
            <div class='flex justify-between items-center'><span class='font-mono text-gray-300'>${escapeHtml(tool)}</span><span class='text-[10px] ${status === 'error' ? 'text-rose-400' : 'text-emerald-400'}'>${escapeHtml(status)}</span></div>
            <div class='text-gray-400 mt-1'>${escapeHtml(msg)}</div>
            <div class='flex justify-between mt-1'>${timingBadge}${repeatBadge}</div>
        </div>`;
    }).join('');

    DOM.ragSelectedCaseTrace.innerHTML = `<h3 class='text-white font-semibold mb-2 flex items-center gap-4'>Execution Trace
        <label class='flex items-center gap-1 text-[11px] font-normal'><input type='checkbox' id='toggle-system-logs' ${state.showSystemLogsInCaseTrace ? 'checked' : ''}>System Logs</label>
        <label class='flex items-center gap-1 text-[11px] font-normal'><input type='checkbox' id='toggle-collapse-dupes' ${state.collapseDuplicateTraceEntries ? 'checked' : ''}>Collapse Duplicates</label>
    </h3>
    <div class='space-y-1 max-h-64 overflow-y-auto pr-1'>${html}</div>`;

    const sysToggle = document.getElementById('toggle-system-logs');
    const dupesToggle = document.getElementById('toggle-collapse-dupes');
    if (sysToggle) {
        sysToggle.onchange = () => { state.showSystemLogsInCaseTrace = sysToggle.checked; renderCaseTrace(); };
    }
    if (dupesToggle) {
        dupesToggle.onchange = () => { state.collapseDuplicateTraceEntries = dupesToggle.checked; renderCaseTrace(); };
    }
}

/**
 * Copies the session ID to the clipboard.
 * @param {string} sessionId - The session ID to copy.
 */
export function copySessionIdToClipboard(sessionId) {
    navigator.clipboard.writeText(sessionId).then(() => {
    }).catch(err => {
        console.error('Failed to copy session ID:', err);
    });
}

/**
 * Load document count for a Knowledge repository
 */
async function loadKnowledgeRepositoryDocCount(collectionId) {
    try {
        const token = localStorage.getItem('tda_auth_token');
        const response = await fetch(`/api/v1/knowledge/repositories/${collectionId}/documents`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });
        
        if (response.ok) {
            const data = await response.json();
            const count = data.documents ? data.documents.length : 0;
            const countEl = document.getElementById(`doc-count-${collectionId}`);
            if (countEl) {
                countEl.textContent = `${count} document${count !== 1 ? 's' : ''}`;
            }
        }
    } catch (error) {
        console.error(`Failed to load document count for collection ${collectionId}:`, error);
    }
}

/**
 * Open Knowledge repository view to show documents and details
 */
function openKnowledgeRepositoryView(collection) {
    console.log('[Knowledge] Opening repository view:', collection);
    
    // Store current repository in state
    state.currentKnowledgeRepository = collection;
    
    // For now, show an alert with details - we'll create a proper view modal later
    const backendLabel = _getBackendLabel(collection.backend_type || 'chromadb');
    const msg = `Knowledge Repository: ${collection.name}\n\n` +
                `Description: ${collection.description || 'N/A'}\n` +
                `Chunking: ${collection.chunking_strategy || 'semantic'}\n` +
                `Collection ID: ${collection.id}\n` +
                `Backend: ${backendLabel} (${collection.collection_name})\n\n` +
                `View modal coming soon...`;
    
    // Using info banner as temporary solution until proper modal is implemented
    showAppBanner('Repository details view coming soon', 'info');
    console.log(msg);
    
    // NOTE: Repository details modal deferred to future enhancement.
    // Planned features: document list, upload dates, chunking preview, bulk operations.
    // Current implementation provides basic view functionality via UI.
}

/**
 * Delete a Knowledge repository
 */
export async function deleteKnowledgeRepository(collectionId, collectionName) {
    // Use custom confirmation modal (same as planner repositories)
    if (!window.showConfirmation) {
        console.error('Confirmation system not available');
        return;
    }

    showConfirmation(
        'Delete Knowledge Repository',
        `Are you sure you want to delete the repository "${collectionName}"?\n\nThis will remove all documents and cannot be undone.`,
        async () => {
            try {
                const token = localStorage.getItem('tda_auth_token');
                const response = await fetch(`/api/v1/rag/collections/${collectionId}`, {
                    method: 'DELETE',
                    headers: {
                        'Authorization': `Bearer ${token}`
                    }
                });

                const data = await response.json();

                if (response.ok) {
                    showAppBanner(`Repository "${collectionName}" deleted successfully`, 'success');
                    // Reload the collections list
                    await loadRagCollections();
                } else {
                    showAppBanner(`Failed to delete repository: ${data.message || data.error || 'Unknown error'}`, 'error');
                }
            } catch (error) {
                console.error('[Knowledge] Error deleting repository:', error);
                showAppBanner(`Error deleting repository: ${error.message}`, 'error');
            }
        }
    );
}

/**
 * Switch between Components sub-tabs (User Interface / Connectors).
 * Called by onclick handlers on the tab buttons in the components-view.
 */
window.switchPlatformComponentsTab = function switchPlatformComponentsTab(tabName) {
    // Update tab button active states
    document.querySelectorAll('.platform-component-tab').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tabName);
    });

    // Show/hide content panes
    document.querySelectorAll('.platform-component-content').forEach(pane => {
        pane.classList.add('hidden');
    });
    const target = document.getElementById(
        tabName === 'mcp-servers' ? 'platform-content-mcp-servers' : 'platform-content-ui-components'
    );
    if (target) target.classList.remove('hidden');

    // Show/hide header action areas
    const uiActions = document.getElementById('platform-ui-components-actions');
    const connectorActions = document.getElementById('platform-connector-actions');
    if (uiActions) uiActions.classList.toggle('hidden', tabName === 'mcp-servers');
    if (connectorActions) {
        connectorActions.classList.toggle('hidden', tabName !== 'mcp-servers');
        // flex is needed when not hidden
        if (tabName === 'mcp-servers') {
            connectorActions.classList.add('flex');
        } else {
            connectorActions.classList.remove('flex');
        }
    }

    // Lazy-load connectors panel on first activation
    if (tabName === 'mcp-servers' && typeof window.loadPlatformConnectorPanel === 'function') {
        window.loadPlatformConnectorPanel();
    }
};

// ═══════════════════════════════════════════════════════════════════════════
// S1 — The Governed Stream: the provisional answer bubble.
//
// `answer_delta` events append provisional text to ONE bubble that is replaced,
// wholesale, by the authoritative `final_answer` (`addMessage`). The bubble is
// deliberately NOT an assistant message: it carries no turn badge and no
// `[data-turn-id]`, so the capsule/pin/feedback observers leave it alone, and it
// never enters history — history is written by the server, once, from the final.
//
// Rendering is batched (one markdown parse per animation frame at most) and the
// view follows the text only while the reader is at the bottom — scrolling up
// releases the follow lock, scrolling back to the bottom re-engages it.
// ═══════════════════════════════════════════════════════════════════════════
const _prov = { el: null, content: null, text: '', seq: 0, msg: 0, raf: 0, follow: true,
                scroller: null, onScroll: null, sessionId: null, html: null, htmlUpto: 0 };

// The provisional text in the FINAL answer's shape. The server renders the stable part with
// the very formatter the final uses (`answer_delta.html`, `html_upto`); only the line being
// written right now is drawn here, as the paragraph it will become. So the answer the reader
// watches is the answer they keep — the final lands in place instead of redrawing it.
const _PROV_P = 'text-gray-300 mb-4';
const _PROV_UL = '<ul class="list-disc list-outside space-y-2 text-gray-300 mb-4 pl-5">';
function _provEsc(t) { return String(t).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
// ── the client mirror of formatter.md_inline + the block rules, for the line still being
// written. test/test_markdown_render.py executes both and holds them equal: whatever the
// final answer will render, the streamed line renders the same way.
const _PROV_CODE = 'bg-gray-900/70 text-teradata-orange rounded-md px-1.5 py-0.5 font-mono text-sm';
const _PROV_W = '\\p{L}\\p{N}_';
const _provRx = (src) => new RegExp(src, 'gu');
const _PROV_ESCAPABLE = _provRx('\\\\([\\\\`*_{}\\[\\]()#+\\-.!~|<>])');
const _PROV_CODE_RX = _provRx('(`+)(.+?)(?<!`)\\1(?!`)');
const _PROV_AUTOLINK = new RegExp('<((?:https?://|mailto:)[^\\s<>]+)>', 'giu');
const _PROV_IMAGE = _provRx('!\\[([^\\[\\]]*)\\]\\(\\s*<?([^\\s<>()]+)>?(?:\\s+"[^"]*")?\\s*\\)');
const _PROV_LINK = _provRx('\\[([^\\[\\]]+)\\]\\(\\s*<?([^\\s<>()]+)>?(?:\\s+"[^"]*")?\\s*\\)');
const _PROV_ENTITY = _provRx('&(?!(?:#\\d{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});)');
const _PROV_SAFE_TAG = new RegExp('&lt;(/?)(br|b|i|em|strong|sub|sup|u|s|del|kbd)\\s*/?&gt;', 'giu');
const _PROV_EMPHASIS = [
    [_provRx(`(?<![${_PROV_W}*])\\*\\*\\*(?![\\s*])(.+?)(?<![\\s*])\\*\\*\\*(?![${_PROV_W}*])`), '<strong><em>$1</em></strong>'],
    [_provRx(`(?<![${_PROV_W}])___(?![\\s_])(.+?)(?<![\\s_])___(?![${_PROV_W}])`), '<strong><em>$1</em></strong>'],
    [_provRx('(?<!\\*)\\*\\*(?![\\s*])(.+?)(?<![\\s*])\\*\\*(?!\\*)'), '<strong>$1</strong>'],
    [_provRx(`(?<![${_PROV_W}])__(?![\\s_])(?![a-z0-9_]+__(?![${_PROV_W}]))(.+?)(?<![\\s_])__(?![${_PROV_W}])`), '<strong>$1</strong>'],
    [_provRx(`(?<![${_PROV_W}*])\\*(?![\\s*])(.+?)(?<![\\s*])\\*(?![${_PROV_W}*])`), '<em>$1</em>'],
    [_provRx(`(?<![${_PROV_W}])_(?![\\s_])(.+?)(?<![\\s_])_(?![${_PROV_W}])`), '<em>$1</em>'],
    [_provRx('~~(?![\\s~])(.+?)(?<![\\s~])~~'), '<del>$1</del>'],
];
function _provEscText(t) {
    t = String(t).replace(_PROV_ENTITY, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return t.replace(_PROV_SAFE_TAG, (m, sl, tag) => `<${sl}${tag.toLowerCase()}>`);
}
function _provAttr(t) { return String(t).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
function _provLink(href, labelHtml) {
    if (!/^(https?:\/\/|mailto:)/i.test(href)) return labelHtml;
    return `<a class="ans-md-link" href="${_provAttr(href)}" target="_blank" rel="noopener noreferrer">${labelHtml}</a>`;
}
function _provInline(text) {
    // formatter.md_inline, applied to the line in progress
    const held = [];
    const hold = (html) => { held.push(html); return `\u0000${held.length - 1}\u0000`; };
    let t = String(text == null ? '' : text).replace(/\u0000/g, '');
    t = t.replace(_PROV_ESCAPABLE, (m, c) => hold(_provEscText(c)));
    t = t.replace(_PROV_CODE_RX, (m, ticks, body) => hold(`<code class="${_PROV_CODE}">${_provEscText(body)}</code>`));
    t = t.replace(_PROV_AUTOLINK, (m, href) => hold(_provLink(href, _provEscText(href))));
    t = t.replace(_PROV_IMAGE, (m, alt, href) => hold(_provLink(href, _provEscText(alt || href))));
    t = t.replace(_PROV_LINK, (m, label, href) => hold(_provLink(href, _provInline(label))));
    t = _provEscText(t);
    for (const [rx, rep] of _PROV_EMPHASIS) t = t.replace(rx, rep);
    for (let guard = 0; t.includes('\u0000') && guard < 50; guard++) {
        const nxt = t.replace(/\u0000(\d+)\u0000/g, (m, n) => held[Number(n)]);
        if (nxt === t) break;
        t = nxt;
    }
    return t;
}
const _PROV_OL_OPEN = '<ol class="list-decimal list-outside space-y-2 text-gray-300 mb-4 pl-5"';
const _PROV_QUOTE = '<blockquote class="ans-md-quote">';
function _provTailLine(line) {
    // one complete-or-partial line → the block it will become (formatter._render_standard_markdown)
    if (/^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/.test(line) || /^={3,}\s*$/.test(line)) return '<hr class="border-gray-600 my-4">';
    const h = /^(#{1,6})\s+(.*?)(?:\s+#+)?\s*$/.exec(line);
    const li = /^(?:([*+-])|(\d{1,9})[.)])\s+(.*)$/.exec(line);
    const q = (!li) ? /^>\s?(.*)$/.exec(line) : null;
    if (li) {
        const body = li[3].trim();
        const task = /^\[([ xX])\]\s+(.*)$/.exec(body);
        const item = task
            ? `<li class="ans-md-task"><input type="checkbox" class="ans-md-task-box" disabled${task[1].toLowerCase() === 'x' ? ' checked' : ''} aria-label="${task[1].toLowerCase() === 'x' ? 'done' : 'open'}"><span>${_provInline(task[2])}</span></li>`
            : (body ? `<li>${_provInline(body)}</li>` : '');
        if (li[2]) { const n = Number(li[2]); return `${_PROV_OL_OPEN}${n !== 1 ? ` start="${n}"` : ''}>${item}</ol>`; }
        return `${_PROV_UL}${item}</ul>`;
    }
    if (q) return `${_PROV_QUOTE}${q[1].trim() ? _provTailLine(q[1].replace(/^ +/, '')) : ''}</blockquote>`;
    if (h) return h[1].length === 1 ? `<h2 class="text-xl font-bold text-white mb-3 border-b border-gray-700 pb-2">${_provInline(h[2].trim())}</h2>`
                : h[1].length === 2 ? `<h3 class="text-lg font-bold text-white mb-3 border-b border-gray-700 pb-2">${_provInline(h[2].trim())}</h3>`
                : `<h4 class="text-base font-semibold text-white mt-4 mb-2">${_provInline(h[2].trim())}</h4>`;
    return `<p class="${_PROV_P}">${_provInline(line)}</p>`;
}
function _provJoinBlocks(html) {
    // consecutive single-line blocks of one list / quotation are ONE block, as in the final
    return html
        .split(`</ul>${_PROV_UL}`).join('')
        .replace(/<\/ol><ol class="list-decimal list-outside space-y-2 text-gray-300 mb-4 pl-5"(?: start="\d+")?>/g, '')
        .split(`</blockquote>${_PROV_QUOTE}`).join('');
}
function _provTailHtml(tail) {
    if (!tail) return '';
    const fence = tail.indexOf('```');
    const prose = fence >= 0 ? tail.slice(0, fence) : tail;
    let out = '';
    for (const raw of prose.split('\n')) {
        const line = raw.replace(/^ +/, '');
        if (!line.trim() || /^\|/.test(line)) continue;           // a table still forming is held
        out += _provTailLine(line);
    }
    out = _provJoinBlocks(out);
    if (fence >= 0) {
        const body = tail.slice(fence).replace(/^```[^\n]*\n?/, '');
        out += `<div class="sql-code-block mb-4"><pre><code>${_provEsc(body)}</code></pre></div>`;
    }
    return out;
}
function _provComposeHtml() {
    if (_prov.html == null) return null;               // an older server / the genie lane: markdown here
    const upto = Math.min(_prov.htmlUpto || 0, _prov.text.length);
    let head = _prov.html;
    let tail = _provTailHtml(_prov.text.slice(upto));
    // a bullet / numbered item / quoted line being written belongs to the block above it —
    // as it will once its line completes
    if (tail.startsWith(_PROV_UL) && /<\/ul>\s*$/.test(head)) {
        head = head.replace(/<\/ul>\s*$/, '');
        tail = tail.slice(_PROV_UL.length);
    } else if (tail.startsWith(_PROV_OL_OPEN) && /<\/ol>\s*$/.test(head)) {
        head = head.replace(/<\/ol>\s*$/, '');
        tail = tail.slice(tail.indexOf('>') + 1);
    } else if (tail.startsWith(_PROV_QUOTE) && /<\/blockquote>\s*$/.test(head)) {
        head = head.replace(/<\/blockquote>\s*$/, '');
        tail = tail.slice(_PROV_QUOTE.length);
    }
    return `<div class="response-card summary-card mb-4">${head}${tail}</div>`;
}

function _provScroller() {
    return document.getElementById('chat-container') || (DOM.chatLog && DOM.chatLog.parentElement);
}

function _provAtBottom(sc) {
    return sc ? (sc.scrollHeight - sc.scrollTop - sc.clientHeight) < 48 : true;
}

function _provPill(show) {
    // "↓ new text" — the mail-client convention: the view never hijacks scroll; the
    // reader who scrolled up is told, once, that the answer grew, and one click returns.
    let pill = document.getElementById('answer-stream-pill');
    if (!show) { if (pill) pill.remove(); return; }
    if (pill) return;
    const sc = _prov.scroller; if (!sc) return;
    pill = document.createElement('button');
    pill.id = 'answer-stream-pill'; pill.type = 'button'; pill.className = 'answer-stream-pill';
    pill.setAttribute('aria-label', 'New text arrived — jump to the latest');
    pill.innerHTML = '<svg width="12" height="12" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M10 4v12m0 0l-5-5m5 5l5-5"/></svg><span>new text</span>';
    pill.addEventListener('click', () => { _prov.follow = true; if (_prov.scroller) _prov.scroller.scrollTop = _prov.scroller.scrollHeight; _provPill(false); });
    sc.appendChild(pill);   // last child of the scroller: sticky at its bottom edge while scrolled up
}

function _provAnnounce(text) {
    // Announced ONCE per event to assistive tech; the streaming text itself is never
    // read a word at a time (the region is aria-busy while it streams).
    let live = document.getElementById('answer-stream-status');
    if (!live) {
        live = document.createElement('div'); live.id = 'answer-stream-status'; live.className = 'sr-only';
        live.setAttribute('role', 'status'); live.setAttribute('aria-live', 'polite');
        document.body.appendChild(live);
    }
    live.textContent = ''; setTimeout(() => { live.textContent = text; }, 30);
}

function _provRender() {
    _prov.raf = 0;
    if (!_prov.content) return;
    let rendered = false;
    const composed = _provComposeHtml();
    // S1.1 — the Living Answer renders the same text with a stable block model (a table once
    // its header row is closed, a fence once it closes) and lets only the NEW characters rise.
    try {
        if (_prov.el && _prov.el.dataset && _prov.el.dataset.living && window.LivingAnswer && window.LivingAnswer.renderText) {
            rendered = !!window.LivingAnswer.renderText(_prov.content, _prov.text, composed);
        }
    } catch (_) { rendered = false; }
    if (!rendered) {
        let html = composed;
        if (html == null) {
            try {
                html = (typeof marked !== 'undefined') ? marked.parse(_prov.text) : _prov.text.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
            } catch (_) { html = _prov.text; }
        }
        _prov.content.innerHTML = html;
    }
    // The caret rides at the end of the last text block (a trailing block-level
    // element would put it on a line of its own), so it reads as a typing cursor.
    const caret = document.createElement('span');
    caret.className = 'answer-stream-caret'; caret.setAttribute('aria-hidden', 'true');
    let host = _prov.content;
    while (host.lastElementChild && /^(P|LI|H[1-6]|BLOCKQUOTE|TD|PRE|UL|OL|DIV)$/.test(host.lastElementChild.tagName)) host = host.lastElementChild;
    host.appendChild(caret);
    if (_prov.follow && _prov.scroller) _prov.scroller.scrollTop = _prov.scroller.scrollHeight;
    else if (_prov.scroller) _provPill(true);
}

function _provSchedule() {
    if (_prov.raf) return;
    _prov.raf = requestAnimationFrame(_provRender);
}

/** Start (or restart) the provisional bubble for the current stream. */
export function beginProvisionalAnswer(sessionId) {
    finishProvisionalAnswer();
    // A bubble restored from a session-switch snapshot is a stale copy: drop it.
    try { DOM.chatLog.querySelectorAll('[data-provisional]').forEach(n => n.remove()); } catch (_) {}
    // S1.1 — the Living Answer was born at task_start: stream INTO its bubble (one bubble,
    // never two). Absent (switch off, opted out, script missing) ⇒ the plain S1 bubble below.
    try {
        const host = (window.LivingAnswer && window.LivingAnswer.contentHost) ? window.LivingAnswer.contentHost(sessionId) : null;
        if (host && host.wrapper && host.content) {
            _prov.el = host.wrapper; _prov.content = host.content; _prov.text = ''; _prov.seq = 0; _prov.msg = 0;
            _prov.html = null; _prov.htmlUpto = 0;
            _prov.sessionId = sessionId || null;
            _prov.scroller = _provScroller();
            // The Living Answer's follow lock is the truth here: it was armed when the
            // reader submitted and released only by their own upward scroll — "at the
            // bottom right now" is false whenever the working phase grew the bubble past
            // the fold, and that is exactly the turn the reader most wants followed.
            _prov.follow = _provAtBottom(_prov.scroller) || !!(window.LivingAnswer.following && window.LivingAnswer.following());
            if (_prov.scroller) {
                if (_prov.follow) _prov.scroller.scrollTop = _prov.scroller.scrollHeight;
                _prov.onScroll = () => { _prov.follow = _provAtBottom(_prov.scroller); if (_prov.follow) _provPill(false); };
                _prov.scroller.addEventListener('scroll', _prov.onScroll, { passive: true });
            }
            _provSchedule();
            return;
        }
    } catch (_) { /* the plain bubble is always available */ }
    if (window.hideWelcomeScreen) window.hideWelcomeScreen();
    const wrapper = document.createElement('div');
    wrapper.className = 'message-bubble group flex items-start gap-4 answer-provisional';
    wrapper.setAttribute('aria-busy', 'true');
    wrapper.dataset.provisional = '1';
    const icon = document.createElement('div');
    icon.className = 'flex-shrink-0 w-10 h-10 rounded-full flex items-center justify-center text-white font-bold shadow-lg assistant-avatar-icon';
    icon.textContent = 'A';
    icon.style.background = 'var(--teradata-orange, #F15F22)';
    const box = document.createElement('div');
    box.className = 'p-4 rounded-xl shadow-lg max-w-3xl glass-panel relative flex-1 min-w-0';
    box.style.background = 'var(--card-bg, rgba(20, 22, 25, 0.6))';
    const sr = document.createElement('span');
    sr.className = 'sr-only';
    sr.textContent = 'The answer is being written.';
    const author = document.createElement('p');
    author.className = 'font-bold mb-2 text-sm';
    author.textContent = 'Assistant';
    author.style.color = 'var(--teradata-orange, #F15F22)';
    const content = document.createElement('div');
    content.className = 'message-content';
    box.appendChild(author);
    box.appendChild(sr);
    box.appendChild(content);
    wrapper.appendChild(icon);
    wrapper.appendChild(box);
    DOM.chatLog.appendChild(wrapper);
    _prov.el = wrapper; _prov.content = content; _prov.text = ''; _prov.seq = 0; _prov.msg = 0;
    _prov.html = null; _prov.htmlUpto = 0;
    _prov.sessionId = sessionId || null;
    _prov.scroller = _provScroller();
    _prov.follow = _provAtBottom(_prov.scroller);
    if (_prov.scroller) {
        _prov.onScroll = () => { _prov.follow = _provAtBottom(_prov.scroller); if (_prov.follow) _provPill(false); };
        _prov.scroller.addEventListener('scroll', _prov.onScroll, { passive: true });
    }
    _provSchedule();
}

/** One `answer_delta` payload: {seq, message_index, text} or {reset:true}. Out-of-order
 *  or stale deltas (from a withdrawn message) are dropped — the final answer is authoritative. */
export function provisionalAnswerDelta(payload, sessionId) {
    if (!payload) return;
    if (!_prov.el || !_prov.el.isConnected) beginProvisionalAnswer(sessionId);
    const mi = Number(payload.message_index || 0);
    if (payload.reset) {
        if (mi > _prov.msg) { _prov.msg = mi; _prov.text = ''; _prov.html = null; _prov.htmlUpto = 0; _provSchedule(); }
        return;
    }
    if (mi < _prov.msg) return;              // text for a message that was withdrawn
    if (mi > _prov.msg) { _prov.msg = mi; _prov.text = ''; _prov.html = null; _prov.htmlUpto = 0; }
    const seq = Number(payload.seq || 0);
    if (seq && seq <= _prov.seq) return;     // duplicate delivery
    if (seq) _prov.seq = seq;
    const _wasEmpty = !_prov.text;
    _prov.text += String(payload.text || '');
    if (typeof payload.html === 'string') { _prov.html = payload.html; _prov.htmlUpto = Number(payload.html_upto || 0); }
    // S1.1 — the first token is the fold: chips converge into the summary line
    if (_wasEmpty && _prov.text && window.LivingAnswer && window.LivingAnswer.firstToken) { try { window.LivingAnswer.firstToken(_prov.sessionId || sessionId); } catch (_) {} }
    _provSchedule();
}

/** Remove the provisional bubble — the authoritative message replaces it.
 *  `outcome`: 'complete' | 'partial' → announced once to assistive tech; absent = silent. */
export function finishProvisionalAnswer(outcome) {
    if (outcome === 'complete') _provAnnounce('Answer complete.');
    else if (outcome === 'partial') _provAnnounce('Answer stopped — the partial answer is kept.');
    _provPill(false);
    if (_prov.raf) { cancelAnimationFrame(_prov.raf); _prov.raf = 0; }
    if (_prov.scroller && _prov.onScroll) _prov.scroller.removeEventListener('scroll', _prov.onScroll);
    let _keep = false;
    // S1.1 — a Living Answer bubble stays for the settle (its rail, summary and steps move
    // onto the authoritative message); with no outcome (error, cancel) it is dropped.
    try {
        if (_prov.el && _prov.el.dataset && _prov.el.dataset.living && window.LivingAnswer && window.LivingAnswer.textDone) {
            _keep = !!window.LivingAnswer.textDone(_prov.el.dataset.living, outcome);
            const _c = _prov.el.querySelector('.answer-stream-caret'); if (_c) _c.remove();
        }
    } catch (_) { _keep = false; }
    if (!_keep && _prov.el && _prov.el.parentNode) _prov.el.parentNode.removeChild(_prov.el);
    _prov.el = null; _prov.content = null; _prov.text = ''; _prov.onScroll = null; _prov.scroller = null;
    _prov.html = null; _prov.htmlUpto = 0;
}

// ── Landing the final IN PLACE ─────────────────────────────────────────────────────────
// The streamed answer and the authoritative one are different DOM nodes, so the swap must
// not read as a new message arriving: no entrance animation, and the line the reader's eye
// is on stays on the same pixel row. The anchor is taken from the streamed text BEFORE it is
// dropped (the line being written when the view follows, else the line at the reading point)
// and found again in the final by its words; a line the final no longer carries falls back
// to the answer's own top edge. Nothing scrolls to the bottom after the landing — the extras
// the final adds (marks, notes, table acts, the verdict row) appear where they are.
const _ANCHOR_SEL = 'p, li, h1, h2, h3, h4, h5, h6, pre, tr, dt, dd, blockquote';
function _anchorNorm(el) { return (el.textContent || '').replace(/\s+/g, ' ').trim(); }
function _anchorBlocks(root) {
    if (!root) return [];
    const all = [...root.querySelectorAll(_ANCHOR_SEL)];
    return all.filter(el => !el.querySelector(_ANCHOR_SEL) && !el.closest('.la-line, .la-proc'));
}
const _ANCHOR_KEY = 40;
function _anchorKey(t) { return t.slice(0, _ANCHOR_KEY); }

/** The reading anchor of the streamed answer — call BEFORE `finishProvisionalAnswer`. */
export function captureReadingAnchor() {
    try {
        const content = _prov.content, sc = _prov.scroller || _provScroller();
        if (!content || !sc || !content.isConnected) return null;
        const vr = sc.getBoundingClientRect();
        const blocks = _anchorBlocks(content).filter(el => _anchorNorm(el).length >= 12);
        const cr = content.getBoundingClientRect();
        let pick = null;
        if (_prov.follow) pick = blocks[blocks.length - 1] || null;
        if (!pick) {
            const focal = vr.top + vr.height * 0.4;
            let best = Infinity;
            for (const el of blocks) {
                const r = el.getBoundingClientRect();
                if (r.bottom < vr.top || r.top > vr.bottom) continue;
                const d = r.top <= focal && r.bottom >= focal ? 0 : Math.min(Math.abs(r.top - focal), Math.abs(r.bottom - focal));
                if (d < best) { best = d; pick = el; }
            }
        }
        const a = { answerTop: cr.top, sc };
        if (pick) {
            const key = _anchorKey(_anchorNorm(pick));
            a.key = key;
            a.occ = blocks.slice(0, blocks.indexOf(pick)).filter(el => _anchorKey(_anchorNorm(el)) === key).length;
            a.top = pick.getBoundingClientRect().top;
        }
        return a;
    } catch (_) { return null; }
}

/** Land the authoritative message where the streamed one stood (see above). */
export function landInPlace(wrapper, anchor) {
    if (!wrapper) return;
    try { wrapper.classList.add('msg-in-place'); } catch (_) { /* no-op */ }
    if (!anchor || !anchor.sc) return;
    try {
        const content = wrapper.querySelector('.message-content');
        if (!content) return;
        let target = null, was = anchor.answerTop;
        if (anchor.key) {
            const hits = _anchorBlocks(content).filter(el => {
                const k = _anchorKey(_anchorNorm(el)), n = Math.min(k.length, anchor.key.length);
                return n >= 12 && k.slice(0, n) === anchor.key.slice(0, n);
            });
            target = hits[Math.min(anchor.occ || 0, hits.length - 1)] || null;
            if (target) was = anchor.top;
        }
        const now = (target || content).getBoundingClientRect().top;
        const d = now - was;
        if (Math.abs(d) >= 1) anchor.sc.scrollTop += d;
    } catch (_) { /* the answer is delivered either way */ }
}

export function provisionalAnswerActive() { return !!_prov.el; }
export function provisionalAnswerText() { return _prov.text; }
