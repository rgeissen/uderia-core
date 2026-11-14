// static/js/handlers/configurationHandler.js
// Manages the new modular configuration system with MCP servers, LLM providers, and localStorage

import { announceProfileConfigChange } from './resourceSync.js?v=1.2';
import { handleViewSwitch, updateGenieMasterBadges, sortSessionsHierarchically } from '../ui.js?v=1.287';
import { handleStartNewSession, handleLoadSession } from './sessionManagement.js?v=3.209';
import { handleLoadResources } from '../eventHandlers.js?v=4.214';
import * as API from '../api.js?v=1.2';
import * as UI from '../ui.js?v=1.287';
import { IFOC_CONFIG } from '../profileColors.js';
import * as DOM from '../domElements.js';
import * as Utils from '../utils.js';
import { state } from '../state.js';
import { safeSetItem, safeGetItem } from '../storageUtils.js';
import { showAppBanner } from '../bannerSystem.js';

/**
 * The pricing check at selection time (OC-F12): the server resolves — or fills — the price of
 * the model a configuration names and returns it as `pricing`. A model it could not price
 * would be billed at the fallback rate, an estimate that reads like a price everywhere it is
 * shown, so the person choosing the model is told, here, while they can still enter one.
 * Pure on its input; exported for the gate.
 */
export function pricingNotice(pricing, label) {
    if (!pricing || typeof pricing !== 'object') return null;
    const who = label ? `"${label}"` : 'This model';
    if (pricing.status === 'unpriced') {
        return { type: 'warning', text: `${who} has no price — its calls would be billed at the fallback rate (an estimate). Add its price under Cost Management.` };
    }
    if (pricing.status === 'filled' && pricing.note) {
        return { type: 'info', text: `${who}: ${pricing.note}` };
    }
    if (pricing.status === 'filled') {
        return { type: 'info', text: `${who}: price added from ${pricing.source}.` };
    }
    return null;
}

function showPricingNotice(pricing, label) {
    const n = pricingNotice(pricing, label);
    if (n) showAppBanner(n.text, n.type, n.type === 'warning' ? 8000 : 4000);
}
import { markSaving } from '../configDirtyState.js?v=1.189';
import { loadAgentPacks } from './agentPackHandler.js?v=1.193';
import { loadExtensions, initializeExtensionHandlers } from './extensionHandler.js?v=1.0';
import { loadSkills, initializeSkillHandlers } from './skillHandler.js?v=1.9';
import { groupByAgentPack, createPackContainerCard, attachPackContainerHandlers } from './agentPackGrouping.js?v=1.2';
import { loadContextWindowTypes, initContextWindowTab, populateContextWindowTypeDropdown, getContextWindowTypes } from './contextWindowHandler.js?v=1.189';
import * as DataSources from './dataSources.js?v=1.1';
import { fetchRoles as fetchPlatformRoles, countsLine as roleCountsLine, reachHTML as roleReachHTML } from './platformRolesView.js?v=1.2';

// ============================================================================
// MEMORY TAB SUB-TABS (Context Window vs Agent Memory)
// ============================================================================
// The Memory config tab has two sub-tabs: Context Window (session/working memory
// budgets) and Agent Memory (informational — durable user facts, no config here).
function initMemorySubtabs() {
    const tab = document.getElementById('context-window-tab');
    if (!tab || tab.dataset.subtabsWired) return;
    tab.dataset.subtabsWired = '1';

    const btns = tab.querySelectorAll('.memory-subtab');
    const panels = {
        'context-window': document.getElementById('memory-subtab-context-window'),
        'agent-memory': document.getElementById('memory-subtab-agent'),
    };
    btns.forEach(b => b.addEventListener('click', () => {
        btns.forEach(x => x.classList.remove('active'));
        b.classList.add('active');
        const k = b.dataset.subtab;
        Object.entries(panels).forEach(([key, el]) => { if (el) el.classList.toggle('hidden', key !== k); });
    }));

    // "Agent Memory component settings" → open the Components view directly on the
    // memory component's detail/settings panel.
    const openBtn = document.getElementById('open-agent-memory-btn');
    if (openBtn) openBtn.addEventListener('click', () => {
        import('./componentHandler.js?v=1.189')
            .then(m => m.openComponentDetail('memory'))
            .catch(() => { document.getElementById('view-switch-components')?.click(); });
    });
}

// ============================================================================
// SESSION PAGINATION STATE
// ============================================================================

const SESSION_PAGE_SIZE = 50;
let sessionPaginationState = {
    loadedOffset: 0,
    totalCount: 0,
    hasMore: false,
    isLoading: false
};

/**
 * Resets the session pagination state (call before initial load)
 */
function resetSessionPagination() {
    sessionPaginationState = {
        loadedOffset: 0,
        totalCount: 0,
        hasMore: false,
        isLoading: false
    };
}

/**
 * Creates or updates the "Load More" button for sessions
 * @param {number} remainingCount - Number of remaining sessions to load
 */
function showLoadMoreSessionsButton(remainingCount) {
    console.log('[Session Pagination] showLoadMoreSessionsButton called with remainingCount:', remainingCount);

    // Remove existing button if any
    hideLoadMoreSessionsButton();

    if (remainingCount <= 0) {
        console.log('[Session Pagination] remainingCount <= 0, not showing button');
        return;
    }

    const btn = document.createElement('button');
    btn.id = 'load-more-sessions-btn';
    btn.className = 'w-full py-2 px-3 mt-2 text-sm text-teradata-orange hover:text-white hover:bg-teradata-orange/20 rounded-lg transition-colors flex items-center justify-center gap-2 border border-dashed border-teradata-orange/40';
    btn.innerHTML = `
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
        </svg>
        Load ${Math.min(remainingCount, SESSION_PAGE_SIZE)} more (${remainingCount} remaining)
    `;
    btn.onclick = handleLoadMoreSessions;

    // Append INSIDE the session list so it scrolls with the sessions
    if (DOM.sessionList) {
        DOM.sessionList.appendChild(btn);
        console.log('[Session Pagination] Button added inside session list');
    } else {
        console.error('[Session Pagination] DOM.sessionList not found!');
    }
}

/**
 * Hides the "Load More" button
 */
function hideLoadMoreSessionsButton() {
    const existingBtn = document.getElementById('load-more-sessions-btn');
    if (existingBtn) {
        existingBtn.remove();
    }
}

/**
 * Handles clicking the "Load More" button to fetch additional sessions
 */
async function handleLoadMoreSessions() {
    if (sessionPaginationState.isLoading) return;

    sessionPaginationState.isLoading = true;
    const btn = document.getElementById('load-more-sessions-btn');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `
            <svg class="w-4 h-4 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            Loading...
        `;
    }

    try {
        const newOffset = sessionPaginationState.loadedOffset + SESSION_PAGE_SIZE;
        const result = await API.loadSessions(SESSION_PAGE_SIZE, newOffset);

        const newSessions = result.sessions || [];
        sessionPaginationState.loadedOffset = newOffset;
        sessionPaginationState.hasMore = result.has_more;
        sessionPaginationState.totalCount = result.total_count;

        // Filter out archived sessions
        const activeSessions = newSessions.filter(s => !s.archived);

        // Sort and append to the session list
        const sortedSessions = sortSessionsHierarchically(activeSessions);

        sortedSessions.forEach((session) => {
            const sessionItem = UI.addSessionToList(session, false);
            DOM.sessionList.appendChild(sessionItem);
        });

        // Update utility sessions filter visibility
        if (window.updateUtilitySessionsFilter) {
            window.updateUtilitySessionsFilter();
        }

        // Update genie master badges
        updateGenieMasterBadges();

        // Update or hide the load more button
        if (result.has_more) {
            const remaining = result.total_count - newOffset - newSessions.length;
            showLoadMoreSessionsButton(remaining);
        } else {
            hideLoadMoreSessionsButton();
        }
    } catch (error) {
        console.error('Failed to load more sessions:', error);
        showNotification('error', 'Failed to load more sessions');
    } finally {
        sessionPaginationState.isLoading = false;
    }
}

// ============================================================================
// UTILITY FUNCTIONS
// ============================================================================

/**
 * Shows a toast notification in the header status area
 * @deprecated Use showAppBanner from bannerSystem.js instead
 * @param {string} type - 'success', 'error', 'warning', 'info'
 * @param {string} message - The message to display
 */
function showNotification(type, message) {
    // Redirect to the centralized banner system
    showAppBanner(message, type);
}

// Make showNotification globally available for backward compatibility
window.showNotification = showNotification;

function showDeleteConfirmation(message, onConfirm) {
    const banner = document.getElementById('delete-confirmation-banner');
    const messageEl = document.getElementById('delete-confirmation-message');
    const cancelBtn = document.getElementById('delete-confirmation-cancel');
    const okBtn = document.getElementById('delete-confirmation-ok');
    
    if (!banner || !messageEl || !cancelBtn || !okBtn) {
        console.error('Delete confirmation banner elements not found');
        return;
    }
    
    // Set the message
    messageEl.textContent = message;
    
    // Show the banner
    banner.classList.remove('hidden');
    
    // Remove any existing event listeners by cloning
    const newCancelBtn = cancelBtn.cloneNode(true);
    const newOkBtn = okBtn.cloneNode(true);
    cancelBtn.parentNode.replaceChild(newCancelBtn, cancelBtn);
    okBtn.parentNode.replaceChild(newOkBtn, okBtn);
    
    // Hide banner function
    const hideBanner = () => {
        banner.classList.add('hidden');
    };
    
    // Cancel button
    newCancelBtn.addEventListener('click', hideBanner);
    
    // OK button
    newOkBtn.addEventListener('click', async () => {
        hideBanner();
        if (onConfirm) {
            await onConfirm();
        }
    });
}

/**
 * Generate a client-side ID for LLM configurations and profiles.
 * NOTE: MCP servers now use server-side UUID generation.
 */
function generateId() {
    return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// loadCredentialsFromLocalStorage removed - credentials now only stored in encrypted database

// ============================================================================
// STORAGE KEYS
// ============================================================================
const STORAGE_KEYS = {
    MCP_SERVERS: 'tda_mcp_servers',
    LLM_PROVIDERS: 'tda_llm_providers',
    ACTIVE_MCP: 'tda_active_mcp',
    ACTIVE_LLM: 'tda_active_llm',
};

// ============================================================================
// LLM PROVIDER TEMPLATES
// ============================================================================
const LLM_PROVIDER_TEMPLATES = {
    Google: {
        name: 'Google',
        fields: [
            { id: 'apiKey', label: 'API Key', type: 'password', placeholder: 'Enter your Google API Key', required: true }
        ]
    },
    Anthropic: {
        name: 'Anthropic',
        fields: [
            { id: 'apiKey', label: 'API Key', type: 'password', placeholder: 'Enter your Anthropic API Key', required: true }
        ]
    },
    OpenAI: {
        name: 'OpenAI',
        fields: [
            { id: 'apiKey', label: 'API Key', type: 'password', placeholder: 'Enter your OpenAI API Key', required: true }
        ]
    },
    Azure: {
        name: 'Microsoft Azure',
        fields: [
            { id: 'azure_api_key', label: 'Azure API Key', type: 'password', placeholder: 'Enter your Azure API Key', required: true },
            { id: 'azure_endpoint', label: 'Azure Endpoint', type: 'text', placeholder: 'e.g., https://your-resource.openai.azure.com/', required: true },
            { id: 'azure_deployment_name', label: 'Deployment Name', type: 'text', placeholder: 'Your model deployment name', required: true },
            { id: 'azure_api_version', label: 'API Version', type: 'text', placeholder: 'e.g., 2024-02-01', required: true }
        ]
    },
    Friendli: {
        name: 'Friendli.ai',
        fields: [
            { id: 'friendli_token', label: 'Personal Access Token', type: 'password', placeholder: 'Enter your Friendli PAT', required: true },
            { id: 'friendli_endpoint_url', label: 'Dedicated Endpoint URL (Optional)', type: 'text', placeholder: 'e.g., https://your-endpoint.friendli.ai', required: false }
        ]
    },
    Amazon: {
        name: 'Amazon',
        fields: [
            { id: 'aws_access_key_id', label: 'AWS Access Key ID', type: 'password', placeholder: 'Enter your AWS Access Key ID', required: true },
            { id: 'aws_secret_access_key', label: 'AWS Secret Access Key', type: 'password', placeholder: 'Enter your AWS Secret Access Key', required: true },
            { id: 'aws_region', label: 'AWS Region', type: 'text', placeholder: 'e.g., us-east-1', required: true }
        ],
        extra: {
            listingMethod: [
                { id: 'foundation_models', label: 'Foundation Models', value: 'foundation_models', default: true },
                { id: 'inference_profiles', label: 'Inference Profiles', value: 'inference_profiles', default: false }
            ]
        }
    },
    Ollama: {
        name: 'Ollama (Local)',
        fields: [
            { id: 'ollama_host', label: 'Ollama Host', type: 'text', placeholder: 'e.g., http://localhost:11434', required: true }
        ]
    },
    OpenRouter: {
        name: 'OpenRouter',
        fields: [
            { id: 'openrouter_api_key', label: 'API Key', type: 'password', placeholder: 'Enter your OpenRouter API Key', required: true }
        ]
    },
    XAI: {
        name: 'xAI (Grok)',
        fields: [
            { id: 'xai_api_key', label: 'API Key', type: 'password', placeholder: 'xai-...', required: true }
        ]
    },
    Celeris: {
        name: 'Celeris',
        fields: [
            { id: 'celeris_api_key', label: 'API Key', type: 'password', placeholder: 'Enter your Celeris API Key', required: true },
            { id: 'celeris_base_url', label: 'Base URL (Optional)', type: 'text', placeholder: 'Default: https://inference.celeris.ai/celeris-1/v1', required: false }
        ]
    },
    MLX: {
        name: 'MLX (Apple Silicon)',
        fields: [
            { id: 'mlx_host', label: 'MLX LM Server URL', type: 'text', placeholder: 'e.g., http://localhost:8080', required: true }
        ]
    }
};

// ============================================================================
// MCP CLASSIFICATION SETTING
// ============================================================================

/**
 * Load the MCP classification setting from the backend
 */
async function loadClassificationSetting() {
    try {
        const response = await fetch('/api/v1/config/classification', {
            method: 'GET',
            headers: { 'Content-Type': 'application/json' }
        });
        
        if (response.ok) {
            const result = await response.json();
            const checkbox = document.getElementById('enable-mcp-classification');
            if (checkbox) {
                checkbox.checked = result.enable_mcp_classification;
            }
        }
    } catch (error) {
        console.error('Failed to load classification setting:', error);
    }
}

/**
 * Save the MCP classification setting to the backend
 */
async function saveClassificationSetting(enabled) {
    try {
        const response = await fetch('/api/v1/config/classification', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enable_mcp_classification: enabled })
        });
        
        if (response.ok) {
            const result = await response.json();
            showNotification('success', result.message);
        } else {
            const error = await response.json();
            showNotification('error', error.message || 'Failed to save classification setting');
        }
    } catch (error) {
        console.error('Failed to save classification setting:', error);
        showNotification('error', 'Failed to save classification setting');
    }
}

// ============================================================================
// STATE MANAGEMENT
// ============================================================================
class ConfigurationState {
    constructor() {
        this.mcpServers = [];
        this.llmConfigurations = [];
        this.activeLLM = null;
        this.profiles = [];
        this.activeProfileId = null;
        // DEPRECATED: masterClassificationProfileId (single master for all servers)
        // Maintained for backwards compatibility until migration to per-server masters is complete.
        // Migration path: Use masterClassificationProfileIds[mcpServerId] instead.
        // Removal target: Q2 2026 (after verifying no external integrations depend on this field)
        this.masterClassificationProfileId = null;

        // NEW: Per-server master classification profiles (preferred approach)
        // Each MCP server can have its own primary classification profile
        this.masterClassificationProfileIds = {}; // {mcpServerId: profileId}
        this.initialized = false;
        this.profileTestStatus = {}; // Track test status: profileId -> { tested: boolean, passed: boolean, timestamp: number }
    }

    async initialize() {
        if (this.initialized) return;
        await this.loadMCPServers();
        await this.loadLLMConfigurations();
        await this.migrateLegacyLLMProviders();
        await this.loadProfiles();
        this.initialized = true;
    }

    /**
     * Migrate legacy localStorage LLM providers to backend configurations
     */
    async migrateLegacyLLMProviders() {
        try {
            // Check if there are any legacy providers in localStorage
            const legacyProvidersJSON = safeGetItem(STORAGE_KEYS.LLM_PROVIDERS);
            if (!legacyProvidersJSON) return; // Nothing to migrate

            const legacyProviders = JSON.parse(legacyProvidersJSON);
            const providerKeys = Object.keys(legacyProviders);
            
            if (providerKeys.length === 0) {
                // Clean up empty legacy data
                localStorage.removeItem(STORAGE_KEYS.LLM_PROVIDERS);
                localStorage.removeItem(STORAGE_KEYS.ACTIVE_LLM);
                return;
            }

            console.log('Migrating legacy LLM providers:', providerKeys);

            // Track migration results
            const migrationResults = [];

            // Migrate each provider to a new configuration
            for (const providerKey of providerKeys) {
                const legacyConfig = legacyProviders[providerKey];
                if (!legacyConfig || !legacyConfig.model) continue; // Skip incomplete configs

                const configData = {
                    name: `${providerKey} (Migrated)`,
                    provider: providerKey,
                    model: legacyConfig.model,
                    credentials: legacyConfig.credentials || {}
                };

                // Add listingMethod for Amazon if it exists
                if (providerKey === 'Amazon' && legacyConfig.listingMethod) {
                    configData.credentials.listingMethod = legacyConfig.listingMethod;
                }

                try {
                    const result = await this.addLLMConfiguration(configData);
                    if (result) {
                        migrationResults.push({ provider: providerKey, success: true, id: result.id });
                        console.log(`Migrated ${providerKey} configuration:`, result);
                    }
                } catch (error) {
                    console.error(`Failed to migrate ${providerKey}:`, error);
                    migrationResults.push({ provider: providerKey, success: false, error: error.message });
                }
            }

            // Note: We don't set an active LLM anymore since active state is now
            // determined by profiles. Users will need to update their profiles to
            // use the migrated configurations.

            // Clean up localStorage after successful migration
            localStorage.removeItem(STORAGE_KEYS.LLM_PROVIDERS);
            localStorage.removeItem(STORAGE_KEYS.ACTIVE_LLM);

            const successCount = migrationResults.filter(r => r.success).length;
            if (successCount > 0) {
                showNotification('success', `Migrated ${successCount} LLM configuration(s) to new system`);
            }
        } catch (error) {
            console.error('Error during LLM provider migration:', error);
            // Don't fail initialization if migration fails
        }
    }

    async loadProfiles() {
        try {
            const { profiles, default_profile_id, active_for_consumption_profile_ids } = await API.getProfiles();
            console.log('[ConfigState] Loaded profiles:', { profiles, default_profile_id, active_for_consumption_profile_ids });
            this.profiles = profiles || [];
            this.defaultProfileId = default_profile_id;
            this.activeForConsumptionProfileIds = active_for_consumption_profile_ids || [];

            // Load master classification profile ID (skip if not authenticated yet)
            const authToken = localStorage.getItem('tda_auth_token');
            if (authToken) {
                try {
                    const response = await API.getMasterClassificationProfile();
                    // NEW: Per-server masters dict
                    this.masterClassificationProfileIds = response.master_classification_profile_ids || {};
                    // DEPRECATED: Legacy single master (for backwards compatibility)
                    this.masterClassificationProfileId = response.master_classification_profile_id;
                    console.log('[ConfigState] Loaded master classification profiles:', this.masterClassificationProfileIds);
                } catch (error) {
                    console.error('Failed to load master classification profile:', error);
                    this.masterClassificationProfileIds = {};
                    this.masterClassificationProfileId = null;
                }
            } else {
                this.masterClassificationProfileIds = {};
                this.masterClassificationProfileId = null;
            }

            // Set active_for_consumption flag on each profile based on the active list
            this.profiles.forEach(profile => {
                profile.active_for_consumption = this.activeForConsumptionProfileIds.includes(profile.id);
            });

            console.log('[ConfigState] State after load:', {
                profileCount: this.profiles.length,
                defaultProfileId: this.defaultProfileId,
                masterClassificationProfileId: this.masterClassificationProfileId,
                activeCount: this.activeForConsumptionProfileIds.length,
                activeIds: this.activeForConsumptionProfileIds
            });
            
            // Initialize session header with default profile
            if (this.defaultProfileId && typeof window.updateSessionHeaderProfile === 'function') {
                const defaultProfile = this.profiles.find(p => p.id === this.defaultProfileId);
                if (defaultProfile) {
                    window.updateSessionHeaderProfile(defaultProfile, null);
                }
            }
            
            // Update Planner Repository navigation state based on profile
            if (typeof window.updatePlannerRepositoryNavigation === 'function') {
                window.updatePlannerRepositoryNavigation();
            }
            
            return this.profiles;
        } catch (error) {
            console.error('Failed to load profiles:', error);
            this.profiles = [];
        }
        return this.profiles;
    }
    
    async addProfile(profile) {
        const newProfile = await API.addProfile(profile);
        this.profiles.push(newProfile.profile);
        return newProfile.profile;
    }

    async updateProfile(profileId, updates) {
        const updatedProfile = await API.updateProfile(profileId, updates);
        const index = this.profiles.findIndex(p => p.id === profileId);
        if (index !== -1) {
            this.profiles[index] = { ...this.profiles[index], ...updates };
        }
        
        // Dispatch event to mark configuration as dirty
        UderiaBus.emit('uderia:profile-modified', { profileId, updates });
        // Which components this agent has changed: every surface listing them (the
        // Workspace, the enablement gate) follows at once — never only after a reload.
        if (updates && Object.prototype.hasOwnProperty.call(updates, 'componentConfig')) {
            UderiaBus.emit('uderia:components-changed', { profileId });
        }
        
        // Reset initialization state when profile is updated
        // This ensures the conversation will re-initialize with new settings
        import('../conversationInitializer.js?v=1.190').then(({ resetInitialization }) => {
            resetInitialization();
            console.log('[ConfigState] Profile updated - conversation will re-initialize on next start');
        });
        
        return updatedProfile;
    }

    async copyProfile(profileId) {
        const profile = this.profiles.find(p => p.id === profileId);
        if (!profile) return null;
        
        // Create a copy with a new ID and modified name
        const newProfile = {
            ...profile,
            id: `profile-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
            name: `${profile.name} (Copy)`,
            tag: '' // Clear tag so user can generate/enter a new one
        };
        
        // Add the copied profile using the proper API function
        try {
            await API.addProfile(newProfile);
            await this.loadProfiles();
            return newProfile;
        } catch (error) {
            console.error('[copyProfile] Error:', error);
            return null;
        }
    }

    async removeProfile(profileId) {
        const result = await API.deleteProfile(profileId);
        this.profiles = this.profiles.filter(p => p.id !== profileId);
        if (this.defaultProfileId === profileId) {
            this.defaultProfileId = null;
        }
        this.activeForConsumptionProfileIds = this.activeForConsumptionProfileIds.filter(id => id !== profileId);
        return result;
    }

    async setDefaultProfile(profileId) {
        await API.setDefaultProfile(profileId);
        this.defaultProfileId = profileId;

        // Dispatch event to mark configuration as dirty
        UderiaBus.emit('uderia:default-profile-changed', { profileId });

        // Update Planner Repository navigation state when default profile changes
        if (typeof window.updatePlannerRepositoryNavigation === 'function') {
            window.updatePlannerRepositoryNavigation();
        }

        // Update resource panel to reflect new default profile's tools/prompts
        // BUT only if there's no active @TAG override (don't disrupt user's current selection)
        if (typeof window.updateResourcePanelForProfile === 'function') {
            const hasActiveOverride = window.activeTagPrefix && window.activeTagPrefix.trim().length > 0;
            if (!hasActiveOverride) {
                window.updateResourcePanelForProfile(profileId);
                console.log('[ConfigState] Resource panel updated to new default profile:', profileId);
            } else {
                console.log('[ConfigState] Skipping resource panel update - user has active @TAG override');
            }
        }

        // Reset initialization state when default profile changes
        import('../conversationInitializer.js?v=1.190').then(({ resetInitialization }) => {
            resetInitialization();
            console.log('[ConfigState] Default profile changed - conversation will re-initialize');
        });
    }

    async setActiveForConsumptionProfiles(profileIds) {
        console.log('[ConfigState] 🔄 setActiveForConsumptionProfiles called with:', profileIds);
        await API.setActiveForConsumptionProfiles(profileIds);
        this.activeForConsumptionProfileIds = profileIds;
        
        // Dispatch event to mark configuration as dirty
        console.log('[ConfigState] 📤 Dispatching profile-modified event');
        UderiaBus.emit('uderia:profile-modified', { source: 'active-for-consumption-changed', profileIds });
        
        // Update knowledge indicator when active profiles change
        if (typeof window.updateKnowledgeIndicatorStatus === 'function') {
            window.updateKnowledgeIndicatorStatus();
        }
    }

    async loadMCPServers() {
        try {
            const headers = {};
            const authToken = localStorage.getItem('tda_auth_token');
            if (authToken) {
                headers['Authorization'] = `Bearer ${authToken}`;
            }
            
            const response = await fetch('/api/v1/mcp/servers', { headers });
            const result = await response.json();
            
            if (result.status === 'success') {
                this.mcpServers = result.servers || [];
                this.activeMCP = result.active_server_id;
                return this.mcpServers;
            }
        } catch (error) {
            console.error('Failed to load MCP servers:', error);
            this.mcpServers = [];
        }
        return this.mcpServers;
    }

    async saveMCPServers() {
        // No-op: servers are saved individually via API
        // Kept for compatibility
    }

    async loadLLMConfigurations() {
        try {
            const headers = {};
            const authToken = localStorage.getItem('tda_auth_token');
            if (authToken) {
                headers['Authorization'] = `Bearer ${authToken}`;
            }
            
            const response = await fetch('/api/v1/llm/configurations', { headers });
            const result = await response.json();
            
            if (result.status === 'success') {
                this.llmConfigurations = result.configurations || [];
                this.activeLLM = result.active_configuration_id;
                return this.llmConfigurations;
            }
        } catch (error) {
            console.error('Failed to load LLM configurations:', error);
            this.llmConfigurations = [];
        }
        return this.llmConfigurations;
    }

    async setActiveMCP(serverId) {
        // Skip activation if serverId is null (for llm_only and rag_focused profiles)
        if (!serverId) {
            this.activeMCP = null;
            return;
        }

        try {
            const headers = {};
            const authToken = localStorage.getItem('tda_auth_token');
            if (authToken) {
                headers['Authorization'] = `Bearer ${authToken}`;
            }

            const response = await fetch(`/api/v1/mcp/servers/${serverId}/activate`, {
                method: 'POST',
                headers: headers
            });

            if (response.ok) {
                this.activeMCP = serverId;
                updateReconnectButton();
            }
        } catch (error) {
            console.error('Failed to set active MCP server:', error);
        }
    }

    async setActiveLLM(configId) {
        try {
            const headers = {};
            const authToken = localStorage.getItem('tda_auth_token');
            if (authToken) {
                headers['Authorization'] = `Bearer ${authToken}`;
            }
            
            const response = await fetch(`/api/v1/llm/configurations/${configId}/activate`, {
                method: 'POST',
                headers: headers
            });
            
            if (response.ok) {
                this.activeLLM = configId;
                updateReconnectButton();
            }
        } catch (error) {
            console.error('Failed to set active LLM configuration:', error);
        }
    }

    async addMCPServer(server) {
        // Server-side UUID generation - remove client-side ID generation
        // Backend will generate ID if not provided

        try {
            const headers = { 'Content-Type': 'application/json' };
            const authToken = localStorage.getItem('tda_auth_token');
            if (authToken) {
                headers['Authorization'] = `Bearer ${authToken}`;
            }

            const response = await fetch('/api/v1/mcp/servers', {
                method: 'POST',
                headers: headers,
                body: JSON.stringify(server)
            });

            if (response.ok) {
                const result = await response.json();
                const serverId = result.server_id; // Backend-generated ID

                // Reload servers from backend to get the updated list
                await this.loadMCPServers();
                // Dispatch event to mark config as dirty
                UderiaBus.emit('uderia:profile-modified', { source: 'mcp-server-add' });
                return serverId;
            } else {
                const errorData = await response.json();
                console.error('Failed to add MCP server:', errorData);
                throw new Error(errorData.message || 'Failed to add MCP server');
            }
        } catch (error) {
            console.error('Failed to add MCP server:', error);
            throw error;
        }
    }

    async removeMCPServer(serverId) {
        try {
            const headers = {};
            const authToken = localStorage.getItem('tda_auth_token');
            if (authToken) {
                headers['Authorization'] = `Bearer ${authToken}`;
            }

            const response = await fetch(`/api/v1/mcp/servers/${serverId}`, {
                method: 'DELETE',
                headers: headers
            });

            if (response.ok) {
                this.mcpServers = this.mcpServers.filter(s => s.id !== serverId);
                if (this.activeMCP === serverId) {
                    this.activeMCP = null;
                }
                // Dispatch event to mark config as dirty
                UderiaBus.emit('uderia:profile-modified', { source: 'mcp-server-remove' });
                return { success: true };
            } else {
                const errorData = await response.json();
                return {
                    success: false,
                    error: errorData.message || 'Failed to remove MCP server'
                };
            }
        } catch (error) {
            console.error('Failed to remove MCP server:', error);
            return {
                success: false,
                error: error.message || 'Failed to remove MCP server'
            };
        }
    }

    async updateMCPServer(serverId, updates) {
        try {
            const headers = { 'Content-Type': 'application/json' };
            const authToken = localStorage.getItem('tda_auth_token');
            if (authToken) {
                headers['Authorization'] = `Bearer ${authToken}`;
            }
            
            const response = await fetch(`/api/v1/mcp/servers/${serverId}`, {
                method: 'PUT',
                headers: headers,
                body: JSON.stringify(updates)
            });
            
            if (response.ok) {
                // Reload servers from backend to get the updated list
                await this.loadMCPServers();
                
                // Clear test status for all profiles using this MCP server
                this.profiles.forEach(profile => {
                    if (profile.mcpServerId === serverId) {
                        delete this.profileTestStatus[profile.id];
                    }
                });
                
                // Dispatch event to mark config as dirty
                UderiaBus.emit('uderia:profile-modified', { source: 'mcp-server-update' });
                return true;
            } else {
                // Extract error message from response
                const errorData = await response.json().catch(() => ({}));
                const errorMessage = errorData.message || `Server returned status ${response.status}`;
                throw new Error(errorMessage);
            }
        } catch (error) {
            console.error('Failed to update MCP server:', error);
            throw error; // Re-throw to be handled by caller
        }
    }

    async addLLMConfiguration(configuration) {
        configuration.id = configuration.id || generateId();
        
        try {
            const headers = { 'Content-Type': 'application/json' };
            const authToken = localStorage.getItem('tda_auth_token');
            if (authToken) {
                headers['Authorization'] = `Bearer ${authToken}`;
            }
            
            const response = await fetch('/api/v1/llm/configurations', {
                method: 'POST',
                headers: headers,
                body: JSON.stringify(configuration)
            });
            
            if (response.ok) {
                const result = await response.json();
                await this.loadLLMConfigurations();
                showPricingNotice(result.pricing, configuration && configuration.name);
                // Dispatch event to mark config as dirty
                UderiaBus.emit('uderia:profile-modified', { source: 'llm-config-add' });
                return result.configuration; // Return the full configuration object with ID
            } else {
                const errorData = await response.json();
                console.error('Failed to add LLM configuration:', errorData);
                throw new Error(errorData.message || 'Failed to add LLM configuration');
            }
        } catch (error) {
            console.error('Failed to add LLM configuration:', error);
            throw error;
        }
    }

    async removeLLMConfiguration(configId) {
        try {
            const response = await fetch(`/api/v1/llm/configurations/${configId}`, {
                method: 'DELETE'
            });
            
            if (response.ok) {
                this.llmConfigurations = this.llmConfigurations.filter(c => c.id !== configId);
                if (this.activeLLM === configId) {
                    this.activeLLM = null;
                }
                // Dispatch event to mark config as dirty
                UderiaBus.emit('uderia:profile-modified', { source: 'llm-config-remove' });
                return { success: true };
            } else {
                const errorData = await response.json();
                return { 
                    success: false, 
                    error: errorData.message || 'Failed to remove LLM configuration' 
                };
            }
        } catch (error) {
            console.error('Failed to remove LLM configuration:', error);
            return { 
                success: false, 
                error: error.message || 'Failed to remove LLM configuration' 
            };
        }
    }

    async updateLLMConfiguration(configId, updates) {
        try {
            const headers = { 'Content-Type': 'application/json' };
            const authToken = localStorage.getItem('tda_auth_token');
            if (authToken) {
                headers['Authorization'] = `Bearer ${authToken}`;
            }
            
            const response = await fetch(`/api/v1/llm/configurations/${configId}`, {
                method: 'PUT',
                headers: headers,
                body: JSON.stringify(updates)
            });
            
            if (response.ok) {
                const _upd = await response.json().catch(() => ({}));
                await this.loadLLMConfigurations();
                showPricingNotice(_upd.pricing, updates && updates.name);
                
                // Clear test status for all profiles using this LLM configuration
                this.profiles.forEach(profile => {
                    if (profile.llmConfigurationId === configId) {
                        delete this.profileTestStatus[profile.id];
                    }
                });
                
                // Dispatch event to mark config as dirty
                UderiaBus.emit('uderia:profile-modified', { source: 'llm-config-update' });
                return true;
            }
        } catch (error) {
            console.error('Failed to update LLM configuration:', error);
        }
        return false;
    }

    getActiveMCPServer() {
        return this.mcpServers.find(s => s.id === this.activeMCP);
    }

    getActiveLLMConfiguration() {
        return this.llmConfigurations.find(c => c.id === this.activeLLM);
    }

    canReconnect() {
        // First check global active MCP/LLM
        let mcpServer = this.getActiveMCPServer();
        let llmConfig = this.getActiveLLMConfiguration();
        let defaultProfile = null;

        // Check if there's a default profile and if it's active
        if (this.defaultProfileId) {
            defaultProfile = this.profiles.find(p => p.id === this.defaultProfileId);

            // CRITICAL: Profile must be activated before allowing connection
            if (!defaultProfile || !defaultProfile.active_for_consumption) {
                return false;
            }
        } else {
            // No default profile set
            return false;
        }

        // If no global active MCP/LLM, check the default profile's configuration
        if ((!mcpServer || !llmConfig) && defaultProfile) {
            if (!mcpServer && defaultProfile.mcpServerId) {
                mcpServer = this.mcpServers.find(s => s.id === defaultProfile.mcpServerId);
            }
            if (!llmConfig && defaultProfile.llmConfigurationId) {
                llmConfig = this.llmConfigurations.find(c => c.id === defaultProfile.llmConfigurationId);
            }
        }

        const profileType = defaultProfile?.profile_type || 'tool_enabled';

        // Conversation, Knowledge, and Genie profiles only need LLM
        // Tool Focused (MCP) profiles need both MCP and LLM
        if (profileType === 'llm_only' || profileType === 'rag_focused' || profileType === 'genie') {
            return !!(llmConfig && llmConfig.model);
        } else {
            return !!(mcpServer && llmConfig && llmConfig.model);
        }
    }
}

// Global state instance

export const configState = new ConfigurationState();

// Also expose to window to avoid circular imports
window.configState = configState;

/**
 * The tools/prompts selection a profile save writes. An EMPTY UI is not an empty choice and
 * not an "all" choice: when the list did not load (still loading, a failed or refused listing,
 * or a server that offered nothing), the stored selection is carried forward unchanged — a new
 * profile gets the default ['*']. Only a loaded, non-empty list whose every entry is checked
 * collapses to ['*']. (Before: `0 selected === 0 available` saved ['*'], silently re-enabling
 * every prompt the agent had switched off whenever the prompt listing failed.)
 */
export function resolveSavedSelection(loadState, selected, all, stored, isEdit) {
    if (loadState !== 'ok' || !Array.isArray(all) || all.length === 0) {
        return isEdit ? stored : ['*'];
    }
    return selected.length === all.length && all.every(n => selected.includes(n)) ? ['*'] : selected;
}

// The resource panel's eye toggle persists the tool/prompt selection on the profile; refresh
// the cached profiles so the profile editor shows the same selection (never a stale copy).
UderiaBus.on('uderia:profile-selection-changed', () => {
    configState.loadProfiles().catch(err => console.warn('Profile refresh after selection change failed:', err));
});

// Deferred: renderProfiles is defined later in this file but referenced via
// window.renderProfiles by modules that cannot import it directly (circular dep).
// The assignment is done after the function definition — search "window.renderProfiles =".

// ============================================================================
// UI RENDERING - MCP SERVERS
// ============================================================================
export function renderMCPServers() {
    const container = document.getElementById('mcp-servers-container');
    if (!container) return;

    // Filter by transport if not "all"
    let servers = configState.mcpServers;
    if (activeMCPTransportFilter !== 'all') {
        servers = servers.filter(server => {
            const transportType = server.transport?.type || 'sse';
            if (activeMCPTransportFilter === 'http') {
                return transportType === 'http' || transportType === 'streamable_http';
            }
            return transportType === activeMCPTransportFilter;
        });
    }

    if (servers.length === 0) {
        const transportName = activeMCPTransportFilter === 'all'
            ? ''
            : ` for ${activeMCPTransportFilter.toUpperCase()}`;
        container.innerHTML = `
            <div class="text-center text-gray-400 py-8">
                <p>No MCP servers found${transportName}. Click "Add Server" to get started.</p>
            </div>
        `;
        return;
    }

    // Determine which servers are used by profiles
    const defaultProfile = configState.profiles.find(p => p.id === configState.defaultProfileId);
    const activeProfiles = configState.profiles.filter(p =>
        configState.activeForConsumptionProfileIds.includes(p.id)
    );

    container.innerHTML = servers.map(server => {
        // Check if this server is used by default profile
        const isDefault = defaultProfile?.mcpServerId === server.id;
        
        // Check if this server is used by any active profile
        const isActive = activeProfiles.some(p => p.mcpServerId === server.id);
        
        // Build status badges
        let statusBadges = '';
        if (isDefault) {
            statusBadges += '<span class="inline-flex items-center px-2.5 py-0.5 text-xs font-medium bg-blue-500 text-white rounded-full">Default</span>';
        }
        if (isActive) {
            statusBadges += '<span class="inline-flex items-center px-2.5 py-0.5 text-xs font-medium bg-green-500 text-white rounded-full ml-2">Active</span>';
        }

        const transportType = server.transport?.type || 'sse';

        return `
            <div class="bg-gradient-to-br from-white/10 to-white/5 border-2 border-white/10 rounded-xl p-4 hover:border-white/20 transition-all duration-200" data-mcp-id="${server.id}">
                <div class="flex items-center justify-between gap-4">
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2 mb-1">
                            <h4 class="text-base font-bold text-white truncate">${escapeHtml(server.name)}</h4>
                            ${statusBadges}
                        </div>
                        <div class="text-sm text-gray-400">
                            <span class="font-medium text-gray-300">Transport:</span> ${getTransportLabel(transportType)}
                            <span class="mx-2">•</span>
                            <span class="font-medium text-gray-300">Host:</span> ${escapeHtml(server.host)}:${escapeHtml(server.port)}
                            <span class="mx-2">•</span>
                            <span class="font-medium text-gray-300">Path:</span> ${escapeHtml(server.path)}
                        </div>
                        ${server.testStatus ? `
                            <div class="mt-2 text-sm ${server.testStatus === 'success' ? 'text-green-400' : 'text-red-400'}">
                                ${escapeHtml(server.testMessage || '')}
                            </div>
                        ` : ''}
                    </div>
                    <div class="flex items-center gap-2 flex-shrink-0">
                        <button type="button" data-action="test-mcp" data-server-id="${server.id}"
                            class="card-btn card-btn--info">
                            Test
                        </button>
                        <button type="button" data-action="edit-mcp" data-server-id="${server.id}"
                            class="card-btn card-btn--neutral">
                            Edit
                        </button>
                        <button type="button" data-action="delete-mcp" data-server-id="${server.id}"
                            class="card-btn card-btn--danger">
                            Delete
                        </button>
                    </div>
                </div>
            </div>
        `;
    }).join('');

    attachMCPEventListeners();
}

function attachMCPEventListeners() {

    // Test MCP button - remove old listeners by cloning
    document.querySelectorAll('[data-action="test-mcp"]').forEach(btn => {
        btn.replaceWith(btn.cloneNode(true));
    });
    document.querySelectorAll('[data-action="test-mcp"]').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const serverId = e.currentTarget.dataset.serverId;
            testMCPConnection(serverId);
        });
    });

    // Edit MCP button - remove old listeners by cloning
    document.querySelectorAll('[data-action="edit-mcp"]').forEach(btn => {
        btn.replaceWith(btn.cloneNode(true));
    });
    document.querySelectorAll('[data-action="edit-mcp"]').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const serverId = e.currentTarget.dataset.serverId;
            showMCPServerModal(serverId);
        });
    });

    // Delete MCP button - remove old listeners by cloning
    document.querySelectorAll('[data-action="delete-mcp"]').forEach(btn => {
        btn.replaceWith(btn.cloneNode(true));
    });
    document.querySelectorAll('[data-action="delete-mcp"]').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const serverId = e.currentTarget.dataset.serverId;
            const server = configState.mcpServers.find(s => s.id === serverId);
            const serverName = server ? server.name : 'this server';

            // Count associated collections
            const { collections: allCollections } = await API.getRagCollections();
            const serverCollections = allCollections.filter(c => c.mcp_server_id === serverId);
            const collectionCount = serverCollections.length;

            // Build confirmation message with collection warning
            let confirmMessage = `Are you sure you want to delete MCP server "${serverName}"?`;
            if (collectionCount > 0) {
                confirmMessage += `\n\nWarning: This will also delete ${collectionCount} associated collection(s):`;
                serverCollections.forEach(c => {
                    confirmMessage += `\n• ${c.name}`;
                });
            }

            showDeleteConfirmation(confirmMessage, async () => {
                try {
                    const token = localStorage.getItem('tda_auth_token');
                    const response = await fetch(`/api/v1/mcp/servers/${serverId}`, {
                        method: 'DELETE',
                        headers: {
                            'Authorization': `Bearer ${token}`
                        }
                    });

                    if (response.ok) {
                        const data = await response.json();
                        const archivedCount = data.archived_sessions || 0;

                        let message = 'MCP server deleted successfully';
                        if (collectionCount > 0) {
                            message += ` (${collectionCount} collection${collectionCount !== 1 ? 's' : ''} removed)`;
                        }
                        if (archivedCount > 0) {
                            message += `\n\n${archivedCount} session(s) using this server have been archived.`;
                            message += `\n\nArchived sessions can be viewed in the Sessions panel by enabling "Show Archived".`;
                        }

                        // Update local state
                        configState.mcpServers = configState.mcpServers.filter(s => s.id !== serverId);
                        if (configState.activeMCP === serverId) {
                            configState.activeMCP = null;
                        }

                        // Dispatch event to mark config as dirty
                        UderiaBus.emit('uderia:profile-modified', { source: 'mcp-server-remove' });

                        renderMCPServers();
                        showNotification('success', message);

                        // Refresh sessions list if sessions were archived
                        if (archivedCount > 0) {
                            try {
                                // Auto-disable toggle (user deleted artifact = cleanup intent)
                                const toggle = document.getElementById('sidebar-show-archived-sessions-toggle');
                                if (toggle && toggle.checked) {
                                    toggle.checked = false;
                                    localStorage.setItem('sidebarShowArchivedSessions', 'false');
                                    console.log('[MCP Delete] Auto-disabled "Show Archived" toggle');
                                }

                                // Full refresh: fetch + re-render + apply filters
                                const { refreshSessionsList } = await import('./configManagement.js?v=1.189');
                                await refreshSessionsList();

                                console.log('[MCP Delete] Session list refreshed after archiving', archivedCount, 'sessions');
                            } catch (error) {
                                console.error('[MCP Delete] Failed to refresh sessions:', error);
                                // Non-fatal: MCP server deleted successfully, just UI refresh failed
                            }
                        }
                    } else {
                        const errorData = await response.json();
                        showNotification('error', errorData.message || 'Failed to delete MCP server');
                    }
                } catch (error) {
                    console.error('Failed to delete MCP server:', error);
                    showNotification('error', error.message || 'Failed to delete MCP server');
                }
            });
        });
    });
}

// ============================================================================
// UI RENDERING - LLM CONFIGURATIONS
// ============================================================================
export function renderLLMProviders() {
    const container = document.getElementById('llm-providers-container');
    if (!container) return;

    // Filter by provider if not "all"
    let configs = configState.llmConfigurations;
    if (activeLLMProviderFilter !== 'all') {
        configs = configs.filter(c =>
            c.provider.toLowerCase() === activeLLMProviderFilter ||
            (activeLLMProviderFilter === 'amazon' && c.provider.toLowerCase() === 'amazon_bedrock')
        );
    }

    if (configs.length === 0) {
        const providerName = activeLLMProviderFilter === 'all'
            ? ''
            : ` for ${activeLLMProviderFilter.charAt(0).toUpperCase() + activeLLMProviderFilter.slice(1)}`;
        container.innerHTML = `
            <div class="col-span-full text-center text-gray-400 py-8">
                <p>No LLM configurations found${providerName}. Click "Add Configuration" to get started.</p>
            </div>
        `;
        return;
    }

    // Determine which configurations are used by profiles
    const defaultProfile = configState.profiles.find(p => p.id === configState.defaultProfileId);
    const activeProfiles = configState.profiles.filter(p =>
        configState.activeForConsumptionProfileIds.includes(p.id)
    );

    container.innerHTML = configs.map(config => {
        // Check if this configuration is used by default profile
        const isDefault = defaultProfile?.llmConfigurationId === config.id;
        
        // Check if this configuration is used by any active profile
        const isActive = activeProfiles.some(p => p.llmConfigurationId === config.id);

        // Build status badges
        let statusBadges = '';
        if (isDefault) {
            statusBadges += '<span class="inline-flex items-center px-2.5 py-0.5 text-xs font-medium bg-blue-500 text-white rounded-full">Default</span>';
        }
        if (isActive) {
            statusBadges += '<span class="inline-flex items-center px-2.5 py-0.5 text-xs font-medium bg-green-500 text-white rounded-full ml-2">Active</span>';
        }
        // Mark the EXCEPTION, never the rule: participation is the default, so only an
        // opted-out config carries a badge. Muted, because this is a scope note about
        // evaluation — not a health state.
        if (config.judge_panel_enabled === false) {
            statusBadges += '<span class="inline-flex items-center px-2.5 py-0.5 text-xs font-medium rounded-full ml-2"'
                + ' style="background-color: var(--hover-bg-strong); color: var(--text-muted); border: 1px solid var(--border-secondary);"'
                + ' title="Excluded from the LLM judge panel. This model still answers normally; it just does not score answer quality.">Not a judge</span>';
        }

        return `
            <div class="bg-gradient-to-br from-white/10 to-white/5 border-2 border-white/10 rounded-xl p-5 hover:border-white/20 transition-all duration-200 h-full" data-llm-config-id="${config.id}">
                <div class="flex flex-col gap-4 h-full">
                    <div class="flex items-start justify-between flex-1">
                        <div class="flex-1 min-w-0">
                            <div class="flex items-center gap-2 mb-3">
                                <h4 class="text-lg font-bold text-white">${escapeHtml(config.name)}</h4>
                                ${statusBadges}
                            </div>
                            <div class="space-y-2">
                                <div class="flex items-center gap-2 text-sm">
                                    <span class="font-semibold text-gray-300">Provider:</span>
                                    ${getProviderLabel(config.provider)}
                                </div>
                                <div class="flex items-center gap-2 text-sm min-w-0">
                                    <span class="font-semibold text-gray-300 shrink-0">Model:</span>
                                    <span class="text-gray-400 truncate" title="${escapeHtml(config.model)}">${escapeHtml(config.model)}</span>
                                </div>
                            </div>
                        </div>
                    </div>
                    <!-- Test results container -->
                    <div id="llm-test-results-${config.id}" class="text-sm min-h-[20px]"></div>
                    
                    <div class="flex items-center gap-2 pt-2 border-t border-white/10">
                        <button type="button" data-action="test-llm" data-config-id="${config.id}"
                            class="card-btn card-btn--info">
                            Test
                        </button>
                        <button type="button" data-action="edit-llm" data-config-id="${config.id}"
                            class="flex-1 card-btn card-btn--neutral">
                            Edit
                        </button>
                        <button type="button" data-action="delete-llm" data-config-id="${config.id}"
                            class="flex-1 card-btn card-btn--danger">
                            Delete
                        </button>
                    </div>
                </div>
            </div>
        `;
    }).join('');

    attachLLMEventListeners();
}

function attachLLMEventListeners() {
    // Test LLM button
    document.querySelectorAll('[data-action="test-llm"]').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const configId = e.target.dataset.configId;
            const resultsContainer = document.getElementById(`llm-test-results-${configId}`);
            const testBtn = e.target;
            
            // Show testing state
            resultsContainer.innerHTML = '<span class="text-yellow-400">Testing credentials...</span>';
            testBtn.disabled = true;
            testBtn.textContent = 'Testing...';
            
            try {
                const result = await API.testLLMConfiguration(configId);
                
                if (result.status === 'success') {
                    resultsContainer.innerHTML = `<span class="text-green-400">✓ Credentials valid</span>`;
                } else {
                    // Extract just the key part of the error message
                    let errorMsg = result.message || 'Test failed';
                    // Remove file paths and stack traces
                    errorMsg = errorMsg.split('(/')[0].split('(File:')[0].trim();
                    // Limit length
                    if (errorMsg.length > 60) {
                        errorMsg = errorMsg.substring(0, 60) + '...';
                    }
                    resultsContainer.innerHTML = `<span class="text-red-400">✗ ${errorMsg}</span>`;
                }
            } catch (error) {
                // Extract just the key part of the error message
                let errorMsg = error.message || 'Test failed';
                errorMsg = errorMsg.split('(/')[0].split('(File:')[0].trim();
                if (errorMsg.length > 60) {
                    errorMsg = errorMsg.substring(0, 60) + '...';
                }
                resultsContainer.innerHTML = `<span class="text-red-400">✗ ${errorMsg}</span>`;
            } finally {
                testBtn.disabled = false;
                testBtn.textContent = 'Test';
            }
        });
    });
    
    // Edit LLM button
    document.querySelectorAll('[data-action="edit-llm"]').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const configId = e.target.dataset.configId;
            await showLLMConfigurationModal(configId);
        });
    });

    // Delete LLM button
    document.querySelectorAll('[data-action="delete-llm"]').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const configId = e.target.dataset.configId;
            const config = configState.llmConfigurations.find(c => c.id === configId);

            if (config) {
                showDeleteConfirmation(`Are you sure you want to delete "${config.name}"?`, async () => {
                    try {
                        const token = localStorage.getItem('tda_auth_token');
                        const response = await fetch(`/api/v1/llm/configurations/${configId}`, {
                            method: 'DELETE',
                            headers: {
                                'Authorization': `Bearer ${token}`
                            }
                        });

                        if (response.ok) {
                            const data = await response.json();
                            const archivedCount = data.archived_sessions || 0;

                            let message = 'LLM configuration deleted successfully';
                            if (archivedCount > 0) {
                                message += `\n\n${archivedCount} session(s) using this configuration have been archived.`;
                                message += `\n\nArchived sessions can be viewed in the Sessions panel by enabling "Show Archived".`;
                            }

                            // Update local state
                            configState.llmConfigurations = configState.llmConfigurations.filter(c => c.id !== configId);
                            if (configState.activeLLM === configId) {
                                configState.activeLLM = null;
                            }

                            // Dispatch event to mark config as dirty
                            UderiaBus.emit('uderia:profile-modified', { source: 'llm-config-remove' });

                            renderLLMProviders();
                            updateReconnectButton();
                            showNotification('success', message);

                            // Refresh sessions list if sessions were archived
                            if (archivedCount > 0) {
                                try {
                                    // Auto-disable toggle (user deleted artifact = cleanup intent)
                                    const toggle = document.getElementById('sidebar-show-archived-sessions-toggle');
                                    if (toggle && toggle.checked) {
                                        toggle.checked = false;
                                        localStorage.setItem('sidebarShowArchivedSessions', 'false');
                                        console.log('[LLM Delete] Auto-disabled "Show Archived" toggle');
                                    }

                                    // Full refresh: fetch + re-render + apply filters
                                    const { refreshSessionsList } = await import('./configManagement.js?v=1.189');
                                    await refreshSessionsList();

                                    console.log('[LLM Delete] Session list refreshed after archiving', archivedCount, 'sessions');
                                } catch (error) {
                                    console.error('[LLM Delete] Failed to refresh sessions:', error);
                                    // Non-fatal: LLM config deleted successfully, just UI refresh failed
                                }
                            }
                        } else {
                            const errorData = await response.json();
                            showNotification('error', errorData.message || 'Failed to delete LLM configuration');
                        }
                    } catch (error) {
                        console.error('Failed to delete LLM configuration:', error);
                        showNotification('error', error.message || 'Failed to delete LLM configuration');
                    }
                });
            }
        });
    });
}

// Placeholder for LLM configuration modal (to be implemented)
export async function showLLMConfigurationModal(configId = null, preselectedProvider = null) {
    let config = configId ? configState.llmConfigurations.find(c => c.id === configId) : null;
    const isEdit = !!config;
    
    // If editing, fetch the full configuration with decrypted credentials
    if (isEdit && configId) {
        try {
            const headers = {};
            const authToken = localStorage.getItem('tda_auth_token');
            if (authToken) {
                headers['Authorization'] = `Bearer ${authToken}`;
            }
            
            const response = await fetch(`/api/v1/llm/configurations/${configId}`, { 
                method: 'GET',
                headers 
            });
            
            if (response.ok) {
                const result = await response.json();
                if (result.status === 'success' && result.configuration) {
                    config = result.configuration;
                }
            }
        } catch (error) {
            console.error('Failed to fetch configuration with credentials:', error);
            // Continue with config from state (without credentials)
        }
    }
    
    const selectedProvider = config?.provider || preselectedProvider || 'Google';

    // Build provider options — alphabetical by provider, the order of the provider tabs above
    // the list (sorted by key: the display name "Microsoft Azure" must still sit under A, as
    // its tab does); the preselection above is explicit, so the order never picks the default.
    const providerOptions = Object.keys(LLM_PROVIDER_TEMPLATES)
        .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }))
        .map(key => `<option value="${key}" ${key === selectedProvider ? 'selected' : ''}>${LLM_PROVIDER_TEMPLATES[key].name}</option>`)
        .join('');

    const modalHTML = `
        <div id="llm-config-modal" class="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50">
            <div class="glass-panel rounded-xl p-6 max-w-md w-full mx-4 max-h-[90vh] overflow-y-auto">
                <h3 class="text-xl font-bold text-white mb-4">${isEdit ? 'Edit' : 'Add'} LLM Configuration</h3>
                <!-- Error banner container -->
                <div id="llm-modal-error" class="hidden mb-4 p-3 bg-red-500/20 border border-red-500 rounded-md">
                    <div class="flex items-start gap-2">
                        <svg class="w-5 h-5 text-red-400 flex-shrink-0 mt-0.5" fill="currentColor" viewBox="0 0 20 20">
                            <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"/>
                        </svg>
                        <span id="llm-modal-error-text" class="text-sm text-red-200 flex-1"></span>
                    </div>
                </div>
                <div class="space-y-4">
                    <div>
                        <label class="block text-sm font-medium text-gray-300 mb-1">Configuration Name</label>
                        <input type="text" id="llm-modal-name" value="${config ? escapeHtml(config.name) : ''}" 
                            placeholder="e.g., Production GPT-4" 
                            class="w-full p-2 bg-gray-700 border border-gray-600 rounded-md focus:ring-2 focus:ring-[#F15F22] focus:border-[#F15F22] outline-none">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-300 mb-1">Provider</label>
                        <select id="llm-modal-provider" class="w-full p-2 bg-gray-700 border border-gray-600 rounded-md focus:ring-2 focus:ring-[#F15F22] focus:border-[#F15F22] outline-none" ${isEdit ? 'disabled' : ''}>
                            ${providerOptions}
                        </select>
                        ${isEdit ? '<p class="text-xs text-gray-400 mt-1">Provider cannot be changed after creation</p>' : ''}
                    </div>
                    <div id="llm-modal-credentials-container">
                        <!-- Credentials fields will be inserted here -->
                    </div>
                    <div id="llm-modal-model-container">
                        <label class="block text-sm font-medium mb-1" style="color: var(--text-secondary);">Model</label>
                        <div class="flex items-center gap-2">
                            <!-- Custom Model Dropdown -->
                            <div id="llm-modal-model-dropdown" class="relative flex-1 min-w-0">
                                <input type="hidden" id="llm-modal-model" value="${config?.model || ''}">
                                <button type="button" id="llm-modal-model-trigger"
                                    class="w-full p-2 rounded-md text-left flex items-center justify-between transition-colors"
                                    style="background-color: var(--input-bg); border: 1px solid var(--border-primary); color: var(--text-primary);">
                                    <span id="llm-modal-model-display" class="truncate">${config?.model ? escapeHtml(config.model) : '-- Select a model --'}</span>
                                    <svg id="llm-modal-model-chevron" class="w-4 h-4 ml-2 flex-shrink-0 transition-transform" style="color: var(--text-muted);" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
                                    </svg>
                                </button>
                                <div id="llm-modal-model-list" class="hidden absolute z-50 w-full bottom-full mb-1 rounded-md shadow-lg max-h-48 overflow-y-auto"
                                    style="background-color: var(--modal-bg); border: 1px solid var(--border-primary); box-shadow: 0 -4px 6px -1px rgba(0,0,0,0.1), 0 -2px 4px -1px rgba(0,0,0,0.06);">
                                    <!-- Model options will be rendered here -->
                                </div>
                            </div>
                            <button type="button" id="llm-modal-refresh-models"
                                class="flex-shrink-0 p-2 rounded-md transition-colors"
                                style="background-color: var(--input-bg); border: 1px solid var(--border-primary);"
                                onmouseover="this.style.backgroundColor='var(--hover-bg)'"
                                onmouseout="this.style.backgroundColor='var(--input-bg)'">
                                <svg class="w-5 h-5" style="color: var(--text-secondary);" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0011.667 0l3.181-3.183m-4.991-2.691L7.985 5.356m0 0v4.992m0 0h4.992m0 0l3.181-3.183a8.25 8.25 0 0111.667 0l3.181 3.183" />
                                </svg>
                            </button>
                        </div>
                    </div>
                    <div id="llm-modal-thinking-container" class="${(config?.provider || '') === 'Google' ? '' : 'hidden'}">
                        <label class="block text-sm font-medium mb-1" style="color: var(--text-secondary);">Thinking Budget</label>
                        <input type="number" id="llm-modal-thinking-budget" value="${config?.thinking_budget ?? ''}" min="-1" placeholder="model default"
                            class="w-full p-2 rounded-md outline-none"
                            style="background-color: var(--input-bg); border: 1px solid var(--border-primary); color: var(--text-primary);">
                        <p class="text-xs mt-1" style="color: var(--text-muted);">Gemini 2.5+ thinking tokens. Leave empty for the model's own default; 0 = disabled, -1 = dynamic/unlimited. Some models refuse a disabled budget and run with thinking enabled instead, so 0 is best-effort.</p>
                    </div>
                    <!-- A ROLE, not a credential: this model's second job. Given its own
                         labelled group so it reads as a distinct decision rather than one
                         more field in the connection form. Summary-first — one state line,
                         with the reason to switch it off revealed only when you do. -->
                    <div id="llm-modal-judge-container" class="pt-2">
                        <div class="text-[11px] font-semibold uppercase tracking-wider mb-2"
                            style="color: var(--text-muted);">Judging role</div>
                        <label id="llm-judge-card" for="llm-modal-judge-panel"
                            class="flex items-center gap-3 p-3 rounded-lg cursor-pointer transition-colors"
                            style="background-color: var(--input-bg); border: 1px solid var(--border-primary);">
                            <span id="llm-judge-icon" class="flex-shrink-0 w-8 h-8 rounded-md flex items-center justify-center transition-colors">
                                <svg class="w-[18px] h-[18px]" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                                    stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                                    <path d="M12 3v18"/><path d="M5 7h14"/>
                                    <path d="M5 7l-2.5 5a3 3 0 0 0 5 0L5 7z"/><path d="M19 7l-2.5 5a3 3 0 0 0 5 0L19 7z"/>
                                </svg>
                            </span>
                            <span class="flex-1 min-w-0">
                                <span class="block text-sm font-medium" style="color: var(--text-primary);">Judge</span>
                                <span id="llm-judge-state" class="block text-xs mt-0.5" style="color: var(--text-muted);"></span>
                            </span>
                            <!-- The shared switch component: state is driven entirely by CSS
                                 sibling selectors off this input, so the control can never
                                 show one thing while the checkbox saves another. -->
                            <input type="checkbox" id="llm-modal-judge-panel" class="sr-only u-switch-input"
                                ${config?.judge_panel_enabled === false ? '' : 'checked'}>
                            <span class="u-switch"><span class="u-switch-thumb"></span></span>
                        </label>
                        <!-- The rationale is only worth reading while you are choosing to
                             opt out, so it appears exactly then instead of taxing every
                             reader of this dialog. -->
                        <p id="llm-judge-hint" class="hidden text-xs mt-2 leading-relaxed"
                            style="color: var(--text-muted);"></p>
                    </div>
                    <div class="flex gap-3 pt-4">
                        <button id="llm-modal-cancel" class="flex-1 card-btn card-btn--neutral">
                            Cancel
                        </button>
                        <button id="llm-modal-save" class="flex-1 card-btn card-btn--primary">
                            ${isEdit ? 'Update' : 'Add'}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `;

    document.body.insertAdjacentHTML('beforeend', modalHTML);

    const modal = document.getElementById('llm-config-modal');

    // ── the judging-role card ───────────────────────────────────────────────
    // ONE renderer for the card's PROSE, so the state line, the icon and the hint
    // always describe the same value. The switch itself is deliberately NOT painted
    // here — it is the shared `.u-switch` component, driven by CSS off the checkbox,
    // so the control cannot show one state while a different one is saved.
    (() => {
        const box = modal.querySelector('#llm-modal-judge-panel');
        if (!box) return;
        const card = modal.querySelector('#llm-judge-card');
        const icon = modal.querySelector('#llm-judge-icon');
        const state = modal.querySelector('#llm-judge-state');
        const hint = modal.querySelector('#llm-judge-hint');
        const ACCENT = 'var(--teradata-orange)';
        const paint = () => {
            const on = box.checked;
            // Scope matters here: one panel serves BOTH judge layers. It scores answers
            // after the turn AND is what a gate escalates to in flight, where a ruling can
            // withhold the answer. Saying only "scores answer quality" would understate
            // what switching this off actually changes.
            state.textContent = on
                ? 'Judges answers in flight and after the turn'
                : 'Answers only — it never judges';
            icon.style.backgroundColor = on ? 'color-mix(in srgb, var(--teradata-orange) 14%, transparent)'
                                            : 'var(--hover-bg-strong)';
            icon.style.color = on ? ACCENT : 'var(--text-muted)';
            card.style.borderColor = on ? 'color-mix(in srgb, var(--teradata-orange) 35%, transparent)'
                                        : 'var(--border-primary)';
            hint.textContent = on ? '' :
                'It will no longer be consulted when a gate decides whether to withhold an '
                + 'answer, nor when answer quality is scored afterwards. Worth doing for a model '
                + 'that often fails to respond: the panel waits for every member on every judged '
                + 'turn, so one silent model sets the pace for all of them. How this model '
                + 'answers is unaffected.';
            hint.classList.toggle('hidden', on);
        };
        box.addEventListener('change', paint);
        paint();
    })();

    const providerSelect = modal.querySelector('#llm-modal-provider');
    const credentialsContainer = modal.querySelector('#llm-modal-credentials-container');
    const refreshBtn = modal.querySelector('#llm-modal-refresh-models');
    const modelInput = modal.querySelector('#llm-modal-model');
    const modelTrigger = modal.querySelector('#llm-modal-model-trigger');
    const modelDisplay = modal.querySelector('#llm-modal-model-display');
    const modelList = modal.querySelector('#llm-modal-model-list');
    const errorBanner = modal.querySelector('#llm-modal-error');
    const errorText = modal.querySelector('#llm-modal-error-text');

    // Store fetched models for re-rendering
    let cachedModels = [];
    let dropdownOpen = false;

    // Toggle dropdown
    const chevron = modal.querySelector('#llm-modal-model-chevron');
    function toggleDropdown(open) {
        dropdownOpen = open !== undefined ? open : !dropdownOpen;
        if (dropdownOpen) {
            modelList.style.display = 'flex';
            modelList.classList.remove('hidden');
            modelTrigger.style.borderColor = 'var(--primary-color, #F15F22)';
            chevron.style.transform = 'rotate(180deg)';
            // Focus and clear search input when opening
            setTimeout(() => {
                const searchInput = modelList.querySelector('#llm-model-search');
                if (searchInput) {
                    searchInput.value = '';
                    searchInput.focus();
                    if (typeof modelList._renderModelItems === 'function') modelList._renderModelItems('');
                }
            }, 30);
        } else {
            modelList.style.display = 'none';
            modelList.classList.add('hidden');
            modelTrigger.style.borderColor = 'var(--border-primary)';
            chevron.style.transform = 'rotate(0deg)';
        }
    }

    // Select a model
    function selectModel(modelName, isRecommended) {
        modelInput.value = modelName;
        modelDisplay.innerHTML = isRecommended
            ? `<span class="inline-flex items-center"><span class="mr-1.5" style="color: var(--primary-color, #F15F22);">★</span>${escapeHtml(modelName)}</span>`
            : escapeHtml(modelName);
        toggleDropdown(false);
    }

    // Click outside to close
    function handleClickOutside(e) {
        if (!modal.querySelector('#llm-modal-model-dropdown').contains(e.target)) {
            toggleDropdown(false);
        }
    }

    modelTrigger.addEventListener('click', () => toggleDropdown());
    document.addEventListener('click', handleClickOutside);

    // Helper functions for error banner
    function showModalError(message) {
        errorText.textContent = message;
        errorBanner.classList.remove('hidden');
    }
    
    function hideModalError() {
        errorBanner.classList.add('hidden');
        errorText.textContent = '';
    }

    // Auto-fill credentials from an existing config for the same provider
    async function autoFillCredentials(provider) {
        const existingConfig = configState.llmConfigurations.find(c => c.provider === provider);
        if (!existingConfig) return;

        try {
            const headers = {};
            const authToken = localStorage.getItem('tda_auth_token');
            if (authToken) {
                headers['Authorization'] = `Bearer ${authToken}`;
            }

            const response = await fetch(`/api/v1/llm/configurations/${existingConfig.id}`, {
                method: 'GET',
                headers
            });

            if (response.ok) {
                const result = await response.json();
                if (result.status === 'success' && result.configuration?.credentials) {
                    const creds = result.configuration.credentials;
                    credentialsContainer.querySelectorAll('[data-credential]').forEach(input => {
                        const fieldId = input.dataset.credential;
                        if (creds[fieldId] !== undefined) {
                            if (input.type === 'radio') {
                                input.checked = (input.value === creds[fieldId]);
                            } else {
                                input.value = creds[fieldId];
                            }
                        }
                    });
                }
            }
        } catch (error) {
            console.error('Failed to auto-fill credentials:', error);
        }
    }

    // Function to render credential fields based on selected provider
    function renderCredentialFields(provider) {
        const template = LLM_PROVIDER_TEMPLATES[provider];
        if (!template) return;

        let html = '';

        // Render regular credential fields
        template.fields.forEach(field => {
            let value = '';
            
            // Get credentials from config (backend database)
            if (config?.credentials) {
                value = config.credentials[field.id] || '';
            }
            
            html += `
                <div>
                    <label class="block text-sm font-medium text-gray-300 mb-1">${escapeHtml(field.label)}</label>
                    <input type="${field.type}" 
                        data-credential="${field.id}" 
                        value="${escapeHtml(value)}" 
                        placeholder="${escapeHtml(field.placeholder)}" 
                        ${field.required ? 'required' : ''}
                        class="w-full p-2 bg-gray-700 border border-gray-600 rounded-md focus:ring-2 focus:ring-[#F15F22] focus:border-[#F15F22] outline-none">
                </div>
            `;
        });

        // Render extra fields (like AWS listing method)
        if (template.extra?.listingMethod) {
            html += '<div><label class="block text-sm font-medium text-gray-300 mb-2">Model Listing Method</label><div class="flex items-center gap-6">';
            template.extra.listingMethod.forEach(option => {
                const checked = config?.listingMethod === option.value || (option.default && !config?.listingMethod);
                html += `
                    <div class="flex items-center">
                        <input id="listing-${option.id}" name="listing_method" type="radio" value="${option.value}" 
                            ${checked ? 'checked' : ''} 
                            data-credential="listing_method"
                            class="h-4 w-4 border-gray-300 text-[#F15F22] focus:ring-[#F15F22]">
                        <label for="listing-${option.id}" class="ml-2 text-sm text-gray-300">${escapeHtml(option.label)}</label>
                    </div>
                `;
            });
            html += '</div></div>';
        }

        credentialsContainer.innerHTML = html;

        // FriendliAI dedicated endpoint: toggle model input mode when URL changes
        if (provider === 'Friendli') {
            const urlInput = credentialsContainer.querySelector('[data-credential="friendli_endpoint_url"]');
            if (urlInput) {
                urlInput.addEventListener('input', () => updateFriendliModelInputMode(urlInput.value));
                // Apply immediately (handles edit mode where value is already populated)
                updateFriendliModelInputMode(urlInput.value);
            }
        }
    }

    // For FriendliAI dedicated endpoints: swap dropdown for a free-text endpoint ID input
    function updateFriendliModelInputMode(endpointUrl) {
        const modelDropdown = document.getElementById('llm-modal-model-dropdown');
        const refreshBtn = document.getElementById('llm-modal-refresh-models');
        const modelInput = document.getElementById('llm-modal-model');
        let manualInput = document.getElementById('llm-modal-model-manual');

        if (endpointUrl && endpointUrl.trim()) {
            // Dedicated endpoint mode — show text input, hide dropdown + refresh
            if (!manualInput) {
                manualInput = document.createElement('input');
                manualInput.type = 'text';
                manualInput.id = 'llm-modal-model-manual';
                manualInput.placeholder = 'Enter endpoint ID (e.g. depps38232ivn8v)';
                manualInput.className = 'flex-1 p-2 rounded-md outline-none';
                manualInput.style.cssText = 'background-color: var(--input-bg); border: 1px solid var(--border-primary); color: var(--text-primary);';
                manualInput.addEventListener('input', () => {
                    if (modelInput) modelInput.value = manualInput.value.trim();
                });
                modelDropdown?.parentElement?.insertBefore(manualInput, modelDropdown);
            }
            // Pre-fill from hidden input if a model is already set
            if (modelInput && modelInput.value && !manualInput.value) {
                manualInput.value = modelInput.value;
            }
            modelDropdown?.classList.add('hidden');
            if (refreshBtn) refreshBtn.classList.add('hidden');
            manualInput.classList.remove('hidden');
        } else {
            // Serverless mode — show dropdown, hide manual input
            if (manualInput) manualInput.classList.add('hidden');
            modelDropdown?.classList.remove('hidden');
            if (refreshBtn) refreshBtn.classList.remove('hidden');
        }
    }

    // Function to refresh models
    async function refreshModels() {
        const provider = providerSelect.value;
        
        if (!provider) {
            showNotification('error', 'Please select a provider first');
            return;
        }
        
        const credentials = {};
        
        // Collect credentials
        credentialsContainer.querySelectorAll('[data-credential]').forEach(input => {
            const field = input.dataset.credential;
            if (input.type === 'radio') {
                if (input.checked) {
                    credentials[field] = input.value;
                }
            } else {
                credentials[field] = input.value;
            }
        });
        
        // Validate required credentials
        const template = LLM_PROVIDER_TEMPLATES[provider];
        const missingFields = template.fields.filter(f => f.required && !credentials[f.id]);
        if (missingFields.length > 0) {
            showNotification('error', `Please enter: ${missingFields.map(f => f.label).join(', ')}`);
            return;
        }

        refreshBtn.disabled = true;
        refreshBtn.innerHTML = '<svg class="w-5 h-5 animate-spin" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>';

        try {
            const response = await fetch('/models', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ provider, ...credentials })
            });

            const data = await response.json();
            
            if (!response.ok) {
                throw new Error(data.message || 'Failed to fetch models');
            }

            if (data.models && data.models.length > 0) {
                // Cache models for re-filtering without re-fetching
                cachedModels = data.models;
                // Render models based on current filter mode
                renderModelOptions();

                const recommendedCount = data.models.filter(m => typeof m === 'object' ? m.recommended : true).length;
                showNotification('success', `Found ${data.models.length} models (${recommendedCount} recommended)`);
            } else {
                cachedModels = [];
                showNotification('warning', 'No models found');
            }
        } catch (error) {
            showNotification('error', `Failed to fetch models: ${error.message}`);
        } finally {
            refreshBtn.disabled = false;
            refreshBtn.innerHTML = '<svg class="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0011.667 0l3.181-3.183m-4.991-2.691L7.985 5.356m0 0v4.992m0 0h4.992m0 0l3.181-3.183a8.25 8.25 0 0111.667 0l3.181 3.183" /></svg>';
        }
    }

    // Function to render model options — sets up the search input + scrollable items container
    function renderModelOptions() {
        const currentValue = modelInput.value;
        modelList.innerHTML = '';

        // Make modelList a flex column so search stays fixed and items scroll
        modelList.classList.remove('max-h-48', 'overflow-y-auto');
        modelList.style.display = 'flex';
        modelList.style.flexDirection = 'column';
        modelList.style.maxHeight = '280px';

        // --- Search input (sticky at top) ---
        const searchWrapper = document.createElement('div');
        searchWrapper.style.cssText = 'flex-shrink: 0; padding: 6px 8px; border-bottom: 1px solid var(--border-primary); background-color: var(--modal-bg);';
        searchWrapper.innerHTML = `
            <div style="position: relative;">
                <svg style="position: absolute; left: 8px; top: 50%; transform: translateY(-50%); width: 13px; height: 13px; color: var(--text-muted);" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0"/>
                </svg>
                <input type="text" id="llm-model-search" placeholder="Search models…" autocomplete="off"
                    style="width: 100%; padding: 5px 8px 5px 26px; font-size: 13px; border-radius: 4px;
                           background-color: var(--input-bg); border: 1px solid var(--border-secondary);
                           color: var(--text-primary); outline: none; box-sizing: border-box;">
            </div>
        `;
        modelList.appendChild(searchWrapper);

        // --- Scrollable items container ---
        const itemsContainer = document.createElement('div');
        itemsContainer.id = 'llm-model-items';
        itemsContainer.style.cssText = 'overflow-y: auto; flex: 1;';
        modelList.appendChild(itemsContainer);

        // Wire up search input
        const searchInput = searchWrapper.querySelector('#llm-model-search');
        searchInput.addEventListener('input', () => renderModelItems(searchInput.value));
        searchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') { toggleDropdown(false); e.stopPropagation(); }
        });
        // Prevent the click-outside handler from closing the dropdown
        searchInput.addEventListener('click', (e) => e.stopPropagation());

        // Helper to create a single model item
        function createModelItem(modelName, isRecommended) {
            const item = document.createElement('div');
            item.className = 'px-3 py-2 cursor-pointer transition-colors';
            item.style.cssText = 'color: var(--text-primary);';
            item.addEventListener('mouseenter', () => { item.style.backgroundColor = 'var(--hover-bg)'; });
            item.addEventListener('mouseleave', () => { item.style.backgroundColor = 'transparent'; });
            if (isRecommended) {
                item.innerHTML = `<span class="inline-flex items-center"><span class="mr-1.5" style="color: var(--primary-color, #F15F22);">★</span>${escapeHtml(modelName)}</span>`;
            } else {
                item.innerHTML = `<span style="color: var(--text-muted);">${escapeHtml(modelName)}</span>`;
            }
            item.addEventListener('click', () => selectModel(modelName, isRecommended));
            return item;
        }

        // Render (filtered) model items into the items container
        function renderModelItems(filterText) {
            itemsContainer.innerHTML = '';
            const searchLower = (filterText || '').toLowerCase().trim();
            // Read live value — NOT the stale closure captured at renderModelOptions() time.
            // The closure value is '' even after auto-select sets modelInput.value, which
            // causes the auto-select branch to fire every time the dropdown re-opens.
            const liveValue = modelInput.value;

            const recommendedModels = [];
            const otherModels = [];
            cachedModels.forEach(model => {
                const modelName = typeof model === 'string' ? model : model.name;
                const recommended = typeof model === 'object' ? model.recommended : true;
                if (recommended) recommendedModels.push({ name: modelName, recommended: true });
                else otherModels.push({ name: modelName, recommended: false });
            });

            const filteredRecommended = searchLower
                ? recommendedModels.filter(m => m.name.toLowerCase().includes(searchLower))
                : recommendedModels;
            const filteredOther = searchLower
                ? otherModels.filter(m => m.name.toLowerCase().includes(searchLower))
                : otherModels;

            const firstRecommendedModel = recommendedModels.length > 0 ? recommendedModels[0].name : null;

            if (filteredRecommended.length > 0) {
                const header = document.createElement('div');
                header.className = 'px-3 py-1.5 text-xs font-semibold uppercase tracking-wider';
                header.style.cssText = 'color: var(--text-muted); background-color: var(--hover-bg); border-bottom: 1px solid var(--border-primary);';
                header.textContent = 'Recommended';
                itemsContainer.appendChild(header);
                filteredRecommended.forEach(m => itemsContainer.appendChild(createModelItem(m.name, true)));
            }

            if (filteredOther.length > 0) {
                const header = document.createElement('div');
                header.className = 'px-3 py-1.5 text-xs font-semibold uppercase tracking-wider';
                header.style.cssText = 'color: var(--text-muted); background-color: var(--hover-bg); border-bottom: 1px solid var(--border-primary);';
                if (filteredRecommended.length > 0) header.style.borderTop = '1px solid var(--border-primary)';
                header.textContent = 'All Models';
                itemsContainer.appendChild(header);
                filteredOther.forEach(m => itemsContainer.appendChild(createModelItem(m.name, false)));
            }

            if (filteredRecommended.length === 0 && filteredOther.length === 0) {
                const emptyState = document.createElement('div');
                emptyState.className = 'px-3 py-4 text-center text-sm';
                emptyState.style.cssText = 'color: var(--text-muted);';
                emptyState.textContent = searchLower ? `No models matching "${filterText}"` : 'No models available. Click refresh to load models.';
                itemsContainer.appendChild(emptyState);
            }

            // Auto-select first recommended on initial render if nothing selected
            if (!filterText) {
                if (!liveValue && firstRecommendedModel) {
                    selectModel(firstRecommendedModel, true);
                } else if (liveValue) {
                    const isCurrentRecommended = recommendedModels.some(m => m.name === liveValue);
                    modelDisplay.innerHTML = isCurrentRecommended
                        ? `<span class="inline-flex items-center"><span class="mr-1.5" style="color: var(--primary-color, #F15F22);">★</span>${escapeHtml(liveValue)}</span>`
                        : escapeHtml(liveValue);
                }
            }
        }

        // Expose renderModelItems so toggleDropdown can call it on open (to reset filter)
        modelList._renderModelItems = renderModelItems;

        // Initial render with no filter
        renderModelItems('');
    }

    // Initial render of credential fields
    renderCredentialFields(selectedProvider);

    // Auto-fill credentials for new configs on initial render
    if (!isEdit) {
        autoFillCredentials(selectedProvider);
    }

    // Provider change handler (only for new configs)
    if (!isEdit) {
        providerSelect.addEventListener('change', async () => {
            const newProvider = providerSelect.value;
            renderCredentialFields(newProvider);
            // Clear model selection and cached models when provider changes
            modelInput.value = '';
            modelDisplay.textContent = '-- Select a model --';
            modelList.innerHTML = '';
            cachedModels = [];

            // Toggle thinking budget visibility (Google only)
            const thinkingContainer = modal.querySelector('#llm-modal-thinking-container');
            if (thinkingContainer) {
                thinkingContainer.classList.toggle('hidden', newProvider !== 'Google');
            }

            // Auto-fill credentials from existing config for same provider
            await autoFillCredentials(newProvider);
        });
    }

    // Refresh models button
    refreshBtn.addEventListener('click', refreshModels);

    // Auto-refresh models when editing (credentials are already set)
    // Skip for dedicated Friendli endpoints — model ID is entered manually
    const isFriendliDedicated = config?.provider === 'Friendli' && config?.credentials?.friendli_endpoint_url;
    if (isEdit && !isFriendliDedicated && config?.credentials && Object.keys(config.credentials).length > 0) {
        // Small delay to ensure modal is visible
        setTimeout(() => refreshModels(), 100);
    }

    // Cleanup function for event listeners
    function cleanupModal() {
        document.removeEventListener('click', handleClickOutside);
        modal.remove();
    }

    // Cancel button
    modal.querySelector('#llm-modal-cancel').addEventListener('click', cleanupModal);

    // Save button
    modal.querySelector('#llm-modal-save').addEventListener('click', async () => {
        const saveBtn = modal.querySelector('#llm-modal-save');
        const name = modal.querySelector('#llm-modal-name').value.trim();
        const provider = providerSelect.value;
        const model = modelInput.value;
        
        // Clear any previous errors
        hideModalError();
        
        if (!name) {
            showModalError('Configuration name is required');
            return;
        }

        if (!model) {
            showModalError('Please select a model');
            return;
        }

        // Collect credentials
        const credentials = {};
        credentialsContainer.querySelectorAll('[data-credential]').forEach(input => {
            const field = input.dataset.credential;
            if (input.type === 'radio') {
                if (input.checked) {
                    credentials[field] = input.value;
                }
            } else {
                credentials[field] = input.value;
            }
        });

        // Validate required fields
        const template = LLM_PROVIDER_TEMPLATES[provider];
        const missingFields = template.fields.filter(f => f.required && !credentials[f.id]);
        if (missingFields.length > 0) {
            showModalError(`Missing required fields: ${missingFields.map(f => f.label).join(', ')}`);
            return;
        }

        const judgeToggle = modal.querySelector('#llm-modal-judge-panel');
        const configData = {
            name,
            provider,
            model,
            credentials,
            // Judge-panel participation. Written on EVERY save (not only when opting out)
            // so an edit can turn it back on: a key omitted on update would leave a stored
            // `false` in place and the checkbox would silently fail to re-enable the model.
            judge_panel_enabled: judgeToggle ? !!judgeToggle.checked : true
        };

        // Include thinking_budget for Google models. An EMPTY field means "not set":
        // the backend then sends no thinking config and the model uses its own
        // default. `parseInt('') || 0` used to stamp 0 on every save, so a field
        // nobody touched silently disabled thinking for every Google model.
        if (provider === 'Google') {
            const thinkingInput = modal.querySelector('#llm-modal-thinking-budget');
            const raw = thinkingInput ? thinkingInput.value.trim() : '';
            const parsed = raw === '' ? null : parseInt(raw, 10);
            // Sent EXPLICITLY as null rather than omitted: `update_llm_configuration`
            // shallow-merges, so an omitted key would leave a previously-stored 0 in
            // place and clearing the field would silently do nothing. Every backend
            // reader tests `is not None`, so a stored null behaves exactly as absent.
            configData.thinking_budget = Number.isFinite(parsed) ? parsed : null;
        }

        try {
            // Disable save button during validation
            saveBtn.disabled = true;
            saveBtn.textContent = 'Testing credentials...';
            
            // Save the configuration first (temporarily)
            let savedConfigId;
            let isNewConfig = !isEdit;
            
            if (isEdit) {
                await configState.updateLLMConfiguration(configId, configData);
                savedConfigId = configId;
            } else {
                const savedConfig = await configState.addLLMConfiguration(configData);
                savedConfigId = savedConfig?.id || configData.id;
                if (!savedConfigId) {
                    throw new Error('Failed to create configuration');
                }
            }
            
            // Test the credentials
            try {
                const testResult = await API.testLLMConfiguration(savedConfigId);
                
                if (testResult.status !== 'success') {
                    // Test failed - remove the config if it was new, or revert if edited
                    if (isNewConfig) {
                        await configState.removeLLMConfiguration(savedConfigId);
                    }
                    showModalError(`Credential validation failed: ${testResult.message}`);
                    return;
                }
                
                // Credentials are now stored in encrypted database only (no localStorage)

                renderLLMProviders();
                showNotification('success', `LLM configuration ${isEdit ? 'updated' : 'added'} successfully with validated credentials`);
                cleanupModal();
                
            } catch (testError) {
                // Test failed with exception - remove the config if it was new
                if (isNewConfig) {
                    await configState.removeLLMConfiguration(savedConfigId);
                }
                showModalError(`Credential validation failed: ${testError.message}`);
            }
        } catch (error) {
            showModalError(`Failed to ${isEdit ? 'update' : 'add'} configuration: ${error.message}`);
        } finally {
            saveBtn.disabled = false;
            saveBtn.textContent = isEdit ? 'Update' : 'Add';
        }
    });
}

// Old LLM provider form functions removed - now using LLM configurations
// renderLLMProviderForm, refreshModels, saveLLMProvider are deprecated

// ============================================================================
// ACTION HANDLERS - MCP SERVERS
// ============================================================================
export function showMCPServerModal(serverId = null) {
    const server = serverId ? configState.mcpServers.find(s => s.id === serverId) : null;
    const isEdit = !!server;

    // Resolve existing transport type; legacy servers without transport field default to 'sse'
    const existingTransport = server?.transport?.type || 'sse';
    const initialTransport = (existingTransport === 'http' || existingTransport === 'streamable_http') ? 'http' : 'sse';

    const pathPlaceholders = { sse: '/sse', http: '/mcp' };

    const modalHTML = `
        <div id="mcp-server-modal" class="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50">
            <div class="glass-panel rounded-xl p-6 max-w-md w-full mx-4">
                <h3 class="text-xl font-bold text-white mb-4">${isEdit ? 'Edit' : 'Add'} MCP Server</h3>
                <div class="space-y-4">
                    <div>
                        <label class="block text-sm font-medium mb-2" style="color: var(--text-muted);">Transport Type</label>
                        <div id="mcp-modal-transport-tabs" class="flex rounded-lg overflow-hidden border" style="border-color: var(--border-primary);">
                            <button data-transport="sse"
                                class="mcp-transport-tab flex-1 py-2 text-sm font-medium transition-colors"
                                style="${initialTransport === 'sse'
                                    ? 'background: rgba(74,222,128,0.15); color: #4ade80; border-right: 1px solid var(--border-primary);'
                                    : 'background: transparent; color: var(--text-muted); border-right: 1px solid var(--border-primary);'}">
                                SSE
                            </button>
                            <button data-transport="http"
                                class="mcp-transport-tab flex-1 py-2 text-sm font-medium transition-colors"
                                style="${initialTransport === 'http'
                                    ? 'background: rgba(59,130,246,0.15); color: #3b82f6;'
                                    : 'background: transparent; color: var(--text-muted);'}">
                                HTTP
                            </button>
                        </div>
                        <p id="mcp-modal-transport-hint" class="text-xs mt-1" style="color: var(--text-muted);">
                            ${initialTransport === 'sse'
                                ? 'Legacy SSE streaming transport (e.g. path <code>/sse</code>)'
                                : 'Streamable HTTP transport — MCP spec 2025-03-26 (e.g. path <code>/mcp</code>)'}
                        </p>
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-300 mb-1">Server Name</label>
                        <input type="text" id="mcp-modal-name" value="${server ? escapeHtml(server.name) : ''}"
                            placeholder="e.g., Production DB Server"
                            class="w-full p-2 bg-gray-700 border border-gray-600 rounded-md focus:ring-2 focus:ring-[#F15F22] focus:border-[#F15F22] outline-none">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-300 mb-1">Host</label>
                        <input type="text" id="mcp-modal-host" value="${server ? escapeHtml(server.host) : ''}"
                            placeholder="e.g., localhost"
                            class="w-full p-2 bg-gray-700 border border-gray-600 rounded-md focus:ring-2 focus:ring-[#F15F22] focus:border-[#F15F22] outline-none">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-300 mb-1">Port</label>
                        <input type="text" id="mcp-modal-port" value="${server ? escapeHtml(server.port) : ''}"
                            placeholder="e.g., 8000"
                            class="w-full p-2 bg-gray-700 border border-gray-600 rounded-md focus:ring-2 focus:ring-[#F15F22] focus:border-[#F15F22] outline-none">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-300 mb-1">Path</label>
                        <input type="text" id="mcp-modal-path" value="${server ? escapeHtml(server.path) : ''}"
                            placeholder="${pathPlaceholders[initialTransport]}"
                            class="w-full p-2 bg-gray-700 border border-gray-600 rounded-md focus:ring-2 focus:ring-[#F15F22] focus:border-[#F15F22] outline-none">
                    </div>
                    <div class="flex gap-3 pt-4">
                        <button id="mcp-modal-cancel" class="flex-1 card-btn card-btn--neutral">
                            Cancel
                        </button>
                        <button id="mcp-modal-save" class="flex-1 card-btn card-btn--primary">
                            ${isEdit ? 'Update' : 'Add'}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `;

    document.body.insertAdjacentHTML('beforeend', modalHTML);

    const modal = document.getElementById('mcp-server-modal');
    const pathInput = modal.querySelector('#mcp-modal-path');
    const hint = modal.querySelector('#mcp-modal-transport-hint');

    // Track selected transport type
    let selectedTransport = initialTransport;

    const hints = {
        sse: 'Legacy SSE streaming transport (e.g. path <code>/sse</code>)',
        http: 'Streamable HTTP transport — MCP spec 2025-03-26 (e.g. path <code>/mcp</code>)'
    };
    const tabActiveStyles = {
        sse: 'background: rgba(74,222,128,0.15); color: #4ade80;',
        http: 'background: rgba(59,130,246,0.15); color: #3b82f6;'
    };

    modal.querySelectorAll('.mcp-transport-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            selectedTransport = btn.dataset.transport;

            modal.querySelectorAll('.mcp-transport-tab').forEach(b => {
                const isActive = b.dataset.transport === selectedTransport;
                const baseStyle = isActive ? tabActiveStyles[selectedTransport] : 'background: transparent; color: var(--text-muted);';
                // Preserve border-right on the SSE tab
                b.style.cssText = baseStyle + (b.dataset.transport === 'sse' ? ' border-right: 1px solid var(--border-primary);' : '');
            });

            hint.innerHTML = hints[selectedTransport];
            pathInput.placeholder = pathPlaceholders[selectedTransport];
        });
    });

    modal.querySelector('#mcp-modal-cancel').addEventListener('click', () => modal.remove());
    modal.querySelector('#mcp-modal-save').addEventListener('click', async () => {
        const data = {
            name: modal.querySelector('#mcp-modal-name').value.trim(),
            host: modal.querySelector('#mcp-modal-host').value.trim(),
            port: modal.querySelector('#mcp-modal-port').value.trim(),
            path: pathInput.value.trim(),
            transport: { type: selectedTransport },
        };

        if (!data.name || !data.host || !data.port || !data.path) {
            showNotification('error', 'All fields are required');
            return;
        }

        try {
            let success;
            if (isEdit) {
                success = await configState.updateMCPServer(serverId, data);
            } else {
                const result = await configState.addMCPServer(data);
                success = result !== null && result !== undefined;
            }

            if (success) {
                renderMCPServers();
                modal.remove();
                showNotification('success', `MCP server ${isEdit ? 'updated' : 'added'} successfully`);
            } else {
                showNotification('error', `Failed to ${isEdit ? 'update' : 'add'} MCP server`);
            }
        } catch (error) {
            showNotification('error', `Failed to ${isEdit ? 'update' : 'add'} MCP server: ${error.message}`);
        }
    });
}

export function showImportMCPServerModal() {
    const modalHTML = `
        <div id="import-mcp-server-modal" class="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50">
            <div class="glass-panel rounded-xl p-6 max-w-2xl w-full mx-4">
                <h3 class="text-xl font-bold text-white mb-4">Import MCP Server Configuration</h3>
                <div class="space-y-4">
                    <div>
                        <label class="block text-sm font-medium text-gray-300 mb-2">
                            Paste configuration or upload file
                        </label>
                        <div class="text-xs text-gray-400 mb-2">
                            Supports: <span class="font-semibold text-blue-400">MCP Registry format</span> or <span class="font-semibold text-green-400">Claude Desktop format</span>
                        </div>
                        <textarea id="import-mcp-json-input"
                            placeholder='MCP Registry format:
{
  "name": "io.example/server",
  "version": "1.0.0",
  "packages": [{"transport": {"type": "sse", "url": "http://localhost:8000/sse"}}]
}

Claude Desktop format:
{
  "mcpServers": {
    "server-name": {
      "command": "npx",
      "args": ["-y", "@package/name"],
      "env": {"API_KEY": "your-key"}
    }
  }
}'
                            rows="14"
                            class="w-full p-3 bg-gray-700 border border-gray-600 rounded-md focus:ring-2 focus:ring-[#F15F22] focus:border-[#F15F22] outline-none font-mono text-sm text-gray-200"></textarea>
                    </div>
                    <div class="flex items-center gap-3">
                        <label class="flex-1 cursor-pointer">
                            <input type="file" id="import-mcp-file-input" accept=".json,application/json" class="hidden">
                            <span class="flex items-center justify-center gap-2 px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-md transition-colors text-white text-sm">
                                <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                                    <path stroke-linecap="round" stroke-linejoin="round" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
                                </svg>
                                Or Upload server.json File
                            </span>
                        </label>
                        <span id="import-mcp-filename" class="text-sm text-gray-400 flex-1"></span>
                    </div>
                    <div id="import-mcp-preview" class="hidden">
                        <h4 class="text-sm font-semibold text-white mb-2">Server Preview:</h4>
                        <div id="import-mcp-preview-content" class="bg-gray-700/50 rounded-md p-3 text-sm text-gray-300"></div>
                    </div>
                    <div id="import-mcp-error" class="hidden">
                        <div class="bg-red-500/20 border border-red-500/50 rounded-md p-3 text-sm text-red-400"></div>
                    </div>
                    <div class="flex gap-3 pt-4">
                        <button id="import-mcp-modal-cancel" class="flex-1 card-btn card-btn--neutral">
                            Cancel
                        </button>
                        <button id="import-mcp-modal-import" class="flex-1 card-btn card-btn--primary">
                            Import Server
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `;

    document.body.insertAdjacentHTML('beforeend', modalHTML);

    const modal = document.getElementById('import-mcp-server-modal');
    const jsonInput = modal.querySelector('#import-mcp-json-input');
    const fileInput = modal.querySelector('#import-mcp-file-input');
    const filenameDisplay = modal.querySelector('#import-mcp-filename');
    const previewDiv = modal.querySelector('#import-mcp-preview');
    const previewContent = modal.querySelector('#import-mcp-preview-content');
    const errorDiv = modal.querySelector('#import-mcp-error');

    // File upload handler
    fileInput.addEventListener('change', async (e) => {
        const file = e.target.files[0];
        if (file) {
            filenameDisplay.textContent = file.name;
            try {
                const text = await file.text();
                jsonInput.value = text;
                validateAndPreviewServer(text);
            } catch (error) {
                showError(`Failed to read file: ${error.message}`);
            }
        }
    });

    // JSON input validation
    jsonInput.addEventListener('input', () => {
        const value = jsonInput.value.trim();
        if (value) {
            validateAndPreviewServer(value);
        } else {
            previewDiv.classList.add('hidden');
            errorDiv.classList.add('hidden');
        }
    });

    function validateAndPreviewServer(jsonText) {
        try {
            const serverData = JSON.parse(jsonText);

            // Detect format
            let formatName = '';
            let previewHTML = '';

            if (serverData.mcpServers) {
                // Claude Desktop format
                formatName = 'Claude Desktop';
                const servers = serverData.mcpServers;
                const serverCount = Object.keys(servers).length;

                if (serverCount === 0) {
                    showError("mcpServers object is empty");
                    return;
                }

                previewHTML = `
                    <div class="space-y-2">
                        <div><span class="font-semibold text-green-400">Format:</span> Claude Desktop</div>
                        <div><span class="font-semibold">Servers:</span> ${serverCount}</div>
                        <div class="mt-2 text-xs">
                            ${Object.keys(servers).map(name => `<div class="py-1">• ${escapeHtml(name)}</div>`).join('')}
                        </div>
                    </div>
                `;

            } else if (serverData.name || serverData.$schema) {
                // MCP Registry format
                formatName = 'MCP Registry';

                if (!serverData.name) {
                    showError("Missing required field: 'name'");
                    return;
                }
                if (!serverData.version) {
                    showError("Missing required field: 'version'");
                    return;
                }

                const transportInfo = extractTransportInfo(serverData);
                previewHTML = `
                    <div class="space-y-1">
                        <div><span class="font-semibold text-blue-400">Format:</span> MCP Registry</div>
                        <div><span class="font-semibold">Name:</span> ${escapeHtml(serverData.title || serverData.name)}</div>
                        <div><span class="font-semibold">Version:</span> ${escapeHtml(serverData.version)}</div>
                        ${serverData.description ? `<div><span class="font-semibold">Description:</span> ${escapeHtml(serverData.description)}</div>` : ''}
                        <div><span class="font-semibold">Transport:</span> ${escapeHtml(transportInfo)}</div>
                    </div>
                `;

            } else {
                showError("Unknown format. Expected MCP Registry or Claude Desktop format.");
                return;
            }

            // Show preview
            errorDiv.classList.add('hidden');
            previewDiv.classList.remove('hidden');
            previewContent.innerHTML = previewHTML;

        } catch (error) {
            showError(`Invalid JSON: ${error.message}`);
        }
    }

    function extractTransportInfo(serverData) {
        const packages = serverData.packages || [];
        const remotes = serverData.remotes || [];

        for (const pkg of packages) {
            const transport = pkg.transport;
            if (transport) {
                return `${transport.type} (${transport.url || 'local command'})`;
            }
        }

        for (const remote of remotes) {
            const transport = remote.transport;
            if (transport) {
                return `${transport.type} (${transport.url || 'remote'})`;
            }
        }

        return 'Unknown';
    }

    function showError(message) {
        errorDiv.classList.remove('hidden');
        errorDiv.querySelector('div').textContent = message;
        previewDiv.classList.add('hidden');
    }

    // Cancel button
    modal.querySelector('#import-mcp-modal-cancel').addEventListener('click', () => modal.remove());

    // Import button
    modal.querySelector('#import-mcp-modal-import').addEventListener('click', async () => {
        const jsonText = jsonInput.value.trim();

        if (!jsonText) {
            showNotification('error', 'Please paste or upload a server.json file');
            return;
        }

        try {
            const serverData = JSON.parse(jsonText);

            // Call import API
            const authToken = localStorage.getItem('tda_auth_token');
            const headers = {
                'Content-Type': 'application/json'
            };
            if (authToken) {
                headers['Authorization'] = `Bearer ${authToken}`;
            }

            const response = await fetch('/api/v1/mcp/servers/import', {
                method: 'POST',
                headers: headers,
                body: JSON.stringify(serverData)
            });

            const result = await response.json();
            console.log('[Import] Backend response:', result);

            if (result.status === 'success') {
                const serverCount = result.servers?.length || 1;
                showNotification('success', `Successfully imported ${serverCount} server(s)`);
                await configState.loadMCPServers(); // Reload MCP servers list
                renderMCPServers();
                modal.remove();
            } else {
                // Show detailed error with skipped servers info
                let errorMsg = result.message || 'Failed to import server';
                if (result.skipped && result.skipped.length > 0) {
                    errorMsg += '\n\nSkipped servers:';
                    result.skipped.forEach(skip => {
                        errorMsg += `\n• ${skip.name}: ${skip.reason}`;
                    });
                }
                showError(errorMsg);
            }
        } catch (error) {
            showError(`Import failed: ${error.message}`);
        }
    });
}

async function testMCPConnection(serverId) {
    const server = configState.mcpServers.find(s => s.id === serverId);
    if (!server) return;

    const btn = document.querySelector(`[data-action="test-mcp"][data-server-id="${serverId}"]`);
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Testing...';
    }

    try {
        // Build test payload - include transport info for stdio servers
        const testPayload = {
            id: server.id,
            name: server.name,
            host: server.host,
            port: server.port,
            path: server.path
        };

        // Include transport configuration if available (for stdio servers)
        if (server.transport) {
            testPayload.transport = server.transport;
        }

        const response = await fetch('/test-mcp-connection', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(testPayload)
        });

        const result = await response.json();

        server.testStatus = result.status;
        server.testMessage = result.message;
        configState.saveMCPServers();
        renderMCPServers();

        showNotification(result.status, result.message);
    } catch (error) {
        showNotification('error', `Test failed: ${error.message}`);
        server.testStatus = 'error';
        server.testMessage = error.message;
        configState.saveMCPServers();
        renderMCPServers();
    }
}

// ============================================================================
// UI UPDATE HELPERS
// ============================================================================
function updateProfilesTab() {
    const profilesTab = document.querySelector('.config-tab[data-tab="profiles-tab"]');
    if (!profilesTab) return;

    const mcpConfigured = configState.mcpServers.length > 0;
    const llmConfigured = configState.llmConfigurations.length > 0;

    if (mcpConfigured && llmConfigured) {
        profilesTab.disabled = false;
        profilesTab.classList.remove('opacity-50', 'cursor-not-allowed');
    } else {
        profilesTab.disabled = true;
        profilesTab.classList.add('opacity-50', 'cursor-not-allowed');
    }
}

export function updateReconnectButton() {
    const btn = document.getElementById('reconnect-and-load-btn');
    if (!btn) return;

    const canReconnect = configState.canReconnect();
    btn.disabled = !canReconnect;
    btn.classList.toggle('opacity-50', !canReconnect);
    btn.classList.toggle('cursor-not-allowed', !canReconnect);
}

export async function reconnectAndLoad(silent = false) {
    const defaultProfile = configState.profiles.find(p => p.id === configState.defaultProfileId);

    if (!defaultProfile) {
        showNotification('error', 'Please set a default profile before connecting.');
        return;
    }

    // CRITICAL: Check if profile is activated
    if (!defaultProfile.active_for_consumption) {
        showNotification('error', 'Please activate the default profile before connecting. Run a test to validate and activate it.');
        return;
    }

    // Set the active MCP and LLM based on the default profile
    await configState.setActiveMCP(defaultProfile.mcpServerId);
    await configState.setActiveLLM(defaultProfile.llmConfigurationId);

    const mcpServer = configState.getActiveMCPServer();
    const llmConfig = configState.getActiveLLMConfiguration();
    const profileType = defaultProfile.profile_type || 'tool_enabled';

    // Validate LLM configuration (required for all profile types)
    if (!llmConfig) {
        showNotification('error', 'Please configure and select an LLM Configuration first (go to LLM Providers tab)');
        return;
    }

    // Validate MCP server only for Tool Focused profiles
    if (profileType === 'tool_enabled') {
        if (!mcpServer) {
            showNotification('error', 'Please configure and select an MCP Server first (go to MCP Servers tab)');
            return;
        }

        // Additional validation for required fields
        if (!mcpServer.host || !mcpServer.port) {
            showNotification('error', 'MCP Server configuration is incomplete (missing host or port)');
            return;
        }
    }

    // Credentials are now fetched from backend database during connection
    // No need to load from localStorage anymore

    const btn = silent ? null : document.getElementById('reconnect-and-load-btn');
    const btnText = silent ? null : document.getElementById('reconnect-button-text');
    const spinner = silent ? null : document.getElementById('reconnect-loading-spinner');
    const statusDiv = silent ? null : document.getElementById('reconnect-status');

    if (!silent) {
        btn.disabled = true;
        btnText.textContent = 'Connecting...';
        spinner.classList.remove('hidden');
        spinner.classList.add('animate-spin');
        statusDiv.innerHTML = '<span class="text-gray-400">Initializing connection...</span>';
    }

    try {
        // Build base config data
        const configData = {
            provider: llmConfig.provider,
            model: llmConfig.model,
            credentials: llmConfig.credentials,
            listing_method: llmConfig.listingMethod || 'foundation_models'
        };

        // Add MCP server data only for Tool Focused profiles
        if (profileType === 'tool_enabled' && mcpServer) {
            configData.server_name = mcpServer.name;
            configData.server_id = mcpServer.id;
            configData.mcp_server = {
                id: mcpServer.id,
                name: mcpServer.name,
                host: mcpServer.host,
                port: mcpServer.port,
                path: mcpServer.path
            };
        }


        const headers = { 'Content-Type': 'application/json' };
        
        // Add authentication token if available
        const authToken = localStorage.getItem('tda_auth_token');
        if (authToken) {
            headers['Authorization'] = `Bearer ${authToken}`;
        }
        
        // Authentication is handled via JWT tokens only
        
        const response = await fetch('/configure', {
            method: 'POST',
            headers: headers,
            body: JSON.stringify(configData)
        });

        const result = await response.json();

        if (result.status === 'success') {
            if (!silent) {
                statusDiv.innerHTML = '<span class="text-green-400">✓ ' + escapeHtml(result.message) + '</span>';
                showNotification('success', result.message);
            }
            
            // Don't override active profiles - they are already loaded from backend configuration
            // The active_for_consumption_profile_ids should persist from the saved config
            
            // Update status indicators
            DOM.mcpStatusDot.classList.remove('disconnected');
            DOM.mcpStatusDot.classList.add('connected');
            DOM.llmStatusDot.classList.remove('disconnected', 'busy');
            DOM.llmStatusDot.classList.add('idle');
            DOM.contextStatusDot.classList.remove('disconnected');
            DOM.contextStatusDot.classList.add('idle');
            
            // Update CCR (Champion Case Retrieval) indicator - check if active after configuration
            if (DOM.ccrStatusDot) {
                const status = await API.checkServerStatus();
                if (status.rag_active) {
                    DOM.ccrStatusDot.classList.remove('disconnected');
                    DOM.ccrStatusDot.classList.add('connected');
                } else {
                    DOM.ccrStatusDot.classList.remove('connected');
                    DOM.ccrStatusDot.classList.add('disconnected');
                }
            }
            
            // Update state with current provider/model
            state.currentProvider = configData.provider;
            state.currentModel = configData.model;
            safeSetItem('lastSelectedProvider', configData.provider);
            
            // Update status bar with provider and model info
            UI.updateStatusPromptName(configData.provider, configData.model);
            
            // Enable panel toggle buttons after configuration
            if (DOM.toggleHistoryButton) {
                DOM.toggleHistoryButton.classList.remove('btn-disabled');
                DOM.toggleHistoryButton.style.opacity = '';
                DOM.toggleHistoryButton.style.cursor = 'pointer';
                DOM.toggleHistoryButton.style.pointerEvents = 'auto';
                if (DOM.historyExpandIcon) DOM.historyExpandIcon.classList.remove('hidden');
                if (DOM.historyCollapseIcon) DOM.historyCollapseIcon.classList.add('hidden');
            }
            if (DOM.toggleStatusButton) {
                DOM.toggleStatusButton.classList.remove('btn-disabled');
                DOM.toggleStatusButton.style.opacity = '';
                DOM.toggleStatusButton.style.cursor = 'pointer';
                DOM.toggleStatusButton.style.pointerEvents = 'auto';
                if (DOM.statusExpandIcon) DOM.statusExpandIcon.classList.remove('hidden');
                if (DOM.statusCollapseIcon) DOM.statusCollapseIcon.classList.add('hidden');
            }
            if (DOM.toggleHeaderButton) {
                DOM.toggleHeaderButton.classList.remove('btn-disabled');
                DOM.toggleHeaderButton.style.opacity = '';
                DOM.toggleHeaderButton.style.cursor = 'pointer';
                DOM.toggleHeaderButton.style.pointerEvents = 'auto';
                if (DOM.headerExpandIcon) DOM.headerExpandIcon.classList.remove('hidden');
                if (DOM.headerCollapseIcon) DOM.headerCollapseIcon.classList.add('hidden');
            }
            
            // Show conversation header after successful configuration, but only if the
            // welcome screen is not currently visible (hideWelcomeScreen handles it otherwise)
            const welcomeScreen = document.getElementById('welcome-screen');
            const isWelcomeVisible = welcomeScreen && !welcomeScreen.classList.contains('hidden');
            if (!isWelcomeVisible) {
                const conversationHeader = document.getElementById('conversation-header');
                if (conversationHeader) conversationHeader.classList.remove('hidden');
            }
            
            // Show panel toggle buttons after configuration
            const topButtonsContainer = document.getElementById('top-buttons-container');
            if (topButtonsContainer) {
                topButtonsContainer.classList.remove('hidden');
            }

            // Load resources using profile-specific endpoint for ALL profile types
            // This avoids reliance on active_for_consumption_profile_ids which can contain stale IDs
            // (e.g., after toggling model mode with a profile override active)
            //
            // If a @TAG profile override is active (badge visible or window.activeProfileOverrideId set),
            // load that override's resources instead of the default profile's.
            let resourceProfileId = defaultProfile.id;
            const tagBadge = document.getElementById('active-profile-tag');
            const isTagBadgeVisible = tagBadge && !tagBadge.classList.contains('hidden');
            if (isTagBadgeVisible && window.configState?.profiles) {
                const tagSpan = tagBadge.querySelector('span:first-child');
                const badgeText = (tagSpan?.textContent || window.activeTagPrefix || '').trim();
                const tag = badgeText.replace('@', '').trim().toUpperCase();
                if (tag) {
                    const overrideProfile = window.configState.profiles.find(p => p.tag === tag);
                    if (overrideProfile) {
                        resourceProfileId = overrideProfile.id;
                        console.log(`[Initialization] Active @${tag} override detected, loading its resources`);
                    }
                }
            } else if (window.activeProfileOverrideId) {
                resourceProfileId = window.activeProfileOverrideId;
                console.log('[Initialization] Active profile override detected:', resourceProfileId);
            }

            if (typeof window.updateResourcePanelForProfile === 'function') {
                await window.updateResourcePanelForProfile(resourceProfileId);
                console.log('[Initialization] Loaded resources for profile:', resourceProfileId);
            } else {
                // Fallback to legacy loading if updateResourcePanelForProfile not yet available
                console.warn('[Initialization] updateResourcePanelForProfile not available, falling back to legacy loading');
                await Promise.all([
                    handleLoadResources('tools'),
                    handleLoadResources('prompts'),
                    handleLoadResources('connectors'),
                    handleLoadResources('resources')
                ]);
            }
            
            // Enable chat input
            if (DOM.chatModalButton) DOM.chatModalButton.disabled = false;
            DOM.userInput.placeholder = "Ask about databases, tables, users...";
            UI.setExecutionState(false);
            
            // Load existing session or create new one, then switch to conversation view
            // Skipped in silent mode (auto-init) — no view switching, no session loading
            if (!silent) setTimeout(async () => {
                try {
                    // Read from localStorage directly — we deliberately don't pre-populate
                    // state.currentSessionId at startup to avoid 404s from loadContextPanel
                    // before the session is confirmed to exist on the server.
                    const currentSessionId = state.currentSessionId || localStorage.getItem('currentSessionId');

                    // Reset pagination state for fresh load
                    resetSessionPagination();

                    // Load first page of sessions with pagination
                    console.log('[Session Pagination] Loading sessions with PAGE_SIZE:', SESSION_PAGE_SIZE);
                    const result = await API.loadSessions(SESSION_PAGE_SIZE, 0);
                    console.log('[Session Pagination] API result:', {
                        sessionsCount: result.sessions?.length,
                        total_count: result.total_count,
                        has_more: result.has_more
                    });

                    const sessions = result.sessions || [];
                    sessionPaginationState.loadedOffset = 0;
                    sessionPaginationState.totalCount = result.total_count;
                    sessionPaginationState.hasMore = result.has_more;

                    // Filter out archived sessions from the conversation view selector
                    const activeSessions = sessions ? sessions.filter(s => !s.archived) : [];

                    // Sort sessions hierarchically so slave sessions appear under their parents
                    const sortedSessions = sortSessionsHierarchically(activeSessions);

                    // Populate session list UI
                    DOM.sessionList.innerHTML = '';

                    // Remove any existing load more button
                    hideLoadMoreSessionsButton();

                    if (sortedSessions && Array.isArray(sortedSessions) && sortedSessions.length > 0) {
                        // Populate session list dropdown/sidebar
                        sortedSessions.forEach((session) => {
                            const isActive = session.id === currentSessionId;
                            const sessionItem = UI.addSessionToList(session, isActive);
                            DOM.sessionList.appendChild(sessionItem);
                        });

                        // Show "Load More" button if there are more sessions
                        console.log('[Session Pagination] Checking has_more:', result.has_more, 'total_count:', result.total_count, 'loaded:', sessions.length);
                        if (result.has_more) {
                            const remainingCount = result.total_count - sessions.length;
                            console.log('[Session Pagination] Showing load more button, remainingCount:', remainingCount);
                            showLoadMoreSessionsButton(remainingCount);
                        } else {
                            console.log('[Session Pagination] No more sessions to load');
                        }

                        // Update utility sessions filter visibility
                        if (window.updateUtilitySessionsFilter) {
                            window.updateUtilitySessionsFilter();
                        }

                        // Update genie master badges (adds collapse toggles to sessions with slaves)
                        updateGenieMasterBadges();

                        // Determine which session to load:
                        // 1. If currentSessionId exists (from localStorage), try to load it directly
                        // 2. The session may be beyond the paginated list - that's OK, try loading anyway
                        // 3. If that fails (session deleted), fall back to the first session
                        if (currentSessionId) {
                            const isInLoadedList = sortedSessions.some(s => s.id === currentSessionId);
                            console.log('[Session Load] Attempting to load stored session:', currentSessionId,
                                isInLoadedList ? '(found in loaded list)' : '(not in first page, loading directly)');

                            try {
                                await handleLoadSession(currentSessionId);
                            } catch (loadError) {
                                console.warn('[Session Load] Failed to load stored session, falling back to first session:', loadError.message);
                                localStorage.removeItem('currentSessionId');
                                await handleLoadSession(sortedSessions[0].id);
                            }
                        } else {
                            // No stored session ID, load the most recent session
                            console.log('[Session Load] No stored session ID, loading most recent:', sortedSessions[0].id);
                            await handleLoadSession(sortedSessions[0].id);
                        }
                    } else {
                        // No sessions on server — stale localStorage ID is invalid, clear it
                        if (currentSessionId) {
                            localStorage.removeItem('currentSessionId');
                        }
                        // No active sessions exist, create a new one
                        await handleStartNewSession();
                    }
                    
                    // Hide welcome screen and show chat interface
                    if (window.hideWelcomeScreen) {
                        window.hideWelcomeScreen();
                    }
                    
                    handleViewSwitch('conversation-view');
                    
                    // Mark initialization as complete for the conversation initializer
                    console.log('[reconnectAndLoad] Conversation fully initialized');
                    
                    // Mark configuration as clean after successful save & connect
                    import('../configDirtyState.js?v=1.189').then(({ markConfigClean }) => {
                        markConfigClean();
                        console.log('[reconnectAndLoad] Configuration marked as clean after successful save');
                    });
                    
                } catch (sessionError) {
                    console.error('Failed to load/create session:', sessionError);
                    showNotification('warning', 'Configuration successful, but failed to initialize session. Please create one manually.');
                    handleViewSwitch('conversation-view');
                    
                    // Still mark as clean since configuration was saved successfully
                    import('../configDirtyState.js?v=1.189').then(({ markConfigClean }) => {
                        markConfigClean();
                    });
                    // Show welcome screen since no session was created
                    if (window.showWelcomeScreen) {
                        await window.showWelcomeScreen();
                    }
                }
            }, 1000); // Small delay to allow user to see success message
        } else {
            if (!silent) {
                statusDiv.innerHTML = '<span class="text-red-400">✗ ' + escapeHtml(result.message) + '</span>';
                showNotification('error', result.message);
            } else {
                throw new Error(result.message);
            }
        }
    } catch (error) {
        if (!silent) {
            statusDiv.innerHTML = '<span class="text-red-400">✗ Connection failed</span>';
            showNotification('error', `Connection failed: ${error.message}`);
        } else {
            throw error;
        }
    } finally {
        if (!silent) {
            btn.disabled = false;
            btnText.textContent = 'Save & Connect';
            spinner.classList.remove('animate-spin');
            spinner.classList.add('hidden');
        }
    }
}

// ============================================================================
// INITIALIZATION
// ============================================================================

/**
 * Initialize configuration tabs
 */
function initializeConfigTabs() {
    const tabs = document.querySelectorAll('.config-tab');
    const tabContents = document.querySelectorAll('.config-tab-content');
    
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const targetTabId = tab.getAttribute('data-tab');
            
            // Update tab buttons
            tabs.forEach(t => {
                t.classList.remove('active');
            });
            tab.classList.add('active');
            
            // Update tab content
            tabContents.forEach(content => {
                if (content.id === targetTabId) {
                    content.classList.remove('hidden');
                    content.classList.add('active');
                } else {
                    content.classList.add('hidden');
                    content.classList.remove('active');
                }
            });

            // Internal-scroll tabs keep their header (title, sub-tabs, search,
            // filters) fixed and scroll only their list/grid body. For flex-1 on
            // such a tab to resolve to a definite height, the whole chain down
            // from the tab container must be a bounded flex column — including
            // the shared #config-tab-inner (max-w-7xl) wrapper. Multi-section
            // tabs (anatomy, memory, advanced) are NOT listed: they flow normally
            // and scroll as a whole via the container's own overflow-y-auto.
            const INTERNAL_SCROLL_TABS = new Set([
                'profiles-tab', 'mcp-servers-tab', 'vector-stores-tab',
                'kg-stores-tab', 'llm-providers-tab', 'skills-tab',
                'extensions-tab', 'agent-packs-tab',
            ]);
            const tabContainer = document.getElementById('config-tab-content-container');
            const tabInner = document.getElementById('config-tab-inner');
            if (tabContainer && tabInner) {
                if (INTERNAL_SCROLL_TABS.has(targetTabId)) {
                    tabContainer.style.display = 'flex';
                    tabContainer.style.flexDirection = 'column';
                    tabContainer.style.overflowY = 'hidden';
                    tabInner.style.flex = '1 1 0';
                    tabInner.style.minHeight = '0';
                    tabInner.style.display = 'flex';
                    tabInner.style.flexDirection = 'column';
                    tabInner.style.width = '100%';
                } else {
                    tabContainer.style.display = '';
                    tabContainer.style.flexDirection = '';
                    tabContainer.style.overflowY = '';
                    tabInner.style.flex = '';
                    tabInner.style.minHeight = '';
                    tabInner.style.display = '';
                    tabInner.style.flexDirection = '';
                    tabInner.style.width = '';
                }
            }

            // Lazy-load context window types when tab is activated
            if (targetTabId === 'context-window-tab') {
                loadContextWindowTypes().then(() => initContextWindowTab());
                initMemorySubtabs();
            }

            // Lazy-load agent packs when tab is activated
            if (targetTabId === 'agent-packs-tab') {
                loadAgentPacks();
            }

            // Lazy-load skills when tab is activated
            if (targetTabId === 'skills-tab') {
                loadSkills();
            }

            // Lazy-load extensions when tab is activated
            if (targetTabId === 'extensions-tab') {
                loadExtensions();
            }

            // Lazy-load vector stores when tab is activated
            if (targetTabId === 'vector-stores-tab') {
                if (window.vectorStoreHandler && !window.vectorStoreHandler.isLoaded()) {
                    window.vectorStoreHandler.load();
                }
            }
        });
    });
}

// ── Tri-state toggle handler for profile pack containers ─────────────────────

function attachPackTriStateToggleHandlers(parentElement) {
    parentElement.querySelectorAll('.pack-tristate-checkbox').forEach(checkbox => {
        checkbox.addEventListener('change', async (e) => {
            e.stopPropagation();
            const packId = e.target.dataset.packId;
            const containerCard = e.target.closest('.agent-pack-container-card');
            if (!containerCard) return;

            // Gather profile IDs from child cards inside the pack children area
            const childArea = containerCard.querySelector('.pack-children-container');
            if (!childArea) return;
            const childProfileIds = [...new Set(
                Array.from(childArea.querySelectorAll('.pack-children-stack--profile > [data-profile-id]'))
                    .map(el => el.dataset.profileId).filter(Boolean)
            )];

            if (childProfileIds.length === 0) return;

            const enableAll = e.target.checked;

            // Guard: cannot disable all if pack contains the default profile
            if (!enableAll && childProfileIds.includes(configState.defaultProfileId)) {
                e.target.checked = true;
                showNotification('error',
                    'Cannot deactivate all — this pack contains the default profile. ' +
                    'Set a different profile as default first.');
                return;
            }

            let activeIds = [...configState.activeForConsumptionProfileIds];

            if (enableAll) {
                for (const pid of childProfileIds) {
                    if (!activeIds.includes(pid)) {
                        activeIds.push(pid);
                    }
                }
            } else {
                activeIds = activeIds.filter(id => !childProfileIds.includes(id));
            }

            try {
                // Save expanded state before re-render
                const expandedPacks = new Set();
                document.querySelectorAll('.agent-pack-container-card[data-expanded="true"]').forEach(card => {
                    expandedPacks.add(card.dataset.packId);
                });

                await configState.setActiveForConsumptionProfiles(activeIds);
                await configState.loadProfiles();
                renderProfiles();
                renderLLMProviders();
                renderMCPServers();

                // Restore expanded state after re-render
                expandedPacks.forEach(pid => {
                    const card = document.querySelector(`.agent-pack-container-card[data-pack-id="${pid}"]`);
                    if (card) {
                        const children = card.querySelector('.pack-children-container');
                        if (children) {
                            children.style.maxHeight = children.scrollHeight + 'px';
                            children.style.opacity = '1';
                            children.style.marginTop = '1rem';
                            card.dataset.expanded = 'true';
                            card.classList.add('pack-container-expanded');
                        }
                    }
                });

                showNotification('success',
                    `All pack profiles ${enableAll ? 'enabled' : 'disabled'}`);
            } catch (err) {
                console.error('[PackGrouping] Profile toggle all failed:', err);
                showNotification('error', 'Failed to toggle profiles');
                renderProfiles();
            }
        });
    });
}

// ============================================================================
// UI RENDERING - PROFILES
// ============================================================================

// Filter & sort state for the IFOC profile tabs (shared across all 4 tabs).
// Persisted in localStorage so the user's choices survive a reload.
const PROFILE_FILTER_STORAGE_KEY = 'uderia_profile_filter_state_v1';
const profileFilterState = (() => {
    const defaults = { search: '', source: 'all', status: 'all', sort: 'default' };
    try {
        const saved = JSON.parse(localStorage.getItem(PROFILE_FILTER_STORAGE_KEY) || '{}');
        return { ...defaults, ...saved };
    } catch {
        return defaults;
    }
})();

function persistProfileFilterState() {
    try {
        localStorage.setItem(PROFILE_FILTER_STORAGE_KEY, JSON.stringify(profileFilterState));
    } catch { /* quota / disabled — non-fatal */ }
}

function applyProfileFilters(profiles) {
    const { search, source, status } = profileFilterState;
    const needle = search.trim().toLowerCase();
    return profiles.filter(p => {
        if (source === 'pack' && (!p.agent_packs || p.agent_packs.length === 0)) return false;
        if (source === 'standalone' && p.agent_packs && p.agent_packs.length > 0) return false;

        const isActive = configState.activeForConsumptionProfileIds.includes(p.id);
        if (status === 'active' && !isActive) return false;
        if (status === 'inactive' && isActive) return false;

        if (needle) {
            const haystack = [
                p.name, p.tag, p.description,
                p.llmConfigName, p.providerName, p.modelName,
                ...(p.agent_packs || []).map(a => a.name)
            ].filter(Boolean).join(' ').toLowerCase();
            if (!haystack.includes(needle)) return false;
        }
        return true;
    });
}

function applyProfileSort(profiles) {
    const { sort } = profileFilterState;
    const byName = (a, b) => (a.name || '').localeCompare(b.name || '');
    if (sort === 'az') return [...profiles].sort(byName);
    if (sort === 'active') {
        return [...profiles].sort((a, b) => {
            const aActive = configState.activeForConsumptionProfileIds.includes(a.id) ? 0 : 1;
            const bActive = configState.activeForConsumptionProfileIds.includes(b.id) ? 0 : 1;
            return aActive - bActive || byName(a, b);
        });
    }
    // 'default' — preserve historical order: alphabetical by profile name,
    // then groupByAgentPack pulls packs out at the position of their first member.
    return [...profiles].sort(byName);
}

let profileFilterBarWired = false;
function setupProfileFilterBar() {
    if (profileFilterBarWired) {
        // Already wired — just refresh the visible state in case of reload.
        syncProfileFilterUi();
        return;
    }
    profileFilterBarWired = true;

    const searchInput = document.getElementById('profile-search');
    const filterContainer = document.getElementById('profile-filters');
    const sortTrigger = document.getElementById('profile-sort-trigger');
    const sortMenu = document.getElementById('profile-sort-menu');
    if (!searchInput || !filterContainer || !sortTrigger || !sortMenu) return;

    let searchTimer = null;
    searchInput.addEventListener('input', e => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => {
            profileFilterState.search = e.target.value;
            persistProfileFilterState();
            renderProfiles();
        }, 150);
    });

    filterContainer.addEventListener('click', e => {
        const pill = e.target.closest('button[data-pf-filter]');
        if (!pill) return;
        const filter = pill.dataset.pfFilter;
        const value = pill.dataset.value;
        if (profileFilterState[filter] === value) return;
        profileFilterState[filter] = value;
        persistProfileFilterState();
        // Update active class on sibling pills.
        filterContainer.querySelectorAll(`button[data-pf-filter="${filter}"]`).forEach(btn => {
            btn.classList.toggle('active', btn.dataset.value === value);
        });
        renderProfiles();
    });

    sortTrigger.addEventListener('click', e => {
        e.stopPropagation();
        sortMenu.classList.toggle('hidden');
    });
    sortMenu.addEventListener('click', e => {
        const opt = e.target.closest('button[data-value]');
        if (!opt) return;
        profileFilterState.sort = opt.dataset.value;
        persistProfileFilterState();
        sortMenu.classList.add('hidden');
        syncProfileFilterUi();
        renderProfiles();
    });
    document.addEventListener('click', e => {
        if (!sortMenu.classList.contains('hidden') && !sortMenu.contains(e.target) && e.target !== sortTrigger) {
            sortMenu.classList.add('hidden');
        }
    });

    syncProfileFilterUi();
}

function syncProfileFilterUi() {
    const searchInput = document.getElementById('profile-search');
    if (searchInput && searchInput.value !== profileFilterState.search) {
        searchInput.value = profileFilterState.search;
    }
    document.querySelectorAll('#profile-filters button[data-pf-filter]').forEach(btn => {
        const filter = btn.dataset.pfFilter;
        btn.classList.toggle('active', btn.dataset.value === profileFilterState[filter]);
    });
    const sortLabel = document.getElementById('profile-sort-label');
    if (sortLabel) {
        const labels = { default: 'Default', az: 'A — Z', active: 'Active first' };
        sortLabel.textContent = labels[profileFilterState.sort] || 'Default';
    }
    document.querySelectorAll('#profile-sort-menu .ext-sort-option').forEach(btn => {
        btn.classList.toggle('selected', btn.dataset.value === profileFilterState.sort);
    });
}

function updateProfileFilterSummary() {
    const summary = document.getElementById('profile-filter-summary');
    if (!summary) return;
    const total = configState.profiles.length;
    const visible = applyProfileFilters(configState.profiles).length;
    summary.textContent = visible === total ? '' : `${visible} of ${total} match`;
}

export function renderProfiles() {
    const conversationContainer = document.getElementById('conversation-profiles-container');
    const toolContainer = document.getElementById('tool-profiles-container');

    if (!conversationContainer || !toolContainer) return;

    console.log('[renderProfiles] Rendering', configState.profiles.length, 'profiles');

    // Update Test All Profiles button state
    const testAllProfilesBtn = document.getElementById('test-all-profiles-btn');
    if (testAllProfilesBtn) {
        if (configState.profiles.length === 0) {
            testAllProfilesBtn.disabled = true;
            testAllProfilesBtn.classList.add('opacity-50', 'cursor-not-allowed');
            testAllProfilesBtn.classList.remove('hover:bg-blue-700');
        } else {
            testAllProfilesBtn.disabled = false;
            testAllProfilesBtn.classList.remove('opacity-50', 'cursor-not-allowed');
            testAllProfilesBtn.classList.add('hover:bg-blue-700');
        }
    }

    // Separate profiles by type
    const conversationProfiles = configState.profiles.filter(p => p.profile_type === 'llm_only');
    const ragFocusedProfiles = configState.profiles.filter(p => p.profile_type === 'rag_focused');
    const genieProfiles = configState.profiles.filter(p => p.profile_type === 'genie');
    const toolProfiles = configState.profiles.filter(p => !p.profile_type || p.profile_type === 'tool_enabled');

    // Helper: render profiles into a container, grouping by agent pack
    function renderProfilesIntoContainer(profiles, container, emptyMsg) {
        if (!container) return;
        const totalCount = profiles.length;
        if (totalCount === 0) {
            container.innerHTML = `<div class="text-center text-gray-400 py-8"><p>${emptyMsg}</p></div>`;
            return;
        }
        const filtered = applyProfileFilters(profiles);
        if (filtered.length === 0) {
            container.innerHTML = `<div class="text-center text-gray-400 py-8"><p>No profiles match the current filters.</p><p class="text-xs mt-2">${totalCount} hidden — clear filters to show all.</p></div>`;
            return;
        }
        const sorted = applyProfileSort(filtered);
        const { packGroups, ungrouped } = groupByAgentPack(sorted);
        let html = '';
        for (const [, group] of packGroups) {
            if (group.resources.length === 1) {
                html += renderProfileCard(group.resources[0]);
            } else {
                const childHtml = group.resources.map(p => renderProfileCard(p)).join('');
                html += createPackContainerCard(group.pack, group.resources, 'profile', childHtml);
            }
        }
        html += ungrouped.map(p => renderProfileCard(p)).join('');
        container.innerHTML = html;
        attachPackContainerHandlers(container);
        attachPackTriStateToggleHandlers(container);
        if (window.TrustBadge) window.TrustBadge.mountAgentBadges(container);

        // Hide "Reclassify All" for non-tool_enabled profile tabs
        const profileType = profiles[0]?.profile_type || 'tool_enabled';
        if (profileType !== 'tool_enabled') {
            container.querySelectorAll('.pack-reclassify-all-btn').forEach(btn => {
                btn.previousElementSibling?.classList.contains('pack-edit-dropdown-divider')
                    && btn.previousElementSibling.remove();
                btn.remove();
            });
        }
    }

    // Render all 4 profile class sections
    renderProfilesIntoContainer(conversationProfiles, conversationContainer, 'No conversation profiles configured.');

    const ragContainer = document.getElementById('rag-profiles-container');
    renderProfilesIntoContainer(ragFocusedProfiles, ragContainer, 'No RAG focused profiles configured.');

    const genieContainer = document.getElementById('genie-profiles-container');
    renderProfilesIntoContainer(genieProfiles, genieContainer,
        'No Genie profiles configured.</p><p class="text-xs mt-2">Genie profiles coordinate multiple other profiles to answer complex questions.');

    renderProfilesIntoContainer(toolProfiles, toolContainer, 'No tool-enabled profiles configured.');

    // Setup tab switching
    setupProfileTypeTabs();

    // Wire up the shared filter/sort bar (idempotent — only attaches listeners once).
    setupProfileFilterBar();
    updateProfileFilterSummary();

    // Attach event listeners to profile action buttons
    attachProfileEventListeners();
}

// Expose on window for modules that can't import directly (circular dependency)
window.renderProfiles = renderProfiles;

function renderProfileCard(profile) {
        const isDefault = profile.id === configState.defaultProfileId;

        // Check if this profile is the master for its specific MCP server
        const mcpServerId = profile.mcpServerId;
        const isMasterClassification = mcpServerId && configState.masterClassificationProfileIds[mcpServerId] === profile.id;

        const isActiveForConsumption = configState.activeForConsumptionProfileIds.includes(profile.id);
        const testStatus = configState.profileTestStatus[profile.id];
        const testsPassedForDefault = testStatus?.passed === true;

        return `
        <div class="bg-white/5 border ${isDefault ? 'border-[#F15F22]' : 'border-white/10'} rounded-lg p-4" data-profile-id="${profile.id}">
            <div class="flex items-start justify-between">
                <div class="flex items-start gap-3 flex-1">
                    <div class="flex flex-col items-center gap-2">
                        <button title="${isDefault ? 'Current Default Profile' : (testsPassedForDefault ? 'Set as Default' : 'Click to test and set as default')}"
                                data-action="set-default-profile"
                                data-profile-id="${profile.id}"
                                class="p-1 rounded-full ${isDefault ? 'text-yellow-400' : 'text-gray-500 hover:text-yellow-400'}">
                            <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20"><path d="M10 15l-5.878 3.09 1.123-6.545L.489 6.91l6.572-.955L10 0l2.939 5.955 6.572.955-4.756 4.635 1.123 6.545z"/></svg>
                        </button>
                        <div class="flex items-center" title="${(() => {
                            const testStatus = configState.profileTestStatus[profile.id];
                            if (isActiveForConsumption) return 'Active for Consumption';
                            if (!testStatus?.tested || !testStatus?.passed) return 'Click to test and activate';
                            return 'Activate for Consumption';
                        })()}">
                           <label class="ind-toggle ind-toggle--primary">
                                <input type="checkbox" data-action="toggle-active-consumption" data-profile-id="${profile.id}" ${isActiveForConsumption ? 'checked' : ''}>
                                <span class="ind-track"></span>
                            </label>
                        </div>
                    </div>
                    <div class="flex-1">
                        <div class="flex items-center gap-2 mb-1">
                            <h4 class="font-semibold text-white">${escapeHtml(profile.name || profile.tag)}</h4>
                            <span class="inline-flex items-center" data-trust-agent-id="${profile.id}" data-trust-agent-tag="${escapeHtml(profile.tag || '')}"></span>
                            <div class="flex items-center gap-1">
                                ${(() => {
                                    // IFOC colour — single source of truth (profileColors.js).
                                    const tagColor = (IFOC_CONFIG[profile.profile_type] && IFOC_CONFIG[profile.profile_type].color) || profile.color || '#F15F22';
                                    const packBadges = (profile.agent_packs || []).map(p =>
                                        `<span class="agent-pack-badge"><svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4"/></svg>${escapeHtml(p.name)}</span>`
                                    ).join('');
                                    return `
                                <span class="profile-tag profile-tag--md"
                                      style="--profile-tag-bg: ${tagColor}; --profile-tag-border: ${tagColor}; --profile-tag-text: #FFFFFF;">
                                    @${escapeHtml(profile.tag)}
                                </span>` + packBadges;
                                })()}
                                <button type="button" data-action="copy-profile-id" data-profile-id="${profile.id}"
                                    class="inline-flex items-center justify-center p-1 ml-1 text-gray-400 hover:text-[#F15F22] transition-colors rounded hover:bg-white/5 group relative"
                                    title="Copy Profile ID">
                                    <svg class="w-4 h-4" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                        <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                                        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                                    </svg>
                                    <span class="text-xs font-semibold opacity-0 group-hover:opacity-100 transition-opacity absolute left-full ml-2 bg-gray-800 px-2 py-1 rounded whitespace-nowrap pointer-events-none">Copied!</span>
                                </button>
                            </div>
                        </div>
                        ${(() => {
                            // Status badges section - non-interactive indicators
                            const badges = [];

                            // Warning for RAG focused profiles without knowledge collections
                            const profileType = profile.profile_type || 'tool_enabled';
                            if (profileType === 'rag_focused') {
                                const knowledgeCollections = profile.knowledgeConfig?.collections || [];
                                if (knowledgeCollections.length === 0) {
                                    badges.push(`
                                    <span class="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-semibold bg-yellow-500/20 text-yellow-300 border border-yellow-400/30 rounded-full" title="RAG focused profiles require at least 1 knowledge collection">
                                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/>
                                        </svg>
                                        No Knowledge Collections
                                    </span>
                                    `);
                                }
                            }

                            // Master Classification status badge
                            if (isMasterClassification) {
                                badges.push(`
                                <span class="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-semibold bg-amber-500/20 text-amber-300 border border-amber-400/30 rounded-full" title="This is the primary classification profile - other profiles inherit classification from this one">
                                    <svg class="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 20 20">
                                        <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z"/>
                                    </svg>
                                    Primary Classification
                                </span>
                                `);
                            }

                            // Inheritance status badge (only for tool-enabled profiles)
                            if (profile.profile_type === 'tool_enabled' && profile.inherit_classification && !isMasterClassification) {
                                // Get the per-server master for this profile's MCP server
                                const profileMcpServerId = profile.mcpServerId;
                                const masterProfileId = profileMcpServerId ? configState.masterClassificationProfileIds[profileMcpServerId] : null;
                                const masterProfile = masterProfileId ? configState.profiles.find(p => p.id === masterProfileId) : null;
                                const masterName = masterProfile ? escapeHtml(masterProfile.name) : 'master profile';
                                badges.push(`
                                <span class="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-semibold bg-orange-500/20 text-orange-300 border border-orange-400/30 rounded-full" title="Inheriting classification from ${masterName}">
                                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>
                                    </svg>
                                    Inherits from ${masterName}
                                </span>
                                `);
                            }

                            return badges.length > 0 ? `<div class="flex items-center gap-2 mb-2">${badges.join('')}</div>` : '';
                        })()}
                        <p class="text-sm text-gray-400 mb-3">${escapeHtml(profile.description)}</p>
                        <div class="text-sm text-gray-400 space-y-1">
                            <p><span class="font-medium">LLM:</span> ${(() => {
                                const llmConfig = configState.llmConfigurations.find(c => c.id === profile.llmConfigurationId);
                                if (llmConfig) {
                                    const providerDisplay = profile.providerName || llmConfig.provider;
                                    return `${getProviderLabel(providerDisplay)} / ${escapeHtml(llmConfig.model)}`;
                                }
                                return 'N/A';
                            })()}</p>
                            ${(() => {
                                // Show dual-model badge if configured
                                if (profile.dualModelConfig && (profile.dualModelConfig.strategicModelId || profile.dualModelConfig.tacticalModelId)) {
                                    const getLLMName = (id) => {
                                        const config = configState.llmConfigurations.find(c => c.id === id);
                                        return config ? config.name.split('/').pop() : 'default'; // Short name
                                    };

                                    const strategicName = profile.dualModelConfig.strategicModelId ?
                                        getLLMName(profile.dualModelConfig.strategicModelId) : 'main';
                                    const tacticalName = profile.dualModelConfig.tacticalModelId ?
                                        getLLMName(profile.dualModelConfig.tacticalModelId) : 'main';

                                    return `
                                        <div class="mt-1 text-xs text-blue-400 flex items-center gap-1">
                                            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"></path>
                                            </svg>
                                            <span>Dual: ${escapeHtml(strategicName)} / ${escapeHtml(tacticalName)}</span>
                                        </div>
                                    `;
                                }
                                return '';
                            })()}
                            ${profile.profile_type === 'tool_enabled' ? `
                            <p><span class="font-medium">MCP:</span> ${escapeHtml(configState.mcpServers.find(s => s.id === profile.mcpServerId)?.name || 'Unknown')}</p>
                            ` : ''}
                            ${profile.classification_mode && profile.profile_type === 'tool_enabled' ? `
                            <p><span class="font-medium">Classification:</span> ${(() => {
                                const mode = profile.classification_mode;
                                const badges = {
                                    'light': '<span class="inline-flex items-center px-1.5 py-0.5 text-xs font-medium bg-cyan-500/20 text-cyan-400 rounded">Light</span>',
                                    'full': '<span class="inline-flex items-center px-1.5 py-0.5 text-xs font-medium bg-orange-500/20 text-orange-400 rounded">Full</span>'
                                };
                                return badges[mode] || badges['light'];
                            })()}</p>
                            ` : ''}
                            ${profile.needs_reclassification && (profile.profile_type === 'tool_enabled' || (profile.profile_type === 'llm_only' && profile.useMcpTools)) ? `
                            <p><span class="inline-flex items-center gap-1 px-2 py-1 text-xs font-medium bg-yellow-500/20 text-yellow-400 rounded border border-yellow-500/30">
                                <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path>
                                </svg>
                                Reclassification Recommended
                            </span></p>
                            ` : ''}
                            ${profile.profile_type === 'genie' ? `
                            <p><span class="font-medium">Child Profiles:</span> ${(() => {
                                const slaveProfiles = profile.genieConfig?.slaveProfiles || [];
                                if (slaveProfiles.length === 0) {
                                    return '<span class="text-yellow-400">None configured</span>';
                                }
                                const slaveTags = slaveProfiles.map(slaveId => {
                                    const slaveProfile = configState.profiles.find(p => p.id === slaveId);
                                    if (!slaveProfile) return '<span class="text-gray-400">Unknown</span>';
                                    // IFOC colour — single source of truth (profileColors.js).
                                    const color = (IFOC_CONFIG[slaveProfile.profile_type] && IFOC_CONFIG[slaveProfile.profile_type].color) || '#9ca3af';
                                    const packIcon = (slaveProfile.agent_packs?.length) ? `<span class="agent-pack-indicator" title="${escapeHtml(slaveProfile.agent_packs[0].name)}"><svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4"/></svg></span>` : '';
                                    return `<span class="profile-tag profile-tag--sm" style="--profile-tag-bg: ${color}; --profile-tag-border: ${color}; --profile-tag-text: #FFFFFF;">@${escapeHtml(slaveProfile.tag)}</span>${packIcon}`;
                                });
                                return slaveTags.join(', ');
                            })()}</p>
                            ` : ''}
                        </div>
                        <div class="mt-2 text-xs" id="test-results-${profile.id}">${(() => {
                            const testStatus = configState.profileTestStatus[profile.id];
                            // If test results exist, show them
                            if (testStatus?.results) {
                                return testStatus.results;
                            }
                            // Otherwise show warning if not active and not tested/passed
                            if (!isActiveForConsumption && (!testStatus?.tested || !testStatus?.passed)) {
                                return '<span class="text-yellow-400">⚠ Run test before activating profile</span>';
                            }
                            return '';
                        })()}</div>
                    </div>
                </div>
                <div class="flex items-center gap-2">
                    <!-- Test Button (always visible for all profile types) -->
                    <button type="button" data-action="test-profile" data-profile-id="${profile.id}"
                        class="card-btn card-btn--lg card-btn--info flex items-center gap-2"
                        title="Test profile configuration">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>
                        </svg>
                        Test
                    </button>

                    <!-- Classification Dropdown (only for tool-enabled profiles) -->
                    ${profile.profile_type === 'tool_enabled' ? `
                    <div class="relative inline-block">
                        <button type="button" data-action="toggle-classification-menu" data-profile-id="${profile.id}"
                            class="card-btn card-btn--lg card-btn--secondary flex items-center gap-2"
                            title="Classification options">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01"/>
                            </svg>
                            Classification
                            <svg class="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
                                <path fill-rule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd"/>
                            </svg>
                        </button>
                        <div class="classification-menu hidden absolute right-0 mt-2 w-64 bg-gray-800 border border-gray-700 rounded-lg shadow-xl z-50" data-profile-id="${profile.id}">
                            <div class="py-1">
                                <!-- Set as Master Classification -->
                                <button type="button" data-action="set-master-classification" data-profile-id="${profile.id}"
                                    class="w-full text-left px-4 py-2.5 text-sm hover:bg-gray-700 flex items-center gap-3 ${isMasterClassification ? 'text-amber-300 bg-amber-500/10' : 'text-gray-300'} ${!profile.mcpServerId ? 'opacity-50 cursor-not-allowed' : ''}"
                                    title="${(() => {
                                        if (!profile.mcpServerId) return 'Profile must have an MCP server configured';
                                        if (isMasterClassification) return 'This is the primary classification profile';
                                        return 'Set as primary classification profile (other profiles inherit from this)';
                                    })()}"
                                    ${!profile.mcpServerId ? 'disabled' : ''}>
                                    <svg class="w-4 h-4 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                                        <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z"/>
                                    </svg>
                                    <span class="flex-1">${isMasterClassification ? 'Primary Classification' : 'Set as Primary'}</span>
                                </button>

                                <!-- Inherit Classification Toggle -->
                                <button type="button" data-action="inherit-classification" data-profile-id="${profile.id}"
                                    class="w-full text-left px-4 py-2.5 text-sm hover:bg-gray-700 flex items-center gap-3 ${profile.inherit_classification ? 'text-orange-300 bg-orange-500/10' : 'text-gray-300'} ${!isActiveForConsumption || isMasterClassification ? 'opacity-50 cursor-not-allowed' : ''}"
                                    title="${isMasterClassification ? 'Primary profile cannot inherit (it is the source)' : (!isActiveForConsumption ? 'Activate profile to enable inheritance' : (profile.inherit_classification ? 'Currently inheriting - click to disable' : 'Inherit from primary classification profile'))}"
                                    ${!isActiveForConsumption || isMasterClassification ? 'disabled' : ''}>
                                    <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"/>
                                    </svg>
                                    <span class="flex-1">${profile.inherit_classification ? '✓ Inheriting' : 'Inherit Classification'}</span>
                                </button>

                                <div class="border-t border-gray-700 my-1"></div>

                                <!-- Reclassify Profile -->
                                <button type="button" data-action="reclassify-profile" data-profile-id="${profile.id}"
                                    class="w-full text-left px-4 py-2.5 text-sm text-gray-300 hover:bg-gray-700 flex items-center gap-3 ${!isActiveForConsumption || profile.inherit_classification ? 'opacity-50 cursor-not-allowed' : ''}"
                                    title="${profile.inherit_classification ? 'Disable inherit to reclassify' : (!isActiveForConsumption ? 'Activate profile to reclassify' : 'Re-run classification for this profile')}"
                                    ${!isActiveForConsumption || profile.inherit_classification ? 'disabled' : ''}>
                                    <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
                                    </svg>
                                    <span class="flex-1">Reclassify</span>
                                </button>

                                <!-- Show Classification -->
                                <button type="button" data-action="show-classification" data-profile-id="${profile.id}"
                                    class="w-full text-left px-4 py-2.5 text-sm text-gray-300 hover:bg-gray-700 flex items-center gap-3 ${!isActiveForConsumption || !profile.classification_results || profile.inherit_classification ? 'opacity-50 cursor-not-allowed' : ''}"
                                    title="${profile.inherit_classification ? 'Disable inherit to view own classification' : (!isActiveForConsumption ? 'Activate profile to view' : (profile.classification_results ? 'View classification results' : 'No classification results'))}"
                                    ${!isActiveForConsumption || !profile.classification_results || profile.inherit_classification ? 'disabled' : ''}>
                                    <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/>
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/>
                                    </svg>
                                    <span class="flex-1">Show Results</span>
                                </button>
                            </div>
                        </div>
                    </div>
                    ` : ''}

                    <!-- Overflow Menu (Copy/Edit/Delete - consistent across all profile types) -->
                    <div class="relative inline-block">
                        <button type="button" data-action="toggle-profile-menu" data-profile-id="${profile.id}"
                            class="card-btn card-btn--lg card-btn--neutral"
                            title="More options">
                            <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                                <path d="M10 6a2 2 0 110-4 2 2 0 010 4zM10 12a2 2 0 110-4 2 2 0 010 4zM10 18a2 2 0 110-4 2 2 0 010 4z"/>
                            </svg>
                        </button>
                        <div class="profile-menu hidden absolute right-0 mt-2 w-48 bg-gray-800 border border-gray-700 rounded-lg shadow-xl z-50" data-profile-id="${profile.id}">
                            <div class="py-1">
                                <button type="button" data-action="copy-profile" data-profile-id="${profile.id}"
                                    class="w-full text-left px-4 py-2.5 text-sm text-gray-300 hover:bg-gray-700 flex items-center gap-3">
                                    <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"/>
                                    </svg>
                                    <span class="flex-1">Duplicate Profile</span>
                                </button>
                                ${(profile.is_subscribed && !profile.is_owned) ? `
                                <button type="button" disabled
                                    class="w-full text-left px-4 py-2.5 text-sm text-gray-600 cursor-not-allowed flex items-center gap-3"
                                    title="Managed by: ${escapeHtml((profile.agent_packs || []).map(p => p.name).join(', ') || 'external source')} — uninstall the pack(s) to edit">
                                    <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/>
                                    </svg>
                                    <span class="flex-1">Edit Profile</span>
                                </button>
                                ` : `
                                <button type="button" data-action="edit-profile" data-profile-id="${profile.id}"
                                    class="w-full text-left px-4 py-2.5 text-sm text-gray-300 hover:bg-gray-700 flex items-center gap-3">
                                    <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/>
                                    </svg>
                                    <span class="flex-1">Edit Profile</span>
                                </button>
                                `}
                                <button type="button" data-action="open-anatomy" data-profile-id="${profile.id}"
                                    class="w-full text-left px-4 py-2.5 text-sm text-gray-300 hover:bg-gray-700 flex items-center gap-3">
                                    <svg class="w-4 h-4 flex-shrink-0" viewBox="-13 -13 26 26" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round">
                                        <path d="M0,-11 L2.7,-3.72 L10.46,-3.4 L4.37,1.42 L6.47,8.9 L0,4.6 L-6.47,8.9 L-4.37,1.42 L-10.46,-3.4 L-2.7,-3.72 Z"/>
                                    </svg>
                                    <span class="flex-1">Anatomy</span>
                                </button>
                                <div class="border-t border-gray-700 my-1"></div>
                                ${(profile.agent_packs?.length > 0) ? `
                                <button type="button" disabled
                                    class="w-full text-left px-4 py-2.5 text-sm text-gray-600 cursor-not-allowed flex items-center gap-3"
                                    title="Managed by: ${escapeHtml((profile.agent_packs || []).map(p => p.name).join(', '))} — uninstall the pack(s) to remove">
                                    <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
                                    </svg>
                                    <span class="flex-1">Delete Profile</span>
                                </button>
                                ` : `
                                <button type="button" data-action="delete-profile" data-profile-id="${profile.id}"
                                    class="w-full text-left px-4 py-2.5 text-sm text-red-400 hover:bg-red-500/10 hover:text-red-300 flex items-center gap-3">
                                    <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
                                    </svg>
                                    <span class="flex-1">Delete Profile</span>
                                </button>
                                `}
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `;
}

// Profile tab colors - matches the color scheme for each profile type
const profileColors = {
    'conversation-profiles': '#4ade80',  // Green - Ideate
    'rag-profiles': '#3b82f6',           // Blue - Focus
    'tool-profiles': '#9333ea',          // Purple - Optimize
    'genie-profiles': '#F15F22'          // Orange - Coordinate (Teradata brand)
};

// Description banner mapping for each tab
const descriptionMap = {
    'conversation-profiles': 'description-conversation',
    'rag-profiles': 'description-rag',
    'tool-profiles': 'description-tool',
    'genie-profiles': 'description-genie'
};

// LLM Provider tab colors (brand colors)
const providerColors = {
    'all': '#6b7280',       // Gray
    'google': '#4285f4',    // Google Blue
    'anthropic': '#8b5cf6', // Purple
    'openai': '#10a37f',    // OpenAI Green
    'amazon': '#ff9900',    // AWS Orange
    'azure': '#00bfff',     // Azure Cyan
    'friendli': '#ec4899',  // Pink
    'mlx': '#34c759',       // Apple system green
    'ollama': '#64748b',    // Slate gray
    'openrouter': '#6366f1',// Indigo
    'xai': '#e5e5e5',       // Near-white (xAI / Grok)
    'celeris': '#06b6d4'    // Cyan (Celeris diffusion LLMs)
};

// LLM Provider description banner mapping
const llmDescriptionMap = {
    'all': 'llm-description-all',
    'google': 'llm-description-google',
    'anthropic': 'llm-description-anthropic',
    'openai': 'llm-description-openai',
    'amazon': 'llm-description-amazon',
    'azure': 'llm-description-azure',
    'friendli': 'llm-description-friendli',
    'mlx': 'llm-description-mlx',
    'ollama': 'llm-description-ollama',
    'openrouter': 'llm-description-openrouter',
    'xai': 'llm-description-xai',
    'celeris': 'llm-description-celeris'
};

// Current LLM provider filter
let activeLLMProviderFilter = 'all';

// MCP transport type colors
const mcpTransportColors = {
    'all': '#6b7280',    // Gray
    'stdio': '#F15F22',  // Orange (local execution)
    'http': '#3b82f6',   // Blue (network)
    'sse': '#4ade80'     // Green (streaming)
};

// MCP transport description mapping
const mcpDescriptionMap = {
    'all': 'mcp-description-all',
    'stdio': 'mcp-description-stdio',
    'http': 'mcp-description-http',
    'sse': 'mcp-description-sse'
};

// Provider icons for card labels (with colors)
export const providerIcons = {
    'google': {
        color: '#4285f4',
        icon: `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 inline-block align-text-bottom" viewBox="0 0 24 24" fill="#4285f4"><path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/><path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/><path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/></svg>`
    },
    'anthropic': {
        color: '#8b5cf6',
        icon: `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 inline-block align-text-bottom" viewBox="0 0 24 24" fill="#8b5cf6"><path d="M17.604 3.332L12 20.668l-1.604-5.163h-6.26L12 3.332h5.604zM6.136 15.505L12 3.332l5.864 12.173H6.136z"/></svg>`
    },
    'openai': {
        color: '#10a37f',
        icon: `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 inline-block align-text-bottom" viewBox="0 0 24 24" fill="#10a37f"><path d="M22.282 9.821a5.985 5.985 0 0 0-.516-4.91 6.046 6.046 0 0 0-6.51-2.9A6.065 6.065 0 0 0 4.981 4.18a5.985 5.985 0 0 0-3.998 2.9 6.046 6.046 0 0 0 .743 7.097 5.98 5.98 0 0 0 .51 4.911 6.051 6.051 0 0 0 6.515 2.9A5.985 5.985 0 0 0 13.26 24a6.056 6.056 0 0 0 5.772-4.206 5.99 5.99 0 0 0 3.997-2.9 6.056 6.056 0 0 0-.747-7.073zM13.26 22.43a4.476 4.476 0 0 1-2.876-1.04l.141-.081 4.779-2.758a.795.795 0 0 0 .392-.681v-6.737l2.02 1.168a.071.071 0 0 1 .038.052v5.583a4.504 4.504 0 0 1-4.494 4.494zM3.6 18.304a4.47 4.47 0 0 1-.535-3.014l.142.085 4.783 2.759a.771.771 0 0 0 .78 0l5.843-3.369v2.332a.08.08 0 0 1-.033.062L9.74 19.95a4.5 4.5 0 0 1-6.14-1.646zM2.34 7.896a4.485 4.485 0 0 1 2.366-1.973V11.6a.766.766 0 0 0 .388.676l5.815 3.355-2.02 1.168a.076.076 0 0 1-.071 0l-4.83-2.786A4.504 4.504 0 0 1 2.34 7.872zm16.597 3.855l-5.833-3.387L15.119 7.2a.076.076 0 0 1 .071 0l4.83 2.791a4.494 4.494 0 0 1-.676 8.105v-5.678a.79.79 0 0 0-.407-.667zm2.01-3.023l-.141-.085-4.774-2.782a.776.776 0 0 0-.785 0L9.409 9.23V6.897a.066.066 0 0 1 .028-.061l4.83-2.787a4.5 4.5 0 0 1 6.68 4.66zm-12.64 4.135l-2.02-1.164a.08.08 0 0 1-.038-.057V6.075a4.5 4.5 0 0 1 7.375-3.453l-.142.08L8.704 5.46a.795.795 0 0 0-.393.681zm1.097-2.365l2.602-1.5 2.607 1.5v2.999l-2.597 1.5-2.607-1.5z"/></svg>`
    },
    'amazon': {
        color: '#ff9900',
        icon: `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 inline-block align-text-bottom" viewBox="0 0 24 24" fill="#ff9900"><path d="M.045 18.02c.072-.116.187-.124.348-.022 3.636 2.11 7.594 3.166 11.87 3.166 2.852 0 5.668-.533 8.447-1.595l.315-.14c.138-.06.234-.1.293-.13.226-.088.39-.046.525.13.12.174.09.336-.12.48-.256.19-.6.41-1.006.654-1.244.743-2.64 1.316-4.185 1.726a17.617 17.617 0 01-10.951-.577 17.88 17.88 0 01-5.43-3.35c-.1-.074-.151-.15-.151-.22 0-.047.021-.09.045-.122zm6.087-6.67c0-1.293.252-2.36.73-3.2.476-.84 1.132-1.47 1.965-1.89.834-.42 1.8-.63 2.887-.63 1.065 0 1.93.18 2.59.535l-.07.21c-.1.29-.17.495-.2.62-.03.12-.075.17-.135.19l-.115.01c-.16-.07-.36-.14-.6-.21a3.9 3.9 0 00-1.09-.14c-.92 0-1.637.29-2.148.87-.51.58-.767 1.39-.767 2.44v1.13c0 1.13.26 2 .78 2.58.52.58 1.25.87 2.19.87.59 0 1.18-.09 1.77-.27.165-.05.265-.055.3-.015.035.04.05.135.05.29l.05.49c.02.12.015.2-.02.26-.03.06-.09.11-.17.15a6.98 6.98 0 01-2.3.53c-1.62 0-2.86-.455-3.715-1.36-.856-.905-1.283-2.19-1.283-3.85v-.98z"/></svg>`
    },
    'azure': {
        color: '#00bfff',
        icon: `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 inline-block align-text-bottom" viewBox="0 0 24 24" fill="#00bfff"><path d="M13.05 4.24L6.56 18.05a.5.5 0 00.46.7h12.43a.5.5 0 00.44-.74L13.5 4.24a.5.5 0 00-.45-.29.5.5 0 00-.45.29zM5.58 18.75L.26 10.04a.5.5 0 01.43-.75h5.53l4.68 9.46H5.58z"/></svg>`
    },
    'friendli': {
        color: '#ec4899',
        icon: `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 inline-block align-text-bottom" viewBox="0 0 24 24" fill="#ec4899"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>`
    },
    'ollama': {
        color: '#64748b',
        icon: `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 inline-block align-text-bottom" viewBox="0 0 24 24" fill="#64748b"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>`
    },
    'openrouter': {
        color: '#6366f1',
        icon: `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 inline-block align-text-bottom" viewBox="0 0 24 24" fill="#6366f1"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 14H9V8h2v8zm4 0h-2V8h2v8z"/></svg>`
    },
    'xai': {
        color: '#e5e5e5',
        icon: `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 inline-block align-text-bottom" viewBox="0 0 24 24" fill="none" stroke="#e5e5e5" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="5" x2="19" y2="19"/><line x1="19" y1="5" x2="5" y2="19"/></svg>`
    },
    'celeris': {
        color: '#06b6d4',
        icon: `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 inline-block align-text-bottom" viewBox="0 0 24 24" fill="none" stroke="#06b6d4" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z"/></svg>`
    },
    'mlx': {
        color: '#34c759',
        icon: `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 inline-block align-text-bottom" viewBox="0 0 24 24" fill="none" stroke="#34c759" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>`
    }
};

// MCP transport icons for card labels (with colors)
const transportIcons = {
    'stdio': {
        color: '#F15F22',
        icon: `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 inline-block align-text-bottom" viewBox="0 0 24 24" fill="#F15F22"><path fill-rule="evenodd" d="M2.25 6a3 3 0 013-3h13.5a3 3 0 013 3v12a3 3 0 01-3 3H5.25a3 3 0 01-3-3V6zm3.97.97a.75.75 0 011.06 0l2.25 2.25a.75.75 0 010 1.06l-2.25 2.25a.75.75 0 01-1.06-1.06l1.72-1.72-1.72-1.72a.75.75 0 010-1.06zm4.28 4.28a.75.75 0 000 1.5h3a.75.75 0 000-1.5h-3z" clip-rule="evenodd" /></svg>`
    },
    'http': {
        color: '#3b82f6',
        icon: `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 inline-block align-text-bottom" viewBox="0 0 24 24" fill="#3b82f6"><path fill-rule="evenodd" d="M12 2.25c-5.385 0-9.75 4.365-9.75 9.75s4.365 9.75 9.75 9.75 9.75-4.365 9.75-9.75S17.385 2.25 12 2.25zM6.262 6.072a8.25 8.25 0 1010.562-.766 4.5 4.5 0 01-1.318 1.357L14.25 7.5l.165.33a.809.809 0 01-1.086 1.085l-.604-.302a1.125 1.125 0 00-1.298.21l-.132.131c-.439.44-.439 1.152 0 1.591l.296.296c.256.257.622.374.98.314l1.17-.195c.323-.054.654.036.905.245l1.33 1.108c.32.267.46.694.358 1.1a8.7 8.7 0 01-2.288 4.04l-.723.724a1.125 1.125 0 01-1.298.21l-.153-.076a1.125 1.125 0 01-.622-1.006v-1.089c0-.298-.119-.585-.33-.796l-1.347-1.347a1.125 1.125 0 01-.21-1.298L9.75 12l-1.64-1.64a6 6 0 01-1.676-3.257l-.172-1.03z" clip-rule="evenodd" /></svg>`
    },
    'sse': {
        color: '#4ade80',
        icon: `<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 inline-block align-text-bottom" viewBox="0 0 24 24" fill="#4ade80"><path fill-rule="evenodd" d="M14.615 1.595a.75.75 0 01.359.852L12.982 9.75h7.268a.75.75 0 01.548 1.262l-10.5 11.25a.75.75 0 01-1.272-.71l1.992-7.302H3.75a.75.75 0 01-.548-1.262l10.5-11.25a.75.75 0 01.913-.143z" clip-rule="evenodd" /></svg>`
    }
};

// Helper function to render provider label with icon and color
function getProviderLabel(providerName) {
    const key = providerName.toLowerCase();
    const info = providerIcons[key];
    if (info) {
        return `${info.icon} <span style="color: ${info.color};">${escapeHtml(providerName)}</span>`;
    }
    return `<span class="text-gray-400">${escapeHtml(providerName)}</span>`;
}

// Helper function to render transport label with icon and color
function getTransportLabel(transport) {
    const key = transport.toLowerCase();
    const info = transportIcons[key];
    if (info) {
        return `${info.icon} <span style="color: ${info.color};">${escapeHtml(transport.toUpperCase())}</span>`;
    }
    return `<span class="text-gray-400">${escapeHtml(transport)}</span>`;
}

// Current MCP transport filter
let activeMCPTransportFilter = 'all';

// IFOC accent colors keyed by tab id — used to theme the shared profile filter bar.
const IFOC_ACCENT = {
    'conversation-profiles': { fg: '#4ade80', rgb: '74,222,128' },   // Ideate     — green
    'rag-profiles':          { fg: '#3b82f6', rgb: '59,130,246' },   // Focus      — blue
    'tool-profiles':         { fg: '#9333ea', rgb: '147,51,234' },   // Optimize   — purple
    'genie-profiles':        { fg: '#F15F22', rgb: '241,95,34' }     // Coordinate — orange
};

function applyIfocAccentToFilterBar(targetTab) {
    const bar = document.getElementById('profile-filter-bar');
    if (!bar) return;
    const c = IFOC_ACCENT[targetTab] || IFOC_ACCENT['conversation-profiles'];
    bar.style.setProperty('--ifoc-accent-fg', c.fg);
    bar.style.setProperty('--ifoc-accent-bg', `rgba(${c.rgb},0.12)`);
    bar.style.setProperty('--ifoc-accent-border', `rgba(${c.rgb},0.35)`);
}

function setupProfileTypeTabs() {
    const tabs = document.querySelectorAll('.profile-type-tab');
    const contents = document.querySelectorAll('.profile-type-content');

    // Apply accent for whichever tab is currently active on first run.
    const activeTab = document.querySelector('.profile-type-tab.active');
    if (activeTab) applyIfocAccentToFilterBar(activeTab.dataset.tab);

    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const targetTab = tab.dataset.tab;

            // Update tab styles
            tabs.forEach(t => {
                if (t === tab) {
                    t.classList.add('active');
                } else {
                    t.classList.remove('active');
                }
            });

            // Show/hide content
            contents.forEach(content => {
                content.classList.toggle('hidden', content.id !== targetTab + '-container');
            });

            // Show/hide description banners
            document.querySelectorAll('.profile-description').forEach(desc => {
                desc.classList.toggle('hidden', desc.id !== descriptionMap[targetTab]);
            });

            // Re-color the filter bar to match the IFOC tab.
            applyIfocAccentToFilterBar(targetTab);
        });
    });
}

function setupLLMProviderTabs() {
    const tabs = document.querySelectorAll('.llm-provider-tab');

    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const targetProvider = tab.dataset.provider;
            activeLLMProviderFilter = targetProvider;

            // Update tab styles with color-matched borders
            tabs.forEach(t => {
                if (t === tab) {
                    t.classList.remove('text-gray-400', 'border-transparent', 'hover:border-white/20');
                    t.classList.add('text-white');
                    t.style.borderColor = providerColors[t.dataset.provider] || '#6b7280';
                } else {
                    t.classList.remove('text-white');
                    t.classList.add('text-gray-400', 'border-transparent', 'hover:border-white/20');
                    t.style.borderColor = '';
                }
            });

            // Show/hide description banners
            document.querySelectorAll('.llm-provider-description').forEach(desc => {
                desc.classList.toggle('hidden', desc.id !== llmDescriptionMap[targetProvider]);
            });

            // Re-render filtered configurations
            renderLLMProviders();
        });
    });
}

function setupMCPTransportTabs() {
    const tabs = document.querySelectorAll('.mcp-transport-tab');

    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const targetTransport = tab.dataset.transport;
            activeMCPTransportFilter = targetTransport;

            // Update tab styles with color-matched borders
            tabs.forEach(t => {
                if (t === tab) {
                    t.classList.remove('text-gray-400', 'border-transparent', 'hover:border-white/20');
                    t.classList.add('text-white');
                    t.style.borderColor = mcpTransportColors[t.dataset.transport] || '#6b7280';
                } else {
                    t.classList.remove('text-white');
                    t.classList.add('text-gray-400', 'border-transparent', 'hover:border-white/20');
                    t.style.borderColor = '';
                }
            });

            // Show/hide description banners
            document.querySelectorAll('.mcp-transport-description').forEach(desc => {
                desc.classList.toggle('hidden', desc.id !== mcpDescriptionMap[targetTransport]);
            });

            // Re-render filtered servers
            renderMCPServers();
        });
    });
}

function attachProfileEventListeners() {
    // Set Default Profile button
    document.querySelectorAll('[data-action="set-default-profile"]').forEach(btn => {
        btn.replaceWith(btn.cloneNode(true)); // Remove old listeners
    });
    document.querySelectorAll('[data-action="set-default-profile"]').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const button = e.currentTarget;
            const profileId = button.dataset.profileId;
            const profile = configState.profiles.find(p => p.id === profileId);
            
            if (!profile) {
                showNotification('error', 'Profile not found');
                return;
            }
            
            // If already default, no need to do anything
            const isAlreadyDefault = profileId === configState.defaultProfileId;
            if (isAlreadyDefault) {
                return;
            }
            
            try {
                // Disable button during processing
                button.disabled = true;
                const originalHTML = button.innerHTML;
                button.innerHTML = '<svg class="animate-spin h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>';

                // Auto-test: If tests haven't passed yet, run them automatically
                let testStatus = configState.profileTestStatus[profileId];
                if (!testStatus || !testStatus.passed) {
                    showNotification('info', `Testing profile "${profile.name}" before activation...`);

                    // Update test results UI to show testing in progress
                    const resultsContainer = document.getElementById(`test-results-${profileId}`);
                    if (resultsContainer) {
                        resultsContainer.innerHTML = `<span class="text-yellow-400">Testing...</span>`;
                    }

                    // Run the test
                    const testResult = await API.testProfile(profileId);
                    const allSuccessful = Object.values(testResult.results).every(r => r.status === 'success' || r.status === 'info');

                    // Build results HTML
                    let html = '';
                    for (const [key, value] of Object.entries(testResult.results)) {
                        let statusClass;
                        if (value.status === 'success') {
                            statusClass = 'text-green-400';
                        } else if (value.status === 'info') {
                            statusClass = 'text-blue-400';
                        } else if (value.status === 'warning') {
                            statusClass = 'text-yellow-400';
                        } else {
                            statusClass = 'text-red-400';
                        }
                        html += `<p class="${statusClass}">${value.message}</p>`;
                    }

                    // Update test results display
                    if (resultsContainer) {
                        resultsContainer.innerHTML = html;
                    }

                    // Update test status in state
                    configState.profileTestStatus[profileId] = {
                        tested: true,
                        passed: allSuccessful,
                        timestamp: Date.now(),
                        results: html
                    };

                    // If test failed, abort activation
                    if (!allSuccessful) {
                        showNotification('error', `Profile test failed. Cannot activate "${profile.name}".`);
                        button.disabled = false;
                        button.innerHTML = originalHTML;
                        renderProfiles(); // Re-render to show test results
                        return;
                    }

                    showNotification('success', `Profile test passed for "${profile.name}". Activating...`);
                }
                
                // Step 1: If profile is also the master classification profile for its MCP server AND has inherit_classification enabled, disable it
                // (Master classification profile cannot inherit - circular dependency)
                const mcpServerId = profile.mcpServerId;
                const isMasterClassification = mcpServerId && configState.masterClassificationProfileIds[mcpServerId] === profileId;
                if (isMasterClassification && profile.inherit_classification) {
                    showNotification('info', 'Disabling classification inheritance (primary classification profile cannot inherit from itself)...');
                    await configState.updateProfile(profileId, {
                        inherit_classification: false
                    });
                    await configState.loadProfiles();
                }
                // Note: If profile is NOT the master, it can remain with inherit_classification enabled
                // and will inherit from the master classification profile
                
                // Step 2: Set as default
                await configState.setDefaultProfile(profileId);
                
                // Step 3: Auto-activate the default profile if it's not already active
                if (!configState.activeForConsumptionProfileIds.includes(profileId)) {
                    const activeIds = [...configState.activeForConsumptionProfileIds, profileId];
                    await configState.setActiveForConsumptionProfiles(activeIds);
                }
                
                // Step 4: Check if classification is needed based on profile type
                const updatedProfile = configState.profiles.find(p => p.id === profileId);
                const profileType = updatedProfile?.profile_type || 'tool_enabled';
                const useMcpTools = updatedProfile?.useMcpTools || false;

                // Determine if this profile type needs MCP classification
                const needsClassification =
                    profileType === 'tool_enabled' ||
                    (profileType === 'llm_only' && useMcpTools);

                // Step 5: Only trigger reclassification for profiles that need it
                if (needsClassification) {
                    const classificationResults = updatedProfile?.classification_results || {};
                    const toolsDict = classificationResults.tools || {};
                    const promptsDict = classificationResults.prompts || {};
                    const totalTools = Object.values(toolsDict).reduce((sum, tools) => sum + tools.length, 0);
                    const totalPrompts = Object.values(promptsDict).reduce((sum, prompts) => sum + prompts.length, 0);
                    const hasClassification = totalTools > 0 || totalPrompts > 0;

                    if (!hasClassification) {
                        showNotification('info', `Running classification for default profile "${profile.name}"...`);

                        const headers = { 'Content-Type': 'application/json' };
                        const authToken = localStorage.getItem('tda_auth_token');
                        if (authToken) {
                            headers['Authorization'] = `Bearer ${authToken}`;
                        }

                        const response = await fetch(`/api/v1/profiles/${profileId}/reclassify`, {
                            method: 'POST',
                            headers: headers,
                            credentials: 'include'
                        });

                        const result = await response.json();

                        if (response.ok && result.status === 'success') {
                            showNotification('success', `Default profile "${profile.name}" classified successfully`);
                        } else {
                            showNotification('warning', `Default profile set, but classification failed: ${result.message || 'Unknown error'}`);
                        }
                    } else {
                        showNotification('success', `Default profile changed to "${profile.name}"`);
                    }
                } else {
                    // Profile doesn't need classification (genie, rag_focused, or pure llm_only)
                    showNotification('success', `Default profile changed to "${profile.name}"`);
                }
                
                // Step 6: Reload and re-render
                await configState.loadProfiles();
                renderProfiles();
                renderLLMProviders(); // Re-render to update default/active badges
                renderMCPServers(); // Re-render to update default/active badges
                updateReconnectButton(); // Update Reconnect & Load button state
                
                // Restore button
                button.disabled = false;
                button.innerHTML = originalHTML;
                
            } catch (error) {
                console.error('Set default profile error:', error);
                showNotification('error', `Failed to set default profile: ${error.message}`);
                
                // Restore button
                button.disabled = false;
                const originalHTML = '★';
                button.innerHTML = originalHTML;
            }
        });
    });

    // Toggle Active for Consumption checkbox
    document.querySelectorAll('[data-action="toggle-active-consumption"]').forEach(checkbox => {
        checkbox.addEventListener('change', async (e) => {
            const profileId = e.target.dataset.profileId;
            const isChecked = e.target.checked;
            
            // Prevent deactivating the default profile
            if (!isChecked && profileId === configState.defaultProfileId) {
                e.target.checked = true; // Revert the checkbox
                showNotification('error', 'Cannot deactivate the default profile. Set a different profile as default first.');
                return;
            }
            
            let activeIds = configState.activeForConsumptionProfileIds;

            if (isChecked) {
                // Get profile to check type
                const profile = configState.profiles.find(p => p.id === profileId);
                // Check if profile doesn't need MCP classification (llm_only, rag_focused, genie)
                const skipMcpClassification = profile && ['llm_only', 'rag_focused', 'genie'].includes(profile.profile_type);

                // Test the profile first before any other checks
                const resultsContainer = document.getElementById(`test-results-${profileId}`);
                if (resultsContainer) {
                    // Show different message based on profile type
                    const testingMessage = skipMcpClassification
                        ? 'Testing profile...'
                        : 'Testing and classifying profile...';

                    resultsContainer.innerHTML = `
                        <div class="flex items-center gap-2">
                            <svg class="animate-spin h-4 w-4 text-yellow-400" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                            </svg>
                            <span class="text-yellow-400">${testingMessage}</span>
                        </div>
                    `;
                }
                
                try {
                    const result = await API.testProfile(profileId);
                    const allSuccessful = Object.values(result.results).every(r => r.status === 'success' || r.status === 'info');
                    
                    let testResultsHTML = '';
                    for (const [key, value] of Object.entries(result.results)) {
                        let statusClass;
                        if (value.status === 'success') {
                            statusClass = 'text-green-400';
                        } else if (value.status === 'info') {
                            statusClass = 'text-blue-400';
                        } else if (value.status === 'warning') {
                            statusClass = 'text-yellow-400';
                        } else {
                            statusClass = 'text-red-400';
                        }
                        testResultsHTML += `<p class="${statusClass}">${value.message}</p>`;
                    }
                    
                    if (!allSuccessful) {
                        // Tests failed - revert checkbox and show results
                        e.target.checked = false;
                        if (resultsContainer) {
                            resultsContainer.innerHTML = testResultsHTML;
                        }
                        showNotification('error', 'Profile tests failed. Cannot activate profile.');
                        return;
                    }
                    
                    // Tests passed - update test status and activate the profile
                    configState.profileTestStatus[profileId] = {
                        tested: true,
                        passed: true,
                        timestamp: Date.now(),
                        results: testResultsHTML
                    };

                    if (!activeIds.includes(profileId)) {
                        activeIds.push(profileId);
                    }
                    await configState.setActiveForConsumptionProfiles(activeIds);

                    // If no default profile exists, set this profile as default
                    const wasSetAsDefault = !configState.defaultProfileId;
                    if (wasSetAsDefault) {
                        await configState.setDefaultProfile(profileId);
                    }

                    // Check if profile is tool-enabled (reuse profile variable from above)
                    const isToolEnabled = profile && profile.profile_type === 'tool_enabled';

                    // If tool-enabled and no master classification profile exists for this MCP server, set as master
                    let wasSetAsMaster = false;
                    const profileMcpServerId = profile.mcpServerId;
                    const hasServerMaster = profileMcpServerId && configState.masterClassificationProfileIds[profileMcpServerId];

                    if (isToolEnabled && profileMcpServerId && !hasServerMaster) {
                        try {
                            await API.setMasterClassificationProfile(profileId);
                            // Update per-server master
                            configState.masterClassificationProfileIds[profileMcpServerId] = profileId;
                            // Also update legacy single master for backwards compatibility
                            configState.masterClassificationProfileId = profileId;
                            wasSetAsMaster = true;
                            showNotification('info', `Profile "${profile.name}" set as primary classification profile for this MCP server.`);
                        } catch (masterError) {
                            console.error('Failed to set master classification profile:', masterError);
                        }
                    }

                    // Update the profile object's active_for_consumption property
                    const profileIndex = configState.profiles.findIndex(p => p.id === profileId);
                    if (profileIndex !== -1) {
                        configState.profiles[profileIndex].active_for_consumption = true;
                    }

                    await configState.loadProfiles(); // Reload to sync state

                    // If profile became master classification profile, trigger auto-classification
                    let classificationTriggered = false;
                    if (wasSetAsMaster) {
                        showNotification('info', `Running auto-classification for master profile "${profile.name}"...`);

                        // Disable Save & Connect button during classification
                        const reconnectBtn = document.getElementById('reconnect-and-load-btn');
                        const reconnectBtnText = document.getElementById('reconnect-button-text');
                        const originalBtnText = reconnectBtnText ? reconnectBtnText.textContent : 'Save & Connect';
                        if (reconnectBtn) {
                            reconnectBtn.disabled = true;
                            reconnectBtn.classList.add('opacity-50', 'cursor-not-allowed');
                            if (reconnectBtnText) {
                                reconnectBtnText.textContent = 'Classifying...';
                            }
                        }

                        try {
                            const headers = { 'Content-Type': 'application/json' };
                            const authToken = localStorage.getItem('tda_auth_token');
                            if (authToken) {
                                headers['Authorization'] = `Bearer ${authToken}`;
                            }

                            const classifyResponse = await fetch(`/api/v1/profiles/${profileId}/reclassify`, {
                                method: 'POST',
                                headers: headers,
                                credentials: 'include'
                            });

                            const classifyResult = await classifyResponse.json();

                            if (classifyResponse.ok && classifyResult.status === 'success') {
                                showNotification('success', `Master profile "${profile.name}" classified successfully.`);
                                classificationTriggered = true;
                            } else {
                                showNotification('warning', `Master profile set, but classification failed: ${classifyResult.message || 'Unknown error'}`);
                            }
                        } catch (classifyError) {
                            console.error('Auto-classification error:', classifyError);
                            showNotification('warning', `Master profile set, but auto-classification failed: ${classifyError.message}`);
                        } finally {
                            // Restore Save & Connect button text and update state
                            if (reconnectBtnText) {
                                reconnectBtnText.textContent = originalBtnText;
                            }
                            updateReconnectButton(); // Let the function determine proper state
                        }

                        // Reload profiles to get updated classification results
                        await configState.loadProfiles();
                    }

                    renderProfiles(); // Re-render with updated state (reclassify button will be enabled)
                    renderLLMProviders(); // Re-render to update default/active badges
                    renderMCPServers(); // Re-render to update default/active badges
                    updateReconnectButton(); // Update Save & Connect button state

                    // Update status indicators after activation
                    DOM.mcpStatusDot.classList.remove('disconnected');
                    DOM.mcpStatusDot.classList.add('connected');
                    DOM.llmStatusDot.classList.remove('disconnected', 'busy');
                    DOM.llmStatusDot.classList.add('idle');

                    // Update CCR (Champion Case Retrieval) indicator
                    if (DOM.ccrStatusDot) {
                        const status = await API.checkServerStatus();
                        if (status.rag_active) {
                            DOM.ccrStatusDot.classList.remove('disconnected');
                            DOM.ccrStatusDot.classList.add('connected');
                        } else {
                            DOM.ccrStatusDot.classList.remove('connected');
                            DOM.ccrStatusDot.classList.add('disconnected');
                        }
                    }

                    // Build notification message based on what happened
                    let message;
                    if (wasSetAsDefault && wasSetAsMaster && classificationTriggered) {
                        message = `Profile "${profile ? profile.name : 'Profile'}" activated, set as default and primary classification profile, and classified successfully.`;
                    } else if (wasSetAsDefault && wasSetAsMaster) {
                        message = `Profile "${profile ? profile.name : 'Profile'}" activated and set as default and primary classification profile.`;
                    } else if (wasSetAsDefault) {
                        message = `Profile "${profile ? profile.name : 'Profile'}" activated and set as default. Click "Reclassify" to classify tools and prompts.`;
                    } else {
                        message = `Profile "${profile ? profile.name : 'Profile'}" activated successfully. Click "Reclassify" to classify tools and prompts.`;
                    }
                    showNotification('success', message);
                    
                    // Re-apply test results after render
                    const newResultsContainer = document.getElementById(`test-results-${profileId}`);
                    if (newResultsContainer) {
                        newResultsContainer.innerHTML = testResultsHTML;
                    }
                } catch (error) {
                    // Test error - revert checkbox
                    e.target.checked = false;
                    if (resultsContainer) {
                        resultsContainer.innerHTML = `<span class="text-red-400">Test failed: ${error.message}</span>`;
                    }
                    showNotification('error', `Failed to test profile: ${error.message}`);
                }
            } else {
                // Deactivating - no test needed
                activeIds = activeIds.filter(id => id !== profileId);
                await configState.setActiveForConsumptionProfiles(activeIds);
                await configState.loadProfiles(); // Reload to update active_for_consumption flags
                renderProfiles(); // Re-render with updated state (reclassify button will be disabled)
                renderLLMProviders(); // Re-render to update default/active badges
                renderMCPServers(); // Re-render to update default/active badges
                showNotification('success', 'Profile deactivated successfully');
            }
        });
    });
    
    // Test Profile button
    document.querySelectorAll('[data-action="test-profile"]').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const button = e.currentTarget;
            const profileId = button.dataset.profileId;
            const resultsContainer = document.getElementById(`test-results-${profileId}`);
            if (!resultsContainer) {
                console.error(`Test results container not found for profile ${profileId}`);
                return;
            }
            resultsContainer.innerHTML = `<span class="text-yellow-400">Testing...</span>`;
            try {
                const result = await API.testProfile(profileId);
                let html = '';
                const all_successful = Object.values(result.results).every(r => r.status === 'success' || r.status === 'info');

                for (const [key, value] of Object.entries(result.results)) {
                    let statusClass;
                    if (value.status === 'success') {
                        statusClass = 'text-green-400';
                    } else if (value.status === 'info') {
                        statusClass = 'text-blue-400';
                    } else if (value.status === 'warning') {
                        statusClass = 'text-yellow-400';
                    } else {
                        statusClass = 'text-red-400';
                    }
                    html += `<p class="${statusClass}">${value.message}</p>`;
                }
                resultsContainer.innerHTML = html;

                // Update test status
                configState.profileTestStatus[profileId] = {
                    tested: true,
                    passed: all_successful,
                    timestamp: Date.now(),
                    results: html  // Store the formatted results
                };
                
                // Re-render profiles to update activation toggle state and star button
                renderProfiles();
                
                // Restore test results after re-render
                const newResultsContainer = document.getElementById(`test-results-${profileId}`);
                if (newResultsContainer) {
                    newResultsContainer.innerHTML = html;
                }
                
                if (all_successful) {
                    showNotification('success', 'Profile test passed! You can now set it as default or activate it.');
                } else {
                    showNotification('error', 'Profile test failed. Please fix the issues before activating.');
                }

                // Just display results - don't auto-activate the profile
                // User can manually activate via the toggle switch if tests pass

            } catch (error) {
                resultsContainer.innerHTML = `<span class="text-red-400">${error.message}</span>`;
                // Also uncheck on API error
                let activeIds = configState.activeForConsumptionProfileIds.filter(id => id !== profileId);
                await configState.setActiveForConsumptionProfiles(activeIds);
                const checkbox = document.querySelector(`input[data-action="toggle-active-consumption"][data-profile-id="${profileId}"]`);
                if (checkbox) {
                    checkbox.checked = false;
                }
            }
        });
    });

    // Edit Profile button
    document.querySelectorAll('[data-action="edit-profile"]').forEach(btn => {
        btn.replaceWith(btn.cloneNode(true)); // Remove old listeners
    });
    document.querySelectorAll('[data-action="edit-profile"]').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const profileId = e.currentTarget.dataset.profileId;
            showProfileModal(profileId);
        });
    });

    // Anatomy (the agent-as-organism view) — delegates to the anatomy panel.
    document.querySelectorAll('[data-action="open-anatomy"]').forEach(btn => {
        btn.replaceWith(btn.cloneNode(true));
    });
    document.querySelectorAll('[data-action="open-anatomy"]').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const profileId = e.currentTarget.dataset.profileId;
            if (window.AgentAnatomy && window.AgentAnatomy.open) window.AgentAnatomy.open(profileId);
        });
    });

    // Duplicate Profile button
    document.querySelectorAll('[data-action="copy-profile"]').forEach(btn => {
        btn.replaceWith(btn.cloneNode(true)); // Remove old listeners
    });
    document.querySelectorAll('[data-action="copy-profile"]').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const profileId = e.currentTarget.dataset.profileId;
            const profile = configState.profiles.find(p => p.id === profileId);
            const profileName = profile ? profile.name : 'this profile';
            
            const copiedProfile = await configState.copyProfile(profileId);
            if (copiedProfile) {
                renderProfiles();
                renderLLMProviders(); // Re-render to update default/active badges
                renderMCPServers(); // Re-render to update default/active badges
                showNotification('success', `Profile "${profileName}" copied successfully. Please edit to set a unique tag.`);
            } else {
                showNotification('error', 'Failed to copy profile');
            }
        });
    });

    // Delete Profile button
    document.querySelectorAll('[data-action="delete-profile"]').forEach(btn => {
        btn.replaceWith(btn.cloneNode(true)); // Remove old listeners
    });
    document.querySelectorAll('[data-action="delete-profile"]').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const profileId = e.currentTarget.dataset.profileId;
            const profile = configState.profiles.find(p => p.id === profileId);
            const profileName = profile ? profile.name : 'this profile';

            // Only allow deleting the default profile if it's the last profile
            const isDefault = profileId === configState.defaultProfileId;
            const isLastProfile = configState.profiles.length === 1;

            if (isDefault && !isLastProfile) {
                showNotification('error', 'Cannot delete the default profile while other profiles exist. Please change the default profile first.');
                return;
            }

            // Function to perform the actual deletion
            const doDelete = async () => {
                let result;
                try {
                    result = await configState.removeProfile(profileId);
                } catch (deleteErr) {
                    showNotification('error', deleteErr.message || 'Failed to delete profile');
                    return;
                }
                renderProfiles();
                renderLLMProviders(); // Re-render to update default/active badges
                renderMCPServers(); // Re-render to update default/active badges

                // Show success message with archive information
                const archivedCount = result.sessions_archived || 0;
                let message = 'Profile deleted successfully';

                if (archivedCount > 0) {
                    message += `\n\n${archivedCount} session(s) using this profile have been archived.`;
                    if (result.genie_children_archived > 0) {
                        message += `\n(${result.genie_children_archived} Genie child sessions included)`;
                    }
                    message += `\n\nArchived sessions can be viewed in the Sessions panel by enabling "Show Archived".`;
                }

                showNotification('success', message);

                // Refresh sessions list if sessions were archived
                if (archivedCount > 0) {
                    try {
                        // Auto-disable toggle (user deleted artifact = cleanup intent)
                        const toggle = document.getElementById('sidebar-show-archived-sessions-toggle');
                        if (toggle && toggle.checked) {
                            toggle.checked = false;
                            localStorage.setItem('sidebarShowArchivedSessions', 'false');
                            console.log('[Config Delete] Auto-disabled "Show Archived" toggle');
                        }

                        // Full refresh: fetch + re-render + apply filters
                        const { refreshSessionsList } = await import('./configManagement.js?v=1.189');
                        await refreshSessionsList();

                        console.log('[Config Delete] Session list refreshed after archiving', archivedCount, 'sessions');
                    } catch (error) {
                        console.error('[Config Delete] Failed to refresh sessions:', error);
                        // Non-fatal: profile/MCP/LLM deleted successfully, just UI refresh failed
                    }
                }
            };

            // Check for active sessions before showing confirmation
            try {
                const token = localStorage.getItem('tda_auth_token');
                // MIGRATED: Use unified relationships endpoint instead of old check-sessions
                const checkRes = await fetch(`/api/v1/artifacts/profile/${profileId}/relationships`, {
                    headers: {
                        'Authorization': `Bearer ${token}`
                    }
                });
                const checkData = await checkRes.json();

                let message = `Are you sure you want to delete profile <strong>${escapeHtml(profileName)}</strong>?`;

                // Extract data from unified endpoint response structure
                const deletionInfo = checkData.deletion_info || {};
                const blockers = deletionInfo.blockers || [];
                const warnings = deletionInfo.warnings || [];
                const sessions = checkData.relationships?.sessions || {};
                const activeCount = sessions.active_count || 0;

                // Show blockers (prevent deletion)
                if (blockers.length > 0) {
                    const blockerMessages = blockers
                        .map(b => `• ${escapeHtml(b.message || b)}`)
                        .join('<br>');
                    message += `<br><br><span style="color: #ef4444; font-weight: 600;">🚫 Cannot Delete:</span><br><span style="font-size: 0.9em;">${blockerMessages}</span>`;

                    window.showConfirmation('Cannot Delete Profile', message, null);
                    return;
                }

                // Add dynamic warning if active sessions exist
                if (activeCount > 0) {
                    const sessionWord = activeCount === 1 ? 'session' : 'sessions';
                    message += `<br><br><span style="color: #f59e0b; font-weight: 600;">⚠️ Warning: ${activeCount} active ${sessionWord} will be archived.</span>`;

                    // Show sample session names
                    if (sessions.items && sessions.items.length > 0) {
                        const activeSessions = sessions.items.filter(s => !s.is_archived);
                        if (activeSessions.length > 0) {
                            const sessionNames = activeSessions
                                .map(s => `• ${escapeHtml(s.session_name)}`)
                                .join('<br>');
                            message += `<br><br><span style="font-size: 0.9em;">Affected sessions:<br>${sessionNames}</span>`;

                            if (activeCount > activeSessions.length) {
                                message += `<br><span style="font-size: 0.9em; color: #9ca3af;">...and ${activeCount - activeSessions.length} more</span>`;
                            }
                        }
                    }
                }

                // Show additional warnings from deletion analysis
                if (warnings.length > 0) {
                    const warningMessages = warnings
                        .filter(w => !w.toLowerCase().includes('session'))  // Skip session warnings (already shown above)
                        .map(w => `• ${escapeHtml(w)}`)
                        .join('<br>');
                    if (warningMessages) {
                        message += `<br><br><span style="color: #f59e0b; font-weight: 600;">⚠️ Additional Warnings:</span><br><span style="font-size: 0.9em;">${warningMessages}</span>`;
                    }
                }

                // Use window.showConfirmation for HTML support
                window.showConfirmation('Delete Profile', message, doDelete);
            } catch (err) {
                // Fallback to basic confirmation if check fails
                console.error('Failed to check active sessions:', err);
                window.showConfirmation(
                    'Delete Profile',
                    `Are you sure you want to delete profile <strong>${escapeHtml(profileName)}</strong>?`,
                    doDelete
                );
            }
        });
    });
    
    // Reclassify Profile button
    document.querySelectorAll('[data-action="inherit-classification"]').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const button = e.currentTarget;
            const profileId = button.dataset.profileId;
            const profile = configState.profiles.find(p => p.id === profileId);

            if (button.disabled) {
                return;
            }

            // Prevent master classification profile from inheriting
            const mcpServerId = profile.mcpServerId;
            const isMasterForThisServer = mcpServerId && configState.masterClassificationProfileIds[mcpServerId] === profileId;

            if (isMasterForThisServer) {
                showNotification('error', 'Primary classification profile cannot inherit classification (it is the source for other profiles)');
                return;
            }

            // Toggle the inherit_classification flag
            const newInheritState = !profile.inherit_classification;

            try {
                // Update the profile with the new inherit_classification state
                await configState.updateProfile(profileId, {
                    inherit_classification: newInheritState
                });

                // Reload profiles to get updated state
                await configState.loadProfiles();
                renderProfiles();

                // Get the per-server master profile name
                const masterProfileId = mcpServerId ? configState.masterClassificationProfileIds[mcpServerId] : null;
                const masterProfile = masterProfileId ? configState.profiles.find(p => p.id === masterProfileId) : null;
                const masterProfileName = masterProfile ? masterProfile.name : 'primary classification profile';

                const message = newInheritState
                    ? `Profile "${profile.name}" will now inherit classification from ${masterProfileName}`
                    : `Profile "${profile.name}" will use its own classification`;
                showNotification('success', message);
            } catch (error) {
                console.error('Inherit classification toggle error:', error);
                showNotification('error', 'Failed to update classification inheritance');
            }
        });
    });

    // Set Master Classification Profile button
    document.querySelectorAll('[data-action="set-master-classification"]').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const button = e.currentTarget;
            const profileId = button.dataset.profileId;
            const profile = configState.profiles.find(p => p.id === profileId);

            if (button.disabled) {
                return;
            }

            // Check if this profile is already the master for its MCP server
            const mcpServerId = profile.mcpServerId;
            if (mcpServerId && configState.masterClassificationProfileIds[mcpServerId] === profileId) {
                showNotification('info', `Profile "${profile.name}" is already the primary classification profile for this MCP server`);
                return;
            }

            // Disable Save & Connect button during the process
            const reconnectBtn = document.getElementById('reconnect-and-load-btn');
            const reconnectBtnText = document.getElementById('reconnect-button-text');
            const originalReconnectText = reconnectBtnText ? reconnectBtnText.textContent : 'Save & Connect';
            if (reconnectBtn) {
                reconnectBtn.disabled = true;
                reconnectBtn.classList.add('opacity-50', 'cursor-not-allowed');
                if (reconnectBtnText) {
                    reconnectBtnText.textContent = 'Setting Master...';
                }
            }

            try {
                // Disable button and show loading state
                button.disabled = true;
                const originalText = button.textContent;
                button.textContent = 'Setting...';

                // Call API to set master classification profile
                await API.setMasterClassificationProfile(profileId);

                // Update per-server master in configState
                if (mcpServerId) {
                    configState.masterClassificationProfileIds[mcpServerId] = profileId;
                }
                // Also update legacy single master for backwards compatibility
                configState.masterClassificationProfileId = profileId;

                showNotification('success', `Profile "${profile.name}" is now the primary classification profile for this MCP server`);

                // Trigger auto-classification for the new master profile
                showNotification('info', `Running auto-classification for master profile "${profile.name}"...`);

                if (reconnectBtn && reconnectBtnText) {
                    reconnectBtnText.textContent = 'Classifying...';
                }

                try {
                    const headers = { 'Content-Type': 'application/json' };
                    const authToken = localStorage.getItem('tda_auth_token');
                    if (authToken) {
                        headers['Authorization'] = `Bearer ${authToken}`;
                    }

                    const classifyResponse = await fetch(`/api/v1/profiles/${profileId}/reclassify`, {
                        method: 'POST',
                        headers: headers,
                        credentials: 'include'
                    });

                    const classifyResult = await classifyResponse.json();

                    if (classifyResponse.ok && classifyResult.status === 'success') {
                        showNotification('success', `Master profile "${profile.name}" classified successfully.`);
                    } else {
                        showNotification('warning', `Master profile set, but classification failed: ${classifyResult.message || 'Unknown error'}`);
                    }
                } catch (classifyError) {
                    console.error('Auto-classification error:', classifyError);
                    showNotification('warning', `Master profile set, but auto-classification failed: ${classifyError.message}`);
                }

                // Reload profiles to get updated state
                await configState.loadProfiles();
                renderProfiles();

                // Restore button state
                button.disabled = false;
                button.textContent = originalText;
            } catch (error) {
                console.error('Set master classification profile error:', error);
                showNotification('error', `Failed to set primary classification profile: ${error.message}`);

                // Restore button state
                button.disabled = false;
                button.textContent = 'Set as Master Classification';
            } finally {
                // Restore Save & Connect button text and update state
                if (reconnectBtnText) {
                    reconnectBtnText.textContent = originalReconnectText;
                }
                updateReconnectButton(); // Let the function determine proper state
            }
        });
    });

    document.querySelectorAll('[data-action="reclassify-profile"]').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const button = e.currentTarget;
            const profileId = button.dataset.profileId;
            const profile = configState.profiles.find(p => p.id === profileId);
            const profileName = profile ? profile.name : 'this profile';
            
            // Button should be disabled if profile is not active (handled in rendering)
            if (button.disabled) {
                return;
            }
            
            try {
                // Disable button and show loading state
                button.disabled = true;
                button.textContent = 'Reclassifying...';
                
                // Show classification progress in the test results area
                const resultsContainer = document.getElementById(`test-results-${profileId}`);
                if (resultsContainer) {
                    resultsContainer.innerHTML = `
                        <div class="flex items-center gap-2">
                            <svg class="animate-spin h-4 w-4 text-orange-400" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                            </svg>
                            <span class="text-orange-400">Reclassifying profile...</span>
                        </div>
                    `;
                }
                
                showNotification('info', `Reclassifying "${profileName}"... This will clear cached results and reclassify all resources.`);
                
                const headers = { 'Content-Type': 'application/json' };
                const authToken = localStorage.getItem('tda_auth_token');
                if (authToken) {
                    headers['Authorization'] = `Bearer ${authToken}`;
                }
                
                const response = await fetch(`/api/v1/profiles/${profileId}/reclassify`, {
                    method: 'POST',
                    headers: headers,
                    credentials: 'include'  // Include session cookies for authentication
                });
                
                const result = await response.json();
                
                if (response.ok && result.status === 'success') {
                    showNotification('success', result.message || 'Profile reclassified successfully');
                    
                    // Show success in test results area
                    const resultsContainer = document.getElementById(`test-results-${profileId}`);
                    if (resultsContainer) {
                        resultsContainer.innerHTML = `<p class="text-green-400">✓ Reclassification completed successfully</p>`;
                    }
                    
                    // Refresh profile list to update needs_reclassification flag
                    await configState.loadProfiles();
                    renderProfiles();
                } else {
                    showNotification('error', result.message || 'Failed to reclassify profile');
                    
                    // Show error in test results area
                    const resultsContainer = document.getElementById(`test-results-${profileId}`);
                    if (resultsContainer) {
                        resultsContainer.innerHTML = `<p class="text-red-400">✗ Reclassification failed</p>`;
                    }
                }
            } catch (error) {
                console.error('Reclassify error:', error);
                showNotification('error', 'Failed to reclassify profile');
                
                // Show error in test results area
                const resultsContainer = document.getElementById(`test-results-${profileId}`);
                if (resultsContainer) {
                    resultsContainer.innerHTML = `<p class="text-red-400">✗ Reclassification failed: ${error.message}</p>`;
                }
            } finally {
                // Re-enable button
                button.disabled = false;
                button.textContent = 'Reclassify';
            }
        });
    });

    document.querySelectorAll('[data-action="show-classification"]').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const profileId = e.currentTarget.dataset.profileId;
            const profile = configState.profiles.find(p => p.id === profileId);
            
            if (!profile) {
                showNotification('error', 'Profile not found');
                return;
            }
            
            try {
                // Fetch fresh classification results from API (with authentication)
                const result = await API.getProfileClassification(profileId);
                
                if (result.status === 'success') {
                    // Update profile with fresh classification results.
                    // system_tools: always-active platform tools (TDA_*) returned by the API —
                    // these are never in profile.tools but must always render as active.
                    const profileWithResults = {
                        ...profile,
                        classification_results: result.classification_results,
                        classification_mode: result.classification_mode,
                        system_tools: new Set(result.system_tools || []),
                        component_tools: result.component_tools || [],
                        platform_tools: result.platform_tools || [],
                    };
                    showClassificationModal(profileWithResults);
                } else {
                    showNotification('error', result.message || 'Failed to load classification results');
                }
            } catch (error) {
                console.error('Show classification error:', error);
                showNotification('error', 'Failed to load classification results');
            }
        });
    });

    // Copy Profile ID button
    document.querySelectorAll('[data-action="copy-profile-id"]').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.preventDefault();
            e.stopPropagation();

            const button = e.target.closest('[data-action="copy-profile-id"]');
            const profileId = button.dataset.profileId;
            const profile = configState.profiles.find(p => p.id === profileId);

            try {
                await navigator.clipboard.writeText(profileId);

                // Show visual feedback - find the tooltip span
                const tooltipSpan = button.querySelector('span');
                if (tooltipSpan) {
                    tooltipSpan.style.opacity = '1';
                    setTimeout(() => {
                        tooltipSpan.style.opacity = '0';
                    }, 1500);
                }

                // Show notification
                showNotification('success', `Profile ID copied: ${profileId}`);
            } catch (err) {
                console.error('Failed to copy profile ID:', err);
                showNotification('error', 'Failed to copy profile ID to clipboard');
            }
        });
    });

    // Toggle Classification Dropdown Menu
    document.querySelectorAll('[data-action="toggle-classification-menu"]').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const profileId = btn.dataset.profileId;
            const menu = document.querySelector(`.classification-menu[data-profile-id="${profileId}"]`);

            if (menu) {
                // Close all other menus first
                document.querySelectorAll('.classification-menu, .profile-menu').forEach(m => {
                    if (m !== menu) m.classList.add('hidden');
                });

                // Toggle this menu
                menu.classList.toggle('hidden');
            }
        });
    });

    // Toggle Profile Overflow Menu
    document.querySelectorAll('[data-action="toggle-profile-menu"]').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const profileId = btn.dataset.profileId;
            const menu = document.querySelector(`.profile-menu[data-profile-id="${profileId}"]`);

            if (menu) {
                // Close all other menus first
                document.querySelectorAll('.classification-menu, .profile-menu').forEach(m => {
                    if (m !== menu) m.classList.add('hidden');
                });

                // Toggle this menu
                menu.classList.toggle('hidden');
            }
        });
    });

    // Close dropdown menus when clicking menu items (they will trigger their own actions)
    document.querySelectorAll('.classification-menu button, .profile-menu button').forEach(menuItem => {
        menuItem.addEventListener('click', (e) => {
            // Find parent menu and close it
            const menu = e.target.closest('.classification-menu, .profile-menu');
            if (menu) {
                menu.classList.add('hidden');
            }
        });
    });

    // Click outside to close menus
    document.addEventListener('click', (e) => {
        // If click is not on a dropdown button or menu, close all menus
        if (!e.target.closest('[data-action="toggle-classification-menu"]') &&
            !e.target.closest('[data-action="toggle-profile-menu"]') &&
            !e.target.closest('.classification-menu') &&
            !e.target.closest('.profile-menu')) {
            document.querySelectorAll('.classification-menu, .profile-menu').forEach(menu => {
                menu.classList.add('hidden');
            });
        }
    });
}

function showClassificationModal(profile) {
    const modal = document.getElementById('classification-modal');
    if (!modal) return;

    const title = modal.querySelector('#classification-modal-title');
    const subtitle = modal.querySelector('#classification-modal-subtitle');
    const content = modal.querySelector('#classification-modal-content');
    
    // Set title and subtitle
    title.textContent = `Classification Results - ${profile.name || profile.tag}`;
    subtitle.textContent = `Mode: ${profile.classification_mode || 'light'} | Profile: @${profile.tag}`;
    
    // Get classification results
    const results = profile.classification_results;
    if (!results) {
        content.innerHTML = '<p class="text-gray-400">No classification results available.</p>';
        modal.classList.remove('hidden');
        return;
    }
    
    // Helper function to check if a tool/prompt is active in the profile
    const isActive = (name, type) => {
        // System tools (TDA_*) are always-active platform tools, never in profile.tools.
        // Mirror the same override logic that the Resource Panel applies server-side.
        if (type === 'tool' && profile.system_tools && profile.system_tools.has(name)) return true;
        const list = type === 'tool' ? profile.tools : profile.prompts;
        if (!list || list.length === 0) return false;
        if (list.includes('*')) return true;
        return list.includes(name);
    };
    
    // Build HTML for tools and prompts
    let html = '';
    
    // MCP Server Tools section
    if (results.tools && Object.keys(results.tools).length > 0) {
        const totalTools = Object.values(results.tools).reduce((sum, arr) => sum + (Array.isArray(arr) ? arr.length : 0), 0);
        html += `
            <div class="space-y-4">
                <div class="flex items-center justify-between">
                    <h4 class="text-base font-semibold" style="color: var(--text-primary);">MCP Server Tools</h4>
                    <span class="text-sm" style="color: var(--text-muted);">${Object.keys(results.tools).length} categories • ${totalTools} total</span>
                </div>
                <div class="space-y-3">
        `;
        
        for (const [category, tools] of Object.entries(results.tools).sort()) {
            const toolList = Array.isArray(tools) ? tools : [];
            const categoryColor = getCategoryColor(category);
            const activeCount = toolList.filter(t => {
                const name = typeof t === 'string' ? t : (t.name || '');
                return isActive(name, 'tool');
            }).length;
            
            html += `
                <div class="rounded-lg p-4" style="background: var(--card-bg); border: 1px solid var(--border-secondary);">
                    <div class="flex items-center justify-between mb-3">
                        <h5 class="text-sm font-medium" style="color: ${categoryColor};">
                            <span class="inline-block w-2 h-2 rounded-full mr-2" style="background: ${categoryColor};"></span>
                            ${escapeHtml(category)}
                        </h5>
                        <span class="text-xs" style="color: var(--text-muted);">${activeCount}/${toolList.length} active</span>
                    </div>
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
            `;

            toolList.forEach(tool => {
                const toolName = typeof tool === 'string' ? tool : (tool.name || 'Unknown');
                const active = isActive(toolName, 'tool');
                html += `
                    <div class="text-sm font-mono px-3 py-1.5 rounded" style="${active ? 'color: var(--text-primary); background: rgba(255,255,255,0.04); border: 1px solid var(--border-secondary);' : 'color: var(--text-muted); background: transparent; border: 1px solid var(--border-secondary); opacity: 0.5; text-decoration: line-through;'}">
                        ${escapeHtml(toolName)}
                        ${!active ? '<span class="text-xs ml-2" style="color: #f87171;">(deactivated)</span>' : ''}
                    </div>
                `;
            });
            
            html += `
                    </div>
                </div>
            `;
        }
        
        html += `
                </div>
            </div>
        `;
    }
    
    // MCP Server Prompts section
    if (results.prompts && Object.keys(results.prompts).length > 0) {
        const totalPrompts = Object.values(results.prompts).reduce((sum, arr) => sum + (Array.isArray(arr) ? arr.length : 0), 0);
        html += `
            <div class="space-y-4 border-t border-gray-700/50 pt-6">
                <div class="flex items-center justify-between">
                    <h4 class="text-base font-semibold" style="color: var(--text-primary);">MCP Server Prompts</h4>
                    <span class="text-sm" style="color: var(--text-muted);">${Object.keys(results.prompts).length} categories • ${totalPrompts} total</span>
                </div>
                <div class="space-y-3">
        `;
        
        for (const [category, prompts] of Object.entries(results.prompts).sort()) {
            const promptList = Array.isArray(prompts) ? prompts : [];
            const categoryColor = getCategoryColor(category);
            const activeCount = promptList.filter(p => {
                const name = typeof p === 'string' ? p : (p.name || '');
                return isActive(name, 'prompt');
            }).length;
            
            html += `
                <div class="rounded-lg p-4" style="background: var(--card-bg); border: 1px solid var(--border-secondary);">
                    <div class="flex items-center justify-between mb-3">
                        <h5 class="text-sm font-medium" style="color: ${categoryColor};">
                            <span class="inline-block w-2 h-2 rounded-full mr-2" style="background: ${categoryColor};"></span>
                            ${escapeHtml(category)}
                        </h5>
                        <span class="text-xs" style="color: var(--text-muted);">${activeCount}/${promptList.length} active</span>
                    </div>
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
            `;

            promptList.forEach(prompt => {
                const promptName = typeof prompt === 'string' ? prompt : (prompt.name || 'Unknown');
                const active = isActive(promptName, 'prompt');
                html += `
                    <div class="text-sm font-mono px-3 py-1.5 rounded" style="${active ? 'color: var(--text-primary); background: rgba(255,255,255,0.04); border: 1px solid var(--border-secondary);' : 'color: var(--text-muted); background: transparent; border: 1px solid var(--border-secondary); opacity: 0.5; text-decoration: line-through;'}">
                        ${escapeHtml(promptName)}
                        ${!active ? '<span class="text-xs ml-2" style="color: #f87171;">(deactivated)</span>' : ''}
                    </div>
                `;
            });
            
            html += `
                    </div>
                </div>
            `;
        }
        
        html += `
                </div>
            </div>
        `;
    }
    
    // Component Tools section (enabled for this profile, shown with COMP_ display prefix)
    const componentTools = profile.component_tools || [];
    if (componentTools.length > 0) {
        html += `
            <div class="space-y-4 border-t border-gray-700/50 pt-6">
                <div class="flex items-center justify-between">
                    <h4 class="text-base font-semibold" style="color: var(--text-primary);">
                        Component Tools
                        <span class="ml-2 text-xs font-normal px-2 py-0.5 rounded" style="background: rgba(99,102,241,0.15); color: #818cf8; border: 1px solid rgba(99,102,241,0.3);">enabled for profile</span>
                    </h4>
                    <span class="text-sm" style="color: var(--text-muted);">${componentTools.length} tools</span>
                </div>
                <div class="rounded-lg p-4" style="background: var(--card-bg); border: 1px solid var(--border-secondary);">
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
        `;
        componentTools.forEach(ct => {
            const displayName = ct.display_name || ct.name;
            const desc = ct.description || '';
            html += `
                <div class="text-sm font-mono px-3 py-1.5 rounded" style="background: rgba(99,102,241,0.08); border: 1px solid rgba(99,102,241,0.2); color: #c7d2fe;" title="${escapeHtml(desc)}">
                    ${escapeHtml(displayName)}
                </div>
            `;
        });
        html += `
                    </div>
                </div>
            </div>
        `;
    }

    // Platform Connector Tools — render one section per connector (e.g. "Platform: Google")
    const platformTools = profile.platform_tools || [];
    if (platformTools.length > 0) {
        // Group by category field
        const ptByCategory = {};
        platformTools.forEach(pt => {
            const cat = pt.category || 'Platform Tools';
            if (!ptByCategory[cat]) ptByCategory[cat] = [];
            ptByCategory[cat].push(pt);
        });
        Object.entries(ptByCategory).forEach(([cat, tools]) => {
            html += `
                <div class="space-y-4 border-t border-gray-700/50 pt-6">
                    <div class="flex items-center justify-between">
                        <h4 class="text-base font-semibold" style="color: var(--text-primary);">
                            ${escapeHtml(cat)}
                            <span class="ml-2 text-xs font-normal px-2 py-0.5 rounded" style="background: rgba(16,185,129,0.15); color: #34d399; border: 1px solid rgba(16,185,129,0.3);">active for this profile</span>
                        </h4>
                        <span class="text-sm" style="color: var(--text-muted);">${tools.length} tool${tools.length !== 1 ? 's' : ''}</span>
                    </div>
                    <div class="rounded-lg p-4" style="background: var(--card-bg); border: 1px solid var(--border-secondary);">
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
            `;
            tools.forEach(pt => {
                const desc = pt.description || '';
                const argCount = (pt.arguments || []).length;
                const argHint = argCount > 0 ? ` · ${argCount} arg${argCount !== 1 ? 's' : ''}` : '';
                html += `
                    <div class="text-sm font-mono px-3 py-1.5 rounded" style="background: rgba(16,185,129,0.08); border: 1px solid rgba(16,185,129,0.2); color: #6ee7b7;" title="${escapeHtml(desc)}">
                        ${escapeHtml(pt.name)}
                        <span class="text-xs ml-1" style="color: var(--text-muted); font-family: sans-serif;">${argHint}</span>
                    </div>
                `;
            });
            html += `
                        </div>
                    </div>
                </div>
            `;
        });
    }

    // If no tools, prompts, component tools, or platform tools
    if ((!results.tools || Object.keys(results.tools).length === 0) &&
        (!results.prompts || Object.keys(results.prompts).length === 0) &&
        componentTools.length === 0 &&
        platformTools.length === 0) {
        html = '<p style="color: var(--text-muted);">No tools or prompts have been classified yet.</p>';
    }

    content.innerHTML = html;
    
    // Show modal
    modal.classList.remove('hidden');
    
    // Close button handler
    const closeBtn = modal.querySelector('#classification-modal-close');
    closeBtn.onclick = () => modal.classList.add('hidden');
    
    // Click outside to close
    modal.onclick = (e) => {
        if (e.target === modal) {
            modal.classList.add('hidden');
        }
    };
}

function getCategoryColor(category) {
    const colors = {
        'Performance': '#3b82f6',      // blue
        'Data Quality': '#10b981',      // green
        'Data Visualization': '#8b5cf6', // purple
        'Database Information': '#06b6d4', // cyan
        'Table Management': '#f59e0b',  // amber
        'Security': '#ef4444',          // red
        'User Management': '#ec4899',   // pink
        'Utilities': '#6b7280',         // gray
        'Reporting': '#14b8a6',         // teal
        'Archiving': '#a855f7'          // purple
    };
    return colors[category] || '#9ca3af'; // default gray
}

/**
 * Library access — the anchor grant control (PROP-005 §6).
 *
 * `skillsConfig.skills[].anchors` is the runtime twin of `param`: config-time `param`
 * selects what is INJECTED, runtime `anchors` selects what the agent may FETCH from the
 * skill's on-demand library. It is enforced fail-closed in the skill_library handler
 * ("the list is the grant"), so it is a governance control — it must be visible and
 * operable, not JSON-only.
 *
 * Semantics preserved exactly: ABSENT = all sections (the additive default). "All" therefore
 * saves NO key rather than an exhaustive list — an exhaustive list would freeze the grant
 * and silently exclude sections a later skill update adds. Glob patterns already in a
 * stored grant (e.g. `teradata/modules/semantic/*`) are preserved verbatim as chips: they
 * grant forward, and expanding them into concrete anchors would quietly destroy that.
 */
function _mountAnchorGrantControl(row, skill) {
    const total = skill.library_sections || 0;
    const nameCell = row.querySelector('.min-w-0 > .min-w-0') || row.querySelector('.min-w-0');
    if (!nameCell) return;

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.dataset.role = 'anchor-grant-btn';
    btn.className = 'text-[10px] font-medium px-2 py-0.5 rounded whitespace-nowrap mt-1 inline-flex items-center gap-1 transition-colors';

    const readGrant = () => {
        if (!row.dataset.anchors) return null;
        try { const a = JSON.parse(row.dataset.anchors); return Array.isArray(a) ? a : null; }
        catch { return null; }
    };
    const paint = () => {
        const g = readGrant();
        const restricted = Array.isArray(g);
        const n = restricted ? g.filter(a => !a.includes('*')).length + g.filter(a => a.includes('*')).length : total;
        btn.textContent = restricted ? `Library access · ${n} of ${total}` : `Library access · all ${total}`;
        btn.style.cssText = restricted
            ? 'background: rgba(245,158,11,0.12); color: #fbbf24; border: 1px solid rgba(245,158,11,0.25);'
            : 'background: rgba(14,165,233,0.10); color: #38bdf8; border: 1px solid rgba(14,165,233,0.20);';
        btn.title = restricted
            ? 'This agent may fetch only the granted library sections; anything else is refused at the tool layer. Click to change.'
            : 'This agent may fetch any section of the skill library. Click to restrict.';
    };
    paint();

    // ONE dialog, shared with the Skill Library component's Libraries tab — the
    // editing semantics live there, so the two entry points cannot drift. Here the
    // caller persists into the row dataset; the profile save writes it.
    btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const open = window.openSkillGrantDialog;
        if (!open) return;
        open({
            skillId: skill.skill_id,
            skillName: skill.name || skill.skill_id,
            totalSections: total,
            profileTag: row.closest('[data-profile-tag]')?.dataset?.profileTag || '',
            anchors: readGrant(),
            onSave: (grant) => {
                if (grant === null) delete row.dataset.anchors;
                else row.dataset.anchors = JSON.stringify(grant);
                paint();
            },
        });
    });

    nameCell.appendChild(btn);
}

/**
 * Version pin — the per-profile skill version binding (best-practice step 4b).
 *
 * `skillsConfig.skills[].content_hash` pins this profile to one content-addressed
 * version of the skill: the live copy when its address matches, else the archived
 * snapshot from the version archive. ABSENT = follow the live (latest) copy — the
 * additive default, byte-identical round-trip. The chip renders only where the
 * control is actionable (a pin is stored, or archived versions exist), so the
 * common unversioned skill row stays clean.
 *
 * State lives on `row.dataset.contentHash` (stamped synchronously at render from
 * the STORED entry, mutated only here) — the same truthful-save contract as the
 * anchor grant. The version list is fetched lazily on first open, never per-row.
 */
function _mountVersionPinControl(row, skill) {
    const nameCell = row.querySelector('.min-w-0 > .min-w-0') || row.querySelector('.min-w-0');
    if (!nameCell) return;

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.dataset.role = 'version-pin-btn';
    btn.className = 'text-[10px] font-medium px-2 py-0.5 rounded whitespace-nowrap mt-1 mr-1 inline-flex items-center gap-1 transition-colors';

    const readPin = () => row.dataset.contentHash || null;
    const paint = () => {
        const pin = readPin();
        btn.textContent = pin ? `Version · pinned ${pin.slice(0, 8)}` : 'Version · latest';
        btn.style.cssText = pin
            ? 'background: rgba(245,158,11,0.12); color: #fbbf24; border: 1px solid rgba(245,158,11,0.25);'
            : 'background: rgba(148,163,184,0.10); color: var(--text-muted, #94a3b8); border: 1px solid rgba(148,163,184,0.20);';
        btn.title = pin
            ? 'This agent is PINNED to one version of the skill — later updates to the live copy do not reach it until unpinned. Click to change.'
            : 'This agent follows the live (latest) copy of the skill. Click to pin a specific version.';
    };
    paint();

    // Popover panel — body-portaled; carries data-skill-param-panel so it shares
    // the skills list's existing outside-click close AND re-render cleanup.
    const panel = document.createElement('div');
    panel.setAttribute('data-skill-param-panel', '');
    panel.setAttribute('data-version-pin-panel', '');
    panel.className = 'hidden fixed w-80 rounded-lg shadow-2xl z-[500] py-1 overflow-y-auto max-h-64';
    panel.style.cssText = 'background-color: var(--card-bg); border: 1px solid var(--border-primary);';
    document.body.appendChild(panel);

    const CHECK = `<svg class="w-3 h-3" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/></svg>`;
    const optRow = (val, label, sub, selected) => `
        <div data-ver-opt="${val}" class="flex items-start gap-2 px-3 py-2 text-xs cursor-pointer select-none transition-colors"
             style="color: ${selected ? '#fbbf24' : 'var(--text-muted)'};">
            <span class="w-3 flex-shrink-0 mt-0.5" style="color:#fbbf24">${selected ? CHECK : ''}</span>
            <span class="min-w-0"><span class="block" style="color: ${selected ? '#fbbf24' : 'var(--text-primary)'}">${label}</span>
            ${sub ? `<span class="block text-[10px]" style="color: var(--text-muted)">${sub}</span>` : ''}</span>
        </div>`;

    let loaded = null;  // cached versions payload (per open profile modal lifetime)
    async function buildPanel() {
        const pin = readPin();
        if (loaded === null) {
            panel.innerHTML = `<div class="px-3 py-2 text-xs" style="color: var(--text-muted)">Loading versions…</div>`;
            try {
                const token = localStorage.getItem('tda_auth_token');
                const res = await fetch(`/api/v1/skills/${encodeURIComponent(skill.skill_id)}/versions`,
                    { headers: { 'Authorization': `Bearer ${token}` } });
                loaded = res.ok ? await res.json() : false;
            } catch { loaded = false; }
        }
        const cur = (loaded && loaded.current_content_hash) || null;
        const rows = [optRow('', 'Follow latest',
            'Always the live copy — pack updates and edits apply immediately.', !pin)];
        if (cur) {
            rows.push(optRow(cur, `Pin current · ${cur.slice(0, 12)}`,
                'Freeze this agent on the content installed right now.',
                !!pin && cur.startsWith(pin.slice(0, 12))));
        }
        for (const v of (loaded && loaded.versions) || []) {
            const h = v.content_hash || v.short_hash || '';
            if (!h || (cur && cur.startsWith(h.slice(0, 12)))) continue;
            const when = (v.archived_at || '').slice(0, 10);
            rows.push(optRow(h, `Archived · ${h.slice(0, 12)}`,
                when ? `Replaced on ${when}.` : '',
                !!pin && h.startsWith(pin.slice(0, 12))));
        }
        if (loaded === false) {
            rows.push(`<div class="px-3 py-2 text-[10px]" style="color: var(--text-muted)">Could not load the version list — “Follow latest” still works.</div>`);
        }
        panel.innerHTML = rows.join('');
        panel.querySelectorAll('[data-ver-opt]').forEach(opt => {
            opt.addEventListener('mouseenter', () => { opt.style.backgroundColor = 'var(--hover-bg)'; });
            opt.addEventListener('mouseleave', () => { opt.style.backgroundColor = ''; });
            opt.addEventListener('click', (e) => {
                e.stopPropagation();
                const val = opt.dataset.verOpt;
                if (!val) delete row.dataset.contentHash;
                else row.dataset.contentHash = val;
                paint();
                panel.classList.add('hidden');
            });
        });
    }

    btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const opening = panel.classList.contains('hidden');
        document.querySelectorAll('[data-skill-param-panel]').forEach(p => p.classList.add('hidden'));
        if (!opening) return;
        const rect = btn.getBoundingClientRect();
        panel.style.top = `${rect.bottom + 4}px`;
        panel.style.left = `${rect.left}px`;
        panel.classList.remove('hidden');
        await buildPanel();
    });

    nameCell.appendChild(btn);
}

/**
 * Attach the version pin to a collected skill entry, if the row carries one.
 * Absent → NO `content_hash` key (= follow latest, the additive default), so an
 * unpinned skill round-trips byte-identically to how it arrived.
 */
function _withVersionPin(entry, row) {
    const pin = row?.dataset?.contentHash;
    if (pin && /^[0-9a-f]{12,64}$/.test(pin)) entry.content_hash = pin;
    return entry;
}

/**
 * Collect skill toggle states from the profile modal into a skillsConfig object.
 */
function _collectSkillsConfig(modal, storedSkillsConfig) {
    const container = modal.querySelector('#profile-modal-skills-list');
    // "An empty UI is not an empty choice": the skills list renders ASYNC (it fetches
    // /v1/skills). A save firing before that render — or after a failed fetch — would
    // collect zero rows and wipe every skill assignment, including the anchor grants.
    // The render stamps completion; without the stamp we carry the STORED config forward.
    if (container && container.dataset.skillsRendered !== '1') {
        return storedSkillsConfig || { skills: [] };
    }
    const rows = modal.querySelectorAll('#profile-modal-skills-list [data-skill-row]');
    const skills = [];
    for (const row of rows) {
        const skillId = row.dataset.skillRow;
        const compId = row.dataset.componentId || '';
        const enabledCb = row.querySelector('input[data-role="enabled"]');
        const activeCb = row.querySelector('input[data-role="active"]');
        const isEnabled = enabledCb?.checked || false;
        const isActive = activeCb?.checked || false;
        const paramSel = row.querySelector('select[data-role="param"]');
        const paramVal = paramSel ? (paramSel.value || null) : null;

        if (compId) {
            // Companion skill of a component: persist the user's intent relative to the
            // component-driven default. auto=true means "track the component" (the backend
            // follows component enablement, ignoring the stored active flag); auto=false is
            // a deliberate user override (opt-out active=false / opt-in active=true).
            const compEnabled = _isComponentEnabledInModal(modal, compId);
            const auto = (isActive === compEnabled);
            // Always persist a companion entry so an opt-out (active=false while the
            // component is on) survives — the legacy "only if enabled||active" filter
            // would have dropped it and the backend would re-auto-apply the skill.
            skills.push(_withVersionPin(_withAnchorGrant({ id: skillId, enabled: isEnabled || isActive, active: isActive, param: paramVal, auto }, row), row));
        } else if (isEnabled || isActive) {
            // Non-companion: unchanged behavior.
            skills.push(_withVersionPin(_withAnchorGrant({ id: skillId, enabled: true, active: isActive, param: paramVal }, row), row));
        }
    }
    return { skills };
}

/**
 * Attach the anchor grant to a collected skill entry, if the row carries one.
 * Absent / empty → NO `anchors` key at all (= all sections, the additive default),
 * so an unrestricted skill round-trips byte-identically to how it arrived.
 */
function _withAnchorGrant(entry, row) {
    const raw = row?.dataset?.anchors;
    if (!raw) return entry;
    try {
        const list = JSON.parse(raw).filter(a => a !== '__none__');
        if (Array.isArray(list) && list.length) entry.anchors = list;
        else if (JSON.parse(raw).includes('__none__')) entry.anchors = [];   // explicit deny-all
    } catch { /* malformed → leave the entry unrestricted rather than guess */ }
    return entry;
}

/**
 * Is the builtin component `compId` enabled in this open profile modal? Prefers the
 * live component toggle; falls back to the component-enabled flag captured on the
 * companion skill row at render time.
 */
function _isComponentEnabledInModal(modal, compId) {
    const toggle = modal.querySelector(`.comp-toggle[data-component-id="${compId}"]`);
    // a companion skill follows the TOOL it teaches: a component whose tool is opt-in (Files)
    // counts as on only while the opt-in is (components.enablement.offers_tool)
    const optin = modal.querySelector(`.comp-tool-optin[data-component-id="${compId}"]`);
    if (toggle) return !!toggle.checked && (!optin || !!optin.checked);
    const row = modal.querySelector(`#profile-modal-skills-list [data-component-id="${compId}"]`);
    return row?.dataset.componentEnabled === '1';
}

/**
 * Re-sync auto-managed companion skill rows to their components' current toggle state.
 * Called when a component toggle changes so the skill's Active reflects the new default
 * live. Rows the user has manually overridden (data-auto-managed="0") are left alone.
 */
function _refreshCompanionSkillRows(modal) {
    const rows = modal.querySelectorAll('#profile-modal-skills-list [data-skill-row][data-component-id]');
    for (const row of rows) {
        const compId = row.dataset.componentId;
        if (!compId) continue;
        const compEnabled = _isComponentEnabledInModal(modal, compId);
        row.dataset.componentEnabled = compEnabled ? '1' : '0';
        if (row.dataset.autoManaged !== '1') continue;  // user-owned → don't touch
        const enabledCb = row.querySelector('input[data-role="enabled"]');
        const activeCb = row.querySelector('input[data-role="active"]');
        if (activeCb) activeCb.checked = compEnabled;
        if (enabledCb && compEnabled) enabledCb.checked = true;
    }
}

/**
 * Populate the Skills tab in the profile edit modal.
 * Fetches all available skills and renders toggle switches for each.
 * Assigned skills are read from profile.skillsConfig.skills[].
 */
async function _populateSkillsTab(modal, profile) {
    const container = modal.querySelector('#profile-modal-skills-list');
    if (!container) return;
    // Cleared up-front: a re-render that fails midway must not inherit the previous
    // render's trust stamp (the save would then collect a half-built list).
    delete container.dataset.skillsRendered;
    document.querySelectorAll('[data-skill-anchor-panel]').forEach(p => p.remove());

    const INJECTION_LABELS = {
        system_prompt: { label: 'System Prompt', color: '#a78bfa' },
        user_context:  { label: 'User Context',  color: '#60a5fa' },
    };

    try {
        const token = localStorage.getItem('tda_auth_token');
        const res = await fetch('/api/v1/skills', {
            headers: { 'Authorization': `Bearer ${token}` },
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const allSkills = data.skills || [];

        if (allSkills.length === 0) {
            container.innerHTML = '<p class="text-xs text-gray-500 italic">No skills installed</p>';
            return;
        }

        // Current assignments from profile
        const assigned = (profile?.skillsConfig?.skills || []);
        const assignedMap = {};
        for (const entry of assigned) {
            assignedMap[entry.id] = entry;
        }

        // Determine whether any skill in the list has allowed_params
        const anyHasParams = allSkills.some(s => s.allowed_params?.length > 0);
        // Default: show param column only if at least one assigned skill already has a param set
        const showParams = anyHasParams && allSkills.some(s => assignedMap[s.skill_id]?.param);
        container.dataset.paramsVisible = showParams ? '1' : '0';
        // Remove any portaled panels left over from a previous render of this container
        document.querySelectorAll('[data-skill-param-panel]').forEach(p => p.remove());

        const PARAM_TOGGLE_SVG = `<svg class="w-3 h-3 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4"/></svg>`;

        // Column headers
        container.innerHTML = `
            <div class="flex items-center justify-between px-4 py-2 bg-gray-800/50 rounded-lg border-b border-gray-700/40 mb-1">
                <div class="flex items-center gap-3 flex-1 min-w-0">
                    <span class="text-xs text-gray-300 font-medium">Skill</span>
                    ${anyHasParams ? `<button type="button" data-role="param-toggle"
                        class="flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-md border transition-all ${showParams ? 'text-blue-400 border-blue-500/40 bg-blue-500/10' : 'text-gray-500 border-gray-600/30 hover:text-gray-400 hover:border-gray-500/50'}">
                        ${PARAM_TOGGLE_SVG}
                        Parameter Assignment
                    </button>` : ''}
                </div>
                <div class="flex items-center gap-6 flex-shrink-0">
                    ${anyHasParams ? `<span data-role="param-header" class="text-xs text-gray-300 font-medium w-28 text-center"${showParams ? '' : ' style="display:none"'}>Param</span>` : ''}
                    <span class="text-xs text-gray-300 font-medium w-16 text-center">Enabled</span>
                    <span class="text-xs text-gray-300 font-medium w-16 text-center">Auto</span>
                </div>
            </div>
        `;

        // One document-level handler to close all param panels on outside click (re-registered each render)
        if (container._closeParamPanels) document.removeEventListener('click', container._closeParamPanels);
        container._closeParamPanels = () => document.querySelectorAll('[data-skill-param-panel]').forEach(p => p.classList.add('hidden'));
        document.addEventListener('click', container._closeParamPanels);

        const CHECK_SVG = `<svg class="w-3 h-3" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/></svg>`;

        // alphabetical by the name a person reads
        allSkills.sort((a, b) => String(a.display_name || a.name || a.skill_id || '').localeCompare(String(b.display_name || b.name || b.skill_id || '')));
        for (const skill of allSkills) {
            const entry = assignedMap[skill.skill_id];
            const compId = skill.component_id || '';
            // Companion skill: auto-managed unless the user owns the entry. Legacy entries
            // (no `auto` key) are treated as user-owned → backward compatible.
            const userOwned = !!entry && entry.auto !== true;
            let compEnabled = false;
            if (compId) {
                const liveToggle = modal.querySelector(`.comp-toggle[data-component-id="${compId}"]`);
                compEnabled = liveToggle ? _isComponentEnabledInModal(modal, compId)
                    : !!(profile?.componentConfig?.[compId]?.enabled) && (compId !== 'files' || profile?.componentConfig?.files?.agentTool === true);
            }
            const autoManaged = !!compId && !userOwned;
            // New profiles: default enabled=true, active=false
            let isEnabled = entry ? (entry.enabled !== false) : !profile;
            let isActive = entry ? !!entry.active : false;
            // Auto-managed companion skill: Active follows its component's enablement.
            if (autoManaged) { isActive = compEnabled; isEnabled = compEnabled || isEnabled; }
            const injTarget = INJECTION_LABELS[skill.injection_target] || INJECTION_LABELS.system_prompt;

            // Build custom param dropdown HTML (avoids un-styleable native select popup)
            let paramCellHtml = '';
            if (anyHasParams) {
                if (skill.allowed_params?.length > 0) {
                    const curParam = entry?.param || '';
                    const opts = ['', ...(skill.allowed_params)].map(p => {
                        const sel = curParam === p;
                        return `<div data-param-opt="${p}"
                            class="flex items-center gap-2 px-3 py-1.5 text-xs cursor-pointer select-none transition-colors ${sel ? 'text-blue-400 bg-blue-500/10' : ''}"
                            style="${sel ? '' : 'color: var(--text-muted);'}">
                            <span class="w-3 flex-shrink-0 text-blue-400">${sel ? CHECK_SVG : ''}</span>
                            <span>${p || '— none —'}</span>
                        </div>`;
                    }).join('');
                    paramCellHtml = `<div class="w-28 flex justify-center" data-param-col${showParams ? '' : ' style="display:none"'}>
                        <div class="relative w-full" data-param-wrapper>
                            <button type="button" data-role="param-btn"
                                class="flex items-center justify-between gap-2 w-full text-xs rounded-md px-2.5 py-1.5 focus:outline-none transition-all"
                                style="background-color: var(--input-bg); border: 1px solid var(--border-primary); color: var(--text-primary);">
                                <span data-role="param-label" class="truncate">${curParam || '— none —'}</span>
                                <svg class="w-3 h-3 flex-shrink-0" style="color: var(--text-muted)" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7"/></svg>
                            </button>
                            <input type="hidden" data-role="param" value="${curParam}">
                            <div data-role="param-panel" data-skill-param-panel
                                class="hidden fixed w-36 rounded-lg shadow-2xl z-[500] py-1 overflow-y-auto max-h-52"
                                style="background-color: var(--card-bg); border: 1px solid var(--border-primary);">
                                ${opts}
                            </div>
                        </div>
                    </div>`;
                } else {
                    paramCellHtml = `<div class="w-28 flex justify-center" data-param-col${showParams ? '' : ' style="display:none"'}><span class="text-xs text-gray-600">—</span></div>`;
                }
            }

            const row = document.createElement('div');
            row.className = 'flex items-center justify-between px-4 py-2.5 bg-gray-800/30 hover:bg-gray-800/50 rounded-lg border border-gray-700/30 hover:border-gray-600/50 transition-all';
            row.dataset.skillRow = skill.skill_id;
            // Anchor grant (PROP-005 §6): the profile's library slice for this skill.
            // Stamped SYNCHRONOUSLY from the stored profile so a save always reads a
            // truthful value — the control below only mutates it. Absent = all anchors
            // (the additive default); an explicit list is the restriction.
            if (skill.progressive) {
                row.dataset.progressive = '1';
                if (Array.isArray(entry?.anchors) && entry.anchors.length) {
                    row.dataset.anchors = JSON.stringify(entry.anchors);
                }
            }
            // Version pin (best-practice step 4b): stamped synchronously from the
            // stored entry so a save always reads a truthful value; the picker
            // below only mutates it. Absent = follow latest.
            if (typeof entry?.content_hash === 'string' && entry.content_hash) {
                row.dataset.contentHash = entry.content_hash;
            }
            if (compId) {
                row.dataset.componentId = compId;
                row.dataset.componentEnabled = compEnabled ? '1' : '0';
                row.dataset.autoManaged = autoManaged ? '1' : '0';
            }
            row.innerHTML = `
                <div class="flex items-center gap-2 min-w-0 flex-1">
                    <span class="text-xs px-2 py-0.5 rounded-full flex-shrink-0" style="color: ${injTarget.color}; background: ${injTarget.color}20; border: 1px solid ${injTarget.color}40;">${injTarget.label}</span>
                    <div class="min-w-0">
                        <span class="text-sm text-white font-medium truncate">${skill.name || skill.skill_id}${compId ? `<span data-auto-badge class="text-[10px] font-normal px-2 py-0.5 rounded-full align-middle ml-1.5 ${autoManaged ? '' : 'hidden'}" style="color: var(--text-muted, #94a3b8); background: rgba(148,163,184,0.12); border: 1px solid rgba(148,163,184,0.25);" title="The platform holds the switch: this skill follows the ${compId} component (active while the component is enabled for this agent). Toggle to take ownership.">Auto</span><button type="button" data-skill-reset class="text-[10px] px-2 py-0.5 rounded align-middle ml-1.5 ${autoManaged ? 'hidden' : ''}" style="color: var(--text-muted, #94a3b8); background: transparent; border: 1px dashed rgba(148,163,184,0.35);" title="Hand the switch back to the platform: drop this agent's choice and follow the ${compId} component again">Reset to default</button>` : ''}</span>
                        ${skill.description ? `<span class="text-xs text-gray-400 block truncate">${skill.description}</span>` : ''}
                    </div>
                </div>
                <div class="flex items-center gap-6 flex-shrink-0">
                    ${paramCellHtml}
                    <div class="w-16 flex justify-center">
                        <label class="profile-toggle">
                            <input type="checkbox" data-role="enabled" ${isEnabled ? 'checked' : ''}>
                            <span class="profile-toggle-slider" data-color="blue"></span>
                        </label>
                    </div>
                    <div class="w-16 flex justify-center">
                        <label class="profile-toggle">
                            <input type="checkbox" data-role="active" ${isActive ? 'checked' : ''}>
                            <span class="profile-toggle-slider" data-color="green"></span>
                        </label>
                    </div>
                </div>
            `;

            // Constraint: active implies enabled; enabled off → active off
            const enabledCb = row.querySelector('input[data-role="enabled"]');
            const activeCb = row.querySelector('input[data-role="active"]');
            // A manual toggle on a companion skill makes it user-owned: it stops tracking
            // the component (auto:false on save), the "Auto" badge drops and Reset appears.
            // Reset mirrors the components' control: the row follows its component again
            // (active = component enabled) and the save writes auto:true.
            const _setSkillOwned = (owned) => {
                if (!compId) return;
                row.dataset.autoManaged = owned ? '0' : '1';
                row.querySelector('[data-auto-badge]')?.classList.toggle('hidden', owned);
                row.querySelector('[data-skill-reset]')?.classList.toggle('hidden', !owned);
            };
            const _markUserOwned = () => { if (row.dataset.autoManaged === '1') _setSkillOwned(true); };
            enabledCb.addEventListener('change', () => { if (!enabledCb.checked) activeCb.checked = false; _markUserOwned(); });
            activeCb.addEventListener('change', () => { if (activeCb.checked) enabledCb.checked = true; _markUserOwned(); });
            row.querySelector('[data-skill-reset]')?.addEventListener('click', () => {
                const follows = _isComponentEnabledInModal(modal, compId);
                row.dataset.componentEnabled = follows ? '1' : '0';
                activeCb.checked = follows;
                if (follows) enabledCb.checked = true;
                _setSkillOwned(false);
            });

            // Wire custom param dropdown
            const paramWrapper = row.querySelector('[data-param-wrapper]');
            if (paramWrapper) {
                const btn    = paramWrapper.querySelector('[data-role="param-btn"]');
                const label  = paramWrapper.querySelector('[data-role="param-label"]');
                const hidden = paramWrapper.querySelector('[data-role="param"]');
                const panel  = paramWrapper.querySelector('[data-role="param-panel"]');

                // Portal: move panel to document.body so it has no scrollable ancestors
                document.body.appendChild(panel);

                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const opening = panel.classList.contains('hidden');
                    document.querySelectorAll('[data-skill-param-panel]').forEach(p => p.classList.add('hidden'));
                    if (opening) {
                        const rect = btn.getBoundingClientRect();
                        panel.style.top  = `${rect.bottom + 4}px`;
                        panel.style.left = `${rect.left}px`;
                        panel.style.minWidth = `${rect.width}px`;
                        panel.classList.remove('hidden');
                    }
                });

                panel.querySelectorAll('[data-param-opt]').forEach(opt => {
                    opt.addEventListener('mouseenter', () => {
                        if (!opt.classList.contains('text-blue-400')) {
                            opt.style.backgroundColor = 'var(--hover-bg)';
                            opt.style.color = 'var(--text-primary)';
                        }
                    });
                    opt.addEventListener('mouseleave', () => {
                        if (!opt.classList.contains('text-blue-400')) {
                            opt.style.backgroundColor = '';
                            opt.style.color = 'var(--text-muted)';
                        }
                    });
                    opt.addEventListener('click', (e) => {
                        e.stopPropagation();
                        const val = opt.dataset.paramOpt;
                        hidden.value = val;
                        label.textContent = val || '— none —';
                        panel.querySelectorAll('[data-param-opt]').forEach(o => {
                            const sel = o.dataset.paramOpt === val;
                            o.classList.toggle('text-blue-400',  sel);
                            o.classList.toggle('bg-blue-500/10', sel);
                            o.style.color = sel ? '' : 'var(--text-muted)';
                            o.style.backgroundColor = sel ? '' : '';
                            const chk = o.querySelector('span');
                            if (chk) chk.innerHTML = sel ? CHECK_SVG : '';
                        });
                        panel.classList.add('hidden');
                    });
                });
            }

            // ── Library access (the anchor grant) — progressive skills only ──────
            if (skill.progressive) _mountAnchorGrantControl(row, skill);

            // ── Version pin — only where actionable (archived versions exist,
            // or a pin is already stored and must stay visible/operable) ─────
            if ((skill.archived_versions || 0) > 0 || row.dataset.contentHash) {
                _mountVersionPinControl(row, skill);
            }

            container.appendChild(row);
        }

        // Render completed: a save may now trust the rows (see _collectSkillsConfig).
        container.dataset.skillsRendered = '1';

        // Wire the "Parameter Assignment" toggle button
        const paramToggleBtn = container.querySelector('[data-role="param-toggle"]');
        if (paramToggleBtn) {
            paramToggleBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                const nowVisible = container.dataset.paramsVisible === '1';
                const nextVisible = !nowVisible;
                container.dataset.paramsVisible = nextVisible ? '1' : '0';
                container.querySelectorAll('[data-param-col]').forEach(el => el.style.display = nextVisible ? '' : 'none');
                const hdr = container.querySelector('[data-role="param-header"]');
                if (hdr) hdr.style.display = nextVisible ? '' : 'none';
                if (nextVisible) {
                    paramToggleBtn.classList.add('text-blue-400', 'border-blue-500/40', 'bg-blue-500/10');
                    paramToggleBtn.classList.remove('text-gray-500', 'border-gray-600/30', 'hover:text-gray-400', 'hover:border-gray-500/50');
                } else {
                    paramToggleBtn.classList.remove('text-blue-400', 'border-blue-500/40', 'bg-blue-500/10');
                    paramToggleBtn.classList.add('text-gray-500', 'border-gray-600/30');
                }
            });
        }

        // Live re-sync: when a component toggle changes anywhere in the modal, update
        // auto-managed companion skill rows so their "Active" reflects the new component
        // default immediately (user-overridden rows are left untouched). Wired once.
        if (!modal._companionSyncWired) {
            modal._companionSyncWired = true;
            modal.addEventListener('change', (e) => {
                if (e.target?.classList?.contains('comp-toggle') || e.target?.classList?.contains('comp-tool-optin')) {
                    _refreshCompanionSkillRows(modal);
                }
            });
        }
    } catch (err) {
        console.error('[Profile Modal] Failed to load skills:', err);
        container.innerHTML = `<p class="text-xs text-red-400">Failed to load skills: ${err.message}</p>`;
    }
}

async function populateSystemPrompts(modal, profile) {
    const profileType = profile ? (profile.profile_type || 'tool_enabled') : 'tool_enabled';

    // Get all containers
    const masterPromptsContainer = modal.querySelector('#profile-modal-master-prompts');
    const workflowPromptsContainer = modal.querySelector('#profile-modal-workflow-prompts');
    const errorPromptsContainer = modal.querySelector('#profile-modal-error-prompts');
    const dataPromptsContainer = modal.querySelector('#profile-modal-data-prompts');
    const visualizationPromptsContainer = modal.querySelector('#profile-modal-visualization-prompts');
    const conversationPromptsContainer = modal.querySelector('#profile-modal-conversation-prompts');
    const ragPromptsContainer = modal.querySelector('#profile-modal-rag-prompts');
    const geniePromptsContainer = modal.querySelector('#profile-modal-genie-prompts');

    // Show/hide sections based on profile type
    const conversationSection = modal.querySelector('#conversation-execution-section');
    const ragSection = modal.querySelector('#rag-execution-section');
    const genieSection = modal.querySelector('#genie-coordination-section');
    const masterSystemSection = modal.querySelector('#master-system-section');
    const toolEnabledSection = modal.querySelector('#tool-enabled-prompts-section');
    const systemPromptsDescription = modal.querySelector('#system-prompts-description');

    if (profileType === 'llm_only') {
        // Conversation focused: show only conversation execution section
        if (conversationSection) conversationSection.classList.remove('hidden');
        if (ragSection) ragSection.classList.add('hidden');
        if (genieSection) genieSection.classList.add('hidden');
        if (masterSystemSection) masterSystemSection.classList.add('hidden');
        if (toolEnabledSection) toolEnabledSection.style.display = 'none';
        if (systemPromptsDescription) {
            systemPromptsDescription.textContent = 'Configure which conversation prompt version should be used for this profile.';
        }
    } else if (profileType === 'rag_focused') {
        // RAG focused: show only RAG execution section
        if (conversationSection) conversationSection.classList.add('hidden');
        if (ragSection) ragSection.classList.remove('hidden');
        if (genieSection) genieSection.classList.add('hidden');
        if (masterSystemSection) masterSystemSection.classList.add('hidden');
        if (toolEnabledSection) toolEnabledSection.style.display = 'none';
        if (systemPromptsDescription) {
            systemPromptsDescription.textContent = 'Configure which RAG synthesis prompt version should be used for this profile.';
        }
    } else if (profileType === 'genie') {
        // Genie: show only genie coordination section
        if (conversationSection) conversationSection.classList.add('hidden');
        if (ragSection) ragSection.classList.add('hidden');
        if (genieSection) genieSection.classList.remove('hidden');
        if (masterSystemSection) masterSystemSection.classList.add('hidden');
        if (toolEnabledSection) toolEnabledSection.style.display = 'none';
        if (systemPromptsDescription) {
            systemPromptsDescription.textContent = 'Configure the coordinator prompt that guides how this Genie orchestrates between child profiles.';
        }
    } else {
        // Tool enabled: show all sections
        if (conversationSection) conversationSection.classList.add('hidden');
        if (ragSection) ragSection.classList.add('hidden');
        if (genieSection) genieSection.classList.add('hidden');
        if (masterSystemSection) masterSystemSection.classList.remove('hidden');
        if (toolEnabledSection) toolEnabledSection.style.display = '';
        if (systemPromptsDescription) {
            systemPromptsDescription.textContent = 'Configure which prompt versions should be used for different functional areas. Defaults to system-wide prompt mappings.';
        }
    }

    // Get the provider from the profile's LLM configuration
    let profileProvider = null;
    if (profile && profile.llmConfigurationId) {
        const llmConfig = configState.llmConfigurations.find(c => c.id === profile.llmConfigurationId);
        if (llmConfig) {
            profileProvider = llmConfig.provider;
        }
    }

    // Define the categories and their subcategories with display names based on profile type
    let categories = {};

    if (profileType === 'llm_only') {
        // Conversation focused: check if MCP tools are enabled to determine which prompt category to show
        const useMcpToolsCheckbox = modal.querySelector('#profile-modal-use-mcp-tools');
        const useMcpTools = profile?.useMcpTools || useMcpToolsCheckbox?.checked || false;

        if (useMcpTools) {
            // MCP Tools enabled: show conversation_with_tools prompt option
            categories = {
                conversation_execution: {
                    container: conversationPromptsContainer,
                    subcategories: {
                        'conversation_with_tools': 'Conversation with Tools Prompt'
                    }
                }
            };
        } else {
            // Standard conversation: show basic conversation execution prompt
            categories = {
                conversation_execution: {
                    container: conversationPromptsContainer,
                    subcategories: {
                        'conversation': 'Conversation Execution Prompt'
                    }
                }
            };
        }
    } else if (profileType === 'rag_focused') {
        // RAG focused: only rag_focused_execution category
        categories = {
            rag_focused_execution: {
                container: ragPromptsContainer,
                subcategories: {
                    'rag_focused_execution': 'RAG Focused Execution Prompt'
                }
            }
        };
    } else if (profileType === 'genie') {
        // Genie: only genie_coordination category
        categories = {
            genie_coordination: {
                container: geniePromptsContainer,
                subcategories: {
                    'coordinator_prompt': 'Coordinator System Prompt'
                }
            }
        };
    } else {
        // Tool enabled: all categories
        categories = {
            master_system_prompts: {
                container: masterPromptsContainer,
                subcategories: profileProvider ? {
                    [profileProvider]: {
                        'Google': 'Google Gemini',
                        'Anthropic': 'Anthropic Claude',
                        'OpenAI': 'OpenAI GPT',
                        'Amazon': 'Amazon Bedrock',
                        'Azure': 'Azure OpenAI',
                        'Friendli': 'Friendli AI',
                        'Ollama': 'Ollama (Local)',
                        'OpenRouter': 'OpenRouter'
                    }[profileProvider] || `${profileProvider} Master System Prompt`
                } : {}
            },
            workflow_classification: {
                container: workflowPromptsContainer,
                subcategories: {
                    'task_classification': 'Task Classification',
                    'workflow_meta_planning': 'Workflow Meta Planning',
                    'workflow_tactical': 'Workflow Tactical'
                }
            },
            error_recovery: {
                container: errorPromptsContainer,
                subcategories: {
                    'error_recovery': 'Error Recovery',
                    'tactical_self_correction': 'Tactical Self-Correction',
                    'self_correction_column_error': 'Column Error Correction',
                    'self_correction_table_error': 'Table Error Correction'
                }
            },
            data_operations: {
                container: dataPromptsContainer,
                subcategories: {
                    'sql_consolidation': 'SQL Consolidation'
                }
            },
            visualization: {
                container: visualizationPromptsContainer,
                subcategories: {
                    'charting_instructions': 'Charting Instructions',
                    'g2plot_guidelines': 'G2Plot Guidelines'
                }
            }
        };
    }

    try {
        // Fetch available prompts from the system
        const token = localStorage.getItem('tda_auth_token');
        const availableResponse = await fetch('/api/v1/system-prompts/available', {
            headers: { 'Authorization': `Bearer ${token}` }
        });

        if (!availableResponse.ok) {
            throw new Error('Failed to fetch available prompts');
        }

        const availableData = await availableResponse.json();
        const availableCategories = availableData.categories || {};
        const defaults = availableData.defaults || {};
        
        // Debug logging
        console.log('[System Prompts] Available categories:', availableCategories);
        console.log('[System Prompts] Profile provider:', profileProvider);
        console.log('[System Prompts] Defaults:', defaults);

        // Fetch profile's current mappings if editing existing profile
        let profileMappings = {};
        if (profile && profile.id) {
            try {
                const mappingsResponse = await fetch(`/api/v1/system-prompts/profiles/${profile.id}/mappings`, {
                    headers: { 'Authorization': `Bearer ${token}` }
                });
                if (mappingsResponse.ok) {
                    const mappingsData = await mappingsResponse.json();
                    profileMappings = mappingsData.mappings || {};
                }
            } catch (err) {
                console.warn('Failed to load profile mappings:', err);
            }
        }

        // Render dropdowns for each category/subcategory
        for (const [category, config] of Object.entries(categories)) {
            const container = config.container;
            container.innerHTML = '';

            // Skip empty categories (e.g., master_system when no provider selected)
            if (Object.keys(config.subcategories).length === 0) {
                const emptyMessage = document.createElement('div');
                emptyMessage.className = 'text-xs text-gray-500 italic py-2';
                emptyMessage.textContent = category === 'master_system_prompts' 
                    ? 'Select an LLM Configuration first'
                    : 'No prompts in this category';
                container.appendChild(emptyMessage);
                continue;
            }

            for (const [subcategory, displayName] of Object.entries(config.subcategories)) {
                const div = document.createElement('div');
                div.className = 'flex items-center justify-between py-2';
                
                const label = document.createElement('label');
                label.className = 'text-xs font-medium text-gray-300 flex-1';
                // For master_system_prompts, don't show the provider name as label since dropdown shows prompt names
                label.textContent = category === 'master_system_prompts' ? 'Master System Prompt' : displayName;

                const selectWrapper = document.createElement('div');
                selectWrapper.className = 'flex-1';

                const select = document.createElement('select');
                select.className = 'w-full px-3 py-1.5 bg-gray-800/70 border border-gray-700/40 rounded text-xs text-white focus:outline-none focus:ring-2 focus:ring-orange-500/50 focus:border-orange-500/50 hover:border-gray-600/50 transition-all';
                select.dataset.category = category;
                select.dataset.subcategory = subcategory;

                // Add available prompts as options from the corresponding category
                const categoryPrompts = availableCategories[category]?.[subcategory] || [];
                console.log(`[System Prompts] ${category}/${subcategory}:`, categoryPrompts.length, 'prompts');
                
                let defaultPromptName = null;
                const optionValues = []; // Track all option values for debugging
                categoryPrompts.forEach(prompt => {
                    const option = document.createElement('option');
                    option.value = prompt.is_default ? '' : prompt.name;  // Empty value for default = use system default
                    optionValues.push({ name: prompt.name, value: option.value, is_default: prompt.is_default });
                    if (prompt.is_default) {
                        defaultPromptName = prompt.name;  // Remember the default prompt name
                        select.dataset.defaultPrompt = prompt.name;  // Store in dataset for save logic
                        option.textContent = `${prompt.display_name} (v${prompt.version}) (System Default)`;
                    } else {
                        option.textContent = `${prompt.display_name} (v${prompt.version})`;
                    }
                    select.appendChild(option);
                });

                console.log(`[System Prompts] Options for ${category}/${subcategory}:`, optionValues);

                // Set selected value if profile has custom mapping
                const currentMapping = profileMappings[category]?.[subcategory];
                console.log(`[System Prompts] Current mapping for ${category}/${subcategory}:`, currentMapping);
                if (currentMapping) {
                    // Check if the current mapping is the default prompt
                    if (currentMapping === defaultPromptName) {
                        console.log(`[System Prompts] Selecting default (empty) for ${category}/${subcategory}`);
                        select.value = '';  // Select the default option (empty value)
                    } else {
                        console.log(`[System Prompts] Selecting override "${currentMapping}" for ${category}/${subcategory}`);
                        select.value = currentMapping;  // Select the specific override
                        
                        // Verify the value was set correctly
                        if (select.value !== currentMapping) {
                            console.warn(`[System Prompts] Failed to set value for ${category}/${subcategory}. Tried: "${currentMapping}", got: "${select.value}"`);
                            console.warn(`[System Prompts] Available options:`, Array.from(select.options).map(opt => ({ text: opt.textContent, value: opt.value })));
                        }
                    }
                } else {
                    console.log(`[System Prompts] No custom mapping for ${category}/${subcategory}, using default`);
                }
                
                selectWrapper.appendChild(select);
                div.appendChild(label);
                div.appendChild(selectWrapper);
                container.appendChild(div);
            }
        }
    } catch (error) {
        console.error('Error populating system prompts:', error);
        // Show error in each container
        Object.values(categories).forEach(config => {
            config.container.innerHTML = '<span class="text-red-400 text-xs">Failed to load prompts</span>';
        });
    }
}

// ============================================================================
// SESSION PRIMER UTILITY FUNCTIONS
// ============================================================================

/**
 * Renders statement cards in the primer configuration UI
 * @param {Array<string>} statements - Array of statement strings
 */
function renderPrimerStatements(statements) {
    const container = document.getElementById('primer-statements-container');
    if (!container) return;

    const html = statements.map((statement, index) => {
        const statementId = `stmt-${index}`;
        return `
            <div class="bg-gradient-to-br from-white/10 to-white/5 border-2 border-white/10 rounded-lg p-3 hover:border-white/20 transition-all duration-200" data-statement-index="${index}">
                <div class="flex items-start justify-between gap-2 mb-2">
                    <span class="text-xs font-semibold text-gray-300">Statement ${index + 1}</span>
                    <div class="flex items-center gap-1 flex-shrink-0">
                        <button type="button" data-action="move-up" data-index="${index}"
                            class="card-btn card-btn--neutral card-btn--sm" ${index === 0 ? 'disabled' : ''}>
                            ↑
                        </button>
                        <button type="button" data-action="move-down" data-index="${index}"
                            class="card-btn card-btn--neutral card-btn--sm" ${index === statements.length - 1 ? 'disabled' : ''}>
                            ↓
                        </button>
                        <button type="button" data-action="delete" data-index="${index}"
                            class="card-btn card-btn--danger card-btn--sm">
                            ×
                        </button>
                    </div>
                </div>
                <textarea id="${statementId}" rows="2"
                    placeholder="Enter a statement or question to execute..."
                    class="w-full px-3 py-2 bg-gray-800/40 border border-gray-700/50 rounded-lg text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-teradata-orange focus:ring-1 focus:ring-teradata-orange resize-y"
                >${escapeHtml(statement)}</textarea>
            </div>
        `;
    }).join('');

    container.innerHTML = html;
    attachPrimerEventListeners();
}

/**
 * Attaches event listeners to primer statement cards and buttons
 */
function attachPrimerEventListeners() {
    const container = document.getElementById('primer-statements-container');
    if (!container) return;

    // Add statement button
    const addBtn = document.getElementById('add-primer-statement');
    if (addBtn) {
        // Remove old listener by cloning
        const newAddBtn = addBtn.cloneNode(true);
        addBtn.replaceWith(newAddBtn);

        newAddBtn.addEventListener('click', () => {
            const currentStatements = collectPrimerStatements();
            currentStatements.push('');
            renderPrimerStatements(currentStatements);
        });
    }

    // Move up/down/delete buttons
    container.querySelectorAll('[data-action]').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const action = e.currentTarget.dataset.action;
            const index = parseInt(e.currentTarget.dataset.index);
            const statements = collectPrimerStatements();

            if (action === 'move-up' && index > 0) {
                [statements[index - 1], statements[index]] = [statements[index], statements[index - 1]];
            } else if (action === 'move-down' && index < statements.length - 1) {
                [statements[index], statements[index + 1]] = [statements[index + 1], statements[index]];
            } else if (action === 'delete') {
                statements.splice(index, 1);
            }

            renderPrimerStatements(statements);
        });
    });
}

/**
 * Collects current statement values from the UI
 * @returns {Array<string>} Array of statement strings (trimmed)
 */
function collectPrimerStatements() {
    const container = document.getElementById('primer-statements-container');
    if (!container) return [];

    const statements = [];
    container.querySelectorAll('textarea').forEach(textarea => {
        statements.push(textarea.value.trim());
    });
    return statements;
}

// IFOC colour config for KG profile badges (matches knowledgeGraphPanelHandler.js)
const KG_IFOC_CONFIG = {
    llm_only:     { label: 'Ideate',     color: '#4ade80' },
    rag_focused:  { label: 'Focus',      color: '#3b82f6' },
    tool_enabled: { label: 'Optimize',   color: '#9333ea' },
    genie:        { label: 'Coordinate', color: '#F15F22' },
};

/**
 * Render the Knowledge Graphs section in the profile edit modal.
 * Shows all KGs assigned to (or owned by) the given profile, with a
 * radio button to set which one is active.  Activation is immediate
 * (same PATCH endpoint as the Resource Panel) — not deferred to Save.
 */
async function renderKnowledgeGraphsForProfile(profileId, kgSection, kgList) {
    try {
        const response = await API.loadKnowledgeGraphList();
        const allKgs = response.knowledge_graphs || [];

        // KGs where this profile is the owner OR appears in assigned_profiles
        const relevantKgs = allKgs.filter(kg =>
            kg.profile_id === profileId ||
            (kg.assigned_profiles || []).some(ap => ap.id === profileId)
        );

        if (relevantKgs.length === 0) {
            kgSection.classList.add('hidden');
            return;
        }

        kgSection.classList.remove('hidden');

        kgList.innerHTML = relevantKgs.map(kg => {
            const isOwner = kg.profile_id === profileId;
            // Determine is_active for this profile
            let isActive = false;
            if (isOwner) {
                isActive = !!kg.is_active_for_owner;
            } else {
                const entry = (kg.assigned_profiles || []).find(ap => ap.id === profileId);
                isActive = entry ? !!entry.is_active : false;
            }

            const ifoc = KG_IFOC_CONFIG[kg.profile_type] || { label: kg.profile_type || '?', color: '#6b7280' };
            const ownerLabel = isOwner
                ? '<span class="text-xs text-gray-500 ml-1">(own)</span>'
                : '';
            const ifocBadge = `<span class="text-xs font-semibold px-2 py-0.5 rounded-full" style="color:${ifoc.color};background:${ifoc.color}20;border:1px solid ${ifoc.color}40;">${ifoc.label}</span>`;
            const tagLabel = kg.profile_tag
                ? `<span class="text-xs font-mono text-gray-500">@${kg.profile_tag}</span>`
                : '';

            return `
                <div class="flex items-center justify-between py-2.5 bg-gray-800/30 hover:bg-gray-800/50 rounded-lg border border-gray-700/30 hover:border-gray-600/50 transition-all" style="padding-left:16px;padding-right:16px;">
                    <div class="flex items-center gap-2 flex-1 min-w-0">
                        <span class="text-sm text-gray-200 truncate">${escapeHtml(kg.profile_name || kg.profile_id)}</span>
                        ${tagLabel}
                        ${ifocBadge}
                        ${ownerLabel}
                    </div>
                    <div class="flex items-center justify-center" style="width:50px;">
                        <label class="ind-toggle">
                            <input type="radio"
                                   name="kg-active-selection"
                                   data-kg-owner-id="${kg.profile_id}"
                                   data-assigned-profile-id="${profileId}"
                                   ${isActive ? 'checked' : ''}>
                            <span class="ind-track"></span>
                        </label>
                    </div>
                </div>
            `;
        }).join('');

        // Activation handler — fires immediately, no Save needed
        kgList.addEventListener('change', async (e) => {
            const radio = e.target.closest('input[type="radio"][name="kg-active-selection"]');
            if (!radio) return;

            const kgOwnerId = radio.dataset.kgOwnerId;
            const assignedProfileId = radio.dataset.assignedProfileId;

            try {
                await API.activateKgForProfile(kgOwnerId, assignedProfileId);
                // Sync Resource Panel if visible
                const { refreshKnowledgeGraphsPanel } = await import('./knowledgeGraphPanelHandler.js?v=1.189');
                if (document.getElementById('knowledge-graphs-content')) {
                    await refreshKnowledgeGraphsPanel();
                }
            } catch (err) {
                console.error('[Profile Modal] KG activation failed:', err);
            }
        });

    } catch (err) {
        console.error('[Profile Modal] Failed to load knowledge graphs:', err);
        kgSection.classList.add('hidden');
    }
}

function _initProfileModalAccordion(modal) {
    // Tear down previous listeners (prevents stacking on modal re-open)
    if (modal._accordionAbort) modal._accordionAbort.abort();
    modal._accordionAbort = new AbortController();
    const { signal } = modal._accordionAbort;

    function makeExclusive(group) {
        group.forEach(details => {
            details.addEventListener('toggle', () => {
                if (!details.open) return;
                group.forEach(other => { if (other !== details) other.open = false; });
            }, { signal });
        });
    }

    // Level 1: top-level glass-panel cards within each section
    modal.querySelectorAll('.profile-modal-section').forEach(section => {
        makeExclusive(Array.from(section.querySelectorAll('.glass-panel > details')));
    });

    // Level 2: sub-section details inside the Intelligence Collections card
    const intelCard = modal.querySelector('#profile-content-intelligence > details');
    if (intelCard) {
        // Sub-details are: content-div > wrapper-div > details
        const subDetails = Array.from(intelCard.querySelectorAll(':scope > div > div > details'));
        makeExclusive(subDetails);
    }
}

function _openFirstCard(section) {
    if (!section) return;
    const cards = Array.from(section.querySelectorAll('.glass-panel > details'));
    // Collapsed by default (operator-settled): auto-opening the first card buried
    // the section under whichever card happened to be first. EXCEPTION — a section
    // with exactly ONE card keeps it open: collapsing a lone card is an extra
    // click for no scanning benefit.
    cards.forEach((d) => { d.open = cards.length === 1; });
}

function _initProfileModalNav(modal) {
    const navBtns = modal.querySelectorAll('.profile-nav-item');
    const sections = modal.querySelectorAll('.profile-modal-section');

    // Reset to Identity section each time the modal opens
    navBtns.forEach(b => b.classList.remove('active'));
    sections.forEach(s => s.classList.add('hidden'));
    const identityBtn = modal.querySelector('[data-profile-section="identity"]');
    const identitySection = modal.querySelector('#profile-section-identity');
    if (identityBtn) identityBtn.classList.add('active');
    if (identitySection) {
        identitySection.classList.remove('hidden');
        _openFirstCard(identitySection);
    }

    navBtns.forEach(btn => {
        // Replace with a fresh clone to avoid stacking listeners on re-open
        const fresh = btn.cloneNode(true);
        btn.parentNode.replaceChild(fresh, btn);
        fresh.addEventListener('click', () => {
            modal.querySelectorAll('.profile-nav-item').forEach(b => b.classList.remove('active'));
            sections.forEach(s => s.classList.add('hidden'));
            fresh.classList.add('active');
            const target = modal.querySelector(`#profile-section-${fresh.dataset.profileSection}`);
            if (target) {
                target.classList.remove('hidden');
                _openFirstCard(target);
            }
        });
    });
}

/**
 * Context-specific deep-link anchor: open THIS profile's editor from anywhere
 * (e.g. the Ontology inspect sidebar). Switches to Configuration, waits for the
 * view to mount, then opens the exact profile's modal. Created because no clean
 * programmatic entry point existed.
 */
export async function openProfileForEdit(profileId, section) {
    if (!profileId) return;
    try {
        const { handleViewSwitch } = await import('../ui.js?v=1.287');
        handleViewSwitch('credentials-view');
    } catch (e) { /* already on the view */ }
    await new Promise(r => setTimeout(r, 140));   // let the view + cards mount
    try {
        showProfileModal(profileId);
    } catch (e) {
        // Fallback to the card's own trigger if the modal helper isn't ready.
        const btn = document.querySelector(`[data-action="edit-profile"][data-profile-id="${profileId}"]`);
        if (btn) btn.click();
    }
    // Optional section landing (the Anatomy deep link): click the modal's OWN
    // nav item — never re-implement its switching. Progressive: section landing
    // is the contract, silence on failure (the modal opens on Identity anyway).
    if (section) {
        for (let i = 0; i < 3; i++) {
            await new Promise(r => setTimeout(r, 120));
            const nav = document.querySelector(`#profile-modal .profile-nav-item[data-profile-section="${section}"]`);
            if (nav) { nav.click(); return; }
        }
    }
}

// ── #25 · deployment-master awareness for the posture cards ──────────────────
// One cached read of the masters (the same evals-dashboard payload the Trust Circle
// reads — one source per rule). null = unknown (older backend, fetch failure) and
// must render NOTHING: claiming "off" on absent data is the same falsehood inverted.
let _deployMasters;                 // undefined = not fetched · null = unknown · {}
async function _fetchDeploymentMasters() {
    if (_deployMasters !== undefined) return _deployMasters;
    try {
        const r = await fetch('/api/v1/evals/online/dashboard?limit=1', {
            headers: { 'Authorization': `Bearer ${localStorage.getItem('tda_auth_token')}` },
        });
        const d = r.ok ? await r.json() : null;
        _deployMasters = (d && d.deployment_masters) || null;
    } catch (_) { _deployMasters = null; }
    return _deployMasters;
}

async function _applyMasterBanners(modal) {
    const masters = await _fetchDeploymentMasters();
    if (!masters) return;
    // ── DEACTIVATED family ──────────────────────────────────────────────────
    // Master off ⇒ the stored per-profile modes are genuinely INERT (each
    // runtime short-circuits at its first line: grounding/runtime.py,
    // guardrails/runtime.py, the online-eval hook). The card says so and is
    // greyed — DEACTIVATED, not locked: the controls stay editable so a
    // profile can be configured ahead of enablement, but nothing on the card
    // pretends the settings act. Cards with NO master (Honesty, grainJudge,
    // the non-surface Agent Authority controls) are deliberately absent from
    // this list and must never be marked — a false "deactivated" on an acting
    // protection is the inverse lie (gate-asserted).
    const CARDS = [
        ['grounding-mode', 'grounding_gate_enabled', 'The grounding gate',
         'the GROUNDED station'],
        ['guardrail-mode', 'guardrails_enabled', 'Guardrails', 'the GUARDED station'],
        // the inbound guardrail is its own card and was unmarked (assessment
        // finding): same master, same inertness, same marking
        ['guardrail-inbound-mode', 'guardrails_enabled', 'Guardrails',
         'the GUARDED station'],
        // judgeConfig is hard-gated by the scoring master — "a profile can
        // never turn scoring on for a deployment that switched it off"
        ['profile-judge-mode', 'online_eval_enabled', 'Automatic agent scoring',
         'Scoring & evidence'],
    ];
    const CHIP_CSS = 'margin-left:8px;font-size:10px;font-weight:600;'
        + 'padding:2px 7px;border-radius:9999px;vertical-align:2px;'
        + 'color:#f59e0b;background:rgba(245,158,11,0.10);'
        + 'border:1px solid rgba(245,158,11,0.25);white-space:nowrap;';
    const BANNER_CSS = 'padding:10px 12px;border-radius:8px;margin-bottom:12px;'
        + 'font-size:12px;line-height:1.5;color:var(--text-primary,#e2e8f0);'
        + 'background:rgba(245,158,11,0.08);border:1px solid rgba(245,158,11,0.25);';
    for (const [ctlName, masterKey, label, station] of CARDS) {
        const off = masters[masterKey] === false;
        const ctl = modal.querySelector(`input[name="${ctlName}"]`);
        const card = ctl && ctl.closest('details');
        if (!card) continue;
        const bannerId = `master-off-banner-${ctlName}`;
        const chipId = `master-off-chip-${ctlName}`;
        const oldB = card.querySelector(`#${bannerId}`); if (oldB) oldB.remove();
        const oldC = card.querySelector(`#${chipId}`); if (oldC) oldC.remove();
        const body = card.querySelector('summary + div, summary ~ div');
        // un-grey on every pass so a master flipped ON between opens recovers
        if (body) for (const child of body.children) child.style.opacity = '';
        if (!off) continue;
        // Collapsed state must be honest too: a chip beside the heading.
        const h3 = card.querySelector('summary h3');
        if (h3) {
            const chip = document.createElement('span');
            chip.id = chipId;
            chip.textContent = 'deactivated — master off';
            chip.style.cssText = CHIP_CSS;
            h3.appendChild(chip);
        }
        if (body) {
            const b = document.createElement('div');
            b.id = bannerId;
            b.style.cssText = BANNER_CSS;
            b.textContent = `${label} is switched OFF for this deployment, so the `
                + 'settings below are stored but DEACTIVATED — they will not run on '
                + 'any turn. You can still edit them (they take effect the moment the '
                + 'master is on). An administrator activates it under Administration '
                + `→ App Config → Trust → ${station}.`;
            body.prepend(b);
            // greyed out = deactivated at a glance; the banner itself stays legible
            for (const child of body.children) {
                if (child.id !== bannerId) child.style.opacity = '0.55';
            }
        }
    }
    // Smart Next Steps (Capabilities section) — same deactivated family,
    // anchored on its own control (RECOMMENDER_ENABLED gates every suggestion).
    {
        const ctl = modal.querySelector('#profile-recommender-enabled');
        const card = ctl && ctl.closest('details');
        if (card) {
            const bannerId = 'master-off-banner-recommender';
            const chipId = 'master-off-chip-recommender';
            const oldB = card.querySelector(`#${bannerId}`); if (oldB) oldB.remove();
            const oldC = card.querySelector(`#${chipId}`); if (oldC) oldC.remove();
            const body = card.querySelector('summary + div, summary ~ div');
            if (body) for (const child of body.children) child.style.opacity = '';
            if (masters.recommender_enabled === false) {
                const h3 = card.querySelector('summary h3');
                if (h3) {
                    const chip = document.createElement('span');
                    chip.id = chipId;
                    chip.textContent = 'deactivated — master off';
                    chip.style.cssText = CHIP_CSS;
                    h3.appendChild(chip);
                }
                if (body) {
                    const b = document.createElement('div');
                    b.id = bannerId;
                    b.style.cssText = BANNER_CSS;
                    b.textContent = 'Smart Next Steps is switched OFF for this '
                        + 'deployment, so the settings below are stored but '
                        + 'DEACTIVATED — no profile can emit suggestions. An '
                        + 'administrator activates it under Administration → App '
                        + 'Config → Features.';
                    body.prepend(b);
                    for (const child of body.children) {
                        if (child.id !== bannerId) child.style.opacity = '0.55';
                    }
                }
            }
        }
    }
    // ── the OBDA-READ data surface — the ADDITIVE imperative ─────────────────────
    // "If OBDA-READ is disabled the system must behave as before" (operator-restated
    // charter #15, after session d51cf073). The master now gates the WHOLE lane
    // at the one parse seam (agent_authority.parse_scope): contracts are not
    // rendered, query_product refuses, and the surface does NOT enforce. So the
    // surface block joins the deactivated family — an earlier version of this
    // marking claimed "still ENFORCES", which described a gap, not a design.
    {
        const surf = modal.querySelector('#profile-authority-surface-products');
        const block = surf && surf.parentElement;
        if (block) {
            const noteId = 'master-off-note-obda-surface';
            const oldN = block.querySelector(`#${noteId}`); if (oldN) oldN.remove();
            for (const child of block.children) child.style.opacity = '';
            if (masters.obda_enabled === false) {
                const n = document.createElement('div');
                n.id = noteId;
                n.style.cssText = BANNER_CSS + 'margin-top:6px;margin-bottom:6px;';
                n.textContent = 'The OBDA-READ component is switched OFF for this deployment '
                    + '(or for you), so this surface is stored but DEACTIVATED — the platform '
                    + 'behaves as if OBDA-READ were not installed: no product contracts reach '
                    + 'the planner, query_product refuses, and this surface does not confine. '
                    + 'An administrator enables it under Administration → Components → OBDA-READ; '
                    + 'the agent then needs the component switched on in its Capabilities.';
                const lbl = block.querySelector('label[for="profile-authority-surface-products"]');
                if (lbl && lbl.nextSibling) block.insertBefore(n, lbl.nextSibling);
                else block.prepend(n);
                for (const child of block.children) {
                    if (child.id !== noteId) child.style.opacity = '0.55';
                }
            }
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// PROFILE-EDITOR SURFACE CONTRACT — start
//
// ONE table saying which configuration surfaces each IFOC class exposes, so the
// editor's structure is a pure function of (profile class, capability toggles,
// tier) and never a residue of whichever branch happened to run last.
//
// Why it exists: visibility used to be hand-written across four per-class
// branches PLUS a post-init "re-activate the default tab" pass left over from
// when Resources was a tabbed pane. The two disagreed and the tab-era pass won,
// hiding the Intelligence Collections card for every class that also shows the
// MCP card — Ideate and Optimize. That made planner-repository scoping
// unreachable in the UI, and it is load-bearing for Optimize (planner.py scopes
// champion-case retrieval to `ragCollections`).
//
// Four rules, enforced by applyEditorSurface() on EVERY pass:
//   1. A surface is exposed iff that class's runtime actually consumes it.
//   2. Every surface is resolved on every pass, so an element no branch
//      mentions can no longer inherit a previous open's state.
//   3. One state, both channels (inline display AND the Tailwind `hidden`
//      class). Writing only one is what let two mechanisms disagree.
//   4. A declared surface whose element is missing is NAMED, never silently
//      skipped — a quiet `if (el)` no-op is how dead ids survived for months.
//
// Surfaces are addressed by stable ids on purpose: the previous
// `.closest('.mb-6')` / `.closest('.bg-gray-800\/40').parentElement` lookups
// were one markup tweak away from silently targeting the wrong element.
const IFOC_PROFILE_CLASSES = ['llm_only', 'rag_focused', 'tool_enabled', 'genie'];

const EDITOR_SURFACES = {
    // ── Capabilities ────────────────────────────────────────────────────────
    conversationCapabilities: { sel: '#conversation-capabilities-section', when: c => c.profileType === 'llm_only' },
    genieChildProfiles:       { sel: '#genie-slave-profiles-section',      when: c => c.profileType === 'genie' },
    genieSettings:            { sel: '#genie-profile-settings-section',    when: c => c.profileType === 'genie' },
    componentConfig:          { sel: '#component-config-section',          when: () => true },
    // ── AI stack ────────────────────────────────────────────────────────────
    dualModel:                { sel: '#dual-model-section',                when: c => c.profileType === 'tool_enabled' },
    mcpServerField:           { sel: '#profile-field-mcp-server',          when: c => c.usesMcp },
    // S6 — the governed tool surface governs the MCP catalog: shown exactly where the server is.
    toolSurfaceField:         { sel: '#profile-field-tool-surface',        when: c => c.usesMcp },
    toolClassification:       { sel: '#profile-card-classification',       when: c => c.profileType === 'tool_enabled' },
    // ── Resources ───────────────────────────────────────────────────────────
    mcpResources:             { sel: '#profile-content-mcp-resources',     when: c => c.usesMcp },
    // MCP prompts run through the planner/executor only; the LangChain path
    // Ideate uses for tools cannot invoke them.
    mcpPrompts:               { sel: '#profile-modal-prompts-section',     when: c => c.profileType === 'tool_enabled' },
    // Autocomplete + follow-up suggestions are read for EVERY class
    // (api/routes.py, recommender Tier B), so the card and the planner list are
    // universal — only the Query column below is class-specific.
    intelligenceCollections:  { sel: '#profile-content-intelligence',      when: () => true },
    plannerRepositories:      { sel: '#profile-card-planner',              when: () => true },
    // The Query column (`ragCollections`) is consumed by the planner alone.
    // Carried as a class on the CARD, not inline styles on the rows: switching
    // MCP server re-renders every row, and per-row state would be lost.
    plannerQueryColumn:       { sel: '#profile-card-planner', cls: 'is-autocomplete-only',
                                                                           when: c => c.profileType === 'tool_enabled' },
    knowledgeRepositories:    { sel: '#profile-card-knowledge',            when: c => c.usesKnowledge },
    knowledgeReranking:       { sel: '#profile-card-knowledge-reranking',  when: c => c.usesKnowledge },
    knowledgeAdvanced:        { sel: '#profile-card-knowledge-advanced',   when: c => c.usesKnowledge },
    skills:                   { sel: '#profile-content-skills',            when: () => true },
    systemPrompts:            { sel: '#profile-content-system-prompts',    when: c => c.isPrivileged },
};

/**
 * Pure: (profile class, capability toggles, tier) → which surfaces are exposed.
 * No DOM. The gate executes this against the whole class × toggle matrix.
 */
function resolveEditorSurface(ctx) {
    const type = ctx.profileType;
    const c = {
        profileType: type,
        isPrivileged: !!ctx.isPrivileged,
        // Ideate reaches MCP tools through its capability checkbox; Optimize
        // always does. Focus and Coordinate never call MCP directly.
        usesMcp: type === 'tool_enabled' || (type === 'llm_only' && !!ctx.useMcpTools),
        // Focus always retrieves; Ideate only when the capability is on.
        usesKnowledge: type === 'rag_focused' || (type === 'llm_only' && !!ctx.useKnowledge),
    };
    const out = {};
    for (const [key, surface] of Object.entries(EDITOR_SURFACES)) out[key] = !!surface.when(c);
    return out;
}

/**
 * Applies the contract. Returns the resolved state (for tests/logging).
 * Every surface is written on every call — that is what makes the structure
 * a function of the inputs rather than of call order.
 */
function applyEditorSurface(modal, ctx) {
    const state = resolveEditorSurface(ctx);
    const missing = [];
    for (const [key, surface] of Object.entries(EDITOR_SURFACES)) {
        const el = modal.querySelector(surface.sel);
        if (!el) { missing.push(key + ' (' + surface.sel + ')'); continue; }
        const show = state[key];
        if (surface.cls) {
            // Marker class carries the NEGATIVE (the modifier hides the part).
            el.classList.toggle(surface.cls, !show);
        } else {
            el.style.display = show ? '' : 'none';
            el.classList.toggle('hidden', !show);
        }
    }
    if (missing.length) {
        console.warn('[Profile Modal] surface contract: declared element(s) not found, so NOT applied:', missing.join(', '));
    }
    return state;
}
// PROFILE-EDITOR SURFACE CONTRACT — end
// ═══════════════════════════════════════════════════════════════════════════


async function showProfileModal(profileId = null, defaultProfileType = null) {
    const profile = profileId ? configState.profiles.find(p => p.id === profileId) : null;
    const isEdit = !!profile;

    const modal = document.getElementById('profile-modal');
    if (!modal) return;

    modal.querySelector('#profile-modal-title').textContent = isEdit ? 'Edit Profile' : 'Add Profile';

    // Trust Membrane badge (Ontology suite) — shown on the agent when editing it; click → detail/sign-off.
    const trustSlot = modal.querySelector('#profile-modal-trust');
    if (trustSlot) {
        trustSlot.innerHTML = '';
        if (isEdit && profile && window.TrustBadge) {
            window.TrustBadge.fetchAgent(profile.id).then(t => {
                if (!trustSlot.isConnected) return;
                trustSlot.innerHTML = window.TrustBadge.badgeHTML(t ? t.state : 'unsigned');
                const b = trustSlot.querySelector('.tb-badge');
                if (b) b.addEventListener('click', () => window.TrustBadge.openDetail(profile.id, profile.tag));
            }).catch(() => {});
        }
    }

    // Determine profile type
    // For new profiles: use defaultProfileType (determined by which profile class tab is active)
    // For existing profiles: use the profile's existing type
    const profileType = profile ?
        (profile.profile_type || 'tool_enabled') :
        (defaultProfileType || 'tool_enabled');
    console.log('[Profile Modal] Profile type determined:', profileType);

    // IFOC Tag configuration
    const ifocTagConfig = {
        'llm_only': { label: 'Ideate', color: '#4ade80', bgColor: 'rgba(74, 222, 128, 0.15)' },
        'rag_focused': { label: 'Focus', color: '#3b82f6', bgColor: 'rgba(59, 130, 246, 0.15)' },
        'tool_enabled': { label: 'Optimize',   color: '#9333ea', bgColor: 'rgba(147, 51, 234, 0.15)' },
        'genie':        { label: 'Coordinate', color: '#F15F22', bgColor: 'rgba(241, 95, 34, 0.15)' }
    };

    // Function to update the IFOC tag in the modal header
    const updateIfocTag = (type) => {
        const ifocTag = modal.querySelector('#profile-modal-ifoc-tag');
        if (ifocTag) {
            const config = ifocTagConfig[type] || ifocTagConfig['tool_enabled'];
            ifocTag.textContent = config.label;
            ifocTag.style.color = config.color;
            ifocTag.style.backgroundColor = config.bgColor;
            ifocTag.style.border = `1px solid ${config.color}`;
        }
    };

    // Set initial IFOC tag
    updateIfocTag(profileType);

    // Function to show/hide tool-related sections based on profile type
    const updateSectionVisibility = (profileType) => {
        console.log('[Profile Modal] updateSectionVisibility called with profileType:', profileType);

        // Structure comes from ONE table — see the PROFILE-EDITOR SURFACE
        // CONTRACT above. Nothing below this call may show or hide a surface;
        // per-class code here does population and listener wiring only.
        const surfaceState = applyEditorSurface(modal, {
            profileType,
            useMcpTools: modal.querySelector('#profile-modal-use-mcp-tools')?.checked || false,
            useKnowledge: modal.querySelector('#profile-modal-use-knowledge')?.checked || false,
            isPrivileged
        });
        console.log('[Profile Modal] surfaces exposed:',
            Object.keys(surfaceState).filter(k => surfaceState[k]).join(', '));
        // the Data sources section reads what the contract decided (whether this class calls
        // MCP tools) — population only, it shows or hides no surface itself
        if (DataSources.current()) { DataSources.syncFromModal(); DataSources.render(); }

        if (profileType === 'genie') {
            if (modal.querySelector('#genie-slave-profiles-section')) {
                populateGenieSlaveProfilesList(modal, profile);
            }
            if (modal.querySelector('#genie-profile-settings-section')) {
                loadGenieProfileSettings(modal, profile);
            }
        } else if (profileType === 'tool_enabled') {
            // Dual-model controls follow their own checkbox. Cloned first so
            // repeated visibility passes cannot stack duplicate listeners.
            const dualModelCheckbox = modal.querySelector('#enable-dual-model');
            const dualModelControls = modal.querySelector('#dual-model-controls');
            if (dualModelCheckbox && dualModelControls) {
                const newCheckbox = dualModelCheckbox.cloneNode(true);
                dualModelCheckbox.parentNode.replaceChild(newCheckbox, dualModelCheckbox);
                newCheckbox.addEventListener('change', (e) => {
                    dualModelControls.style.display = e.target.checked ? '' : 'none';
                });
            }
        }

        console.log('[Profile Modal] updateSectionVisibility completed');
    };

    // Helper function to populate Genie child profiles list
    function populateGenieSlaveProfilesList(modal, currentGenieProfile) {
        const container = modal.querySelector('#genie-slave-profiles-list');
        if (!container) return;

        container.innerHTML = '';

        // Get currently selected children (if editing)
        const selectedSlaves = currentGenieProfile?.genieConfig?.slaveProfiles || [];

        // Get all profiles except self (allow nested Genies, inactive profiles, pack-managed children)
        const availableProfiles = configState.profiles.filter(p => {
            return p.id !== currentGenieProfile?.id;  // Exclude only self-reference
        });

        if (availableProfiles.length === 0) {
            container.innerHTML = '<p class="text-gray-500 text-sm italic">No profiles available. Create other profile types first.</p>';
            return;
        }

        const slaveProfileSettings = currentGenieProfile?.genieConfig?.slaveProfileSettings || {};

        availableProfiles.forEach(profile => {
            const isSelected = selectedSlaves.includes(profile.id);
            const isGenieType = profile.profile_type === 'genie';
            const isOptimizer = profile.profile_type === 'tool_enabled';
            const isPassthroughEnabled = isOptimizer &&
                (slaveProfileSettings[profile.id]?.fullResultPassthrough === true);

            const profileTypeLabel = {
                'llm_only': 'Conversation',
                'tool_enabled': 'Efficiency Focused',
                'rag_focused': 'RAG Focused',
                'genie': 'Genie'
            }[profile.profile_type] || profile.profile_type;

            const profileTypeColor = {
                'llm_only': 'text-green-400',
                'tool_enabled': 'text-orange-400',
                'rag_focused': 'text-blue-400',
                'genie': 'text-purple-600'
            }[profile.profile_type] || 'text-gray-400';

            const div = document.createElement('div');
            div.className = `flex items-center space-x-3 py-2 px-2 rounded hover:bg-gray-700/30 transition-colors ${isGenieType ? 'bg-purple-900/20 border border-purple-700/30' : ''}`;
            div.innerHTML = `
                <input type="checkbox"
                       id="slave-${profile.id}"
                       value="${profile.id}"
                       ${isSelected ? 'checked' : ''}
                       class="genie-slave-checkbox w-4 h-4">
                <label for="slave-${profile.id}" class="flex-1 cursor-pointer">
                    ${isGenieType ? '<span class="text-purple-400 mr-1" title="Nested Genie Coordination">🔮</span>' : ''}
                    <span class="font-semibold text-sm text-white">@${profile.tag || 'UNKNOWN'}</span>
                    <span class="text-gray-400 text-sm ml-2">${profile.name || ''}</span>
                    <span class="text-xs ${profileTypeColor} ml-2 ${isGenieType ? 'font-semibold' : ''}">(${profileTypeLabel})</span>
                    ${isGenieType ? '<span class="ml-2 text-xs bg-purple-600/30 text-purple-300 px-2 py-0.5 rounded-full border border-purple-500/30">Nested</span>' : ''}
                </label>
                ${isOptimizer ? `
                <label class="slave-passthrough-label flex items-center gap-1 cursor-pointer select-none shrink-0 ${isSelected ? '' : 'opacity-30 pointer-events-none'}"
                       title="Pass full data rows to coordinator synthesis LLM">
                    <input type="checkbox"
                           class="slave-passthrough-toggle w-3 h-3 accent-orange-400"
                           data-profile-id="${profile.id}"
                           ${isPassthroughEnabled ? 'checked' : ''}
                           ${isSelected ? '' : 'disabled'}>
                    <span class="text-xs text-orange-300/80 whitespace-nowrap">Full data</span>
                </label>` : ''}
            `;

            // Keep passthrough toggle enabled/disabled in sync with the slave checkbox
            if (isOptimizer) {
                const slaveCheckbox = div.querySelector(`#slave-${profile.id}`);
                const passthroughLabel = div.querySelector('.slave-passthrough-label');
                const passthroughToggle = div.querySelector('.slave-passthrough-toggle');
                if (slaveCheckbox && passthroughLabel && passthroughToggle) {
                    slaveCheckbox.addEventListener('change', () => {
                        const checked = slaveCheckbox.checked;
                        passthroughToggle.disabled = !checked;
                        passthroughLabel.classList.toggle('opacity-30', !checked);
                        passthroughLabel.classList.toggle('pointer-events-none', !checked);
                        if (!checked) passthroughToggle.checked = false;
                    });
                }
            }

            container.appendChild(div);
        });

        // Add warning banner if any selected children are Genies
        const hasNestedGenies = selectedSlaves.some(slaveId => {
            const slaveProfile = configState.profiles.find(p => p.id === slaveId);
            return slaveProfile && slaveProfile.profile_type === 'genie';
        });

        if (hasNestedGenies) {
            const warningDiv = document.createElement('div');
            warningDiv.className = 'mt-4 p-3 bg-yellow-900/30 border border-yellow-600/50 rounded-lg';
            warningDiv.innerHTML = `
                <div class="flex items-start space-x-2">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5 text-yellow-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                    </svg>
                    <div class="flex-1">
                        <p class="text-sm font-semibold text-yellow-300 mb-1">Nested Genie Coordination Active</p>
                        <p class="text-xs text-yellow-200/80">
                            This Genie will coordinate other Genie profiles, significantly increasing token usage.
                            Maximum nesting depth is controlled in <span class="font-mono text-yellow-300">Administration → Expert Settings → Genie Coordination</span>.
                        </p>
                    </div>
                </div>
            `;
            container.appendChild(warningDiv);
        }
    }

    // Helper function to load Genie profile settings (temperature, timeout, max iterations)
    async function loadGenieProfileSettings(modal, profile) {
        console.log('[Profile Modal] Loading Genie settings for profile:', profile?.id);

        // Get form elements
        const tempSlider = modal.querySelector('#profile-genie-temperature');
        const tempValue = modal.querySelector('#profile-genie-temperature-value');
        const tempLockedBadge = modal.querySelector('#profile-genie-temperature-locked-badge');
        const tempHint = modal.querySelector('#profile-genie-temperature-hint');

        const timeoutInput = modal.querySelector('#profile-genie-query-timeout');
        const timeoutLockedBadge = modal.querySelector('#profile-genie-query-timeout-locked-badge');
        const timeoutHint = modal.querySelector('#profile-genie-query-timeout-hint');

        const iterInput = modal.querySelector('#profile-genie-max-iterations');
        const iterLockedBadge = modal.querySelector('#profile-genie-max-iterations-locked-badge');
        const iterHint = modal.querySelector('#profile-genie-max-iterations-hint');

        // Get profile's genie config
        const genieConfig = profile?.genieConfig || {};

        // Trust-aware routing toggle (requireTrustedExperts)
        const trustedOnlyEl = modal.querySelector('#profile-genie-trusted-experts');
        if (trustedOnlyEl) trustedOnlyEl.checked = !!genieConfig.requireTrustedExperts;

        // Load global settings to check for locks
        let globalSettings = {};
        try {
            const token = localStorage.getItem('tda_auth_token');
            if (token) {
                const response = await fetch('/api/v1/admin/genie-settings', {
                    headers: { 'Authorization': `Bearer ${token}` }
                });
                if (response.ok) {
                    const data = await response.json();
                    globalSettings = data.settings || {};
                }
            }
        } catch (e) {
            console.warn('[Profile Modal] Could not load global genie settings:', e);
        }

        // Setup temperature slider with live update
        if (tempSlider) {
            tempSlider.addEventListener('input', (e) => {
                if (tempValue) tempValue.textContent = parseFloat(e.target.value).toFixed(1);
            });
        }

        // Configure temperature
        if (tempSlider && tempValue) {
            const isLocked = globalSettings.temperature?.is_locked || false;
            const globalValue = globalSettings.temperature?.value ?? 0.7;
            const profileValue = genieConfig.temperature;

            if (isLocked) {
                tempSlider.value = globalValue;
                tempSlider.disabled = true;
                tempValue.textContent = parseFloat(globalValue).toFixed(1);
                if (tempLockedBadge) tempLockedBadge.classList.remove('hidden');
                if (tempHint) tempHint.textContent = `Locked by admin to ${globalValue}`;
            } else {
                tempSlider.value = profileValue ?? globalValue;
                tempSlider.disabled = false;
                tempValue.textContent = parseFloat(profileValue ?? globalValue).toFixed(1);
                if (tempLockedBadge) tempLockedBadge.classList.add('hidden');
                if (tempHint) tempHint.textContent = `Leave at ${globalValue} to use global default`;
            }
        }

        // Configure query timeout
        if (timeoutInput) {
            const isLocked = globalSettings.queryTimeout?.is_locked || false;
            const globalValue = globalSettings.queryTimeout?.value ?? 300;
            const profileValue = genieConfig.queryTimeout;

            if (isLocked) {
                timeoutInput.value = globalValue;
                timeoutInput.disabled = true;
                if (timeoutLockedBadge) timeoutLockedBadge.classList.remove('hidden');
                if (timeoutHint) timeoutHint.textContent = `Locked by admin to ${globalValue}s`;
            } else {
                timeoutInput.value = profileValue ?? '';
                timeoutInput.placeholder = String(globalValue);
                timeoutInput.disabled = false;
                if (timeoutLockedBadge) timeoutLockedBadge.classList.add('hidden');
                if (timeoutHint) timeoutHint.textContent = `Leave empty to use global default (${globalValue}s)`;
            }
        }

        // Configure max iterations
        if (iterInput) {
            const isLocked = globalSettings.maxIterations?.is_locked || false;
            const globalValue = globalSettings.maxIterations?.value ?? 10;
            const profileValue = genieConfig.maxIterations;

            if (isLocked) {
                iterInput.value = globalValue;
                iterInput.disabled = true;
                if (iterLockedBadge) iterLockedBadge.classList.remove('hidden');
                if (iterHint) iterHint.textContent = `Locked by admin to ${globalValue}`;
            } else {
                iterInput.value = profileValue ?? '';
                iterInput.placeholder = String(globalValue);
                iterInput.disabled = false;
                if (iterLockedBadge) iterLockedBadge.classList.add('hidden');
                if (iterHint) iterHint.textContent = `Leave empty to use global default (${globalValue})`;
            }
        }

        console.log('[Profile Modal] Genie settings loaded - global:', globalSettings, 'profile:', genieConfig);
    }

    // Add event listeners for Conversation Capabilities checkboxes
    const useMcpToolsCheckbox = modal.querySelector('#profile-modal-use-mcp-tools');
    const useKnowledgeCheckbox = modal.querySelector('#profile-modal-use-knowledge');

    if (useMcpToolsCheckbox) {
        useMcpToolsCheckbox.addEventListener('change', async () => {
            console.log('[Profile Modal] MCP Tools checkbox changed to:', useMcpToolsCheckbox.checked);
            // Re-run visibility update for llm_only profile type
            if (profileType === 'llm_only') {
                updateSectionVisibility('llm_only');
                // Update System Prompts tab to show correct prompt option (conversation vs conversation_with_tools)
                if (Utils.isPrivilegedUser()) {
                    const tempProfile = { ...profile, profile_type: 'llm_only', useMcpTools: useMcpToolsCheckbox.checked };
                    await populateSystemPrompts(modal, tempProfile);
                }
            }
        });
    }

    if (useKnowledgeCheckbox) {
        useKnowledgeCheckbox.addEventListener('change', () => {
            console.log('[Profile Modal] Knowledge checkbox changed to:', useKnowledgeCheckbox.checked);
            // Re-run visibility update for llm_only profile type
            if (profileType === 'llm_only') {
                updateSectionVisibility('llm_only');
            }
        });
    }

    // Set initial checkbox states from profile data (for llm_only profiles)
    if (profile && profileType === 'llm_only') {
        if (useMcpToolsCheckbox) {
            useMcpToolsCheckbox.checked = profile.useMcpTools || false;
        }
        if (useKnowledgeCheckbox) {
            useKnowledgeCheckbox.checked = profile.useKnowledgeCollections || false;
        }
    }

    // Initial visibility update will happen at the END after all async initialization
    // (Deferred to prevent race conditions with collection rendering)

    // Populate LLM configurations
    const llmSelect = modal.querySelector('#profile-modal-llm-provider');
    const activeLLMId = configState.activeLLM || configState.llmConfigurations[0]?.id;
    llmSelect.innerHTML = configState.llmConfigurations
        .map(config => {
            const isSelected = profile ? 
                (profile.llmConfigurationId === config.id) : 
                (config.id === activeLLMId);
            return `<option value="${config.id}" ${isSelected ? 'selected' : ''}>${escapeHtml(config.name)}</option>`;
        })
        .join('');

    // S4 — the SIGNED fallback LLM: "None" leads (a fallback is opt-in; without one an exhausted
    // provider is an honest error), the primary is never offered as its own fallback, and a
    // stored fallback the list no longer has is shown selected so a save does not silently drop it.
    const fbSelect = modal.querySelector('#profile-modal-llm-fallback');
    if (fbSelect) {
        const storedFb = profile ? (profile.llmFallbackConfigurationId || '') : '';
        const _fbOpts = [`<option value="" ${storedFb ? '' : 'selected'}>None — no fallback</option>`]
            .concat(configState.llmConfigurations
                .filter(c => c.id !== llmSelect.value)
                .map(c => `<option value="${c.id}" ${storedFb === c.id ? 'selected' : ''}>${escapeHtml(c.name)}</option>`));
        if (storedFb && !configState.llmConfigurations.some(c => c.id === storedFb)) {
            _fbOpts.push(`<option value="${escapeHtml(storedFb)}" selected>(missing) ${escapeHtml(storedFb)}</option>`);
        }
        fbSelect.innerHTML = _fbOpts.join('');
        // the primary changes → it must leave the fallback list (and a fallback equal to the new
        // primary reads as none)
        llmSelect.addEventListener('change', () => {
            const cur = fbSelect.value;
            fbSelect.innerHTML = [`<option value="">None — no fallback</option>`]
                .concat(configState.llmConfigurations.filter(c => c.id !== llmSelect.value)
                    .map(c => `<option value="${c.id}" ${cur === c.id ? 'selected' : ''}>${escapeHtml(c.name)}</option>`)).join('');
        });
    }

    // S6 — "Tools in context" (toolSurface.mode: all | governed). A stored agent keeps what it
    // has (absent = all, exactly as before); a NEW agent starts on Relevant per turn (Oct 2026).
    const tsSelect = modal.querySelector('#profile-modal-tool-surface');
    if (tsSelect) {
        const storedMode = profile
            ? ((profile.toolSurface && profile.toolSurface.mode === 'governed') ? 'governed' : 'all')
            : 'governed';
        tsSelect.value = storedMode;
    }

    // Populate MCP servers
    const mcpSelect = modal.querySelector('#profile-modal-mcp-server');
    const activeMCPId = configState.activeMCP || configState.mcpServers[0]?.id;
    const mcpOptions = configState.mcpServers
        .map(server => {
            const isSelected = profile ?
                (profile.mcpServerId === server.id) :
                (server.id === activeMCPId);
            return `<option value="${server.id}" ${isSelected ? 'selected' : ''}>${escapeHtml(server.name)}</option>`;
        });
    // Dangling binding (e.g. an agent-pack profile referencing an MCP server
    // that doesn't exist on this instance): surface it honestly — selected, so
    // saving the profile preserves the reference instead of silently dropping it.
    const boundMcpId = profile?.mcpServerId;
    if (boundMcpId && !configState.mcpServers.some(s => s.id === boundMcpId)) {
        mcpOptions.push(`<option value="${escapeHtml(boundMcpId)}" selected>${escapeHtml(boundMcpId)} (not available on this instance)</option>`);
    }
    // Never render an empty <select> (an invisible control): if no servers
    // exist and nothing is bound, show a disabled placeholder.
    if (!mcpOptions.length) {
        mcpOptions.push('<option value="" disabled selected>— no MCP servers configured —</option>');
    }
    mcpSelect.innerHTML = mcpOptions.join('');

    // PROP-006 — the Data sources section: the MCP select above lives there now (same element),
    // beside the Database Navigator's connections and the combining rules. State starts equal
    // to the stored profile, so a save at any moment carries every value forward.
    DataSources.init({ modal, profile,
        // PROP-006 phase 2 — an additional MCP server's tools and prompts, listed the same way
        // as the primary's (the same endpoint, the server's own names)
        fetchResources: async (serverId) => {
            const srv = configState.mcpServers.find(s => s.id === serverId);
            if (!srv) throw new Error('this server is not on this instance');
            const r = await API.fetchResourcesForServer(srv);
            return { tools: Object.values(r.tools || {}).flat().map(t => t.name),
                     prompts: Object.values(r.prompts || {}).flat().map(x => x.name) };
        } });

    // Populate context window type dropdown
    const cwtSelect = modal.querySelector('#profile-modal-context-window-type');
    if (cwtSelect) {
        const cwtTypes = getContextWindowTypes();
        if (cwtTypes.length > 0) {
            const selectedCwtId = profile?.contextWindowTypeId || cwtTypes.find(t => t.is_default)?.id || cwtTypes[0]?.id;
            populateContextWindowTypeDropdown(cwtSelect, selectedCwtId);
        } else {
            // Types not yet loaded — fetch them now
            loadContextWindowTypes().then(() => {
                const types = getContextWindowTypes();
                const selectedCwtId = profile?.contextWindowTypeId || types.find(t => t.is_default)?.id || types[0]?.id;
                populateContextWindowTypeDropdown(cwtSelect, selectedCwtId);
            });
        }
    }

    // Populate KG Store backend dropdown
    if (window.kgStoreHandler) {
        window.kgStoreHandler.populateProfileSelect(profile?.kgStoreConfigId || null);
    }

    // PROP-010 — the agent's file store (fileStoreAdmin.js: the stores this user's tier may use)
    if (window.FileStorePicker) {
        window.FileStorePicker.populate(profile || null, (window.configState && window.configState.profiles) || []);
    }

    // --- Context Limit Slider initialization ---
    const useDefaultCheckbox = modal.querySelector('#context-limit-use-default');
    const sliderControls = modal.querySelector('#context-limit-controls');
    const clSlider = modal.querySelector('#context-limit-slider');
    const clNumberInput = modal.querySelector('#context-limit-input');
    const clDisplay = modal.querySelector('#context-limit-display');
    const sliderContainer = modal.querySelector('#context-limit-slider-container');

    function formatTokensK(tokens) {
        if (tokens >= 1000000) return `${(tokens / 1000000).toFixed(1)}M`;
        return `${Math.round(tokens / 1000)}K`;
    }

    function updateCLDisplayText() {
        const modelMax = parseInt(sliderContainer?.dataset.modelMax || '128000');
        const current = parseInt(clSlider.value);
        clDisplay.textContent = `Context limited to ${formatTokensK(current)} tokens (model supports ${formatTokensK(modelMax)})`;
    }

    async function fetchModelContextLimit(llmConfigId) {
        if (!llmConfigId) return 128000;
        try {
            const token = localStorage.getItem('tda_auth_token');
            const res = await fetch(`/api/v1/llm/configurations/${llmConfigId}/context-limit`, {
                headers: { 'Authorization': `Bearer ${token}` }
            });
            if (res.ok) {
                const data = await res.json();
                return data.max_context_tokens || 128000;
            }
        } catch (err) {
            console.warn('Failed to fetch model context limit:', err);
        }
        return 128000;
    }

    async function updateSliderMax(llmConfigId) {
        const maxTokens = await fetchModelContextLimit(llmConfigId);
        if (sliderContainer) sliderContainer.dataset.modelMax = maxTokens;
        clSlider.max = maxTokens;
        clNumberInput.max = Math.round(maxTokens / 1024);

        if (useDefaultCheckbox.checked) {
            clSlider.value = maxTokens;
            clNumberInput.value = Math.round(maxTokens / 1024);
            clDisplay.textContent = `Using model default context limit (${formatTokensK(maxTokens)})`;
        } else {
            if (parseInt(clSlider.value) > maxTokens) {
                clSlider.value = maxTokens;
                clNumberInput.value = Math.round(maxTokens / 1024);
            }
            updateCLDisplayText();
        }
    }

    if (useDefaultCheckbox && clSlider && clNumberInput) {
        // Checkbox toggle
        useDefaultCheckbox.addEventListener('change', () => {
            if (useDefaultCheckbox.checked) {
                sliderControls.style.display = 'none';
                const modelMax = parseInt(sliderContainer?.dataset.modelMax || '128000');
                clSlider.value = modelMax;
                clNumberInput.value = Math.round(modelMax / 1024);
                clDisplay.textContent = `Using model default context limit (${formatTokensK(modelMax)})`;
            } else {
                sliderControls.style.display = 'flex';
                updateCLDisplayText();
            }
        });

        // Slider <-> number input sync
        clSlider.addEventListener('input', () => {
            clNumberInput.value = Math.round(parseInt(clSlider.value) / 1024);
            updateCLDisplayText();
        });

        clNumberInput.addEventListener('change', () => {
            const kVal = parseInt(clNumberInput.value) || 4;
            const tokens = Math.max(4096, Math.min(parseInt(clSlider.max), kVal * 1024));
            clSlider.value = tokens;
            clNumberInput.value = Math.round(tokens / 1024);
            updateCLDisplayText();
        });

        // Initialize with current LLM config
        updateSliderMax(llmSelect.value);

        // Restore saved value from profile
        if (profile && profile.contextLimitOverride != null) {
            useDefaultCheckbox.checked = false;
            sliderControls.style.display = 'flex';
            clSlider.value = profile.contextLimitOverride;
            clNumberInput.value = Math.round(profile.contextLimitOverride / 1024);
            // Display text will be set once updateSliderMax resolves
        }

        // Update slider when LLM configuration changes
        llmSelect.addEventListener('change', async () => {
            await updateSliderMax(llmSelect.value);
        });
    }

    // Populate strategic and tactical model dropdowns (for dual-model feature)
    const strategicSelect = modal.querySelector('#profile-modal-strategic-model');
    const tacticalSelect = modal.querySelector('#profile-modal-tactical-model');

    if (strategicSelect && tacticalSelect) {
        const llmOptions = configState.llmConfigurations
            .map(config => `<option value="${config.id}">${escapeHtml(config.name)}</option>`)
            .join('');

        // Add default option + all LLM configs
        strategicSelect.innerHTML = '<option value="">Use main LLM configuration</option>' + llmOptions;
        tacticalSelect.innerHTML = '<option value="">Use main LLM configuration</option>' + llmOptions;

        // Restore saved values if editing a dual-model profile
        if (profile && profile.dualModelConfig) {
            const dualModelCheckbox = modal.querySelector('#enable-dual-model');
            const dualModelControls = modal.querySelector('#dual-model-controls');

            if (dualModelCheckbox && dualModelControls) {
                dualModelCheckbox.checked = true;
                dualModelControls.style.display = '';

                if (profile.dualModelConfig.strategicModelId) {
                    strategicSelect.value = profile.dualModelConfig.strategicModelId;
                }
                if (profile.dualModelConfig.tacticalModelId) {
                    tacticalSelect.value = profile.dualModelConfig.tacticalModelId;
                }
            }
        }

        // Fast-track / Plan-gate (Optimize) — populate toggles + wire max-rows show/hide.
        // Additive: defaults off, no effect on existing profiles.
        const ftReportCb = modal.querySelector('#profile-modal-fasttrack-report');
        const ftMaxRows = modal.querySelector('#profile-modal-fasttrack-maxrows');
        const ftReportControls = modal.querySelector('#fasttrack-report-controls');
        const planGateCb = modal.querySelector('#profile-modal-plan-gate');
        if (ftReportCb && ftReportControls) {
            const ftOn = !!(profile && profile.fastTrackSimpleReports);
            ftReportCb.checked = ftOn;
            ftReportControls.style.display = ftOn ? '' : 'none';
            if (ftMaxRows && profile && profile.fastTrackMaxRows) ftMaxRows.value = profile.fastTrackMaxRows;
            ftReportCb.onchange = (e) => { ftReportControls.style.display = e.target.checked ? '' : 'none'; };
        }
        if (planGateCb) planGateCb.checked = !!(profile && profile.planningGate);
    }

    // Populate component configuration toggles
    const compTogglesContainer = modal.querySelector('#profile-component-toggles');
    if (compTogglesContainer) {
        (async () => {
            try {
                const authToken = localStorage.getItem('tda_auth_token');
                const authHeaders = authToken ? { 'Authorization': `Bearer ${authToken}` } : {};
                // scoped to the agent's class: the list then carries `manifest_default` (what an
                // Auto entry follows at runtime) and `profile_enabled_default` (the creation default)
                const ccType = (profile && profile.profile_type) || profileType || 'tool_enabled';
                const resp = await fetch(`/api/v1/components?profile_type=${encodeURIComponent(ccType)}`, { headers: authHeaders });
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                const data = await resp.json();
                const components = data.components || [];

                if (components.length === 0) {
                    compTogglesContainer.innerHTML = '<p class="text-xs text-gray-500 italic">No components installed.</p>';
                    return;
                }

                // Re-read the profile from the shared cache so we pick up any componentConfig
                // changes made via the Component Manager or Resource Panel since the modal opened.
                const liveProfile = profile?.id
                    ? (configState.profiles?.find(p => p.id === profile.id) ?? profile)
                    : profile;
                const componentConfig = liveProfile?.componentConfig ?? {};

                // alphabetical by the name a person reads — the manifest's display name
                components.sort((a, b) => String(a.display_name || a.name || a.component_id || '').localeCompare(String(b.display_name || b.name || b.component_id || '')));
                compTogglesContainer.innerHTML = components.map(comp => {
                    const compSettings = componentConfig[comp.component_id] || {};
                    // ONE rule for the switch (components/enablement.py, the same mark as a companion
                    // skill's): a stored `enabled` is the OWNER's; without one the platform holds the
                    // switch (Auto) and the agent follows the component's class default — an existing
                    // agent the pure manifest default, a new agent the creation default (yours, else
                    // the manifest's `on_for_new_agents`, else the class default)
                    const manifestDefault = !!(comp.manifest_default ?? comp.user_profile_default_enabled ?? false);
                    const creationDefault = !!(comp.profile_enabled_default ?? comp.user_profile_default_enabled ?? manifestDefault);
                    const followsDefault = profile ? manifestDefault : creationDefault;
                    // a creation default that differs from the class default is the owner's choice by
                    // proxy (the Database Navigator starts new agents off): written, never left to Auto
                    const owned = compSettings.enabled !== undefined || (!profile && creationDefault !== manifestDefault);
                    const isEnabled = owned ? !!compSettings.enabled : followsDefault;
                    const intensity = compSettings.intensity || comp.profile_defaults?.default_intensity || 'medium';
                    const hasIntensity = comp.component_type === 'action';
                    const globallyDisabled = !!comp.globally_disabled;
                    const everyTurn = !!comp.acts_every_turn;

                    return `
                        <div class="flex items-center justify-between py-2 px-3 rounded-lg bg-gray-800/30 border border-gray-700/20"
                             data-comp-row="${comp.component_id}" data-owned="${owned ? '1' : '0'}" data-manifest-default="${manifestDefault ? '1' : '0'}"
                             style="${globallyDisabled ? 'opacity:0.45;pointer-events:none' : ''}">
                            <div class="flex items-center gap-3">
                                <span class="text-sm text-gray-200">${comp.display_name}</span>
                                <span class="text-[10px] px-2 py-0.5 rounded bg-gray-700/50 text-gray-400 uppercase tracking-wider">${comp.component_type}</span>
                                ${everyTurn ? `<span class="text-[10px] px-2 py-0.5 rounded" style="background:rgba(6,182,212,0.10);color:#22d3ee;border:1px solid rgba(6,182,212,0.3)" title="Acts on every turn without being called — active whenever it is enabled">every turn</span>` : ''}
                                <span data-comp-auto-badge class="text-[10px] font-normal px-2 py-0.5 rounded-full ${owned ? 'hidden' : ''}" style="color: var(--text-muted, #94a3b8); background: rgba(148,163,184,0.12); border: 1px solid rgba(148,163,184,0.25);" title="The platform holds the switch: this agent stores no setting and follows the component's default for its class (${manifestDefault ? 'on' : 'off'}). Toggle to take ownership.">Auto</span>
                                <button type="button" data-comp-reset class="text-[10px] px-2 py-0.5 rounded ${owned ? '' : 'hidden'}" style="color: var(--text-muted, #94a3b8); background: transparent; border: 1px dashed rgba(148,163,184,0.35);" title="Hand the switch back to the platform: drop this agent's setting and follow the component's default for its class (${manifestDefault ? 'on' : 'off'})">Reset to default</button>
                                ${globallyDisabled ? `<span class="text-[10px] px-2 py-0.5 rounded" style="background:rgba(239,68,68,0.12);color:#f87171;border:1px solid rgba(239,68,68,0.2)">Disabled globally</span>` : ''}
                                ${comp.conflicts_with?.length ? (() => {
                                    const conflictNames = comp.conflicts_with.map(id => {
                                        const c = components.find(x => x.component_id === id);
                                        return c ? c.display_name : id;
                                    }).join(', ');
                                    return `<span class="text-[10px] px-2 py-0.5 rounded" style="background:rgba(245,158,11,0.1);color:#f59e0b;border:1px solid rgba(245,158,11,0.2)" title="Enabling this disables: ${conflictNames}">exclusive</span>`;
                                })() : ''}
                            </div>
                            <div class="flex items-center gap-3">
                                ${hasIntensity ? `
                                <select class="comp-intensity-select text-xs bg-gray-800 border border-gray-600 rounded px-2 py-1 text-gray-300"
                                        data-component-id="${comp.component_id}" ${(!isEnabled || globallyDisabled) ? 'disabled' : ''}>
                                    <option value="minimal" ${intensity === 'minimal' ? 'selected' : ''}>Minimal</option>
                                    <option value="low" ${intensity === 'low' ? 'selected' : ''}>Low</option>
                                    <option value="medium" ${intensity === 'medium' ? 'selected' : ''}>Medium</option>
                                    <option value="high" ${intensity === 'high' ? 'selected' : ''}>High</option>
                                </select>` : ''}
                                ${comp.component_id === 'knowledge_graph' ? `
                                <select class="comp-groundon-select text-xs bg-gray-800 border border-gray-600 rounded px-2 py-1 text-gray-300"
                                        data-component-id="knowledge_graph" ${(!isEnabled || globallyDisabled) ? 'disabled' : ''}
                                        title="Ground on: the live draft (every edit applies at once) or the ontology's RELEASED version only (Tier 4.3 release gates grounding; falls back to the draft, and says so, until a release exists)">
                                    <option value="live" ${(compSettings.groundOn || 'live') !== 'released' ? 'selected' : ''}>Ground on: live draft</option>
                                    <option value="released" ${compSettings.groundOn === 'released' ? 'selected' : ''}>Ground on: released version only</option>
                                </select>` : ''}
                                <label class="ind-toggle ind-toggle--sm">
                                    <input type="checkbox" class="comp-toggle"
                                           data-component-id="${comp.component_id}" ${isEnabled ? 'checked' : ''}
                                           ${globallyDisabled ? 'disabled' : ''}>
                                    <span class="ind-track"></span>
                                </label>
                            </div>
                        </div>${comp.component_id === 'database_navigator' ? `
                        <div class="comp-dbnav-sources-link" style="margin:-2px 0 4px 18px;padding:6px 12px;border-left:2px solid var(--border-secondary);color:var(--text-muted);font-size:0.72rem;">
                            Its databases, default source and queries are set under
                            <button type="button" class="comp-dbnav-open-sources" style="background:transparent;border:none;padding:0;color:var(--text-primary);text-decoration:underline;cursor:pointer;font-size:0.72rem;">Data sources</button>.
                        </div>` : ''}${comp.component_id === 'files' ? `
                        <div class="comp-files-agent" style="margin:-2px 0 4px 18px;padding:6px 12px;border-left:2px solid var(--border-secondary);display:flex;align-items:center;gap:12px;color:var(--text-muted);font-size:0.72rem;">
                            <span style="flex:1;">The agent can list, read and save files itself (TDA_Files). Off: files are kept by you, from its answers — the agent cannot see them.</span>
                            <label class="ind-toggle ind-toggle--sm" title="Let this agent use Files itself">
                                <input type="checkbox" class="comp-tool-optin" data-component-id="files" aria-label="The agent can list, read and save files"
                                       ${compSettings.agentTool === true ? 'checked' : ''} ${(!isEnabled || globallyDisabled) ? 'disabled' : ''}>
                                <span class="ind-track"></span>
                            </label>
                        </div>` : ''}${comp.component_id === 'platform' ? `
                        <div class="comp-platform-roles" data-component-id="platform" style="margin:-2px 0 4px 18px;padding:6px 12px;border-left:2px solid var(--border-secondary);color:var(--text-muted);font-size:0.72rem;">
                            <div style="color:var(--text-primary);margin-bottom:4px;">Roles — what this agent is for</div>
                            <div class="cpr-roles" style="display:flex;flex-direction:column;gap:3px;">Loading roles…</div>
                            <div style="display:flex;align-items:center;gap:10px;margin-top:8px;">
                                <span style="flex:1;">Changes to the platform</span>
                                <select class="cpr-writes-select text-xs rounded px-2 py-1" aria-label="Changes to the platform"
                                        style="background:var(--card-bg);color:var(--text-primary);border:1px solid var(--border-primary);"
                                        ${(!isEnabled || globallyDisabled) ? 'disabled' : ''}>
                                    <option value="off">Off — the agent only reads</option>
                                    <option value="review">Review — each change waits for your approval</option>
                                    <option value="allow">Allow — ordinary changes run</option>
                                </select>
                            </div>
                            <div class="cpr-note" style="margin-top:6px;">What each role grants is part of the agent's signature; this setting is not. A change to what an agent may do, and any delete, always waits for your approval in the Assurance Inbox — and signing, attesting and credentials stay yours in every role.</div>
                            <div class="cpr-ask" style="margin-top:10px;">
                                <div style="color:var(--text-primary);margin-bottom:4px;">Other agents it may ask</div>
                                <div class="cpr-ask-role" style="margin-bottom:4px;">Asking is the Problem Solver role's — tick it to choose whom this agent may ask.</div>
                                <div class="cpr-ask-list" style="display:flex;flex-direction:column;gap:3px;">Loading…</div>
                                <div class="cpr-ask-note" style="margin-top:4px;">Each answers in a conversation of its own, under its own authority, and must be activated for REST use. Not part of the signature, and not carried by an agent pack.</div>
                            </div>
                        </div>` : ''}`;
                }).join('');

                // Platform: the roles an agent can hold, and its write mode (TDA_Platform)
                const _platformRow = compTogglesContainer.querySelector('.comp-platform-roles');
                const _platformSync = () => {
                    if (!_platformRow) return;
                    const on = !!compTogglesContainer.querySelector('.comp-toggle[data-component-id="platform"]')?.checked;
                    const boxes = [..._platformRow.querySelectorAll('.cpr-role-cb')];
                    boxes.forEach(cb => { cb.disabled = !on || cb.dataset.planned === '1'; });
                    const canWrite = boxes.some(cb => cb.checked && cb.dataset.writes === '1');
                    const sel = _platformRow.querySelector('.cpr-writes-select');
                    if (sel) {
                        sel.disabled = !on || !canWrite;
                        sel.title = canWrite ? '' : 'None of the selected roles may change the platform — the agent only reads.';
                    }
                                   // Asking is a role's grant (the Problem Solver): the list is choosable only while one is ticked
                    const canAsk = boxes.some(cb => cb.checked && cb.dataset.asks === '1');
                    _platformRow.querySelectorAll('.cpr-ask-cb').forEach(cb => { cb.disabled = !on || !canAsk; });
                    const askNote = _platformRow.querySelector('.cpr-ask-role');
                    if (askNote) askNote.hidden = canAsk;
                };
                if (_platformRow) {
                    const liveP = (profile?.id ? (configState.profiles?.find(p => p.id === profile.id) ?? profile) : profile) || {};
                    const storedWrites = ((liveP.authorityConfig || liveP.identityConfig || {}).platformSurface || {}).writes;
                    const sel = _platformRow.querySelector('.cpr-writes-select');
                    if (sel) sel.value = ['review', 'allow'].includes(storedWrites) ? storedWrites : 'off';
                    // The agents it may ask (TDA_Platform `ask`) — offered only while the deployment allows asking
                    (async () => {
                        const box = _platformRow.querySelector('.cpr-ask-list');
                        if (!box) return;
                        const escA = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
                        const storedAsk = ((liveP.authorityConfig || liveP.identityConfig || {}).platformSurface || {}).ask;
                        const asked = Array.isArray(storedAsk) ? storedAsk : [];
                        let enabled = false;
                        try {
                            const r = await fetch('/api/v1/platform/asking', { headers: { Authorization: `Bearer ${localStorage.getItem('tda_auth_token')}` } });
                            enabled = !!(r.ok && (await r.json()).settings?.enabled);
                        } catch (_) { enabled = false; }
                        box.dataset.loaded = '1';
                        box.dataset.stored = JSON.stringify(asked);
                        if (!enabled) {
                            box.dataset.enabled = '0';
                            box.textContent = asked.length
                                ? `Asking is off on this deployment — the ${asked.length} agent(s) listed are kept and become askable when an administrator turns it on.`
                                : 'Asking other agents is off on this deployment — an administrator turns it on (Administration → Components → Platform).';
                            return;
                        }
                        box.dataset.enabled = '1';
                        const active = new Set(configState.activeForConsumptionProfileIds || []);
                        const others = (configState.profiles || []).filter(p => p.id && p.id !== liveP.id && p.tag)
                            .sort((a, b) => String(a.tag).localeCompare(String(b.tag)));
                        box.innerHTML = others.map(p => `<label class="cpr-ask-row" style="display:flex;align-items:center;gap:6px;">
                                <input type="checkbox" class="cpr-ask-cb" value="${escA(p.id)}" ${asked.includes(p.id) ? 'checked' : ''}>
                                <span style="color:var(--text-primary);">@${escA(p.tag)}</span>
                                <span>${escA(p.name || '')}${active.has(p.id) ? '' : ' — not activated, cannot be asked yet'}</span>
                            </label>`).join('') || '<span>You have no other agents.</span>';
                        _platformSync();
                    })();
                    (async () => {
                        try {
                            const data = await fetchPlatformRoles(false);
                            const storedRoles = Array.isArray(liveP.componentConfig?.platform?.roles) && liveP.componentConfig.platform.roles.length
                                ? liveP.componentConfig.platform.roles : (data.default || ['guide']);
                            const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
                            _platformRow.querySelector('.cpr-roles').innerHTML = (data.roles || []).map(role => {
                                const planned = role.status !== 'active';
                                const writes = (role.effects || []).includes('write');
                                const tip = role.description + (planned ? `\nPlanned — waits for: ${(role.needs || []).join(', ')}` : '');
                                return `<div class="cpr-role-row" data-role="${esc(role.id)}">
                                <label class="cpr-role u-tip" data-tip="${esc(tip)}" style="display:flex;align-items:center;gap:6px;${planned ? 'opacity:0.5;' : ''}">
                                    <input type="checkbox" class="cpr-role-cb" value="${esc(role.id)}" data-writes="${writes ? '1' : '0'}" data-asks="${role.asks ? '1' : '0'}" data-planned="${planned ? '1' : '0'}"
                                           ${storedRoles.includes(role.id) && !planned ? 'checked' : ''}>
                                    <span style="color:var(--text-primary);">${esc(role.title)}</span>
                                    <span style="color:var(--text-muted);">${planned ? 'planned' : (writes ? 'reads and may change' : 'reads') + (role.asks ? ' · asks other agents' : '')}</span>
                                </label>
                                <div class="cpr-role-reach" style="margin-left:22px;display:flex;align-items:baseline;gap:6px;flex-wrap:wrap;">
                                    <span>${esc(roleCountsLine(role))}</span>
                                    ${planned ? '' : `<button type="button" class="cpr-role-more" data-role="${esc(role.id)}"
                                        style="background:none;border:none;padding:0;color:var(--text-primary);text-decoration:underline;cursor:pointer;font-size:inherit;">What it reaches</button>`}
                                </div>
                                <div class="cpr-role-detail" data-role="${esc(role.id)}" style="margin-left:22px;" hidden></div>
                            </div>`;
                            }).join('') || '<span>No roles are available.</span>';
                            _platformRow.querySelectorAll('.cpr-role-cb').forEach(cb => cb.addEventListener('change', _platformSync));
                            // What a role reaches and teaches — the same reading as Components → Platform → Roles,
                            // opened in place (the editor is never left with unsaved changes).
                            _platformRow.querySelectorAll('.cpr-role-more').forEach(btn => btn.addEventListener('click', async () => {
                                const box = _platformRow.querySelector(`.cpr-role-detail[data-role="${btn.dataset.role}"]`);
                                if (!box) return;
                                if (!box.hidden) { box.hidden = true; btn.textContent = 'What it reaches'; return; }
                                box.hidden = false; btn.textContent = 'Hide';
                                if (box.dataset.loaded === '1') return;
                                box.textContent = 'Loading…';
                                try {
                                    const full = await fetchPlatformRoles(true);
                                    const role = (full.roles || []).find(x => x.id === btn.dataset.role);
                                    box.innerHTML = role ? roleReachHTML(role) : 'This role is no longer offered.';
                                    box.dataset.loaded = '1';
                                } catch (_) {
                                    box.textContent = 'Could not load what this role reaches.';
                                }
                            }));
                            _platformRow.dataset.loaded = '1';
                        } catch (_) {
                            _platformRow.querySelector('.cpr-roles').textContent = 'Could not load roles — the agent keeps the roles it has.';
                        }
                        _platformSync();
                    })();
                }

                compTogglesContainer.querySelector('.comp-dbnav-open-sources')?.addEventListener('click', () => {
                    modal.querySelector('.profile-nav-item[data-profile-section="data-sources"]')?.click();
                });
                // the Navigator's on/off state is now known — the Data sources section reads it
                if (DataSources.current()) { DataSources.syncFromModal(); DataSources.render(); }

                // Wire toggle → intensity select enable/disable + mutual exclusion
                const conflictsMap = {};
                components.forEach(c => {
                    if (c.conflicts_with?.length) conflictsMap[c.component_id] = c.conflicts_with;
                });
                // ownership: a hand on the switch owns it (the Auto mark drops, Reset appears);
                // Reset drops the agent's setting and the switch returns to the class default
                const _setOwned = (row, owned) => {
                    if (!row) return;
                    row.dataset.owned = owned ? '1' : '0';
                    row.querySelector('[data-comp-auto-badge]')?.classList.toggle('hidden', owned);
                    row.querySelector('[data-comp-reset]')?.classList.toggle('hidden', !owned);
                };
                compTogglesContainer.querySelectorAll('[data-comp-reset]').forEach(btn => {
                    btn.addEventListener('click', (e) => {
                        const row = e.target.closest('[data-comp-row]');
                        const toggle = row?.querySelector('.comp-toggle');
                        if (!toggle) return;
                        toggle.checked = row.dataset.manifestDefault === '1';
                        toggle.dispatchEvent(new Event('change', { bubbles: true }));   // not trusted: takes no ownership
                        _setOwned(row, false);
                    });
                });
                compTogglesContainer.querySelectorAll('.comp-intensity-select, .comp-groundon-select').forEach(sel => {
                    sel.addEventListener('change', (e) => _setOwned(e.target.closest('[data-comp-row]'), true));
                });
                compTogglesContainer.querySelectorAll('.comp-toggle').forEach(toggle => {
                    toggle.addEventListener('change', (e) => {
                        const row = e.target.closest('.flex');
                        if (e.isTrusted) _setOwned(e.target.closest('[data-comp-row]'), true);
                        const intensitySelect = row?.querySelector('.comp-intensity-select');
                        if (intensitySelect) intensitySelect.disabled = !e.target.checked;
                        const groundSelect = row?.querySelector('.comp-groundon-select');
                        if (groundSelect) groundSelect.disabled = !e.target.checked;
                        // a tool opt-in (Files) is meaningful only while its component is on
                        const optin = compTogglesContainer.querySelector(`.comp-tool-optin[data-component-id="${e.target.dataset.componentId}"]`);
                        if (optin) optin.disabled = !e.target.checked;
                        if (e.target.dataset.componentId === 'platform') _platformSync();

                        // Enforce mutual exclusivity: enabling one disables conflicting components
                        if (e.target.checked) {
                            const componentId = e.target.dataset.componentId;
                            (conflictsMap[componentId] || []).forEach(conflictId => {
                                const conflictToggle = compTogglesContainer.querySelector(`.comp-toggle[data-component-id="${conflictId}"]`);
                                if (conflictToggle?.checked) {
                                    conflictToggle.checked = false;
                                    const conflictIntensitySelect = conflictToggle.closest('.flex')?.querySelector('.comp-intensity-select');
                                    if (conflictIntensitySelect) conflictIntensitySelect.disabled = true;
                                }
                            });
                        }
                    });
                });
            } catch (err) {
                console.warn('[Profile Modal] Failed to load components:', err);
                compTogglesContainer.innerHTML = '<p class="text-xs text-gray-500 italic">Could not load components.</p>';
            }
        })();
    }

    // --- Smart Next Steps (recommenderConfig) population ---
    (() => {
        const rec = profile?.recommenderConfig || {};
        const tiers = Array.isArray(rec.tiers) ? rec.tiers : ['action', 'followup'];
        const enabledEl = modal.querySelector('#profile-recommender-enabled');
        if (enabledEl) enabledEl.checked = rec.enabled !== undefined ? !!rec.enabled : true;
        const setTier = (id, val) => { const el = modal.querySelector(id); if (el) el.checked = val; };
        setTier('#rec-tier-action', tiers.includes('action'));
        setTier('#rec-tier-followup', tiers.includes('followup'));
        setTier('#rec-tier-llm', tiers.includes('llm') && !!rec.llmEnrichment);
        const maxEl = modal.querySelector('#profile-recommender-max');
        if (maxEl) maxEl.value = rec.maxSuggestions != null ? rec.maxSuggestions : 4;
        const relEl = modal.querySelector('#profile-recommender-relevance');
        if (relEl) relEl.value = rec.relevanceThreshold != null ? rec.relevanceThreshold : 0.65;
    })();

    // --- Prompt cache (cacheConfig) population — S2 the Attested Cache ---
    (() => {
        const cc = (profile && profile.cacheConfig) || {};
        const en = modal.querySelector('#profile-cache-enabled');
        if (en) en.checked = cc.enabled !== undefined ? !!cc.enabled : true;   // absent = on under the master
        const ttl = modal.querySelector('#profile-cache-ttl');
        if (ttl) ttl.value = (cc.ttl === '1h') ? '1h' : '5m';
    })();

    // The posture-mode ladder — ONE list behind EVERY gate's population validator
    // (mirrors core/posture_modes.py). Never hand-roll a copy: a validator that
    // omits a valid stored mode DISPLAYS the fallback and then silently downgrades
    // the stored value on the next save. That trap has now been introduced twice —
    // first for grounding + outbound guardrail, then again for the inbound
    // guardrail mode when it was exposed in a later wave.
    //
    // 'annotate' and 'review' stay in the RECOGNITION list but are no longer radio
    // rungs: each is a stored COMPOSITE — a posture plus its one property:
    //
    //     annotate = observe + disclosure  (tell the reader what was found)
    //     review   = enforce + retention   (keep the withheld original as owed work)
    //
    // The editor renders TWO postures (Observe / Enforce), each with its property
    // checkbox — the four strings survive byte-identically on disk (zero migration,
    // zero signature churn). The flat 4-rung ladder lied about strictness twice:
    // review/enforce act identically on the answer, and annotate/observe watch
    // identically (disclosure adds words, it never suppresses harm).
    const POSTURE_MODES = ['observe', 'annotate', 'review', 'enforce'];
    const pickMode = (m, dflt = 'observe', allowed = POSTURE_MODES) =>
        allowed.includes(m) ? m : dflt;
    // ONE map gate-radio-group → its two property checkboxes, shared by population
    // AND save (both live in this closure) — the hand-rolled-copy trap ends here.
    const GATE_PROPS = {
        'agent-posture-mode': { disclose: 'agent-posture-annotate-disclose', retain: 'agent-posture-review-retention' },
        'grounding-mode': { disclose: 'grounding-annotate-disclose', retain: 'grounding-review-retention' },
        'grounding-corpus-mode': { disclose: 'grounding-corpus-annotate-disclose', retain: 'grounding-corpus-review-retention' },
        'honesty-mode': { disclose: 'honesty-annotate-disclose', retain: 'honesty-review-retention' },
        'guardrail-mode': { disclose: 'guardrail-annotate-disclose', retain: 'guardrail-review-retention' },
        'guardrail-inbound-mode': { disclose: 'guardrail-inbound-annotate-disclose', retain: 'guardrail-inbound-review-retention' },
    };
    // Each property is live only under its own posture: disclosure under Observe,
    // retention under Enforce. (Under Enforce, disclosure happens automatically
    // where the judge doesn't rule — the fail-safe — so the checkbox has no
    // independent meaning there.)
    const syncGateProps = (group) => {
        const ids = GATE_PROPS[group] || {};
        const sync = (boxId, radioValue) => {
            const box = modal.querySelector(`#${boxId}`);
            if (!box) return;
            const on = !!modal.querySelector(`input[name="${group}"][value="${radioValue}"]`)?.checked;
            box.disabled = !on;
            const lbl = box.closest('label');
            if (lbl) lbl.style.opacity = on ? '' : '0.45';
        };
        sync(ids.disclose, 'observe');
        sync(ids.retain, 'enforce');
    };
    // Populate one gate: stored 'annotate' ⇒ Observe + disclosure checked; stored
    // 'review' ⇒ Enforce + retention checked; anything else through pickMode as
    // before. Also (re)wires the radio-change sync — `onchange` (not
    // addEventListener) so repopulation stays idempotent.
    const applyGateMode = (group, rawMode, dflt = 'observe', extraAllowed = []) => {
        const isAnnotate = rawMode === 'annotate';
        const isReview = rawMode === 'review';
        const mode = isAnnotate ? 'observe' : isReview ? 'enforce'
            : pickMode(rawMode, dflt, [...extraAllowed, ...POSTURE_MODES]);
        const radio = modal.querySelector(`input[name="${group}"][value="${mode}"]`);
        if (radio) radio.checked = true;
        const ids = GATE_PROPS[group] || {};
        const dBox = modal.querySelector(`#${ids.disclose}`);
        if (dBox) {
            dBox.checked = isAnnotate;
            dBox.onchange = () => updatePostureSummary();
        }
        const rBox = modal.querySelector(`#${ids.retain}`);
        if (rBox) {
            rBox.checked = isReview;
            rBox.onchange = () => updatePostureSummary();
        }
        modal.querySelectorAll(`input[name="${group}"]`).forEach((r) => {
            r.onchange = () => { syncGateProps(group); updatePostureSummary(); };
        });
        syncGateProps(group);
    };
    // Save one gate: Observe + disclosure ⇒ 'annotate'; Enforce + retention ⇒
    // 'review' (byte-identical on disk to the retired rungs). '' = Inherit —
    // the save then OMITS the gate's mode key, which IS the stored inherit form.
    const composeGateMode = (group) => {
        const el = modal.querySelector(`input[name="${group}"]:checked`);
        if (!el) return null;
        const ids = GATE_PROPS[group] || {};
        if (el.value === 'observe'
            && modal.querySelector(`#${ids.disclose}`)?.checked) return 'annotate';
        if (el.value === 'enforce'
            && modal.querySelector(`#${ids.retain}`)?.checked) return 'review';
        return el.value;
    };
    // TRANSPARENCY: the Agent-Posture card always names what actually applies —
    // per gate, the current DOM state resolved exactly as the backend resolver
    // (core/posture_modes.effective_posture) will resolve the saved config: an
    // inheriting gate shows the agent default it will run under; an overriding
    // gate shows its own mode. Live — updated on every radio/checkbox change.
    // ONE label map (trustGrammar.POSTURE_LABEL ← core/posture_modes.POSTURE_LABELS):
    // the ring, the posture matrix, the anatomy and this strip all print the same word
    // for one stored mode. The literal is the absence-tolerant fallback, pinned equal.
    const _POSTURE_WORD = Object.assign({ '': 'inherit' },
        (window.TrustGrammar && window.TrustGrammar.POSTURE_LABEL)
            || { off: 'off', observe: 'watch', annotate: 'watch + disclose', enforce: 'enforce',
                 review: 'enforce + keep for review' });
    const updatePostureSummary = () => {
        const out = modal.querySelector('#agent-posture-summary');
        if (!out) return;
        const agentMode = composeGateMode('agent-posture-mode') || 'observe';
        const gateWord = (group) => {
            const m = composeGateMode(group);
            if (m === null) return null;
            if (m === '') {
                if (group === 'grounding-corpus-mode') {
                    const g = composeGateMode('grounding-mode');
                    const eff = g || agentMode;
                    return `follows Grounding (→ ${_POSTURE_WORD[eff] || eff})`;
                }
                return `inherits (→ ${_POSTURE_WORD[agentMode] || agentMode})`;
            }
            return `overrides: ${_POSTURE_WORD[m] || m}`;
        };
        const rows = [
            ['Grounding', gateWord('grounding-mode')],
            ['Corpus', gateWord('grounding-corpus-mode')],
            ['Honesty', gateWord('honesty-mode')],
            ['Guardrail out', gateWord('guardrail-mode')],
            ['Guardrail in', gateWord('guardrail-inbound-mode')],
        ].filter(([, w]) => w !== null);
        out.innerHTML = rows.map(([label, w]) =>
            `<span style="white-space:nowrap;"><span style="color: var(--text-primary);">${label}</span> — ${
                w.startsWith('overrides') ? `<span style="color:#f59e0b;">${w}</span>` : w
            }</span>`).join('<span style="opacity:.4;"> · </span>');
    };

    // --- Agent posture (postureConfig — the default every gate inherits) ---
    // Populated FIRST so the transparency summary resolves inheriting gates
    // against the correct agent default.
    applyGateMode('agent-posture-mode', profile?.postureConfig?.mode);

    // --- Grounding (groundingConfig) population ---
    // Gate default is '' (Inherit): a block with no own mode follows the agent
    // posture — exactly how the backend resolver reads the saved config. An
    // explicitly stored mode (every profile saved by the pre-inherit editor)
    // honestly shows as an override.
    (() => {
        const g = profile?.groundingConfig || {};
        applyGateMode('grounding-mode', g.mode, '');
        // Corpus-support family ladder — '' (Inherit) means "follow the gate mode".
        applyGateMode('grounding-corpus-mode', g.corpus?.mode, '');
    })();

    // --- Honesty (honestyConfig) population ---
    (() => {
        const h = profile?.honestyConfig || {};
        applyGateMode('honesty-mode', h.mode, '', ['off']);
        // grainJudge — the optional grain_faithfulness confirmation (explicit true only)
        const gj = document.getElementById('honesty-grain-judge');
        if (gj) gj.checked = h.grainJudge === true;
    })();

    // --- Outcome Loop (outcomeConfig — S7) population ---
    (() => {
        const o = (profile && profile.outcomeConfig && typeof profile.outcomeConfig === 'object') ? profile.outcomeConfig : {};
        const en = modal.querySelector('#profile-outcome-enabled');
        if (en) en.checked = o.enabled === true;
        const mr = modal.querySelector('#profile-outcome-max-revisions');
        if (mr) mr.value = (Number(o.maxRevisions) === 2) ? '2' : '1';
        const kinds = Array.isArray(o.revisableKinds) ? o.revisableKinds : null;
        for (const k of ['unsupported_claim', 'out_of_scope_ref', 'ungrounded_number']) {
            const el = modal.querySelector(`#profile-outcome-kind-${k}`);
            if (el) el.checked = kinds === null ? true : kinds.includes(k);
        }
    })();

    // --- Attachment Sources (attachmentConfig — S9) population ---
    (() => {
        const a = (profile && profile.attachmentConfig && typeof profile.attachmentConfig === 'object') ? profile.attachmentConfig : {};
        const el = modal.querySelector('#profile-attachment-transcribe');
        if (el) el.checked = a.transcribeImages === true;
    })();

    // --- Judges (judgeConfig — the per-profile JUDGED station) population ---
    (() => {
        const j = profile?.judgeConfig || {};
        // Not a posture ladder — a tri-state inherit/on/off; never run through pickMode.
        const mode = (j.judges === 'on' || j.judges === 'off') ? j.judges : '';
        const el = modal.querySelector(`input[name="profile-judge-mode"][value="${mode}"]`);
        if (el) el.checked = true;
        const rateEl = modal.querySelector('#profile-judge-sample-rate');
        if (rateEl) rateEl.value = (j.sampleRate ?? '') === '' || j.sampleRate == null ? '' : j.sampleRate;
        const inboxEl = modal.querySelector('#profile-judge-inbox');
        if (inboxEl) inboxEl.checked = j.inboxOnCondemnation !== false;   // default on
    })();

    // --- Guardrails (guardrailConfig.outbound + .inbound) population ---
    (() => {
        const ob = (profile?.guardrailConfig?.outbound) || {};
        applyGateMode('guardrail-mode', ob.mode, '');
        const piiEl = modal.querySelector('#profile-guardrail-pii');
        if (piiEl) piiEl.checked = ob.pii !== false;        // default on
        const toxEl = modal.querySelector('#profile-guardrail-toxicity');
        if (toxEl) toxEl.checked = ob.toxicity !== false;   // default on
        // Inbound (prompt-injection) mode — the backend adapter has resolved
        // guardrailConfig.inbound.mode all along; now exposed in the editor
        // (closes the manual's documented "not yet exposed" limit).
        const ib = (profile?.guardrailConfig?.inbound) || {};
        applyGateMode('guardrail-inbound-mode', ib.mode, '');
        updatePostureSummary();
    })();

    // --- #25 · a card whose runtime is OFF must not look live -------------------
    // With a deployment master off both runtimes short-circuit, yet these cards
    // rendered four radios and two green toggles with the dependency in an 11px
    // footnote. The modes stay EDITABLE (pre-configuring is legitimate); the card
    // just says the truth: stored, not running. Masters ride the evals-dashboard
    // payload (the ring reads the same source); absence = unknown ⇒ no banner.
    _applyMasterBanners(modal);

    // --- Turn Budget (S5 — turnBudget {steps, tokens, seconds}) population ---
    (() => {
        const tb = (profile && profile.turnBudget && typeof profile.turnBudget === 'object') ? profile.turnBudget : {};
        for (const unit of ['steps', 'tokens', 'seconds']) {
            const el = modal.querySelector(`#profile-turn-budget-${unit}`);
            if (el) el.value = (tb[unit] != null && tb[unit] !== '' && Number(tb[unit]) > 0) ? tb[unit] : '';
        }
    })();

    // --- Agent Authority (authorityConfig; legacy identityConfig still read) population ---
    (() => {
        const ic = profile?.authorityConfig || profile?.identityConfig || {};
        const autoEl = modal.querySelector('#profile-authority-autonomous');
        if (autoEl) autoEl.checked = ic.autonomous !== false;   // default on
        const dwEl = modal.querySelector('#profile-authority-deny-writes');
        if (dwEl) dwEl.checked = !!ic.deny_writes;
        const dtEl = modal.querySelector('#profile-authority-denied-tools');
        if (dtEl) dtEl.value = Array.isArray(ic.denied_tools) ? ic.denied_tools.join(', ') : '';
        const conEl = modal.querySelector('#profile-authority-connectors');
        if (conEl) conEl.value = Array.isArray(ic.allowed_connectors) ? ic.allowed_connectors.join(', ') : '';
        const budEl = modal.querySelector('#profile-authority-budget');
        if (budEl) budEl.value = (ic.max_cost_per_run != null && ic.max_cost_per_run !== '') ? ic.max_cost_per_run : '';
        // Four eyes (PROP-008 §5.4a): the designated approvers of this agent's parked writes
        const apEl = modal.querySelector('#profile-authority-approvers');
        if (apEl) {
            const ap = (ic.actions && typeof ic.actions === 'object') ? ic.actions.approvers : null;
            apEl.value = Array.isArray(ap) ? ap.join(', ') : (typeof ap === 'string' ? ap : '');
        }
        // Data surface (OBDA-READ Phase C — E1–E4)
        const ds = (ic.dataSurface && typeof ic.dataSurface === 'object') ? ic.dataSurface : {};
        const elAuthInteractive = document.getElementById('profile-authority-enforce-interactive');
        if (elAuthInteractive) elAuthInteractive.checked = ic.enforce_interactive === true;
        // Needs input (PROP-011) — `authorityConfig.inputPolicy`. Every value the runtime
        // accepts (input_requests.policy_for) is listed, so a stored mode is DISPLAYED, never
        // shown as the fallback and then persisted as it.
        (() => {
            const ip = (ic.inputPolicy && typeof ic.inputPolicy === 'object') ? ic.inputPolicy : {};
            const setSel = (id, v, allowed, dflt) => {
                const el = modal.querySelector(id);
                if (!el) return;
                const s = String(v || '').toLowerCase();
                el.value = allowed.includes(s) ? s : dflt;
            };
            setSel('#profile-input-interactive', ip.interactive, ['ask', 'never'], 'ask');
            setSel('#profile-input-unattended', ip.unattended, ['park', 'fail'], 'park');
            setSel('#profile-input-raisers', ip.raisers, ['enforce', 'observe'], 'enforce');
            const maxEl = modal.querySelector('#profile-input-max');
            if (maxEl) maxEl.value = (Number.isInteger(ip.maxPerTurn) && ip.maxPerTurn >= 1) ? ip.maxPerTurn : '';
            const defEl = modal.querySelector('#profile-input-defaults');
            if (defEl) defEl.value = (ip.defaults && typeof ip.defaults === 'object')
                ? Object.entries(ip.defaults).map(([k, v]) => `${k} = ${typeof v === 'string' ? v : JSON.stringify(v)}`).join('\n')
                : '';
        })();
        const dsProdEl = modal.querySelector('#profile-authority-surface-products');
        if (dsProdEl) dsProdEl.value = Array.isArray(ds.products) ? ds.products.join(', ') : '';
        // the tri-state (operator model): A = no dataSurface (default) ·
        // B = exclusive · C = products without exclusive. An empty stored list
        // reads as A — the runtime (obda.profile_obda) treats it as off.
        const dsMode = (Array.isArray(ds.products) && ds.products.length)
            ? (ds.exclusive ? 'exclusive' : 'mixed') : 'off';
        const dsModeEl = modal.querySelector(
            `input[name="profile-obda-mode"][value="${dsMode}"]`);
        if (dsModeEl) dsModeEl.checked = true;
        const dsAdvEl = modal.querySelector('#profile-authority-surface-advisory');
        if (dsAdvEl) dsAdvEl.checked = !!ds.advisory;
    })();

    // Set classification mode
    const classificationMode = profile?.classification_mode || 'light';
    const modeRadio = modal.querySelector(`input[name="classification-mode"][value="${classificationMode}"]`);
    if (modeRadio) {
        modeRadio.checked = true;
    }

    const toolsContainer = modal.querySelector('#profile-modal-tools');
    const promptsContainer = modal.querySelector('#profile-modal-prompts');
    let allTools = [];
    let allPrompts = [];
    // 'loading' | 'ok' | 'error' per kind — the save carries the stored selection forward
    // unless the list actually loaded (resolveSavedSelection).
    const resourceLoad = { tools: 'loading', prompts: 'loading' };

    async function populateResources(mcpServerId) {
        const server = configState.mcpServers.find(s => s.id === mcpServerId);
        resourceLoad.tools = 'loading';
        resourceLoad.prompts = 'loading';
        if (!server) {
            resourceLoad.tools = 'error';
            resourceLoad.prompts = 'error';
            toolsContainer.innerHTML = '<span class="text-gray-400">Select an MCP server.</span>';
            promptsContainer.innerHTML = '<span class="text-gray-400">Select an MCP server.</span>';
            return;
        }

        toolsContainer.innerHTML = '<span class="text-gray-400">Loading...</span>';
        promptsContainer.innerHTML = '<span class="text-gray-400">Loading...</span>';

        try {
            const resources = await API.fetchResourcesForServer(server);
            allTools = Object.values(resources.tools || {}).flat().map(t => t.name);
            allPrompts = Object.values(resources.prompts || {}).flat().map(p => p.name);

            // For new profiles (isEdit=false), default all to checked
            // For existing profiles with tools=null/undefined (not yet configured), default all to checked
            // For existing profiles with [] (explicitly all disabled), respect that and show all unchecked
            // For existing profiles with ['*'] or a populated array, respect their saved selections
            const toolsNotYetConfigured = profile && (profile.tools === null || profile.tools === undefined);
            const promptsNotYetConfigured = profile && (profile.prompts === null || profile.prompts === undefined);

            toolsContainer.innerHTML = allTools.map(tool => `
                <label class="ind-toggle ind-toggle--sm gap-2 text-sm text-gray-300 w-full">
                    <input type="checkbox" value="${escapeHtml(tool)}" ${!isEdit || toolsNotYetConfigured || profile?.tools?.includes(tool) || profile?.tools?.includes('*') ? 'checked' : ''}>
                    <span class="ind-track flex-shrink-0"></span>
                    <span>${escapeHtml(tool)}</span>
                </label>
            `).join('') || '<span class="text-gray-400">No tools found.</span>';

            promptsContainer.innerHTML = allPrompts.map(prompt => `
                <label class="ind-toggle ind-toggle--sm gap-2 text-sm text-gray-300 w-full">
                    <input type="checkbox" value="${escapeHtml(prompt)}" ${!isEdit || promptsNotYetConfigured || profile?.prompts?.includes(prompt) || profile?.prompts?.includes('*') ? 'checked' : ''}>
                    <span class="ind-track flex-shrink-0"></span>
                    <span>${escapeHtml(prompt)}</span>
                </label>
            `).join('') || '<span class="text-gray-400">No prompts found.</span>';
            resourceLoad.tools = 'ok';
            resourceLoad.prompts = resources.prompts_error ? 'error' : 'ok';
            if (resources.prompts_error) {
                promptsContainer.innerHTML = `<span class="text-red-400">Prompts could not be listed: ${escapeHtml(resources.prompts_error)} — the saved prompt selection is kept unchanged.</span>`;
            }
        } catch (error) {
            resourceLoad.tools = 'error';
            resourceLoad.prompts = 'error';
            toolsContainer.innerHTML = `<span class="text-red-400">Error: ${error.message}</span>`;
            promptsContainer.innerHTML = `<span class="text-red-400">Error: ${error.message}</span>`;
        }
    }
    
    // Note: MCP select onchange handler is set AFTER renderCollections is defined (see below)

    // Initial population - use the ACTUAL selected value from the dropdown
    // (not the first server in the array, which may differ from activeMCP)
    const initialMcpId = mcpSelect.value;
    if (initialMcpId) {
        populateResources(initialMcpId);
    } else {
        toolsContainer.innerHTML = '<span class="text-gray-400">No MCP servers configured.</span>';
        promptsContainer.innerHTML = '<span class="text-gray-400">No MCP servers configured.</span>';
    }


    // Populate Intelligence Collections (Planner and Knowledge Repositories)
    const plannerContainer = modal.querySelector('#profile-modal-planner-collections');
    // reset the render-completion stamps on every open — a PREVIOUS open's
    // stamp claiming "rendered" would let an early save on THIS open collect
    // empties again (the exact wipe this stamp exists to prevent)
    if (plannerContainer) delete plannerContainer.dataset.collectionsRendered;
    const knowledgeContainer = modal.querySelector('#profile-modal-knowledge-collections');
    if (knowledgeContainer) delete knowledgeContainer.dataset.collectionsRendered;

    const { collections: ragCollections } = await API.getRagCollections(true);

    // Separate collections by repository type
    const plannerCollections = ragCollections.filter(coll => coll.repository_type === 'planner');
    const knowledgeCollections = ragCollections.filter(coll => coll.repository_type === 'knowledge');

    // Function to calculate default collection for a given MCP server
    // OC-F5: an Optimize agent with its OWN learning repository (a planner collection carrying
    // its id in learning_profile_id) learns and reads there — the shared default is not bound
    // for it, and its own repository is shown as a fixed row that is NOT saved as a binding (so
    // its contents never enter the agent's signature). A NEW agent gets one at creation. Agents
    // without one keep the shared default exactly as before. Rule: core/agent_learning.py.
    const ownLearningRepos = plannerCollections.filter(c => profile && c.learning_profile_id === profile.id);
    const learnsOwn = isEdit ? ownLearningRepos.length > 0 : true;

    const calculateDefaultCollectionId = (mcpServerId) => {
        if (!mcpServerId) return null;
        // one agent's learning repository is never the shared default (core/agent_learning)
        const serverPlannerCollections = plannerCollections.filter(c => c.mcp_server_id === mcpServerId
                                                                    && !c.learning_profile_id);
        if (serverPlannerCollections.length === 0) return null;
        // Sort by ID (ascending) and return the first (oldest)
        const sorted = serverPlannerCollections.sort((a, b) => a.id - b.id);
        return sorted[0].id;
    };

    // Determine the profile's MCP server ID (for new profiles, use active MCP)
    let currentMcpServerId = profile ? profile.mcpServerId : (configState.activeMCP || configState.mcpServers[0]?.id);
    let defaultCollectionId = calculateDefaultCollectionId(currentMcpServerId);

    console.log('[Profile Modal] Profile MCP Server ID:', currentMcpServerId);
    console.log('[Profile Modal] Default Collection ID:', defaultCollectionId);

    // Note: Global Knowledge Repository Configuration has been moved to Administration panel
    // Per-collection reranking toggles remain in the profile modal
    const rerankingListContainer = modal.querySelector('#profile-modal-knowledge-reranking-list');

    // Update reranking list when knowledge collections change (must be defined before renderCollections)
    const updateRerankingList = () => {
        const selectedKnowledgeCheckboxes = knowledgeContainer.querySelectorAll('input[data-collection-type="query"]:checked');
        const selectedKnowledgeIds = Array.from(selectedKnowledgeCheckboxes).map(cb => parseInt(cb.dataset.collectionId));

        if (selectedKnowledgeIds.length === 0) {
            rerankingListContainer.innerHTML = '<span class="text-gray-400 text-sm">Select knowledge collections above to configure reranking</span>';
            return;
        }

        const existingReranking = profile?.knowledgeConfig?.collections || [];

        rerankingListContainer.innerHTML = selectedKnowledgeIds.map(collId => {
            const collection = knowledgeCollections.find(c => c.id === collId);
            const collectionConfig = existingReranking.find(c => c.id === collId);
            const isRerankingEnabled = collectionConfig?.reranking === true;

            return `
                <div class="flex items-center justify-between px-4 py-2.5 bg-gray-800/30 hover:bg-gray-800/50 rounded-lg border border-gray-700/30 hover:border-gray-600/50 transition-all">
                    <span class="text-sm text-gray-200">${escapeHtml(collection?.name || `Collection ${collId}`)}</span>
                    <div class="flex items-center gap-3">
                        <label class="ind-toggle ind-toggle--sm">
                            <input type="checkbox" data-collection-id="${collId}" data-reranking="true" ${isRerankingEnabled ? 'checked' : ''}>
                            <span class="ind-track"></span>
                        </label>
                        <span class="text-xs font-medium text-gray-400">Rerank</span>
                    </div>
                </div>
            `;
        }).join('');
    };

    // Helper function to create collection entry with toggles
    const ownLearningRowHTML = (coll) => `
            <div class="flex items-center justify-between py-2.5 rounded-lg border" data-own-learning-repository="${coll.id}"
                 style="padding-left: 16px; padding-right: 16px; background: rgba(147,51,234,0.06); border-color: rgba(147,51,234,0.25);"
                 title="This agent learns only here. Not a binding, so what it learns never changes its signature.">
                <div class="flex items-center gap-2 flex-1 min-w-0">
                    <span class="text-sm truncate" style="color: var(--text-primary);">${escapeHtml(coll.name)}</span>
                    <span class="inline-flex items-center px-2 py-0.5 ml-2 text-xs font-semibold rounded-full border"
                          style="color: #c084fc; background: rgba(147,51,234,0.12); border-color: rgba(147,51,234,0.3);">LEARNS HERE</span>
                    ${coll.trust_locked ? '<span class="text-xs" style="color: var(--text-muted);">locked · not learning</span>' : ''}
                </div>
                <span class="text-xs flex-shrink-0" style="color: var(--text-muted);">own repository</span>
            </div>`;

    const ownLearningNoteHTML = (text) => `
            <div class="flex items-center py-2 rounded-lg border" data-own-learning-note
                 style="padding-left: 16px; padding-right: 16px; border-color: rgba(147,51,234,0.25); color: var(--text-muted);">
                <span class="text-xs">${escapeHtml(text)}</span>
            </div>`;

    const createCollectionEntry = (coll, isPlanner) => {
        // The agent's own learning repository: a fixed row, never a binding (OC-F5)
        if (isPlanner && profile && coll.learning_profile_id === profile.id) {
            return coll.mcp_server_id === currentMcpServerId ? ownLearningRowHTML(coll) : '';
        }
        // Check if this is the default collection for the profile's MCP server — bound only for an
        // agent that still learns into the shared notebook
        const isDefaultCollection = isPlanner && !learnsOwn && coll.id === defaultCollectionId;

        // For new profiles (!isEdit), only default collection is enabled
        // For existing profiles being edited (isEdit=true), respect their saved selections
        // DEFAULT COLLECTIONS: Always enabled and cannot be disabled
        let isQueryEnabled;
        if (isPlanner) {
            if (isDefaultCollection) {
                // Default collection is ALWAYS enabled (cannot be disabled)
                isQueryEnabled = true;
            } else if (!isEdit) {
                // New profiles: only default collection is checked
                isQueryEnabled = false;
            } else {
                // Existing profiles: check if collection is in ragCollections array or wildcard.
                // BUT only inherit the saved selection when the collection belongs to the
                // currently selected MCP server. If the user switched to a different MCP
                // server, collections from the old server must be auto-deselected so they
                // are not carried over into the saved profile (which would block deletion
                // of the old MCP server due to the collection reference check).
                const collectionBelongsToCurrentServer = !coll.mcp_server_id || coll.mcp_server_id === currentMcpServerId;
                isQueryEnabled = collectionBelongsToCurrentServer &&
                    (profile?.ragCollections?.includes(coll.id) || profile?.ragCollections?.includes('*'));
            }
        } else {
            // Knowledge repository: check if it's in knowledgeConfig.collections
            const knowledgeCollectionIds = profile?.knowledgeConfig?.collections?.map(c => c.id) || [];
            if (!isEdit) {
                // New profiles: only default collection is checked
                isQueryEnabled = isDefaultCollection;
            } else {
                isQueryEnabled = knowledgeCollectionIds.includes(coll.id);
            }
        }

        // For autocomplete, also enable only default collection for new profiles
        // Note: Knowledge repositories DON'T support autocomplete (they lack user_query metadata)
        let isAutocompleteEnabled;
        if (isPlanner) {
            if (!isEdit) {
                // New profiles: only default collection has autocomplete enabled
                isAutocompleteEnabled = isDefaultCollection;
            } else {
                // Existing profiles: respect saved selections, but only for collections
                // belonging to the currently selected MCP server (same reason as query above).
                const collectionBelongsToCurrentServer = !coll.mcp_server_id || coll.mcp_server_id === currentMcpServerId;
                isAutocompleteEnabled = collectionBelongsToCurrentServer &&
                    (profile?.autocompleteCollections?.includes(coll.id) || profile?.autocompleteCollections?.includes('*'));
            }
        }

        // Build autocomplete toggle HTML only for Planner repositories
        const autocompleteToggleHTML = isPlanner ? `
            <!-- Autocomplete Toggle -->
            <div class="flex items-center justify-center" style="width: 50px;">
                <label class="ind-toggle">
                    <input type="checkbox"
                           data-collection-id="${coll.id}"
                           data-collection-type="autocomplete"
                           ${isAutocompleteEnabled ? 'checked' : ''}>
                    <span class="ind-track"></span>
                </label>
            </div>
        ` : '';

        // Build the default badge if this is the default collection
        const defaultBadgeHTML = isDefaultCollection ? `
            <span class="inline-flex items-center px-2 py-0.5 ml-2 text-xs font-semibold bg-blue-500/20 text-blue-300 border border-blue-400/30 rounded-full" title="Default collection for new patterns">
                DEFAULT
            </span>
        ` : '';

        return `
            <div class="flex items-center justify-between py-2.5 bg-gray-800/30 hover:bg-gray-800/50 rounded-lg border border-gray-700/30 hover:border-gray-600/50 transition-all group" style="padding-left: 16px; padding-right: 16px;">
                <div class="flex items-center gap-2 flex-1 min-w-0">
                    <span class="text-sm text-gray-200 group-hover:text-white transition-colors truncate">${escapeHtml(coll.name)}</span>
                    <span class="inline-flex items-center flex-shrink-0" data-trust-artifact-kind="planner" data-trust-artifact-id="${coll.id}"></span>
                    ${defaultBadgeHTML}
                </div>
                <div class="flex items-center gap-8 flex-shrink-0">
                    <!-- Query Toggle -->
                    <div class="flex items-center justify-center planner-query-toggle" style="width: 50px;" ${isDefaultCollection ? 'title="Default collection cannot be disabled - it stores new discoveries"' : ''}>
                        <label class="ind-toggle ${isDefaultCollection ? 'ind-toggle--info' : ''}">
                            <input type="checkbox"
                                   data-collection-id="${coll.id}"
                                   data-collection-type="query"
                                   ${isQueryEnabled ? 'checked' : ''}
                                   ${isDefaultCollection ? 'disabled' : ''}>
                            <span class="ind-track"></span>
                        </label>
                    </div>
                    ${autocompleteToggleHTML}
                </div>
            </div>
        `;
    };

    // Function to render collections (called on initial load and when MCP server changes)
    const renderCollections = () => {
        // Recalculate default collection based on current MCP server
        currentMcpServerId = mcpSelect.value;
        defaultCollectionId = calculateDefaultCollectionId(currentMcpServerId);

        console.log('[Profile Modal] Rendering collections for MCP Server:', currentMcpServerId);
        console.log('[Profile Modal] Default Collection ID:', defaultCollectionId);

        // Render Planner Collections
        let plannerHTML = plannerCollections.length > 0
            ? plannerCollections.map(coll => createCollectionEntry(coll, true)).join('')
            : '<span class="text-gray-400 text-sm px-3 py-2 block">No planner repositories found.</span>';
        if (learnsOwn && !ownLearningRepos.some(c => c.mcp_server_id === currentMcpServerId)) {
            plannerHTML = ownLearningNoteHTML(isEdit
                ? 'Learns into its own planner repository — created on its first lesson on this server.'
                : 'Learns into its own planner repository — created with the agent.') + plannerHTML;
        }
        console.log('[Profile Modal] Planner HTML length:', plannerHTML.length);
        console.log('[Profile Modal] Planner collections count:', plannerCollections.length);
        plannerContainer.innerHTML = plannerHTML;
        // the render-completion stamp: a save that fires BEFORE this render (or
        // after a failed one) must carry the stored selections forward instead
        // of collecting zero checkboxes — an empty UI is not an empty choice
        // (found live: one early/stale-tab save wiped a profile's
        // autocompleteCollections to [] and silently killed its query
        // suggestions, beside the dataSurface wipe from the same save)
        plannerContainer.dataset.collectionsRendered = '1';
        if (window.TrustBadge) window.TrustBadge.mountArtifactBadges(plannerContainer);

        // Render Knowledge Collections
        const knowledgeHTML = knowledgeCollections.length > 0
            ? knowledgeCollections.map(coll => createCollectionEntry(coll, false)).join('')
            : '<span class="text-gray-400 text-sm px-3 py-2 block">No knowledge repositories found.</span>';
        console.log('[Profile Modal] Knowledge HTML length:', knowledgeHTML.length);
        console.log('[Profile Modal] Knowledge collections count:', knowledgeCollections.length);
        knowledgeContainer.innerHTML = knowledgeHTML;
        knowledgeContainer.dataset.collectionsRendered = '1';

        // Update reranking list after collections change
        updateRerankingList();
    };

    // Initial render
    renderCollections();

    // Render Knowledge Graphs section (only for existing profiles)
    const kgSection = modal.querySelector('#profile-modal-kg-section');
    const kgList = modal.querySelector('#profile-modal-kg-list');
    if (kgSection && kgList && isEdit && profile?.id) {
        await renderKnowledgeGraphsForProfile(profile.id, kgSection, kgList);
    }

    // NOW set the MCP select onchange handler (after renderCollections is defined)
    mcpSelect.onchange = () => {
        populateResources(mcpSelect.value);
        // Re-render collections to update default collection for new MCP server
        renderCollections();
        // Re-apply section visibility to maintain autocomplete-only mode
        // for non-tool_enabled profiles (renderCollections replaces DOM elements)
        updateSectionVisibility(profileType);
    };

    // Update reranking list when knowledge collection checkboxes change
    knowledgeContainer.addEventListener('change', (e) => {
        if (e.target.dataset.collectionType === 'query') {
            updateRerankingList();
        }
    });

    // Set profile name, tag and description
    const profileNameInput = modal.querySelector('#profile-modal-name');
    const profileTagInput = modal.querySelector('#profile-modal-tag');
    const profileDescInput = modal.querySelector('#profile-modal-description');
    
    profileNameInput.value = profile ? (profile.name || '') : '';
    profileTagInput.value = profile ? (profile.tag || '').replace('@', '') : '';
    profileDescInput.value = profile ? profile.description : '';

    // Set session primer fields with state preservation support
    const sessionPrimerCheckbox = modal.querySelector('#profile-modal-enable-primer');
    const sessionPrimerContainer = modal.querySelector('#profile-modal-primer-container');

    if (sessionPrimerCheckbox && sessionPrimerContainer) {
        let primerConfig = null;

        // Backward compatibility: convert string to new format
        if (profile && profile.session_primer) {
            if (typeof profile.session_primer === 'string') {
                primerConfig = {
                    enabled: true,  // Old format assumes enabled
                    mode: 'combined',
                    statements: [profile.session_primer]
                };
            } else {
                primerConfig = profile.session_primer;
                // Handle old object format without 'enabled' field
                if (primerConfig.enabled === undefined) {
                    primerConfig.enabled = true;  // Default to enabled if field missing
                }
            }
        }

        const hasPrimerData = primerConfig !== null && primerConfig.statements?.length > 0;
        const isEnabled = primerConfig?.enabled || false;

        // Set checkbox state
        sessionPrimerCheckbox.checked = isEnabled;
        sessionPrimerContainer.classList.toggle('hidden', !isEnabled);

        // Load execution mode and statements (even if disabled, for recall)
        if (hasPrimerData) {
            const modeRadios = modal.querySelectorAll('input[name="primer-mode"]');
            modeRadios.forEach(radio => {
                radio.checked = radio.value === primerConfig.mode;
            });
            // Briefing vs warm-up (agent/primer.py): absent = warm-up (tools allowed) —
            // the pre-existing behaviour; only an explicit false withholds tools.
            const briefingBox = modal.querySelector('#profile-modal-primer-briefing');
            if (briefingBox) briefingBox.checked = primerConfig.allow_tools === false;
            // Fold the primer turn in the chat log (ui.js:primerCollapsedFor reads the same
            // key): presentation only, absent = shown in full — the pre-existing behaviour.
            const collapsedBox = modal.querySelector('#profile-modal-primer-collapsed');
            if (collapsedBox) collapsedBox.checked = primerConfig.collapsed === true;

            // Render statements (always, even if disabled - they're just hidden)
            renderPrimerStatements(primerConfig.statements);
        }

        // Toggle handler with state preservation
        sessionPrimerCheckbox.onchange = () => {
            const isEnabled = sessionPrimerCheckbox.checked;
            sessionPrimerContainer.classList.toggle('hidden', !isEnabled);

            // IMPORTANT: Don't clear statements when disabled - preserve for recall
            if (isEnabled) {
                // Check if there are existing statements to restore
                const container = document.getElementById('primer-statements-container');
                if (!container.querySelector('textarea')) {
                    // No statements yet - initialize with one empty or restore from saved
                    const statementsToRestore = hasPrimerData && primerConfig.statements.length > 0
                        ? primerConfig.statements
                        : [''];
                    renderPrimerStatements(statementsToRestore);
                }
                // Otherwise, statements are already rendered and preserved
            }
            // When disabled: Keep statements in DOM (just hidden) for easy recall
        };
    }

    // Set advanced knowledge configuration fields
    const minRelevanceInput = modal.querySelector('#profile-modal-min-relevance');
    const maxDocsInput = modal.querySelector('#profile-modal-max-docs');
    const maxTokensInput = modal.querySelector('#profile-modal-max-tokens');
    const maxChunksPerDocInput = modal.querySelector('#profile-modal-max-chunks-per-doc');
    const freshnessWeightInput = modal.querySelector('#profile-modal-freshness-weight');
    const freshnessDecayRateInput = modal.querySelector('#profile-modal-freshness-decay-rate');
    const synthesisPromptInput = modal.querySelector('#profile-modal-synthesis-prompt');
    const minRelevanceLockedBadge = modal.querySelector('#profile-knowledge-min-relevance-locked-badge');
    const maxDocsLockedBadge = modal.querySelector('#profile-knowledge-max-docs-locked-badge');
    const maxTokensLockedBadge = modal.querySelector('#profile-knowledge-max-tokens-locked-badge');
    const maxChunksPerDocLockedBadge = modal.querySelector('#profile-knowledge-max-chunks-per-doc-locked-badge');
    const freshnessWeightLockedBadge = modal.querySelector('#profile-knowledge-freshness-weight-locked-badge');
    const freshnessDecayLockedBadge = modal.querySelector('#profile-knowledge-freshness-decay-locked-badge');
    const synthesisPromptLockedBadge = modal.querySelector('#profile-knowledge-synthesis-prompt-locked-badge');

    // Fetch global settings from database to check for locks and set placeholders
    let knowledgeGlobalSettings = {};
    try {
        const token = localStorage.getItem('tda_auth_token');
        if (token) {
            const response = await fetch('/api/v1/admin/knowledge-global-settings', {
                headers: { 'Authorization': `Bearer ${token}` }
            });
            if (response.ok) {
                const data = await response.json();
                knowledgeGlobalSettings = data.settings || {};
            }
        }
    } catch (error) {
        console.warn('[Profile Modal] Failed to fetch global knowledge settings:', error);
    }

    // Configure min relevance score
    if (minRelevanceInput) {
        const isLocked = knowledgeGlobalSettings.minRelevanceScore?.is_locked || false;
        const globalValue = knowledgeGlobalSettings.minRelevanceScore?.value ?? 0.30;
        const profileValue = profile?.knowledgeConfig?.minRelevanceScore;

        if (isLocked) {
            minRelevanceInput.value = globalValue;
            minRelevanceInput.disabled = true;
            if (minRelevanceLockedBadge) minRelevanceLockedBadge.classList.remove('hidden');
        } else {
            minRelevanceInput.value = profileValue !== undefined ? profileValue : '';
            minRelevanceInput.placeholder = String(globalValue);
            minRelevanceInput.disabled = false;
            if (minRelevanceLockedBadge) minRelevanceLockedBadge.classList.add('hidden');
        }
    }

    // Configure max docs
    if (maxDocsInput) {
        const isLocked = knowledgeGlobalSettings.maxDocs?.is_locked || false;
        const globalValue = knowledgeGlobalSettings.maxDocs?.value ?? 3;
        const profileValue = profile?.knowledgeConfig?.maxDocs;

        if (isLocked) {
            maxDocsInput.value = globalValue;
            maxDocsInput.disabled = true;
            if (maxDocsLockedBadge) maxDocsLockedBadge.classList.remove('hidden');
        } else {
            maxDocsInput.value = profileValue !== undefined ? profileValue : '';
            maxDocsInput.placeholder = String(globalValue);
            maxDocsInput.disabled = false;
            if (maxDocsLockedBadge) maxDocsLockedBadge.classList.add('hidden');
        }
    }

    // Configure max tokens
    if (maxTokensInput) {
        const isLocked = knowledgeGlobalSettings.maxTokens?.is_locked || false;
        const globalValue = knowledgeGlobalSettings.maxTokens?.value ?? 2000;
        const profileValue = profile?.knowledgeConfig?.maxTokens;

        if (isLocked) {
            maxTokensInput.value = globalValue;
            maxTokensInput.disabled = true;
            if (maxTokensLockedBadge) maxTokensLockedBadge.classList.remove('hidden');
        } else {
            maxTokensInput.value = profileValue !== undefined ? profileValue : '';
            maxTokensInput.placeholder = String(globalValue);
            maxTokensInput.disabled = false;
            if (maxTokensLockedBadge) maxTokensLockedBadge.classList.add('hidden');
        }
    }

    // Configure max chunks per document
    if (maxChunksPerDocInput) {
        const isLocked = knowledgeGlobalSettings.maxChunksPerDocument?.is_locked || false;
        const globalValue = knowledgeGlobalSettings.maxChunksPerDocument?.value ?? 0;
        const profileValue = profile?.knowledgeConfig?.maxChunksPerDocument;

        if (isLocked) {
            maxChunksPerDocInput.value = globalValue;
            maxChunksPerDocInput.disabled = true;
            if (maxChunksPerDocLockedBadge) maxChunksPerDocLockedBadge.classList.remove('hidden');
        } else {
            maxChunksPerDocInput.value = profileValue !== undefined ? profileValue : '';
            maxChunksPerDocInput.placeholder = String(globalValue);
            maxChunksPerDocInput.disabled = false;
            if (maxChunksPerDocLockedBadge) maxChunksPerDocLockedBadge.classList.add('hidden');
        }
    }

    // Configure freshness weight
    if (freshnessWeightInput) {
        const isLocked = knowledgeGlobalSettings.freshnessWeight?.is_locked || false;
        const globalValue = knowledgeGlobalSettings.freshnessWeight?.value ?? 0.0;
        const profileValue = profile?.knowledgeConfig?.freshnessWeight;

        if (isLocked) {
            freshnessWeightInput.value = globalValue;
            freshnessWeightInput.disabled = true;
            if (freshnessWeightLockedBadge) freshnessWeightLockedBadge.classList.remove('hidden');
        } else {
            freshnessWeightInput.value = profileValue !== undefined ? profileValue : '';
            freshnessWeightInput.placeholder = String(globalValue);
            freshnessWeightInput.disabled = false;
            if (freshnessWeightLockedBadge) freshnessWeightLockedBadge.classList.add('hidden');
        }
    }

    // Configure retrieval sensitivity ceiling + retrieval-sufficiency threshold.
    // Both are strictness controls that previously had no editor field at all — they were
    // silently deleted on every save (see TIER_5.2_POSTURE.md W1).
    (() => {
        const sensEl = modal.querySelector('#profile-knowledge-max-sensitivity');
        if (sensEl) sensEl.value = profile?.knowledgeConfig?.maxSensitivity || '';
        const rskEl = modal.querySelector('#profile-knowledge-require-signed');
        if (rskEl) rskEl.checked = !!profile?.knowledgeConfig?.requireSignedKnowledge;  // K5
        const suffEl = modal.querySelector('#profile-knowledge-sufficiency');
        if (suffEl) {
            const v = profile?.knowledgeConfig?.sufficiencyThreshold;
            suffEl.value = (v !== undefined && v !== null) ? v : '';
        }
    })();

    // Configure freshness decay rate
    if (freshnessDecayRateInput) {
        const isLocked = knowledgeGlobalSettings.freshnessDecayRate?.is_locked || false;
        const globalValue = knowledgeGlobalSettings.freshnessDecayRate?.value ?? 0.005;
        const profileValue = profile?.knowledgeConfig?.freshnessDecayRate;

        if (isLocked) {
            freshnessDecayRateInput.value = globalValue;
            freshnessDecayRateInput.disabled = true;
            if (freshnessDecayLockedBadge) freshnessDecayLockedBadge.classList.remove('hidden');
        } else {
            freshnessDecayRateInput.value = profileValue !== undefined ? profileValue : '';
            freshnessDecayRateInput.placeholder = String(globalValue);
            freshnessDecayRateInput.disabled = false;
            if (freshnessDecayLockedBadge) freshnessDecayLockedBadge.classList.add('hidden');
        }
    }

    // Configure synthesis prompt override
    if (synthesisPromptInput) {
        const isLocked = knowledgeGlobalSettings.synthesisPromptOverride?.is_locked || false;
        const globalValue = knowledgeGlobalSettings.synthesisPromptOverride?.value ?? '';
        const profileValue = profile?.knowledgeConfig?.synthesisPromptOverride;

        if (isLocked) {
            synthesisPromptInput.value = globalValue;
            synthesisPromptInput.disabled = true;
            if (synthesisPromptLockedBadge) synthesisPromptLockedBadge.classList.remove('hidden');
        } else {
            synthesisPromptInput.value = profileValue !== undefined ? profileValue : '';
            synthesisPromptInput.placeholder = globalValue || 'Leave empty to use global/default synthesis prompt...';
            synthesisPromptInput.disabled = false;
            if (synthesisPromptLockedBadge) synthesisPromptLockedBadge.classList.add('hidden');
        }
    }

    console.log('[Profile Modal] Knowledge settings loaded - global:', knowledgeGlobalSettings, 'profile:', profile?.knowledgeConfig);

    // Tag generation function
    function generateTag() {
        const llmConfig = configState.llmConfigurations.find(c => c.id === llmSelect.value);
        const mcpServer = configState.mcpServers.find(s => s.id === mcpSelect.value);
        const profileName = profileNameInput.value.trim();

        // For llm_only and rag_focused profiles, mcpServer is optional
        if (!llmConfig || !profileName) {
            return '';
        }

        // For tool_enabled profiles, mcpServer is required
        if (profileType === 'tool_enabled' && !mcpServer) {
            return '';
        }

        // Extract characters: 2 from profile name, 1 from provider, 1 from model, 1 from server (if available)
        const namePart = profileName.substring(0, 2).toUpperCase();
        const providerPart = (llmConfig.provider || '').substring(0, 1).toUpperCase();
        const modelPart = (llmConfig.model || '').substring(0, 1).toUpperCase();
        const serverPart = mcpServer ? (mcpServer.name || '').substring(0, 1).toUpperCase() : '';

        let tag = (namePart + providerPart + modelPart + serverPart).substring(0, 5);

        // Ensure uniqueness
        let suffix = '';
        let counter = 1;
        while (configState.profiles.some(p => p.id !== profileId && p.tag === tag + suffix)) {
            suffix = counter.toString();
            counter++;
        }

        return tag + suffix;
    }

    // Auto-generate tag when LLM/MCP changes (only for new profiles)
    if (!isEdit) {
        // Auto-generate when LLM or MCP changes (only if tag is empty)
        const autoGenerate = () => {
            if (!profileTagInput.value.trim()) {
                profileTagInput.value = generateTag();
            }
        };
        llmSelect.addEventListener('change', autoGenerate);
        mcpSelect.addEventListener('change', autoGenerate);
    }

    // Manual tag generation button
    const generateTagBtn = modal.querySelector('#profile-modal-generate-tag');
    if (generateTagBtn) {
        generateTagBtn.onclick = (e) => {
            e.preventDefault();
            e.stopPropagation();
            profileTagInput.value = generateTag();
        };
    }

    // Force uppercase on tag input
    profileTagInput.addEventListener('input', (e) => {
        e.target.value = e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, '').substring(0, 5);
    });

    // Check if user is privileged to see system prompts tab
    const isPrivileged = Utils.isPrivilegedUser();
    
    // Populate system prompts configuration (only for privileged users)
    if (isPrivileged) {
        await populateSystemPrompts(modal, profile);
    }

    // Populate Skills tab
    await _populateSkillsTab(modal, profile);

    // Populate Platform Connectors section (fine-grained tool selection per profile)
    // Pass null for new profiles — loadProfileConnectorSection handles missing settings gracefully
    if (typeof window.loadProfileConnectorSection === 'function') {
        await window.loadProfileConnectorSection(modal, profile ? profile.id : null);
    }

    // Show the modal
    modal.classList.remove('hidden');

    // Initialize two-column sidebar nav (resets to Identity section, wires click handlers)
    _initProfileModalNav(modal);

    // Exclusive accordion: opening one card auto-collapses siblings in the same section
    _initProfileModalAccordion(modal);

    // Attach event listeners for check all / uncheck all buttons
    const toolsCheckBtn   = modal.querySelector('#profile-modal-tools-check-all');
    const toolsUncheckBtn = modal.querySelector('#profile-modal-tools-uncheck-all');
    const promptsCheckBtn   = modal.querySelector('#profile-modal-prompts-check-all');
    const promptsUncheckBtn = modal.querySelector('#profile-modal-prompts-uncheck-all');

    if (toolsCheckBtn) {
        toolsCheckBtn.onclick = () => {
            toolsContainer.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = true);
        };
    }

    if (toolsUncheckBtn) {
        toolsUncheckBtn.onclick = () => {
            toolsContainer.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
        };
    }

    if (promptsCheckBtn) {
        promptsCheckBtn.onclick = () => {
            promptsContainer.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = true);
        };
    }

    if (promptsUncheckBtn) {
        promptsUncheckBtn.onclick = () => {
            promptsContainer.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
        };
    }

    // Stamp current profile ID onto save button so platform MCP tool selector can read it
    const saveBtn = modal.querySelector('#profile-modal-save');
    if (saveBtn) saveBtn.dataset.profileId = profileId || '';

    // Attach event listeners for save/cancel
    modal.querySelector('#profile-modal-cancel').onclick = () => modal.classList.add('hidden');
    modal.querySelector('#profile-modal-save').onclick = async () => {
        const name = modal.querySelector('#profile-modal-name').value.trim();
        const tag = modal.querySelector('#profile-modal-tag').value.trim().toUpperCase();
        const description = modal.querySelector('#profile-modal-description').value.trim();

        if (!name) {
            showNotification('error', 'Profile name is required');
            return;
        }

        if (!tag) {
            showNotification('error', 'Profile tag is required');
            return;
        }

        // Validate tag uniqueness
        if (configState.profiles.some(p => p.id !== profileId && p.tag === tag)) {
            showNotification('error', `Tag "${tag}" is already in use. Please choose a different tag.`);
            return;
        }
        
        const selectedTools = Array.from(toolsContainer.querySelectorAll('input:checked')).map(cb => cb.value);
        const selectedPrompts = Array.from(promptsContainer.querySelectorAll('input:checked')).map(cb => cb.value);
        
        // Collect query-enabled collections from both Planner and Knowledge repositories
        const plannerQueryCheckboxes = plannerContainer.querySelectorAll('input[data-collection-type="query"]:checked');
        const knowledgeQueryCheckboxes = knowledgeContainer.querySelectorAll('input[data-collection-type="query"]:checked');
        let selectedRag = [
            ...Array.from(plannerQueryCheckboxes).map(cb => parseInt(cb.dataset.collectionId)),
            ...Array.from(knowledgeQueryCheckboxes).map(cb => parseInt(cb.dataset.collectionId))
        ];
        // same carry-forward as autocomplete below: an unrendered collections UI
        // must not save an empty ragCollections over the stored value
        if (plannerContainer.dataset.collectionsRendered !== '1'
            && knowledgeContainer.dataset.collectionsRendered !== '1'
            && profile && Array.isArray(profile.ragCollections)) {
            selectedRag = profile.ragCollections;
        }

        // CRITICAL: Ensure the default collection is always included (defensive check)
        // The default collection checkbox should be disabled, but add this as a safety net
        if (!learnsOwn && defaultCollectionId !== null && !selectedRag.includes(defaultCollectionId)) {
            selectedRag.push(defaultCollectionId);
            console.log('[Profile Modal] Added default collection', defaultCollectionId, 'to selectedRag (defensive check)');
        }
        
        // Collect autocomplete-enabled collections from both Planner and Knowledge repositories
        const plannerAutocompleteCheckboxes = plannerContainer.querySelectorAll('input[data-collection-type="autocomplete"]:checked');
        const knowledgeAutocompleteCheckboxes = knowledgeContainer.querySelectorAll('input[data-collection-type="autocomplete"]:checked');
        let selectedAutocomplete = [
            ...Array.from(plannerAutocompleteCheckboxes).map(cb => parseInt(cb.dataset.collectionId)),
            ...Array.from(knowledgeAutocompleteCheckboxes).map(cb => parseInt(cb.dataset.collectionId))
        ];
        // A save before the collections UI rendered (or after a failed render)
        // finds ZERO checkboxes — an empty UI is not an empty choice. Carry the
        // STORED selections forward instead of writing destructive emptiness
        // (found live: one such save wiped autocompleteCollections to [] and
        // silently disabled a profile's query suggestions). A rendered-but-
        // genuinely-unchecked UI still saves [] — that IS a choice.
        const _collectionsRendered = plannerContainer.dataset.collectionsRendered === '1'
            || knowledgeContainer.dataset.collectionsRendered === '1';
        if (!_collectionsRendered && profile) {
            selectedAutocomplete = Array.isArray(profile.autocompleteCollections)
                ? profile.autocompleteCollections : selectedAutocomplete;
        }
        
        // Get selected classification mode
        const classificationModeRadio = modal.querySelector('input[name="classification-mode"]:checked');
        const classificationMode = classificationModeRadio ? classificationModeRadio.value : 'full';

        // Get selected profile type (determined by the active tab)
        // profileType was set earlier from profile.profile_type (edit) or defaultProfileType (new)
        const selectedProfileType = profileType;

        // Build knowledgeConfig object with per-collection reranking settings
        // Note: Global knowledge settings (minRelevance, maxDocs, maxTokens) are now in Administration panel
        // We still include them in the profile for backward compatibility; backend will use these or fall back to admin defaults
        const selectedKnowledgeIds = Array.from(knowledgeQueryCheckboxes).map(cb => parseInt(cb.dataset.collectionId));
        const rerankingListContainer = modal.querySelector('#profile-modal-knowledge-reranking-list');
        const rerankingCheckboxes = rerankingListContainer ? rerankingListContainer.querySelectorAll('input[data-reranking="true"]') : [];
        const collectionsWithReranking = Array.from(rerankingCheckboxes).map(cb => ({
            id: parseInt(cb.dataset.collectionId),
            reranking: cb.checked
        }));

        // Read advanced knowledge configuration fields
        const minRelevanceInput = modal.querySelector('#profile-modal-min-relevance');
        const maxDocsInput = modal.querySelector('#profile-modal-max-docs');
        const maxTokensInput = modal.querySelector('#profile-modal-max-tokens');
        const maxChunksPerDocInput = modal.querySelector('#profile-modal-max-chunks-per-doc');
        const freshnessWeightInput = modal.querySelector('#profile-modal-freshness-weight');
        const freshnessDecayRateInput = modal.querySelector('#profile-modal-freshness-decay-rate');
        const synthesisPromptInput = modal.querySelector('#profile-modal-synthesis-prompt');

        // Check if knowledge is enabled based on profile type and capability checkbox
        // - rag_focused: always enabled
        // - llm_only: enabled only if useKnowledgeCollections checkbox is checked
        // - tool_enabled, genie: disabled
        const useKnowledgeCheckboxForConfig = modal.querySelector('#profile-modal-use-knowledge');
        const knowledgeEnabled = selectedProfileType === 'rag_focused' ||
            (selectedProfileType === 'llm_only' && useKnowledgeCheckboxForConfig?.checked);
        // Sub-keys the editor does not model must SURVIVE a save. The backend merge is a
        // shallow `dict.update` (config_manager.update_profile), so a block rebuilt from a
        // fixed field list silently DESTROYS everything outside that list. Carry forward
        // any stored sub-key the editor doesn't own; editor-owned keys keep their
        // clear-to-remove semantics. See docs/roadmap/TIER_5.2_POSTURE.md (W1).
        const carryForward = (target, blockKey, ownedKeys) => {
            const stored = (profile && profile[blockKey]) || {};
            for (const [k, v] of Object.entries(stored)) {
                if (!ownedKeys.has(k) && !(k in target)) target[k] = v;
            }
            return target;
        };

        const knowledgeConfig = {
            enabled: knowledgeEnabled,
            collections: collectionsWithReranking
        };

        // Add optional fields only if knowledge is enabled and user specified them (non-empty)
        if (knowledgeEnabled) {
            if (minRelevanceInput.value !== '') {
                const minRel = parseFloat(minRelevanceInput.value);
                if (!isNaN(minRel) && minRel >= 0 && minRel <= 1) {
                    knowledgeConfig.minRelevanceScore = minRel;
                }
            }

            if (maxDocsInput.value !== '') {
                const maxDocs = parseInt(maxDocsInput.value);
                if (!isNaN(maxDocs) && maxDocs >= 1) {
                    knowledgeConfig.maxDocs = maxDocs;
                }
            }

            if (maxTokensInput.value !== '') {
                const maxTokens = parseInt(maxTokensInput.value);
                if (!isNaN(maxTokens) && maxTokens >= 500) {
                    knowledgeConfig.maxTokens = maxTokens;
                }
            }

            if (maxChunksPerDocInput && maxChunksPerDocInput.value !== '') {
                const maxChunks = parseInt(maxChunksPerDocInput.value);
                if (!isNaN(maxChunks) && maxChunks >= 0 && maxChunks <= 50) {
                    knowledgeConfig.maxChunksPerDocument = maxChunks;
                }
            }

            if (freshnessWeightInput && freshnessWeightInput.value !== '') {
                const fw = parseFloat(freshnessWeightInput.value);
                if (!isNaN(fw) && fw >= 0 && fw <= 1) {
                    knowledgeConfig.freshnessWeight = fw;
                }
            }

            if (freshnessDecayRateInput && freshnessDecayRateInput.value !== '') {
                const fdr = parseFloat(freshnessDecayRateInput.value);
                if (!isNaN(fdr) && fdr >= 0.001 && fdr <= 1.0) {
                    knowledgeConfig.freshnessDecayRate = fdr;
                }
            }

            if (synthesisPromptInput && synthesisPromptInput.value.trim() !== '') {
                knowledgeConfig.synthesisPromptOverride = synthesisPromptInput.value.trim();
            }

            // Retrieval sensitivity ceiling (rag_retriever enforces it at the one retrieval
            // seam; empty = no ceiling) and retrieval-sufficiency honesty threshold
            // (honesty/runtime; empty = off, absolute scores are backend-relative).
            const sensEl = modal.querySelector('#profile-knowledge-max-sensitivity');
            if (sensEl && sensEl.value) knowledgeConfig.maxSensitivity = sensEl.value;
            const rskEl = modal.querySelector('#profile-knowledge-require-signed');
            if (rskEl && rskEl.checked) knowledgeConfig.requireSignedKnowledge = true;  // K5 (absent = off)
            const suffEl = modal.querySelector('#profile-knowledge-sufficiency');
            if (suffEl && suffEl.value !== '') {
                const sv = parseFloat(suffEl.value);
                if (!isNaN(sv) && sv >= 0 && sv <= 1) knowledgeConfig.sufficiencyThreshold = sv;
            }
        }

        carryForward(knowledgeConfig, 'knowledgeConfig', new Set([
            'enabled', 'collections', 'minRelevanceScore', 'maxDocs', 'maxTokens',
            'maxChunksPerDocument', 'freshnessWeight', 'freshnessDecayRate',
            'synthesisPromptOverride', 'maxSensitivity', 'sufficiencyThreshold',
            'requireSignedKnowledge',  // K5 — modeled: absent after save = deliberately off
        ]));

        // Collect Genie child profiles and settings if this is a genie profile
        let genieConfig = null;
        if (selectedProfileType === 'genie') {
            const slaveCheckboxes = modal.querySelectorAll('.genie-slave-checkbox:checked');
            const selectedSlaveProfiles = Array.from(slaveCheckboxes).map(cb => cb.value);

            if (selectedSlaveProfiles.length === 0) {
                showNotification('error', 'Genie profiles require at least 1 child profile.');
                return;  // Prevent save
            }

            // Collect per-slave passthrough settings for Efficiency Focused profiles
            const slaveProfileSettings = {};
            modal.querySelectorAll('.slave-passthrough-toggle:checked').forEach(toggle => {
                slaveProfileSettings[toggle.dataset.profileId] = { fullResultPassthrough: true };
            });

            genieConfig = {
                slaveProfiles: selectedSlaveProfiles,
                maxConcurrentSlaves: 3,  // Default value
                slaveProfileSettings
            };

            // Trust-aware routing: route ONLY to experts whose Trust-Membrane
            // badge is green (signed + unchanged, incl. every ingredient).
            const trustedOnlyEl = modal.querySelector('#profile-genie-trusted-experts');
            if (trustedOnlyEl) genieConfig.requireTrustedExperts = !!trustedOnlyEl.checked;

            // Collect optional Genie settings (temperature, queryTimeout, maxIterations)
            // Only include if not locked and user provided a value
            const tempSlider = modal.querySelector('#profile-genie-temperature');
            const timeoutInput = modal.querySelector('#profile-genie-query-timeout');
            const iterInput = modal.querySelector('#profile-genie-max-iterations');

            // Temperature - only save if not disabled (not locked) and different from placeholder
            if (tempSlider && !tempSlider.disabled) {
                const tempVal = parseFloat(tempSlider.value);
                if (!isNaN(tempVal)) {
                    genieConfig.temperature = tempVal;
                }
            }

            // Query timeout - only save if not disabled (not locked) and user provided a value
            if (timeoutInput && !timeoutInput.disabled && timeoutInput.value !== '') {
                const timeoutVal = parseInt(timeoutInput.value);
                if (!isNaN(timeoutVal) && timeoutVal >= 60 && timeoutVal <= 900) {
                    genieConfig.queryTimeout = timeoutVal;
                }
            }

            // Max iterations - only save if not disabled (not locked) and user provided a value
            if (iterInput && !iterInput.disabled && iterInput.value !== '') {
                const iterVal = parseInt(iterInput.value);
                if (!isNaN(iterVal) && iterVal >= 1 && iterVal <= 25) {
                    genieConfig.maxIterations = iterVal;
                }
            }
        }

        // Get capability checkbox states for llm_only profiles
        const useMcpToolsCheckbox = modal.querySelector('#profile-modal-use-mcp-tools');
        const useKnowledgeCheckbox = modal.querySelector('#profile-modal-use-knowledge');
        const useMcpTools = selectedProfileType === 'llm_only' && useMcpToolsCheckbox?.checked || false;
        const useKnowledgeCollections = selectedProfileType === 'llm_only' && useKnowledgeCheckbox?.checked || false;

        // Get session primer configuration (PRESERVE EVEN WHEN DISABLED)
        const sessionPrimerCheckbox = modal.querySelector('#profile-modal-enable-primer');
        let sessionPrimerValue = null;

        // Collect statements regardless of checkbox state (for recall capability)
        const statements = collectPrimerStatements().filter(s => s.length > 0);

        if (statements.length > 0) {
            const mode = modal.querySelector('input[name="primer-mode"]:checked')?.value || 'individual';

            sessionPrimerValue = {
                enabled: sessionPrimerCheckbox?.checked || false,  // Track enabled state separately
                mode: mode,
                statements: statements,
                // Written explicitly BOTH ways: the key is what the dispatch reads, and a
                // stored `false` must be clearable by unticking (a merge cannot drop a key).
                allow_tools: !(modal.querySelector('#profile-modal-primer-briefing')?.checked),
                collapsed: !!(modal.querySelector('#profile-modal-primer-collapsed')?.checked)
            };
        } else if (!sessionPrimerCheckbox?.checked) {
            // No statements but checkbox was previously enabled - preserve empty config
            // This prevents losing the mode selection when temporarily disabling
            sessionPrimerValue = {
                enabled: false,
                mode: modal.querySelector('input[name="primer-mode"]:checked')?.value || 'individual',
                statements: [],
                allow_tools: !(modal.querySelector('#profile-modal-primer-briefing')?.checked),
                collapsed: !!(modal.querySelector('#profile-modal-primer-collapsed')?.checked)
            };
        }

        const profileData = {
            id: profile ? profile.id : `profile-${generateId()}`,
            name,
            tag,
            description,
            profile_type: selectedProfileType,
            llmConfigurationId: llmSelect.value,
            // S4 — the signed fallback; null clears it (the merge keeps a stored id otherwise)
            llmFallbackConfigurationId: (() => { const v = modal.querySelector('#profile-modal-llm-fallback')?.value || ''; return (v && v !== llmSelect.value) ? v : null; })(),
            mcpServerId: (selectedProfileType === 'genie' || (selectedProfileType === 'llm_only' && !useMcpTools)) ? null : mcpSelect.value,
            contextWindowTypeId: modal.querySelector('#profile-modal-context-window-type')?.value || null,
            kgStoreConfigId: modal.querySelector('#profile-modal-kg-store')?.value || null,
            fileStoreConfigId: modal.querySelector('#profile-modal-file-store')?.value || null,
            classification_mode: classificationMode,
            tools: resolveSavedSelection(resourceLoad.tools, selectedTools, allTools, profile?.tools, isEdit),
            prompts: resolveSavedSelection(resourceLoad.prompts, selectedPrompts, allPrompts, profile?.prompts, isEdit),
            ragCollections: selectedRag,  // Always save explicit IDs (no wildcard)
            autocompleteCollections: selectedAutocomplete,  // Always save explicit IDs (no wildcard)
            knowledgeConfig: knowledgeConfig,
            skillsConfig: _collectSkillsConfig(modal, profile?.skillsConfig),
            genieConfig: genieConfig,  // Will be null for non-genie profiles
            // Conversation capability flags (only relevant for llm_only profiles)
            useMcpTools: useMcpTools,
            useKnowledgeCollections: useKnowledgeCollections,
            // Session primer - auto-execute question when starting a new session
            session_primer: sessionPrimerValue,
            // Context limit override
            contextLimitOverride: (modal.querySelector('#context-limit-use-default')?.checked !== false)
                ? null
                : (parseInt(modal.querySelector('#context-limit-slider')?.value) || null)
        };

        // Extract dual-model configuration (tool_enabled only)
        if (selectedProfileType === 'tool_enabled') {
            const dualModelCheckbox = modal.querySelector('#enable-dual-model');
            const strategicSelect = modal.querySelector('#profile-modal-strategic-model');
            const tacticalSelect = modal.querySelector('#profile-modal-tactical-model');

            if (dualModelCheckbox && dualModelCheckbox.checked && strategicSelect && tacticalSelect) {
                const strategicId = strategicSelect.value || null;
                const tacticalId = tacticalSelect.value || null;

                // Validation: At least one model must be specified when dual-model is enabled
                if (!strategicId && !tacticalId) {
                    showNotification('error', 'Dual-model mode requires at least one specialized model');
                    return;
                }

                // **NEW: Validate that selected model configurations exist**
                // Note: We don't validate credentials here because they're encrypted on the frontend.
                // Backend will validate credentials during client creation and provide clear error messages.
                const validateModelExists = (modelId, modelLabel) => {
                    if (!modelId) return true; // Empty selection uses main config

                    const config = configState.llmConfigurations.find(c => c.id === modelId);
                    if (!config) {
                        showNotification('error', `${modelLabel} model configuration not found`);
                        return false;
                    }

                    return true;
                };

                // Validate both strategic and tactical model configs exist
                if (!validateModelExists(strategicId, 'Strategic')) return;
                if (!validateModelExists(tacticalId, 'Tactical')) return;

                profileData.dualModelConfig = {
                    strategicModelId: strategicId,
                    tacticalModelId: tacticalId
                };
            } else {
                profileData.dualModelConfig = null;
            }

            // Fast-track / Plan-gate (Optimize) — additive, default off when unchecked.
            const ftReportCbS = modal.querySelector('#profile-modal-fasttrack-report');
            const ftMaxRowsS = modal.querySelector('#profile-modal-fasttrack-maxrows');
            const planGateCbS = modal.querySelector('#profile-modal-plan-gate');
            profileData.fastTrackSimpleReports = !!(ftReportCbS && ftReportCbS.checked);
            profileData.fastTrackMaxRows = Math.max(1, parseInt(ftMaxRowsS && ftMaxRowsS.value) || 1);
            profileData.planningGate = !!(planGateCbS && planGateCbS.checked);
        } else {
            profileData.dualModelConfig = null;
        }

        // Collect componentConfig from component toggles
        const compToggles = modal.querySelectorAll('.comp-toggle');
        if (compToggles.length > 0) {
            const componentConfig = {};
            compToggles.forEach(toggle => {
                const compId = toggle.dataset.componentId;
                const row = toggle.closest('.flex');
                const intensitySelect = row?.querySelector('.comp-intensity-select');
                // a save must never silently drop a key it does not model (the F5-class trap):
                // each component STARTS from its stored settings and the form overlays only what
                // it shows. Rebuilding from {enabled, intensity} once dropped the Actions
                // component's connection — the agent could no longer write (Act II, production)
                const liveCc = ((profile?.id ? (configState.profiles?.find(p => p.id === profile.id) ?? profile) : profile)?.componentConfig) || {};
                const stored = liveCc[compId] || {};
                // the switch is written only where the OWNER holds it (a toggled row, or one that
                // was stored before — a save never strips a stored switch, so a no-op save keeps a
                // signed agent's hash); an Auto row stores nothing of the switch and follows the
                // component's class default. Every other setting of the entry travels unchanged.
                const compRow = toggle.closest('[data-comp-row]');
                const owned = compRow ? compRow.dataset.owned === '1' : true;
                componentConfig[compId] = JSON.parse(JSON.stringify(stored));
                if (owned) {
                    componentConfig[compId].enabled = toggle.checked;
                    componentConfig[compId].intensity = intensitySelect ? intensitySelect.value : (stored.intensity || 'medium');
                } else {
                    delete componentConfig[compId].enabled;
                    delete componentConfig[compId].intensity;
                }
                // Files: the agent's own access (TDA_Files) — stored only when on
                if (compId === 'files') {
                    const optin = modal.querySelector('.comp-tool-optin[data-component-id="files"]');
                    const on = optin ? optin.checked : stored.agentTool === true;
                    if (on) componentConfig[compId].agentTool = true; else delete componentConfig[compId].agentTool;
                }
                // Platform: the roles this agent holds (capability — signed). Only once the role
                // list has loaded: a save before it must carry the stored roles, never empty them.
                if (compId === 'platform') {
                    const prow = modal.querySelector('.comp-platform-roles');
                    if (prow && prow.dataset.loaded === '1') {
                        const picked = [...prow.querySelectorAll('.cpr-role-cb:checked')].map(cb => cb.value);
                        if (picked.length) componentConfig[compId].roles = picked; else delete componentConfig[compId].roles;
                    }
                }
                // Model B (knowledge_graph only): the "Ground on" choice, read from the select
                const groundSelect = row?.querySelector('.comp-groundon-select');
                const groundOn = groundSelect ? groundSelect.value : stored.groundOn;
                if (groundOn === 'released') componentConfig[compId].groundOn = 'released';
                else delete componentConfig[compId].groundOn;
                // Database Navigator: the pinned catalog source and the agent's own queries,
                // edited in the Data sources section. Its state starts equal to the stored
                // profile; with no state for THIS profile the stored values are carried forward.
                if (compId === 'database_navigator') {
                    const dsState = DataSources.stateFor(profile);
                    const sourceId = DataSources.collectSourceId(dsState, stored.sourceId);
                    if (sourceId) componentConfig[compId].sourceId = sourceId; else delete componentConfig[compId].sourceId;
                    const query = DataSources.collectQuery(dsState, stored.query);
                    if (query) componentConfig[compId].query = query; else delete componentConfig[compId].query;
                }
                // an Auto row with nothing else to keep stores no entry at all
                if (!Object.keys(componentConfig[compId]).length) delete componentConfig[compId];
            });
            profileData.componentConfig = componentConfig;
        }

        // PROP-006 — a save before the component rows rendered never ran the loop above, so an
        // edit made in Data sources would be lost. Carry the stored componentConfig and apply
        // only the Navigator's source/queries — and only when they actually changed.
        if (compToggles.length === 0) {
            const dsState = DataSources.stateFor(profile);
            const storedCc = (profile && profile.componentConfig) || {};
            const storedNav = storedCc.database_navigator || {};
            const q = DataSources.collectQuery(dsState, storedNav.query);
            const sid = DataSources.collectSourceId(dsState, storedNav.sourceId);
            if (JSON.stringify(q || null) !== JSON.stringify(storedNav.query || null) || (sid || '') !== (storedNav.sourceId || '')) {
                const cc = JSON.parse(JSON.stringify(storedCc));
                const nav = { ...(cc.database_navigator || {}) };
                if (q) nav.query = q; else delete nav.query;
                if (sid) nav.sourceId = sid; else delete nav.sourceId;
                cc.database_navigator = nav;
                profileData.componentConfig = cc;
            }
        }

        // PROP-006 — the agent's own combining rules (only ADD restrictions). Sent only when set
        // now or before; [] clears a stored set. Validated again at the server.
        {
            const rules = DataSources.collectRules(DataSources.stateFor(profile), profile?.combineIsolation);
            if (rules !== undefined) profileData.combineIsolation = rules;
        }

        // PROP-006 phase 2 — the agent's additional MCP servers. Sent only when set now or
        // before (a single-server agent's save is unchanged); [] removes a stored list, and so
        // does an agent that no longer calls MCP tools. A short-name problem stops the save here.
        {
            const dsState = DataSources.stateFor(profile);
            const usesMcp = !!profileData.mcpServerId;
            const errs = usesMcp && dsState ? DataSources.additionalErrors(dsState) : {};
            const firstBad = Object.keys(errs)[0];
            if (firstBad) {
                showNotification('error', `MCP servers: ${errs[firstBad]}`);
                const nav = modal.querySelector('.profile-nav-item[data-profile-section="data-sources"]');
                if (nav) nav.click();
                return;
            }
            const extra = DataSources.collectAdditional(dsState, profile?.additionalMcpServers, { usesMcp });
            if (extra !== undefined) profileData.additionalMcpServers = extra;
        }

        // Collect recommenderConfig (Smart Next Steps)
        const recEnabledEl = modal.querySelector('#profile-recommender-enabled');
        if (recEnabledEl) {
            const tiers = [];
            if (modal.querySelector('#rec-tier-action')?.checked) tiers.push('action');
            if (modal.querySelector('#rec-tier-followup')?.checked) tiers.push('followup');
            const llmOn = !!modal.querySelector('#rec-tier-llm')?.checked;
            if (llmOn) tiers.push('llm');
            const maxVal = parseInt(modal.querySelector('#profile-recommender-max')?.value, 10);
            const relVal = parseFloat(modal.querySelector('#profile-recommender-relevance')?.value);
            profileData.recommenderConfig = {
                enabled: !!recEnabledEl.checked,
                tiers,
                maxSuggestions: Number.isFinite(maxVal) ? Math.min(8, Math.max(1, maxVal)) : 4,
                relevanceThreshold: Number.isFinite(relVal) ? Math.min(1, Math.max(0, relVal)) : 0.65,
                llmEnrichment: llmOn,
            };
        }

        // Collect cacheConfig (S2). Spread-preserve so unmodelled sub-keys survive a save.
        const cacheEnabledEl = modal.querySelector('#profile-cache-enabled');
        if (cacheEnabledEl) {
            const ttlEl = modal.querySelector('#profile-cache-ttl');
            profileData.cacheConfig = {
                ...((profile && profile.cacheConfig) || {}),
                enabled: !!cacheEnabledEl.checked,
                ttl: (ttlEl && ttlEl.value === '1h') ? '1h' : '5m',
            };
        }

        // Collect postureConfig (the agent-level default every gate inherits).
        // Spread-preserve so unmodelled sub-keys survive a save.
        const agentPostureMode = composeGateMode('agent-posture-mode');
        if (agentPostureMode) {
            profileData.postureConfig = {
                ...((profile && profile.postureConfig) || {}),
                mode: agentPostureMode,
            };
        }

        // Collect groundingConfig. The stored vocabulary keeps all four strings —
        // composeGateMode writes the composite 'review' when Enforce + the retention
        // checkbox are on (byte-identical on disk to the retired rung). '' (Inherit)
        // OMITS the mode key — the block is still written, so a previously explicit
        // mode genuinely clears and the gate follows the agent posture. Nested/
        // unmodelled sub-keys (maxOutOfScopeDbs, minNumericFidelity, …) are carried
        // forward so a save cannot silently weaken a hardened profile.
        const groundingMode = composeGateMode('grounding-mode');
        if (groundingMode !== null) {
            const priorG = (profile && profile.groundingConfig) || {};
            profileData.groundingConfig = {};
            if (groundingMode) profileData.groundingConfig.mode = groundingMode;
            // The corpus-support family has its own ladder; blank = inherit `mode`.
            const corpusMode = composeGateMode('grounding-corpus-mode') || '';
            if (corpusMode) {
                profileData.groundingConfig.corpus = { ...(priorG.corpus || {}), mode: corpusMode };
            }
            carryForward(profileData.groundingConfig, 'groundingConfig',
                new Set(['mode', 'allowOutOfScope', 'allow_out_of_scope', 'corpus']));
        }

        // Collect guardrailConfig (Tier 1.3 outbound PII/toxicity + inbound injection
        // modes). Inherit ('') writes the block WITHOUT a mode key — the spread of the
        // prior block would otherwise resurrect the old explicit mode, so `mode` is
        // deleted before conditionally setting it.
        const guardrailMode = composeGateMode('guardrail-mode');
        if (guardrailMode !== null) {
            const priorGr = (profile && profile.guardrailConfig) || {};
            const ob = {
                ...(priorGr.outbound || {}),
                pii: modal.querySelector('#profile-guardrail-pii')?.checked !== false,
                toxicity: modal.querySelector('#profile-guardrail-toxicity')?.checked !== false,
            };
            delete ob.mode;
            if (guardrailMode) ob.mode = guardrailMode;
            profileData.guardrailConfig = { outbound: ob };
            const inboundMode = composeGateMode('guardrail-inbound-mode');
            if (inboundMode !== null) {
                const ib = { ...(priorGr.inbound || {}) };
                delete ib.mode;
                if (inboundMode) ib.mode = inboundMode;
                profileData.guardrailConfig.inbound = ib;
            }
            carryForward(profileData.guardrailConfig, 'guardrailConfig',
                new Set(['outbound', 'inbound']));
        }

        // Collect honestyConfig (retrieval-failure / retrieval-sufficiency honesty gate).
        // Previously unmodelled by the editor entirely — it survived only by accident of the
        // shallow backend merge, and could never be configured from the UI.
        const honestyMode = composeGateMode('honesty-mode');
        if (honestyMode !== null) {
            profileData.honestyConfig = {};
            if (honestyMode) profileData.honestyConfig.mode = honestyMode;
            // grainJudge: checked → explicit true; UNCHECKED must clear a stored true
            // (the F5-class trap: modelled in the exclusion set so carryForward cannot
            // resurrect it), and absent stays absent so untouched profiles hash unchanged.
            const gjEl = document.getElementById('honesty-grain-judge');
            if (gjEl && gjEl.checked) profileData.honestyConfig.grainJudge = true;
            carryForward(profileData.honestyConfig, 'honestyConfig',
                         new Set(['mode', 'grainJudge']));
        }

        // Collect outcomeConfig (S7 — revise-before-delivery; posture, signed). Unchecked saves
        // {enabled:false} explicitly (its signed default), so a save cannot resurrect a stored
        // true; the kinds list is written only when it narrows the default (all three).
        (() => {
            const en = modal.querySelector('#profile-outcome-enabled');
            if (!en) return;
            const oc = { enabled: !!en.checked };
            if (oc.enabled) {
                // the bound + the kinds are written only when the loop is ON — an OFF block
                // hashes exactly as the signed default {enabled:false, maxRevisions:null}
                const mr = Number(modal.querySelector('#profile-outcome-max-revisions')?.value || 1);
                oc.maxRevisions = mr === 2 ? 2 : 1;
                const kinds = ['unsupported_claim', 'out_of_scope_ref', 'ungrounded_number']
                    .filter(k => modal.querySelector(`#profile-outcome-kind-${k}`)?.checked);
                if (kinds.length < 3) oc.revisableKinds = kinds;   // an empty list = none revisable (a real choice)
            }
            profileData.outcomeConfig = oc;
        })();

        // Collect attachmentConfig (S9 — the unverified vision fallback). Unchecked saves
        // {transcribeImages:false} explicitly so a save cannot resurrect a stored true;
        // every other key of the block is carried forward untouched.
        (() => {
            const el = modal.querySelector('#profile-attachment-transcribe');
            if (!el) return;
            profileData.attachmentConfig = carryForward({ transcribeImages: !!el.checked }, 'attachmentConfig', new Set(['transcribeImages']));
        })();

        // Collect judgeConfig (per-profile JUDGED station — PROP-002 follow-up):
        // judge opt-in/out per agent, sample-rate override, and whether a judge
        // condemnation files an Assurance-Inbox incident. Spread-preserve like the
        // other posture blocks so unmodelled sub-keys survive a save.
        (() => {
            const modeEl = modal.querySelector('input[name="profile-judge-mode"]:checked');
            if (!modeEl) return;   // card not present
            const j = { judges: modeEl.value || 'inherit' };
            const rateRaw = modal.querySelector('#profile-judge-sample-rate')?.value;
            const rate = rateRaw === '' || rateRaw == null ? null : Number(rateRaw);
            j.sampleRate = (rate != null && Number.isFinite(rate))
                ? Math.min(1, Math.max(0, rate)) : null;
            j.inboxOnCondemnation = modal.querySelector('#profile-judge-inbox')?.checked !== false;
            profileData.judgeConfig = j;
            carryForward(profileData.judgeConfig, 'judgeConfig',
                new Set(['judges', 'sampleRate', 'inboxOnCondemnation']));
        })();

        // Collect authorityConfig (Tier 5 Step 4 — autonomous agent authority; subset-only)
        (() => {
            const autoEl = modal.querySelector('#profile-authority-autonomous');
            if (!autoEl) return;  // card not present
            const splitList = (id) => (modal.querySelector(id)?.value || '')
                .split(',').map(s => s.trim()).filter(Boolean);
            const denied = splitList('#profile-authority-denied-tools');
            const connectors = splitList('#profile-authority-connectors');
            const budRaw = modal.querySelector('#profile-authority-budget')?.value;
            const budget = (budRaw != null && budRaw !== '' && Number.isFinite(parseFloat(budRaw)))
                ? parseFloat(budRaw) : null;
            // Data surface — the tri-state (operator model, 2026-08-17):
            //   A ("off", the default)  → dataSurface REMOVED (in the carryForward
            //     exclusion set, so switching to A genuinely clears a stored surface)
            //   B ("exclusive")         → {products, exclusive:true, advisory}
            //   C ("mixed")             → {products, exclusive:false}
            // B/C with an EMPTY list save as A — storing a surface the runtime
            // treats as off would make the editor show a mode that does not act.
            const surfProducts = splitList('#profile-authority-surface-products');
            const surfMode = modal.querySelector(
                'input[name="profile-obda-mode"]:checked')?.value || 'off';
            const surfAdvisory = !!modal.querySelector('#profile-authority-surface-advisory')?.checked;
            const dataSurface = (surfMode !== 'off' && surfProducts.length)
                ? { products: surfProducts, exclusive: surfMode === 'exclusive',
                    advisory: surfMode === 'exclusive' ? surfAdvisory : false }
                : null;
            profileData.authorityConfig = {
                autonomous: !!autoEl.checked,
                deny_writes: !!modal.querySelector('#profile-authority-deny-writes')?.checked,
                denied_tools: denied,
                // null = all connectors allowed; a set list = allow-list
                allowed_connectors: connectors.length ? connectors : null,
                max_cost_per_run: budget,
                dataSurface: dataSurface,
                // Whether this agent's authority applies to interactive turns too.
                // Absent/false reproduces the historic autonomous-only scope exactly.
                enforce_interactive: !!(document.getElementById(
                    'profile-authority-enforce-interactive') || {}).checked,
            };
            // Needs input (PROP-011): the editor owns `inputPolicy` only when its controls were
            // rendered; only what differs from the runtime defaults is written.
            const ownsInput = !!modal.querySelector('#profile-input-interactive');
            if (ownsInput) {
                const sel = (id) => String(modal.querySelector(id)?.value || '').toLowerCase();
                const inputPolicy = {};
                if (sel('#profile-input-interactive') === 'never') inputPolicy.interactive = 'never';
                if (sel('#profile-input-unattended') === 'fail') inputPolicy.unattended = 'fail';
                if (sel('#profile-input-raisers') === 'observe') inputPolicy.raisers = 'observe';
                const mx = parseInt(modal.querySelector('#profile-input-max')?.value, 10);
                if (Number.isInteger(mx) && mx >= 1 && mx <= 8) inputPolicy.maxPerTurn = mx;
                const defaults = {};
                (modal.querySelector('#profile-input-defaults')?.value || '').split('\n').forEach((line) => {
                    const m = line.match(/^\s*([^=]+?)\s*=\s*(.+?)\s*$/);
                    if (m) defaults[m[1]] = m[2];
                });
                if (Object.keys(defaults).length) inputPolicy.defaults = defaults;
                if (Object.keys(inputPolicy).length) profileData.authorityConfig.inputPolicy = inputPolicy;
            }
            carryForward(profileData.authorityConfig, 'authorityConfig', new Set([
                'autonomous', 'deny_writes', 'denied_tools', 'allowed_connectors',
                'max_cost_per_run', 'dataSurface', 'enforce_interactive',
                ...(ownsInput ? ['inputPolicy'] : []),
            ]));
            // Four eyes: the editor owns ONLY actions.approvers — the grant (actions.types,
            // set by the Action Designer) and every other actions key travel unchanged.
            const apEl = modal.querySelector('#profile-authority-approvers');
            if (apEl) {
                const approvers = apEl.value.replace(/;/g, ',').split(',')
                    .map(s => s.trim().replace(/^@/, '')).filter(Boolean);
                const storedActs = ((profile && (profile.authorityConfig || profile.identityConfig)) || {}).actions;
                const acts = (storedActs && typeof storedActs === 'object') ? { ...storedActs } : {};
                if (approvers.length) acts.approvers = [...new Set(approvers)];
                else delete acts.approvers;
                if (Object.keys(acts).length) profileData.authorityConfig.actions = acts;
                else delete profileData.authorityConfig.actions;
            }
            // Trust-breaker freeze markers (eval/online.py) are carried forward by the line
            // above so a save can no longer silently un-freeze a frozen agent. Re-enabling
            // autonomy is an explicit owner override, so it lifts the freeze deliberately
            // rather than leaving `autonomous: true` next to a stale `frozen_by_breaker`.
            if (profileData.authorityConfig.autonomous) {
                delete profileData.authorityConfig.frozen_by_breaker;
                delete profileData.authorityConfig.frozen_reason;
            }
        })();

        // Collect the platform write mode (TDA_Platform phase 3 — authority: unsigned,
        // downgrade-guarded). Only `platformSurface.writes` is the editor's; every other stored
        // authority key travels unchanged, whether or not the authority card was rendered.
        (() => {
            const ws = modal.querySelector('.comp-platform-roles .cpr-writes-select');
            if (!ws) return;   // the Platform row was not rendered
            const liveP = (profile?.id ? (configState.profiles?.find(p => p.id === profile.id) ?? profile) : profile) || {};
            const base = profileData.authorityConfig
                || JSON.parse(JSON.stringify(liveP.authorityConfig || liveP.identityConfig || {}));
            const ps = (base.platformSurface && typeof base.platformSurface === 'object') ? { ...base.platformSurface } : {};
            if (ws.disabled || ws.value === 'off') delete ps.writes; else ps.writes = ws.value;
            // The agents it may ask: written only once the list was shown (asking on); otherwise
            // what was stored is kept as it is.
            const askBox = modal.querySelector('.comp-platform-roles .cpr-ask-list');
            if (askBox && askBox.dataset.enabled === '1') {
                const ids = [...askBox.querySelectorAll('.cpr-ask-cb:checked')].map(cb => cb.value);
                if (ids.length) ps.ask = ids; else delete ps.ask;
            }
            if (Object.keys(ps).length) base.platformSurface = ps; else delete base.platformSurface;
            profileData.authorityConfig = base;
        })();

        // Collect toolSurface (S6 — what the model is SHOWN first; unsigned, portable)
        (() => {
            const v = modal.querySelector('#profile-modal-tool-surface')?.value;
            profileData.toolSurface = { mode: v === 'governed' ? 'governed' : 'all' };
        })();

        // Collect turnBudget (S5 — the Visible Budget). Blank on every unit = NO budget: the
        // block is saved as {} (its signed defaults hash as absent, so a plain save of an
        // unbudgeted agent flips no badge). A unit is a positive integer or absent.
        (() => {
            const tb = {};
            for (const unit of ['steps', 'tokens', 'seconds']) {
                const raw = modal.querySelector(`#profile-turn-budget-${unit}`)?.value;
                const n = raw === '' || raw == null ? NaN : Number(raw);
                if (Number.isFinite(n) && n > 0) tb[unit] = Math.floor(n);
            }
            profileData.turnBudget = tb;
        })();

        // For new profiles: if a default profile exists, enable inherit_classification by default
        if (!isEdit && configState.defaultProfileId) {
            profileData.inherit_classification = true;
        }

        // Collect system prompt mappings from the third tab
        const systemPromptMappings = [];
        const systemPromptsContent = modal.querySelector('#profile-content-system-prompts');
        console.log('[Save Profile] System prompts content element:', systemPromptsContent);
        if (systemPromptsContent) {
            const dropdowns = systemPromptsContent.querySelectorAll('select[data-category][data-subcategory]');
            console.log('[Save Profile] Found', dropdowns.length, 'prompt mapping dropdowns');
            dropdowns.forEach(select => {
                const value = select.value;
                const category = select.dataset.category;
                const subcategory = select.dataset.subcategory;
                console.log(`[Save Profile] Dropdown ${category}/${subcategory}: value="${value}"`);
                
                // Include ALL dropdowns in the payload
                // Empty value means "delete mapping" (revert to system default)
                // Non-empty value means "set mapping" (use custom prompt)
                systemPromptMappings.push({
                    category: category,
                    subcategory: subcategory,
                    prompt_name: value,  // Empty string for delete, prompt name for set
                    action: value ? 'set' : 'delete'  // Explicit action for backend
                });
                
                if (value) {
                    console.log(`[Save Profile] Will SET mapping: ${category}/${subcategory} -> ${value}`);
                } else {
                    console.log(`[Save Profile] Will DELETE mapping: ${category}/${subcategory} (revert to default)`);
                }
            });
        } else {
            console.warn('[Save Profile] System prompts content element not found');
        }
        console.log('[Save Profile] Total mappings to process:', systemPromptMappings.length, systemPromptMappings);

        // Validate RAG focused profiles REQUIRE at least 1 knowledge collection
        if (selectedProfileType === 'rag_focused') {
            const knowledgeCollections = knowledgeConfig.collections || [];
            if (knowledgeCollections.length === 0) {
                showNotification('error', 'RAG focused profiles require at least 1 knowledge collection.');
                return;  // Prevent save
            }
        }

        // Four eyes: a changed designation is validated by the ONE server rule
        // (POST /v1/actions/approvers — every name an active account, never the owner) BEFORE
        // anything is saved, so a typo never leaves a half-saved profile. A new profile has no
        // id yet: it is designated right after it is created (below).
        const _apNew = (profileData.authorityConfig?.actions?.approvers) || [];
        const _apOld = (((profile && (profile.authorityConfig || profile.identityConfig)) || {}).actions || {}).approvers || [];
        const _apChanged = JSON.stringify([..._apNew].sort()) !== JSON.stringify([...(Array.isArray(_apOld) ? _apOld : [_apOld])].sort());
        const _designate = async (pid) => {
            const r = await fetch('/api/v1/actions/approvers', {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${localStorage.getItem('tda_auth_token')}`,
                           'Content-Type': 'application/json' },
                body: JSON.stringify({ profile_id: pid, approvers: _apNew }),
            });
            if (r.ok) return null;
            const body = await r.json().catch(() => ({}));
            return body.message || `the approvers could not be saved (HTTP ${r.status})`;
        };
        if (isEdit && _apChanged) {
            const err = await _designate(profileId);
            if (err) { showNotification('error', `Approvers: ${err}`); return; }
        }

        try {
            if (isEdit) {
                // Get the current state before update
                const profileBeforeUpdate = configState.profiles.find(p => p.id === profileId);
                const hadReclassificationFlag = profileBeforeUpdate?.needs_reclassification || false;
                
                await configState.updateProfile(profileId, profileData);

                // componentConfig may have changed -> drop the cached enablement answers
                // so conversation affordances (Pin to Dashboard, ...) reflect the save
                // immediately instead of serving a stale "enabled" from before the edit.
                if (window.ComponentGate) window.ComponentGate.invalidate(profileId);
                
                // Save system prompt mappings
                if (systemPromptMappings.length > 0) {
                    try {
                        const token = localStorage.getItem('tda_auth_token');
                        const mappingsResponse = await fetch(`/api/v1/system-prompts/profiles/${profileId}/mappings`, {
                            method: 'POST',
                            headers: {
                                'Authorization': `Bearer ${token}`,
                                'Content-Type': 'application/json'
                            },
                            body: JSON.stringify({ mappings: systemPromptMappings })
                        });
                        
                        if (!mappingsResponse.ok) {
                            console.warn('Failed to save prompt mappings:', await mappingsResponse.text());
                        }
                    } catch (err) {
                        console.error('Error saving prompt mappings:', err);
                    }
                }
                
                // Reload profiles to get updated needs_reclassification flag from backend
                await configState.loadProfiles();
                // The resource panel follows the save at once if it shows this agent (resourceSync.js).
                announceProfileConfigChange(profileId, 'profile');
                
                // Clear test status since profile has been modified
                delete configState.profileTestStatus[profileId];
                
                // Check if reclassification flag was newly set during this update
                const updatedProfile = configState.profiles.find(p => p.id === profileId);
                const hasReclassificationFlag = updatedProfile?.needs_reclassification || false;
                const flagWasNewlySet = !hadReclassificationFlag && hasReclassificationFlag;
                
                console.log('[Profile Update] Before:', hadReclassificationFlag, 'After:', hasReclassificationFlag, 'Newly set:', flagWasNewlySet);
                
                renderProfiles();
                renderLLMProviders(); // Re-render to update default/active badges
                renderMCPServers(); // Re-render to update default/active badges
                updateReconnectButton();
                modal.classList.add('hidden');

                if (flagWasNewlySet) {
                    showNotification('warning', 'Profile updated - Reclassification recommended. Please test the profile before activating.');
                } else {
                    showNotification('success', 'Profile updated successfully. Please test the profile before activating.');
                }

                // Profile data is already persisted — clear dirty state so navigation isn't blocked.
                // Only clear if no other connection-level changes (MCP/LLM dropdowns) are pending.
                const { getConfigDirtyState, markConfigClean } = await import('../configDirtyState.js?v=1.189');
                const { changedFields } = getConfigDirtyState();
                if (changedFields.every(f => f.startsWith('profile-'))) {
                    markConfigClean();
                }
            } else {
                await configState.addProfile(profileData);
                
                // Get the new profile ID to save mappings
                const newProfile = configState.profiles.find(p => p.name === name && p.tag === tag);
                if (newProfile && _apNew.length) {
                    const err = await _designate(newProfile.id);
                    if (err) showNotification('warning', `Profile created, but the approvers were not saved: ${err}`);
                }
                if (newProfile && systemPromptMappings.length > 0) {
                    try {
                        const token = localStorage.getItem('tda_auth_token');
                        const mappingsResponse = await fetch(`/api/v1/system-prompts/profiles/${newProfile.id}/mappings`, {
                            method: 'POST',
                            headers: {
                                'Authorization': `Bearer ${token}`,
                                'Content-Type': 'application/json'
                            },
                            body: JSON.stringify({ mappings: systemPromptMappings })
                        });
                        
                        if (!mappingsResponse.ok) {
                            console.warn('Failed to save prompt mappings:', await mappingsResponse.text());
                        }
                    } catch (err) {
                        console.error('Error saving prompt mappings:', err);
                    }
                }
                
                renderProfiles();
                renderLLMProviders(); // Re-render to update default/active badges
                renderMCPServers(); // Re-render to update default/active badges
                updateReconnectButton();
                modal.classList.add('hidden');
                showNotification('success', 'Profile added successfully');

                // Profile data is already persisted — clear dirty state so navigation isn't blocked.
                const { getConfigDirtyState, markConfigClean: markClean } = await import('../configDirtyState.js?v=1.189');
                const { changedFields: addedFields } = getConfigDirtyState();
                if (addedFields.every(f => f.startsWith('profile-'))) {
                    markClean();
                }
            }
        } catch (error) {
            showNotification('error', error.message);
        }
    };

    // FINAL VISIBILITY UPDATE - Execute after all async initialization completes
    // This ensures the correct sections are hidden/shown based on profile type,
    // even if something changed the radio button state during initialization
    setTimeout(() => {
        // Use the profileType variable which includes defaultProfileType for new profiles
        const finalProfileType = profileType;
        console.log('[Profile Modal] Final visibility update with profileType:', finalProfileType);
        updateSectionVisibility(finalProfileType);
    }, 100); // Small delay to ensure all DOM updates are complete
}


export async function initializeConfigurationUI() {
    // Initialize tabs
    initializeConfigTabs();
    
    // Load MCP servers from backend first
    await configState.initialize();
    
    renderMCPServers();
    setupMCPTransportTabs();
    renderLLMProviders();
    setupLLMProviderTabs();
    renderProfiles();
    updateReconnectButton();
    initializeExtensionHandlers();
    initializeSkillHandlers();

    // Load MCP classification setting
    await loadClassificationSetting();

    // Add MCP server button
    const addMCPBtn = document.getElementById('add-mcp-server-btn');
    if (addMCPBtn) {
        addMCPBtn.addEventListener('click', () => showMCPServerModal());
    }

    // Import MCP server button
    const importMCPBtn = document.getElementById('import-mcp-server-btn');
    if (importMCPBtn) {
        importMCPBtn.addEventListener('click', () => showImportMCPServerModal());
    }

    // Add LLM configuration button
    const addLLMConfigBtn = document.getElementById('add-llm-config-btn');
    if (addLLMConfigBtn) {
        addLLMConfigBtn.addEventListener('click', async () => {
            // Pre-select provider based on active tab (map lowercase tab filter to template key)
            const providerKey = activeLLMProviderFilter !== 'all'
                ? Object.keys(LLM_PROVIDER_TEMPLATES).find(k => k.toLowerCase() === activeLLMProviderFilter)
                : null;
            await showLLMConfigurationModal(null, providerKey);
        });
    }

    // Add Profile button
    const addProfileBtn = document.getElementById('add-profile-btn');
    if (addProfileBtn) {
        addProfileBtn.addEventListener('click', () => {
            // Detect which profile type tab is currently active by checking visible content containers
            let defaultProfileType = 'tool_enabled'; // Default fallback

            // Check which content container is visible (not hidden)
            if (!document.getElementById('tool-profiles-container')?.classList.contains('hidden')) {
                defaultProfileType = 'tool_enabled';
            } else if (!document.getElementById('conversation-profiles-container')?.classList.contains('hidden')) {
                defaultProfileType = 'llm_only';
            } else if (!document.getElementById('rag-profiles-container')?.classList.contains('hidden')) {
                defaultProfileType = 'rag_focused';
            } else if (!document.getElementById('genie-profiles-container')?.classList.contains('hidden')) {
                defaultProfileType = 'genie';
            }

            console.log('[Add Profile] Detected active profile type:', defaultProfileType);
            showProfileModal(null, defaultProfileType);
        });
    }

    // Test All Profiles button
    const testAllProfilesBtn = document.getElementById('test-all-profiles-btn');
    if (testAllProfilesBtn) {
        testAllProfilesBtn.addEventListener('click', async () => {
            let activeIds = [...configState.activeForConsumptionProfileIds];
            for (const profile of configState.profiles) {
                const profileId = profile.id;
                const resultsContainer = document.getElementById(`test-results-${profileId}`);
                if (resultsContainer) {
                    resultsContainer.innerHTML = `<span class="text-yellow-400">Testing...</span>`;
                    try {
                        const result = await API.testProfile(profileId);
                        let html = '';
                        const all_successful = Object.values(result.results).every(r => r.status === 'success' || r.status === 'info');

                        for (const [key, value] of Object.entries(result.results)) {
                            let statusClass;
                            if (value.status === 'success') {
                                statusClass = 'text-green-400';
                            } else if (value.status === 'info') {
                                statusClass = 'text-blue-400';
                            } else if (value.status === 'warning') {
                                statusClass = 'text-yellow-400';
                            } else {
                                statusClass = 'text-red-400';
                            }
                            html += `<p class="${statusClass}">${value.message}</p>`;
                        }
                        resultsContainer.innerHTML = html;

                        // Update activeIds list based on result
                        if (all_successful) {
                            if (!activeIds.includes(profileId)) {
                                activeIds.push(profileId);
                            }
                        } else {
                            activeIds = activeIds.filter(id => id !== profileId);
                        }
                        
                        // Manually update the toggle switch state
                        const checkbox = document.querySelector(`input[data-action="toggle-active-consumption"][data-profile-id="${profileId}"]`);
                        if (checkbox) {
                            checkbox.checked = all_successful;
                        }

                    } catch (error) {
                        resultsContainer.innerHTML = `<span class="text-red-400">${error.message}</span>`;
                        // Also uncheck on API error
                        activeIds = activeIds.filter(id => id !== profileId);
                        const checkbox = document.querySelector(`input[data-action="toggle-active-consumption"][data-profile-id="${profileId}"]`);
                        if (checkbox) {
                            checkbox.checked = false;
                        }
                    }
                }
            }
            // Save the final state of all active profiles once at the end
            await configState.setActiveForConsumptionProfiles(activeIds);
        });
    }

    // Reconnect button - use centralized initialization
    const reconnectBtn = document.getElementById('reconnect-and-load-btn');
    if (reconnectBtn) {
        reconnectBtn.addEventListener('click', async () => {
            console.log('[SaveConnect] Button clicked - marking as saving');
            
            // Mark as saving FIRST, before any initialization
            // This must happen synchronously to prevent navigation blocking
            if (typeof markSaving === 'function') {
                markSaving();
                console.log('[SaveConnect] ✅ markSaving() called successfully');
            } else {
                console.error('[SaveConnect] ⚠️ markSaving is not a function:', typeof markSaving);
            }
            
            // Now proceed with initialization
            // Add cache buster to force reload
            const { initializeConversationMode } = await import(`../conversationInitializer.js?v=${Date.now()}`);
            await initializeConversationMode();
        });
    }
    
    // MCP Classification toggle
    const classificationCheckbox = document.getElementById('enable-mcp-classification');
    if (classificationCheckbox) {
        classificationCheckbox.addEventListener('change', async (e) => {
            await saveClassificationSetting(e.target.checked);
        });
    }

}
