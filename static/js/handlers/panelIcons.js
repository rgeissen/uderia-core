/**
 * Panel icons — ONE glyph per function for every component panel's header and menu bar.
 *
 * Rules (enforced by test/test_panel_icons.mjs):
 *   - one function, one glyph: Copy is the same drawing in the canvas and the plan panel,
 *     Close the same in every panel; a renderer imports the glyph from here instead of
 *     keeping its own copy (the copies are how the panels drifted apart);
 *   - one glyph, one function: two different acts never share a drawing — a database
 *     CONNECTION is the plain cylinder, the Database NAVIGATOR is the catalog with a lens;
 *   - industrial style: monochrome line drawings, stroke = currentColor, no fill colour,
 *     no per-icon colour; the button decides the colour (and the accent, if any);
 *   - no size in the markup: the panel CSS sizes every icon (one size per bar), so an icon
 *     can never be drawn at its own size.
 *
 * Loaded as an ES module by main.js (window.PanelIcons) and imported directly by the
 * component renderers (served from /api/v1/components/<id>/renderer).
 */

const svg = (body) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;

export const ICON = Object.freeze({
    copy:       svg('<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>'),
    download:   svg('<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>'),
    close:      svg('<path d="M6 18L18 6M6 6l12 12"/>'),
    run:        svg('<polygon points="6 4 20 12 6 20 6 4"/>'),
    ask:        svg('<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>'),
    // a database CONNECTION — what a statement runs against
    connection: svg('<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>'),
    // the Database NAVIGATOR — browse/describe the catalog (a database with a lens)
    navigator:  svg('<ellipse cx="10" cy="5" rx="7" ry="2.5"/><path d="M3 5v12c0 1.38 3.13 2.5 7 2.5"/><path d="M17 5v4.5"/><path d="M3 11c0 1.38 3.13 2.5 7 2.5"/><circle cx="17" cy="16" r="3"/><path d="M19.2 18.2L21.5 20.5"/>'),
});

if (typeof window !== 'undefined') window.PanelIcons = ICON;
export default ICON;
