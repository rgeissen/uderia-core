// static/js/handlers/nextStepHandler.js
//
// Next-Step Recommender dock — ephemeral, per-session follow-up suggestions
// rendered in #next-step-dock (OUTSIDE #chat-log, so they are never captured by
// the session UI cache, never persisted to chat history, and never re-parsed as
// markdown). Suggestions are stored in state.nextStepSuggestions and re-rendered
// on session switch, so concurrent sessions stay isolated by construction.
//
// World-class UX notes:
//  - Action chips (tool-grounded) are visually distinguished from follow-up
//    question chips and always lead, because they're the differentiator.
//  - A chip click fills the composer and submits via the existing chat-form path.
//  - mousedown + preventDefault is used so clicking never steals focus oddly.
//  - One-click dismiss; per-user off switch via localStorage.
//  - Theme-aware (CSS variables) + monochrome inline SVG icons (no emoji).

import { state } from '../state.js';
import * as DOM from '../domElements.js';

const USER_PREF_KEY = 'tda_next_step_enabled';

// Monochrome industrial SVG icons (single currentColor stroke). No emoji, no multi-colour.
const ICONS = {
    chart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><rect x="7" y="11" width="3" height="6"/><rect x="13" y="7" width="3" height="10"/></svg>',
    dashboard: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/></svg>',
    schedule: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
    flow: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="6" height="4"/><rect x="15" y="16" width="6" height="4"/><path d="M6 8v6a2 2 0 0 0 2 2h7"/></svg>',
    graph: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="5" cy="6" r="2"/><circle cx="19" cy="8" r="2"/><circle cx="12" cy="18" r="2"/><path d="M7 7l4 9M17 9l-4 7"/></svg>',
    canvas: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 9l-3 3 3 3M16 9l3 3-3 3"/></svg>',
    continue: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
    followup: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.5 8.5 0 0 1-12.5 7.5L3 21l2-5.5A8.5 8.5 0 1 1 21 11.5z"/></svg>',
};

export function isRecommenderEnabledForUser() {
    return localStorage.getItem(USER_PREF_KEY) !== 'false';
}

export function setRecommenderEnabledForUser(enabled) {
    localStorage.setItem(USER_PREF_KEY, enabled ? 'true' : 'false');
    if (!enabled) {
        // Hide immediately; data stays in state so re-enabling re-renders.
        const dock = DOM.nextStepDock || document.getElementById('next-step-dock');
        if (dock) dock.classList.add('hidden');
    } else {
        renderDock(state.currentSessionId);
    }
}

function getDock() {
    // domElements captured at load; fall back to live lookup for safety.
    return DOM.nextStepDock || document.getElementById('next-step-dock');
}

function submitSuggestion(prompt) {
    const input = DOM.userInput || document.getElementById('user-input');
    const form = DOM.chatForm || document.getElementById('chat-form');
    if (!input || !form) return;
    input.value = prompt;
    // Match the chat composer auto-grow + enabled-state behaviour.
    input.dispatchEvent(new Event('input', { bubbles: true }));
    form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
}

function buildChip(item, index) {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'next-step-chip' + (item.kind === 'action' ? ' next-step-chip--action' : '');
    chip.title = item.prompt || item.label;
    chip.setAttribute('aria-label',
        (item.kind === 'action' ? 'Action: ' : 'Ask: ') + (item.label || ''));
    // Subtle staggered entrance for a polished reveal.
    chip.style.setProperty('--ns-i', String(index));

    const iconSvg = ICONS[item.icon] || ICONS.followup;
    const iconSpan = document.createElement('span');
    iconSpan.className = 'next-step-chip__icon';
    iconSpan.setAttribute('aria-hidden', 'true');
    iconSpan.innerHTML = iconSvg;

    const labelSpan = document.createElement('span');
    labelSpan.className = 'next-step-chip__label';
    labelSpan.textContent = item.label;

    chip.appendChild(iconSpan);
    chip.appendChild(labelSpan);

    // click fires for BOTH mouse and keyboard (Enter/Space) → accessible.
    let fired = false;
    const activate = () => {
        if (fired) return;          // guard against double-submit
        fired = true;
        submitSuggestion(item.prompt || item.label);
    };
    chip.addEventListener('click', activate);
    return chip;
}

/**
 * Store suggestions for a session and render if it's the active one.
 * Called from the SSE notification handler (already session-guarded there too).
 *
 * The emitting profile's chips are recorded as the displayed set AND seeded into
 * the per-profile rebind cache. When the turn is `rebindable`, the session is
 * marked so that changing the composer's @TAG override re-filters the chips for
 * that same result (see applyEffectiveProfile) — even if the emitting profile
 * itself produced zero chips (a different class may surface some).
 */
export function setSuggestions(sessionId, turnId, items, profileId, rebindable) {
    if (!sessionId) return;
    items = Array.isArray(items) ? items : [];

    if (rebindable) {
        state.nextStepRebind[sessionId] = { turnId, baseProfileId: profileId || null };
        if (profileId) {
            if (!state.nextStepCache[sessionId]) state.nextStepCache[sessionId] = {};
            state.nextStepCache[sessionId][profileId] = items;
        }
    }

    // Empty + rebindable: keep the rebind context, just hide the dock for now.
    // Empty + not rebindable: nothing to track → clear.
    if (items.length === 0 && !rebindable) {
        clearDock(sessionId);
        return;
    }
    if (items.length === 0) {
        // Rebindable but the emitting profile produced nothing — hide, keep state.
        state.nextStepSuggestions[sessionId] = { turnId, items: [], profileId: profileId || null };
        renderDock(sessionId);
        return;
    }

    state.nextStepSuggestions[sessionId] = { turnId, items, profileId: profileId || null };
    renderDock(sessionId);
}

/** Resolve the composer's *effective* profile id (override-first), mirroring the
 *  backend's resolution: a valid leading @TAG (in the input or captured badge)
 *  → that profile; otherwise the default profile. Returns null if unknown. */
export function getEffectiveProfileId() {
    const cs = window.configState;
    if (!cs || !Array.isArray(cs.profiles)) return null;
    const input = DOM.userInput || document.getElementById('user-input');
    const raw = (input && input.value) || '';
    let tag = null;
    const m = raw.match(/^@([\w-]+)/);
    if (m) {
        tag = m[1].toUpperCase();
    } else if (window.activeTagPrefix) {
        const m2 = String(window.activeTagPrefix).match(/^@([\w-]+)/);
        if (m2) tag = m2[1].toUpperCase();
    }
    if (tag) {
        const p = cs.profiles.find(pr => (pr.tag || '').toUpperCase() === tag);
        if (p) return p.id;            // valid override (or the default's own tag — same id)
    }
    return cs.defaultProfileId || null;
}

function _setDisplayed(sessionId, turnId, items, profileId) {
    state.nextStepSuggestions[sessionId] = { turnId, items: items || [], profileId };
    renderDock(sessionId);
}

/**
 * Re-filter the dock to the composer's currently-selected profile against the
 * last (rebindable) turn — 0 tokens, no new turn. Cache-first; on miss, fetches
 * the free (A+B) chips for that profile from the rebind endpoint and caches them
 * so toggling the @TAG back and forth re-shows instantly.
 */
export async function applyEffectiveProfile(sessionId) {
    if (!sessionId || sessionId !== state.currentSessionId) return;
    const rebind = state.nextStepRebind[sessionId];
    if (!rebind) return;                                  // no rebindable turn yet
    const profileId = getEffectiveProfileId();
    if (!profileId) return;

    const displayed = state.nextStepSuggestions[sessionId];
    if (displayed && displayed.profileId === profileId) return;  // already showing this profile

    const cache = state.nextStepCache[sessionId] || {};
    if (Object.prototype.hasOwnProperty.call(cache, profileId)) {
        _setDisplayed(sessionId, rebind.turnId, cache[profileId], profileId);
        return;
    }

    try {
        const token = localStorage.getItem('tda_auth_token');
        const resp = await fetch(`/api/v1/sessions/${sessionId}/next-step-rebind`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
            body: JSON.stringify({ profile_id: profileId, turn_id: rebind.turnId }),
        });
        if (!resp.ok) return;
        const data = await resp.json();
        const items = Array.isArray(data.items) ? data.items : [];
        if (!state.nextStepCache[sessionId]) state.nextStepCache[sessionId] = {};
        state.nextStepCache[sessionId][profileId] = items;   // cache even when empty (no re-fetch on toggle)
        // The user may have typed on / switched session while we awaited — re-check.
        if (sessionId === state.currentSessionId && getEffectiveProfileId() === profileId) {
            _setDisplayed(sessionId, rebind.turnId, items, profileId);
        }
    } catch (e) {
        // Silent: a rebind failure must never disturb the composer.
    }
}

let _rebindTimer = null;
/** Debounced entry point for the composer to request a profile rebind. */
export function scheduleProfileRebind() {
    if (_rebindTimer) clearTimeout(_rebindTimer);
    _rebindTimer = setTimeout(() => { applyEffectiveProfile(state.currentSessionId); }, 250);
}

/**
 * Render the dock for the given session. No-op (and hides) unless the session is
 * the one currently being viewed — this is the cross-session isolation guard.
 */
export function renderDock(sessionId) {
    const dock = getDock();
    if (!dock) return;

    if (!isRecommenderEnabledForUser()
        || !sessionId
        || sessionId !== state.currentSessionId) {
        dock.classList.add('hidden');
        dock.innerHTML = '';
        return;
    }

    const entry = state.nextStepSuggestions[sessionId];
    if (!entry || !entry.items || entry.items.length === 0) {
        dock.classList.add('hidden');
        dock.innerHTML = '';
        return;
    }

    dock.innerHTML = '';
    dock.setAttribute('role', 'group');
    dock.setAttribute('aria-label', 'Suggested next steps');

    // Just the chips — one full-width row (see .next-step-dock CSS). No header
    // label and no dismiss control: the dock is ephemeral and clears itself on
    // the next turn, so neither earns the vertical estate.
    const row = document.createElement('div');
    row.className = 'next-step-dock__row';
    entry.items.forEach((item, i) => row.appendChild(buildChip(item, i)));
    dock.appendChild(row);

    dock.classList.remove('hidden');
}

/**
 * Clear suggestions for a session (default: the active one) and hide the dock.
 * Called when a new turn starts and on session switch-away.
 */
export function clearDock(sessionId) {
    const sid = sessionId || state.currentSessionId;
    if (sid) {
        delete state.nextStepSuggestions[sid];
        delete state.nextStepRebind[sid];     // a new turn invalidates the rebindable context
        delete state.nextStepCache[sid];
    }
    const dock = getDock();
    if (dock) {
        dock.classList.add('hidden');
        dock.innerHTML = '';
    }
}
