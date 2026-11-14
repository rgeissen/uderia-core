/**
 * Knowledge Graph Constructor Modal — AI-Native Data Product (declarative).
 *
 * Deterministic, zero-LLM construction from a DEPLOYED data product: pick a
 * database connection, see which conformant products are actually deployed there,
 * choose one, set granularity, build.  No database name to type, no MCP, no
 * tool-calling profile required.
 *
 * Because discovery is deterministic (a dictionary scan for `data_product_map`),
 * the product list is real rather than free text — the user cannot pick a product
 * that isn't there.
 *
 * Theme-aware (CSS variables); monochrome SVG icons.
 */

import { nodesLabel, entitiesLabel, relationshipsLabel, sizeLabel } from '../../kgVocabulary.js?v=1.0';
import { showAppBanner } from '../../bannerSystem.js';
import { IFOC_LABELS, populateKgBackendDropdown, showOverlay, hideOverlay } from './kgConstructorShared.js';

let _overlay = null;
let _connections = [];
let _products = [];
let _graphs = [];


/** Products the operator has ticked. */
function _selectedProducts() {
    if (!_overlay) return [];
    return [..._overlay.querySelectorAll('.kg-andp-prod:checked')].map(cb => cb.value);
}


/**
 * Say what is about to happen — but ONLY when it is not obvious.
 *
 * The dangerous case is the DEFAULT one: building into "a new knowledge graph"
 * for a profile that already has one silently deactivates the graph that agent is
 * currently grounded on. Nothing is deleted, but the agent stops seeing it, with
 * no warning — which is how this instance ended up with two 3,111-entity NIBRS
 * graphs, one of them quietly retired.
 *
 * Deliberately silent when nothing surprising is happening: a notice that appears
 * every time is one people stop reading, and then it cannot warn about anything.
 */
function _updateNotice() {
    if (!_overlay) return;
    const host = _overlay.querySelector('#kg-andp-notice');
    if (!host) return;
    const picked = _selectedProducts();
    const targetId = _overlay.querySelector('#kg-andp-target').value;
    const profileId = _overlay.querySelector('#kg-andp-profile').value;
    const lines = [];

    if (targetId) {
        const g = _graphs.find(x => String(x.kg_id) === String(targetId));
        const name = (g && (g.kg_name || g.name)) || 'the selected graph';
        const held = g && g.total_entities != null ? ` (${sizeLabel(g)})` : '';
        lines.push(`Adds ${picked.length || 'the selected'} product`
            + `${picked.length === 1 ? '' : 's'} to <strong>${name}</strong>${held}. `
            + `Nothing is removed; a product already in it is updated in place.`);
    } else if (profileId) {
        // Which graph would this profile lose?
        const active = _graphs.find(x => String(x.profile_id) === String(profileId)
                                    && (x.is_active_for_owner || x.is_active));
        if (active) {
            const nm = active.kg_name || active.name || active.kg_id;
            lines.push(`This agent is currently grounded on <strong>${nm}</strong>`
                + `${active.total_entities != null ? ` (${sizeLabel(active)})` : ''}. `
                + `Building a new graph will <strong>deactivate</strong> it — it is kept, not deleted. `
                + `To keep it, choose it under “Build into”.`);
        }
    }
    if (picked.length > 1) {
        lines.push(`${picked.length} products will be built into one graph — they share one `
            + `trusted scope, and names stay product-qualified so they cannot collide.`);
    }

    host.innerHTML = lines.join('<br><br>');
    host.classList.toggle('hidden', lines.length === 0);
}

const CONTENT_ID = 'kg-andp-content';

export function openKgAndpSemanticModal(template, defaults = {}) {
    _ensureModal();
    _populateProfiles();
    populateKgBackendDropdown(_overlay.querySelector('#kg-andp-backend'));
    _resetState();
    _applyDefaults(defaults);
    _loadConnections();
    _loadTargetGraphs();
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
    _overlay.id = 'kg-andp-overlay';
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
                <circle cx="12" cy="5" r="2.2"/><circle cx="5" cy="18" r="2.2"/><circle cx="19" cy="18" r="2.2"/>
                <path d="M12 7.2v4.3M10.4 13.2 6.6 16.2M13.6 13.2l3.8 3"/>
              </svg>
              <div>
                <h3 class="text-lg font-semibold" style="color:var(--text-primary,#fff);">AI-Native Data Product</h3>
                <p class="text-xs mt-0.5" style="color:var(--text-muted,#9ca3af);">
                  Reads a deployed product's own catalog. Deterministic · zero tokens · re-syncable.
                </p>
              </div>
            </div>
            <button id="kg-andp-close" class="p-1 rounded hover:opacity-70" aria-label="Close"
                    style="color:var(--text-muted,#9ca3af);">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M18 6 6 18M6 6l12 12"/></svg>
            </button>
          </div>
        </div>

        <div class="p-5">
          <div class="mb-3">
            <label class="block text-xs mb-1" style="color:var(--text-muted,#9ca3af);">Owner profile</label>
            <select id="kg-andp-profile" class="w-full rounded-lg p-2 text-sm outline-none" style="${FIELD_STYLE}"></select>
          </div>

          <div class="mb-3">
            <label class="block text-xs mb-1" style="color:var(--text-muted,#9ca3af);">Database connection</label>
            <select id="kg-andp-connection" class="w-full rounded-lg p-2 text-sm outline-none" style="${FIELD_STYLE}">
              <option value="">Loading connections…</option>
            </select>
          </div>

          <div class="mb-3">
            <label class="block text-xs mb-1" style="color:var(--text-muted,#9ca3af);">Data products</label>
            <div id="kg-andp-products" class="rounded-lg p-2 text-sm"
                 style="${FIELD_STYLE} max-height:150px; overflow-y:auto;">
              <div style="color:var(--text-muted,#6b7280);">Select a connection first…</div>
            </div>
            <div id="kg-andp-product-note" class="text-xs mt-1" style="color:var(--text-muted,#6b7280);"></div>
          </div>

          <div class="mb-3">
            <label class="block text-xs mb-1" style="color:var(--text-muted,#9ca3af);">Build into</label>
            <select id="kg-andp-target" class="w-full rounded-lg p-2 text-sm outline-none" style="${FIELD_STYLE}">
              <option value="">A new knowledge graph</option>
            </select>
            <div class="text-xs mt-1" style="color:var(--text-muted,#6b7280);">
              An agent binds one graph at a time, so to give it several products put them
              in the same graph. Names are product-qualified, so they cannot collide.
            </div>
          </div>

          <div class="mb-4" id="kg-andp-name-wrap">
            <label class="block text-xs mb-1" style="color:var(--text-muted,#9ca3af);">Graph name (optional)</label>
            <input id="kg-andp-name" type="text" placeholder="Defaults to the product name"
                   class="w-full rounded-lg p-2 text-sm outline-none" style="${FIELD_STYLE}" />
          </div>

          <div class="mb-3 pt-3" style="border-top:1px solid var(--border-secondary,rgba(255,255,255,0.06));">
            <div class="text-xs font-medium mb-2" style="color:var(--text-muted,#9ca3af);">What to include</div>
            ${_checkbox('kg-andp-columns', 'Columns', 'Meanings, PII classification and decoded value codes per column.', true)}
            ${_checkbox('kg-andp-physical', 'Physical layer', 'Deployed schema/table/column with types from the data dictionary. Required for the grounding gate\'s trusted scope.', true)}
            ${_checkbox('kg-andp-knowledge', 'Product knowledge', 'Glossary, proven query recipes, design decisions, naming standards, lineage and quality readings — each linked to what it describes.', true)}
            ${_checkbox('kg-andp-nonbusiness', 'Self-describing modules', 'Include the Semantic / Memory / Observability modules as structure.', true)}
            ${_checkbox('kg-andp-validate', 'Validate against the source data', 'Read the data itself: the stored shape and range of every text-stored date, whether each declared key is unique, blank ratios of coded columns, real row counts — one scan per table, stamped on the graph so agents need no probing at runtime.', false)}
          </div>

          <div class="mb-3">
            <label class="block text-xs mb-1" style="color:var(--text-muted,#9ca3af);">Graph store backend</label>
            <select id="kg-andp-backend" class="w-full rounded-lg p-2 text-sm outline-none" style="${FIELD_STYLE}"></select>
          </div>

          <div id="kg-andp-notice" class="hidden text-xs rounded-lg p-3 mb-3"
               style="background:rgba(241,95,34,0.08); border:1px solid rgba(241,95,34,0.25); color:var(--text-primary,#e5e7eb); line-height:1.5;"></div>

          <div id="kg-andp-status" class="hidden text-sm rounded-lg p-3 mb-3"
               style="background:var(--hover-bg-strong,rgba(255,255,255,0.04));color:var(--text-muted,#9ca3af);"></div>
          <div id="kg-andp-result" class="hidden text-sm rounded-lg p-3 mb-3"
               style="background:var(--hover-bg-strong,rgba(255,255,255,0.04));color:var(--text-primary,#e5e7eb);"></div>
        </div>

        <!-- Two footer states.  Once the graph exists, "Build" is no longer an act
             that is owed — offering it invites a silent duplicate build — so the
             footer swaps to Build another / Done. -->
        <div class="p-5 pt-0 flex justify-end gap-2">
          <button id="kg-andp-cancel" class="px-4 py-2 rounded-lg text-sm"
                  style="background:var(--hover-bg-strong,rgba(255,255,255,0.06));color:var(--text-primary,#e5e7eb);">Cancel</button>
          <button id="kg-andp-build" class="px-4 py-2 rounded-lg text-sm font-medium"
                  style="background:var(--teradata-orange,#F15F22);color:#fff;">Build Knowledge Graph</button>
          <button id="kg-andp-again" class="px-4 py-2 rounded-lg text-sm hidden"
                  style="background:var(--hover-bg-strong,rgba(255,255,255,0.06));color:var(--text-primary,#e5e7eb);">Build another</button>
          <button id="kg-andp-done" class="px-4 py-2 rounded-lg text-sm font-medium hidden"
                  style="background:var(--teradata-orange,#F15F22);color:#fff;">Done</button>
        </div>
      </div>`;
    document.body.appendChild(_overlay);

    _overlay.querySelector('#kg-andp-close').addEventListener('click', _close);
    _overlay.querySelector('#kg-andp-cancel').addEventListener('click', _close);
    _overlay.addEventListener('click', (e) => { if (e.target === _overlay) _close(); });
    _overlay.querySelector('#kg-andp-connection').addEventListener('change', _loadProducts);
    _overlay.querySelector('#kg-andp-target').addEventListener('change', () => {
        // appending never renames the existing graph, so don't offer a name for it
        const appending = !!_overlay.querySelector('#kg-andp-target').value;
        _overlay.querySelector('#kg-andp-name-wrap').classList.toggle('hidden', appending);
        _updateNotice();
    });
    _overlay.querySelector('#kg-andp-profile').addEventListener('change', _updateNotice);
    _overlay.querySelector('#kg-andp-build').addEventListener('click', _build);
    _overlay.querySelector('#kg-andp-done').addEventListener('click', _close);
    _overlay.querySelector('#kg-andp-again').addEventListener('click', () => {
        // Keep the connection and its discovered product list — the likely next
        // action is building the OTHER product on the same connection.
        _overlay.querySelector('#kg-andp-result').classList.add('hidden');
        _overlay.querySelector('#kg-andp-name').value = '';
        _setFormEnabled(true);
        _setFooter('form');
    });
}

function _close() { hideOverlay(_overlay, CONTENT_ID); }

/** Show only the actions that are actually owed in this state. */
function _setFooter(state) {
    const build = _overlay.querySelector('#kg-andp-build');
    const cancel = _overlay.querySelector('#kg-andp-cancel');
    const again = _overlay.querySelector('#kg-andp-again');
    const done = _overlay.querySelector('#kg-andp-done');
    const done_ = state === 'done';
    build.classList.toggle('hidden', done_);
    cancel.classList.toggle('hidden', done_);
    again.classList.toggle('hidden', !done_);
    done.classList.toggle('hidden', !done_);
}

/** Lock the inputs that produced a finished graph — the form is now a record. */
function _setFormEnabled(enabled) {
    for (const cb of _overlay.querySelectorAll('.kg-andp-prod')) cb.disabled = !enabled;
    for (const sel of ['#kg-andp-profile', '#kg-andp-connection',
                       '#kg-andp-name', '#kg-andp-backend', '#kg-andp-columns',
                       '#kg-andp-physical', '#kg-andp-knowledge', '#kg-andp-nonbusiness', '#kg-andp-validate']) {
        const el = _overlay.querySelector(sel);
        if (!el) continue;
        el.disabled = !enabled;
        el.style.opacity = enabled ? '' : '0.6';
    }
}

function _resetState() {
    _products = [];
    const r = _overlay.querySelector('#kg-andp-result');
    const s = _overlay.querySelector('#kg-andp-status');
    r.classList.add('hidden'); s.classList.add('hidden');
    _overlay.querySelector('#kg-andp-name').value = '';
    _overlay.querySelector('#kg-andp-product-note').textContent = '';
    _setFooter('form');
    _setFormEnabled(true);
}

function _applyDefaults(defaults = {}) {
    const map = {
        'kg-andp-columns': 'include_columns',
        'kg-andp-physical': 'include_physical',
        'kg-andp-knowledge': 'include_product_knowledge',
        'kg-andp-nonbusiness': 'include_non_business_modules',
        'kg-andp-validate': 'validate_with_source',
    };
    for (const [id, key] of Object.entries(map)) {
        if (defaults[key] !== undefined) {
            const el = _overlay.querySelector(`#${id}`);
            if (el) el.checked = !!defaults[key];
        }
    }
}

function _populateProfiles() {
    const sel = _overlay.querySelector('#kg-andp-profile');
    sel.innerHTML = '<option value="">Select a profile...</option>';
    for (const p of (window.configState?.profiles || [])) {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = `@${p.tag || p.name} (${IFOC_LABELS[p.profile_type] || p.profile_type})`;
        sel.appendChild(opt);
    }
}

// ── data loading ────────────────────────────────────────────────────────────

function _token() { return localStorage.getItem('tda_auth_token'); }

async function _loadConnections() {
    const sel = _overlay.querySelector('#kg-andp-connection');
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
        console.error('[ANDP KG] connections failed:', err);
    }
}

async function _loadTargetGraphs() {
    const sel = _overlay.querySelector('#kg-andp-target');
    try {
        const resp = await fetch('/api/v1/knowledge-graph/list',
                                 { headers: { Authorization: `Bearer ${_token()}` } });
        const data = await resp.json();
        const graphs = data.graphs || data.knowledge_graphs || [];
        _graphs = graphs;
        sel.innerHTML = '<option value="">A new knowledge graph</option>';
        for (const g of graphs) {
            if (!g.kg_id) continue;
            const opt = document.createElement('option');
            opt.value = g.kg_id;
            // /knowledge-graph/list returns kg_name + total_entities. Reading the
            // wrong field silently degraded this to a raw UUID, which is the one
            // thing this control must never show: appending a product to the wrong
            // graph is the mistake the product-qualified naming exists to survive,
            // and an operator cannot avoid it if every option looks the same.
            const name = g.kg_name || g.name || g.kg_id;
            const count = g.total_entities != null ? g.total_entities : g.entity_count;
            const meta = [
                g.profile_tag ? `@${g.profile_tag}` : null,
                count != null ? sizeLabel({ total_entities: count, entity_types: g.entity_types }) : null,
            ].filter(Boolean).join(' · ');
            opt.textContent = `Add to: ${name}${meta ? ` (${meta})` : ''}`;
            sel.appendChild(opt);
        }
        _updateNotice();
    } catch (err) {
        console.error('[ANDP KG] could not list graphs:', err);
    }
}

async function _loadProducts() {
    const connId = _overlay.querySelector('#kg-andp-connection').value;
    const sel = _overlay.querySelector('#kg-andp-products');
    const note = _overlay.querySelector('#kg-andp-product-note');
    const muted = 'color:var(--text-muted,#6b7280);';
    _products = [];
    if (!connId) {
        sel.innerHTML = `<div style="${muted}">Select a connection first…</div>`;
        note.textContent = '';
        _updateNotice();
        return;
    }
    sel.innerHTML = `<div style="${muted}">Scanning for data products…</div>`;
    note.textContent = '';
    try {
        const resp = await fetch(
            `/api/v1/knowledge-graph/andp-products?connection_id=${encodeURIComponent(connId)}`,
            { headers: { Authorization: `Bearer ${_token()}` } });
        const data = await resp.json();
        if (!resp.ok || (data.status !== 'success' && data.status !== 'partial')) {
            throw new Error(data.message || `HTTP ${resp.status}`);
        }
        _products = data.products || [];
        if (!_products.length) {
            sel.innerHTML = `<div style="${muted}">No AI-Native Data Products found</div>`;
            note.textContent =
                'No database on this connection publishes a data_product_map. ' +
                'A conformant product exposes one in its Semantic module.';
            _updateNotice();
            return;
        }
        // Multi-select: several products in ONE graph is the normal case, because an
        // agent binds one graph. Making it a single act removes the build-then-
        // remember-to-append ritual whose failure mode is a silent duplicate graph.
        sel.innerHTML = '';
        for (const p of _products) {
            const row = document.createElement('label');
            row.className = 'flex items-center gap-2 py-1 cursor-pointer';
            row.innerHTML =
                `<input type="checkbox" class="kg-andp-prod" value="${p.product}" ` +
                `data-semantic="${p.semantic_database || ''}" />` +
                `<span>${p.product}</span>` +
                (p.semantic_database
                    ? `<span style="${muted}font-size:0.7rem;">${p.semantic_database}</span>` : '');
            sel.appendChild(row);
        }
        sel.querySelectorAll('.kg-andp-prod')
           .forEach(cb => cb.addEventListener('change', _updateNotice));
        sel.disabled = false;
        note.textContent = `${_products.length} product${_products.length === 1 ? '' : 's'} discovered on this connection.`;
    } catch (err) {
        sel.innerHTML = '<option value="">Discovery failed</option>';
        note.textContent = String(err.message || err);
        console.error('[ANDP KG] product discovery failed:', err);
    }
}

// ── build ───────────────────────────────────────────────────────────────────

async function _build() {
    const profileId = _overlay.querySelector('#kg-andp-profile').value;
    const connectionId = _overlay.querySelector('#kg-andp-connection').value;
    const products = _selectedProducts();
    const product = products[0] || '';
    const status = _overlay.querySelector('#kg-andp-status');
    const result = _overlay.querySelector('#kg-andp-result');
    const btn = _overlay.querySelector('#kg-andp-build');

    if (!profileId) return showAppBanner('Select an owner profile first.', 'error', 4000);
    if (!connectionId) return showAppBanner('Select a database connection.', 'error', 4000);
    if (!products.length) return showAppBanner('Select at least one data product.', 'error', 4000);

    // semantic_database is a per-product hint; only meaningful for a single pick,
    // and the backend rediscovers it deterministically for each product anyway.
    const selOpt = products.length === 1
        ? _overlay.querySelector('.kg-andp-prod:checked') : null;
    const payload = {
        discovery_mode: 'andp_semantic',
        profile_id: profileId,
        connection_id: connectionId,
        products,
        semantic_database: (selOpt && selOpt.dataset.semantic) || undefined,
        target_kg_id: _overlay.querySelector('#kg-andp-target').value || undefined,
        graph_name: _overlay.querySelector('#kg-andp-name').value.trim() || undefined,
        include_columns: _overlay.querySelector('#kg-andp-columns').checked,
        include_physical: _overlay.querySelector('#kg-andp-physical').checked,
        include_product_knowledge: _overlay.querySelector('#kg-andp-knowledge').checked,
        include_non_business_modules: _overlay.querySelector('#kg-andp-nonbusiness').checked,
        validate_with_source: _overlay.querySelector('#kg-andp-validate')?.checked || false,
        kg_store_config_id: _overlay.querySelector('#kg-andp-backend').value || undefined,
    };

    result.classList.add('hidden');
    status.classList.remove('hidden');
    status.textContent = products.length === 1
        ? `Reading ${product} and building the graph…`
        : `Reading ${products.length} products and building one graph…`;
    btn.disabled = true;
    btn.style.opacity = '0.6';

    try {
        const resp = await fetch('/api/v1/knowledge-graph/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${_token()}` },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok || (data.status !== 'success' && data.status !== 'partial')) {
            throw new Error(data.message || `HTTP ${resp.status}`);
        }
        status.classList.add('hidden');
        result.classList.remove('hidden');
        result.innerHTML = _renderResult(data);
        _setFormEnabled(false);
        _setFooter('done');
        const okList = data.products_built || [product];
        const bad = (data.products || []).filter(p => p.status !== 'success');
        if (bad.length) {
            // Never round a partial build up to a success — a graph missing a
            // product looks identical to a complete one until someone asks it a
            // question it can no longer answer.
            showAppBanner(
                `Built ${okList.join(', ')} — ${bad.length} product` +
                `${bad.length === 1 ? '' : 's'} failed.`, 'warning', 8000);
        } else {
            showAppBanner(`Knowledge graph built from ${okList.join(', ')}.`, 'success', 5000);
        }
        UderiaBus.announce('knowledge_graph', 'created', [data && data.kg_id].filter(Boolean));
        // Refresh the Intelligence tab so the new graph is actually there when the
        // user closes this dialog — without it the build succeeds and the list still
        // looks unchanged until a manual reload.
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
        // a failed build leaves the retry owed — keep the form usable
        _setFormEnabled(true);
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

    // Surface the type-rule finding: a divergence between what the product declares
    // and what is deployed is a finding ABOUT the product, so it is reported here
    // rather than left in a log — and attributed to the product that carries it.
    const mismatchLine = _mismatchLines(data, st);

    const knowledge = [
        st.glossary_terms ? `${st.glossary_terms} glossary terms` : '',
        st.query_recipes ? `${st.query_recipes} query recipes` : '',
        st.design_decisions ? `${st.design_decisions} design decisions` : '',
        st.naming_standards ? `${st.naming_standards} naming standards` : '',
        st.quality_metrics ? `${st.quality_metrics} quality readings` : '',
    ].filter(Boolean).join(' · ');

    const bridgeLine = _bridgeLine(st);
  return `
      <div style="color:${data.status === 'partial'
          ? 'var(--trust-warn,#f59e0b)' : 'var(--trust-pass,#10b981)'};">${
          data.status === 'partial'
              ? 'Graph built — some products failed'
              : (data.appended ? 'Products added to the existing graph'
                               : 'Knowledge graph created')}</div>
      ${_productLines(data)}
      ${row('trusted scope', (data.scope_databases || []).join(', '))}
      <div class="mt-2" style="${muted}">
        ${nodesLabel(data.structural?.entities_added)} · ${relationshipsLabel(data.structural?.relationships_added)}
      </div>
      <div style="${muted}">
        ${entitiesLabel(st.entities_logical)} · ${st.attributes_logical || 0} attributes ·
        ${st.pii_columns || 0} PII-classified · ${st.columns_with_value_codes || 0} with decoded values
      </div>
      ${knowledge ? `<div style="${muted}">${knowledge}</div>` : ''}
      ${bridgeLine}
      ${Number(data.bridge_resolution?.resolved || 0)
          ? `<div style="${muted}">${data.bridge_resolution.resolved} previously dangling `
            + `link${data.bridge_resolution.resolved === 1 ? '' : 's'} resolved once every `
            + `selected product was in the graph</div>` : ''}
      ${mismatchLine}`;
}

/**
 * One line per product, each with its own outcome.
 *
 * A build of several products has several results, and collapsing them into one
 * verdict hides the case that matters: a graph missing a product looks exactly
 * like a complete one until an agent is asked something it can no longer answer.
 */
function _productLines(data) {
    const muted = 'color:var(--text-muted,#9ca3af);';
    const rows = data.products;
    if (!Array.isArray(rows) || !rows.length) {
        return data.product ? `<div style="${muted}">product: ${_esc(data.product)}</div>` : '';
    }
    return rows.map((p) => {
        const okc = 'var(--trust-pass,#10b981)', bad = 'var(--trust-danger,#ef4444)';
        return p.status === 'success'
            ? `<div style="${muted}">${_esc(p.product)} — `
              + `<span style="color:${okc};">built</span> · ${nodesLabel(p.entities_added)}`
              + `${p.semantic_database ? ` · ${_esc(p.semantic_database)}` : ''}</div>`
            : `<div style="${muted}">${_esc(p.product)} — `
              + `<span style="color:${bad};">failed</span>: ${_esc(String(p.error || ''))}</div>`;
    }).join('');
}

/** A declared cross-product link is a CLAIM — report its basis and whether it resolved. */
/**
 * The type-rule finding, attributed to the product that carries it.
 *
 * The stats block is a build-wide sum, but a divergence is a finding ABOUT one
 * product's catalog — a single "1425 columns" line on a CASE+NIBRS card blamed
 * both products for what one of them ships. One line per affected product, named
 * only when there is more than one product to tell apart; an older response with
 * no per-product stats falls back to the aggregate, unattributed.
 */
function _mismatchLines(data, st) {
    const line = (n, who) =>
        `<div class="mt-2 text-xs" style="color:var(--trust-warn,#f59e0b);">
           ${who}${n} column${n === 1 ? '' : 's'} declare a type the deployed schema contradicts —
           the graph carries the deployed type and records the divergence.
         </div>`;
    const rows = (Array.isArray(data.products) ? data.products : [])
        .filter(p => p.status === 'success' && p.stats);
    if (!rows.length) {
        const n = Number(st.type_mismatches || 0);
        return n ? line(n, '') : '';
    }
    const multi = rows.length > 1;
    return rows
        .map(p => ({ p, n: Number((p.stats || {}).type_mismatches || 0) }))
        .filter(x => x.n)
        .map(x => line(x.n, multi ? `${_esc(x.p.product)}: ` : ''))
        .join('');
}

function _bridgeLine(st) {
    const n = Number(st.product_bridges || 0);
    if (!n) return '';
    const unresolved = Number(st.bridges_unresolved || 0);
    const base = `<div style="color:var(--text-muted,#9ca3af);">${n} declared cross-product `
               + `link${n === 1 ? '' : 's'} (${Number(st.bridges_resolved || 0)} resolved)</div>`;
    if (!unresolved) return base;
    return base + `<div class="mt-1 text-xs" style="color:var(--trust-warn,#f59e0b);">
        ${unresolved} point${unresolved === 1 ? 's' : ''} at a target that does not exist in this
        graph — the link is recorded with its declared basis, not treated as a join.</div>`;
}
