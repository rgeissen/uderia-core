/**
 * resourceBadge.js — ONE rule for every resource-panel badge: active / enabled / available.
 *
 * Three questions about an entry on the agent's list, one word each:
 *   available — it is on the list for this agent (installed, assigned, in its server's
 *               catalog): the denominator
 *   enabled   — it is switched on for this agent; the agent may use it WHEN ASKED
 *               (a tool call, a #skill, a component action)
 *   active    — it is in effect on EVERY TURN without being asked; always a subset of enabled
 * "Auto" is not a state: it says the platform holds the switch (a companion skill following
 * its component, a default-on component). It is a mark on the card, never a count.
 *
 * A badge reads `enabled/available`. A lane where active differs from enabled reads
 * `active/enabled/available` (Skills: an enabled skill may be on request only — `#name` on a
 * turn — while an always-on one is injected every turn). Reading rule: the last figure is
 * always available, the one before it enabled, a third leading figure active. Tools, Prompts,
 * Components, Extensions, Connectors and Knowledge Graphs have no such difference (an enabled
 * tool's definition reaches the model on every turn), so they read two figures.
 *
 * History: Tools and Prompts always counted what the agent may use; the siblings had each
 * drifted (Components counted everything installed, Skills only the auto-applied ones and a
 * bare name at zero, Knowledge Graphs every assigned graph, Resources/Connectors the list),
 * and one figure plus an asterisk left the second number to guesswork. Every tab, and every
 * category tab inside Tools/Prompts, goes through these helpers, so the rule cannot drift.
 *
 * The rail badge and the panel-header count are parsed from the tab text by
 * resourcePanelHandler._syncRailBadgesFromTabs. The Workspace is not a list of switches: it
 * reads `items · sections` (workspaceLabel below) — the sum of what it holds first, then the
 * sections it shows (one per enabled component that keeps instances) — with a middle dot, so
 * the figure is never read as a fraction. The Context tab (a percentage) is not a badge of
 * this kind.
 */

/** { active, enabled, available } — active is null unless the lane passes isActive. */
export function badgeCounts(items, isEnabled, isActive) {
    const list = Array.isArray(items) ? items : [];
    const available = list.length;
    const enabled = list.filter(isEnabled).length;
    const active = typeof isActive === 'function'
        ? list.filter(it => isEnabled(it) && isActive(it)).length
        : null;
    return { active, enabled, available };
}

/** "enabled/available", or "active/enabled/available" when the lane passes isActive. */
export function badgeFigures(items, isEnabled, isActive) {
    const { active, enabled, available } = badgeCounts(items, isEnabled, isActive);
    return active === null ? `${enabled}/${available}` : `${active}/${enabled}/${available}`;
}

/** "Name (enabled/available)" / "Name (active/enabled/available)". */
export function tabLabel(name, items, isEnabled, isActive) {
    return `${name} (${badgeFigures(items, isEnabled, isActive)})`;
}

/**
 * "Workspace (items · sections)": the sum of the instances the Workspace lists, then the number
 * of sections listing them. A middle dot, never a slash — this is not enabled/available.
 */
export function workspaceLabel(items, sections) {
    return `Workspace (${items} · ${sections})`;
}

/**
 * A category tab inside Tools/Prompts: its label, then its own enabled/available figures in a
 * muted span. `items` null → the label alone (a server not yet classified lists nothing).
 */
export function fillCategoryTab(tab, label, items, isEnabled = (it) => !it.disabled) {
    tab.textContent = label;
    if (!Array.isArray(items)) return;
    const fig = document.createElement('span');
    fig.className = 'category-tab-fig';
    fig.textContent = badgeFigures(items, isEnabled);
    tab.appendChild(fig);
}
