#!/usr/bin/env node
/**
 * Profile-editor surface gate — the editor's structure is a pure function of
 * (profile class, capability toggles, tier), decided in ONE table.
 *
 * The defect class this pins: visibility used to be hand-written across four
 * per-class branches PLUS a post-init "re-activate the default tab" pass left
 * over from when Resources was a tabbed pane. They disagreed, and the tab-era
 * pass won — it hid the Intelligence Collections card for every class that also
 * shows the MCP card (Ideate and Optimize), so planner-repository scoping went
 * unreachable in the UI. That scoping is load-bearing for Optimize:
 * planner.py scopes champion-case retrieval to `ragCollections`.
 *
 * Method (mirrors test_posture_editor_roundtrip.mjs): extract the REAL contract
 * block verbatim from configurationHandler.js and execute it. No
 * reimplementation — if the shipped table changes, this fails.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const jsPath = join(here, '..', 'static', 'js', 'handlers', 'configurationHandler.js');
const src = readFileSync(jsPath, 'utf8');
const html = readFileSync(join(here, '..', 'templates', 'index.html'), 'utf8');

let pass = 0; const failures = [];
const check = (name, cond, detail = '') => {
    if (cond) { pass++; } else { failures.push(name + (detail ? ` — ${detail}` : '')); }
};

// ── verbatim extraction ──────────────────────────────────────────────────────
const START = '// PROFILE-EDITOR SURFACE CONTRACT — start';
const END = '// PROFILE-EDITOR SURFACE CONTRACT — end';
const a = src.indexOf(START), b = src.indexOf(END);
if (a < 0 || b < 0) { console.error('FAIL: surface contract markers not found'); process.exit(1); }
const block = src.slice(a, b);
for (const sym of ['EDITOR_SURFACES', 'resolveEditorSurface', 'applyEditorSurface', 'IFOC_PROFILE_CLASSES']) {
    if (!block.includes(sym)) { console.error(`FAIL: extracted block lacks ${sym}`); process.exit(1); }
}
const warnings = [];
const realWarn = console.warn;
console.warn = (...args) => warnings.push(args.join(' '));
const { EDITOR_SURFACES, resolveEditorSurface, applyEditorSurface, IFOC_PROFILE_CLASSES } =
    new Function(block + '\nreturn { EDITOR_SURFACES, resolveEditorSurface, applyEditorSurface, IFOC_PROFILE_CLASSES };')();
console.warn = realWarn;

// ── [1] the table is total over the IFOC classes ─────────────────────────────
check('[1] four IFOC classes declared', IFOC_PROFILE_CLASSES.length === 4);
for (const type of IFOC_PROFILE_CLASSES) {
    const st = resolveEditorSurface({ profileType: type, isPrivileged: true, useMcpTools: true, useKnowledge: true });
    check(`[1] every surface resolves for ${type}`,
        Object.keys(EDITOR_SURFACES).every(k => typeof st[k] === 'boolean'));
}
check('[1] an unknown class still resolves (no crash, no undefined)',
    Object.values(resolveEditorSurface({ profileType: 'future_class' })).every(v => typeof v === 'boolean'));

// ── [2] THE REGRESSION: Intelligence Collections is exposed to every class ───
// Autocomplete (`autocompleteCollections`) is read for every profile class by
// api/routes.py and the recommender's Tier B, and Optimize additionally scopes
// planner retrieval here. No class may lose this card.
for (const type of IFOC_PROFILE_CLASSES) {
    const st = resolveEditorSurface({ profileType: type });
    check(`[2] intelligenceCollections exposed for ${type}`, st.intelligenceCollections === true);
    check(`[2] plannerRepositories exposed for ${type}`, st.plannerRepositories === true);
}
// ...and it is NOT mutually exclusive with the MCP card (the shape of the bug).
const optimize = resolveEditorSurface({ profileType: 'tool_enabled' });
check('[2] Optimize shows MCP *and* Intelligence together',
    optimize.mcpResources === true && optimize.intelligenceCollections === true);
const ideate = resolveEditorSurface({ profileType: 'llm_only', useMcpTools: true });
check('[2] Ideate+tools shows MCP *and* Intelligence together',
    ideate.mcpResources === true && ideate.intelligenceCollections === true);

// ── [3] surfaces follow what the runtime consumes ────────────────────────────
const S = (type, extra = {}) => resolveEditorSurface({ profileType: type, ...extra });
// Query column = ragCollections = the planner. Optimize alone.
check('[3] Query column only for Optimize',
    S('tool_enabled').plannerQueryColumn === true &&
    ['llm_only', 'rag_focused', 'genie'].every(t => S(t, { useMcpTools: true }).plannerQueryColumn === false));
// Knowledge: Focus always; Ideate on its capability toggle; never Optimize/Coordinate.
check('[3] knowledge exposed for Focus', S('rag_focused').knowledgeRepositories === true);
check('[3] knowledge follows the Ideate capability toggle',
    S('llm_only', { useKnowledge: true }).knowledgeRepositories === true &&
    S('llm_only', { useKnowledge: false }).knowledgeRepositories === false);
check('[3] knowledge withheld from Optimize and Coordinate',
    S('tool_enabled').knowledgeRepositories === false && S('genie').knowledgeRepositories === false);
check('[3] reranking + advanced track knowledge exactly',
    IFOC_PROFILE_CLASSES.every(t => [true, false].every(k => {
        const st = S(t, { useKnowledge: k });
        return st.knowledgeReranking === st.knowledgeRepositories &&
               st.knowledgeAdvanced === st.knowledgeRepositories;
    })));
// MCP: Optimize always, Ideate on toggle, never Focus/Coordinate.
check('[3] MCP surfaces follow the Ideate capability toggle',
    S('llm_only', { useMcpTools: true }).mcpResources === true &&
    S('llm_only', { useMcpTools: false }).mcpResources === false);
check('[3] MCP withheld from Focus and Coordinate',
    S('rag_focused', { useMcpTools: true }).mcpResources === false &&
    S('genie', { useMcpTools: true }).mcpResources === false);
check('[3] MCP server field tracks the MCP card',
    IFOC_PROFILE_CLASSES.every(t => [true, false].every(m =>
        S(t, { useMcpTools: m }).mcpServerField === S(t, { useMcpTools: m }).mcpResources)));
check('[3] MCP prompts only for Optimize (LangChain path cannot invoke them)',
    S('tool_enabled').mcpPrompts === true && S('llm_only', { useMcpTools: true }).mcpPrompts === false);
// Class-exclusive surfaces.
check('[3] conversation capabilities only for Ideate',
    S('llm_only').conversationCapabilities === true &&
    ['rag_focused', 'tool_enabled', 'genie'].every(t => S(t).conversationCapabilities === false));
check('[3] genie surfaces only for Coordinate',
    S('genie').genieChildProfiles === true && S('genie').genieSettings === true &&
    ['llm_only', 'rag_focused', 'tool_enabled'].every(t => !S(t).genieChildProfiles && !S(t).genieSettings));
check('[3] dual model + classification only for Optimize',
    S('tool_enabled').dualModel === true && S('tool_enabled').toolClassification === true &&
    ['llm_only', 'rag_focused', 'genie'].every(t => !S(t).dualModel && !S(t).toolClassification));
check('[3] component config + skills are universal',
    IFOC_PROFILE_CLASSES.every(t => S(t).componentConfig === true && S(t).skills === true));
check('[3] system prompts follow tier, for every class',
    IFOC_PROFILE_CLASSES.every(t =>
        S(t, { isPrivileged: true }).systemPrompts === true &&
        S(t, { isPrivileged: false }).systemPrompts === false));

// ── [4] the applier: one state, BOTH channels, on every pass ─────────────────
function stubModal(omit = []) {
    const els = {};
    for (const [key, s] of Object.entries(EDITOR_SURFACES)) {
        if (omit.includes(key)) continue;
        if (els[s.sel]) continue;
        const classes = new Set();
        els[s.sel] = {
            style: {},
            classList: {
                toggle: (c, force) => (force ? classes.add(c) : classes.delete(c)),
                has: c => classes.has(c),
                _set: classes,
            },
        };
    }
    return { querySelector: sel => els[sel] || null, _els: els };
}
{
    const modal = stubModal();
    applyEditorSurface(modal, { profileType: 'rag_focused', isPrivileged: true });
    const mcp = modal._els[EDITOR_SURFACES.mcpResources.sel];
    const intel = modal._els[EDITOR_SURFACES.intelligenceCollections.sel];
    check('[4] hidden surface writes BOTH channels',
        mcp.style.display === 'none' && mcp.classList.has('hidden'));
    check('[4] shown surface clears BOTH channels',
        intel.style.display === '' && !intel.classList.has('hidden'));
    check('[4] Query column carried as a class on the card, not inline styles',
        modal._els[EDITOR_SURFACES.plannerRepositories.sel].classList.has('is-autocomplete-only'));
}
{
    // The residue test: applying Optimize AFTER Focus must fully re-decide.
    const modal = stubModal();
    applyEditorSurface(modal, { profileType: 'rag_focused', isPrivileged: true });
    applyEditorSurface(modal, { profileType: 'tool_enabled', isPrivileged: true });
    const mcp = modal._els[EDITOR_SURFACES.mcpResources.sel];
    const know = modal._els[EDITOR_SURFACES.knowledgeRepositories.sel];
    check('[4] no residue: a surface hidden by the previous class is restored',
        mcp.style.display === '' && !mcp.classList.has('hidden'));
    check('[4] no residue: a surface shown by the previous class is withdrawn',
        know.style.display === 'none' && know.classList.has('hidden'));
    check('[4] Query column re-enabled for Optimize',
        !modal._els[EDITOR_SURFACES.plannerRepositories.sel].classList.has('is-autocomplete-only'));
}
{
    // A declared surface with no element is NAMED, never silently skipped.
    warnings.length = 0;
    const modal = stubModal(['skills']);
    const realWarn2 = console.warn;
    console.warn = (...args) => warnings.push(args.join(' '));
    applyEditorSurface(modal, { profileType: 'genie', isPrivileged: true });
    console.warn = realWarn2;
    check('[4] a missing declared element is reported', warnings.some(w => w.includes('skills')));
}

// ── [5] the dead tab machinery stays dead ────────────────────────────────────
const code = src.replace(/^\s*(\/\/.*|\*.*)$/gm, '');   // prose about the code is not the code
check('[5] no tab switcher survives', !code.includes('switchToTab'));
check('[5] no autocomplete-only helper survives', !code.includes('setPlannerAutocompleteOnly'));
for (const id of ['profile-tab-mcp-resources', 'profile-tab-intelligence', 'profile-tab-skills', 'profile-tab-system-prompts']) {
    check(`[5] shim id gone from JS: ${id}`, !code.includes(id));
    check(`[5] shim id gone from markup: ${id}`, !html.includes(`id="${id}"`));
}
check('[5] no .closest(".mb-6") archaeology', !code.includes(`closest('.mb-6')`));

// ── [6] every declared selector exists in the markup ─────────────────────────
for (const [key, s] of Object.entries(EDITOR_SURFACES)) {
    check(`[6] markup carries ${key} (${s.sel})`, html.includes(`id="${s.sel.slice(1)}"`));
}

// ── [7] the contract is the ONLY thing deciding these surfaces ───────────────
// Anything outside the block writing display on a contract element would
// reintroduce the two-mechanisms defect that caused the regression.
const outside = src.slice(0, a) + src.slice(b);
const leaks = Object.entries(EDITOR_SURFACES)
    .filter(([, s]) => outside.includes(s.sel) &&
        new RegExp(`querySelector\\(['"]${s.sel.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}['"]\\)[^;]{0,80}style\\.display`).test(outside))
    .map(([k]) => k);
check('[7] no second mechanism sets display on a contract surface', leaks.length === 0, leaks.join(', '));

// ── report ───────────────────────────────────────────────────────────────────
if (failures.length) {
    console.error(`\n${failures.length} FAILED:`);
    for (const f of failures) console.error('  FAIL: ' + f);
    console.error(`\n${pass} passed, ${failures.length} FAILED`);
    process.exit(1);
}
console.log(`ALL PASS — ${pass} assertions (profile-editor surface contract).`);
