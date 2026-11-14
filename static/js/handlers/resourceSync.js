/**
 * resourceSync.js — live sync of the resource panel (Oct 2026).
 *
 * ONE signal for "an agent's configuration changed", ONE subscriber that re-runs the
 * panel's per-profile load (`updateResourcePanelForProfile` in main.js). That load is
 * idempotent and coalesced and already paints EVERY tab and EVERY badge — Tools,
 * Prompts, Connectors, Resources, Knowledge Graphs, Skills, Extensions, Components,
 * Context, Workspace, the enablement gate — so re-running it IS the refresh; no tab
 * keeps a private idea of when it is stale.
 *
 * Before this module a toggle in the Components panel reached the Workspace tab only
 * (its own listener); the Components, Skills and Tools tabs showed the old state until
 * the page was reloaded, and the profile editor, the skill and extension libraries and
 * Administration → Components told the panel nothing at all.
 *
 * Outside calls are covered by the SERVER: every profile write passes `config_manager.save_profiles`,
 * which announces `profile_config_changed` on the per-user notification channel (core/ui_notify.py);
 * notifications.js turns it into the same event. Browser writers of NON-profile state still call
 * `announceProfileConfigChange(profileId, aspect)` after a successful
 * write — `profileId` for a change to ONE agent, `null` for a change that reaches
 * whatever agent is shown (a library, a global setting). The older component-specific
 * signal `uderia:components-changed` is read by the same subscriber.
 *
 * Gate: test/test_resource_panel_live_sync.mjs (no server, no DOM).
 */

export const PROFILE_CONFIG_EVENT = 'uderia:profile-config-changed';
export const COMPONENTS_EVENT = 'uderia:components-changed';
export const PROFILE_CONFIG_EVENTS = Object.freeze([COMPONENTS_EVENT, PROFILE_CONFIG_EVENT]);
/** A browser writer's own announcement and the server's echo of the same write (notifications.js,
 *  `profile_config_changed`), or a burst of saves (a pack import), collapse into ONE reload. */
export const DEBOUNCE_MS = 250;

/**
 * The panel shows ONE agent. A change to another agent is not its to show; a change
 * that names no agent concerns whatever is shown; with nothing shown there is nothing
 * to refresh.
 */
export function concernsShownProfile(changedProfileId, shownProfileId) {
    if (!shownProfileId) return false;
    if (!changedProfileId) return true;
    return String(changedProfileId) === String(shownProfileId);
}

/** Writers: after a successful write. `aspect` names what changed (debugging only). */
export function announceProfileConfigChange(profileId = null, aspect = 'profile') {
    if (typeof document === 'undefined') return;
    try {
        UderiaBus.emit(PROFILE_CONFIG_EVENT, { profileId: profileId || null, aspect });
    } catch (_) { /* a listener must never break the writer */ }
}

/**
 * The subscriber, wired once. `getShown` reads the agent the panel shows;
 * `reload(profileId)` re-runs the panel load for it.
 */
export function wireResourcePanelSync(getShown, reload) {
    if (typeof document === 'undefined') return false;
    if (window.__resourcePanelSyncWired) return false;
    window.__resourcePanelSyncWired = true;
    let timer = null;
    const onChange = (e) => {
        const changed = e && e.detail ? e.detail.profileId : null;
        const shown = getShown();
        if (!concernsShownProfile(changed, shown)) return;
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => {
            timer = null;
            const now = getShown();           // the agent on show WHEN the reload runs
            if (!now) return;
            try { reload(now); } catch (err) { console.error('[Resource Panel] live sync reload failed:', err); }
        }, DEBOUNCE_MS);
    };
    PROFILE_CONFIG_EVENTS.forEach((name) => UderiaBus.on(name, onChange));
    return true;
}

if (typeof window !== 'undefined') {
    window.announceProfileConfigChange = announceProfileConfigChange;   // for the non-module (classic) scripts
}
