/**
 * Knowledge Graph Constructor Modal
 *
 * Single-step workflow: Select profile + enter database name → agent-driven
 * system session discovers structure via MCP tools and populates the KG.
 *
 * Turn 1: Agent discovers tables/columns via MCP tools → structural entities
 * Turn 2 (optional): Agent analyzes business concepts → semantic entities
 *
 * The system session is visible in the session gallery (purple border + gear badge).
 */

import { nodesLabel, entitiesLabel, relationshipsLabel, sizeLabel, addedLabel, entityCountOf, draftBreakdown } from '../../kgVocabulary.js?v=1.0';
import { generateKnowledgeGraph } from '../../api.js?v=1.2';
import { showAppBanner } from '../../bannerSystem.js';

// ── State ────────────────────────────────────────────────────────────────────

let _overlay = null;

// Discovery method is configured in the template's Edit dialog (Discovery Method +
// MCP Context Prompt). The modal honors that configuration — it does not expose a toggle.
let _discoveryMode = 'tool';
let _mcpContextPrompt = null;   // no hardcoded MCP prompt — comes from template config only
let _targetDatabase = null;     // "Target Database" field → physical-layer infrastructure tag

// ── Public API ───────────────────────────────────────────────────────────────

/**
 * Open the KG Constructor modal.
 * Called from the template system Deploy button handler.
 */
export function openKgConstructorModal(template, defaults = {}) {
    _discoveryMode = defaults.discovery_mode === 'prompt' ? 'prompt' : 'tool';
    _mcpContextPrompt = defaults.mcp_context_prompt || null;
    _targetDatabase = defaults.target_database || null;
    _ensureModalExists();
    _populateProfileDropdown();
    _populateBackendDropdown();
    _resetModalState();
    _updateModeNote();
    _showModal();
}

/** Update the helper note under the profile selector to reflect the configured discovery method. */
function _updateModeNote() {
    const note = _overlay?.querySelector('#kg-constructor-mode-note');
    if (!note) return;
    if (_discoveryMode === 'prompt') {
        note.textContent = _mcpContextPrompt
            ? `Prompt-based discovery (set in template): Turn 2 executes MCP prompt "${_mcpContextPrompt}". Only Optimize profiles are listed.`
            : 'Prompt-based discovery (set in template): Turn 2’s MCP context prompt is auto-derived from this profile’s prompts (or set one in the template Edit dialog). Only Optimize profiles are listed.';
    } else {
        note.textContent = 'Tool-based discovery (set in template): schema discovery is deterministic — discovery tools are LLM-derived from the profile’s available tools (or set in the template).';
    }
    // The schema-discovery tools always drive STRUCTURAL discovery (Turn 1), in both
    // modes — so the section stays visible; only the note clarifies its role.
    const discNote = _overlay?.querySelector('#kg-disc-tools-note');
    if (discNote) {
        discNote.innerHTML = (_discoveryMode === 'prompt')
            ? 'Which MCP tool reads the schema for <strong>structural discovery (Turn 1)</strong> — used in this mode too. ' +
              'The MCP context prompt drives <strong>business enrichment (Turn 2)</strong>. Leave on <em>Auto</em> to let the LLM derive them.'
            : 'Which MCP tool plays each discovery role. Leave on <em>Auto</em> to let the LLM ' +
              'derive them from this profile&apos;s available tools.';
    }
}

// ── Modal DOM Construction ───────────────────────────────────────────────────

function _ensureModalExists() {
    if (_overlay) return;

    _overlay = document.createElement('div');
    _overlay.id = 'kg-constructor-modal-overlay';
    _overlay.className = 'fixed inset-0 z-[9999] flex items-center justify-center bg-black/60 backdrop-blur-sm hidden opacity-0 transition-opacity duration-200';

    _overlay.innerHTML = `
        <div id="kg-constructor-modal-content"
             class="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-2xl max-h-[85vh] overflow-y-auto p-8 transform scale-95 opacity-0 transition-all duration-200">

            <!-- Header -->
            <div class="flex items-center justify-between mb-6">
                <div class="flex items-center gap-3">
                    <div class="w-10 h-10 bg-purple-500/20 rounded-lg flex items-center justify-center">
                        <svg class="w-5 h-5 text-purple-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                            <circle cx="12" cy="5" r="2" /><circle cx="5" cy="19" r="2" /><circle cx="19" cy="19" r="2" />
                            <path stroke-linecap="round" stroke-linejoin="round" d="M12 7v4m-5.2 5.8L11 13m2 0l4.2 3.8" />
                        </svg>
                    </div>
                    <div>
                        <h3 class="text-xl font-bold text-white">Knowledge Graph Constructor</h3>
                        <p class="text-xs text-gray-400">Agent-driven database structure discovery</p>
                    </div>
                </div>
                <button id="kg-constructor-close-btn"
                        class="text-gray-400 hover:text-white transition-colors p-1">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
                    </svg>
                </button>
            </div>

            <!-- Configuration -->
            <div id="kg-constructor-config" class="mb-6">
                <!-- Profile selector -->
                <div class="mb-3">
                    <label class="block text-xs text-gray-400 mb-1">Target Profile</label>
                    <select id="kg-constructor-profile" class="w-full p-2 text-sm text-white rounded-lg outline-none cursor-pointer appearance-none" style="background: rgba(30, 30, 40, 0.8); border: 1px solid rgba(147, 51, 234, 0.3); background-image: url('data:image/svg+xml;charset=UTF-8,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%2212%22 height=%2212%22 viewBox=%220 0 12 12%22%3E%3Cpath fill=%22%239ca3af%22 d=%22M2 4l4 4 4-4%22/%3E%3C/svg%3E'); background-repeat: no-repeat; background-position: right 0.75rem center;">
                        <option value="">Select a profile...</option>
                    </select>
                    <p id="kg-constructor-mode-note" class="text-[10px] mt-1" style="color:var(--text-muted,#6b7280);"></p>
                </div>

                <!-- Graph store backend selector -->
                <div class="mb-3">
                    <label class="block text-xs text-gray-400 mb-1">Graph Store Backend</label>
                    <select id="kg-constructor-backend" class="w-full p-2 text-sm text-white rounded-lg outline-none cursor-pointer appearance-none" style="background: rgba(30, 30, 40, 0.8); border: 1px solid rgba(74, 222, 128, 0.25); background-image: url('data:image/svg+xml;charset=UTF-8,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%2212%22 height=%2212%22 viewBox=%220 0 12 12%22%3E%3Cpath fill=%22%239ca3af%22 d=%22M2 4l4 4 4-4%22/%3E%3C/svg%3E'); background-repeat: no-repeat; background-position: right 0.75rem center;">
                        <option value="">Use profile / system default</option>
                    </select>
                    <p id="kg-constructor-backend-note" class="text-[10px] mt-1" style="color:var(--text-muted,#6b7280);">Overrides the profile&apos;s configured backend for this graph only.</p>
                </div>

                <!-- Graph name -->
                <div class="mb-3">
                    <label class="block text-xs text-gray-400 mb-1">Knowledge Graph Name</label>
                    <input id="kg-constructor-graphname" type="text" placeholder="e.g. Fitness App Data Model"
                           class="w-full bg-gray-800 border border-gray-600 rounded-lg p-2 text-sm text-white focus:ring-2 focus:ring-purple-500 focus:border-purple-500 outline-none" />
                </div>

                <!-- Database name -->
                <div class="mb-3">
                    <label class="block text-xs text-gray-400 mb-1">Database Name</label>
                    <input id="kg-constructor-dbname" type="text" placeholder="e.g. fitness_db"
                           class="w-full bg-gray-800 border border-gray-600 rounded-lg p-2 text-sm text-white focus:ring-2 focus:ring-purple-500 focus:border-purple-500 outline-none" />
                </div>

                <!-- Description -->
                <div class="mb-3">
                    <label class="block text-xs text-gray-400 mb-1">Description <span class="text-gray-600">(optional)</span></label>
                    <textarea id="kg-constructor-description" rows="2" placeholder="Describe the purpose of this knowledge graph..."
                              class="w-full bg-gray-800 border border-gray-600 rounded-lg p-2 text-sm text-white focus:ring-2 focus:ring-purple-500 focus:border-purple-500 outline-none resize-none"></textarea>
                </div>

                <!-- Business analysis toggle -->
                <div class="mb-4 flex items-center gap-3">
                    <input id="kg-constructor-semantic-toggle" type="checkbox" checked
                           class="w-4 h-4 rounded border-gray-600 bg-gray-800 text-purple-500 focus:ring-purple-500 focus:ring-offset-0" />
                    <label for="kg-constructor-semantic-toggle" class="text-sm text-gray-300">
                        Include business analysis
                        <span class="text-xs text-gray-500 ml-1">(business concepts, metrics, taxonomies)</span>
                    </label>
                </div>
                <div class="mb-4 flex items-center gap-3">
                    <input id="kg-constructor-validate-toggle" type="checkbox"
                           class="w-4 h-4 rounded border-gray-600 bg-gray-800 text-purple-500 focus:ring-purple-500 focus:ring-offset-0" />
                    <label for="kg-constructor-validate-toggle" class="text-sm text-gray-300"
                           title="Read the data itself through the profile's SQL tool: the stored shape and range of every text-stored date, whether each key is unique, blank ratios, real row counts — stamped on the graph so agents need no probing at runtime. One scan per table.">
                        Validate against the source data
                        <span class="text-xs text-gray-500 ml-1">(date shapes, key uniqueness, row counts — one scan per table)</span>
                    </label>
                </div>

                <!-- Schema discovery tools (advanced override of the auto-derived roles) -->
                <details id="kg-disc-tools-section" class="mb-3 rounded-lg" style="border:1px solid var(--border-secondary,rgba(255,255,255,0.08));">
                    <summary class="px-3 py-2 text-xs cursor-pointer select-none" style="color:var(--text-muted,#9ca3af);">
                        Schema discovery tools <span style="opacity:0.7;">(advanced — defaults to auto)</span>
                    </summary>
                    <div class="px-3 pb-3 pt-1 space-y-2">
                        <p id="kg-disc-tools-note" class="text-[10px]" style="color:var(--text-muted,#6b7280);">
                            Which MCP tool plays each discovery role. Leave on <em>Auto</em> to let the LLM
                            derive them from this profile&apos;s available tools.
                        </p>
                        <div>
                            <label class="block text-[11px] mb-1" style="color:var(--text-muted,#9ca3af);">List tables</label>
                            <select id="kg-disc-list" class="kg-disc-select w-full p-2 text-xs text-white rounded-lg outline-none cursor-pointer" style="background:rgba(30,30,40,0.8);border:1px solid rgba(147,51,234,0.25);">
                                <option value="">Auto (LLM-derived)</option>
                            </select>
                        </div>
                        <div>
                            <label class="block text-[11px] mb-1" style="color:var(--text-muted,#9ca3af);">Describe columns</label>
                            <select id="kg-disc-cols" class="kg-disc-select w-full p-2 text-xs text-white rounded-lg outline-none cursor-pointer" style="background:rgba(30,30,40,0.8);border:1px solid rgba(147,51,234,0.25);">
                                <option value="">Auto (LLM-derived)</option>
                            </select>
                        </div>
                        <div>
                            <label class="block text-[11px] mb-1" style="color:var(--text-muted,#9ca3af);">Table DDL <span style="opacity:0.6;">(optional)</span></label>
                            <select id="kg-disc-ddl" class="kg-disc-select w-full p-2 text-xs text-white rounded-lg outline-none cursor-pointer" style="background:rgba(30,30,40,0.8);border:1px solid rgba(147,51,234,0.25);">
                                <option value="">Auto (LLM-derived)</option>
                            </select>
                        </div>
                    </div>
                </details>

                <!-- Multi-KG info note -->
                <div class="mb-3 p-3 rounded-lg text-xs" style="background: rgba(147,51,234,0.08); border: 1px solid rgba(147,51,234,0.2); color: #c4b5fd;">
                    A new knowledge graph will be created and set as active for the selected owner profile.
                    Any existing graphs owned by that profile will be preserved but deactivated.
                </div>

                <!-- Generate KG button -->
                <button id="kg-constructor-generate-btn"
                        class="card-btn card-btn--primary w-full flex items-center justify-center gap-2">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <circle cx="12" cy="5" r="2" /><circle cx="5" cy="19" r="2" /><circle cx="19" cy="19" r="2" />
                        <path stroke-linecap="round" stroke-linejoin="round" d="M12 7v4m-5.2 5.8L11 13m2 0l4.2 3.8" />
                    </svg>
                    Generate Knowledge Graph
                </button>

                <!-- Generation status -->
                <div id="kg-generate-status" class="hidden mt-3 text-xs text-gray-400 flex items-center gap-2">
                    <svg class="w-4 h-4 animate-spin text-purple-400" fill="none" viewBox="0 0 24 24">
                        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
                    </svg>
                    <span id="kg-generate-status-text">Initializing...</span>
                </div>
            </div>

            <!-- ─── Results (hidden until generation complete) ─── -->
            <div id="kg-results" class="hidden">
                <div class="my-4 border-t border-white/10"></div>

                <div class="glass-panel rounded-xl p-4">
                    <h4 class="text-sm font-semibold text-white mb-3 flex items-center gap-2">
                        <svg class="w-4 h-4 text-green-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>
                        </svg>
                        Knowledge Graph Generated
                    </h4>
                    <div id="kg-results-body" class="space-y-2 text-xs text-gray-300 font-mono"></div>
                </div>

                <button id="kg-constructor-done-btn"
                        class="mt-4 card-btn card-btn--primary w-full">
                    Done
                </button>
            </div>
        </div>
    `;

    document.body.appendChild(_overlay);
    _attachEventListeners();
}

// ── Event Listeners ──────────────────────────────────────────────────────────

function _attachEventListeners() {
    // Close button
    _overlay.querySelector('#kg-constructor-close-btn').addEventListener('click', _hideModal);

    // Overlay backdrop click
    _overlay.addEventListener('click', (e) => {
        if (e.target === _overlay) _hideModal();
    });

    // Done button (in results section)
    _overlay.querySelector('#kg-constructor-done-btn').addEventListener('click', _hideModal);

    // Generate Knowledge Graph
    _overlay.querySelector('#kg-constructor-generate-btn').addEventListener('click', _handleGenerateKG);

    // Populate the advanced discovery-tool dropdowns from the selected profile's tools.
    _overlay.querySelector('#kg-constructor-profile').addEventListener('change', (e) => {
        _populateDiscoveryToolDropdowns(e.target.value);
    });
}

// ── Discovery-tool override dropdowns ────────────────────────────────────────
// Lists the selected profile's MCP tools so the user can override which tool plays
// each discovery role. Empty value = Auto (the LLM derives it). Args are resolved
// automatically (real arg names → probe), so only the tool needs picking.
async function _populateDiscoveryToolDropdowns(profileId) {
    const selects = ['kg-disc-list', 'kg-disc-cols', 'kg-disc-ddl']
        .map(id => _overlay.querySelector('#' + id)).filter(Boolean);
    const reset = () => selects.forEach(s => { s.innerHTML = '<option value="">Auto (LLM-derived)</option>'; });
    reset();
    if (!profileId) return;
    try {
        const token = localStorage.getItem('tda_auth_token');
        const resp = await fetch(`/api/v1/profiles/${encodeURIComponent(profileId)}/resources`, {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        if (!resp.ok) return;
        const data = await resp.json();
        const byCat = data.tools || {};
        const names = [];
        for (const cat of Object.keys(byCat)) {
            for (const t of (byCat[cat] || [])) {
                if (t && t.name && !String(t.name).startsWith('TDA_')) names.push(t.name);
            }
        }
        names.sort((a, b) => a.localeCompare(b));
        for (const s of selects) {
            for (const n of names) {
                const opt = document.createElement('option');
                opt.value = n; opt.textContent = n;
                s.appendChild(opt);
            }
        }
    } catch (_) { /* non-critical — Auto still works */ }
}

/** Collect the advanced discovery-tool overrides into the request shape, or null. */
function _collectDiscoveryTools() {
    const v = id => (_overlay.querySelector('#' + id)?.value || '').trim();
    const list = v('kg-disc-list'), cols = v('kg-disc-cols'), ddl = v('kg-disc-ddl');
    if (!list && !cols && !ddl) return null;   // all Auto → let the backend derive
    const out = {};
    if (list) out.list_tables = { tool: list };
    if (cols) out.describe_columns = { tool: cols };
    if (ddl) out.table_ddl = { tool: ddl };
    return out;
}

// ── Backend Dropdown ─────────────────────────────────────────────────────────

async function _populateBackendDropdown() {
    const select = _overlay.querySelector('#kg-constructor-backend');
    if (!select) return;
    select.innerHTML = '<option value="">Use profile / system default</option>';
    try {
        const token = localStorage.getItem('tda_auth_token');
        const resp = await fetch('/api/v1/kg-store/configurations', {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        if (!resp.ok) return;
        const data = await resp.json();
        const configs = data.configurations || [];
        for (const cfg of configs) {
            const opt = document.createElement('option');
            opt.value = cfg.id;
            const label = cfg.backend_type === 'neo4j' ? 'Neo4j' : 'SQLite';
            opt.textContent = `${cfg.name} (${label})${cfg.is_default ? ' — default' : ''}`;
            select.appendChild(opt);
        }
    } catch (_) { /* non-critical */ }
}

// ── Profile Dropdown ─────────────────────────────────────────────────────────

function _populateProfileDropdown() {
    const select = _overlay.querySelector('#kg-constructor-profile');
    select.innerHTML = '<option value="">Select a profile...</option>';

    const ifocLabels = {
        'llm_only': 'Ideate', 'rag_focused': 'Focus',
        'tool_enabled': 'Optimize', 'genie': 'Coordinate'
    };

    const profiles = window.configState?.profiles || [];
    for (const p of profiles) {
        // Require MCP server + tool-calling capability (tool_enabled or conversation with tools)
        if (!p.mcpServerId) continue;
        const hasToolCalling = p.profile_type === 'tool_enabled' || (p.profile_type === 'llm_only' && p.useMcpTools);
        if (_discoveryMode === 'prompt') {
            // MCP prompts execute only through the Planner/Executor — Optimize profiles only.
            if (p.profile_type !== 'tool_enabled') continue;
        } else if (!hasToolCalling) {
            continue;
        }
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = `@${p.tag || p.name} (${ifocLabels[p.profile_type] || p.profile_type})`;
        select.appendChild(opt);
    }
}

// ── Generate Knowledge Graph ─────────────────────────────────────────────────

async function _handleGenerateKG() {
    const profileId = _overlay.querySelector('#kg-constructor-profile').value;
    const dbName = _overlay.querySelector('#kg-constructor-dbname').value.trim();
    const graphName = _overlay.querySelector('#kg-constructor-graphname').value.trim() || null;
    const description = _overlay.querySelector('#kg-constructor-description').value.trim() || null;
    const includeSemantic = _overlay.querySelector('#kg-constructor-semantic-toggle').checked;
    const validateWithSource = _overlay.querySelector('#kg-constructor-validate-toggle')?.checked || false;
    const kgStoreConfigId = _overlay.querySelector('#kg-constructor-backend')?.value || null;

    if (!profileId) {
        showAppBanner('Please select a target profile.', 'error');
        return;
    }
    if (!dbName) {
        showAppBanner('Please enter a database name.', 'error');
        return;
    }

    const generateBtn = _overlay.querySelector('#kg-constructor-generate-btn');
    const statusEl = _overlay.querySelector('#kg-generate-status');
    const statusText = _overlay.querySelector('#kg-generate-status-text');

    // Progressive status messages
    const statusMessages = [
        { delay: 0, text: 'Creating system session...' },
        { delay: 3000, text: 'Discovering database structure via MCP tools...' },
        { delay: 15000, text: 'Agent is calling tools to list tables and columns...' },
        { delay: 30000, text: 'Parsing structural entities from tool results...' },
    ];
    if (includeSemantic) {
        statusMessages.push(
            { delay: 40000, text: 'Running business analysis (Turn 2)...' },
            { delay: 55000, text: 'Extracting semantic entities...' },
        );
    }
    statusMessages.push({ delay: 70000, text: 'Finalizing knowledge graph...' });

    const timers = [];

    try {
        generateBtn.disabled = true;
        generateBtn.classList.add('opacity-50');
        statusEl.classList.remove('hidden');

        // Schedule progressive status updates
        for (const msg of statusMessages) {
            const timer = setTimeout(() => {
                if (!generateBtn.disabled) return; // already done
                statusText.textContent = msg.text;
            }, msg.delay);
            timers.push(timer);
        }

        const result = await generateKnowledgeGraph(
            profileId, dbName, includeSemantic, graphName, description, kgStoreConfigId,
            _discoveryMode, _discoveryMode === 'prompt' ? _mcpContextPrompt : null,
            _targetDatabase, _collectDiscoveryTools(), validateWithSource
        );

        // Clear timers
        timers.forEach(t => clearTimeout(t));

        // Hide status spinner + collapse the config form
        statusEl.classList.add('hidden');
        _overlay.querySelector('#kg-constructor-config').classList.add('hidden');

        // Show results
        const resultsEl = _overlay.querySelector('#kg-results');
        const resultsBody = _overlay.querySelector('#kg-results-body');
        resultsEl.classList.remove('hidden');

        const s = result.structural || {};
        const sem = result.semantic || {};
        const t = result.total || {};
        const sessionId = result.session_id || '';

        resultsBody.innerHTML = `
            <div class="flex justify-between py-1 border-b border-white/5">
                <span class="text-gray-400">Structural (tables, columns)</span>
                <span class="text-green-400">${nodesLabel(s.entities_added)}, ${relationshipsLabel(s.relationships_added)}</span>
            </div>
            ${includeSemantic ? `
            <div class="flex justify-between py-1 border-b border-white/5">
                <span class="text-gray-400">Semantic (business concepts)</span>
                <span class="text-purple-400">${nodesLabel(sem.entities_added)}, ${relationshipsLabel(sem.relationships_added)}</span>
            </div>` : ''}
            ${result.phase3_relationships ? `
            <div class="flex justify-between py-1 border-b border-white/5">
                <span class="text-gray-400">Gap-fill relationships</span>
                <span class="text-blue-400">${result.phase3_relationships}</span>
            </div>` : ''}
            <div class="flex justify-between py-1 font-bold">
                <span class="text-white">Total</span>
                <span class="text-white">${nodesLabel(t.entities_added)}, ${relationshipsLabel(t.relationships_added)}</span>
            </div>
            ${sessionId ? `
            <div class="flex justify-between py-1 mt-2 border-t border-white/10 pt-2">
                <span class="text-gray-500">System session</span>
                <span class="text-indigo-400 text-xs cursor-pointer hover:underline" onclick="document.querySelector('[data-session-id=&quot;${sessionId}&quot;]')?.click()" title="Click to view the discovery session">${sessionId.substring(0, 8)}...</span>
            </div>` : ''}
        `;

        const statusLabel = result.status === 'partial' ? 'partially completed' : 'completed';
        showAppBanner(
            `Knowledge Graph ${statusLabel}: ${addedLabel(t.entities_added, t.relationships_added)} imported.`,
            result.status === 'partial' ? 'info' : 'success'
        );

        // Refresh KG cards in the Intelligence tab
        try {
            const { loadKnowledgeGraphsIntelligenceTab } = await import('../knowledgeGraphPanelHandler.js?v=1.189');
            if (typeof loadKnowledgeGraphsIntelligenceTab === 'function') {
                loadKnowledgeGraphsIntelligenceTab('knowledge-graphs-grid-container');
            }
        } catch (_) { /* non-critical */ }

    } catch (err) {
        timers.forEach(t => clearTimeout(t));
        console.error('[KG Constructor] Generation failed:', err);
        statusText.textContent = `Error: ${err.message}`;
        showAppBanner(`Knowledge Graph generation failed: ${err.message}`, 'error');
    } finally {
        generateBtn.disabled = false;
        generateBtn.classList.remove('opacity-50');
    }
}

// ── Modal Show/Hide ──────────────────────────────────────────────────────────

function _showModal() {
    _overlay.classList.remove('hidden');
    requestAnimationFrame(() => {
        _overlay.classList.remove('opacity-0');
        const content = _overlay.querySelector('#kg-constructor-modal-content');
        content.classList.remove('scale-95', 'opacity-0');
        content.classList.add('scale-100', 'opacity-100');
    });
}

function _hideModal() {
    const content = _overlay.querySelector('#kg-constructor-modal-content');
    content.classList.add('scale-95', 'opacity-0');
    content.classList.remove('scale-100', 'opacity-100');
    _overlay.classList.add('opacity-0');
    setTimeout(() => _overlay.classList.add('hidden'), 200);
}

function _resetModalState() {
    // Reset results to hidden, restore config form
    const results = _overlay.querySelector('#kg-results');
    const genStatus = _overlay.querySelector('#kg-generate-status');

    results.classList.add('hidden');
    genStatus.classList.add('hidden');
    _overlay.querySelector('#kg-constructor-config').classList.remove('hidden');

    // Reset inputs
    _overlay.querySelector('#kg-constructor-graphname').value = '';
    _overlay.querySelector('#kg-constructor-description').value = '';
    _overlay.querySelector('#kg-constructor-semantic-toggle').checked = true;
    const _vt = _overlay.querySelector('#kg-constructor-validate-toggle'); if (_vt) _vt.checked = false;
    const backendSel = _overlay.querySelector('#kg-constructor-backend');
    if (backendSel) backendSel.selectedIndex = 0;
    // Reset the advanced discovery-tool overrides back to Auto.
    ['kg-disc-list', 'kg-disc-cols', 'kg-disc-ddl'].forEach(id => {
        const s = _overlay.querySelector('#' + id);
        if (s) s.innerHTML = '<option value="">Auto (LLM-derived)</option>';
    });
    const discSection = _overlay.querySelector('#kg-disc-tools-section');
    if (discSection) discSection.open = false;

    // Re-enable button
    const generateBtn = _overlay.querySelector('#kg-constructor-generate-btn');
    generateBtn.disabled = false;
    generateBtn.classList.remove('opacity-50');
}
