import { state } from './state.js';
import * as UI from './ui.js?v=1.287';
import * as DOM from './domElements.js';
import * as API from './api.js?v=1.2';
import { handleLoadSession } from './handlers/sessionManagement.js?v=3.209';
import { handleGenieEvent } from './handlers/genieHandler.js?v=4.208';
import { handleConversationAgentEvent } from './handlers/conversationAgentHandler.js?v=1.1';
import { renderContextWindowSnapshot } from './handlers/contextPanelHandler.js?v=1.189';

function showRestQueryNotification(message) {
    const notificationContainer = document.createElement('div');
    notificationContainer.id = 'rest-notification';
    notificationContainer.className = 'notification-banner';

    const messageElement = document.createElement('p');
    messageElement.textContent = message;

    const buttonContainer = document.createElement('div');

    const refreshButton = document.createElement('button');
    refreshButton.textContent = 'Refresh';
    refreshButton.onclick = () => {
        window.location.reload();
    };

    const closeButton = document.createElement('button');
    closeButton.textContent = 'Close';
    closeButton.onclick = () => {
        notificationContainer.remove();
    };

    buttonContainer.appendChild(refreshButton);
    buttonContainer.appendChild(closeButton);

    notificationContainer.appendChild(messageElement);
    notificationContainer.appendChild(buttonContainer);

    document.body.appendChild(notificationContainer);
}

function showReconfigurationNotification(data) {
    const overlay = document.createElement('div');
    overlay.className = 'fixed inset-0 bg-black bg-opacity-70 flex items-center justify-center z-50';

    const modal = document.createElement('div');
    modal.className = 'glass-panel rounded-xl shadow-2xl w-full max-w-lg p-6';

    const title = document.createElement('h3');
    title.className = 'text-xl font-bold mb-4 header-title';
    title.textContent = 'Application Reconfigured';

    const message = document.createElement('p');
    message.className = 'mb-4';
    message.style.color = 'var(--text-secondary)';
    message.textContent = data.message;

    const configDetails = document.createElement('pre');
    configDetails.className = 'p-4 rounded-md text-xs overflow-x-auto';
    configDetails.style.backgroundColor = 'var(--bg-tertiary)';
    configDetails.style.color = 'var(--text-secondary)';
    configDetails.textContent = JSON.stringify(data.config, null, 2);

    const buttonContainer = document.createElement('div');
    buttonContainer.className = 'flex justify-end mt-6';

    const refreshButton = document.createElement('button');
    refreshButton.className = 'px-4 py-2 rounded-md bg-teradata-orange hover:bg-teradata-orange-dark transition-colors font-semibold';
    refreshButton.textContent = 'Refresh Now';
    refreshButton.onclick = () => {
        window.location.reload();
    };

    buttonContainer.appendChild(refreshButton);

    modal.appendChild(title);
    modal.appendChild(message);
    modal.appendChild(configDetails);
    modal.appendChild(buttonContainer);

    overlay.appendChild(modal);
    document.body.appendChild(overlay);
}

function showProfileOverrideWarning(overrideProfileName, overrideProfileTag, defaultProfileTag, errorMessage) {
    const banner = document.getElementById('profile-override-warning-banner');
    const message = document.getElementById('profile-override-warning-message');
    const dismissBtn = document.getElementById('dismiss-profile-warning');

    if (!banner || !message) {
        console.error('[showProfileOverrideWarning] Required elements not found!');
        return;
    }

    const displayMessage = `Unable to use profile '${overrideProfileName}' (@${overrideProfileTag}): Missing credentials. Using default profile (@${defaultProfileTag}).`;
    message.textContent = displayMessage;
    banner.classList.remove('hidden');

    // Setup dismiss button
    if (dismissBtn) {
        dismissBtn.onclick = () => {
            banner.classList.add('hidden');
        };
    }
}

/**
 * Get human-readable step title for genie coordination events.
 */
function _getGenieStepTitle(eventType, payload) {
    switch (eventType) {
        case 'genie_start':
            return '🔮 Genie Coordinator Activated';
        case 'genie_routing': {
            const slaveCount = payload.slave_profiles?.length || 0;
            return `Consulting ${slaveCount} expert${slaveCount > 1 ? 's' : ''}`;
        }
        case 'genie_coordination_start':
            return 'Coordinating Response';
        case 'genie_llm_step': {
            const stepName = payload.step_name || `Step ${payload.step_number || '?'}`;
            return `${stepName}`;
        }
        case 'genie_routing_decision': {
            const profileCount = payload.selected_profiles?.length || 0;
            return `Routing to ${profileCount} expert${profileCount > 1 ? 's' : ''}`;
        }
        case 'genie_slave_invoked':
            return `Invoking @${payload.profile_tag || 'PROFILE'}`;
        case 'genie_slave_progress':
            return `@${payload.profile_tag || 'PROFILE'}: ${payload.message || 'Processing'}`;
        case 'genie_slave_completed': {
            const status = payload.success ? 'Completed' : 'Failed';
            const duration = payload.duration_ms ? ` (${(payload.duration_ms / 1000).toFixed(1)}s)` : '';
            return `@${payload.profile_tag || 'PROFILE'}: ${status}${duration}`;
        }
        case 'genie_synthesis_start':
            return 'LLM Synthesis Started';
        case 'genie_synthesis_complete':
            return 'LLM Synthesis Results';
        case 'genie_synthesis_skipped':
            return 'Synthesis Skipped';
        case 'genie_coordination_complete':
            return payload.success ? 'Coordination Complete' : 'Coordination Failed';
        default:
            return eventType;
    }
}

/**
 * Get human-readable step title for conversation agent events.
 */
function _getConversationAgentStepTitle(eventType, payload) {
    switch (eventType) {
        case 'conversation_agent_start': {
            const toolCount = payload.available_tools?.length || 0;
            return `Using Tools (${toolCount} available)`;
        }
        case 'conversation_llm_step': {
            const stepName = payload.step_name || `Step ${payload.step_number || '?'}`;
            return `${stepName}`;
        }
        case 'conversation_tool_invoked':
            return `Executing ${payload.tool_name || 'tool'}`;
        case 'conversation_tool_completed': {
            const status = payload.success ? 'Completed' : 'Failed';
            const duration = payload.duration_ms ? ` (${(payload.duration_ms / 1000).toFixed(1)}s)` : '';
            return `${payload.tool_name || 'Tool'}: ${status}${duration}`;
        }
        case 'conversation_tool_rewrite':
            return `System Self-Correction: ${payload.label || payload.kind || 'statement rewritten'}`;
        case 'conversation_agent_complete': {
            const toolCount = payload.tools_used?.length || 0;
            const duration = payload.total_duration_ms ? ` in ${(payload.total_duration_ms / 1000).toFixed(1)}s` : '';
            return payload.success
                ? `Tools Complete (${toolCount} executed${duration})`
                : 'Execution Failed';
        }
        default:
            return eventType;
    }
}

/**
 * Routes a REST task event to the correct profile-specific renderer.
 * Different profile classes (tool_enabled, llm_only, conversation_with_tools,
 * rag_focused) produce different event types that need different renderers.
 * This dispatcher replaces the previous blanket 'rest' source rendering.
 *
 * @param {Object} event - The canonical event object (has .type and payload fields)
 * @param {string} taskId - The REST task ID
 */
function _dispatchRestEvent(event, taskId) {
    const eventType = event.type;
    if (!eventType) return;

    // --- Token updates: direct to token display, not status window ---
    if (eventType === 'token_update') {
        UI.updateTokenDisplay(event);
        return;
    }

    // --- Status indicators: transient, skip (handled separately) ---
    if (eventType === 'status_indicator_update') return;

    // --- Session model updates: handle metadata only ---
    if (eventType === 'session_model_update') {
        const payload = event.payload || event;
        UI.updateSessionModels(payload.session_id, payload.models_used, payload.profile_tags_used);
        if (payload.session_id === state.currentSessionId) {
            state.currentProvider = payload.provider;
            state.currentModel = payload.model;
            UI.updateStatusPromptName();
        }
        return;
    }

    // --- Conversation Agent events (conversation_with_tools profile) ---
    if (eventType.startsWith('conversation_agent') ||
        eventType.startsWith('conversation_llm') ||
        eventType.startsWith('conversation_tool')) {
        const payload = event.payload || event;
        const stepTitle = _getConversationAgentStepTitle(eventType, payload);
        UI.updateStatusWindow({
            step: stepTitle,
            details: payload,
            type: eventType
        }, eventType === 'conversation_agent_complete', 'conversation_agent');
        // Also update handler state for correct live event processing
        handleConversationAgentEvent(eventType, payload);
        return;
    }

    // --- Knowledge Retrieval events (rag_focused + conversation_with_tools) ---
    if (eventType.startsWith('knowledge_') ||
        eventType === 'rag_llm_step' ||
        eventType === 'knowledge_search_complete') {
        const payload = event.payload || event;
        const stepTitle = _getConversationAgentStepTitle(eventType, payload);
        UI.updateStatusWindow({
            step: stepTitle,
            details: payload,
            type: eventType
        }, false, 'knowledge_retrieval');
        return;
    }

    // --- LLM execution events (llm_only profile) ---
    if (eventType === 'llm_execution' || eventType === 'llm_execution_complete') {
        const payload = event.payload || event;
        const stepTitle = _getConversationAgentStepTitle(eventType, payload);
        UI.updateStatusWindow({
            step: stepTitle,
            details: payload,
            type: eventType
        }, eventType === 'llm_execution_complete', 'conversation_agent');
        return;
    }

    // --- Skill events (pre-processing, all profile types) ---
    if (eventType === 'skills_applied') {
        const payload = event.payload || {};
        UI.updateStatusWindow({
            step: 'Skills applied',
            details: payload,
            type: 'skills_applied'
        }, false, 'skills');
        return;
    }

    // --- Extension events (all profile types) ---
    if (eventType === 'extension_start') {
        const payload = event.payload || {};
        UI.updateStatusWindow({
            step: `Running extension !${payload.name}${payload.param ? ':' + payload.param : ''}`,
            details: 'Processing...',
            type: 'extension_running'
        }, false, 'extension');
        return;
    }
    if (eventType === 'extension_complete') {
        const payload = event.payload || {};
        UI.updateStatusWindow({
            step: `Extension !${payload.name}`,
            details: payload,
            type: 'extension_complete'
        }, false, 'extension');
        return;
    }
    if (eventType === 'extension_results') {
        const payload = event.payload || {};

        // Display results based on each extension's output_target
        for (const [extName, result] of Object.entries(payload)) {
            if (!result.success) continue;
            const outputTarget = result.output_target || 'silent';

            if (outputTarget === 'chat_append' && result.content) {
                // Check if binary/file download (reuses helpers from eventHandlers.js via window globals)
                const isBinary = typeof window._isExtensionBinaryContent === 'function'
                    ? window._isExtensionBinaryContent(result)
                    : (result.content_type === 'application/pdf' || (result.content_type || '').startsWith('application/vnd.'));

                let extHtml;
                if (isBinary && typeof window._buildExtensionDownloadCard === 'function') {
                    extHtml = window._buildExtensionDownloadCard(extName, result);
                } else {
                    extHtml = `
                    <div class="extension-output mt-3 p-3 rounded-lg" data-ext-name="${extName}" style="background: rgba(251, 191, 36, 0.05); border: 1px solid rgba(251, 191, 36, 0.15);">
                        <div class="flex items-center gap-2 mb-2">
                            <span class="text-xs font-semibold px-2 py-0.5 rounded" style="background: rgba(251, 191, 36, 0.15); color: #fbbf24; font-family: 'JetBrains Mono', monospace;">!${extName}</span>
                            <span class="text-xs text-gray-500">${result.content_type}</span>
                        </div>
                        <pre class="text-xs text-gray-300 whitespace-pre-wrap overflow-auto max-h-48" style="font-family: 'JetBrains Mono', monospace;">${
                            typeof result.content === 'object' ? JSON.stringify(result.content, null, 2) : result.content
                        }</pre>
                    </div>
                    `;
                }
                const chatLog = document.getElementById('chat-log');
                if (chatLog) {
                    const lastMsg = chatLog.querySelector('.message-bubble:last-child .message-content');
                    if (lastMsg) lastMsg.insertAdjacentHTML('beforeend', extHtml);
                }
            } else if (outputTarget === 'status_panel' && result.content) {
                UI.updateStatusWindow({
                    step: `Extension: !${extName}`,
                    details: typeof result.content === 'object' ? JSON.stringify(result.content, null, 2) : result.content,
                    type: 'extension_result'
                }, false, 'extension');
            }
        }

        // Render silent extension output previews in Live Status
        UI.updateStatusWindow({
            step: 'Extension results',
            details: payload,
            type: 'extension_results'
        }, false, 'extension');
        return;
    }

    // --- Lifecycle events (all profile types) ---
    if (eventType === 'execution_start' || eventType === 'execution_complete' ||
        eventType === 'execution_error' || eventType === 'execution_cancelled') {
        const payload = event.payload || event;

        // Capture turn number from execution_start for title display
        if (eventType === 'execution_start' && payload.turn_id) {
            state.currentTurnNumber = payload.turn_id;
        }

        const isFinal = eventType !== 'execution_start';
        UI.updateStatusWindow({
            step: eventType,
            details: payload,
            type: eventType
        }, isFinal, 'lifecycle');
        return;
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
        return;
    }

    // --- Default: planner/executor events (tool_enabled profile) → 'rest' source ---
    const isFinal = (eventType === 'final_answer' || eventType === 'error' || eventType === 'cancelled');
    UI.updateStatusWindow(event, isFinal, 'rest', taskId);
}

// The one live stream. The browser retries a DROPPED connection by itself, but an error
// RESPONSE (a proxy's 502 while the server restarts, a non-stream body) closes an EventSource
// for good — it never retries. Then this module opens a new one, backing off 2s → 30s, and
// sooner when the network comes back. Without it the strip stayed "reconnecting" until a reload.
let _sse = null;
let _sseRetryTimer = null;
let _sseRetryDelay = 0;
const SSE_RETRY_MIN_MS = 2000;
const SSE_RETRY_MAX_MS = 30000;

function _scheduleResubscribe(delayMs) {
    clearTimeout(_sseRetryTimer);
    _sseRetryTimer = setTimeout(() => {
        _sseRetryTimer = null;
        if (!localStorage.getItem('tda_auth_token')) return;   // signed out: nothing to reopen
        subscribeToNotifications();
    }, delayMs);
}

window.addEventListener('online', () => {
    if (_sse && _sse.readyState === EventSource.CLOSED) _scheduleResubscribe(0);
});

export function subscribeToNotifications() {
    clearTimeout(_sseRetryTimer);
    _sseRetryTimer = null;
    if (!state.userUUID) {
        // The user id normally comes from the stored login (authClient.getUser()). When only the
        // token is there — storage partly cleared, a token handed to the page by other means —
        // ask the server who we are ONCE and subscribe then: the live channel must not depend on
        // how the login state was stored (every UX surface is live, also for outside calls).
        const token = localStorage.getItem('tda_auth_token');
        if (token && !window.__sseUserLookup) {
            window.__sseUserLookup = true;
            console.warn('[SSE] No stored user id — asking the server (/auth/me) before subscribing');
            fetch('/api/v1/auth/me', { headers: { 'Authorization': `Bearer ${token}` } })
                .then(r => (r.ok ? r.json() : null))
                .then(me => {
                    const u = (me && (me.user || me)) || {};
                    const id = u.id || u.user_uuid || null;
                    if (!id) throw new Error('no id in /auth/me');
                    state.userUUID = id;
                    subscribeToNotifications();
                })
                .catch(err => {
                    console.warn('[SSE] Cannot subscribe: no user ID available', err && err.message ? `(${err.message})` : '');
                    UI.updateSSEStatus('disconnected');
                });
            return;
        }
        // Set status to disconnected if we can't even start.
        console.warn('[SSE] Cannot subscribe: no user ID available');
        UI.updateSSEStatus('disconnected');
        return;
    }

    console.log('[SSE] Subscribing to notifications for user:', state.userUUID);
    // EventSource cannot send the Authorization header: ask for a one-minute stream ticket
    // WITH it, then open the stream with the ticket. A reconnect comes back here and asks
    // again (an expired ticket is refused, the browser closes the stream, onerror reopens).
    const token = localStorage.getItem('tda_auth_token');
    fetch('/api/notifications/ticket', {
        method: 'POST',
        headers: token ? { 'Authorization': `Bearer ${token}` } : {},
    })
        .then(r => (r.ok ? r.json() : Promise.reject(new Error(`ticket ${r.status}`))))
        .then(body => {
            if (!body || !body.ticket) throw new Error('no ticket');
            _openNotificationStream(body.ticket);
        })
        .catch(err => {
            console.warn('[SSE] Cannot open the notification stream:', err && err.message ? err.message : err);
            UI.updateSSEStatus('reconnecting');
            _sseRetryDelay = Math.min(_sseRetryDelay ? _sseRetryDelay * 2 : SSE_RETRY_MIN_MS, SSE_RETRY_MAX_MS);
            _scheduleResubscribe(_sseRetryDelay);
        });
}

function _openNotificationStream(ticket) {
    if (_sse) _sse.close();   // never two streams for one page
    const eventSource = new EventSource(`/api/notifications/subscribe?ticket=${encodeURIComponent(ticket)}`);
    _sse = eventSource;

    eventSource.onopen = async () => {
        console.log('[SSE] Connection established');
        _sseRetryDelay = 0;
        UI.updateSSEStatus('connected');

        // Skip SSE session loading - sessions are loaded by configManagement.js with pagination
        // This prevents duplicate loading and ensures pagination works correctly
        console.log('[SSE] Skipping session load - handled by configManagement.js with pagination');
    };

    eventSource.addEventListener('notification', (event) => {
        const data = JSON.parse(event.data);

        // Only log non-routine notifications (skip frequent status updates)
        if (!['status_indicator_update', 'session_model_update'].includes(data.type)) {
            console.log('[notification] Received:', data.type);
        }

        // When a notification is received, we know the connection is good.
        UI.updateSSEStatus('connected');

        switch (data.type) {
            case 'profile_config_changed': {
                // A profile (or action) write that reached the server from ANYWHERE — this page,
                // another tab, a REST script, the MCP server. The resource panel's one subscriber
                // (resourceSync.js) re-runs the per-profile load; every tab and badge follows.
                const pc = data.payload || {};
                try {
                    UderiaBus.emit('uderia:profile-config-changed', { profileId: pc.profile_id || null, aspect: pc.aspect || 'server' });
                } catch (_) { /* no-op */ }
                break;
            }
            case 'component_changed': {
                // Something a component keeps changed — a file, a dashboard, a canvas, a flow, a
                // graph, a task, a memory, an action, a data product — written from this page,
                // another tab, a REST script, a scheduled run or an agent turn. The store's write
                // seam announced it ONCE (core/ui_notify.announce_component_change); the page has
                // ONE event for it, and every component's surfaces follow through
                // UderiaBus.onComponent (components/signals.py).
                const cc = data.payload || {};
                UderiaBus.emit('uderia:component-changed', {
                    component_id: cc.component_id || null, action: cc.action || 'changed',
                    ids: Array.isArray(cc.ids) ? cc.ids : [], aspect: cc.aspect || null,
                    session_id: cc.session_id || null, profile_id: cc.profile_id || null, origin: 'server',
                    client: cc.client || null,   // the tab that wrote — the bus marks this page's own echo
                });
                break;
            }
            case 'maintenance_banner':
                if (window.showMaintenanceBanner) {
                    window.showMaintenanceBanner(
                        data.payload.message,
                        data.payload.countdown_minutes,
                        data.payload.downtime_minutes
                    );
                }
                break;
            case 'maintenance_banner_cancel':
                if (window.hideMaintenanceBanner) window.hideMaintenanceBanner();
                break;
            case 'reconfiguration':
                showReconfigurationNotification(data.payload);
                break;
            case 'turn_score_complete': {
                // S1.1 — the seal pops on the Living Answer (session-guarded inside)
                try { if (window.LivingAnswer) window.LivingAnswer.onNotification('turn_score_complete', data.payload || {}); } catch (_) { /* no-op */ }
                // The Quality card after execution. The online-eval hook is backgrounded,
                // so this arrives on the always-open notification channel (the per-request
                // /ask_stream that processStream reads has already closed). Render only for
                // the session being viewed; the overlay is idempotent (no double-render if
                // the live stream already injected it).
                const p = data.payload || {};
                // S8 — a sibling's fresh score refreshes its strip in the experiment card
                try { UderiaBus.emit('uderia:turn-scored', { session_id: p.session_id, turn: p.turn_id }); } catch (_) { /* no-op */ }
                if (p.session_id === state.currentSessionId
                        && UI.injectTrajectoryEvalOverlay && p.turn_id != null) {
                    UI.injectTrajectoryEvalOverlay(p.session_id, p.turn_id,
                        { profile_type: p.profile_class, status: 'success' },
                        { verdicts: p.verdicts || [], source: 'online', judged: p.judged,
                          input_tokens: p.input_tokens, output_tokens: p.output_tokens,
                          cost_usd: p.cost_usd });
                    // Settle the dial even when the overlay already existed (the call
                    // above early-returns then, and its dial-settle would be skipped).
                    // Idempotent server-authoritative render — never a second derivation.
                    try {
                        if (window.AssuranceDial && window.AssuranceDial.enabled()) {
                            window.AssuranceDial.inject(p.session_id, p.turn_id,
                                { profile_type: p.profile_class, status: 'success' },
                                { force: true, scored: true });   // the score supersedes a stream-end settle
                        }
                    } catch (_) { /* dial settle is best-effort */ }
                }
                break;
            }
            case 'token_update': {
                // Live KPI update for post-turn judge accounting (online-eval hook,
                // grounding-gate judge, manual "Score this turn") — these route their
                // cost here because they run OUTSIDE an open /ask_stream (backgrounded
                // or a REST call). SESSION-GUARDED: apply only for the viewed session,
                // so another session's counters are never polluted. updateTokenDisplay
                // sets ABSOLUTE totals (the backend computes them), so it is idempotent.
                const tp = data.payload || {};
                if (tp.session_id && tp.session_id === state.currentSessionId) {
                    UI.updateTokenDisplay(tp);
                }
                break;
            }
            case 'llm_recovery': {
                // S4 — a recovery act (retry / continuation / fallback / refusal) as a discrete
                // Live Status STEP, live. Delivered here because the act happens INSIDE the
                // handler, which has no request stream; session-guarded; ALSO persisted on the
                // turn (`resilience.steps`) so it re-renders identically on a plan reload.
                const rv = data.payload || {};
                const rd = rv.details || {};
                try { if (window.LivingAnswer) window.LivingAnswer.onNotification('llm_recovery', rv); } catch (_) { /* no-op */ }   // S1.1: a recovery chip
                if (rd.kind === 'fallback' && rd.to && rd.session_id) {
                    // the answer that follows was served by the fallback: stash per session until
                    // final_answer names the turn (the chip on the answer reads servedByTurn)
                    const [prov, ...rest] = String(rd.to).split('/');
                    if (!state.pendingServedBy) state.pendingServedBy = {};
                    state.pendingServedBy[rd.session_id] = { provider: prov, model: rest.join('/') };
                }
                if (rd.session_id && rd.session_id === state.currentSessionId) {
                    UI.updateStatusWindow({
                        step: rv.step || 'LLM Recovery',
                        details: rd,
                        type: 'llm_recovery',
                    }, true, 'session_name');
                }
                break;
            }
            case 'steer_consumed': {
                // S10 — the agent read a steer at its next step: a discrete Live Status step, live
                // (the boundary sits inside a tool wrapper with no request stream — the S7 idiom);
                // session-guarded; the pinned steer bubble moves into the trace.
                const sp = data.payload || {};
                try { if (window.LivingAnswer) window.LivingAnswer.onNotification('steer_consumed', sp); } catch (_) { /* no-op */ }   // S1.1: a steer chip
                if (sp.session_id && sp.session_id === state.currentSessionId) {
                    UI.updateStatusWindow({ step: sp.step || 'Steer Considered', type: 'steer_consumed', details: sp.details || {} });
                }
                break;
            }
                        case 'outcome_revision': {
                // S7 — a revision as a discrete Live Status STEP, live (the loop runs inside an
                // awaited seam with no request stream; the judge-step precedent). Session-guarded;
                // ALSO recorded in the trace, so it re-renders identically on a plan reload.
                const rp = data.payload || {};
                if (rp.session_id && rp.session_id === state.currentSessionId) {
                    UI.updateStatusWindow({ step: rp.step || 'Outcome Revision', type: 'outcome_revision', details: rp.details || {} });
                }
                try { if (window.LivingAnswer) window.LivingAnswer.onNotification('outcome_revision', rp); } catch (_) { /* no-op */ }   // S1.1: a revision chip
                break;
            }
            case 'tool_surface': {
                // S6 — the surface changed (a find widened it): keep the dial's BOUNDED reading
                // current; the Live Status card re-renders on the reload path from the stamp.
                try {
                    const p = data.payload || {};
                    if (p.session_id && p.session_id === state.currentSessionId && window.AssuranceDial && window.AssuranceDial.note) {
                        window.AssuranceDial.note(p.session_id, 'tool_surface', p);
                    }
                    if (window.LivingAnswer) window.LivingAnswer.onNotification('tool_surface', p);   // S1.1: the exception chips
                } catch (_) { /* a live update must never break the channel */ }
                break;
            }
            case 'turn_budget': {
                // S5 — the turn budget's live state (pushed at every tool boundary / LLM call).
                // The header bar is session-guarded inside BudgetBar.update.
                if (window.BudgetBar && window.BudgetBar.update) {
                    window.BudgetBar.update(data.payload || {});
                }
                break;
            }
            case 'db_health': {
                // The platform database is unreadable (core/db_health.py) — system-wide, so the
                // app-level banner; it stays a minute and is re-sent while the state lasts.
                const dbh = data.payload || {};
                const dbMsg = dbh.summary || 'The platform database is unreadable — an administrator must restore it.';
                if (window.showAppBanner) window.showAppBanner(dbMsg, 'error', 60000);
                else showRestQueryNotification(dbMsg);
                console.error('[db-health]', dbh);
                break;
            }
            case 'provider_health': {
                // S4 — a breaker transition; the header strip shows itself ONLY while a provider
                // is degraded (open / half-open) and disappears on recovery.
                if (window.ProviderHealthStrip && window.ProviderHealthStrip.update) {
                    window.ProviderHealthStrip.update(data.payload || {});
                }
                break;
            }
            case 'proposal_suggestion_complete':
            case 'memory_write': {
                // An LLM call booked AFTER its turn (core/post_turn_usage): the model-gap proposal
                // suggestion, the agent-memory extraction. Its stream has closed, so its step comes
                // here as a discrete Live Status step (its counters as a token_update above), and it
                // is persisted on the turn, so a reload shows the same step. SESSION-GUARDED.
                const pe = data.payload || {};
                const psid = (pe.details && pe.details.session_id) || pe.session_id;
                if (psid && psid === state.currentSessionId) {
                    UI.updateStatusWindow({
                        step: pe.step || (data.type === 'memory_write' ? 'Memory Updated' : 'Proposal Suggested'),
                        details: pe.details || {},
                        type: pe.type || data.type,
                    }, true, 'session_name');
                }
                break;
            }
            case 'judge_evaluation_complete': {
                // The LLM-judge run as a discrete Live Status STEP, so the judge's
                // token cost is VISIBLE and the panel's per-event costs sum to the turn
                // total (cost transparency is a core USP). Delivered here because every
                // judge caller (backgrounded online hook, REST re-score button, inline
                // grounding-gate judge) runs outside an open /ask_stream. SESSION-GUARDED
                // so it never lands in another session's panel. The step is ALSO persisted
                // to the turn server-side, so it re-renders identically on a plan reload.
                const je = data.payload || {};
                const jsid = (je.details && je.details.session_id) || je.session_id;
                if (jsid && jsid === state.currentSessionId) {
                    UI.updateStatusWindow({
                        step: je.step || 'Agent Quality Judged',
                        details: je.details || {},
                        type: je.type || 'judge_evaluation_complete',
                    }, true, 'session_name');
                }
                break;
            }
            case 'action_record': {
                // OBDA-ACT — an act on a signed action as a discrete Live Status step. Delivered
                // here because it is emitted from inside a component tool (and from a
                // person's Confirm, which runs outside any open stream). SESSION-GUARDED.
                // On reload the step is synthesized from turnData.actions (eventHandlers).
                const ar = data.payload || {};
                const asid = (ar.details && ar.details.session_id) || ar.session_id;
                if (asid && asid === state.currentSessionId) {
                    UI.updateStatusWindow({
                        step: ar.step || 'Action',
                        details: ar.details || {},
                        type: 'action_record',
                    }, true, 'session_name');
                }
                break;
            }
            case 'info':
                showRestQueryNotification(data.message);
                break;
            case 'review_decision_notice': {
                // HITL disposition landed (append-only release — history is never
                // rewritten): tell the user where to read the released original.
                // Delivered via the centralized Uderia messaging system (bannerSystem).
                const rdn = data.payload || {};
                const msg = rdn.source === 'action'
                    ? (rdn.decision === 'approved'
                        ? `A parked write (turn ${rdn.turn_id || '?'}) was approved and performed — the answer that asked for it now reads Done.`
                        : `A parked write (turn ${rdn.turn_id || '?'}) was rejected — nothing was written.`)
                    : rdn.decision !== 'approved'
                    ? `A withheld answer (turn ${rdn.turn_id || '?'}) was reviewed — the withholding stands.`
                    : rdn.released_to_context
                        ? `A withheld answer (turn ${rdn.turn_id || '?'}) was released INTO the conversation — subsequent turns can now use it (labelled as your decision).`
                        : `A withheld answer (turn ${rdn.turn_id || '?'}) was released after review — open “Why trust this answer?” under that message to read the original.`;
                if (window.showAppBanner) window.showAppBanner(msg, rdn.decision === 'approved' ? 'success' : 'info', 8000);
                else showRestQueryNotification(msg);
                // The Human Review step, LIVE (session-guarded) — the same card the
                // persisted system_event re-renders on plan reload, so the trace is
                // identical live and historical (mirrors judge_evaluation_complete).
                if (rdn.session_id && rdn.session_id === state.currentSessionId) {
                    UI.updateStatusWindow({
                        step: 'Human Review',
                        type: 'review_decision',
                        details: {
                            review_id: rdn.review_id, source: rdn.source,
                            decision: rdn.decision, note: rdn.note || null,
                            released_to_context: !!rdn.released_to_context,
                            summary: `Withheld answer ${rdn.decision !== 'approved'
                                ? 'withholding upheld by the owner'
                                : rdn.released_to_context
                                    ? 'released by the owner after review — INTO the conversation (available to subsequent turns)'
                                    : 'released by the owner after review (record only)'}`,
                        },
                    }, true, 'session_name');
                }
                // Let the answer capsule refresh its review state in place, and the
                // Assurance Inbox (if open) refresh its list.
                try {
                    UderiaBus.emit('uderia:review-decision', rdn);
                    UderiaBus.emit('uderia:trust-event', rdn);
                } catch (_) { /* refreshes are best-effort */ }
                break;
            }
            case 'platform_change': {
                // A platform write RAN on the server (an approved request, or an agent allowed
                // to write): the page's own lists are stale until told. Agents and what they are
                // built on reload the shared list and say so on the bus — every view that lists
                // agents (Agent Anatomy, the profile cards, the composer) follows profile-modified.
                const pc = data.payload || {};
                if (['profiles', 'llm', 'mcp'].includes(pc.namespace)) {
                    const done = () => UderiaBus.emit('uderia:profile-modified', { source: 'platform', op: pc.op });
                    if (window.configState && typeof window.configState.loadProfiles === 'function') {
                        window.configState.loadProfiles().then(done, done);
                    } else {
                        done();
                    }
                }
                break;
            }
            case 'trust_event_notice': {
                // A trust action landed (memory quarantine, retrieval defuse, …):
                // banner via the central messaging system + in-place inbox refresh.
                // The banner TONE honours the event's severity (PROP-002) — every
                // live trust banner was hardcoded amber, so an `info` receipt and a
                // `danger` red-team failure raised the same alarm.
                const ten = data.payload || {};
                const tone = ten.severity === 'danger' ? 'error'
                    : ten.severity === 'info' ? 'info' : 'warning';
                if (window.showAppBanner) window.showAppBanner(ten.summary || 'A trust event was recorded — see the Assurance Inbox.', tone, 8000);
                else showRestQueryNotification(ten.summary || 'A trust event was recorded.');
                try { UderiaBus.emit('uderia:trust-event', ten); } catch (_) { /* noop */ }
                break;
            }
            case 'trust_breaker': {
                // Trust-budget circuit breaker (opt-in): an agent's trailing conduct
                // entered At-risk. Surface it immediately — this is the closed-loop
                // alert, not dashboard archaeology. Payload carries labels/counts only.
                const tbp = data.payload || {};
                const act = tbp.action === 'autonomy_frozen'
                    ? 'its unattended use has been suspended (Agent Authority) pending review'
                    : 'review it in the Agents lens';
                const tbMsg = `Trust breaker: @${tbp.profile_tag || '?'} entered At-risk — ${tbp.reason || 'conduct below floor'}; ${act}.`;
                // Centralized Uderia messaging system (bannerSystem). Danger severity
                // → error tone (PROP-002: the tone honours the severity).
                if (window.showAppBanner) window.showAppBanner(tbMsg, 'error', 10000);
                else showRestQueryNotification(tbMsg);
                console.warn('[trust-breaker]', tbp);
                try { UderiaBus.emit('uderia:trust-event', tbp); } catch (_) { /* noop */ }
                break;
            }
            case 'new_session_created': {
                const newSession = data.payload;

                // Idempotency guard: a session must never appear twice in the sidebar.
                // If a card for this id already exists (e.g. two new_session_created
                // emits race for the same session), drop the stale card first and
                // re-insert with the freshest payload — never prepend a duplicate.
                if (newSession && newSession.id) {
                    const existingCard = document.getElementById(`session-${newSession.id}`);
                    if (existingCard) {
                        const existingWrapper = existingCard.closest('.genie-wrapper');
                        (existingWrapper || existingCard).remove();
                    }
                }

                // Check if this is a genie slave session to determine if it's the last child
                const genieMetadata = newSession.genie_metadata || {};
                const parentSessionId = genieMetadata.parent_session_id;
                let isLastChild = true;  // New session is always the last child when created

                // Add the new session to the UI list, but do not make it active
                const sessionItem = UI.addSessionToList(newSession, false, isLastChild);

                if (genieMetadata.is_genie_slave && parentSessionId) {
                    // Find the parent session element (might be wrapped)
                    const parentElement = document.getElementById(`session-${parentSessionId}`);
                    if (parentElement) {
                        // Find all existing sibling slaves (same parent) - these are wrapped elements
                        const existingSlaveWrappers = Array.from(DOM.sessionList.querySelectorAll('.genie-wrapper'))
                            .filter(wrapper => {
                                const sessionEl = wrapper.querySelector('.genie-slave-session');
                                return sessionEl && sessionEl.dataset.genieParentId === parentSessionId;
                            });

                        if (existingSlaveWrappers.length === 0) {
                            // No existing slaves, insert directly after parent (or parent's wrapper)
                            const insertAfter = parentElement.parentElement && parentElement.parentElement.classList.contains('genie-wrapper')
                                ? parentElement.parentElement
                                : parentElement;
                            insertAfter.insertAdjacentElement('afterend', sessionItem);
                        } else {
                            // Insert after the last existing slave wrapper to maintain order
                            const lastSlaveWrapper = existingSlaveWrappers[existingSlaveWrappers.length - 1];
                            lastSlaveWrapper.insertAdjacentElement('afterend', sessionItem);

                            // Recalculate last-child connectors (└ vs ├) after inserting the new slave
                            UI.refreshGenieConnectors(parentSessionId);
                        }

                        // Update genie master badges to add collapse toggle to parent if needed
                        UI.updateGenieMasterBadges();

                        // Sync wrapper collapsed states for new child sessions
                        import('./hierarchyHelpers.js').then(module => {
                            module.syncWrapperStates();
                        });
                    } else {
                        // Fallback to prepend if parent not found
                        DOM.sessionList.prepend(sessionItem);
                    }
                } else {
                    // Non-slave sessions go to the top
                    DOM.sessionList.prepend(sessionItem);
                }

                // Update utility sessions filter visibility
                if (window.updateUtilitySessionsFilter) {
                    window.updateUtilitySessionsFilter();
                }
                // Update archived sessions filter visibility
                if (window.updateArchivedSessionsFilter) {
                    window.updateArchivedSessionsFilter();
                }
                break;
            }
            case 'session_name_update': {
                const { session_id, newName } = data.payload;
                UI.updateSessionListItemName(session_id, newName);
                UI.moveSessionToTop(session_id);
                if (session_id === state.currentSessionId) {
                    UI.updateActiveSessionTitle(newName);
                }
                break;
            }
            case 'profile_override_failed': {
                const { override_profile_name, override_profile_tag, default_profile_tag, error_message } = data.payload;
                showProfileOverrideWarning(override_profile_name, override_profile_tag, default_profile_tag, error_message);
                break;
            }
            case 'session_model_update': {
                const { session_id, models_used, profile_tags_used, last_updated, provider, model, name } = data.payload;
                UI.updateSessionModels(session_id, models_used, profile_tags_used);
                UI.updateSessionTimestamp(session_id, last_updated);
                if (name) {
                    UI.updateSessionListItemName(session_id, name);
                }
                UI.moveSessionToTop(session_id);

                if (session_id === state.currentSessionId) {
                    state.currentProvider = provider;
                    state.currentModel = model;
                    UI.updateStatusPromptName();
                }
                break;
            }
            // --- REST task events with buffering and profile-aware dispatch ---
            case 'rest_task_update': {
                const { task_id, session_id, event } = data.payload;

                // --- Always buffer REST events per session for cross-session replay ---
                // Buffer is created on first event, and session is tracked as active.
                // Completion is handled by the separate 'rest_task_complete' notification.
                if (!state.restEventBuffer[session_id]) {
                    state.restEventBuffer[session_id] = { taskId: task_id, events: [], isComplete: false };
                    state.activeRestSessions.add(session_id);
                }
                state.restEventBuffer[session_id].events.push(event);

                // --- Session guard: allow current session + child sessions during Genie ---
                if (session_id !== state.currentSessionId && !state.isGenieCoordinationActive) break;

                // --- Extract indicator events (CCR, KNW) for dot blinks ---
                if (event.type === 'rag_retrieval') {
                    state.lastRagCaseData = event;
                    UI.blinkCcrDot();
                }
                if (event.type === 'knowledge_retrieval' || event.type === 'knowledge_retrieval_complete') {
                    const collections = event.data?.collections || [];
                    const documentCount = event.data?.document_count || 0;
                    if (!state.isViewingHistoricalTurn) UI.blinkKnowledgeDot();
                    UI.updateKnowledgeIndicator(collections, documentCount);
                }

                // During Genie coordination, child REST events should only blink dots.
                // The Genie renderer handles child progress via genie_slave_* events.
                if (state.isGenieCoordinationActive && session_id !== state.currentSessionId) {
                    break;
                }

                // --- CRITICAL FIX: Block token_update events from non-current sessions ---
                // Child sessions (Genie slaves) emit their own token_update events during execution.
                // These must NEVER update the parent Genie session's token display, even if they
                // arrive after isGenieCoordinationActive is cleared (race condition).
                // Only the parent session's token_update (from execution_service.py:495) should update the display.
                if (event.type === 'token_update') {
                    console.log(`%c[Notifications] 🔍 token_update received:`, 'background: #ff00ff; color: #fff; font-weight: bold;');
                    console.log(`  Event session_id: ${session_id}`);
                    console.log(`  Current session_id: ${state.currentSessionId}`);
                    console.log(`  Match: ${session_id === state.currentSessionId}`);
                    console.log(`  Genie active: ${state.isGenieCoordinationActive}`);
                    console.log(`  Token data:`, {
                        stmt_in: event.statement_input,
                        stmt_out: event.statement_output,
                        turn_in: event.turn_input,
                        turn_out: event.turn_output,
                        total_in: event.total_input,
                        total_out: event.total_output,
                        cost: event.cost_usd
                    });

                    if (session_id !== state.currentSessionId) {
                        console.log(`%c[Notifications] 🚫 BLOCKED - child session event`, 'background: #ff0000; color: #fff; font-weight: bold;');
                        break;
                    } else {
                        console.log(`%c[Notifications] ✅ ALLOWED - current session event`, 'background: #00ff00; color: #000; font-weight: bold;');
                    }
                }

                // --- Route to profile-specific renderer ---
                _dispatchRestEvent(event, task_id);
                break;
            }
            case 'rest_task_complete': {
                const { session_id, turn_id, user_input, final_answer, profile_tag, extension_specs, skill_specs, source, origin, input_request } = data.payload;
                // source is optional — 'scheduler' for background task sessions, 'rest' otherwise
                const msgSource = source || 'rest';
                // origin is the orchestration driver (flow/genie), server-stamped
                const msgOrigin = origin || null;

                // --- Definitive cleanup for REST session tracking ---
                state.activeRestSessions.delete(session_id);
                if (state.restEventBuffer[session_id]) {
                    state.restEventBuffer[session_id].isComplete = true;
                }
                // Signal turn completion so open side panels (e.g. Agent Memory) can live-refresh.
                try { UderiaBus.emit('uderia:turn-complete', { session_id }); } catch (_) {}

                if (session_id === state.currentSessionId) {
                    // User is viewing this session — render Q&A and clean up.
                    //
                    // Guard against duplicate messages from the race between two render paths:
                    //
                    // Path A: handleLoadSession loads server history → renders user message (no turn_id
                    //         yet since it's set retroactively by the assistant message). If the task was
                    //         still running when the user switched, history only has the user message.
                    //         Then this notification fires and would add ANOTHER user + assistant pair.
                    //
                    // Path B: handleLoadSession loads complete history (both user + assistant already on
                    //         server). This notification fires and would add a duplicate pair.
                    //
                    // Fix for Path B: if any DOM element already has data-turn-id matching this turn,
                    //   the full Q&A is already rendered — skip everything.
                    //
                    // Fix for Path A: if the last chat bubble is a user message (no assistant after it),
                    //   the server pre-loaded the user message but the turn wasn't complete yet.
                    //   Skip adding the user message again; only add the assistant message.
                    //   The assistant addMessage call will retroactively stamp data-turn-id + badge
                    //   onto the existing server-loaded user bubble — correct behaviour.

                    const turnAlreadyRendered = turn_id && DOM.chatLog?.querySelector(`[data-turn-id="${turn_id}"]`);
                    if (!turnAlreadyRendered) {
                        // Detect server-preloaded user message: last bubble is a user bubble
                        const allBubbles = DOM.chatLog ? Array.from(DOM.chatLog.querySelectorAll('.message-bubble')) : [];
                        const lastBubble = allBubbles[allBubbles.length - 1];
                        const serverPreloadedUserMsg = lastBubble && lastBubble.querySelector('.user-avatar');

                        if (!serverPreloadedUserMsg) {
                            // No partial history — add user message fresh
                            UI.addMessage('user', user_input, turn_id, true, msgSource, profile_tag, false, extension_specs || null, skill_specs || null, msgOrigin);
                        }
                        // Always add assistant message; its render retroactively stamps turn_id on
                        // the preceding user bubble (whether freshly added or server-preloaded)
                        try {
                            const _sb = state.pendingServedBy && state.pendingServedBy[session_id];
                            if (_sb && turn_id != null) { state.servedByTurn[turn_id] = _sb; delete state.pendingServedBy[session_id]; }
                        } catch (_) { /* never break the answer */ }
                        UI.addMessage('assistant', final_answer, turn_id, true);
                        // PROP-011 — the turn stopped to ask: the card under the answer
                        try { if (input_request && window.NeedsInput) window.NeedsInput.attach(DOM.chatLog.lastElementChild, input_request); } catch (_) { /* static question in the HTML */ }
                    }
                    UI.moveSessionToTop(session_id);
                    UI.setExecutionState(false);

                    // Explicitly mark the last active status step as completed
                    const lastStep = DOM.statusWindowContent.querySelector('.status-step.active');
                    if (lastStep) {
                        lastStep.classList.remove('active');
                        lastStep.classList.add('completed');
                    }

                    // Buffer no longer needed — UI is fully rendered
                    delete state.restEventBuffer[session_id];
                } else {
                    // User is viewing a different session
                    UI.highlightSession(session_id);
                    // Delete stale cache (next visit should use normal server load with fresh data)
                    delete state.sessionUiCache[session_id];
                    // Buffer is kept (marked complete) — cleaned on next visit to this session
                }
                break;
            }
            case 'status_indicator_update': {
                // Status indicators (MCP, LLM) are global system health indicators.
                // Always process them regardless of active streams — they reflect
                // system-wide connection state that applies to all sessions equally.
                const { target, state: statusState } = data.payload;
                let dot;
                if (target === 'db') dot = DOM.mcpStatusDot;
                else if (target === 'llm') dot = DOM.llmStatusDot;
                // Handle LLM thinking indicator separately
                if (target === 'llm') UI.setThinkingIndicator(statusState === 'busy');

                if (dot) {
                    if (statusState === 'busy') {
                        dot.classList.replace('idle', 'busy') || dot.classList.replace('connected', 'busy');
                        dot.classList.add('pulsing');
                    } else {
                        dot.classList.remove('pulsing');
                        dot.classList.replace('busy', target === 'db' ? 'connected' : 'idle');
                    }
                }
                break;
            }
            // --- MODIFICATION END ---
            case 'rag_retrieval':
                // RAG retrieval is a system-wide indicator — always process.
                // Per-request stream also handles this (eventHandlers.js:756)
                // for the viewed session; double-blink is acceptable.
                state.lastRagCaseData = data.payload || data;
                UI.blinkCcrDot();
                break;
            // --- Genie Coordination Events ---
            case 'genie_start':  // From execution_service.py
            case 'genie_routing':  // From execution_service.py
            case 'genie_coordination_start':
            case 'genie_llm_step':
            case 'genie_routing_decision':
            case 'genie_slave_invoked':
            case 'genie_slave_progress':
            case 'genie_slave_completed':
            case 'genie_synthesis_start':
            case 'genie_synthesis_skipped':
            case 'genie_coordination_complete': {
                const payload = data.payload || {};
                // Only handle events for current session
                if (payload.session_id && payload.session_id !== state.currentSessionId) {
                    console.log('[Genie] Ignoring event for different session:', payload.session_id);
                    break;
                }
                // Delegate to genie handler for inline card updates
                handleGenieEvent(data.type, payload);
                // Also update status window if open (respects user preference)
                const genieStepTitle = _getGenieStepTitle(data.type, payload);
                UI.updateStatusWindow({
                    step: genieStepTitle,
                    details: payload,
                    type: data.type
                }, data.type === 'genie_coordination_complete', 'genie');
                break;
            }
            // --- KG execution theatre: live stage notices (understand/seed/expand/inject),
            // emitted by the enrichment pipeline over this channel the moment each stage
            // completes. Session-guarded; the theatre itself decides whether it is on. ---
            case 'kg_stage': {
                const payload = data.payload || {};
                if (payload.session_id && payload.session_id !== state.currentSessionId) break;
                try { window.__kgTheatre && window.__kgTheatre.dispatch('kg_stage', payload); } catch (_) {}
                break;
            }
            // --- Tier 1.1 grounding gate (any engine; genie emits it via the notification
            // channel since it has no per-request /ask_stream status yield) ---
            case 'grounding_gate': {
                const payload = data.payload || {};
                if (payload.session_id && payload.session_id !== state.currentSessionId) break;
                // The per-request /ask_stream is the primary status channel; notifications are
                // supplementary — skip if a live stream is already rendering this session.
                if (payload.session_id && state.activeStreamSessions && state.activeStreamSessions.has(payload.session_id)) break;
                try { window.__kgTheatre && window.__kgTheatre.dispatch('grounding_gate', payload); } catch (_) {}
                // Neutral source → _renderStandardStep (which dispatches grounding_gate), so it
                // never toggles isGenieCoordinationActive / resets the genie status window.
                UI.updateStatusWindow({
                    step: 'Grounding Gate',
                    details: payload.details || payload,
                    type: 'grounding_gate'
                }, false);
                break;
            }
            // --- Tier 1.3 guardrail (any engine; genie emits it via the notification channel) ---
            case 'guardrail': {
                const payload = data.payload || {};
                if (payload.session_id && payload.session_id !== state.currentSessionId) break;
                if (payload.session_id && state.activeStreamSessions && state.activeStreamSessions.has(payload.session_id)) break;
                UI.updateStatusWindow({
                    step: 'Guardrail',
                    details: payload.details || payload,
                    type: 'guardrail'
                }, false);
                break;
            }
            // --- Conversation Agent Events (conversation_with_tools profile) ---
            case 'conversation_agent_start':
            case 'conversation_llm_step':
            case 'conversation_tool_invoked':
            case 'conversation_tool_completed':
            case 'conversation_tool_rewrite':
            case 'conversation_agent_complete': {
                const payload = data.payload || {};
                // Only handle events for current session
                if (payload.session_id && payload.session_id !== state.currentSessionId) {
                    console.log('[ConversationAgent] Ignoring event for different session:', payload.session_id);
                    break;
                }
                // CRITICAL: Skip if there's an active stream for this session
                // The /ask_stream channel is the primary handler - notifications are supplementary
                if (payload.session_id && state.activeStreamSessions && state.activeStreamSessions.has(payload.session_id)) {
                    console.log('[ConversationAgent] Skipping - active stream handling this session:', payload.session_id);
                    break;
                }
                // IMPORTANT: Update status window BEFORE calling handler for completion event
                // because handler sets isConversationAgentActive=false which would trigger a reset
                const agentStepTitle = _getConversationAgentStepTitle(data.type, payload);
                UI.updateStatusWindow({
                    step: agentStepTitle,
                    details: payload,
                    type: data.type
                }, data.type === 'conversation_agent_complete', 'conversation_agent');
                // Delegate to conversation agent handler for state tracking (after UI update)
                handleConversationAgentEvent(data.type, payload);
                break;
            }
            // --- Knowledge Retrieval Events for Conversation Agent ---
            case 'knowledge_retrieval':
            case 'knowledge_retrieval_start':
            case 'knowledge_reranking_start':
            case 'knowledge_reranking_complete':
            case 'knowledge_retrieval_complete':
            case 'rag_llm_step':
            case 'knowledge_search_complete':
            case 'kg_enrichment': {
                const payload = data.payload || {};

                // Session guard: skip events for other sessions
                if (payload.session_id && payload.session_id !== state.currentSessionId) {
                    console.log(`[${data.type}] Ignoring notification for different session:`, payload.session_id);
                    break;
                }
                // When multiple streams active and no session_id, skip to avoid ambiguity
                if (!payload.session_id && state.activeStreamSessions.size > 1) {
                    break;
                }

                const stepTitle = _getConversationAgentStepTitle(data.type, payload);
                console.log(`[${data.type}] Received direct notification:`, payload);
                // KG execution theatre: the Coordinate lane has no per-request stream, so its
                // enrichment reaches the stage through this channel.
                if (data.type === 'kg_enrichment') { try { window.__kgTheatre && window.__kgTheatre.dispatch('kg_enrichment', payload); } catch (_) {} }

                // Update knowledge indicator for completion events
                if (data.type === 'knowledge_retrieval_complete' || data.type === 'knowledge_retrieval') {
                    const collections = payload.collections || [];
                    const documentCount = payload.document_count || 0;
                    // Only blink during live execution, not when viewing historical turns
                    if (!state.isViewingHistoricalTurn) {
                        UI.blinkKnowledgeDot();
                    }
                    UI.updateKnowledgeIndicator(collections, documentCount);
                    // Store the knowledge event for potential replay
                    state.pendingKnowledgeRetrievalEvent = payload;
                }

                // Always update status window for knowledge retrieval events
                // For conversation_with_tools, these arrive BEFORE conversation_agent_start
                // so we render them directly without checking isConversationAgentActive
                UI.updateStatusWindow({
                    step: stepTitle,
                    details: payload,
                    type: data.type
                }, false, 'knowledge_retrieval');
                break;
            }
            // --- Flow run lifecycle: discovery signal for the open flow canvas ---
            // The per-user notification channel is always-on, so it tells an open
            // flow canvas that a run STARTED (or ended) regardless of who launched
            // it — chat LLM tool call, REST/n8n, scheduler, another session. The
            // canvas (renderer.js) guards by flow_id and then subscribes to the
            // per-run SSE stream for the blow-by-blow animation. This mirrors how KG
            // animation listens on this same channel. No session guard here: a flow
            // run isn't bound to currentSession; the renderer guards by flow_id.
            case 'flow_run_start':
            case 'flow_run_complete':
            case 'flow_run_error': {
                UderiaBus.emit('uderia:flow-run-event', { type: data.type, payload: data.payload || {} });
                break;
            }
            case 'knowledge_counts_updated': {
                // Deferred chunk count from Teradata server-side chunking
                // completed — refresh the knowledge repository cards.
                const { collection_id, chunk_count } = data.payload || {};
                console.log(`[notification] Chunk count ready for collection ${collection_id}: ${chunk_count}`);
                if (typeof window.loadKnowledgeRepositories === 'function') {
                    window.loadKnowledgeRepositories();
                }
                break;
            }
            default:
                // console.warn("Unknown notification type:", data.type);
        }
    });

    eventSource.onerror = (error) => {
        console.error("EventSource failed:", error);
        UI.updateSSEStatus('reconnecting');
        // CONNECTING = the browser is retrying a dropped connection itself. CLOSED = it gave up
        // (an error response) and will never retry — reopen it, unless a newer stream replaced it.
        if (eventSource.readyState === EventSource.CLOSED && _sse === eventSource) {
            _sseRetryDelay = Math.min(_sseRetryDelay ? _sseRetryDelay * 2 : SSE_RETRY_MIN_MS, SSE_RETRY_MAX_MS);
            console.warn(`[SSE] Stream closed by the browser — reopening in ${_sseRetryDelay / 1000}s`);
            _scheduleResubscribe(_sseRetryDelay);
        }
    };
}