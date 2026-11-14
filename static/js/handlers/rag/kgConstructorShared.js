/**
 * Shared helpers for KG constructor modals (agent-discovery + industry-model).
 *
 * Extracted so the two modals don't duplicate the graph-store backend dropdown,
 * the IFOC profile labelling, and the overlay show/hide animation.  The legacy
 * kgConstructorModal.js can adopt these later; the industry-model modal uses them
 * now.  Theme-aware (CSS variables); no hardcoded colours.
 */

export const IFOC_LABELS = {
    llm_only: 'Ideate',
    rag_focused: 'Focus',
    tool_enabled: 'Optimize',
    genie: 'Coordinate',
};

/** Populate a <select> with the user's KG-store backend configurations. */
export async function populateKgBackendDropdown(select) {
    if (!select) return;
    select.innerHTML = '<option value="">Use profile / system default</option>';
    try {
        const token = localStorage.getItem('tda_auth_token');
        const resp = await fetch('/api/v1/kg-store/configurations', {
            headers: { Authorization: `Bearer ${token}` },
        });
        if (!resp.ok) return;
        const data = await resp.json();
        for (const cfg of (data.configurations || [])) {
            const opt = document.createElement('option');
            opt.value = cfg.id;
            const label = cfg.backend_type === 'neo4j' ? 'Neo4j' : 'SQLite';
            opt.textContent = `${cfg.name} (${label})${cfg.is_default ? ' — default' : ''}`;
            select.appendChild(opt);
        }
    } catch (_) { /* non-critical */ }
}

/** Animated show of a modal overlay built by the modal modules. */
export function showOverlay(overlay, contentId) {
    overlay.classList.remove('hidden');
    requestAnimationFrame(() => {
        overlay.classList.remove('opacity-0');
        const content = overlay.querySelector(`#${contentId}`);
        if (content) {
            content.classList.remove('scale-95', 'opacity-0');
            content.classList.add('scale-100', 'opacity-100');
        }
    });
}

/** Animated hide of a modal overlay. */
export function hideOverlay(overlay, contentId) {
    const content = overlay.querySelector(`#${contentId}`);
    if (content) {
        content.classList.add('scale-95', 'opacity-0');
        content.classList.remove('scale-100', 'opacity-100');
    }
    overlay.classList.add('opacity-0');
    setTimeout(() => overlay.classList.add('hidden'), 200);
}
