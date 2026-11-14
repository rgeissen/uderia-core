/**
 * Knowledge Graph Constructor Modal — Declared Model (ISO/IEC 11179 registry).
 *
 * Deterministic, zero-LLM construction from a model that stores its metadata WITH
 * itself as a 11179 registry: pick a database connection, see which registries are
 * actually deployed there, choose one, set granularity, build.  No database name to
 * type, no MCP, no tool-calling profile required.
 *
 * Discovery is a dictionary scan for the five registry tables, so the registry list
 * is real rather than free text — the user cannot pick a registry that isn't there,
 * and each entry already carries the core database its entities deploy into.
 *
 * Mirrors kgAndpSemanticModal.js (same chrome, same footer states, same theme
 * variables); one registry per build because a declared model IS one graph.
 * Theme-aware (CSS variables); monochrome SVG icons.
 */

import { nodesLabel, entitiesLabel, relationshipsLabel, sizeLabel, addedLabel, entityCountOf, draftBreakdown } from '../../kgVocabulary.js?v=1.0';
import { showAppBanner } from '../../bannerSystem.js';
import { IFOC_LABELS, populateKgBackendDropdown, showOverlay, hideOverlay } from './kgConstructorShared.js';

let _overlay = null;
let _connections = [];
let _registries = [];
let _graphs = [];

const CONTENT_ID = 'kg-dm-content';

export function openKgDeclaredModelModal(template, defaults = {}) {
    _ensureModal();
    _populateProfiles();
    populateKgBackendDropdown(_overlay.querySelector('#kg-dm-backend'));
    _resetState();
    _applyDefaults(defaults);
    _loadConnections();
    _loadGraphs();
    showOverlay(_overlay, CONTENT_ID);
}

// ── DOM ─────────────────────────────────────────────────────────────────────

const FIELD_STYLE =
    'background:var(--bg-overlay,rgba(30,30,40,0.8));border:1px solid var(--border-primary,rgba(147,51,234,0.3));color:var(--text-primary,#fff);';

function _checkbox(id, label, hint, checked = false) {
    return `
      <label class="flex items-start gap-2 mb-2 cursor-pointer">
        <input id="${id}" type="checkbox" class="mt-0.5 w-4 h-4 rounded"${checked ? ' checked' : ''}
               style="accent-color:var(--teradata-orange,#9333ea);" />
        <span class="text-sm" style="color:var(--text-primary,#e5e7eb);">${label}
          <span class="text-xs block" style="color:var(--text-muted,#6b7280);">${hint}</span>
        </span>
      </label>`;
}

function _ensureModal() {
    if (_overlay) return;
    _overlay = document.createElement('div');
    _overlay.id = 'kg-dm-overlay';
    _overlay.className =
        'fixed inset-0 z-[9999] flex items-center justify-center bg-black/60 backdrop-blur-sm hidden opacity-0 transition-opacity duration-200';
    _overlay.innerHTML = `
      <div id="${CONTENT_ID}" class="glass-panel rounded-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto transform scale-95 transition-transform duration-200"
           style="background:var(--card-bg,rgba(20,20,30,0.95));border:1px solid var(--border-primary,rgba(147,51,234,0.3));">
        <div class="p-5 border-b" style="border-color:var(--border-secondary,rgba(255,255,255,0.06));">
          <div class="flex items-start justify-between gap-3">
            <div class="flex items-start gap-3">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"
                   style="color:var(--teradata-orange,#F15F22);margin-top:2px;">
                <rect x="3" y="4" width="18" height="16" rx="2"/>
                <path d="M3 9h18M8 4v16M12.5 13h5M12.5 16.5h5"/>
              </svg>
              <div>
                <h3 class="text-lg font-semibold" style="color:var(--text-primary,#fff);">Declared Model</h3>
                <p class="text-xs mt-0.5" style="color:var(--text-muted,#9ca3af);">
                  Reads an ISO/IEC 11179 metadata registry stored with the model. Deterministic · zero tokens · re-syncable.
                </p>
              </div>
            </div>
            <button id="kg-dm-close" class="p-1 rounded hover:opacity-70" aria-label="Close"
                    style="color:var(--text-muted,#9ca3af);">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M18 6 6 18M6 6l12 12"/></svg>
            </button>
          </div>
        </div>

        <div class="p-5">
          <div class="mb-3">
            <label class="block text-xs mb-1" style="color:var(--text-muted,#9ca3af);">Owner profile</label>
            <select id="kg-dm-profile" class="w-full rounded-lg p-2 text-sm outline-none" style="${FIELD_STYLE}"></select>
          </div>

          <div class="mb-3">
            <label class="block text-xs mb-1" style="color:var(--text-muted,#9ca3af);">Database connection</label>
            <select id="kg-dm-connection" class="w-full rounded-lg p-2 text-sm outline-none" style="${FIELD_STYLE}">
              <option value="">Loading connections…</option>
            </select>
          </div>

          <div class="mb-3">
            <label class="block text-xs mb-1" style="color:var(--text-muted,#9ca3af);">Metadata registry</label>
            <select id="kg-dm-registry" class="w-full rounded-lg p-2 text-sm outline-none" style="${FIELD_STYLE}" disabled>
              <option value="">Select a connection first…</option>
            </select>
            <div id="kg-dm-registry-note" class="text-xs mt-1" style="color:var(--text-muted,#6b7280);"></div>
          </div>

          <div class="grid grid-cols-2 gap-3 mb-3">
            <div>
              <label class="block text-xs mb-1" style="color:var(--text-muted,#9ca3af);">Model name</label>
              <input id="kg-dm-model" type="text" placeholder="e.g. Policing Core"
                     class="w-full rounded-lg p-2 text-sm outline-none" style="${FIELD_STYLE}" />
              <div class="text-xs mt-1" style="color:var(--text-muted,#6b7280);">The logical root; every entity is qualified by it.</div>
            </div>
            <div>
              <label class="block text-xs mb-1" style="color:var(--text-muted,#9ca3af);">Core database</label>
              <input id="kg-dm-core" type="text" placeholder="read from the registry"
                     class="w-full rounded-lg p-2 text-sm outline-none" style="${FIELD_STYLE}" />
              <div class="text-xs mt-1" style="color:var(--text-muted,#6b7280);">Where the entities deploy — the trusted scope.</div>
            </div>
          </div>

          <div class="mb-4">
            <label class="block text-xs mb-1" style="color:var(--text-muted,#9ca3af);">Graph name (optional)</label>
            <input id="kg-dm-name" type="text" placeholder="Defaults to the model name"
                   class="w-full rounded-lg p-2 text-sm outline-none" style="${FIELD_STYLE}" />
          </div>

          <div class="mb-3 pt-3" style="border-top:1px solid var(--border-secondary,rgba(255,255,255,0.06));">
            <div class="text-xs font-medium mb-2" style="color:var(--text-muted,#9ca3af);">What to include</div>
            ${_checkbox('kg-dm-columns', 'Columns', 'Definitions, PII classification, declared vs deployed type and decoded values per data element.', true)}
            ${_checkbox('kg-dm-physical', 'Physical layer', 'Deployed schema/table/column with types from the data dictionary — views included. Required for the grounding gate\'s trusted scope.', true)}
            ${_checkbox('kg-dm-validate', 'Validate against the source data', 'Read the data itself: row counts, declared-key uniqueness, stored date shapes, blank ratios — one bounded scan per object, stamped on the graph.', false)}
          </div>

          <div class="mb-3">
            <label class="block text-xs mb-1" style="color:var(--text-muted,#9ca3af);">Graph store backend</label>
            <select id="kg-dm-backend" class="w-full rounded-lg p-2 text-sm outline-none" style="${FIELD_STYLE}"></select>
          </div>

          <div id="kg-dm-notice" class="hidden text-xs rounded-lg p-3 mb-3"
               style="background:rgba(241,95,34,0.08); border:1px solid rgba(241,95,34,0.25); color:var(--text-primary,#e5e7eb); line-height:1.5;"></div>

          <div id="kg-dm-status" class="hidden text-sm rounded-lg p-3 mb-3"
               style="background:var(--hover-bg-strong,rgba(255,255,255,0.04));color:var(--text-muted,#9ca3af);"></div>
          <div id="kg-dm-result" class="hidden text-sm rounded-lg p-3 mb-3"
               style="background:var(--hover-bg-strong,rgba(255,255,255,0.04));color:var(--text-primary,#e5e7eb);"></div>
        </div>

        <!-- Two footer states: once the graph exists, "Build" is no longer owed. -->
        <div class="p-5 pt-0 flex justify-end gap-2">
          <button id="kg-dm-cancel" class="px-4 py-2 rounded-lg text-sm"
                  style="background:var(--hover-bg-strong,rgba(255,255,255,0.06));color:var(--text-primary,#e5e7eb);">Cancel</button>
          <button id="kg-dm-build" class="px-4 py-2 rounded-lg text-sm font-medium"
                  style="background:var(--teradata-orange,#F15F22);color:#fff;">Build Knowledge Graph</button>
          <button id="kg-dm-again" class="px-4 py-2 rounded-lg text-sm hidden"
                  style="background:var(--hover-bg-strong,rgba(255,255,255,0.06));color:var(--text-primary,#e5e7eb);">Build another</button>
          <button id="kg-dm-done" class="px-4 py-2 rounded-lg text-sm font-medium hidden"
                  style="background:var(--teradata-orange,#F15F22);color:#fff;">Done</button>
        </div>
      </div>`;
    document.body.appendChild(_overlay);

    _overlay.querySelector('#kg-dm-close').addEventListener('click', _close);
    _overlay.querySelector('#kg-dm-cancel').addEventListener('click', _close);
    _overlay.addEventListener('click', (e) => { if (e.target === _overlay) _close(); });
    _overlay.querySelector('#kg-dm-connection').addEventListener('change', _loadRegistries);
    _overlay.querySelector('#kg-dm-registry').addEventListener('change', _applyRegistry);
    _overlay.querySelector('#kg-dm-profile').addEventListener('change', _updateNotice);
    _overlay.querySelector('#kg-dm-build').addEventListener('click', _build);
    _overlay.querySelector('#kg-dm-done').addEventListener('click', _close);
    _overlay.querySelector('#kg-dm-again').addEventListener('click', () => {
        _overlay.querySelector('#kg-dm-result').classList.add('hidden');
        _overlay.querySelector('#kg-dm-name').value = '';
        _setFormEnabled(true);
        _setFooter('form');
    });
}

function _close() { hideOverlay(_overlay, CONTENT_ID); }

/** Show only the actions that are actually owed in this state. */
function _setFooter(state) {
    const done_ = state === 'done';
    _overlay.querySelector('#kg-dm-build').classList.toggle('hidden', done_);
    _overlay.querySelector('#kg-dm-cancel').classList.toggle('hidden', done_);
    _overlay.querySelector('#kg-dm-again').classList.toggle('hidden', !done_);
    _overlay.querySelector('#kg-dm-done').classList.toggle('hidden', !done_);
}

/** Lock the inputs that produced a finished graph — the form is now a record. */
function _setFormEnabled(enabled) {
    for (const sel of ['#kg-dm-profile', '#kg-dm-connection', '#kg-dm-registry', '#kg-dm-model',
                       '#kg-dm-core', '#kg-dm-name', '#kg-dm-backend', '#kg-dm-columns',
                       '#kg-dm-physical', '#kg-dm-validate']) {
        const el = _overlay.querySelector(sel);
        if (!el) continue;
        el.disabled = !enabled;
        el.style.opacity = enabled ? '' : '0.6';
    }
    // the registry picker stays disabled until a connection has been scanned
    if (enabled && !_registries.length) _overlay.querySelector('#kg-dm-registry').disabled = true;
}

function _resetState() {
    _registries = [];
    _overlay.querySelector('#kg-dm-result').classList.add('hidden');
    _overlay.querySelector('#kg-dm-status').classList.add('hidden');
    _overlay.querySelector('#kg-dm-name').value = '';
    _overlay.querySelector('#kg-dm-model').value = '';
    _overlay.querySelector('#kg-dm-core').value = '';
    _overlay.querySelector('#kg-dm-registry-note').textContent = '';
    const reg = _overlay.querySelector('#kg-dm-registry');
    reg.innerHTML = '<option value="">Select a connection first…</option>';
    reg.disabled = true;
    _setFooter('form');
    _setFormEnabled(true);
}

function _applyDefaults(defaults = {}) {
    const map = {
        'kg-dm-columns': 'include_columns',
        'kg-dm-physical': 'include_physical',
        'kg-dm-validate': 'validate_with_source',
    };
    for (const [id, key] of Object.entries(map)) {
        if (defaults[key] !== undefined) {
            const el = _overlay.querySelector(`#${id}`);
            if (el) el.checked = !!defaults[key];
        }
    }
    if (defaults.model_name) _overlay.querySelector('#kg-dm-model').value = defaults.model_name;
    if (defaults.core_database) _overlay.querySelector('#kg-dm-core').value = defaults.core_database;
}

function _populateProfiles() {
    const sel = _overlay.querySelector('#kg-dm-profile');
    sel.innerHTML = '<option value="">Select a profile...</option>';
    for (const p of (window.configState?.profiles || [])) {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = `@${p.tag || p.name} (${IFOC_LABELS[p.profile_type] || p.profile_type})`;
        sel.appendChild(opt);
    }
}

/**
 * Say what is about to happen — but ONLY when it is not obvious.  Building a new
 * graph for a profile that already has one silently deactivates the graph that
 * agent is currently grounded on (kept, not deleted). Silent otherwise.
 */
function _updateNotice() {
    if (!_overlay) return;
    const host = _overlay.querySelector('#kg-dm-notice');
    const profileId = _overlay.querySelector('#kg-dm-profile').value;
    const lines = [];
    if (profileId) {
        const active = _graphs.find(x => String(x.profile_id) === String(profileId)
                                    && (x.is_active_for_owner || x.is_active));
        if (active) {
            const nm = active.kg_name || active.name || active.kg_id;
            lines.push(`This agent is currently grounded on <strong>${_esc(nm)}</strong>`
                + `${active.total_entities != null ? ` (${sizeLabel(active)})` : ''}. `
                + `Building a new graph will <strong>deactivate</strong> it — it is kept, not deleted.`);
        }
    }
    host.innerHTML = lines.join('<br><br>');
    host.classList.toggle('hidden', lines.length === 0);
}

// ── data loading ────────────────────────────────────────────────────────────

function _token() { return localStorage.getItem('tda_auth_token'); }

async function _loadConnections() {
    const sel = _overlay.querySelector('#kg-dm-connection');
    try {
        const resp = await fetch('/api/v1/database-connections', {
            headers: { Authorization: `Bearer ${_token()}` },
        });
        const data = await resp.json();
        // Teradata only: the reader speaks the Teradata data dictionary
        // (DBC.TablesV / DBC.ColumnsV), which is what makes the type rule enforceable.
        _connections = (data.connections || [])
            .filter((c) => (c.driver || '').toLowerCase() === 'teradata');
        sel.innerHTML = _connections.length
            ? '<option value="">Select a connection…</option>'
            : '<option value="">No Teradata connections — add one in Components → Connectors → Database Connection</option>';
        for (const c of _connections) {
            const opt = document.createElement('option');
            opt.value = c.id;
            opt.textContent = `${c.name} (${c.host || c.driver || 'teradata'})`;
            sel.appendChild(opt);
        }
    } catch (err) {
        sel.innerHTML = '<option value="">Could not load connections</option>';
        console.error('[Declared-model KG] connections failed:', err);
    }
}

async function _loadGraphs() {
    try {
        const resp = await fetch('/api/v1/knowledge-graph/list',
                                 { headers: { Authorization: `Bearer ${_token()}` } });
        const data = await resp.json();
        _graphs = data.graphs || data.knowledge_graphs || [];
        _updateNotice();
    } catch (err) {
        console.error('[Declared-model KG] could not list graphs:', err);
    }
}

async function _loadRegistries() {
    const connId = _overlay.querySelector('#kg-dm-connection').value;
    const sel = _overlay.querySelector('#kg-dm-registry');
    const note = _overlay.querySelector('#kg-dm-registry-note');
    _registries = [];
    sel.disabled = true;
    if (!connId) {
        sel.innerHTML = '<option value="">Select a connection first…</option>';
        note.textContent = '';
        return;
    }
    sel.innerHTML = '<option value="">Scanning for metadata registries…</option>';
    note.textContent = '';
    try {
        const resp = await fetch(
            `/api/v1/knowledge-graph/declared-registries?connection_id=${encodeURIComponent(connId)}`,
            { headers: { Authorization: `Bearer ${_token()}` } });
        const data = await resp.json();
        if (!resp.ok || data.status !== 'success') {
            throw new Error(data.message || `HTTP ${resp.status}`);
        }
        _registries = data.registries || [];
        if (!_registries.length) {
            sel.innerHTML = '<option value="">No ISO/IEC 11179 registries found</option>';
            note.textContent =
                'No database on this connection holds all five registry tables ' +
                '(object_class, data_element, value_domain, permissible_value, association).';
            return;
        }
        sel.innerHTML = '<option value="">Select a registry…</option>';
        for (const r of _registries) {
            const opt = document.createElement('option');
            opt.value = r.catalog_database;
            const meta = [
                r.entities ? entitiesLabel(r.entities) : null,   // a registry's object classes ARE entities
                (r.subject_areas || []).length ? r.subject_areas.join(' + ') : null,
            ].filter(Boolean).join(' · ');
            opt.textContent = `${r.catalog_database}${meta ? ` (${meta})` : ''}`;
            sel.appendChild(opt);
        }
        sel.disabled = false;
        if (_registries.length === 1) {   // one registry: pick it, nothing to choose
            sel.value = _registries[0].catalog_database;
            _applyRegistry();
        }
        note.textContent = `${_registries.length} registr${_registries.length === 1 ? 'y' : 'ies'} discovered on this connection.`;
    } catch (err) {
        sel.innerHTML = '<option value="">Discovery failed</option>';
        note.textContent = String(err.message || err);
        console.error('[Declared-model KG] registry discovery failed:', err);
    }
}

/** A chosen registry already knows its core database; a model name is derived from it. */
function _applyRegistry() {
    const cat = _overlay.querySelector('#kg-dm-registry').value;
    const r = _registries.find(x => x.catalog_database === cat);
    if (!r) return;
    const core = _overlay.querySelector('#kg-dm-core');
    if (!core.value.trim()) core.value = r.core_database || '';
    const model = _overlay.querySelector('#kg-dm-model');
    if (!model.value.trim()) {
        // "Policing_Core" → "Policing Core": readable, and still the registry's own name
        model.value = (r.core_database || cat).replace(/_/g, ' ');
    }
}

// ── build ───────────────────────────────────────────────────────────────────

async function _build() {
    const profileId = _overlay.querySelector('#kg-dm-profile').value;
    const connectionId = _overlay.querySelector('#kg-dm-connection').value;
    const catalog = _overlay.querySelector('#kg-dm-registry').value;
    const status = _overlay.querySelector('#kg-dm-status');
    const result = _overlay.querySelector('#kg-dm-result');
    const btn = _overlay.querySelector('#kg-dm-build');

    if (!profileId) return showAppBanner('Select an owner profile first.', 'error', 4000);
    if (!connectionId) return showAppBanner('Select a database connection.', 'error', 4000);
    if (!catalog) return showAppBanner('Select a metadata registry.', 'error', 4000);

    const payload = {
        discovery_mode: 'declared_model',
        profile_id: profileId,
        connection_id: connectionId,
        catalog_database: catalog,
        core_database: _overlay.querySelector('#kg-dm-core').value.trim() || undefined,
        model_name: _overlay.querySelector('#kg-dm-model').value.trim() || undefined,
        graph_name: _overlay.querySelector('#kg-dm-name').value.trim() || undefined,
        include_columns: _overlay.querySelector('#kg-dm-columns').checked,
        include_physical: _overlay.querySelector('#kg-dm-physical').checked,
        validate_with_source: _overlay.querySelector('#kg-dm-validate').checked,
        kg_store_config_id: _overlay.querySelector('#kg-dm-backend').value || undefined,
    };

    result.classList.add('hidden');
    status.classList.remove('hidden');
    status.textContent = `Reading ${catalog} and building the graph…`;
    btn.disabled = true;
    btn.style.opacity = '0.6';

    try {
        const resp = await fetch('/api/v1/knowledge-graph/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${_token()}` },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok || data.status !== 'success') {
            throw new Error(data.message || `HTTP ${resp.status}`);
        }
        status.classList.add('hidden');
        result.classList.remove('hidden');
        result.innerHTML = _renderResult(data);
        _setFormEnabled(false);
        _setFooter('done');
        showAppBanner(`Knowledge graph built from ${catalog}.`, 'success', 5000);
        UderiaBus.announce('knowledge_graph', 'created', [data && data.kg_id].filter(Boolean));
        try {
            const { loadKnowledgeGraphsIntelligenceTab } =
                await import('../knowledgeGraphPanelHandler.js?v=1.189');
            loadKnowledgeGraphsIntelligenceTab?.('knowledge-graphs-grid-container');
        } catch (_) { /* non-critical: the graph exists either way */ }
    } catch (err) {
        status.classList.add('hidden');
        result.classList.remove('hidden');
        result.innerHTML =
            `<div style="color:var(--trust-danger,#ef4444);">Build failed</div>
             <div class="text-xs mt-1" style="color:var(--text-muted,#9ca3af);">${_esc(String(err.message || err))}</div>`;
        _setFormEnabled(true);   // a failed build leaves the retry owed
        _setFooter('form');
    } finally {
        btn.disabled = false;
        btn.style.opacity = '1';
    }
}

function _esc(s) {
    return String(s).replace(/[&<>"']/g, (c) =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function _renderResult(data) {
    const st = data.stats || {};
    const muted = 'color:var(--text-muted,#9ca3af);';
    const row = (label, value) =>
        value ? `<div style="${muted}">${label}: ${_esc(String(value))}</div>` : '';
    const n = Number(st.type_mismatches || 0);
    const mismatch = n
        ? `<div class="mt-2 text-xs" style="color:var(--trust-warn,#f59e0b);">
             ${n} data element${n === 1 ? '' : 's'} declare a type the deployed schema contradicts —
             the graph carries the deployed type and records the divergence.
           </div>` : '';
    return `
      <div style="color:var(--trust-pass,#10b981);">Knowledge graph created</div>
      ${row('model', data.model_name)}
      ${row('registry', data.catalog_database)}
      ${row('trusted scope', (data.scope_databases || []).join(', '))}
      <div class="mt-2" style="${muted}">
        ${nodesLabel(data.structural?.entities_added)} · ${relationshipsLabel(data.structural?.relationships_added)}
      </div>
      <div style="${muted}">
        ${entitiesLabel(st.entities_logical)} · ${st.attributes_logical || 0} attributes ·
        ${st.domains || 0} subject areas · ${st.foreign_keys || 0} declared joins · ${st.is_a_edges || 0} supertypes ·
        ${st.pii_columns || 0} PII-classified · ${st.columns_with_value_codes || 0} with decoded values
      </div>
      ${_bridgeLine(st)}
      ${mismatch}`;
}

/** A declared cross-domain correspondence is a CLAIM — report whether its ends resolved. */
function _bridgeLine(st) {
    const n = Number(st.type_correspondences || 0);
    if (!n) return '';
    const unresolved = Number(st.bridges_unresolved || 0);
    const base = `<div style="color:var(--text-muted,#9ca3af);">${n} declared type correspondence`
               + `${n === 1 ? '' : 's'} (${Number(st.bridges_resolved || 0)} resolved) — class-level, never a join</div>`;
    if (!unresolved) return base;
    return base + `<div class="mt-1 text-xs" style="color:var(--trust-warn,#f59e0b);">
        ${unresolved} name${unresolved === 1 ? 's' : ''} an end that does not exist in this
        graph — recorded with its declared basis as a governance finding.</div>`;
}
