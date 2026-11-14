/**
 * main.js
 * * This is the entry point for the application.
 * It initializes the application by setting up event listeners and loading initial data.
 */

import { initializeEventListeners } from './eventHandlers.js?v=4.214';
import { renderersReady } from './componentRenderers.js?v=1.7';
import { finalizeConfiguration } from './handlers/configManagement.js?v=1.189';
import { initializeConfigurationUI } from './handlers/configurationHandler.js?v=2.214';
import * as API from './api.js?v=1.2';
import * as DOM from './domElements.js';
import { state } from './state.js';
import { setupPanelToggle } from './utils.js';
import * as UI from './ui.js?v=1.287';
import { handleViewSwitch } from './ui.js?v=1.287';
import { initializeVoiceRecognition } from './voice.js?v=1.189';
import { subscribeToNotifications } from './notifications.js?v=5.129';
import { initializeMarketplace, unsubscribeFromCollection } from './handlers/marketplaceHandler.js?v=1.190';
import * as capabilitiesModule from './handlers/capabilitiesManagement.js?v=1.189';
// Import hierarchy helpers for industrial blueprint session tree visualization
import { initializePathHighlighting, syncWrapperStates } from './hierarchyHelpers.js';
// Import conversationInitializer early to ensure window.__conversationInitState is available
import './conversationInitializer.js?v=1.190';
// Import splitViewHandler for Genie slave session split view (auto-initializes on import)
import './handlers/splitViewHandler.js?v=1.0';
// Import document upload initialization for chat conversations
import { initializeUploadUI, initializeUploadCapabilities } from './handlers/chatDocumentUpload.js?v=1.2';
import { loadKnowledgeGraphsPanel } from './handlers/knowledgeGraphPanelHandler.js?v=1.189';
import { loadContextPanel } from './handlers/contextPanelHandler.js?v=1.189';
import { loadWorkspacePanel } from './handlers/workspacePanelHandler.js?v=1.115';
import './handlers/splitPanelChrome.js?v=1.2';  // registers window.mountSplitPanelSwitcher
import './handlers/panelIcons.js?v=1.0';  // window.PanelIcons — one glyph per function for every panel bar
import './handlers/panelOpenControl.js?v=1.5';  // registers window.PanelOpenControl (the card -> side-panel button)
import './handlers/perfPanelResizer.js';  // draggable KPI/roster divider on the performance panels
import './customSelect.js?v=1.3';  // replaces native <select> OS popups with themed dropdowns (self-initialising)
import { initSkillsPanelEvents } from './handlers/skillsPanelHandler.js?v=1.14';
import { initComponentsPanelEvents } from './handlers/componentsPanelHandler.js?v=1.11';
import { initResourcePanel } from './handlers/resourcePanelHandler.js?v=1.4';
import { wireResourcePanelSync } from './handlers/resourceSync.js?v=1.2';
import { coalescedFetch } from './handlers/requestCoalescer.js';
import { scheduleProfileRebind } from './handlers/nextStepHandler.js?v=1.3';  // re-filter Next-Step chips on @TAG change

// Expose capabilities module globally for resource panel updates
window.capabilitiesModule = capabilitiesModule;

// ============================================================================
// UTILITY FUNCTIONS - Color Manipulation for Pure Industrial Design
// ============================================================================

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

    // ADJUSTED THRESHOLD: 0.6 instead of 0.5
    // Orange #F15F22 has luminance ~0.52 but is visually dark (needs white text)
    // Green #4ade80 has luminance ~0.66 and is visually light (needs black text)
    return luminance > 0.6;
}

// Session header profile display - uses unified profile tag system
function updateSessionHeaderProfile(defaultProfile, overrideProfile) {
    const headerDefaultProfile = document.getElementById('header-default-profile');
    const headerDefaultProfileTag = document.getElementById('header-default-profile-tag');
    const headerOverrideProfile = document.getElementById('header-override-profile');
    const headerOverrideProfileTag = document.getElementById('header-override-profile-tag');

    // Helper to convert hex to rgba
    const hexToRgba = (hex, alpha) => {
        const r = parseInt(hex.slice(1, 3), 16);
        const g = parseInt(hex.slice(3, 5), 16);
        const b = parseInt(hex.slice(5, 7), 16);
        return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    };

    // Detect light mode for higher contrast
    const isLightMode = document.documentElement.getAttribute('data-theme') === 'light';

    // Update knowledge indicator based on active profile
    const profile = overrideProfile || defaultProfile;
    updateKnowledgeIndicatorStatus(profile);

    // Helper to build agent pack indicator HTML (cube icon)
    const packIndicatorHtml = (profile) => {
        const packInfo = profile.agent_packs?.[0];
        if (!packInfo) return '';
        return `<span class="agent-pack-indicator" title="${packInfo.name}">` +
            `<svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">` +
            `<path stroke-linecap="round" stroke-linejoin="round" d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4"/>` +
            `</svg></span>`;
    };

    // Update default profile - PURE INDUSTRIAL SOLID COLORS
    if (defaultProfile && defaultProfile.tag) {
        headerDefaultProfileTag.innerHTML = `@${defaultProfile.tag}` + packIndicatorHtml(defaultProfile);
        if (defaultProfile.color) {
            // PURE INDUSTRIAL: Solid color, flat monolithic design
            headerDefaultProfile.style.setProperty('--profile-tag-bg', defaultProfile.color);

            // Flat monolithic: Border matches background (no gradient depth)
            headerDefaultProfile.style.setProperty('--profile-tag-border', defaultProfile.color);

            // Pure industrial: ALWAYS WHITE TEXT (no luminance calculation)
            headerDefaultProfile.style.setProperty('--profile-tag-text', '#FFFFFF');

            // Hover state (10% lighter)
            const hoverColor = adjustColorBrightness(defaultProfile.color, 10);
            headerDefaultProfile.style.setProperty('--profile-tag-bg-hover', hoverColor);
        }
        headerDefaultProfile.classList.remove('hidden');
    } else {
        headerDefaultProfile.classList.add('hidden');
        headerDefaultProfile.style.removeProperty('--profile-tag-bg');
        headerDefaultProfile.style.removeProperty('--profile-tag-border');
        headerDefaultProfile.style.removeProperty('--profile-tag-text');
    }

    // Update override profile - PURE INDUSTRIAL SOLID COLORS
    if (overrideProfile && overrideProfile.tag) {
        headerOverrideProfileTag.innerHTML = `@${overrideProfile.tag}` + packIndicatorHtml(overrideProfile);
        if (overrideProfile.color) {
            // PURE INDUSTRIAL: Solid color, flat monolithic design
            headerOverrideProfile.style.setProperty('--profile-tag-bg', overrideProfile.color);

            // Flat monolithic: Border matches background (no gradient depth)
            headerOverrideProfile.style.setProperty('--profile-tag-border', overrideProfile.color);

            // Pure industrial: ALWAYS WHITE TEXT (no luminance calculation)
            headerOverrideProfile.style.setProperty('--profile-tag-text', '#FFFFFF');

            // Hover state (10% lighter)
            const hoverColor = adjustColorBrightness(overrideProfile.color, 10);
            headerOverrideProfile.style.setProperty('--profile-tag-bg-hover', hoverColor);
        }
        headerOverrideProfile.classList.remove('hidden');
    } else {
        headerOverrideProfile.classList.add('hidden');
        headerOverrideProfile.style.removeProperty('--profile-tag-bg');
        headerOverrideProfile.style.removeProperty('--profile-tag-border');
        headerOverrideProfile.style.removeProperty('--profile-tag-text');
    }

    // Tint the conversation title bar's --sp-accent with the active profile's IFOC
    // class colour, so the panel's identity colour matches the profile driving it.
    const activeProfile = overrideProfile || defaultProfile;
    // Set on the conversation PANEL (the .sp-panel) so the header (inherits) and
    // the focus ring both pick up the active profile's IFOC colour.
    const _accentTarget = document.getElementById('conversation-panel') || document.getElementById('conversation-header');
    if (_accentTarget) {
        const _ifoc = window.ProfileColors && activeProfile && window.ProfileColors.IFOC_CONFIG[activeProfile.profile_type];
        const _accent = (_ifoc && _ifoc.color) || (activeProfile && activeProfile.color) || '#F15F22';
        _accentTarget.style.setProperty('--sp-accent', _accent);
    }
    if (activeProfile && window.configState?.llmConfigurations) {
        // Extract model info from profile
        let provider, model, dualModelInfo = null;

        // Check if profile has dual-model configuration
        if (activeProfile.dualModelConfig &&
            (activeProfile.dualModelConfig.strategicModelId || activeProfile.dualModelConfig.tacticalModelId)) {

            const strategicConfig = window.configState.llmConfigurations.find(
                c => c.id === activeProfile.dualModelConfig.strategicModelId
            );
            const tacticalConfig = window.configState.llmConfigurations.find(
                c => c.id === activeProfile.dualModelConfig.tacticalModelId
            );

            if (strategicConfig && tacticalConfig) {
                // Dual-model profile
                provider = strategicConfig.provider;
                model = strategicConfig.model;
                dualModelInfo = {
                    strategicProvider: strategicConfig.provider,
                    strategicModel: strategicConfig.model,
                    tacticalProvider: tacticalConfig.provider,
                    tacticalModel: tacticalConfig.model
                };
            }
        }

        // Fallback to single-model configuration
        if (!dualModelInfo && activeProfile.llmConfigurationId) {
            const llmConfig = window.configState.llmConfigurations.find(
                c => c.id === activeProfile.llmConfigurationId
            );
            if (llmConfig) {
                provider = llmConfig.provider;
                model = llmConfig.model;
            }
        }

        // Update the Live Status header if we have model info
        if (provider && model) {
            state.currentProvider = provider;
            state.currentModel = model;
            state.currentDualModelInfo = dualModelInfo;
            UI.updateStatusPromptName(provider, model, false, dualModelInfo);
        }
    }
}

// Update knowledge indicator based on all active profiles' knowledge collections
function updateKnowledgeIndicatorStatus(profile) {
    const knowledgeDot = document.getElementById('knowledge-status-dot');
    if (!knowledgeDot) return;

    // Check if ANY active-for-consumption profile has knowledge collections
    let hasActiveKnowledgeCollections = false;

    if (window.configState && window.configState.profiles) {
        const activeProfiles = window.configState.profiles.filter(p =>
            window.configState.activeForConsumptionProfileIds.includes(p.id)
        );

        // Check active profiles directly
        hasActiveKnowledgeCollections = activeProfiles.some(p =>
            p.knowledgeConfig?.collections?.length > 0
        );

        // Also check child profiles of active genie coordinators
        if (!hasActiveKnowledgeCollections) {
            for (const p of activeProfiles) {
                if (p.profile_type === 'genie' && p.genieConfig?.slaveProfiles?.length > 0) {
                    const childHasKnowledge = p.genieConfig.slaveProfiles.some(childId => {
                        const child = window.configState.profiles.find(cp => cp.id === childId);
                        return child?.knowledgeConfig?.collections?.length > 0;
                    });
                    if (childHasKnowledge) {
                        hasActiveKnowledgeCollections = true;
                        break;
                    }
                }
            }
        }
    }

    if (hasActiveKnowledgeCollections) {
        knowledgeDot.classList.remove('knowledge-idle', 'knowledge-active');
        knowledgeDot.classList.add('knowledge-configured');
    } else {
        knowledgeDot.classList.remove('knowledge-configured', 'knowledge-active');
        knowledgeDot.classList.add('knowledge-idle');
    }
}

// Make it globally accessible
window.updateSessionHeaderProfile = updateSessionHeaderProfile;
window.updateKnowledgeIndicatorStatus = updateKnowledgeIndicatorStatus;

async function initializeRAGAutoCompletion() {
    const suggestionsContainer = document.getElementById('rag-suggestions-container');
    const profileTagSelector = document.getElementById('profile-tag-selector');
    const activeProfileTag = document.getElementById('active-profile-tag');
    const userInput = document.getElementById('user-input');
    let debounceTimer = null;
    let currentSuggestions = [];
    let selectedIndex = -1;
    let currentProfiles = [];
    let profileSelectedIndex = -1;
    let isShowingProfileSelector = false;

    // Extension selector state
    const extensionSelector = document.getElementById('extension-selector');
    const activeExtensionTagsContainer = document.getElementById('active-extension-tags');
    let currentExtensions = [];
    let extensionSelectedIndex = -1;
    let isShowingExtensionSelector = false;
    let activeExtensions = [];  // [{name, param}] — active extension badges
    window.activeExtensions = activeExtensions;

    // --- Skill badge system (mirrors extension badges, emerald green, # trigger) ---
    const skillSelector = document.getElementById('skill-selector');
    const activeSkillTagsContainer = document.getElementById('active-skill-tags');
    let currentSkills = [];
    let skillSelectedIndex = -1;
    let isShowingSkillSelector = false;
    let activeSkills = [];  // [{name, param}] — active skill badges
    window.activeSkills = activeSkills;

    // Prevent scroll events from propagating to parent when scrolling inside dropdown
    profileTagSelector.addEventListener('wheel', (e) => {
        const { scrollTop, scrollHeight, clientHeight } = profileTagSelector;
        const atTop = scrollTop === 0;
        const atBottom = scrollTop + clientHeight >= scrollHeight;

        // Only prevent default if we're at the boundary and trying to scroll further
        if ((atTop && e.deltaY < 0) || (atBottom && e.deltaY > 0)) {
            e.preventDefault();
        }
        e.stopPropagation();
    }, { passive: false });

    extensionSelector.addEventListener('wheel', (e) => {
        const { scrollTop, scrollHeight, clientHeight } = extensionSelector;
        const atTop = scrollTop === 0;
        const atBottom = scrollTop + clientHeight >= scrollHeight;
        if ((atTop && e.deltaY < 0) || (atBottom && e.deltaY > 0)) {
            e.preventDefault();
        }
        e.stopPropagation();
    }, { passive: false });

    skillSelector.addEventListener('wheel', (e) => {
        const { scrollTop, scrollHeight, clientHeight } = skillSelector;
        const atTop = scrollTop === 0;
        const atBottom = scrollTop + clientHeight >= scrollHeight;
        if ((atTop && e.deltaY < 0) || (atBottom && e.deltaY > 0)) {
            e.preventDefault();
        }
        e.stopPropagation();
    }, { passive: false });

    function highlightSuggestion(index) {
        const items = suggestionsContainer.querySelectorAll('.rag-suggestion-item');
        items.forEach((item, idx) => {
            if (idx === index) {
                item.classList.add('rag-suggestion-highlighted');
            } else {
                item.classList.remove('rag-suggestion-highlighted');
            }
        });
    }

    function highlightProfile(index) {
        const items = profileTagSelector.querySelectorAll('.profile-tag-item');
        items.forEach((item, idx) => {
            if (idx === index) {
                item.classList.add('profile-tag-highlighted');
                // Auto-scroll highlighted item into view within the dropdown only
                // Use scrollTop manipulation to avoid scrolling the whole page
                const container = profileTagSelector;
                const itemTop = item.offsetTop;
                const itemBottom = itemTop + item.offsetHeight;
                const containerTop = container.scrollTop;
                const containerBottom = containerTop + container.clientHeight;

                if (itemTop < containerTop) {
                    container.scrollTop = itemTop;
                } else if (itemBottom > containerBottom) {
                    container.scrollTop = itemBottom - container.clientHeight;
                }
            } else {
                item.classList.remove('profile-tag-highlighted');
            }
        });
    }

    function selectSuggestion(index) {
        if (index >= 0 && index < currentSuggestions.length) {
            const currentValue = userInput.value;
            const tagMatch = currentValue.match(/^@([\w-]+)\s/);
            
            if (tagMatch) {
                // Preserve the @TAG prefix
                userInput.value = tagMatch[0] + currentSuggestions[index];
            } else {
                userInput.value = currentSuggestions[index];
            }
            
            suggestionsContainer.innerHTML = '';
            suggestionsContainer.classList.add('hidden');
            currentSuggestions = [];
            selectedIndex = -1;
        }
    }

    let activeTagPrefix = '';
    let isUpdatingInput = false;
    
    // Expose activeTagPrefix to window for access by other modules
    Object.defineProperty(window, 'activeTagPrefix', {
        get: () => activeTagPrefix,
        set: (value) => { activeTagPrefix = value; }
    });

    function showActiveTagBadge(profile) {
        // Never show the default profile as an override — the default can change over time,
        // so this check is dynamic against the current configState
        if (profile.id === window.configState?.defaultProfileId) {
            activeTagPrefix = '';  // Clear any prefix set by callers before invoking this function
            hideActiveTagBadge();
            return;
        }

        // Add unified profile tag classes - use --md for better visibility in question box
        activeProfileTag.className = 'profile-tag profile-tag--md profile-tag--removable';
        activeProfileTag.innerHTML = `
            <span>@${profile.tag}</span>
            <span class="profile-tag__remove" title="Remove profile override">×</span>
        `;

        // Apply provider color via CSS custom properties
        if (profile.color && profile.colorSecondary) {
            const hexToRgba = (hex, alpha) => {
                const r = parseInt(hex.slice(1, 3), 16);
                const g = parseInt(hex.slice(3, 5), 16);
                const b = parseInt(hex.slice(5, 7), 16);
                return `rgba(${r}, ${g}, ${b}, ${alpha})`;
            };

            // Detect light mode for text color and opacity compensation
            const isLightMode = document.documentElement.getAttribute('data-theme') === 'light';

            // Question box tag needs higher opacity in light mode to compensate for glass-panel washout
            // In light mode, the glass-panel has rgba(255, 255, 255, 0.85) which desaturates colors
            const primaryOpacity = isLightMode ? 0.50 : 0.25;
            const secondaryOpacity = isLightMode ? 0.30 : 0.12;
            const borderOpacity = isLightMode ? 0.65 : 0.4;

            const color1 = hexToRgba(profile.color, primaryOpacity);
            const color2 = hexToRgba(profile.color, secondaryOpacity);
            const borderColor = hexToRgba(profile.color, borderOpacity);

            // PURE INDUSTRIAL: Flat monolithic design
            activeProfileTag.style.setProperty('--profile-tag-bg', profile.color);
            // Flat monolithic: Border matches background (no gradient depth)
            activeProfileTag.style.setProperty('--profile-tag-border', profile.color);
            activeProfileTag.style.setProperty('--profile-tag-shadow', '0 2px 4px rgba(0, 0, 0, 0.2)');
            activeProfileTag.style.setProperty('--profile-tag-text', '#FFFFFF');
        }

        activeProfileTag.classList.remove('hidden');
        
        // Store the tag prefix and remove it from visible input
        const currentValue = userInput.value;
        const tagMatch = currentValue.match(/^@[\w-]+\s/);
        if (tagMatch) {
            activeTagPrefix = tagMatch[0];
            isUpdatingInput = true;
            userInput.value = currentValue.substring(tagMatch[0].length);
            isUpdatingInput = false;
        }
        
        // Add click handler for remove button
        const removeBtn = activeProfileTag.querySelector('.profile-tag__remove');
        removeBtn.addEventListener('click', () => {
            activeTagPrefix = '';
            hideActiveTagBadge();
            userInput.focus();
            scheduleProfileRebind();   // override removed → revert chips to default profile
        });

        // Update session header to show override (keep default profile visible)
        const defaultProfileId = window.configState?.defaultProfileId;
        const defaultProfile = defaultProfileId && window.configState?.profiles
            ? window.configState.profiles.find(p => p.id === defaultProfileId)
            : null;
        updateSessionHeaderProfile(defaultProfile, profile);

        // Update resource panel to show profile-specific tools/prompts
        updateResourcePanelForProfile(profile.id);
    }

    function hideActiveTagBadge() {
        // Skip if badge is already hidden (prevents unnecessary API calls on each keystroke)
        if (activeProfileTag.classList.contains('hidden')) {
            return;
        }

        activeProfileTag.innerHTML = '';
        activeProfileTag.className = 'hidden';  // Reset all classes
        activeProfileTag.style.removeProperty('--profile-tag-bg');
        activeProfileTag.style.removeProperty('--profile-tag-border');
        activeProfileTag.style.removeProperty('--profile-tag-shadow');
        activeProfileTag.style.color = '';  // Reset direct color style

        // Clear last-executed override so new sessions use the default profile primer
        delete window.activeProfileOverrideId;

        // Clear session header override and restore default
        const defaultProfileId = window.configState?.defaultProfileId;
        if (defaultProfileId && window.configState?.profiles) {
            const defaultProfile = window.configState.profiles.find(p => p.id === defaultProfileId);
            updateSessionHeaderProfile(defaultProfile, null);

            // Restore default profile resources in resource panel
            updateResourcePanelForProfile(defaultProfileId);
        } else {
            // Fallback to generic resources if no default profile
            updateResourcePanelForProfile(null);
        }
    }

    // In-flight memo, keyed by profile. Boot calls this for the default profile and
    // reconnectAndLoad() calls it again ~1.6s later for the SAME profile, and each
    // invocation fans out to six panels (KG, Context, Workspace, Skills, Extensions,
    // Components) plus the /resources MCP round trip — so the whole set was fetched
    // twice. While a load for a given profile is still running, a second request for
    // that same profile joins it instead of starting a parallel one.
    //
    // NOT a cache: the memo is dropped the moment the load settles, so a later call
    // always does real work and can never paint stale data. Merging is safe because the
    // function is idempotent for a given profileId — two concurrent loads of the same
    // profile produce the same DOM.
    const _resourcePanelInflight = new Map();   // profileId -> Promise

    async function updateResourcePanelForProfile(profileId) {
        const _memoKey = String(profileId);
        const _running = _resourcePanelInflight.get(_memoKey);
        if (_running) {
            console.log(`📦 [Resource Panel] Joining the in-flight load for ${profileId}`);
            return _running;
        }
        const _p = _updateResourcePanelForProfile(profileId).finally(() => {
            // Release only if the slot is still ours.
            if (_resourcePanelInflight.get(_memoKey) === _p) _resourcePanelInflight.delete(_memoKey);
        });
        _resourcePanelInflight.set(_memoKey, _p);
        return _p;
    }

    async function _updateResourcePanelForProfile(profileId) {
        /**
         * Update the resource panel to show tools/prompts for a specific profile.
         * If profileId is null, restores default profile resources.
         */
        try {
            const authToken = localStorage.getItem('tda_auth_token');
            if (!authToken) return;

            // Store the profile ID for prompt invocations from resource panel
            // This ensures prompts are executed with the correct profile even if
            // window.activeProfileOverrideId is lost (e.g., page refresh)
            state.currentResourcePanelProfileId = profileId;
            window.currentResourcePanelProfileId = profileId;  // expose for non-module handlers
            console.log(`📦 [Resource Panel] Profile ID stored: ${profileId}`);

            // ONE signal for "the profile the next turn will run under changed" — every
            // @TAG override (typed, picked, removed) and every default-profile change
            // already funnels through here. Consumers that show per-profile state
            // elsewhere (the knowledge-graph split panel) subscribe to this rather than
            // to the five composer sites. `streaming` lets a consumer decline to swap a
            // view a running turn is still using.
            try {
                UderiaBus.emit('uderia:effective-profile', {
                        profileId: profileId || null,
                        streaming: !!(state.activeStreamSessions && state.currentSessionId
                                      && state.activeStreamSessions.has(state.currentSessionId)),
                    });
            } catch (_) { /* a listener must never break the panel update */ }

            // Update Knowledge Graphs panel active indicator (survives @TAG overrides)
            loadKnowledgeGraphsPanel();

            // Update Context panel to reflect the active profile's context window type
            loadContextPanel();

            // Update Workspace panel (component instances) + rail visibility for this profile
            loadWorkspacePanel();

            // Warm the component-enablement gate for this profile so conversation
            // affordances (Pin to Dashboard, ...) resolve from cache instead of racing
            // a fetch on first render. Invalidate first: a profile save may have changed
            // componentConfig since the last warm.
            if (window.ComponentGate && profileId) {
                window.ComponentGate.invalidate(profileId);
                window.ComponentGate.prime(profileId);
            }

            // Update Skills panel to reflect the active profile's assigned skills
            import('./handlers/skillsPanelHandler.js?v=1.14').then(mod => mod.loadSkillsPanel());

            // Update Extensions panel (user-level, refresh on profile switch for freshness)
            import('./handlers/extensionsPanelHandler.js?v=1.8').then(mod => mod.loadExtensionsPanel());

            // Update Components panel to reflect the active profile's componentConfig
            import('./handlers/componentsPanelHandler.js?v=1.11').then(mod => mod.loadComponentsPanel());

            // Refresh #skill autocomplete for the new profile's enabled skills
            loadActivatedSkills();

            let tools, prompts, connectors = {};
            // PROP-006 phase 2 — which MCP server each category belongs to (multi-server agents only)
            let panelServers = null;

            // ONE mechanism: the panel reads the agent it shows through the per-agent endpoint,
            // the default agent included. The default used to read the legacy /tools, /prompts
            // and /connectors routes, which served the PROCESS-GLOBAL catalog — whichever agent
            // was last loaded into APP_STATE, with THAT agent's on/off flags — so the Tools
            // figure and the eye icons belonged to another agent whenever one had been loaded
            // since (the operator's "the Tools figure is not correct").
            const fetchId = profileId || window.configState?.defaultProfileId || null;
            if (fetchId) {
                const response = await fetch(`/api/v1/profiles/${fetchId}/resources`, {
                    headers: { 'Authorization': `Bearer ${authToken}` }
                });

                if (!response.ok) {
                    console.error('Failed to fetch profile resources:', response.status);
                    // Same stale-response guard as the success path: a failure for a
                    // profile the user has already navigated away from must not clear a
                    // newer profile's panel.
                    if (state.currentResourcePanelProfileId !== profileId) return;
                    // Clear stale data so panel doesn't show wrong profile's tools
                    state.resourceData.tools = {};
                    state.resourceData.prompts = {};
                    state.activeGenieProfile = null;
                    state.activeRagProfile = null;
                    state.activeLlmOnlyProfile = null;
                    if (window.capabilitiesModule) {
                        window.capabilitiesModule.renderResourcePanel('tools');
                        window.capabilitiesModule.renderResourcePanel('prompts');
                    }
                    return;
                }

                const data = await response.json();
                console.log(`📊 Profile resources received:`, {
                    profile_type: data.profile_type,
                    tag: data.profile_tag,
                    hasTools: Object.keys(data.tools || {}).length > 0
                });
                tools = data.tools || {};
                prompts = data.prompts || {};
                connectors = data.connectors || {};
                panelServers = Array.isArray(data.servers) && data.servers.length
                    ? { servers: data.servers, categoryServers: data.category_servers || {} } : null;

                // Handle Genie profiles: show coordinator info in resource panel
                if (data.profile_type === 'genie') {
                    // Store Genie profile info in state for rendering
                    state.activeGenieProfile = {
                        tag: data.profile_tag,
                        slaveProfiles: data.slave_profiles || []
                    };
                    state.activeRagProfile = null;
                    state.activeLlmOnlyProfile = null;
                    console.log(`🧞 Genie coordinator profile active: @${data.profile_tag}`);
                } else if (data.profile_type === 'rag_focused') {
                    // Store RAG-focused profile info in state for rendering
                    state.activeRagProfile = {
                        tag: data.profile_tag,
                        knowledgeCollections: data.knowledge_collections || []
                    };
                    state.activeGenieProfile = null;
                    state.activeLlmOnlyProfile = null;
                    console.log(`📚 RAG-focused profile active: @${data.profile_tag}`);
                } else if (data.profile_type === 'llm_only') {
                    // Check if this llm_only profile has tools (useMcpTools=true case)
                    const hasTools = data.tools && Object.keys(data.tools).length > 0;

                    if (hasTools) {
                        // llm_only with tools: treat like tool_enabled for panel display
                        state.activeGenieProfile = null;
                        state.activeRagProfile = null;
                        state.activeLlmOnlyProfile = null;
                        console.log(`💬🔧 Conversation profile with tools active: @${data.profile_tag}`);
                    } else {
                        // Pure llm_only: show conversation panel
                        state.activeLlmOnlyProfile = {
                            tag: data.profile_tag,
                            knowledgeCollections: data.knowledge_collections || []
                        };
                        state.activeGenieProfile = null;
                        state.activeRagProfile = null;
                        console.log(`💬 Conversation-focused profile active: @${data.profile_tag}`);
                    }
                } else {
                    // Clear special profile info if switching away
                    state.activeGenieProfile = null;
                    state.activeRagProfile = null;
                    state.activeLlmOnlyProfile = null;
                    console.log(`🔍 Resource panel updated for profile: @${data.profile_tag}`);
                }

                // Update key observations button visibility based on new profile type
                updateKeyObservationsButtonVisibility();
            } else {
                // No default agent configured: nothing to show.
                tools = {}; prompts = {}; connectors = {};
                state.activeGenieProfile = null;
                state.activeRagProfile = null;
                state.activeLlmOnlyProfile = null;
            }

            // STALE-RESPONSE GUARD. This function is invoked concurrently — boot fires it
            // for the default profile (unawaited), and a session restore / profile switch
            // fires it for a DIFFERENT profile moments later. Without this check the
            // slower response wins the write, and the panel renders one profile's tools
            // under another profile's id. Six call sites already fired unawaited, so the
            // race pre-dates this guard; deferring the boot call made it reachable at
            // login, which is how it was found.
            if (state.currentResourcePanelProfileId !== profileId) {
                console.log(`📦 [Resource Panel] Discarding stale response for ${profileId} (current: ${state.currentResourcePanelProfileId})`);
                return;
            }

            // Update the resource panel with new data
            state.resourceData.tools = tools;
            state.resourceData.prompts = prompts;
            state.resourceData.connectors = connectors;
            state.resourceData.mcpServers = panelServers;

            // Trigger re-render of resource panels (using existing state data, not fetching)
            if (window.capabilitiesModule) {
                window.capabilitiesModule.renderResourcePanel('tools');
                window.capabilitiesModule.renderResourcePanel('prompts');
                window.capabilitiesModule.renderResourcePanel('connectors');
            }

            // Hide Resources tab - profile resources endpoint doesn't serve MCP resources,
            // and renderResourcePanel for special profiles (genie/rag/llm_only) already hides it.
            // For tool_enabled profiles we must hide it explicitly since it has no data.
            const resourcesTab = document.querySelector('.resource-tab[data-type="resources"]');
            if (resourcesTab) {
                resourcesTab.style.display = 'none';
            }

        } catch (error) {
            console.error('Error updating resource panel:', error);
        }
    }

    // Expose globally for use by configurationHandler when default profile changes
    window.updateResourcePanelForProfile = updateResourcePanelForProfile;

    // Live sync (resourceSync.js): any change to the agent on show — a component toggled in
    // the Components panel, the profile editor's save, the skill/extension libraries,
    // Administration → Components — re-runs the load above, so every tab and badge follows
    // without a page reload. A change to another agent is not this panel's to show.
    wireResourcePanelSync(() => state.currentResourcePanelProfileId || null,
                          (profileId) => updateResourcePanelForProfile(profileId));

    // Expose profile tag badge functions for use by sessionManagement when loading sessions
    window.showActiveTagBadge = showActiveTagBadge;
    window.hideActiveTagBadge = hideActiveTagBadge;

    function updateTagBadge() {
        if (isUpdatingInput) return;
        
        // Check if we have an active tag
        if (activeTagPrefix) {
            // Tag is active, badge should be showing
            return;
        }
        
        // Check if input starts with @TAG
        const inputValue = userInput.value;
        const tagMatch = inputValue.match(/^@([\w-]+)\s/);
        
        if (tagMatch && window.configState?.profiles) {
            const tag = tagMatch[1].toUpperCase();
            const profile = window.configState.profiles.find(p => p.tag === tag);
            if (profile) {
                showActiveTagBadge(profile);
                return;
            }
        }
        
        // No valid tag found, hide badge
        if (!activeTagPrefix) {
            hideActiveTagBadge();
        }
    }

    function selectProfile(index) {
        if (index >= 0 && index < currentProfiles.length) {
            const profile = currentProfiles[index];

            // Clear the input (remove the @ or partial tag)
            isUpdatingInput = true;
            userInput.value = '';
            isUpdatingInput = false;
            UI.autoResizeChatInput();

            // If user selected the default profile, don't treat as override
            if (profile.id === window.configState?.defaultProfileId) {
                activeTagPrefix = '';
                hideActiveTagBadge();
                profileTagSelector.innerHTML = '';
                profileTagSelector.classList.add('hidden');
                currentProfiles = [];
                profileSelectedIndex = -1;
                isShowingProfileSelector = false;
                userInput.focus();
                scheduleProfileRebind();   // selected default → revert chips to default profile
                return;
            }

            // Set up the badge and store the tag prefix
            activeTagPrefix = `@${profile.tag} `;

            // Use the unified showActiveTagBadge function for consistent styling
            showActiveTagBadge(profile);
            scheduleProfileRebind();   // override selected → re-filter chips to this profile's class

            // Add click handler for remove button
            const removeBtn = activeProfileTag.querySelector('.profile-tag__remove');
            removeBtn.addEventListener('click', () => {
                activeTagPrefix = '';
                hideActiveTagBadge();
                userInput.focus();
                scheduleProfileRebind();   // override removed → revert chips to default profile
            });

            // Update session header to show override (keep default profile visible)
            const defaultProfileId = window.configState?.defaultProfileId;
            const defaultProfile = defaultProfileId && window.configState?.profiles
                ? window.configState.profiles.find(p => p.id === defaultProfileId)
                : null;
            updateSessionHeaderProfile(defaultProfile, profile);

            // Update resource panel to show profile-specific tools/prompts
            updateResourcePanelForProfile(profile.id);

            profileTagSelector.innerHTML = '';
            profileTagSelector.classList.add('hidden');
            currentProfiles = [];
            profileSelectedIndex = -1;
            isShowingProfileSelector = false;
            userInput.focus();
        }
    }

    function showSuggestions(questionsToShow) {
        currentSuggestions = questionsToShow;
        selectedIndex = questionsToShow.length > 0 ? 0 : -1;

        if (questionsToShow.length === 0) {
            suggestionsContainer.innerHTML = '';
            suggestionsContainer.classList.add('hidden');
            return;
        }

        suggestionsContainer.innerHTML = '';
        questionsToShow.forEach((q, index) => {
            const suggestionItem = document.createElement('div');
            suggestionItem.className = 'rag-suggestion-item';
            if (index === 0) {
                suggestionItem.classList.add('rag-suggestion-highlighted');
            }
            suggestionItem.textContent = q;
            suggestionItem.addEventListener('mousedown', (e) => {
                e.preventDefault();
                selectSuggestion(index);
                userInput.focus();
            });
            suggestionItem.addEventListener('mouseenter', () => {
                selectedIndex = index;
                highlightSuggestion(index);
            });
            suggestionsContainer.appendChild(suggestionItem);
        });
        suggestionsContainer.classList.remove('hidden');
    }

    function showProfileSelector(profiles) {
        currentProfiles = profiles;
        const defaultProfileId = window.configState?.defaultProfileId;
        
        // Set initial selection to first non-default profile
        if (profiles.length > 0) {
            const firstSelectableIndex = profiles.findIndex(p => p.id !== defaultProfileId);
            profileSelectedIndex = firstSelectableIndex >= 0 ? firstSelectableIndex : -1;
        } else {
            profileSelectedIndex = -1;
        }
        
        isShowingProfileSelector = true;

        if (profiles.length === 0) {
            profileTagSelector.innerHTML = '';
            profileTagSelector.classList.add('hidden');
            return;
        }

        profileTagSelector.innerHTML = '';
        
        profiles.forEach((profile, index) => {
            const isDefault = profile.id === defaultProfileId;
            const profileItem = document.createElement('div');
            profileItem.className = 'profile-tag-item';
            
            // Make default profile non-selectable
            if (isDefault) {
                profileItem.classList.add('profile-tag-disabled');
                profileItem.style.opacity = '0.6';
                profileItem.style.cursor = 'not-allowed';
            } else if (index === 1 || (index === 0 && !isDefault)) {
                // Highlight first selectable profile
                profileItem.classList.add('profile-tag-highlighted');
            }

            const header = document.createElement('div');
            header.className = 'profile-tag-header';

            const badge = document.createElement('span');
            badge.className = 'profile-tag profile-tag--md profile-tag-badge';
            badge.textContent = `@${profile.tag}`;

            // Apply PURE INDUSTRIAL SOLID COLORS via CSS custom properties
            if (profile.color) {
                // PURE INDUSTRIAL: Flat monolithic design
                badge.style.setProperty('--profile-tag-bg', profile.color);

                // Flat monolithic: Border matches background (no gradient depth)
                badge.style.setProperty('--profile-tag-border', profile.color);

                // Pure industrial: Always white text on solid color backgrounds
                badge.style.setProperty('--profile-tag-text', '#FFFFFF');

                // Hover state (10% lighter)
                const hoverColor = adjustColorBrightness(profile.color, 10);
                badge.style.setProperty('--profile-tag-bg-hover', hoverColor);
            }

            const name = document.createElement('span');
            name.className = 'profile-tag-name';
            name.textContent = profile.name;

            // Add default indicator if this is the default profile
            if (isDefault) {
                const defaultIndicator = document.createElement('span');
                defaultIndicator.className = 'profile-default-indicator';
                defaultIndicator.textContent = '★ Default';
                defaultIndicator.title = 'Default Profile (already active)';
                header.appendChild(badge);
                header.appendChild(name);
                header.appendChild(defaultIndicator);
            } else {
                header.appendChild(badge);
                header.appendChild(name);
            }

            // Add agent pack label if profile belongs to a pack
            if (profile.agent_packs?.length) {
                const packLabel = document.createElement('span');
                packLabel.className = 'text-xs opacity-75';
                packLabel.style.color = '#22d3ee';
                packLabel.textContent = profile.agent_packs[0].name;
                header.appendChild(packLabel);
            }

            profileItem.appendChild(header);

            if (profile.description) {
                const description = document.createElement('div');
                description.className = 'profile-tag-description';
                description.textContent = profile.description;
                profileItem.appendChild(description);
            }

            // Only add click handlers for non-default profiles
            if (!isDefault) {
                profileItem.addEventListener('mousedown', (e) => {
                    e.preventDefault();
                    selectProfile(index);
                });
                profileItem.addEventListener('mouseenter', () => {
                    profileSelectedIndex = index;
                    highlightProfile(index);
                });
            }

            profileTagSelector.appendChild(profileItem);
        });
        profileTagSelector.classList.remove('hidden');
    }

    function hideProfileSelector() {
        profileTagSelector.innerHTML = '';
        profileTagSelector.classList.add('hidden');
        currentProfiles = [];
        profileSelectedIndex = -1;
        isShowingProfileSelector = false;
    }

    // --- Extension selector functions ---

    function highlightExtension(index) {
        const items = extensionSelector.querySelectorAll('.extension-item');
        items.forEach((item, idx) => {
            if (idx === index) {
                item.classList.add('extension-highlighted');
                const container = extensionSelector;
                const itemTop = item.offsetTop;
                const itemBottom = itemTop + item.offsetHeight;
                const containerTop = container.scrollTop;
                const containerBottom = containerTop + container.clientHeight;
                if (itemTop < containerTop) {
                    container.scrollTop = itemTop;
                } else if (itemBottom > containerBottom) {
                    container.scrollTop = itemBottom - container.clientHeight;
                }
            } else {
                item.classList.remove('extension-highlighted');
            }
        });
    }

    function showExtensionSelector(extensions) {
        currentExtensions = extensions;
        extensionSelectedIndex = extensions.length > 0 ? 0 : -1;
        isShowingExtensionSelector = true;

        if (extensions.length === 0) {
            extensionSelector.innerHTML = '';
            extensionSelector.classList.add('hidden');
            return;
        }

        extensionSelector.innerHTML = '';

        extensions.forEach((ext, index) => {
            const item = document.createElement('div');
            item.className = 'extension-item';
            if (index === 0) {
                item.classList.add('extension-highlighted');
            }

            const header = document.createElement('div');
            header.className = 'extension-item-header';

            const activationName = ext.activation_name || ext.extension_id;

            const badge = document.createElement('span');
            badge.className = 'extension-item-badge';
            badge.textContent = `!${activationName}`;
            header.appendChild(badge);

            const name = document.createElement('span');
            name.className = 'extension-item-name';
            name.textContent = ext.display_name || ext.extension_id;
            header.appendChild(name);

            item.appendChild(header);

            if (ext.description) {
                const desc = document.createElement('div');
                desc.className = 'extension-item-description';
                desc.textContent = ext.description;
                item.appendChild(desc);
            }

            // Show default param if set via activation
            if (ext.default_param) {
                const param = document.createElement('div');
                param.className = 'extension-item-param';
                param.textContent = `Default: ${ext.default_param}`;
                item.appendChild(param);
            }

            item.addEventListener('mousedown', (e) => {
                e.preventDefault();
                selectExtension(index);
                userInput.focus();
            });
            item.addEventListener('mouseenter', () => {
                extensionSelectedIndex = index;
                highlightExtension(index);
            });

            extensionSelector.appendChild(item);
        });

        extensionSelector.classList.remove('hidden');
    }

    function hideExtensionSelector() {
        extensionSelector.innerHTML = '';
        extensionSelector.classList.add('hidden');
        currentExtensions = [];
        extensionSelectedIndex = -1;
        isShowingExtensionSelector = false;
    }

    function selectExtension(index) {
        if (index >= 0 && index < currentExtensions.length) {
            const ext = currentExtensions[index];
            const activationName = ext.activation_name || ext.extension_id;

            // Remove the partial !tag from input text
            const currentValue = userInput.value;
            const bangMatch = currentValue.match(/!(\w*)$/);
            if (bangMatch) {
                const beforeBang = currentValue.substring(0, bangMatch.index);
                isUpdatingInput = true;
                userInput.value = beforeBang.trimEnd();
                isUpdatingInput = false;
            }

            // Add as visual badge (skip if already active)
            if (!activeExtensions.find(e => e.name === activationName)) {
                const defaultParam = ext.default_param || null;
                addExtensionBadge(activationName, defaultParam);
            }

            hideExtensionSelector();
            userInput.focus();
        }
    }

    function addExtensionBadge(name, param) {
        activeExtensions.push({ name, param });
        window.activeExtensions = activeExtensions;
        _renderExtensionBadges();
    }

    function removeExtensionBadge(name) {
        activeExtensions = activeExtensions.filter(e => e.name !== name);
        window.activeExtensions = activeExtensions;
        _renderExtensionBadges();
        userInput.focus();
    }

    function clearExtensionBadges() {
        activeExtensions = [];
        window.activeExtensions = activeExtensions;
        activeExtensionTagsContainer.innerHTML = '';
    }
    window.clearExtensionBadges = clearExtensionBadges;

    // --- Skill badge functions (mirrors extension badges, emerald green) ---

    function showSkillSelector(skills) {
        currentSkills = skills;
        isShowingSkillSelector = true;

        if (skills.length === 0) {
            skillSelector.innerHTML = '';
            skillSelector.classList.add('hidden');
            return;
        }

        skillSelector.innerHTML = '';

        // Find the first selectable (non-auto) skill for initial highlight
        const firstSelectable = skills.findIndex(s => !s.active);
        skillSelectedIndex = firstSelectable >= 0 ? firstSelectable : -1;

        skills.forEach((skill, index) => {
            const isAuto = !!skill.active;
            const item = document.createElement('div');
            item.className = 'skill-item';
            if (isAuto) item.classList.add('skill-item-auto');
            if (index === firstSelectable) item.classList.add('skill-highlighted');

            const header = document.createElement('div');
            header.className = 'skill-item-header';

            const activationName = skill.activation_name || skill.skill_id;

            const badge = document.createElement('span');
            badge.className = 'skill-item-badge';
            badge.textContent = `#${activationName}`;
            header.appendChild(badge);

            const name = document.createElement('span');
            name.className = 'skill-item-name';
            name.textContent = skill.name || skill.skill_id;
            header.appendChild(name);

            if (isAuto) {
                const autoBadge = document.createElement('span');
                autoBadge.className = 'skill-item-auto-badge';
                autoBadge.textContent = 'AUTO';
                header.appendChild(autoBadge);
            }

            item.appendChild(header);

            if (skill.description) {
                const desc = document.createElement('div');
                desc.className = 'skill-item-description';
                desc.textContent = skill.description;
                item.appendChild(desc);
            }

            if (skill.default_param && !isAuto) {
                const param = document.createElement('div');
                param.className = 'skill-item-param';
                param.textContent = `Default: ${skill.default_param}`;
                item.appendChild(param);
            }

            if (!isAuto) {
                item.addEventListener('mousedown', (e) => {
                    e.preventDefault();
                    selectSkill(index);
                    userInput.focus();
                });
                item.addEventListener('mouseenter', () => {
                    skillSelectedIndex = index;
                    highlightSkill(index);
                });
            }

            skillSelector.appendChild(item);
        });

        skillSelector.classList.remove('hidden');
    }

    function hideSkillSelector() {
        skillSelector.innerHTML = '';
        skillSelector.classList.add('hidden');
        currentSkills = [];
        skillSelectedIndex = -1;
        isShowingSkillSelector = false;
    }

    function highlightSkill(index) {
        const items = skillSelector.querySelectorAll('.skill-item');
        items.forEach((item, idx) => {
            if (idx === index) {
                item.classList.add('skill-highlighted');
                const container = skillSelector;
                const itemTop = item.offsetTop;
                const itemBottom = itemTop + item.offsetHeight;
                const containerTop = container.scrollTop;
                const containerBottom = containerTop + container.clientHeight;
                if (itemTop < containerTop) {
                    container.scrollTop = itemTop;
                } else if (itemBottom > containerBottom) {
                    container.scrollTop = itemBottom - container.clientHeight;
                }
            } else {
                item.classList.remove('skill-highlighted');
            }
        });
    }

    function selectSkill(index) {
        if (index >= 0 && index < currentSkills.length) {
            const skill = currentSkills[index];
            if (skill.active) return; // auto-active skills are not selectable
            const activationName = skill.activation_name || skill.skill_id;

            // Remove the partial #tag from input text
            const currentValue = userInput.value;
            const hashMatch = currentValue.match(/#(\w*)$/);
            if (hashMatch) {
                const beforeHash = currentValue.substring(0, hashMatch.index);
                isUpdatingInput = true;
                userInput.value = beforeHash.trimEnd();
                isUpdatingInput = false;
            }

            // Add as visual badge (skip if already active)
            if (!activeSkills.find(s => s.name === activationName)) {
                const defaultParam = skill.default_param || null;
                addSkillBadge(activationName, defaultParam);
            }

            hideSkillSelector();
            userInput.focus();
        }
    }

    function addSkillBadge(name, param) {
        activeSkills.push({ name, param });
        window.activeSkills = activeSkills;
        _renderSkillBadges();
    }

    function removeSkillBadge(name) {
        activeSkills = activeSkills.filter(s => s.name !== name);
        window.activeSkills = activeSkills;
        _renderSkillBadges();
        userInput.focus();
    }

    function clearSkillBadges() {
        activeSkills = [];
        window.activeSkills = activeSkills;
        activeSkillTagsContainer.innerHTML = '';
    }
    window.clearSkillBadges = clearSkillBadges;

    function _getSkillMeta(activationName) {
        return (window.skillState?.activated || []).find(
            s => (s.activation_name || s.skill_id) === activationName
        );
    }

    function _validateSkillParam(activationName, paramValue) {
        const meta = _getSkillMeta(activationName);
        if (!meta) return 'none';
        const allowed = meta.allowed_params || [];
        if (allowed.length === 0) return 'none'; // no param restrictions
        if (!paramValue) return 'none';
        return allowed.includes(paramValue) ? 'valid' : 'invalid';
    }

    function _renderSkillBadges() {
        activeSkillTagsContainer.innerHTML = '';
        activeSkills.forEach(spec => {
            const badge = document.createElement('span');
            badge.className = 'active-skill-badge';

            const validation = spec.param ? _validateSkillParam(spec.name, spec.param) : 'none';
            if (validation === 'invalid') {
                badge.classList.add('active-skill-badge--invalid');
            }

            const label = `#${spec.name}${spec.param ? ':' + spec.param : ''}`;
            badge.innerHTML = `<span>${label}</span><span class="active-skill-badge__remove" title="Remove skill">×</span>`;
            badge.querySelector('.active-skill-badge__remove').addEventListener('click', () => {
                removeSkillBadge(spec.name);
            });
            activeSkillTagsContainer.appendChild(badge);
        });
    }

    // --- Skill param picker (reuses skillSelector dropdown) ---
    let isShowingSkillParamPicker = false;
    let skillParamPickerValues = [];
    let skillParamPickerIndex = -1;

    function showSkillParamPicker(hints, typedPrefix) {
        const filtered = typedPrefix
            ? hints.filter(v => v.toLowerCase().startsWith(typedPrefix.toLowerCase()))
            : hints;

        skillParamPickerValues = filtered;
        skillParamPickerIndex = filtered.length > 0 ? 0 : -1;
        isShowingSkillParamPicker = true;

        skillSelector.innerHTML = '';

        const meta = activeSkills.length > 0 ? _getSkillMeta(activeSkills[activeSkills.length - 1].name) : null;
        const desc = meta?.param_descriptions ? 'Parameter' : 'Parameter';

        const header = document.createElement('div');
        header.className = 'skill-param-picker-header';
        const descSpan = document.createElement('span');
        descSpan.textContent = desc;
        header.appendChild(descSpan);
        skillSelector.appendChild(header);

        if (filtered.length === 0 && typedPrefix) {
            const noMatch = document.createElement('div');
            noMatch.className = 'skill-param-picker-no-params';
            noMatch.textContent = `"${typedPrefix}" is not a valid parameter`;
            skillSelector.appendChild(noMatch);
            skillSelector.classList.remove('hidden');
            return;
        }

        filtered.forEach((val, index) => {
            const item = document.createElement('div');
            item.className = 'skill-item';
            if (index === 0) item.classList.add('skill-highlighted');

            const itemHeader = document.createElement('div');
            itemHeader.className = 'skill-item-header';

            const badge = document.createElement('span');
            badge.className = 'skill-item-badge';
            badge.textContent = `:${val}`;
            itemHeader.appendChild(badge);

            // Show param description if available
            if (meta?.param_descriptions?.[val]) {
                const paramDesc = document.createElement('span');
                paramDesc.className = 'skill-item-name';
                paramDesc.textContent = meta.param_descriptions[val];
                itemHeader.appendChild(paramDesc);
            }

            item.appendChild(itemHeader);

            item.addEventListener('mousedown', (e) => {
                e.preventDefault();
                selectSkillParamValue(val);
            });
            item.addEventListener('mouseenter', () => {
                skillParamPickerIndex = index;
                _highlightSkillParamPicker(index);
            });

            skillSelector.appendChild(item);
        });

        skillSelector.classList.remove('hidden');
    }

    function showNoSkillParamsHint() {
        isShowingSkillParamPicker = true;
        skillParamPickerValues = [];
        skillParamPickerIndex = -1;
        skillSelector.innerHTML = '';

        const hint = document.createElement('div');
        hint.className = 'skill-param-picker-no-params';
        hint.textContent = 'This skill does not accept parameters';
        skillSelector.appendChild(hint);
        skillSelector.classList.remove('hidden');

        setTimeout(() => {
            if (isShowingSkillParamPicker && skillParamPickerValues.length === 0) {
                hideSkillParamPicker();
            }
        }, 1500);
    }

    function _highlightSkillParamPicker(index) {
        const items = skillSelector.querySelectorAll('.skill-item');
        items.forEach((item, idx) => {
            item.classList.toggle('skill-highlighted', idx === index);
        });
    }

    function selectSkillParamValue(val) {
        if (activeSkills.length > 0) {
            activeSkills[activeSkills.length - 1].param = val;
            window.activeSkills = activeSkills;
            _renderSkillBadges();
        }
        isUpdatingInput = true;
        userInput.value = '';
        isUpdatingInput = false;
        UI.autoResizeChatInput();
        hideSkillParamPicker();
        userInput.focus();
    }

    function hideSkillParamPicker() {
        skillSelector.innerHTML = '';
        skillSelector.classList.add('hidden');
        isShowingSkillParamPicker = false;
        skillParamPickerValues = [];
        skillParamPickerIndex = -1;
    }

    /**
     * Lookup manifest metadata for an activation name.
     * Returns the merged activation+manifest object from window.extensionState.activated.
     */
    function _getExtensionMeta(activationName) {
        return (window.extensionState?.activated || []).find(
            e => (e.activation_name || e.extension_id) === activationName
        );
    }

    /**
     * Validate a param value against the extension's allowed_values.
     * Returns: 'valid' | 'invalid' | 'none' (no restrictions)
     */
    function _validateExtensionParam(activationName, paramValue) {
        const meta = _getExtensionMeta(activationName);
        if (!meta) return 'none';
        const params = meta.parameters || {};
        if (!params.supported) return 'invalid'; // params not supported at all
        if (!params.allowed_values) return 'none'; // freeform
        if (!paramValue) return 'none';
        return params.allowed_values.includes(paramValue) ? 'valid' : 'invalid';
    }

    function _renderExtensionBadges() {
        activeExtensionTagsContainer.innerHTML = '';
        activeExtensions.forEach(spec => {
            const badge = document.createElement('span');
            badge.className = 'active-extension-badge';

            // Validate param
            const validation = spec.param ? _validateExtensionParam(spec.name, spec.param) : 'none';
            if (validation === 'invalid') {
                badge.classList.add('active-extension-badge--invalid');
            }

            const label = `!${spec.name}${spec.param ? ':' + spec.param : ''}`;
            badge.innerHTML = `<span>${label}</span><span class="active-extension-badge__remove" title="Remove extension">×</span>`;
            badge.querySelector('.active-extension-badge__remove').addEventListener('click', () => {
                removeExtensionBadge(spec.name);
            });
            activeExtensionTagsContainer.appendChild(badge);
        });
    }

    // --- Param picker (reuses extensionSelector dropdown) ---
    let isShowingParamPicker = false;
    let paramPickerValues = [];
    let paramPickerIndex = -1;

    function showParamPicker(hints, typedPrefix, isStrict) {
        // Filter hints by typed prefix
        const filtered = typedPrefix
            ? hints.filter(v => v.toLowerCase().startsWith(typedPrefix.toLowerCase()))
            : hints;

        paramPickerValues = filtered;
        paramPickerIndex = filtered.length > 0 ? 0 : -1;
        isShowingParamPicker = true;

        extensionSelector.innerHTML = '';

        // Header: description + strict/suggested mode badge
        const meta = activeExtensions.length > 0 ? _getExtensionMeta(activeExtensions[activeExtensions.length - 1].name) : null;
        const desc = meta?.parameters?.description || 'Parameter';

        const header = document.createElement('div');
        header.className = 'param-picker-header';

        const descSpan = document.createElement('span');
        descSpan.textContent = desc;
        header.appendChild(descSpan);

        const modeBadge = document.createElement('span');
        modeBadge.className = `param-picker-mode ${isStrict ? 'param-picker-mode--strict' : 'param-picker-mode--suggested'}`;
        modeBadge.textContent = isStrict ? 'Required' : 'Suggested';
        header.appendChild(modeBadge);

        extensionSelector.appendChild(header);

        if (filtered.length === 0 && typedPrefix) {
            // No matches for current prefix
            const noMatch = document.createElement('div');
            noMatch.className = 'param-picker-no-params';
            noMatch.textContent = isStrict
                ? `"${typedPrefix}" is not a valid parameter`
                : `No suggestions matching "${typedPrefix}"`;
            extensionSelector.appendChild(noMatch);
            extensionSelector.classList.remove('hidden');
            return;
        }

        filtered.forEach((val, index) => {
            const item = document.createElement('div');
            item.className = 'extension-item';
            if (index === 0) item.classList.add('extension-highlighted');

            const itemHeader = document.createElement('div');
            itemHeader.className = 'extension-item-header';

            const badge = document.createElement('span');
            badge.className = 'extension-item-badge';
            badge.textContent = `:${val}`;
            itemHeader.appendChild(badge);

            item.appendChild(itemHeader);

            item.addEventListener('mousedown', (e) => {
                e.preventDefault();
                selectParamValue(val);
            });
            item.addEventListener('mouseenter', () => {
                paramPickerIndex = index;
                _highlightParamPicker(index);
            });

            extensionSelector.appendChild(item);
        });

        extensionSelector.classList.remove('hidden');
    }

    /**
     * Show a "no parameters" hint in the dropdown.
     */
    function showNoParamsHint() {
        isShowingParamPicker = true;
        paramPickerValues = [];
        paramPickerIndex = -1;
        extensionSelector.innerHTML = '';

        const hint = document.createElement('div');
        hint.className = 'param-picker-no-params';
        hint.textContent = 'This extension does not accept parameters';
        extensionSelector.appendChild(hint);
        extensionSelector.classList.remove('hidden');

        // Auto-hide after 1.5s
        setTimeout(() => {
            if (isShowingParamPicker && paramPickerValues.length === 0) {
                hideParamPicker();
            }
        }, 1500);
    }

    function _highlightParamPicker(index) {
        const items = extensionSelector.querySelectorAll('.extension-item');
        items.forEach((item, idx) => {
            item.classList.toggle('extension-highlighted', idx === index);
        });
    }

    function hideParamPicker() {
        isShowingParamPicker = false;
        paramPickerValues = [];
        paramPickerIndex = -1;
        extensionSelector.innerHTML = '';
        extensionSelector.classList.add('hidden');
    }

    function selectParamValue(val) {
        if (activeExtensions.length > 0) {
            const last = activeExtensions[activeExtensions.length - 1];
            last.param = val;
            _renderExtensionBadges();
        }
        isUpdatingInput = true;
        userInput.value = '';
        isUpdatingInput = false;
        UI.autoResizeChatInput();
        hideParamPicker();
        userInput.focus();
    }

    async function fetchAndShowSuggestions(queryText) {
        if (!queryText || queryText.length < 2) {
            showSuggestions([]);
            return;
        }

        // Determine which profile to use for collection filtering
        let profileId = window.configState?.defaultProfileId || null;
        
        // Priority 1: Check if there's an active profile badge (user selected via @TAG)
        if (activeTagPrefix && window.configState?.profiles) {
            const tag = activeTagPrefix.replace('@', '').trim().toUpperCase();
            const overrideProfile = window.configState.profiles.find(p => p.tag.toUpperCase() === tag);
            if (overrideProfile) {
                profileId = overrideProfile.id;
            }
        }
        // Priority 2: Check if there's an active profile override from previous message
        else if (window.activeProfileOverrideId) {
            profileId = window.activeProfileOverrideId;
        }
        // Priority 3: Check if query text starts with @TAG (not yet selected)
        else {
            const tagMatch = queryText.match(/^@([\w-]+)\s/);
            if (tagMatch && window.configState?.profiles) {
                const tag = tagMatch[1].toUpperCase();
                const overrideProfile = window.configState.profiles.find(p => p.tag.toUpperCase() === tag);
                if (overrideProfile) {
                    profileId = overrideProfile.id;
                    // Remove @TAG from query for autocomplete search
                    queryText = queryText.substring(tagMatch[0].length);
                }
            }
        }
        
        // Check if autocomplete is disabled for the resolved profile
        if (profileId && window.configState?.profiles) {
            const profile = window.configState.profiles.find(p => p.id === profileId);
            if (profile && Array.isArray(profile.autocompleteCollections) && profile.autocompleteCollections.length === 0) {
                showSuggestions([]);
                return;
            }
        }

        // Fetch semantically ranked questions from backend
        const questions = await API.fetchRAGQuestions(queryText, profileId, 5);
        // Guard: discard stale results if input was cleared while request was in-flight
        if (userInput.value.trim().length >= 2) {
            showSuggestions(questions);
        }
    }

    if (suggestionsContainer && userInput) {
        userInput.addEventListener('focus', () => {
            const inputValue = userInput.value.trim();
            if (inputValue.length >= 2) {
                fetchAndShowSuggestions(inputValue);
            } else {
                showSuggestions([]);
            }
        });

        userInput.addEventListener('input', () => {
            const inputValue = userInput.value;

            // Grow/shrink the composer to fit the text (covers typing, paste, cut).
            UI.autoResizeChatInput();

            // Don't update tag badge if we're in the middle of programmatic changes
            if (!isUpdatingInput) {
                // If we have an active tag, check if the full message (prefix + input) still has a valid tag
                if (activeTagPrefix) {
                    // Reconstruct what the full message would look like
                    const fullMessage = activeTagPrefix + inputValue;
                    const tagMatch = fullMessage.match(/^@([\w-]+)\s/);

                    if (!tagMatch) {
                        // Tag pattern is no longer valid - user deleted it
                        activeTagPrefix = '';
                        hideActiveTagBadge();
                    }
                } else {
                    // No active tag - check if user is adding one
                    updateTagBadge();
                }
            }

            // Re-filter the Next-Step dock to the composer's effective profile
            // (debounced, 0-token cache-first rebind). Profile-class-specific chips
            // change as the user adds/removes an @TAG override.
            scheduleProfileRebind();

            // Clear previous timer
            if (debounceTimer) {
                clearTimeout(debounceTimer);
            }

            // Check if user typed @ at the start (profile tag selector)
            // But only if we don't have an active tag badge already
            if (!activeTagPrefix && inputValue.startsWith('@') && !inputValue.includes(' ')) {
                // User is typing @TAG but hasn't added space yet - show profile selector
                hideProfileSelector();
                showSuggestions([]);
                
                // Show only active profiles with tags
                const profiles = window.configState?.profiles || [];
                const activeIds = window.configState?.activeForConsumptionProfileIds || [];
                const defaultProfileId = window.configState?.defaultProfileId;
                
                const activeProfilesWithTags = profiles.filter(p => 
                    p.tag && activeIds.includes(p.id)
                );
                
                // Sort profiles: default first, then by IFOC order (Ideate → Focus → Optimize → Coordinate), then alphabetical within each group
                const ifocOrder = { 'llm_only': 0, 'rag_focused': 1, 'tool_enabled': 2, 'genie': 3 };
                const sortedProfiles = activeProfilesWithTags.sort((a, b) => {
                    if (a.id === defaultProfileId) return -1;
                    if (b.id === defaultProfileId) return 1;
                    const typeA = ifocOrder[a.profile_type] ?? 99;
                    const typeB = ifocOrder[b.profile_type] ?? 99;
                    if (typeA !== typeB) return typeA - typeB;
                    return (a.name || '').localeCompare(b.name || '');
                });
                
                if (inputValue === '@') {
                    // Show all active profiles
                    showProfileSelector(sortedProfiles);
                } else {
                    // Filter active profiles by partial tag match
                    const partialTag = inputValue.substring(1).toUpperCase();
                    const filteredProfiles = sortedProfiles.filter(p => 
                        p.tag.toUpperCase().startsWith(partialTag)
                    );
                    showProfileSelector(filteredProfiles);
                }
                return;
            }

            // Hide profile selector if we're past the tag selection phase
            if (isShowingProfileSelector) {
                hideProfileSelector();
            }

            // Check if user is typing :param for the last skill or extension badge
            const colonMatch = inputValue.match(/^:(\S*)$/);
            if (colonMatch) {
                const typedPrefix = colonMatch[1] || '';

                // Priority: check skills first (most recently added badge), then extensions
                if (activeSkills.length > 0) {
                    const last = activeSkills[activeSkills.length - 1];
                    last.param = typedPrefix || null;
                    _renderSkillBadges();

                    const meta = _getSkillMeta(last.name);
                    const allowed = meta?.allowed_params || [];
                    if (allowed.length === 0) {
                        if (!isShowingSkillParamPicker) showNoSkillParamsHint();
                    } else {
                        showSkillParamPicker(allowed, typedPrefix);
                    }
                    return;
                } else if (activeExtensions.length > 0) {
                    const last = activeExtensions[activeExtensions.length - 1];
                    last.param = typedPrefix || null;
                    _renderExtensionBadges();

                    const meta = _getExtensionMeta(last.name);
                    const paramDef = meta?.parameters || {};
                    if (paramDef.supported === false) {
                        if (!isShowingParamPicker) showNoParamsHint();
                    } else {
                        const allowed = paramDef.allowed_values || null;
                        const hints = allowed || paramDef.examples || [];
                        if (hints.length > 0) {
                            showParamPicker(hints, typedPrefix, !!allowed);
                        } else if (isShowingParamPicker) {
                            hideParamPicker();
                        }
                    }
                    return;
                }
            }
            // Hide param pickers if not in :param mode
            if (isShowingParamPicker) {
                hideParamPicker();
            }
            if (isShowingSkillParamPicker) {
                hideSkillParamPicker();
            }

            // Check if user is typing #skill (show skill autocomplete)
            const hashMatch = inputValue.match(/#(\w*)$/);
            if (hashMatch && window.skillState?.activated?.length > 0) {
                const prefix = hashMatch[1].toLowerCase();
                const filtered = window.skillState.activated.filter(s =>
                    (s.activation_name || s.skill_id).toLowerCase().startsWith(prefix)
                );
                if (filtered.length > 0) {
                    showSkillSelector(filtered);
                    return;
                }
            }
            // Hide skill selector if not typing #
            if (isShowingSkillSelector) {
                hideSkillSelector();
            }

            // Check if user is typing !extension (show extension autocomplete)
            const bangMatch = inputValue.match(/!(\w*)$/);
            if (bangMatch && window.extensionState?.activated?.length > 0) {
                const prefix = bangMatch[1].toLowerCase();
                const filtered = window.extensionState.activated.filter(e =>
                    (e.activation_name || e.extension_id).toLowerCase().startsWith(prefix)
                );
                if (filtered.length > 0) {
                    showExtensionSelector(filtered);
                    return;
                }
            }
            // Hide extension selector if not typing !
            if (isShowingExtensionSelector) {
                hideExtensionSelector();
            }

            const trimmedValue = inputValue.trim();
            if (trimmedValue.length >= 2) {
                // Debounce API calls (300ms delay)
                debounceTimer = setTimeout(() => {
                    fetchAndShowSuggestions(trimmedValue);
                }, 300);
            } else {
                showSuggestions([]);
            }
        });

        window.cancelRagSuggestions = () => {
            if (debounceTimer) {
                clearTimeout(debounceTimer);
                debounceTimer = null;
            }
            showSuggestions([]);
        };

        userInput.addEventListener('keydown', (e) => {
            // Enter submits; Shift+Enter inserts a newline. The element is now a
            // <textarea>, so the browser would otherwise insert a newline on Enter.
            // Yield Enter to any open autocomplete/picker so it can commit a
            // selection; skip while an IME composition is in progress (CJK input).
            if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
                const selectorCapturesEnter =
                    (isShowingSkillParamPicker && skillParamPickerValues.length > 0) ||
                    (isShowingParamPicker && paramPickerValues.length > 0) ||
                    (isShowingProfileSelector && currentProfiles.length > 0) ||
                    (isShowingSkillSelector && currentSkills.length > 0) ||
                    (isShowingExtensionSelector && currentExtensions.length > 0);
                if (!selectorCapturesEnter) {
                    e.preventDefault();
                    if (DOM.chatForm && !DOM.userInput.disabled) {
                        DOM.chatForm.requestSubmit
                            ? DOM.chatForm.requestSubmit()
                            : DOM.chatForm.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
                    }
                    return;
                }
                // else: fall through so the open selector's Enter handler commits.
            }

            // Handle skill param picker navigation and commit
            if (isShowingSkillParamPicker && skillParamPickerValues.length > 0) {
                if (e.key === 'ArrowDown') {
                    e.preventDefault();
                    skillParamPickerIndex = (skillParamPickerIndex + 1) % skillParamPickerValues.length;
                    _highlightSkillParamPicker(skillParamPickerIndex);
                    return;
                } else if (e.key === 'ArrowUp') {
                    e.preventDefault();
                    skillParamPickerIndex = (skillParamPickerIndex - 1 + skillParamPickerValues.length) % skillParamPickerValues.length;
                    _highlightSkillParamPicker(skillParamPickerIndex);
                    return;
                } else if ((e.key === 'Tab' || e.key === 'Enter') && skillParamPickerIndex >= 0) {
                    e.preventDefault();
                    selectSkillParamValue(skillParamPickerValues[skillParamPickerIndex]);
                    return;
                } else if (e.key === 'Escape') {
                    hideSkillParamPicker();
                    return;
                }
            }

            // Handle param picker navigation and commit
            if (isShowingParamPicker && paramPickerValues.length > 0) {
                if (e.key === 'ArrowDown') {
                    e.preventDefault();
                    paramPickerIndex = (paramPickerIndex + 1) % paramPickerValues.length;
                    _highlightParamPicker(paramPickerIndex);
                    return;
                } else if (e.key === 'ArrowUp') {
                    e.preventDefault();
                    paramPickerIndex = (paramPickerIndex - 1 + paramPickerValues.length) % paramPickerValues.length;
                    _highlightParamPicker(paramPickerIndex);
                    return;
                } else if ((e.key === 'Tab' || e.key === 'Enter') && paramPickerIndex >= 0) {
                    e.preventDefault();
                    selectParamValue(paramPickerValues[paramPickerIndex]);
                    return;
                } else if (e.key === 'Escape') {
                    hideParamPicker();
                    return;
                }
            }

            // Commit :param to last skill or extension badge on Space or Tab (no picker match)
            const paramInput = userInput.value.match(/^:(\S+)$/);
            if (paramInput && (e.key === ' ' || e.key === 'Tab')) {
                if (activeSkills.length > 0) {
                    e.preventDefault();
                    isUpdatingInput = true;
                    userInput.value = '';
                    isUpdatingInput = false;
                    UI.autoResizeChatInput();
                    hideSkillParamPicker();
                    return;
                } else if (activeExtensions.length > 0) {
                    e.preventDefault();
                    isUpdatingInput = true;
                    userInput.value = '';
                    isUpdatingInput = false;
                    UI.autoResizeChatInput();
                    hideParamPicker();
                    return;
                }
            }

            // Handle backspace on empty input: remove last extension badge, then skill badge, then profile tag
            if (e.key === 'Backspace' && userInput.value === '') {
                if (activeExtensions.length > 0) {
                    e.preventDefault();
                    const last = activeExtensions[activeExtensions.length - 1];
                    removeExtensionBadge(last.name);
                    return;
                }
                if (activeSkills.length > 0) {
                    e.preventDefault();
                    const last = activeSkills[activeSkills.length - 1];
                    removeSkillBadge(last.name);
                    return;
                }
                if (activeTagPrefix) {
                    e.preventDefault();
                    activeTagPrefix = '';
                    hideActiveTagBadge();
                    return;
                }
            }
            
            // Handle profile selector navigation
            if (isShowingProfileSelector && currentProfiles.length > 0) {
                const defaultProfileId = window.configState?.defaultProfileId;
                const selectableProfiles = currentProfiles.filter(p => p.id !== defaultProfileId);
                
                if (e.key === 'ArrowDown') {
                    e.preventDefault();
                    // If no selectable profiles, don't navigate
                    if (selectableProfiles.length === 0) return;
                    
                    let nextIndex = (profileSelectedIndex + 1) % currentProfiles.length;
                    let iterations = 0;
                    const maxIterations = currentProfiles.length;
                    
                    // Skip default profile
                    while (iterations < maxIterations && currentProfiles[nextIndex].id === defaultProfileId) {
                        nextIndex = (nextIndex + 1) % currentProfiles.length;
                        iterations++;
                    }
                    
                    // Only update if we found a selectable profile
                    if (iterations < maxIterations) {
                        profileSelectedIndex = nextIndex;
                        highlightProfile(profileSelectedIndex);
                    }
                } else if (e.key === 'ArrowUp') {
                    e.preventDefault();
                    // If no selectable profiles, don't navigate
                    if (selectableProfiles.length === 0) return;
                    
                    let prevIndex = (profileSelectedIndex - 1 + currentProfiles.length) % currentProfiles.length;
                    let iterations = 0;
                    const maxIterations = currentProfiles.length;
                    
                    // Skip default profile
                    while (iterations < maxIterations && currentProfiles[prevIndex].id === defaultProfileId) {
                        prevIndex = (prevIndex - 1 + currentProfiles.length) % currentProfiles.length;
                        iterations++;
                    }
                    
                    // Only update if we found a selectable profile
                    if (iterations < maxIterations) {
                        profileSelectedIndex = prevIndex;
                        highlightProfile(profileSelectedIndex);
                    }
                } else if ((e.key === 'Tab' || e.key === 'Enter') && profileSelectedIndex >= 0) {
                    e.preventDefault();
                    // Only select if not default profile
                    if (currentProfiles[profileSelectedIndex].id !== defaultProfileId) {
                        selectProfile(profileSelectedIndex);
                    }
                } else if (e.key === 'Escape') {
                    hideProfileSelector();
                }
                return;
            }

            // Handle skill selector navigation (skip auto-active items)
            if (isShowingSkillSelector && currentSkills.length > 0) {
                const _nextSelectable = (from, dir) => {
                    const len = currentSkills.length;
                    for (let i = 1; i <= len; i++) {
                        const idx = (from + dir * i + len) % len;
                        if (!currentSkills[idx].active) return idx;
                    }
                    return -1;
                };
                if (e.key === 'ArrowDown') {
                    e.preventDefault();
                    const next = _nextSelectable(skillSelectedIndex, 1);
                    if (next >= 0) { skillSelectedIndex = next; highlightSkill(skillSelectedIndex); }
                } else if (e.key === 'ArrowUp') {
                    e.preventDefault();
                    const prev = _nextSelectable(skillSelectedIndex, -1);
                    if (prev >= 0) { skillSelectedIndex = prev; highlightSkill(skillSelectedIndex); }
                } else if ((e.key === 'Tab' || e.key === 'Enter') && skillSelectedIndex >= 0) {
                    e.preventDefault();
                    selectSkill(skillSelectedIndex);
                } else if (e.key === 'Escape') {
                    hideSkillSelector();
                }
                return;
            }

            // Handle extension selector navigation
            if (isShowingExtensionSelector && currentExtensions.length > 0) {
                if (e.key === 'ArrowDown') {
                    e.preventDefault();
                    extensionSelectedIndex = (extensionSelectedIndex + 1) % currentExtensions.length;
                    highlightExtension(extensionSelectedIndex);
                } else if (e.key === 'ArrowUp') {
                    e.preventDefault();
                    extensionSelectedIndex = (extensionSelectedIndex - 1 + currentExtensions.length) % currentExtensions.length;
                    highlightExtension(extensionSelectedIndex);
                } else if ((e.key === 'Tab' || e.key === 'Enter') && extensionSelectedIndex >= 0) {
                    e.preventDefault();
                    selectExtension(extensionSelectedIndex);
                } else if (e.key === 'Escape') {
                    hideExtensionSelector();
                }
                return;
            }

            // Handle autocomplete navigation
            if (currentSuggestions.length === 0) return;

            if (e.key === 'ArrowDown') {
                e.preventDefault();
                selectedIndex = (selectedIndex + 1) % currentSuggestions.length;
                highlightSuggestion(selectedIndex);
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                selectedIndex = (selectedIndex - 1 + currentSuggestions.length) % currentSuggestions.length;
                highlightSuggestion(selectedIndex);
            } else if (e.key === 'Tab' && selectedIndex >= 0) {
                e.preventDefault();
                selectSuggestion(selectedIndex);
            } else if (e.key === 'Escape') {
                suggestionsContainer.innerHTML = '';
                suggestionsContainer.classList.add('hidden');
                currentSuggestions = [];
                selectedIndex = -1;
            }
        });

        userInput.addEventListener('blur', () => {
            setTimeout(() => {
                suggestionsContainer.innerHTML = '';
                suggestionsContainer.classList.add('hidden');
                currentSuggestions = [];
                selectedIndex = -1;
                hideProfileSelector();
                hideExtensionSelector();
            }, 150);
        });

        // The grow cap follows the room the conversation column has — recompute the fit (and
        // whether the internal scrollbar is needed) whenever that column changes size: a window
        // resize, and on iOS the keyboard, which fires no window resize (appViewport.js resizes
        // the shell). The composer's own height (attachments, the receipt, the next-step dock) is
        // republished whenever it changes: it is the column's minimum (main.css).
        if (typeof ResizeObserver === 'function') {
            const mainContent = document.getElementById('main-content');
            const chatFooter = document.getElementById('chat-footer');
            const column = document.getElementById('conversation-column');   // the conversation header above it changed
            const refit = new ResizeObserver(() => UI.autoResizeChatInput());
            if (mainContent) refit.observe(mainContent);
            if (column) refit.observe(column);
            if (chatFooter) new ResizeObserver(() => UI.publishComposerHeight()).observe(chatFooter);
        } else {
            window.addEventListener('resize', () => UI.autoResizeChatInput());
        }
        // The chat never scrolls sideways (main.css): a scrollIntoView() that still finds width to
        // reveal is put back, so the conversation is never left shifted with no way to swipe back.
        const chatScroller = document.getElementById('chat-container');
        if (chatScroller) chatScroller.addEventListener('scroll', () => { if (chatScroller.scrollLeft) chatScroller.scrollLeft = 0; }, { passive: true });

        // Establish the correct one-line height on first paint.
        UI.autoResizeChatInput();
    }
}

/**
 * Load activated extensions from the API for ! autocomplete.
 * Stores results in window.extensionState.activated for access by the input handler.
 */
async function loadActivatedExtensions() {
    try {
        const token = localStorage.getItem('tda_auth_token');
        if (!token) return;

        const response = await coalescedFetch('/api/v1/extensions/activated', {
            headers: { 'Authorization': `Bearer ${token}` }
        });

        if (response.ok) {
            const data = await response.json();
            window.extensionState = { activated: data.extensions || [] };
            console.log(`[Extensions] Loaded ${window.extensionState.activated.length} activated extension(s)`);
        }
    } catch (err) {
        console.warn('[Extensions] Failed to load activated extensions:', err);
        window.extensionState = { activated: [] };
    }
}
// Expose for refresh from Extensions config tab
window.loadActivatedExtensions = loadActivatedExtensions;

/**
 * Load enabled skills from the current profile for # autocomplete.
 * Uses /api/v1/profiles/<id>/skills which returns enriched skill data.
 * Stores results in window.skillState.activated for access by the input handler.
 */
async function loadActivatedSkills() {
    try {
        const token = localStorage.getItem('tda_auth_token');
        if (!token) return;

        const profileId = state.currentResourcePanelProfileId;
        if (!profileId) {
            window.skillState = { activated: [] };
            return;
        }

        const headers = { 'Authorization': `Bearer ${token}` };

        const res = await coalescedFetch(`/api/v1/profiles/${encodeURIComponent(profileId)}/skills`, { headers });
        if (!res.ok) {
            window.skillState = { activated: [] };
            return;
        }

        const data = await res.json();

        // Collect enabled skills for #hashtag autocomplete
        // Auto-active skills are included but marked so they render as non-selectable
        const enabledSkills = [];
        for (const skill of (data.skills || [])) {
            if (!skill.available) continue;
            if (!skill.enabled && !skill.active) continue;
            enabledSkills.push({
                skill_id: skill.id,
                activation_name: skill.id,
                name: skill.name || skill.id,
                description: skill.description || '',
                default_param: skill.param || null,
                active: !!skill.active,
            });
        }

        window.skillState = { activated: enabledSkills };
        console.log(`[Skills] Loaded ${enabledSkills.length} enabled skill(s) from profile`);
    } catch (err) {
        console.warn('[Skills] Failed to load activated skills:', err);
        window.skillState = { activated: [] };
    }
}
// Expose for refresh from Skills config tab
window.loadActivatedSkills = loadActivatedSkills;

/**
 * Extract user ID from JWT authentication for SSE notifications.
 * User identification is now handled via JWT authentication tokens.
 */
function ensureUserUUID() {
    // Extract user ID from JWT-stored user object
    if (window.authClient) {
        const user = window.authClient.getUser();
        if (user && user.id) {
            state.userUUID = user.id;
            console.log('[Auth] User ID extracted from JWT:', user.id);
        } else {
            console.warn('[Auth] No user ID found in session');
        }
    } else {
        console.warn('[Auth] authClient not initialized');
    }
}

/**
 * Fetches the current star count for the GitHub repository and updates the UI.
 * The GitHub API call is OFF by default (the repository is not public); --gitcall enables it.
 * Hides the button completely if disabled.
 */
async function fetchGitHubStarCount() {
    const starButtonElement = document.querySelector('a[href="https://github.com/rgeissen/uderia-core"]');
    const starCountElement = document.getElementById('github-star-count');
    const starIconElement = starButtonElement ? starButtonElement.querySelector('svg') : null;
    
    // The button is rendered only under the default brand (index.html `brand.is_default`);
    // a white-label brand omits it by design, so its absence is not an error.
    if (!starCountElement || !starButtonElement) return;

    // Check if GitHub API is enabled
    try {
        const settingsResponse = await fetch('/app-settings');
        if (settingsResponse.ok) {
            const settings = await settingsResponse.json();
            console.log('GitHub API enabled status:', settings.github_api_enabled);
            if (!settings.github_api_enabled) {
                console.log('GitHub API disabled (default; --gitcall enables). Button hidden.');
                starButtonElement.style.display = 'none';
                return;
            }
            // If enabled, ensure button is visible
            starButtonElement.style.display = 'flex';
        }
    } catch (error) {
        console.error('Error checking app settings:', error);
        starButtonElement.style.display = 'none';
        return;
    }

    // A repository GitHub does not show to anonymous callers (private, renamed, removed)
    // answers 404: the count can never load and the button would lead a visitor to a 404
    // page. That answer is remembered for a day so the page does not ask on every load.
    const _HIDDEN_KEY = 'uderia_github_repo_not_public_until';
    try {
        if (Number(localStorage.getItem(_HIDDEN_KEY) || 0) > Date.now()) {
            starButtonElement.style.display = 'none';
            return;
        }
    } catch (_) { /* storage unavailable — ask GitHub */ }

    try {
        const response = await fetch('https://api.github.com/repos/rgeissen/uderia-core', {
            method: 'GET',
            headers: {
                'Accept': 'application/vnd.github.v3+json'
            },
            mode: 'cors'
        });
        
        
        if (response.ok) {
            const data = await response.json();
            const starCount = data.stargazers_count || 0;
            // Format the number with comma separators
            starCountElement.textContent = starCount.toLocaleString('en-US');
            
            // Update star icon based on count (filled if > 0, outline if 0)
            if (starIconElement) {
                if (starCount > 0) {
                    starIconElement.setAttribute('fill', 'currentColor');
                } else {
                    starIconElement.setAttribute('fill', 'none');
                    starIconElement.setAttribute('stroke', 'currentColor');
                    starIconElement.setAttribute('stroke-width', '1.5');
                }
            }
            
        } else if (response.status === 404) {
            // not publicly visible: no count to show and no page a visitor could open
            starButtonElement.style.display = 'none';
            try { localStorage.setItem(_HIDDEN_KEY, String(Date.now() + 24 * 3600 * 1000)); } catch (_) {}
        } else {
            // rate limit or an outage: keep the button, say nothing loud about it
            console.warn('GitHub star count unavailable:', response.status);
            starCountElement.textContent = '-';
        }
    } catch (error) {
        console.error('Error fetching GitHub star count:', error);
        console.error('Error details:', error.message, error.stack);
        starCountElement.textContent = '-';
    }
}

// REMOVED: loadInitialConfig() function - obsolete with new configuration system
// The new configuration system (configState) automatically loads from localStorage
// and doesn't need manual form population

/**
 * Update Intelligence and Marketplace navigation state based on current active profile
 * Disables these views if no profile is set as default (unconfigured system)
 */
function updatePlannerRepositoryNavigation() {
    const intelligenceNavLink = document.getElementById('view-switch-rag-maintenance');
    const marketplaceNavLink = document.getElementById('view-switch-rag-marketplace');
    
    if (!intelligenceNavLink || !marketplaceNavLink) {
        console.warn('[PlannerRepository] Navigation links not found in DOM');
        return;
    }
    
    // Get the currently active (default) profile
    const defaultProfileId = window.configState?.defaultProfileId;
    
    console.log('[PlannerRepository] Checking navigation state. defaultProfileId:', defaultProfileId);
    
    // Disable navigation if no default profile is set
    if (!defaultProfileId) {
        intelligenceNavLink.classList.add('opacity-50', 'cursor-not-allowed', 'pointer-events-none');
        marketplaceNavLink.classList.add('opacity-50', 'cursor-not-allowed', 'pointer-events-none');
        intelligenceNavLink.setAttribute('title', 'Set a default profile to access Planner Repository');
        marketplaceNavLink.setAttribute('title', 'Set a default profile to access Planner Repository Marketplace');
        console.log('[PlannerRepository] Navigation DISABLED - no default profile set');
        return;
    }
    
    // If we reach here, a default profile is set - enable navigation
    {
        // Enable Intelligence and Marketplace views
        intelligenceNavLink.classList.remove('opacity-50', 'cursor-not-allowed', 'pointer-events-none');
        marketplaceNavLink.classList.remove('opacity-50', 'cursor-not-allowed', 'pointer-events-none');
        
        // Restore original titles
        intelligenceNavLink.setAttribute('title', '');
        marketplaceNavLink.setAttribute('title', '');
        
        console.log('[PlannerRepository] Navigation ENABLED - default profile is set:', defaultProfileId);
    }
}

// Export for use in other modules
window.updatePlannerRepositoryNavigation = updatePlannerRepositoryNavigation;

// Define welcome screen functions BEFORE DOMContentLoaded to ensure they're available
function hideWelcomeScreen() {
    const welcomeScreen = document.getElementById('welcome-screen');
    const chatLog = document.getElementById('chat-log');
    const chatFooter = document.getElementById('chat-footer');
    const chatContainer = document.getElementById('chat-container');
    
    if (welcomeScreen && chatLog) {
        welcomeScreen.classList.add('hidden');
        chatLog.classList.remove('hidden');
        chatLog.style.display = 'block';  // Ensure it's visible
        if (chatFooter) {
            chatFooter.classList.remove('hidden');
            chatFooter.style.display = 'block';  // Ensure it's visible
        }
        if (chatContainer) {
            chatContainer.style.display = 'flex';
            chatContainer.style.flexDirection = 'column';
        }
        
        // Show all panels (History, Live Status, Resource) when welcome screen is hidden
        document.querySelectorAll('[data-requires-config="true"]').forEach(panel => {
            panel.style.display = '';  // Reset to default (CSS will control visibility)
        });

        const conversationHeader = document.getElementById('conversation-header');
        if (conversationHeader) conversationHeader.classList.remove('hidden');
    }
}

// Make function available globally
window.hideWelcomeScreen = hideWelcomeScreen;

// The startup. Registered on DOMContentLoaded, and run directly when the document is already
// loaded by the time this module evaluates (a module graph that fetches before it evaluates —
// the component renderers — can cross that moment; 11 Nov 2026). It first awaits the renderer
// registry, so every component row rendered below finds its renderer.
const _startup = async () => {
    await renderersReady;
    const savedShowWelcomeScreen = localStorage.getItem('showWelcomeScreenAtStartup');
    state.showWelcomeScreenAtStartup = savedShowWelcomeScreen === null ? true : savedShowWelcomeScreen === 'true';
    const welcomeScreenCheckbox = document.getElementById('toggle-welcome-screen-checkbox');
    const welcomeScreenPopupCheckbox = document.getElementById('welcome-screen-show-at-startup-checkbox');
    if (welcomeScreenCheckbox) {
        welcomeScreenCheckbox.checked = state.showWelcomeScreenAtStartup;
    }
    if (welcomeScreenPopupCheckbox) {
        welcomeScreenPopupCheckbox.checked = state.showWelcomeScreenAtStartup;
    }

    ensureUserUUID(); // Get/Set the User UUID right away
    subscribeToNotifications();
    initializeRAGAutoCompletion();
    initializeMarketplace();
    
    // Make marketplace functions globally available
    window.marketplaceHandler = {
        unsubscribeFromCollection: unsubscribeFromCollection
    };
    
    // Note: currentSessionId is intentionally NOT restored from localStorage here.
    // It is read lazily inside reconnectAndLoad() once the session is confirmed to exist,
    // avoiding 404 console errors from loadContextPanel() on stale session references.

    // Fetch GitHub star count
    fetchGitHubStarCount();

    // REMOVED: loadInitialConfig() - obsolete with new configuration system
    // The new configuration system uses configState which loads from localStorage automatically

    // Initialize all event listeners first to ensure they are ready.
    initializeEventListeners();
    initializeVoiceRecognition();
    initializeUploadUI();
    initSkillsPanelEvents();
    initComponentsPanelEvents();
    initResourcePanel();

    // Apply each component's manifest accent (single source of truth) to its
    // split-panel <aside> as --sp-accent. Keeps panel chrome in sync with the
    // admin card icons and avoids hardcoding the colour in index.html.
    import('./handlers/componentHandler.js?v=1.189').then(m => m.applyComponentPanelAccents && m.applyComponentPanelAccents()).catch(() => {});

    // Initialize industrial hierarchy path highlighting for session tree
    initializePathHighlighting();

    // Sync wrapper collapsed states on page load (hide tree connectors for collapsed children)
    syncWrapperStates();

    // Import and wire repository tabs
    const { wireRepositoryTabs } = await import('./eventHandlers.js?v=4.214');
    wireRepositoryTabs();
    
    // Initialize utility sessions filter for sidebar
    const { initializeUtilitySessionsFilter, initializeArchivedSessionsFilter } = await import('./handlers/sessionManagement.js?v=3.209');
    initializeUtilitySessionsFilter();
    initializeArchivedSessionsFilter();
    
    // Initialize Execution Dashboard
    if (window.ExecutionDashboard) {
        window.executionDashboard = new window.ExecutionDashboard();
    } else {
    }

    // Initialize new configuration UI (async - loads MCP servers from backend)
    await initializeConfigurationUI();

    // Update resource panel with default profile after configuration loads.
    //
    // DELIBERATELY NOT AWAITED. This call's first await is a /profiles/<id>/resources
    // fetch that round-trips the MCP server (measured: ~5.2s of a ~8s login). It
    // populates the resource panel, which showWelcomeScreen() explicitly HIDES
    // ([data-requires-config="true"]) — so awaiting it blocked the welcome screen on
    // work the user cannot see. Everything before that fetch (currentResourcePanelProfileId,
    // and the KG / Context / Workspace / Skills / Extensions / Components loaders) runs
    // synchronously on invocation, so the only thing deferred is the hidden panel's
    // tools/prompts. finalizeConfiguration() awaits this same function again on the way
    // into the conversation, so the panel is still fully populated before it is shown.
    // Six of the other eight call sites already invoke it without awaiting.
    if (window.configState?.defaultProfileId) {
        updateResourcePanelForProfile(window.configState.defaultProfileId)
            .then(() => console.log('[Startup] Resource panel initialized with default profile:', window.configState.defaultProfileId))
            .catch(e => console.warn('[Startup] Resource panel init failed (non-blocking):', e));
    }

    // Auto-initialize silently if a default profile is already configured and activated.
    // This allows repository constructors to work immediately without "Save & Connect".
    import('./conversationInitializer.js?v=1.190').then(({ autoInitializeIfReady }) => {
        autoInitializeIfReady().then(result => {
            if (!result.success) {
                console.log('[Startup] Auto-init skipped:', result.reason, result.detail || '');
            }
        }).catch(err => console.warn('[Startup] Silent auto-init failed:', err));
    });

    // Load activated extensions for ! autocomplete
    loadActivatedExtensions();

    // Load activated skills for # autocomplete
    loadActivatedSkills();

    // Hide prompt editor menu item if it exists (may be removed from UI)
    if (DOM.promptEditorButton?.parentElement) {
        DOM.promptEditorButton.parentElement.style.display = 'none';
    }

    try {
        // Use the app config that was already fetched in index.html
        // Wait for it to be available if it hasn't loaded yet
        let attempts = 0;
        while (!window.appConfigData && attempts < 50) {
            await new Promise(resolve => setTimeout(resolve, 100));
            attempts++;
        }
        
        if (window.appConfigData) {
            state.appConfig = window.appConfigData;
        } else {
            // Fallback: fetch directly if window.appConfigData isn't available
            const token = localStorage.getItem('tda_auth_token');
            const res = await fetch('/app-config', {
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });
            state.appConfig = await res.json();
        }

        // Not awaited: it only reconciles locally cached custom prompts with the server's
        // prompt version — nothing on the way to the welcome screen reads them.
        API.checkAndUpdateDefaultPrompts();

        updateVoiceButtonVisibility(state.appConfig.voice_conversation_enabled);

        // Initialize user TTS settings section based on tts_mode
        updateUserTtsSection(state.appConfig.tts_mode || 'disabled');

        // Initialize upload button visibility (requires auth token to be available)
        initializeUploadCapabilities(state.currentSessionId || undefined);

        if (DOM.ccrStatusDot) {
            if (!state.appConfig.rag_enabled) {
                DOM.ccrStatusDot.parentElement.style.display = 'none';
            } else {
                DOM.ccrStatusDot.parentElement.style.display = 'flex';
            }
        }

    } catch (e) {
        console.error("Could not fetch app config", e);
    }

    // Initialize panels as hidden and disabled until conversation pane is entered
    // Panels will be configured based on admin settings when entering conversation view
    DOM.sessionHistoryPanel.style.display = 'none';
    DOM.statusWindow.style.display = 'none';
    DOM.toolHeader.style.display = 'none';
    DOM.toggleHistoryButton.style.display = 'none';
    DOM.toggleStatusButton.style.display = 'none';
    DOM.toggleHeaderButton.style.display = 'none';

    try {
        const status = await API.checkServerStatus();
        
        // Note: Credentials are ALWAYS stored in localStorage
        
        // Update CCR (Champion Case Retrieval) indicator based on status
        if (DOM.ccrStatusDot && state.appConfig.rag_enabled) {
            if (status.rag_active) {
                DOM.ccrStatusDot.classList.remove('disconnected');
                DOM.ccrStatusDot.classList.add('connected');
            } else {
                DOM.ccrStatusDot.classList.remove('connected');
                DOM.ccrStatusDot.classList.add('disconnected');
            }
        }

        if (status.isConfigured) {

            // Check authentication status - show welcome screen for unauthenticated users
            const isAuthenticated = window.authClient && window.authClient.isAuthenticated();
            
            // Check if admin has enabled "always show welcome screen"
            const alwaysShowWelcome = state.appConfig?.window_defaults?.always_show_welcome_screen || false;
            
            if (!isAuthenticated || alwaysShowWelcome) {
                // Show welcome screen for unauthenticated users or if admin setting enabled
                await showWelcomeScreen();
            } else {
                // Normal flow - finalize configuration and show chat interface
                // NOTE: With new config UI, we don't need to pre-fill old form fields
                // The configurationHandler manages its own state via localStorage
                
                // Old code removed:
                // DOM.llmProviderSelect.value = status.provider;
                // DOM.mcpServerNameInput.value = status.mcp_server.name;
                // await loadCredentialsAndModels();

                const currentConfig = { provider: status.provider, model: status.model };
                // Pass the mcp_server details from status to ensure they are used if re-finalizing
                currentConfig.mcp_server = status.mcp_server;
                await finalizeConfiguration(currentConfig, true);

                // handleViewSwitch is now called inside finalizeConfiguration
            }

        } else {
            // The new configuration UI handles its own state
            // No need to pre-fill old form fields
            // TTS credentials now managed server-side via dedicated endpoints

            // Show welcome screen in conversation view
            await showWelcomeScreen();
        }
    } catch (startupError) {
        console.error("DEBUG: Error during startup configuration/session loading. Showing config modal.", startupError);
        // Fallback to showing credentials view
        try {
             // NOTE: Old loadCredentialsAndModels() removed - new config UI handles this
        } catch (prefillError) {
            console.error("DEBUG: Error during fallback pre-fill:", prefillError);
        }
        
        handleViewSwitch('credentials-view');
    }

    // Panel setup will be done when entering conversation pane
    // (panels remain hidden during welcome screen)

    const savedKeyObservationsMode = localStorage.getItem('keyObservationsMode');
    if (['autoplay-off', 'autoplay-on', 'off'].includes(savedKeyObservationsMode)) {
        state.keyObservationsMode = savedKeyObservationsMode;
    }

    UI.updateHintAndIndicatorState();
    UI.updateVoiceModeUI();
    UI.updateKeyObservationsModeUI();
    
    // Update Planner Repository navigation based on profile state
    // Need to wait a bit for configState to be initialized
    setTimeout(() => {
        if (typeof updatePlannerRepositoryNavigation === 'function') {
            updatePlannerRepositoryNavigation();
        }
    }, 500);

    // Save user preferences before page unloads
    window.addEventListener('beforeunload', () => {
        localStorage.setItem('showWelcomeScreenAtStartup', state.showWelcomeScreenAtStartup);
    });
};
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _startup);
else _startup();

// ============================================================================
// PANEL MANAGEMENT
// ============================================================================

/**
 * Update the user TTS settings section based on the current TTS mode.
 * Shows/hides the appropriate message or credential input form.
 */
/**
 * Show or hide voice-related buttons in the chat input area based on TTS state.
 */
window.updateVoiceButtonVisibility = updateVoiceButtonVisibility;
function updateVoiceButtonVisibility(enabled) {
    if (enabled) {
        DOM.voiceInputButton.classList.remove('hidden');
    } else {
        DOM.voiceInputButton.classList.add('hidden');
    }
    // Sync app config state so synthesizeText() in api.js works correctly
    if (state.appConfig) {
        state.appConfig.voice_conversation_enabled = enabled;
    }
    updateKeyObservationsButtonVisibility();
}

/**
 * Show key observations toggle only when TTS is enabled AND the active profile is tool_enabled.
 * Key observations are only produced by tool_enabled (efficiency focused) profiles.
 */
function updateKeyObservationsButtonVisibility() {
    const ttsEnabled = state.appConfig?.voice_conversation_enabled;
    const isToolEnabled = !state.activeGenieProfile && !state.activeRagProfile && !state.activeLlmOnlyProfile;

    if (ttsEnabled && isToolEnabled) {
        DOM.keyObservationsToggleButton.classList.remove('hidden');
    } else {
        DOM.keyObservationsToggleButton.classList.add('hidden');
    }
}

window.updateUserTtsSection = updateUserTtsSection;
function updateUserTtsSection(mode) {
    const badge = document.getElementById('user-tts-mode-badge');
    const disabledMsg = document.getElementById('tts-disabled-message');
    const globalMsg = document.getElementById('tts-global-message');
    const userSection = document.getElementById('tts-user-credentials-section');

    // Hide all sections
    [disabledMsg, globalMsg, userSection].forEach(el => { if (el) el.classList.add('hidden'); });

    if (mode === 'disabled') {
        if (badge) { badge.textContent = 'Disabled'; badge.className = 'ml-2 text-xs px-2 py-0.5 rounded-full bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-400'; }
        if (disabledMsg) disabledMsg.classList.remove('hidden');
    } else if (mode === 'global') {
        if (badge) { badge.textContent = 'Organization'; badge.className = 'ml-2 text-xs px-2 py-0.5 rounded-full bg-green-100 text-green-800 dark:bg-green-900/50 dark:text-green-400'; }
        if (globalMsg) globalMsg.classList.remove('hidden');
    } else if (mode === 'user') {
        if (badge) { badge.textContent = 'User Credentials'; badge.className = 'ml-2 text-xs px-2 py-0.5 rounded-full bg-yellow-100 text-yellow-800 dark:bg-yellow-900/50 dark:text-yellow-400'; }
        if (userSection) userSection.classList.remove('hidden');
        // Load user TTS credential status from server
        _loadUserTtsStatus();
    }
}

/**
 * Load user TTS credential status and set up button handlers.
 */
async function _loadUserTtsStatus() {
    try {
        const token = localStorage.getItem('tda_auth_token');
        if (!token) return;

        const resp = await fetch('/api/user/tts-credentials', {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        if (!resp.ok) return;
        const data = await resp.json();

        const statusEl = document.getElementById('user-tts-status');
        const deleteBtn = document.getElementById('delete-user-tts-btn');

        if (data.has_credentials) {
            if (statusEl) { statusEl.textContent = 'Credentials configured'; statusEl.className = 'text-xs text-green-400'; }
            if (deleteBtn) deleteBtn.classList.remove('hidden');
        } else {
            if (statusEl) { statusEl.textContent = 'No credentials configured'; statusEl.className = 'text-xs text-gray-400'; }
            if (deleteBtn) deleteBtn.classList.add('hidden');
        }

        // Setup button handlers (idempotent - removes old listeners via clone)
        _setupUserTtsButtons();
    } catch (err) {
        console.error('[TTS] Error loading user TTS status:', err);
    }
}

/**
 * Setup event listeners for user TTS credential buttons.
 */
function _setupUserTtsButtons() {
    const token = localStorage.getItem('tda_auth_token');
    const headers = { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' };

    const saveBtn = document.getElementById('save-user-tts-btn');
    const testBtn = document.getElementById('test-user-tts-btn');
    const deleteBtn = document.getElementById('delete-user-tts-btn');
    const statusEl = document.getElementById('user-tts-status');
    const textarea = document.getElementById('tts-credentials-json');

    if (saveBtn) {
        saveBtn.onclick = async () => {
            const creds = textarea?.value?.trim();
            if (!creds) { if (statusEl) { statusEl.textContent = 'Please paste credentials first'; statusEl.className = 'text-xs text-yellow-400'; } return; }
            if (statusEl) { statusEl.textContent = 'Saving...'; statusEl.className = 'text-xs text-blue-400'; }
            try {
                const resp = await fetch('/api/user/tts-credentials', { method: 'POST', headers, body: JSON.stringify({ credentials_json: creds }) });
                const data = await resp.json();
                if (resp.ok) {
                    if (statusEl) { statusEl.textContent = 'Credentials saved'; statusEl.className = 'text-xs text-green-400'; }
                    if (deleteBtn) deleteBtn.classList.remove('hidden');
                    saveBtn.disabled = true;
                    saveBtn.classList.add('opacity-50', 'cursor-not-allowed');
                } else {
                    if (statusEl) { statusEl.textContent = data.error || 'Save failed'; statusEl.className = 'text-xs text-red-400'; }
                }
            } catch (e) { if (statusEl) { statusEl.textContent = 'Error: ' + e.message; statusEl.className = 'text-xs text-red-400'; } }
        };
    }

    // Re-enable save button when credentials are modified
    if (textarea && saveBtn) {
        textarea.addEventListener('input', () => {
            if (saveBtn.disabled) {
                saveBtn.disabled = false;
                saveBtn.classList.remove('opacity-50', 'cursor-not-allowed');
            }
        });
    }

    if (testBtn) {
        testBtn.onclick = async () => {
            const creds = textarea?.value?.trim();
            if (!creds) { if (statusEl) { statusEl.textContent = 'Please paste credentials first'; statusEl.className = 'text-xs text-yellow-400'; } return; }
            if (statusEl) { statusEl.textContent = 'Testing...'; statusEl.className = 'text-xs text-blue-400'; }
            try {
                const resp = await fetch('/api/user/tts-credentials/test', { method: 'POST', headers, body: JSON.stringify({ credentials_json: creds }) });
                const data = await resp.json();
                if (resp.ok) {
                    if (statusEl) { statusEl.textContent = 'Credentials are valid'; statusEl.className = 'text-xs text-green-400'; }
                } else {
                    if (statusEl) { statusEl.textContent = data.error || 'Test failed'; statusEl.className = 'text-xs text-red-400'; }
                }
            } catch (e) { if (statusEl) { statusEl.textContent = 'Error: ' + e.message; statusEl.className = 'text-xs text-red-400'; } }
        };
    }

    if (deleteBtn) {
        deleteBtn.onclick = async () => {
            window.showConfirmation(
                'Delete TTS Credentials',
                '<p>Are you sure you want to <strong>delete</strong> your Text-to-Speech credentials?</p>',
                async () => {
                    try {
                        await fetch('/api/user/tts-credentials', { method: 'DELETE', headers });
                        if (statusEl) { statusEl.textContent = 'Credentials deleted'; statusEl.className = 'text-xs text-gray-400'; }
                        deleteBtn.classList.add('hidden');
                    } catch (e) { console.error('[TTS] Delete error:', e); }
                }
            );
        };
    }
}

/**
 * Initialize panels based on admin window defaults
 * Called when entering conversation pane (not during welcome screen)
 */
function initializePanels() {
    const windowDefaults = state.appConfig?.window_defaults || {};
    
    console.log('[Panel Init] Initializing panels with defaults:', windowDefaults);
    
    // Setup each panel with admin settings
    DOM.toggleHistoryCheckbox.checked = windowDefaults.session_history_default_mode === 'expanded';
    setupPanelToggle(DOM.toggleHistoryButton, DOM.sessionHistoryPanel, DOM.toggleHistoryCheckbox, DOM.historyCollapseIcon, DOM.historyExpandIcon, windowDefaults);

    DOM.toggleHeaderCheckbox.checked = windowDefaults.resources_default_mode === 'expanded';
    setupPanelToggle(DOM.toggleHeaderButton, DOM.toolHeader, DOM.toggleHeaderCheckbox, DOM.headerCollapseIcon, DOM.headerExpandIcon, windowDefaults);

    DOM.toggleStatusCheckbox.checked = windowDefaults.status_default_mode === 'expanded';
    setupPanelToggle(DOM.toggleStatusButton, DOM.statusWindow, DOM.toggleStatusCheckbox, DOM.statusCollapseIcon, DOM.statusExpandIcon, windowDefaults);
    
    console.log('[Panel Init] Panels initialized');
}

// Make initializePanels globally accessible
window.initializePanels = initializePanels;
console.log('[main.js] window.initializePanels is now available:', typeof window.initializePanels);

// ============================================================================
// WELCOME SCREEN MANAGEMENT
// ============================================================================

async function _buildWelcomeConfigHTML(d) {
    // The @-chip is the profile's ADDRESSABLE TAG, never its IFOC class.
    // The `@` sigil means "this is the handle that selects this profile"
    // everywhere else in the platform (conversation header, chat badges, roster,
    // the composer's own @-selector), so spending it on a class label — the
    // "@COORDINATE" this screen used to print — teaches a new user, on the first
    // screen they see, a handle that does not exist and cannot be typed.
    //
    // The class is carried by ONE channel: the tag chip's colour (resolved inside
    // renderProfileTag via profileColors.js). It is deliberately NOT restated as a
    // word — an "Ideate" pill beside a green @IDEAT beside a profile named
    // "Ideate and Explore" said the same thing three times.
    // The local IFOC palette that used to live here (a fifth hand-rolled copy, and
    // the source of the wrong labels) is gone with it.
    //
    // Tags are stored bare, but every tag-keyed reading in profileColors.js
    // strips a leading '@' — do the same so a hand-edited '@FOO' can't render '@@FOO'.
    const tag = d.profileTag ? String(d.profileTag).replace(/^@/, '').trim() : '';
    const esc = (t) => String(t ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    // Reuse the same provider icons already defined in configurationHandler
    let providerIconsMap = {};
    try {
        const { providerIcons } = await import('/static/js/handlers/configurationHandler.js?v=2.214');
        providerIconsMap = providerIcons || {};
    } catch(e) { /* fall through — no icons */ }

    const S = 'display:inline-flex;align-items:center;gap:4px;padding:2px 9px;border-radius:9999px;font-size:11px;white-space:nowrap;';
    const chip  = (t, icon='') => `<span style="${S}border:1px solid var(--border-secondary);background:var(--bg-overlay);color:var(--text-muted);">${icon}${esc(t)}</span>`;
    const code  = (t) => `<code style="padding:2px 7px;border-radius:4px;background:var(--bg-overlay);color:var(--text-muted);font-size:10px;font-family:monospace;white-space:nowrap;border:1px solid var(--border-secondary);">${esc(t)}</code>`;
    const dot   = `<span style="color:var(--border-primary);font-size:13px;line-height:1;">·</span>`;
    const dbSvg = `<svg xmlns="http://www.w3.org/2000/svg" style="width:10px;height:10px;flex-shrink:0;" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4.03 3-9 3S3 13.66 3 12"/><path d="M3 5v14c0 1.66 4.03 3 9 3s9-1.34 9-3V5"/></svg>`;

    // Build provider chip: real icon (scaled to 12px) + colored name
    const providerChip = (name) => {
        const key = (name || '').toLowerCase();
        const info = providerIconsMap[key];
        if (info) {
            // Scale the existing SVG down by replacing class h-4 w-4 with inline size
            const smallIcon = info.icon.replace(/class="h-4 w-4[^"]*"/, 'style="width:12px;height:12px;flex-shrink:0;vertical-align:-2px;"');
            return `<span style="${S}border:1px solid var(--border-secondary);background:var(--bg-overlay);color:${info.color};">${smallIcon}${name}</span>`;
        }
        return chip(name);
    };

    // Strip parentheticals and trim profile name
    const cleanName = d.profileName ? d.profileName.replace(/\s*\(.*?\)\s*/g, '').trim() : null;

    const parts = [];
    // THE canonical chip builder (ui.js) — the same markup, CSS variables and
    // hover colour as the conversation header this screen hands off to, so the
    // chip does not change identity the moment you press Start Reasoning.
    // No tag ⇒ no @-chip at all: an absent handle is stated by its absence,
    // never substituted with something that looks like one.
    if (tag && typeof UI.renderProfileTag === 'function') {
        parts.push(UI.renderProfileTag(esc(tag), d.profileType));
    }
    // The name is dropped only when it restates the tag beside it.
    const _same = (a, b) => !!a && !!b && a.trim().toLowerCase() === b.trim().toLowerCase();
    if (cleanName && !_same(cleanName, tag)) parts.push(chip(cleanName));
    if (d.mcpName) parts.push(chip(d.mcpName, dbSvg));
    if (d.provider || d.model) {
        parts.push(dot);
        if (d.provider) parts.push(providerChip(d.provider));
        if (d.model)    parts.push(code(d.model));
    }

    return `
        <div style="position:relative;display:inline-flex;align-items:center;justify-content:center;border:1px solid var(--border-secondary);border-radius:10px;padding:14px 20px 12px;margin-top:8px;">
            <span style="position:absolute;top:-8px;left:50%;transform:translateX(-50%);padding:0 10px;background:var(--bg-primary);font-size:9px;font-weight:600;letter-spacing:0.1em;text-transform:uppercase;color:var(--text-muted);white-space:nowrap;">Default Agent</span>
            <div style="display:flex;align-items:center;justify-content:center;gap:6px;flex-wrap:wrap;">
                ${parts.join('')}
            </div>
        </div>`;
}

// Generation of the latest welcome render — a background profile test that lands after a
// newer render must not repaint the screen with its (older) answer.
let _welcomeValidationGen = 0;

/**
 * Show the welcome screen for unconfigured applications
 */
async function showWelcomeScreen() {
    const welcomeScreen = document.getElementById('welcome-screen');
    const chatLog = document.getElementById('chat-log');
    const chatFooter = document.getElementById('chat-footer');
    const welcomeBtn = document.getElementById('welcome-configure-btn');
    const welcomeBtnText = document.getElementById('welcome-button-text');
    const welcomeCogwheel = document.getElementById('welcome-cogwheel-icon');
    const welcomeSubtext = document.querySelector('.welcome-subtext');
    const reconfigureLink = document.getElementById('welcome-reconfigure-link');
    const welcomeCheckbox = document.getElementById('welcome-screen-show-at-startup-checkbox');
    
    // Sync checkbox state with user preference
    if (welcomeCheckbox) {
        welcomeCheckbox.checked = state.showWelcomeScreenAtStartup;
    }
    
    if (welcomeScreen && chatLog) {
        welcomeScreen.classList.remove('hidden');
        chatLog.classList.add('hidden');
        // Hide chat input footer when showing welcome screen
        if (chatFooter) {
            chatFooter.classList.add('hidden');
        }
        
        // Hide all panels (History, Live Status, Resource) when welcome screen is shown
        document.querySelectorAll('[data-requires-config="true"]').forEach(panel => {
            panel.style.display = 'none';
        });

        const conversationHeader = document.getElementById('conversation-header');
        if (conversationHeader) conversationHeader.classList.add('hidden');
    }
    
    // Check if user has previously saved configurations
    // For tool_enabled: MCP server + LLM required
    // For llm_only/rag_focused: LLM only required
    let hasSavedConfig = false;
    let configDetails = null; // { profileName, profileType, mcpName, provider, model }

    try {
        const token = localStorage.getItem('tda_auth_token');

        // Get default profile to determine requirements
        const defaultProfile = window.configState?.defaultProfileId
            ? window.configState.profiles?.find(p => p.id === window.configState.defaultProfileId)
            : null;

        const profileType = defaultProfile?.profile_type || 'tool_enabled';
        const activeLLM = configState.getActiveLLMConfiguration();

        if (profileType === 'tool_enabled') {
            // Tool Focused: Requires MCP server + LLM
            const response = await fetch('/api/v1/mcp/servers', {
                headers: { 'Authorization': `Bearer ${token}` }
            });

            if (response.ok) {
                const data = await response.json();
                const hasMCPServer = data && data.servers && data.servers.length > 0;

                if (hasMCPServer && data.active_server_id) {
                    const activeServer = data.servers.find(s => s.id === data.active_server_id);
                    if (activeServer) {
                        const mcpName = activeServer.name || 'Unknown Server';

                        if (activeLLM) {
                            configDetails = { profileName: defaultProfile?.name, profileTag: defaultProfile?.tag, profileType: defaultProfile?.profile_type || null, mcpName, provider: activeLLM.provider || 'Unknown', model: activeLLM.model || '' };
                            hasSavedConfig = true;
                        } else {
                            configDetails = { profileName: defaultProfile?.name, profileTag: defaultProfile?.tag, profileType: defaultProfile?.profile_type || null, mcpName, provider: null, model: null };
                            hasSavedConfig = false;
                        }
                    }
                }
            }
        } else {
            // Ideate / Focus / Coordinate: Only LLM required
            if (activeLLM) {
                configDetails = { profileName: defaultProfile?.name, profileTag: defaultProfile?.tag, profileType: defaultProfile?.profile_type || null, mcpName: null, provider: activeLLM.provider || 'Unknown', model: activeLLM.model || '' };
                hasSavedConfig = true;
            } else {
                configDetails = { profileName: defaultProfile?.name, profileTag: defaultProfile?.tag, profileType: defaultProfile?.profile_type || null, mcpName: null, provider: null, model: null };
                hasSavedConfig = false;
            }
        }
    } catch (error) {
        console.error("Error checking for saved configurations:", error);
    }

    // Check if default profile exists and is valid
    let isDefaultProfileValid = false;
    let defaultProfileFailureDetail = '';
    const validationGen = ++_welcomeValidationGen;
    const defaultId = window.configState?.defaultProfileId;

    // The live profile test makes a real round trip to the LLM provider (~1–2 s on
    // production). An ACTIVATED default profile passed this exact test when it was
    // activated, so its welcome renders at once and the live test confirms it in the
    // background — downgrading the screen to the real failure reason if it no longer
    // passes. A profile that is not activated has no such record: it still waits.
    const runDefaultProfileTest = async () => {
        try {
            console.log('[Welcome Screen] Testing default profile:', defaultId);
            const { testProfile } = await import('./api.js?v=1.2');
            const result = await testProfile(defaultId);

            // Check if all tests passed (accept both 'success' and 'info' as valid)
            // 'info' status is used for optional features (e.g., RAG collections in conversation profiles)
            const failedChecks = Object.entries(result.results || {}).filter(
                ([, r]) => r.status !== 'success' && r.status !== 'info'
            );
            const valid = failedChecks.length === 0;
            console.log('[Welcome Screen] Default profile validation result:', valid);
            if (valid) return { valid, detail: '' };
            console.warn('[Welcome Screen] Default profile validation failed:', result.results);
            // Surface the ACTUAL reason(s) on the welcome screen instead of a generic
            // "check your credentials" — e.g. an unreachable local MLX/Ollama server,
            // a bad API key, or a missing MCP server each show their real message.
            const CHECK_LABELS = {
                llm_connection: 'LLM', mcp_connection: 'MCP server',
                profile_type: 'Profile', knowledge_collections: 'Knowledge',
                rag_collections: 'Collections'
            };
            return {
                valid,
                detail: failedChecks
                    .map(([key, r]) => r.message ? `${CHECK_LABELS[key] || key}: ${r.message}` : (CHECK_LABELS[key] || key))
                    .join(' · '),
            };
        } catch (error) {
            console.error('[Welcome Screen] Error testing default profile:', error);
            return { valid: false, detail: error?.message || '' };
        }
    };

    const renderWelcomeValidity = async () => {
        // Update button text based on whether user has a valid default profile — but never
        // over a press in progress ("Connecting…"); its finally block restores the text.
        if (welcomeBtnText && !(welcomeBtn && welcomeBtn.disabled)) {
            const buttonText = (hasSavedConfig && isDefaultProfileValid) ? 'Start Reasoning' : 'Configure Application';
            welcomeBtnText.textContent = buttonText;
        }

        // Update subtext with styled configuration badges or fallback message
        if (welcomeSubtext) {
            if (hasSavedConfig && isDefaultProfileValid && configDetails) {
                welcomeSubtext.innerHTML = await _buildWelcomeConfigHTML(configDetails);
            } else if (hasSavedConfig && !isDefaultProfileValid) {
                welcomeSubtext.textContent = defaultProfileFailureDetail
                    || "Default agent needs validation. Please check your credentials.";
            } else {
                welcomeSubtext.textContent = "Please activate a Profile";
            }
        }
    };

    const activated = !!defaultId &&
        (window.configState?.activeForConsumptionProfileIds || []).includes(defaultId);
    if (defaultId && activated && hasSavedConfig) {
        isDefaultProfileValid = true;   // provisional: its activation record, confirmed below
        runDefaultProfileTest().then(async ({ valid, detail }) => {
            // A newer welcome render owns the screen — this answer is about an older one.
            if (validationGen !== _welcomeValidationGen || valid) return;
            isDefaultProfileValid = false;
            defaultProfileFailureDetail = detail;
            await renderWelcomeValidity();
        });
    } else if (defaultId) {
        ({ valid: isDefaultProfileValid, detail: defaultProfileFailureDetail } = await runDefaultProfileTest());
    }

    await renderWelcomeValidity();
    
    // Show/hide reconfigure link based on saved config
    if (reconfigureLink) {
        if (hasSavedConfig) {
            reconfigureLink.classList.remove('hidden');
        } else {
            reconfigureLink.classList.add('hidden');
        }
        
        // Wire up the reconfigure link
        if (!reconfigureLink.dataset._wired) {
            reconfigureLink.addEventListener('click', (e) => {
                e.preventDefault();
                handleViewSwitch('credentials-view');
            });
            reconfigureLink.dataset._wired = 'true';
        }
    }
    
    // Wire up the configure button
    if (welcomeBtn && !welcomeBtn.dataset._wired) {
        welcomeBtn.addEventListener('click', async () => {
            if (hasSavedConfig && isDefaultProfileValid) {
                // User has valid default profile - start conversation
                
                // Show spinning cogwheel and update button text
                if (welcomeCogwheel) {
                    welcomeCogwheel.classList.add('animate-spin');
                }
                if (welcomeBtnText) {
                    welcomeBtnText.textContent = 'Connecting...';
                }
                welcomeBtn.disabled = true;
                
                try {
                    // Use centralized initialization - ensures all services are ready
                    const { initializeConversationMode } = await import('./conversationInitializer.js?v=1.190');
                    await initializeConversationMode();
                    
                } catch (error) {
                    console.error('[WelcomeScreen] Error during conversation initialization:', error);
                    // Error notifications handled by initializeConversationMode
                } finally {
                    // Stop spinning and restore button
                    if (welcomeCogwheel) {
                        welcomeCogwheel.classList.remove('animate-spin');
                    }
                    if (welcomeBtnText) {
                        welcomeBtnText.textContent = (hasSavedConfig && isDefaultProfileValid) ? 'Start Reasoning' : 'Configure Application';
                    }
                    welcomeBtn.disabled = false;    
                }
            } else {
                // No valid profile - go to credentials view to configure
                handleViewSwitch('credentials-view');
            }
        });
        welcomeBtn.dataset._wired = 'true';
    }
    
    // After setting up welcome screen, check auth state and override button if needed
    if (window.updateAuthUI) {
        window.updateAuthUI();
    }
    
    // Fetch and display consumption warnings
    await fetchConsumptionWarnings();
}

/**
 * Fetch consumption warnings from the API
 */
async function fetchConsumptionWarnings() {
    try {
        const token = localStorage.getItem('tda_auth_token');
        const userUUID = localStorage.getItem('user_uuid');
        
        if (!token || !userUUID) {
            // Hide warning banner if not authenticated
            document.getElementById('consumption-warning-banner')?.classList.add('hidden');
            return;
        }
        
        const response = await fetch('/api/v1/consumption_warnings', {
            headers: {
                'Authorization': `Bearer ${token}`,
                'X-User-UUID': userUUID
            }
        });
        
        if (!response.ok) {
            // Hide warning banner if error
            document.getElementById('consumption-warning-banner')?.classList.add('hidden');
            return;
        }
        
        const data = await response.json();
        displayConsumptionWarning(data);
    } catch (error) {
        console.error('Error fetching consumption warnings:', error);
        document.getElementById('consumption-warning-banner')?.classList.add('hidden');
    }
}

/**
 * Display consumption warning banner based on usage data
 */
function displayConsumptionWarning(data) {
    const banner = document.getElementById('consumption-warning-banner');
    const warning80 = document.getElementById('consumption-warning-80');
    const warning95 = document.getElementById('consumption-warning-95');
    const warning100 = document.getElementById('consumption-warning-100');
    
    if (!banner || !warning80 || !warning95 || !warning100) return;
    
    // Hide all warnings first
    warning80.classList.add('hidden');
    warning95.classList.add('hidden');
    warning100.classList.add('hidden');
    banner.classList.add('hidden');
    
    // If no warning level, don't show anything
    if (!data.warning_level) return;
    
    // Show appropriate warning based on level
    let activeWarning;
    if (data.warning_level === 'critical' || data.percentage >= 100) {
        activeWarning = warning100;
        document.getElementById('consumption-warning-100-message').textContent = data.message;
        document.getElementById('consumption-percentage-100').textContent = `${Math.round(data.percentage)}%`;
        document.getElementById('consumption-progress-100').style.width = `${Math.min(data.percentage, 100)}%`;
    } else if (data.warning_level === 'urgent' || data.percentage >= 95) {
        activeWarning = warning95;
        document.getElementById('consumption-warning-95-message').textContent = data.message;
        document.getElementById('consumption-percentage-95').textContent = `${Math.round(data.percentage)}%`;
        document.getElementById('consumption-progress-95').style.width = `${Math.min(data.percentage, 100)}%`;
    } else if (data.warning_level === 'warning' || data.percentage >= 80) {
        activeWarning = warning80;
        document.getElementById('consumption-warning-80-message').textContent = data.message;
        document.getElementById('consumption-percentage-80').textContent = `${Math.round(data.percentage)}%`;
        document.getElementById('consumption-progress-80').style.width = `${Math.min(data.percentage, 100)}%`;
    }
    
    // Show the banner and active warning with smooth animation
    if (activeWarning) {
        banner.classList.remove('hidden');
        setTimeout(() => {
            activeWarning.classList.remove('hidden');
        }, 100);
    }
}

/**
 * Populate the disabled capabilities section on the welcome screen
 */
function populateWelcomeDisabledCapabilities() {
    const container = document.getElementById('welcome-disabled-capabilities');
    if (!container) return;

    const disabledTools = [];
    if (state.resourceData.tools) {
        Object.values(state.resourceData.tools).flat().forEach(tool => {
            if (tool.disabled) disabledTools.push(tool.name);
        });
    }

    const disabledPrompts = [];
    if (state.resourceData.prompts) {
        Object.values(state.resourceData.prompts).flat().forEach(prompt => {
            if (prompt.disabled) disabledPrompts.push(prompt.name);
        });
    }

    if (disabledTools.length === 0 && disabledPrompts.length === 0) {
        container.classList.add('hidden');
        return;
    }

    container.classList.remove('hidden');
    
    let html = `
        <div class="pt-6 border-t border-white/10 max-w-3xl mx-auto text-left">
            <h4 class="text-lg font-bold text-yellow-300 mb-2">Reactive Capabilities</h4>
            <p class="text-sm text-gray-400 mb-4">The following capabilities are not actively participating in queries. You can enable them in the Capabilities panel.</p>
            <div class="grid md:grid-cols-2 gap-6">
    `;

    if (disabledTools.length > 0) {
        html += '<div><h5 class="font-semibold text-sm text-white mb-2">Tools</h5><ul class="space-y-1">';
        disabledTools.forEach(name => {
            html += `<li class="text-xs text-gray-300"><code class="text-teradata-orange">${name}</code></li>`;
        });
        html += '</ul></div>';
    }

    if (disabledPrompts.length > 0) {
        html += '<div><h5 class="font-semibold text-sm text-white mb-2">Prompts</h5><ul class="space-y-1">';
        disabledPrompts.forEach(name => {
            html += `<li class="text-xs text-gray-300"><code class="text-teradata-orange">${name}</code></li>`;
        });
        html += '</ul></div>';
    }

    html += '</div></div>';
    container.innerHTML = html;
}

// Make showWelcomeScreen available globally
window.showWelcomeScreen = showWelcomeScreen;

// Make showConfirmation available globally for custom confirmation modals
window.showConfirmation = UI.showConfirmation;