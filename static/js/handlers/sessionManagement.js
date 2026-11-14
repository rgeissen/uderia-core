/**
 * handlers/sessionManagement.js
 * * This module handles all logic related to session management.
 * (create, load, rename, delete)
 */

import * as DOM from '../domElements.js';
import { loadRendererModule } from '../componentRenderers.js?v=1.7';
import { state } from '../state.js';
import * as API from '../api.js?v=1.2';
import * as UI from '../ui.js?v=1.287';
import { renameSession, deleteSession } from '../api.js?v=1.2';
import { updateActiveSessionTitle } from '../ui.js?v=1.287';
import { renderAttachmentChips, initializeUploadCapabilities } from './chatDocumentUpload.js?v=1.2';
import { genieState, cleanupCoordination } from './genieHandler.js?v=4.208';
import { conversationAgentState, cleanupExecution } from './conversationAgentHandler.js?v=1.1';
import { renderDock as renderNextStepDock } from './nextStepHandler.js?v=1.3';
import { resetContextPanelState, loadContextPanel, renderContextWindowSnapshot } from './contextPanelHandler.js?v=1.189';
import { resetComponentsPanelState } from './componentsPanelHandler.js?v=1.11';

// 🔥 DEBUG: Module load detection (v3.3 - Feb 13, 2026)
console.log('%c🔥 SESSION MANAGEMENT LOADED - VERSION 3.3 (NEW CODE)', 'background: #ff00ff; color: #fff; font-size: 16px; font-weight: bold; padding: 5px;');

// NOTE: createHistoricalGenieCard import removed - genie coordination cards
// are no longer rendered inline. Coordination details shown in Live Status window only.
// NOTE: createHistoricalAgentCard import removed - conversation agent cards
// are no longer rendered inline. Tool details shown in Live Status window only.

/**
 * Creates a new session, adds it to the list, and loads it.
 */
export async function handleStartNewSession() {
    // Hide welcome screen when starting a new session
    if (window.hideWelcomeScreen) {
        window.hideWelcomeScreen();
    }
    
    DOM.chatLog.innerHTML = '';
    DOM.statusWindowContent.innerHTML = '<p class="text-gray-400">Waiting for a new request...</p>';

    // Reset cost accumulator for new session
    if (window.sessionCostAccumulator) {
        window.sessionCostAccumulator.turn = 0;
        window.sessionCostAccumulator.session = 0;
        window.sessionCostAccumulator.strategic = 0;
        window.sessionCostAccumulator.tactical = 0;
        window.sessionCostAccumulator.strategicTurnIn = 0;
        window.sessionCostAccumulator.strategicTurnOut = 0;
        window.sessionCostAccumulator.strategicTurnCost = 0;
        window.sessionCostAccumulator.tacticalTurnIn = 0;
        window.sessionCostAccumulator.tacticalTurnOut = 0;
        window.sessionCostAccumulator.tacticalTurnCost = 0;
        window.sessionCostAccumulator.strategicSessionIn = 0;
        window.sessionCostAccumulator.strategicSessionOut = 0;
        window.sessionCostAccumulator.tacticalSessionIn = 0;
        window.sessionCostAccumulator.tacticalSessionOut = 0;
        window.sessionCostAccumulator.lastStmtPhase = null;
        // Clear the idempotent per-call cost ledgers too, otherwise _recomputeLiveCost()
        // would re-derive the previous session's cost and overwrite these zeros.
        window.sessionCostAccumulator.turnCalls = {};
        window.sessionCostAccumulator.sessionCalls = {};
        window.sessionCostAccumulator.sessionBaseCost = 0;
        window.sessionCostAccumulator.lastTurnNumber = null;
    }
    // Update cost display to reflect reset
    const turnCostElNew = document.getElementById('turn-cost-value');
    const sessionCostElNew = document.getElementById('session-cost-value');
    if (turnCostElNew) turnCostElNew.textContent = '$0.000000';
    if (sessionCostElNew) sessionCostElNew.textContent = '$0.000000';
    // Clear dual-model tooltips
    ['metric-card-statement','metric-card-turn','metric-card-session',
     'metric-card-turn-cost','metric-card-session-cost'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.removeAttribute('data-tooltip-html');
    });

    UI.updateTokenDisplay({ statement_input: 0, statement_output: 0, total_input: 0, total_output: 0 });
    UI.addMessage('assistant', "Starting a new conversation... Please wait.");
    UI.setThinkingIndicator(false);

    // --- MODIFICATION START: Hide profile override warning banner on new session ---
    const profileWarningBanner = document.getElementById('profile-override-warning-banner');
    if (profileWarningBanner) {
        profileWarningBanner.classList.add('hidden');
    }
    // Capture the active profile override BEFORE clearing it - needed for session primer
    // PRIORITY: Badge (current selection) > activeProfileOverrideId (last executed) > null
    let activeProfileOverrideId = null;

    // Check badge FIRST - it represents the user's current intent (what they typed/selected)
    // This takes priority over window.activeProfileOverrideId which is the last *executed* profile
    const tagBadge = document.getElementById('active-profile-tag');
    const isTagBadgeVisible = tagBadge && !tagBadge.classList.contains('hidden');

    if (isTagBadgeVisible && window.configState?.profiles) {
        // Get tag from badge's first span (the one containing "@TAG"), fallback to activeTagPrefix
        // Badge structure: <span>@TAG</span><span class="tag-remove">×</span>
        const tagSpan = tagBadge.querySelector('span:first-child');
        const badgeText = (tagSpan?.textContent || window.activeTagPrefix || '').trim();
        const tag = badgeText.replace('@', '').trim().toUpperCase();
        if (tag) {
            const overrideProfile = window.configState.profiles.find(p => p.tag === tag);
            if (overrideProfile) {
                activeProfileOverrideId = overrideProfile.id;
                console.log(`[Session Primer] Resolved tag badge @${tag} to profile ID: ${activeProfileOverrideId}`);
            }
        }
    }

    // Fall back to last executed profile if no badge is active
    if (!activeProfileOverrideId) {
        activeProfileOverrideId = window.activeProfileOverrideId || null;
    }

    // Clear the active profile override for autocomplete when starting a new session
    if (window.activeProfileOverrideId) {
        delete window.activeProfileOverrideId;
    }
    // --- MODIFICATION END ---

    // --- MODIFICATION START: Hide header buttons and clear turnId on new session ---
    if (DOM.headerReplayPlannedButton) {
        DOM.headerReplayPlannedButton.classList.add('hidden');
        DOM.headerReplayPlannedButton.dataset.turnId = '';
    }
    if (DOM.headerReplayOptimizedButton) {
        DOM.headerReplayOptimizedButton.classList.add('hidden');
        DOM.headerReplayOptimizedButton.dataset.turnId = '';
    }
    // --- MODIFICATION END ---

    // --- MODIFICATION START: Clear task ID display on new session ---
    UI.updateTaskIdDisplay(null);
    // --- MODIFICATION END ---
    
    // Clear persisted session ID when starting new session
    localStorage.removeItem('currentSessionId');
    state.sessionLoaded = false;

    try {
        console.log('[Session Primer] Starting new session with profile override ID:', activeProfileOverrideId);
        const data = await API.startNewSession(activeProfileOverrideId);
        console.log('[Session Primer] Session created, response:', { id: data.id, has_primer: !!data.session_primer });
        const sessionItem = UI.addSessionToList(data, true);
        DOM.sessionList.prepend(sessionItem);
        await handleLoadSession(data.id, true);

        // AUTO-EXECUTE PRIMER IF CONFIGURED AND ENABLED
        if (data.session_primer) {
            // Backward compatibility: convert string to new format
            const primerConfig = typeof data.session_primer === 'string'
                ? { enabled: true, mode: 'combined', statements: [data.session_primer] }
                : data.session_primer;

            // Only execute if explicitly enabled
            if (!primerConfig.enabled) {
                console.log('[Session Primer] Primer data exists but is disabled - skipping execution');
            } else {
                console.log(`[Session Primer] Executing ${primerConfig.statements.length} statement(s) in ${primerConfig.mode} mode`);

                // Import and use handleStreamRequest to execute the primer(s)
                const { handleStreamRequest } = await import('../eventHandlers.js?v=4.214');

                if (primerConfig.mode === 'combined') {
                    // Execute all statements as one merged query with clear separators
                    let mergedQuery;
                    if (primerConfig.statements.length === 1) {
                        // Single statement - no need for numbering
                        mergedQuery = primerConfig.statements[0];
                    } else {
                        // Multiple statements - add clear numbering and separators
                        mergedQuery = primerConfig.statements
                            .map((stmt, idx) => `[Statement ${idx + 1}]\n${stmt}`)
                            .join('\n\n---\n\n');
                    }
                    console.log('[Session Primer] Combined mode - merging statements into single query with separators');
                    const primerRequest = {
                        message: mergedQuery,
                        session_id: data.id,
                        is_session_primer: true
                    };

                    if (data.profile_override_id) {
                        primerRequest.profile_override_id = data.profile_override_id;
                    }

                    handleStreamRequest('/ask_stream', primerRequest);

                } else if (primerConfig.mode === 'individual') {
                    // Execute each statement separately in sequence
                    console.log('[Session Primer] Individual mode - executing statements sequentially');

                    // Simple sequential execution with delays
                    const executeNextStatement = (index) => {
                        if (index >= primerConfig.statements.length) {
                            console.log('[Session Primer] All statements executed');
                            return;
                        }

                        const statement = primerConfig.statements[index];
                        console.log(`[Session Primer] Executing statement ${index + 1}/${primerConfig.statements.length}`);

                        const primerRequest = {
                            message: statement,
                            session_id: data.id,
                            is_session_primer: true
                        };

                        if (data.profile_override_id) {
                            primerRequest.profile_override_id = data.profile_override_id;
                        }

                        // Execute statement (uses existing combined mode logic)
                        handleStreamRequest('/ask_stream', primerRequest);

                        // Wait 10 seconds before next statement (ensures completion)
                        if (index < primerConfig.statements.length - 1) {
                            setTimeout(() => executeNextStatement(index + 1), 10000);
                        }
                    };

                    // Start with first statement
                    executeNextStatement(0);
                }
            }
        }
    } catch (error) {
        UI.addMessage('assistant', `Failed to start a new session: ${error.message}`);
    } finally {
        DOM.userInput.focus();
    }
}

// ============================================================================
// Session UI State Capture/Restore Helpers (for stream isolation)
// ============================================================================

/**
 * Captures the current status window header DOM state as a plain object.
 * Used to snapshot the header before switching away from an active session.
 */
function _captureHeaderState() {
    return {
        statusTitle: (DOM.statusTitle || document.getElementById('status-title'))?.textContent || 'Live Status',
        taskIdValue: DOM.taskIdValue?.textContent || '',
        taskIdVisible: DOM.taskIdDisplay ? !DOM.taskIdDisplay.classList.contains('hidden') : false,
        promptNameHTML: document.getElementById('prompt-name-display')?.innerHTML || '',
        thinkingActive: DOM.thinkingIndicator ? !DOM.thinkingIndicator.classList.contains('hidden') : false,
        knowledgeBannerVisible: !document.getElementById('knowledge-banner')?.classList.contains('hidden'),
        knowledgeCollectionsList: document.getElementById('knowledge-collections-list')?.textContent || '',
        tokenData: {
            statementInput: document.getElementById('statement-input-tokens')?.textContent || '0',
            statementOutput: document.getElementById('statement-output-tokens')?.textContent || '0',
            turnInput: document.getElementById('turn-input-tokens')?.textContent || '0',
            turnOutput: document.getElementById('turn-output-tokens')?.textContent || '0',
            totalInput: document.getElementById('total-input-tokens')?.textContent || '0',
            totalOutput: document.getElementById('total-output-tokens')?.textContent || '0',
            turnLabel: document.getElementById('turn-token-label')?.textContent || 'Last Turn',
            normalVisible: !document.getElementById('token-normal-display')?.classList.contains('hidden'),
            awsVisible: !document.getElementById('token-aws-message')?.classList.contains('hidden'),
        },
        // Capture cost accumulator state for session switching isolation
        costAccumulator: window.sessionCostAccumulator ? {
            turn: window.sessionCostAccumulator.turn,
            session: window.sessionCostAccumulator.session,
            strategic: window.sessionCostAccumulator.strategic,
            tactical: window.sessionCostAccumulator.tactical,
            strategicTurnIn: window.sessionCostAccumulator.strategicTurnIn,
            strategicTurnOut: window.sessionCostAccumulator.strategicTurnOut,
            strategicTurnCost: window.sessionCostAccumulator.strategicTurnCost,
            tacticalTurnIn: window.sessionCostAccumulator.tacticalTurnIn,
            tacticalTurnOut: window.sessionCostAccumulator.tacticalTurnOut,
            tacticalTurnCost: window.sessionCostAccumulator.tacticalTurnCost,
            strategicSessionIn: window.sessionCostAccumulator.strategicSessionIn,
            strategicSessionOut: window.sessionCostAccumulator.strategicSessionOut,
            tacticalSessionIn: window.sessionCostAccumulator.tacticalSessionIn,
            tacticalSessionOut: window.sessionCostAccumulator.tacticalSessionOut,
            lastStmtPhase: window.sessionCostAccumulator.lastStmtPhase,
            lastTurnNumber: window.sessionCostAccumulator.lastTurnNumber,
            sessionId: window.sessionCostAccumulator.sessionId,
        } : null,
    };
}

/**
 * Captures per-execution state variables from the global state object.
 */
function _captureExecutionState() {
    return {
        currentStatusId: state.currentStatusId,
        isRestTaskActive: state.isRestTaskActive,
        activeRestTaskId: state.activeRestTaskId,
        isConversationAgentActive: state.isConversationAgentActive,
        isGenieCoordinationActive: state.isGenieCoordinationActive,
        isInFastPath: state.isInFastPath,
        currentTaskId: state.currentTaskId,
        currentTurnNumber: state.currentTurnNumber,
        currentProvider: state.currentProvider,
        currentModel: state.currentModel,
        profileId: state.currentSessionProfileId,
        pendingSubtaskPlanningEvents: [...state.pendingSubtaskPlanningEvents],
        pendingKnowledgeRetrievalEvent: state.pendingKnowledgeRetrievalEvent
            ? { ...state.pendingKnowledgeRetrievalEvent } : null,
        lastRagCaseData: state.lastRagCaseData
            ? { ...state.lastRagCaseData } : null,
    };
}

/**
 * Captures module-level state from genie and conversation agent handler modules.
 */
function _captureHandlerState() {
    return {
        genie: {
            activeCoordination: genieState.activeCoordination,
            slaveProgress: JSON.parse(JSON.stringify(genieState.slaveProgress)),
            startTime: genieState.startTime,
            availableProfiles: [...(genieState.availableProfiles || [])],
            profilesInvoked: [...(genieState.profilesInvoked || [])],
            profileTag: genieState.profileTag,
            selectedProfiles: [...(genieState.selectedProfiles || [])],
            synthesisStarted: genieState.synthesisStarted || false,
            profilesConsulted: [...(genieState.profilesConsulted || [])],
        },
        conversationAgent: {
            activeExecution: conversationAgentState.activeExecution,
            toolProgress: JSON.parse(JSON.stringify(conversationAgentState.toolProgress)),
            startTime: conversationAgentState.startTime,
            availableTools: [...(conversationAgentState.availableTools || [])],
            toolsUsed: [...(conversationAgentState.toolsUsed || [])],
            profileTag: conversationAgentState.profileTag,
        },
    };
}

/**
 * Restores status window header DOM state from a snapshot.
 */
function _restoreHeaderState(h) {
    const statusTitle = DOM.statusTitle || document.getElementById('status-title');
    if (statusTitle) statusTitle.textContent = h.statusTitle;

    if (DOM.taskIdValue) DOM.taskIdValue.textContent = h.taskIdValue;
    if (DOM.taskIdDisplay) DOM.taskIdDisplay.classList.toggle('hidden', !h.taskIdVisible);

    const promptNameDisplay = document.getElementById('prompt-name-display');
    if (promptNameDisplay) promptNameDisplay.innerHTML = h.promptNameHTML;

    // Thinking indicator
    if (h.thinkingActive) {
        DOM.thinkingIndicator?.classList.remove('hidden');
        DOM.thinkingIndicator?.classList.add('flex');
        DOM.promptNameDisplay?.classList.add('hidden');
    } else {
        DOM.thinkingIndicator?.classList.add('hidden');
        DOM.thinkingIndicator?.classList.remove('flex');
        DOM.promptNameDisplay?.classList.remove('hidden');
    }

    // Knowledge banner
    const knowledgeBanner = document.getElementById('knowledge-banner');
    if (knowledgeBanner) knowledgeBanner.classList.toggle('hidden', !h.knowledgeBannerVisible);
    const collectionsList = document.getElementById('knowledge-collections-list');
    if (collectionsList) collectionsList.textContent = h.knowledgeCollectionsList;

    // Token display (restore raw textContent values)
    const t = h.tokenData;
    if (t) {
        const el = (id) => document.getElementById(id);
        if (el('statement-input-tokens')) el('statement-input-tokens').textContent = t.statementInput;
        if (el('statement-output-tokens')) el('statement-output-tokens').textContent = t.statementOutput;
        if (el('turn-input-tokens')) el('turn-input-tokens').textContent = t.turnInput;
        if (el('turn-output-tokens')) el('turn-output-tokens').textContent = t.turnOutput;
        if (el('total-input-tokens')) el('total-input-tokens').textContent = t.totalInput;
        if (el('total-output-tokens')) el('total-output-tokens').textContent = t.totalOutput;
        const turnLabel = el('turn-token-label');
        if (turnLabel) turnLabel.textContent = t.turnLabel;
        el('token-normal-display')?.classList.toggle('hidden', !t.normalVisible);
        el('token-aws-message')?.classList.toggle('hidden', !t.awsVisible);
    }

    // Restore cost accumulator (critical for session switching isolation)
    if (h.costAccumulator && window.sessionCostAccumulator) {
        Object.assign(window.sessionCostAccumulator, h.costAccumulator);
        console.log(`[Cached Restore] Restored cost accumulator: Turn=$${h.costAccumulator.turn.toFixed(6)}, Session=$${h.costAccumulator.session.toFixed(6)}`);

        // Update cost display immediately
        const turnCostEl = document.getElementById('turn-cost-value');
        const sessionCostEl = document.getElementById('session-cost-value');
        if (turnCostEl) turnCostEl.textContent = `$${h.costAccumulator.turn.toFixed(6)}`;
        if (sessionCostEl) sessionCostEl.textContent = `$${h.costAccumulator.session.toFixed(6)}`;
    }
}

/**
 * Restores per-execution state variables from a snapshot.
 * Phase container DOM refs are reset to null (stale after innerHTML restore).
 */
function _restoreExecutionState(s) {
    state.currentStatusId = s.currentStatusId;
    state.isRestTaskActive = s.isRestTaskActive;
    state.activeRestTaskId = s.activeRestTaskId;
    state.isConversationAgentActive = s.isConversationAgentActive;
    state.isGenieCoordinationActive = s.isGenieCoordinationActive;
    state.isInFastPath = s.isInFastPath;
    state.currentTaskId = s.currentTaskId;
    state.currentProvider = s.currentProvider;
    state.currentModel = s.currentModel;
    if (s.profileId) state.currentSessionProfileId = s.profileId;
    state.currentTurnNumber = s.currentTurnNumber || null;
    state.pendingSubtaskPlanningEvents = s.pendingSubtaskPlanningEvents || [];
    state.pendingKnowledgeRetrievalEvent = s.pendingKnowledgeRetrievalEvent || null;
    state.lastRagCaseData = s.lastRagCaseData || null;
    // Phase containers: stale DOM refs after innerHTML restore - reset to null.
    // New events will render at root level, which is acceptable.
    state.currentPhaseContainerEl = null;
    state.phaseContainerStack = [];
}

/**
 * Restores module-level handler state from a snapshot.
 */
function _restoreHandlerState(h) {
    if (h.genie) {
        Object.assign(genieState, {
            activeCoordination: h.genie.activeCoordination,
            slaveProgress: JSON.parse(JSON.stringify(h.genie.slaveProgress)),
            startTime: h.genie.startTime,
            availableProfiles: [...h.genie.availableProfiles],
            profilesInvoked: [...h.genie.profilesInvoked],
            profileTag: h.genie.profileTag,
            selectedProfiles: [...(h.genie.selectedProfiles || [])],
            synthesisStarted: h.genie.synthesisStarted || false,
            profilesConsulted: [...(h.genie.profilesConsulted || [])],
        });
    }
    if (h.conversationAgent) {
        Object.assign(conversationAgentState, {
            activeExecution: h.conversationAgent.activeExecution,
            toolProgress: JSON.parse(JSON.stringify(h.conversationAgent.toolProgress)),
            startTime: h.conversationAgent.startTime,
            availableTools: [...h.conversationAgent.availableTools],
            toolsUsed: [...h.conversationAgent.toolsUsed],
            profileTag: h.conversationAgent.profileTag,
        });
    }
}

/**
 * Restore dashboard split panel for a session if it had one open when last left.
 * Dashboard ID is persisted to localStorage (dashboard_state_<sessionId>) on session switch.
 */
function _restoreDashboardState(sessionId) {
    try {
        const dashId = localStorage.getItem(`dashboard_state_${sessionId}`);
        if (!dashId) return;
        loadRendererModule('dashboard').then(m => { if (!m) throw new Error('dashboard is not installed in this deployment'); return m; }).then(({ openDashboardPanel }) => {
            openDashboardPanel({ dashboard_id: dashId });
        }).catch(() => { /* dashboard component unavailable */ });
    } catch (e) { /* parse error or unavailable */ }
}

/**
 * Reopen the Files panel for a session that had it open when last left (or before a reload),
 * on the file that was selected. The marker is the renderer's (`files_state_<sessionId>`).
 */
function _restoreFilesState(sessionId) {
    let info;
    try { info = JSON.parse(localStorage.getItem(`files_state_${sessionId}`) || 'null'); } catch (_) { info = null; }
    if (!info || !info.open) return;
    import('/api/v1/components/files/renderer').then(({ openFilesPanel }) => {
        openFilesPanel(info.focus ? { focus: info.focus } : {});
    }).catch(() => { /* files component unavailable */ });
}

/**
 * Restore flow split panel for a session if it had one open when last left.
 * Flow spec is persisted to localStorage (flow_state_<sessionId>) on session switch.
 */
function _restoreFlowState(sessionId) {
    let spec;
    try {
        const saved = localStorage.getItem(`flow_state_${sessionId}`);
        if (!saved) return;
        spec = JSON.parse(saved);
    } catch (e) { return; /* parse error */ }

    const open = (s) => loadRendererModule('flow').then(m => { if (!m) throw new Error('flow is not installed in this deployment'); return m; })
        .then(({ openFlowSplitPanel }) => openFlowSplitPanel(s))
        .catch(() => { /* flow component unavailable */ });

    // The saved snapshot is the canvas state as it was when the panel was last open —
    // its node positions can be stale (e.g. auto-layout ran server-side after a build,
    // so the DB has the correct positions but the snapshot does not). Re-fetch the
    // authoritative flow by id so the restored canvas matches the stored layout and
    // doesn't require a manual Layout click. Fall back to the snapshot on failure.
    const flowId = spec?.flow?.flow_id || spec?.flow_id;
    const token = localStorage.getItem('tda_auth_token');
    if (flowId && token) {
        fetch(`/api/v1/flows/${flowId}`, { headers: { 'Authorization': `Bearer ${token}` } })
            .then(r => (r.ok ? r.json() : null))
            .then(fresh => open(fresh ? { ...spec, flow: fresh, definition: fresh.definition } : spec))
            .catch(() => open(spec));
    } else {
        open(spec);
    }
}

/**
 * Restore canvas split panel for a session if it had one open when last left.
 * Canvas spec is persisted to localStorage (canvas_state_<sessionId>) whenever
 * the panel is closed during a session switch.  Only reopens when canvasSplitMode
 * is active so the panel is available.
 */
async function _restoreCanvasState(sessionId) {
    // Reflect the panel state the user left this session in. The presence of a
    // `canvas_state_<sessionId>` record means the panel was OPEN when last here
    // (the record is written when the panel opens and removed on manual close —
    // see the canvas renderer). Its absence means it was closed → do nothing.
    let info;
    try {
        const saved = localStorage.getItem(`canvas_state_${sessionId}`);
        if (!saved) return;
        info = JSON.parse(saved);
    } catch (e) { return; }

    let spec = { ...info, action: 'open' };

    // For persisted canvases, fetch the latest content + version history so the
    // restored panel is current, not a stale snapshot.
    if (info.canvas_id) {
        try {
            const token = localStorage.getItem('tda_auth_token');
            const r = await fetch(`/api/v1/canvases/${info.canvas_id}`, {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (r.ok) {
                const c = await r.json();
                const lang = (c.language || '').toLowerCase();
                spec = {
                    canvas_id: c.canvas_id,
                    title: c.title,
                    language: lang,
                    content: c.content,
                    sources: c.sources || null,
                    previewable: ['html', 'svg', 'markdown'].includes(lang),
                    line_count: (c.content.match(/\n/g) || []).length + 1,
                    versions: c.versions || [],
                    action: 'open',
                };
            }
        } catch (_) { /* fall back to the stored snapshot */ }
    }

    try {
        const { renderCanvas } = loadRendererModule('canvas').then(m => { if (!m) throw new Error('canvas is not installed in this deployment'); return m; });
        const tempId = `canvas-restore-${Date.now()}`;
        const tempDiv = document.createElement('div');
        tempDiv.id = tempId;
        tempDiv.style.display = 'none';
        document.body.appendChild(tempDiv);
        // action:'open' drives the panel open. With the autoPopOut transition-fallback
        // fix this now renders content (not blank). The chat's inline card already
        // exists from history replay; the temp div is just the render trigger.
        renderCanvas(tempId, { component: 'canvas', ...spec }).finally(() => tempDiv.remove());
    } catch (_) { /* canvas component unavailable */ }
}

/**
 * Reflect the KG panel state the user left this session in (event-driven, mirrors
 * _restoreCanvasState). A `kg_state_<sessionId>` record means the panel was OPEN
 * (written on open, removed on manual close — see the KG renderer). On restore we
 * re-fetch the graph for that profile and reopen; absence means it stays closed.
 */
async function _restoreKGState(sessionId) {
    const saved = localStorage.getItem(`kg_state_${sessionId}`);
    if (!saved) return; // panel was closed when last in this session
    let info;
    try { info = JSON.parse(saved); }
    catch (e) { return; }

    // Resolve the profile at restore time (marker first, else the just-loaded
    // session's profile — set before this runs). graph-spec resolves the active KG
    // via kg_profile_assignments server-side, so a fresh fetch returns the right graph.
    const profileId = info.profile_id || window.__currentProfileId || null;
    if (!profileId) return;

    try {
        const token = localStorage.getItem('tda_auth_token');
        const r = await fetch(
            `/api/v1/knowledge-graph/graph-spec?profile_id=${encodeURIComponent(profileId)}&max_nodes=500`,
            { headers: { 'Authorization': `Bearer ${token}` } });
        if (!r.ok) return;
        const data = await r.json();
        const gspec = data.spec || {};
        if (!gspec.nodes || gspec.nodes.length === 0) return; // empty graph — nothing to open
        const { openKGSplitPanel } = await import('/api/v1/components/knowledge_graph/renderer');
        openKGSplitPanel({ ...gspec, kg_id: info.kg_id, profile_id: profileId, action: 'open' });
    } catch (_) { /* KG component unavailable */ }
}

/**
 * Replays buffered REST events into the Live Status window for a child session.
 * Contains its own event-type routing logic (mirrors _dispatchRestEvent from
 * notifications.js) to avoid circular dependency issues. The replay version
 * suppresses indicator blinks and manages state flags directly during replay.
 *
 * Called when switching to a session with an active REST event buffer but no cached UI.
 * @param {string} sessionId - The session ID whose buffer to replay.
 */
function _replayRestEventBuffer(sessionId) {
    const buffer = state.restEventBuffer[sessionId];
    if (!buffer || buffer.events.length === 0) return;

    // Reset status window for replay
    DOM.statusWindowContent.innerHTML = '';
    const statusTitle = DOM.statusTitle || document.getElementById('status-title');
    if (statusTitle) statusTitle.textContent = 'Live Status';

    // Suppress indicator blinks during replay
    state.isViewingHistoricalTurn = true;

    for (const event of buffer.events) {
        const eventType = event.type;
        if (!eventType) continue;

        // Skip transient events that don't need replay
        if (eventType === 'status_indicator_update') continue;

        // Token updates → direct to token display
        if (eventType === 'token_update') {
            UI.updateTokenDisplay(event);
            continue;
        }

        // Session model updates → metadata only
        if (eventType === 'session_model_update') {
            const payload = event.payload || event;
            state.currentProvider = payload.provider || state.currentProvider;
            state.currentModel = payload.model || state.currentModel;
            UI.updateStatusPromptName(payload.provider, payload.model);
            continue;
        }

        // RAG retrieval → store data without blink
        if (eventType === 'rag_retrieval') {
            state.lastRagCaseData = event;
            continue;
        }

        // Knowledge retrieval → update indicator without blink
        if (eventType === 'knowledge_retrieval') {
            const collections = event.data?.collections || [];
            const documentCount = event.data?.document_count || 0;
            UI.updateKnowledgeIndicator(collections, documentCount);
        }

        // --- Conversation Agent events (conversation_with_tools profile) ---
        if (eventType.startsWith('conversation_agent') ||
            eventType.startsWith('conversation_llm') ||
            eventType.startsWith('conversation_tool')) {
            const payload = event.payload || event;
            // Set state flags during replay
            if (eventType === 'conversation_agent_start') state.isConversationAgentActive = true;
            if (eventType === 'conversation_agent_complete') state.isConversationAgentActive = false;
            UI.updateStatusWindow({
                step: eventType, details: payload, type: eventType
            }, eventType === 'conversation_agent_complete', 'conversation_agent');
            continue;
        }

        // --- Knowledge Retrieval events (rag_focused + conversation_with_tools) ---
        if (eventType.startsWith('knowledge_') ||
            eventType === 'rag_llm_step' ||
            eventType === 'knowledge_search_complete') {
            UI.updateStatusWindow({
                step: eventType, details: event.payload || event, type: eventType
            }, false, 'knowledge_retrieval');
            continue;
        }

        // --- LLM execution events (llm_only profile) ---
        if (eventType === 'llm_execution' || eventType === 'llm_execution_complete') {
            UI.updateStatusWindow({
                step: eventType, details: event.payload || event, type: eventType
            }, eventType === 'llm_execution_complete', 'conversation_agent');
            continue;
        }

        // --- Skill events (pre-processing, all profile types) ---
        if (eventType === 'skills_applied') {
            UI.updateStatusWindow({
                step: 'Skills applied',
                details: event.payload || {},
                type: 'skills_applied'
            }, false, 'skills');
            continue;
        }

        // --- Lifecycle events (all profile types) ---
        if (eventType === 'execution_start' || eventType === 'execution_complete' ||
            eventType === 'execution_error' || eventType === 'execution_cancelled') {
            const payload = event.payload || event;
            // Capture turn number from execution_start during replay
            if (eventType === 'execution_start' && payload.turn_id) {
                state.currentTurnNumber = payload.turn_id;
            }
            const isFinal = eventType !== 'execution_start';
            UI.updateStatusWindow({
                step: eventType, details: payload, type: eventType
            }, isFinal, 'lifecycle');
            continue;
        }

        // --- Session name events ---
        if (eventType === 'session_name' || eventType === 'session_name_generation_start' ||
            eventType === 'session_name_generation_complete') {
            UI.updateStatusWindow(event, false, 'session_name');
            continue;
        }

        // --- Context Window Snapshot events (all profile types) ---
        if (eventType === 'context_window_snapshot') {
            const snapshotPayload = event.payload || {};
            const snapshotHtml = renderContextWindowSnapshot(snapshotPayload);
            UI.updateStatusWindow({
                step: event.step || 'Context Window Assembly',
                details: snapshotHtml,
                type: 'context_window_snapshot'
            }, true, 'context_window');
            continue;
        }

        // --- Default: planner/executor events (tool_enabled profile) → 'rest' source ---
        const isFinal = (eventType === 'final_answer' || eventType === 'error' || eventType === 'cancelled');
        UI.updateStatusWindow(event, isFinal, 'rest', buffer.taskId);
    }

    state.isViewingHistoricalTurn = false;

    // Set execution state based on buffer completeness
    if (!buffer.isComplete) {
        state.isRestTaskActive = true;
        state.activeRestTaskId = buffer.taskId;
        UI.setExecutionState(true);
    }
}

/**
 * Loads a specific session's history and data into the UI.
 * @param {string} sessionId - The ID of the session to load.
 * @param {boolean} [isNewSession=false] - Flag to skip redundant checks if it's a new session.
 */
export async function handleLoadSession(sessionId, isNewSession = false, force = false) {
    // Skip loading only if session ID matches AND session is already loaded in UI
    // (`force` re-reads the session that is already on screen — S8: after a regenerate, an
    // adopt or a branch switch changed its SHAPE on the server, the one source of lineage).
    if (state.currentSessionId === sessionId && state.sessionLoaded && !isNewSession && !force) return;
    // the composer's receipt belongs to the session it was drawn for — never to the next one
    if (state.currentSessionId !== sessionId) { try { if (window.Composer) window.Composer.clear(); } catch (_) { /* no-op */ } }

    // --- Session stream isolation: save current session's full UI state before switching ---
    // If the current session has an active stream, cache its DOM state, header state,
    // execution mode flags, and handler module state so we can restore everything
    // instantly when the user switches back (avoids server reload).
    const previousSessionId = state.currentSessionId;
    if (previousSessionId && (state.activeStreamSessions.has(previousSessionId) || state.activeRestSessions.has(previousSessionId))) {
        state.sessionUiCache[previousSessionId] = {
            chatHTML: DOM.chatLog.innerHTML,
            statusHTML: DOM.statusWindowContent.innerHTML,
            statusTurn: DOM.statusWindowContent.dataset.laTurn || '',   // S1.1 depth 3: which turn the cached panel shows
            headerState: _captureHeaderState(),
            executionState: _captureExecutionState(),
            handlerState: _captureHandlerState(),
        };
        console.log(`[handleLoadSession] Cached full UI state for active session ${previousSessionId}`);
    }

    // Close canvas split panel when switching sessions (prevents cross-session contamination).
    // Save the canvas spec to localStorage first so we can restore it when switching back.
    try {
        const splitPanel = document.getElementById('canvas-split-panel');
        if (splitPanel && splitPanel.classList.contains('canvas-split--open')) {
            const { getOpenCanvasState, closeSplitPanel } = loadRendererModule('canvas').then(m => { if (!m) throw new Error('canvas is not installed in this deployment'); return m; });
            // The open marker for this session already exists (written when the panel
            // opened). Refresh its content snapshot with the live editor content so
            // unsaved edits survive the switch, but PRESERVE the open marker + canvas_id
            // — this is a programmatic close, not a user close, so it must restore on
            // return. (Manual close is the only thing that clears the marker.)
            if (previousSessionId) {
                try {
                    const live = getOpenCanvasState() || {};
                    const existing = JSON.parse(localStorage.getItem(`canvas_state_${previousSessionId}`) || '{}');
                    localStorage.setItem(`canvas_state_${previousSessionId}`,
                        JSON.stringify({ ...existing, ...live, action: 'open' }));
                } catch (_) { /* keep existing marker as-is */ }
            }
            closeSplitPanel();
        }
        // Panel not open at switch-away → leave state as-is. A manual close already
        // cleared the marker; never-opened sessions have none. (No clear here.)
    } catch (e) { /* canvas component may not be loaded */ }

    // Close KG split panel when switching sessions
    try {
        const kgPanel = document.getElementById('kg-split-panel');
        if (kgPanel && kgPanel.classList.contains('kg-split--open')) {
            const { closeKGSplitPanel } = await import('/api/v1/components/knowledge_graph/renderer');
            closeKGSplitPanel();  // programmatic — preserves the marker so it restores on return
        }
    } catch (e) { /* knowledge_graph component may not be loaded */ }

    // Close scheduler split panel when switching sessions
    try {
        const schedPanel = document.getElementById('scheduler-split-panel');
        if (schedPanel && schedPanel.classList.contains('sched-split--open')) {
            const { closeSchedulerSplitPanel } = await import('/api/v1/components/scheduler/renderer');
            closeSchedulerSplitPanel();
        }
    } catch (e) { /* scheduler component may not be loaded */ }

    // Close flow split panel when switching sessions; save spec for restore.
    try {
        const flowPanel = document.getElementById('flow-split-panel');
        if (flowPanel && flowPanel.classList.contains('flow-split--open')) {
            const { getOpenFlowState, closeFlowSplitPanel } = await loadRendererModule('flow').then(m => { if (!m) throw new Error('flow is not installed in this deployment'); return m; });
            const flowState = getOpenFlowState();
            if (flowState && previousSessionId) {
                localStorage.setItem(`flow_state_${previousSessionId}`, JSON.stringify(flowState));
            }
            closeFlowSplitPanel();
        } else if (previousSessionId) {
            localStorage.removeItem(`flow_state_${previousSessionId}`);
        }
    } catch (e) { /* flow component may not be loaded */ }

    // Close dashboard split panel when switching sessions; save which dashboard was open.
    try {
        const dashPanel = document.getElementById('dashboard-split-panel');
        if (dashPanel && dashPanel.classList.contains('dash-split--open')) {
            const { getOpenDashboardState, closeDashboardPanel } = await loadRendererModule('dashboard').then(m => { if (!m) throw new Error('dashboard is not installed in this deployment'); return m; });
            const dashId = getOpenDashboardState();
            if (dashId && previousSessionId) {
                localStorage.setItem(`dashboard_state_${previousSessionId}`, dashId);
            }
            closeDashboardPanel();
        } else if (previousSessionId) {
            localStorage.removeItem(`dashboard_state_${previousSessionId}`);
        }
    } catch (e) { /* dashboard component may not be loaded */ }

    // Close Agent Memory split panel when switching sessions (not session-bound —
    // it shows user-level memory — so just close it, no per-session state to save).
    try {
        const memPanel = document.getElementById('memory-split-panel');
        if (memPanel && memPanel.classList.contains('mem-split--open')) {
            const { closeMemorySplitPanel } = await import('/api/v1/components/memory/renderer');
            closeMemorySplitPanel();
        }
    } catch (e) { /* memory component may not be loaded */ }

    // Close the Files split panel when switching sessions. Its `files_state_<sid>` marker (written
    // by the renderer on open, removed only by the user's own close) stays, so returning to the
    // session reopens Files on the same file (_restoreFilesState).
    try {
        const filesPanel = document.getElementById('files-split-panel');
        if (filesPanel && filesPanel.classList.contains('files-split--open')) {
            const { closeFilesPanel } = await import('/api/v1/components/files/renderer');
            closeFilesPanel();
        }
    } catch (e) { /* files component may not be loaded */ }

    // Close the Database Navigator split panel when switching sessions (it keeps its own
    // last source / selection in localStorage, so re-opening restores where you were).
    try {
        const dbnavPanel = document.getElementById('dbnav-split-panel');
        if (dbnavPanel && dbnavPanel.classList.contains('dbnav-split--open')) {
            const { closeDatabaseNavigatorPanel } = await import('/api/v1/components/database_navigator/renderer');
            closeDatabaseNavigatorPanel();
        }
    } catch (e) { /* database_navigator component may not be loaded */ }

    // Close the EXPLAIN Plan panel when switching sessions (its plans belong to what you were
    // looking at, not to the next session).
    try {
        const planPanel = document.getElementById('explain-plan-split-panel');
        if (planPanel && planPanel.classList.contains('xplan-split--open')) {
            const { closeExplainPlanPanel } = await import('/api/v1/components/explain_plan/renderer');
            closeExplainPlanPanel();
        }
    } catch (e) { /* explain_plan component may not be loaded */ }

    // Close the Actions panel when switching sessions (its pending and confirmed acts belong
    // to the conversation you were reading).
    try {
        const actPanel = document.getElementById('action-split-panel');
        if (actPanel && actPanel.classList.contains('act-split--open')) {
            const { closeActionsPanel } = await import('/api/v1/components/action/renderer');
            closeActionsPanel();
        }
    } catch (e) { /* action component may not be loaded */ }
    // …and its two siblings, for the same reason: the governed reads and the proposals the
    // OBDA-READ / OBDA-MODEL panels list are this conversation's.
    try {
        const rp = document.getElementById('obda-read-split-panel');
        if (rp && rp.classList.contains('obda-read-split--open')) {
            const { closeObdaReadPanel } = await import('/api/v1/components/obda_read/renderer');
            closeObdaReadPanel();
        }
    } catch (e) { /* obda_read component may not be loaded */ }
    try {
        const mp = document.getElementById('obda-model-split-panel');
        if (mp && mp.classList.contains('obda-model-split--open')) {
            const { closeObdaModelPanel } = await import('/api/v1/components/obda_model/renderer');
            closeObdaModelPanel();
        }
    } catch (e) { /* obda_model component may not be loaded */ }

    // --- MODIFICATION START: Clear task ID display on session load ---
    UI.updateTaskIdDisplay(null);
    // --- MODIFICATION END ---

    // --- MODIFICATION START: Remove highlight on load ---
    UI.removeHighlight(sessionId);
    // --- MODIFICATION END ---

    // --- MODIFICATION START: Hide profile override warning banner when switching sessions ---
    const profileWarningBanner = document.getElementById('profile-override-warning-banner');
    if (profileWarningBanner) {
        profileWarningBanner.classList.add('hidden');
    }
    // --- MODIFICATION END ---

    // --- Session stream isolation: fast-path restore for sessions with active streams ---
    // If the target session has a running stream AND cached UI state, restore from
    // cache instead of reloading from the server. This avoids unnecessary API calls
    // and preserves the live execution context (status window header, content, chat progress).
    const cached = state.sessionUiCache[sessionId];
    if (cached && (state.activeStreamSessions.has(sessionId) || state.activeRestSessions.has(sessionId))) {
        console.log(`[handleLoadSession] Restoring cached UI for active session ${sessionId}`);
        state.currentSessionId = sessionId;
        state.currentSessionProfileId = cached.executionState?.profileId || state.currentSessionProfileId;
        window.__currentProfileId = state.currentSessionProfileId || null;
        state.sessionLoaded = true;
        localStorage.setItem('currentSessionId', sessionId);
        window.__currentSessionId = sessionId;

        // Restore cached DOM state (chat canvas + status window content)
        DOM.chatLog.innerHTML = cached.chatHTML;
        DOM.statusWindowContent.innerHTML = cached.statusHTML;
        DOM.statusWindowContent.dataset.laTurn = cached.statusTurn || '';

        // Restore full status window header state (title, tokens, model, knowledge, thinking)
        if (cached.headerState) _restoreHeaderState(cached.headerState);

        // Restore execution mode state variables (status ID, mode flags, provider/model)
        if (cached.executionState) _restoreExecutionState(cached.executionState);

        // Restore handler module state (genie coordination, conversation agent progress)
        if (cached.handlerState) _restoreHandlerState(cached.handlerState);

        UI.setExecutionState(true);

        // Update sidebar highlighting and session title
        document.querySelectorAll('.session-item').forEach(item => {
            item.classList.toggle('active', item.dataset.sessionId === sessionId);
        });
        const activeItem = document.getElementById(`session-${sessionId}`);
        const nameSpan = activeItem ? activeItem.querySelector('.session-name-span') : null;
        if (nameSpan) {
            updateActiveSessionTitle(nameSpan.textContent.trim());
        }

        // Restore canvas state if this session had one open
        _restoreCanvasState(sessionId);
        _restoreKGState(sessionId);
        _restoreFlowState(sessionId);
        _restoreDashboardState(sessionId);
        _restoreFilesState(sessionId);

        // Restore any ephemeral next-step suggestions for this (still-active) session.
        renderNextStepDock(sessionId);

        DOM.userInput.focus();
        return;
    }

    try {
        const data = await API.loadSession(sessionId);
        state.currentSessionId = sessionId;
        state.sessionLoaded = true; // Mark session as loaded in UI

        // Idle sessions carry no live suggestions (they're ephemeral, never persisted);
        // this hides any dock left over from the previously-viewed session.
        renderNextStepDock(sessionId);

        // Persist session ID to localStorage for browser refresh persistence
        localStorage.setItem('currentSessionId', sessionId);
        window.__currentSessionId = sessionId;

        state.currentProvider = data.provider || state.currentProvider;
        state.currentModel = data.model || state.currentModel;

        // --- MODIFICATION START: Restore feedback state from session data ---
        // S4 — which turns a fallback served (the `resilience` stamp on the persisted turn):
        // filled BEFORE the messages render so the answer's `served by` chip can read it.
        state.servedByTurn = {};
        try {
            (Array.isArray(data.workflow_history) ? data.workflow_history : []).forEach((t, i) => {
                const sb = t && t.resilience && t.resilience.served_by;
                if (sb) state.servedByTurn[t.turn != null ? t.turn : (i + 1)] = sb;
            });
        } catch (_) { /* a chip must never break a session load */ }
        if (data.feedback_by_turn) {
            state.feedbackByTurn = { ...data.feedback_by_turn };
        } else {
            state.feedbackByTurn = {};
        }
        // --- MODIFICATION END ---

        // Hide welcome screen when loading a session with history
        if (window.hideWelcomeScreen) {
            window.hideWelcomeScreen();
        }

        // Initialize cost accumulator and fetch last turn costs
        // This ensures costs display correctly immediately when switching between sessions
        if (window.sessionCostAccumulator) {
            window.sessionCostAccumulator.turn = 0;
            window.sessionCostAccumulator.session = 0;
            window.sessionCostAccumulator.strategic = 0;
            window.sessionCostAccumulator.tactical = 0;
            window.sessionCostAccumulator.strategicTurnIn = 0;
            window.sessionCostAccumulator.strategicTurnOut = 0;
            window.sessionCostAccumulator.strategicTurnCost = 0;
            window.sessionCostAccumulator.tacticalTurnIn = 0;
            window.sessionCostAccumulator.tacticalTurnOut = 0;
            window.sessionCostAccumulator.tacticalTurnCost = 0;
            window.sessionCostAccumulator.strategicSessionIn = 0;
            window.sessionCostAccumulator.strategicSessionOut = 0;
            window.sessionCostAccumulator.tacticalSessionIn = 0;
            window.sessionCostAccumulator.tacticalSessionOut = 0;
            window.sessionCostAccumulator.lastStmtPhase = null;
        }

        // Fetch last turn's cost data to initialize display
        // This provides immediate accurate costs when switching sessions
        const workflowHistory = data.workflow_history || [];
        const lastTurn = workflowHistory.length > 0 ? workflowHistory[workflowHistory.length - 1] : null;

        if (lastTurn) {
            // Use backend-provided costs if available (for sessions created after the fix)
            const lastTurnCost = lastTurn.turn_cost || 0;
            // The session's cost is the SESSION's fact (Σ every turn, siblings of a regenerate
            // included) served on the payload — never the last turn's frozen snapshot, which is
            // "cost at the time that turn ran" and moved with which answer was adopted. The
            // snapshot stays the fallback for a backend that does not serve the total.
            const sessionCost = (data.session_cost_usd != null) ? (parseFloat(data.session_cost_usd) || 0) : (lastTurn.session_cost_usd || 0);

            if (window.sessionCostAccumulator) {
                window.sessionCostAccumulator.turn = lastTurnCost;
                window.sessionCostAccumulator.session = sessionCost;
                // the live ledger builds on top of this authoritative base (a turn run before any
                // plan reload seeded it must not restart the session cost from zero)
                window.sessionCostAccumulator.sessionBaseCost = sessionCost;
                window.sessionCostAccumulator.sessionCalls = {};
            }

            console.log(`[Session Load] Initialized costs from last turn: Turn=$${lastTurnCost.toFixed(6)}, Session=$${sessionCost.toFixed(6)}`);
        }

        // Update cost display immediately
        const turnCostEl = document.getElementById('turn-cost-value');
        const sessionCostEl = document.getElementById('session-cost-value');
        const displayTurnCost = window.sessionCostAccumulator?.turn || 0;
        const displaySessionCost = window.sessionCostAccumulator?.session || 0;

        if (turnCostEl) turnCostEl.textContent = `$${displayTurnCost.toFixed(6)}`;
        if (sessionCostEl) sessionCostEl.textContent = `$${displaySessionCost.toFixed(6)}`;

        // Clear dual-model tooltips
        ['metric-card-statement','metric-card-turn','metric-card-session',
         'metric-card-turn-cost','metric-card-session-cost'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.removeAttribute('data-tooltip-html');
        });

        DOM.chatLog.innerHTML = '';
        if (data.history && data.history.length > 0) {
            // --- MODIFICATION START: Pass turn_id and isValid during history load ---
            // NOTE: Genie coordination cards and conversation agent cards are NO LONGER
            // rendered inline in chat. All execution details (tool calls, slave invocations)
            // are shown in the Live Status window only. This keeps the conversation pane
            // clean and focused on Q&A. Historical execution data is available when
            // clicking on a turn to reload its plan/trace in the status panel.

            // Suppress component panel auto-open during history replay. Stored canvas
            // messages carry action:'open' in their spec, which would otherwise re-open
            // the split panel on every session load. The reopen decision belongs solely
            // to _restoreCanvasState/_restoreFlowState/_restoreDashboardState (driven by
            // saved per-session state), so a manually-closed panel stays closed.
            // renderCanvas evaluates its auto-open decision synchronously at call time,
            // so this flag reliably covers the synchronous addMessage replay loop.
            window.__suppressComponentReplayOpen = true;
            // S8 — the session's lineage (sibling sets + branches), served only when something
            // regenerated or branched. The VIEW is the active branch; a turn outside it is kept
            // in the record but not rendered; of a sibling set only the ADOPTED answer is the
            // bubble — the others live in the experiment card mounted under it.
            if (window.TurnLineage && window.TurnLineage.setEnabled) window.TurnLineage.setEnabled(!!data.lineage_enabled);
            const lineage = (data.lineage && typeof data.lineage === 'object') ? data.lineage : null;
            const viewSet = lineage && Array.isArray(lineage.branches?.view) ? new Set(lineage.branches.view) : null;
            const siblingOfTurn = new Map();   // turn → its sibling set (root + members)
            if (lineage && lineage.siblings) {
                for (const [root, set] of Object.entries(lineage.siblings)) {
                    for (const t of (set.turns || [])) siblingOfTurn.set(Number(t.turn), { root: Number(root), set });
                }
            }
            const siblingMessages = new Map();  // turn → the assistant message (for the experiment card)
            try {
                // Simulate turn IDs based on message pairs for existing sessions
                let currentTurnId = 1;
                for (let i = 0; i < data.history.length; i++) {
                    const msg = data.history[i];
                    // Default to true if isValid flag is missing (for older sessions)
                    const isValid = msg.isValid === undefined ? true : msg.isValid;
                    // Get profile_tag from message (for user messages)
                    const profileTag = msg.profile_tag || null;
                    // Get is_session_primer flag (defaults to false for older sessions)
                    const isSessionPrimer = msg.is_session_primer || false;
                    // The message's own turn number (every served message carries one; the
                    // counter is the fallback for a history written before turn numbers).
                    const tn = Number.isInteger(msg.turn_number) ? msg.turn_number : null;

                    // S8: a turn outside the active branch's view is in the record, not on screen
                    if (viewSet && tn != null && !viewSet.has(tn)) continue;

                    if (msg.role === 'assistant') {
                        const sib = tn != null ? siblingOfTurn.get(tn) : null;
                        if (sib) siblingMessages.set(tn, msg);
                        // S8: of a sibling set only the adopted answer is the bubble
                        if (sib && sib.set.adopted !== tn) continue;
                        const turnIdToUse = tn != null ? tn : currentTurnId;
                        // Pass the calculated turn ID, validity, and primer flag for assistant messages
                        UI.addMessage(msg.role, msg.content, turnIdToUse, isValid, msg.source, null, isSessionPrimer);
                        const bubble = DOM.chatLog.lastElementChild;
                        if (bubble && tn != null) bubble.dataset.lineageTurn = String(tn);
                        // S1.1 — reload parity: on a streaming deployment the answer wears the SAME
                        // work line, receipt and verdict it wore live, rebuilt from its persisted turn
                        // through the same reducer (the server says whether it streams: `living_answer`).
                        if (bubble && data.living_answer && window.LivingAnswer && window.LivingAnswer.restore) {
                            try {
                                window.__livingAnswerEnabled = true;
                                const wfAll = Array.isArray(data.workflow_history) ? data.workflow_history : [];
                                const wfTurn = wfAll.find(t => t && Number(t.turn) === Number(turnIdToUse)) || null;
                                if (wfTurn && !isSessionPrimer) window.LivingAnswer.restore(bubble, wfTurn, { sessionId });
                            } catch (e) { console.warn('[living-answer] reload rebuild failed', e); }
                        }
                        if (sib && bubble && window.TurnLineage) {
                            try { window.TurnLineage.mountExperiment(bubble, sib.root, sib.set, { sessionId, history: data.history, workflowHistory: data.workflow_history || [] }); }
                            catch (e) { console.warn('[lineage] experiment card failed', e); }
                        }
                        // PROP-011 — a turn that stopped to ask wears its question card on reload too:
                        // pending (answerable), or closed with its outcome
                        if (bubble && window.NeedsInput) {
                            try {
                                const wfAll = Array.isArray(data.workflow_history) ? data.workflow_history : [];
                                const wfTurn = wfAll.find(t => t && Number(t.turn) === Number(turnIdToUse)) || null;
                                const ir = wfTurn && wfTurn.input_request;
                                if (ir) {
                                    // the turn record is the question as RAISED (pending); its outcome is the
                                    // store's — the session's pending one (with the binding), else the live
                                    // record by id, else closed: a snapshot alone never renders pending
                                    const live = (data.pending_input_request && data.pending_input_request.id === ir.id)
                                        ? data.pending_input_request
                                        : ((data.input_requests && data.input_requests[ir.id]) || { status: 'superseded' });
                                    window.NeedsInput.attach(bubble, Object.assign({}, ir, live));
                                }
                            } catch (e) { console.warn('[needs-input] reload card failed', e); }
                        }
                        currentTurnId = (tn != null ? tn : currentTurnId) + 1; // the next turn after this one
                    } else {
                        // User messages: include attachment chips if present
                        let displayContent = msg.content;
                        // S9: the turn's attachment sources decorate the chips ("used as source ·
                        // OCR · 3 rows", the image as its own thumbnail) — from the persisted stamp
                        const wfTurn = (tn != null && Array.isArray(data.workflow_history)) ? data.workflow_history[tn - 1] : null;
                        const attSources = wfTurn && wfTurn.attachment_sources && Array.isArray(wfTurn.attachment_sources.sources) ? wfTurn.attachment_sources.sources : null;
                        if (msg.attachments && msg.attachments.length > 0) {
                            displayContent = msg.content + renderAttachmentChips(msg.attachments, attSources);
                        }
                        UI.addMessage(msg.role, displayContent, null, isValid, msg.source, profileTag, isSessionPrimer, msg.extension_specs || null, msg.skill_specs || null, msg.origin || null);
                        const bubble = DOM.chatLog.lastElementChild;
                        if (bubble && attSources && window.AttachmentSources) {
                            try { window.AttachmentSources.hydrate(bubble, sessionId); } catch (e) { console.warn('[attachments] thumbnails failed', e); }
                        }
                        if (bubble && tn != null) {
                            bubble.dataset.lineageTurn = String(tn);
                            bubble.dataset.lineageQuestion = String(msg.content || '');
                            if (lineage && window.TurnLineage) {
                                try { window.TurnLineage.mountBranchSwitch(bubble, tn, lineage, sessionId); }
                                catch (e) { console.warn('[lineage] branch switch failed', e); }
                            }
                        }
                    }
                }
            } finally {
                // Replay done — component auto-open may resume for live renders.
                // try/finally guarantees the flag clears even if a message render
                // throws, otherwise live canvas creates would be silently suppressed.
                window.__suppressComponentReplayOpen = false;
            }
            // --- MODIFICATION END ---

            // --- Re-render extension download cards for chat_append results ---
            // Extension cards (e.g., !pdf download) are rendered into the DOM during
            // live execution but NOT persisted in session_history messages. The data IS
            // persisted in workflow_history[turn].extension_results. Re-render them here
            // so download cards survive page reloads and session switches.
            const wfHistory = data.workflow_history || [];
            for (const turn of wfHistory) {
                const extResults = turn.extension_results;
                if (!extResults || typeof extResults !== 'object') continue;

                const turnId = turn.turn || turn.turn_id || turn.turn_number;
                if (!turnId) continue;

                for (const [extName, result] of Object.entries(extResults)) {
                    if (!result.success || !result.content) continue;
                    const target = result.output_target || 'silent';
                    if (target !== 'chat_append') continue;

                    // Find the assistant message bubble for this turn via its badge.
                    // Note: .clickable-avatar[data-turn-id] matches the USER avatar (not assistant).
                    // The assistant badge (.assistant-badge) is appended to the assistant's wrapper.
                    const badge = DOM.chatLog.querySelector(`.assistant-badge[data-turn-id="${turnId}"]`);
                    if (!badge) continue;
                    const msgBubble = badge.closest('.message-bubble');
                    const msgContent = msgBubble?.querySelector('.message-content');
                    if (!msgContent) continue;

                    // Use the existing card builder (exposed on window by eventHandlers.js)
                    if (window._isExtensionBinaryContent && window._isExtensionBinaryContent(result)) {
                        const cardHtml = window._buildExtensionDownloadCard(extName, result);
                        msgContent.insertAdjacentHTML('beforeend', cardHtml);
                    } else {
                        // Non-binary chat_append (e.g., JSON text output)
                        const cardHtml = `
                            <div class="extension-output mt-3 p-3 rounded-lg" data-ext-name="${extName}"
                                 style="background: rgba(251, 191, 36, 0.05); border: 1px solid rgba(251, 191, 36, 0.15);">
                                <div class="flex items-center gap-2 mb-2">
                                    <span class="text-xs font-semibold px-2 py-0.5 rounded"
                                          style="background: rgba(251, 191, 36, 0.15); color: #fbbf24; font-family: 'JetBrains Mono', monospace;">!${extName}</span>
                                    <span class="text-xs text-gray-500">${result.content_type}</span>
                                </div>
                                <pre class="text-xs text-gray-300 whitespace-pre-wrap overflow-auto max-h-48"
                                     style="font-family: 'JetBrains Mono', monospace;">${
                                    typeof result.content === 'object' ? JSON.stringify(result.content, null, 2) : result.content
                                }</pre>
                            </div>`;
                        msgContent.insertAdjacentHTML('beforeend', cardHtml);
                    }
                }
            }

        } else {
             UI.addMessage('assistant', "I'm ready to help. How can I assist you with your Teradata system today?");
        }

        // Scroll to the beginning of the last assistant answer (not the bottom of chat).
        // Uses 'instant' to override any pending smooth scrolls from addMessage() calls above.
        const assistantMsgs = DOM.chatLog.querySelectorAll('.message-bubble:not(.justify-end)');
        const lastAssistant = assistantMsgs.length > 0 ? assistantMsgs[assistantMsgs.length - 1] : null;
        if (lastAssistant) {
            lastAssistant.scrollIntoView({ behavior: 'instant', block: 'start' });
        }

        // Explicitly pass all token fields as 0 for empty sessions to prevent stale values from previous session
        // Pass isHistorical=true to bypass terminal event detection and force normal update (reset to 0)
        console.log(`%c[Session Load] 🔍 Calling updateTokenDisplay for session ${sessionId}`, 'background: #ff00ff; color: #fff; font-weight: bold;');
        console.log(`  data.input_tokens: ${data.input_tokens}`);
        console.log(`  data.output_tokens: ${data.output_tokens}`);
        console.log(`  Passing isHistorical=true to bypass terminal event detection`);

        UI.updateTokenDisplay({
            statement_input: 0,           // Reset statement tokens (prevents stale values)
            statement_output: 0,
            turn_input: 0,                // Reset turn tokens
            turn_output: 0,
            total_input: data.input_tokens,   // Use backend data for session totals
            total_output: data.output_tokens,
            turn_cost: window.sessionCostAccumulator?.turn || 0,         // Backend-calculated cost from workflow_history
            session_cost_usd: window.sessionCostAccumulator?.session || 0  // Backend-calculated cumulative cost
        }, true);  // isHistorical=true bypasses terminal event detection, forces normal update

        // --- MODIFICATION START: Refresh feedback button states after loading history ---
        UI.refreshFeedbackButtons();
        // --- MODIFICATION END ---

        document.querySelectorAll('.session-item').forEach(item => {
            item.classList.toggle('active', item.dataset.sessionId === sessionId);
        });

        // --- NEW: Update active session title (derive from list item since name not in loadSession response) ---
        const activeItem = document.getElementById(`session-${sessionId}`);
        const nameSpan = activeItem ? activeItem.querySelector('.session-name-span') : null;
        if (nameSpan) {
            updateActiveSessionTitle(nameSpan.textContent.trim());
        }

        // --- MODIFICATION START ---
        // Explicitly update the models/profile tags for the loaded session in the UI
        UI.updateSessionModels(sessionId, data.models_used, data.profile_tags_used);

        // Get dual-model info from session data OR profile configuration (for new sessions)
        let dualModelInfo = data.dual_model_info;
        console.log('[Session Load] 🔍 Checking dual-model:', {
            sessionDualModelInfo: dualModelInfo,
            profileId: data.profile_id,
            hasConfigState: !!window.configState,
            hasProfiles: !!window.configState?.profiles,
            hasLLMConfigs: !!window.configState?.llmConfigurations
        });

        // For new sessions without dual_model_info, check the profile configuration
        if (!dualModelInfo && data.profile_id && window.configState?.profiles) {
            const currentProfile = window.configState.profiles.find(p => p.id === data.profile_id);
            console.log('[Session Load] Found profile:', currentProfile?.name, {
                hasDualModelConfig: !!currentProfile?.dualModelConfig,
                strategicModelId: currentProfile?.dualModelConfig?.strategicModelId,
                tacticalModelId: currentProfile?.dualModelConfig?.tacticalModelId
            });

            if (currentProfile?.dualModelConfig && (currentProfile.dualModelConfig.strategicModelId || currentProfile.dualModelConfig.tacticalModelId)) {
                // Profile has dual-model configured - extract model info from LLM configs
                const strategicConfig = window.configState.llmConfigurations?.find(c => c.id === currentProfile.dualModelConfig.strategicModelId);
                const tacticalConfig = window.configState.llmConfigurations?.find(c => c.id === currentProfile.dualModelConfig.tacticalModelId);
                console.log('[Session Load] LLM Configs:', {
                    strategic: strategicConfig ? `${strategicConfig.provider}/${strategicConfig.model}` : 'NOT FOUND',
                    tactical: tacticalConfig ? `${tacticalConfig.provider}/${tacticalConfig.model}` : 'NOT FOUND'
                });

                if (strategicConfig && tacticalConfig) {
                    dualModelInfo = {
                        strategicProvider: strategicConfig.provider,
                        strategicModel: strategicConfig.model,
                        tacticalProvider: tacticalConfig.provider,
                        tacticalModel: tacticalConfig.model
                    };
                    console.log('[Session Load] ✅ Extracted dual-model info from profile config:', dualModelInfo);
                }
            }
        }

        // Store dual-model info in state for use throughout the session
        state.currentDualModelInfo = dualModelInfo;
        console.log('[Session Load] Stored in state.currentDualModelInfo:', state.currentDualModelInfo);

        // Resolve CURRENT provider/model from configState so the Live Status header always
        // reflects the profile's current LLM config — not a stale value written to the
        // session file during a previous execution.  This ensures a profile LLM change is
        // visible immediately without a page refresh.
        let displayProvider = data.provider;
        let displayModel    = data.model;

        if (data.profile_id && window.configState?.profiles && window.configState?.llmConfigurations) {
            const currentProfile = window.configState.profiles.find(p => p.id === data.profile_id);
            if (currentProfile?.llmConfigurationId) {
                const currentLlm = window.configState.llmConfigurations.find(
                    c => c.id === currentProfile.llmConfigurationId
                );
                if (currentLlm?.provider && currentLlm?.model) {
                    displayProvider = currentLlm.provider;
                    displayModel    = currentLlm.model;
                }
            }
        }

        // Sync state so downstream code uses the fresh values
        state.currentProvider         = displayProvider || state.currentProvider;
        state.currentModel            = displayModel    || state.currentModel;
        state.currentSessionProfileId = data.profile_id || null;
        // Expose to global scope so component renderers (KG panel) can resolve the
        // active profile without depending on it being baked into a backend payload.
        window.__currentProfileId = data.profile_id || null;

        // This will reset the status display to the globally configured model, including dual-model info
        UI.updateStatusPromptName(displayProvider, displayModel, false, dualModelInfo);
        // --- MODIFICATION END ---

        // Initialize upload capabilities for the loaded session's provider
        initializeUploadCapabilities(sessionId);

        // Preset profile tag badge based on session's first profile
        // Only show badge if session has actual profile usage history (not for new/unused sessions)
        const sessionProfileTag = (data.profile_tags_used && data.profile_tags_used.length > 0)
            ? data.profile_tags_used[0]
            : null;

        if (sessionProfileTag && window.configState?.profiles) {
            const tagUpper = sessionProfileTag.toUpperCase();
            const matchedProfile = window.configState.profiles.find(
                p => p.tag && p.tag.toUpperCase() === tagUpper
            );
            if (matchedProfile && typeof window.showActiveTagBadge === 'function') {
                window.activeTagPrefix = `@${matchedProfile.tag} `;
                window.showActiveTagBadge(matchedProfile);
            }
        } else if (!isNewSession) {
            // Switching to an existing session with no profile history — clear stale badge
            // When isNewSession=true, preserve any manually-selected override badge
            window.activeTagPrefix = '';
            if (typeof window.hideActiveTagBadge === 'function') window.hideActiveTagBadge();
        }

        // Reset execution state for the loaded session (clean slate)
        // If this session has an active stream, processStream will resume rendering
        // events and manage execution state from that point forward.
        UI.setExecutionState(false);
        UI.setThinkingIndicator(false);

        // --- Reset status window to idle state for loaded (non-streaming) session ---
        DOM.statusWindowContent.innerHTML = '<p class="text-gray-400">Waiting for a new request...</p>';
        const statusTitle = DOM.statusTitle || document.getElementById('status-title');
        if (statusTitle) statusTitle.textContent = 'Live Status';

        // Reset knowledge banner
        const knowledgeBanner = document.getElementById('knowledge-banner');
        if (knowledgeBanner) knowledgeBanner.classList.add('hidden');

        // Reset execution mode flags to clean slate
        state.currentStatusId = 0;
        state.isRestTaskActive = false;
        state.activeRestTaskId = null;
        state.isConversationAgentActive = false;
        state.isGenieCoordinationActive = false;
        state.isInFastPath = false;
        state.currentPhaseContainerEl = null;
        state.phaseContainerStack = [];
        state.pendingSubtaskPlanningEvents = [];
        state.pendingKnowledgeRetrievalEvent = null;
        state.currentTurnNumber = null;

        // Reset handler module state for idle session
        cleanupCoordination();
        cleanupExecution();
        resetContextPanelState();
        resetComponentsPanelState();
        // Re-render Context tab if it's currently visible (avoid stale slider)
        const contextPanel = document.getElementById('context-panel');
        if (contextPanel && contextPanel.style.display !== 'none') {
            loadContextPanel();
        }

        // Check if this session has an active REST event buffer (e.g., Genie child during execution)
        const restBuffer = state.restEventBuffer[sessionId];
        if (restBuffer && !restBuffer.isComplete && state.activeRestSessions.has(sessionId)) {
            // Active REST execution: replay buffered events into status window
            _replayRestEventBuffer(sessionId);
        } else {
            // Auto-load last turn's execution data into the Live Status window.
            // Uses dynamic import() to avoid circular dependency with eventHandlers.js
            // (eventHandlers.js already imports from sessionManagement.js).
            const allAvatars = DOM.chatLog.querySelectorAll('.clickable-avatar[data-turn-id]');
            const lastAvatar = allAvatars.length > 0 ? allAvatars[allAvatars.length - 1] : null;
            if (lastAvatar) {
                import('../eventHandlers.js?v=4.214').then(({ handleReloadPlanClick }) => {
                    handleReloadPlanClick(lastAvatar);
                });
            }
            // Clean up completed buffers on visit (server has the data now)
            if (restBuffer) {
                delete state.restEventBuffer[sessionId];
            }
        }

        // Restore canvas state if this session had one open
        _restoreCanvasState(sessionId);
        _restoreKGState(sessionId);
        _restoreFlowState(sessionId);
        _restoreDashboardState(sessionId);
        _restoreFilesState(sessionId);

        // S10 — a turn still RUNNING in this session (the tab was closed or refreshed mid-turn):
        // reattach to its cursor-stamped stream and backfill silently; a no-op when nothing runs
        // or the switch is off. Fire-and-forget — the history is already on screen.
        if (typeof window.__reattachRunningTurn === 'function' && !state.activeStreamSessions.has(sessionId)) {
            window.__reattachRunningTurn(sessionId).catch(e => console.warn('[reattach] failed', e));
        }

        // Mark conversation as initialized after successful session load
        if (window.__conversationInitState) {
            window.__conversationInitState.initialized = true;
            window.__conversationInitState.lastInitTimestamp = Date.now();
            window.__conversationInitState.inProgress = false;
            console.log('[handleLoadSession] Marked conversation as initialized');
        }
    } catch (error) {
        UI.addMessage('assistant', `Error loading session: ${error.message}`);
        throw error;  // Re-throw so callers can implement fallback logic
    } finally {
        DOM.userInput.focus();
    }
}

/**
 * Handles the save action when editing a session name (Enter or Blur).
 * @param {Event} e - The event object (blur or keydown).
 */
export async function handleSessionRenameSave(e) {
    const inputElement = e.target;
    const sessionItem = inputElement.closest('.session-item');
    if (!sessionItem) return;

    const sessionId = sessionItem.dataset.sessionId;
    const newName = inputElement.value.trim();
    const originalName = inputElement.dataset.originalName;

    if (!newName || newName === originalName) {
        UI.exitSessionEditMode(inputElement, originalName);
        return;
    }

    inputElement.disabled = true;
    inputElement.style.opacity = '0.7';

    try {
        await renameSession(sessionId, newName);
        UI.exitSessionEditMode(inputElement, newName);
        UI.moveSessionToTop(sessionId);
        if (state.currentSessionId === sessionId) {
            updateActiveSessionTitle(newName);
        }
    } catch (error) {
        console.error(`Failed to rename session ${sessionId}:`, error);
        inputElement.style.borderColor = 'red';
        inputElement.disabled = false;
        // Revert to original name and exit edit mode on API error
        UI.exitSessionEditMode(inputElement, originalName);
    }
}

// --- NEW: Rename active session directly (used by header title editing) ---
export async function renameActiveSession(newName) {
    if (!state.currentSessionId) return;
    const trimmed = (newName || '').trim();
    if (!trimmed) return;
    try {
        await renameSession(state.currentSessionId, trimmed);
        updateActiveSessionTitle(trimmed);
        UI.updateSessionListItemName(state.currentSessionId, trimmed);
        UI.moveSessionToTop(state.currentSessionId);
    } catch (error) {
        console.error('Failed to rename active session via header:', error);
    }
}

/**
 * Handles the cancel action when editing a session name (Escape).
 * @param {Event} e - The event object (keydown).
 */
export function handleSessionRenameCancel(e) {
    const inputElement = e.target;
    const originalName = inputElement.dataset.originalName;
    UI.exitSessionEditMode(inputElement, originalName);
}

/**
 * Handles the click event for the delete session button.
 * @param {HTMLButtonElement} deleteButton - The delete button element that was clicked.
 */
export async function handleDeleteSessionClick(deleteButton) {
    const sessionItem = deleteButton.closest('.session-item');
    if (!sessionItem) return;

    const sessionId = sessionItem.dataset.sessionId;
    const sessionName = sessionItem.querySelector('.session-name-span')?.textContent || 'this session';

    // Check if this is a Genie parent with child sessions
    const isGenieParent = sessionItem.classList.contains('genie-master-session');
    let confirmMessage = `Are you sure you want to permanently delete '${sessionName}'? This action cannot be undone.`;
    if (isGenieParent) {
        const childCount = document.querySelectorAll(`[data-genie-parent-id="${sessionId}"]`).length;
        if (childCount > 0) {
            confirmMessage = `Are you sure you want to permanently delete '${sessionName}' and its ${childCount} child session${childCount > 1 ? 's' : ''}? This action cannot be undone.`;
        }
    }

    UI.showConfirmation(
        'Delete Session?',
        confirmMessage,
        async () => {
            try {
                const result = await deleteSession(sessionId);
                const deletedChildren = result?.deleted_children || [];
                const wasActiveSession = state.currentSessionId === sessionId || deletedChildren.includes(state.currentSessionId);

                // Clean up persisted canvas state for deleted sessions
                localStorage.removeItem(`canvas_state_${sessionId}`);
                for (const childId of deletedChildren) {
                    localStorage.removeItem(`canvas_state_${childId}`);
                }

                // Refresh the session list to show archived state.
                // refreshSessionsList() already handles switching away from the deleted session
                // internally, so no additional wasActiveSession handling is needed here.
                try {
                    const { refreshSessionsList } = await import('./configManagement.js?v=1.189');
                    await refreshSessionsList();
                    console.log('[Session Delete] Session list refreshed after archiving');
                } catch (refreshError) {
                    console.error('[Session Delete] Failed to refresh sessions list:', refreshError);
                    // Fallback: remove from DOM if refresh fails
                    UI.removeSessionAndDescendantsFromList(sessionId, deletedChildren);
                    // Fallback: handle session switch manually if refresh failed
                    if (wasActiveSession) {
                        await handleStartNewSession();
                    }
                }
            } catch (error) {
                console.error(`Failed to delete session ${sessionId}:`, error);
                UI.addMessage('assistant', `Error: Could not delete session '${sessionName}'. ${error.message}`);
            }
        }
    );
}

/**
 * Initialize utility sessions filter for the sidebar
 */
export function initializeUtilitySessionsFilter() {
    const toggle = document.getElementById('sidebar-show-utility-sessions-toggle');
    const container = document.getElementById('sidebar-show-utility-sessions-container');

    if (!toggle || !container) return;

    // Load saved preference
    const savedPref = localStorage.getItem('sidebarShowUtilitySessions');
    let showUtility = savedPref !== null ? savedPref === 'true' : true; // Default to showing
    toggle.checked = showUtility;
    container.classList.toggle('active', showUtility);

    // Function to update session visibility
    const updateSessionVisibility = () => {
        // Sync state from checkbox (external callers may set toggle.checked directly)
        showUtility = toggle.checked;
        container.classList.toggle('active', showUtility);

        const sessions = document.querySelectorAll('.session-item');
        let hasUtilitySessions = false;

        sessions.forEach(item => {
            const isTemporary = item.dataset.isTemporary === 'true';
            if (isTemporary) {
                hasUtilitySessions = true;
                item.style.display = showUtility ? '' : 'none';
            }
        });

        // Show/hide the button based on whether utility sessions exist
        // (but don't remove 'active' — 'hidden' takes precedence)
        container.classList.toggle('hidden', !hasUtilitySessions);
    };

    // Apply initial state
    updateSessionVisibility();

    // Handle button click
    container.addEventListener('click', (e) => {
        e.preventDefault();
        toggle.checked = !toggle.checked;
        localStorage.setItem('sidebarShowUtilitySessions', toggle.checked);
        updateSessionVisibility();
    });

    // Re-check visibility whenever sessions are added/removed
    // This will be called after sessions are loaded
    window.updateUtilitySessionsFilter = updateSessionVisibility;
}

/**
 * Initialize archived sessions filter for the sidebar
 */
export function initializeArchivedSessionsFilter() {
    const toggle = document.getElementById('sidebar-show-archived-sessions-toggle');
    const container = document.getElementById('sidebar-show-archived-sessions-container');

    if (!toggle || !container) return;

    // Load saved preference (default to NOT showing archived)
    const savedPref = localStorage.getItem('sidebarShowArchivedSessions');
    let showArchived = savedPref !== null ? savedPref === 'true' : false;
    toggle.checked = showArchived;
    container.classList.toggle('active', showArchived);

    // Function to update session visibility
    const updateSessionVisibility = () => {
        // Sync state from checkbox (external callers may set toggle.checked directly)
        showArchived = toggle.checked;
        container.classList.toggle('active', showArchived);

        const sessions = document.querySelectorAll('.session-item');
        sessions.forEach(item => {
            const isArchived = item.dataset.archived === 'true';
            if (isArchived) {
                item.style.display = showArchived ? '' : 'none';
                item.classList.toggle('archived-session', showArchived);
            }
        });
    };

    // Apply initial state
    updateSessionVisibility();

    // Handle button click
    container.addEventListener('click', (e) => {
        e.preventDefault();
        toggle.checked = !toggle.checked;
        localStorage.setItem('sidebarShowArchivedSessions', toggle.checked);
        updateSessionVisibility();
    });

    // Re-check visibility whenever sessions are added/removed
    // This will be called after sessions are loaded
    window.updateArchivedSessionsFilter = updateSessionVisibility;
}

// ---------------------------------------------------------------------------
// Reactive model display: update Live Status header immediately when the
// current session's profile LLM configuration is changed in the admin panel.
// configState.updateProfile() dispatches 'profile-modified' after every save.
// ---------------------------------------------------------------------------
UderiaBus.on('uderia:profile-modified', (e) => {
    const { profileId } = e.detail || {};
    if (!profileId || !state.currentSessionId) return;
    if (profileId !== state.currentSessionProfileId) return;

    if (!window.configState?.profiles || !window.configState?.llmConfigurations) return;
    const updatedProfile = window.configState.profiles.find(p => p.id === profileId);
    if (!updatedProfile?.llmConfigurationId) return;

    const updatedLlm = window.configState.llmConfigurations.find(
        c => c.id === updatedProfile.llmConfigurationId
    );
    if (!updatedLlm?.provider || !updatedLlm?.model) return;

    state.currentProvider = updatedLlm.provider;
    state.currentModel    = updatedLlm.model;
    UI.updateStatusPromptName(updatedLlm.provider, updatedLlm.model, false, state.currentDualModelInfo);
    console.log(`[profile-modified] Live Status model updated → ${updatedLlm.provider}/${updatedLlm.model}`);
});

// S8 — the one session reloader the lineage acts (adopt · switch branch · a finished regenerate) use.
if (typeof window !== 'undefined') window.__reloadSession = (sid) => handleLoadSession(sid, false, true);
