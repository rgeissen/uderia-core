/**
 * kgStoreHandler.js
 *
 * Manages KG (knowledge graph) backend configurations and governance settings
 * in the Anatomy → Graph Stores tab.
 *
 * IIFE pattern — exposes via window.kgStoreHandler.
 * Follows the same conventions as vectorStoreHandler.js.
 *
 * Uses CSS variables for all colours (theme-aware).
 * Icons are monochrome inline SVGs only — no emoji.
 */
(function () {
    'use strict';

    let configurations = [];
    let availableBackends = [];    // from GET /kg-store/backends — includes allowed_for_tier
    let allowedBackends = ['sqlite', 'neo4j']; // updated after governance fetch
    let loaded = false;
    let editingConfigId = null;    // null → add mode, string → edit mode
    let activeBackendFilter = 'all';

    const kgBackendColors = {
        all:    '#6b7280',
        sqlite: '#4ade80',
        neo4j:  '#3b82f6',
    };

    // ── API helpers ────────────────────────────────────────────────────────

    function getAuthHeaders() {
        const token = localStorage.getItem('tda_auth_token');
        return {
            'Authorization': `Bearer ${token}`,
            'Content-Type': 'application/json'
        };
    }

    async function apiGet(url) {
        const res = await fetch(url, { headers: getAuthHeaders() });
        if (!res.ok) {
            const text = await res.text();
            throw new Error(`HTTP ${res.status}: ${text}`);
        }
        return res.json();
    }

    async function apiPost(url, body) {
        const res = await fetch(url, {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify(body)
        });
        return { status: res.status, data: await res.json() };
    }

    async function apiPut(url, body) {
        const res = await fetch(url, {
            method: 'PUT',
            headers: getAuthHeaders(),
            body: JSON.stringify(body)
        });
        return { status: res.status, data: await res.json() };
    }

    async function apiDelete(url) {
        const res = await fetch(url, {
            method: 'DELETE',
            headers: getAuthHeaders()
        });
        return { status: res.status, data: await res.json() };
    }

    // ── Load & Render ──────────────────────────────────────────────────────

    async function load() {
        const container = document.getElementById('kg-stores-container');
        if (!container) return;

        container.innerHTML = '<p style="color: var(--text-muted); text-align: center; padding: 2rem 0;">Loading KG backends…</p>';

        try {
            // Fetch backend list (includes allowed_for_tier)
            const backendsResult = await apiGet('/api/v1/kg-store/backends');
            availableBackends = backendsResult.backends || [];
            allowedBackends = availableBackends
                .filter(b => b.allowed_for_tier)
                .map(b => b.backend_type);

            const result = await apiGet('/api/v1/kg-store/configurations');
            if (result.kg_enabled === false) {
                container.innerHTML = '<p style="color: var(--text-muted); text-align: center; padding: 2rem 0;">Knowledge Graph feature is disabled for your tier.</p>';
                return;
            }
            configurations = result.configurations || [];
            resetBackendTabs();
            _renderConfigurations(container);
            loaded = true;
        } catch (e) {
            container.innerHTML = `<p style="color: #f87171; text-align: center; padding: 2rem 0;">Failed to load KG backends: ${e.message}</p>`;
        }
    }

    function isLoaded() { return loaded; }

    function _backendColor(backendType) {
        return kgBackendColors[backendType] || '#6b7280';
    }

    // ── Filter tabs ────────────────────────────────────────────────────────

    function resetBackendTabs() {
        activeBackendFilter = 'all';
        const tabs = document.querySelectorAll('.kg-backend-tab');
        tabs.forEach(t => {
            if (t.dataset.backend === 'all') {
                t.classList.remove('text-gray-400', 'border-transparent', 'hover:border-white/20');
                t.classList.add('text-white');
                t.style.borderColor = kgBackendColors['all'];
            } else {
                t.classList.remove('text-white');
                t.classList.add('text-gray-400', 'border-transparent', 'hover:border-white/20');
                t.style.borderColor = '';
            }
        });
    }

    function setActiveFilter(backend) {
        activeBackendFilter = backend;
        const color = kgBackendColors[backend] || '#6b7280';
        document.querySelectorAll('.kg-backend-tab').forEach(t => {
            if (t.dataset.backend === backend) {
                t.classList.remove('text-gray-400', 'border-transparent', 'hover:border-white/20');
                t.classList.add('text-white');
                t.style.borderColor = color;
            } else {
                t.classList.remove('text-white');
                t.classList.add('text-gray-400', 'border-transparent', 'hover:border-white/20');
                t.style.borderColor = '';
            }
        });
        const container = document.getElementById('kg-stores-container');
        if (container) _renderConfigurations(container);
    }

    function _renderConfigurations(container) {
        const filtered = activeBackendFilter === 'all'
            ? configurations
            : configurations.filter(c => c.backend_type === activeBackendFilter);

        if (filtered.length === 0) {
            container.innerHTML = `
                <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 4rem 0; text-align: center;">
                    <svg xmlns="http://www.w3.org/2000/svg" style="width: 4rem; height: 4rem; color: var(--text-muted); margin-bottom: 1rem;" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z"/>
                    </svg>
                    <p style="color: var(--text-primary); font-size: 1.125rem; font-weight: 500;">No KG backends configured${activeBackendFilter !== 'all' ? ' for ' + activeBackendFilter : ''}</p>
                    <p style="color: var(--text-muted); font-size: 0.875rem; margin-top: 0.5rem;">Click "Add KG Backend" to create a configuration.</p>
                </div>`;
            return;
        }
        container.innerHTML = filtered.map(cfg => _renderCard(cfg)).join('');
    }

    function _renderCard(cfg) {
        const color = _backendColor(cfg.backend_type);
        const isDefault = cfg.is_default || cfg.id === 'kg-default-sqlite';
        const isRestricted = !allowedBackends.includes(cfg.backend_type);
        const backendLabel = cfg.backend_type === 'sqlite' ? 'SQLite' : cfg.backend_type === 'neo4j' ? 'Neo4j' : cfg.backend_type;
        const canDelete = cfg.id !== 'kg-default-sqlite' && !isRestricted;

        const defaultBadge = isDefault
            ? `<span style="font-size: 0.7rem; font-weight: 600; padding: 0.1rem 0.5rem; border-radius: 9999px; background: rgba(74,222,128,0.12); border: 1px solid rgba(74,222,128,0.25); color: #4ade80;">Default</span>`
            : '';
        const restrictedBadge = isRestricted
            ? `<span style="font-size: 0.7rem; font-weight: 600; padding: 0.1rem 0.5rem; border-radius: 9999px; background: rgba(248,113,113,0.12); border: 1px solid rgba(248,113,113,0.25); color: #f87171;">Restricted</span>`
            : '';

        const actions = isRestricted
            ? `<span style="font-size: 0.75rem; font-style: italic; color: #f87171;">Backend restricted by admin</span>`
            : [
                `<button onclick="window.kgStoreHandler.testConfig('${_escAttr(cfg.id)}')" class="card-btn card-btn--info" style="font-size: 0.75rem; padding: 0.3rem 0.75rem;">Test</button>`,
                `<button onclick="window.kgStoreHandler.showEditModal('${_escAttr(cfg.id)}')" class="card-btn" style="font-size: 0.75rem; padding: 0.3rem 0.75rem; background: rgba(255,255,255,0.05); border-color: rgba(255,255,255,0.1); color: var(--text-muted);">Edit</button>`,
                canDelete ? `<button onclick="window.kgStoreHandler.deleteConfig('${_escAttr(cfg.id)}')" class="card-btn card-btn--danger" style="font-size: 0.75rem; padding: 0.3rem 0.75rem;">Delete</button>` : '',
              ].join('');

        return `
        <div class="glass-panel rounded-xl p-5${isRestricted ? ' opacity-50' : ''}" data-config-id="${cfg.id}" style="border-left: 3px solid ${color};">
            <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                <div style="display: flex; align-items: center; gap: 0.75rem;">
                    <div style="width: 2.5rem; height: 2.5rem; border-radius: 0.5rem; background: ${color}22; display: flex; align-items: center; justify-content: center;">
                        <svg xmlns="http://www.w3.org/2000/svg" style="width: 1.25rem; height: 1.25rem;" fill="none" viewBox="0 0 24 24" stroke="${color}" stroke-width="2">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z"/>
                        </svg>
                    </div>
                    <div>
                        <div style="display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;">
                            <span style="color: var(--text-primary); font-weight: 600;">${_escHtml(cfg.name)}</span>
                            <span style="font-size: 0.75rem; font-weight: 600; padding: 0.1rem 0.5rem; border-radius: 9999px; background: ${color}22; border: 1px solid ${color}44; color: ${color};">${backendLabel}</span>
                            ${defaultBadge}${restrictedBadge}
                        </div>
                        <div style="color: var(--text-muted); font-size: 0.75rem; margin-top: 0.25rem;">ID: ${_escHtml(cfg.id)}</div>
                    </div>
                </div>
                <div style="display: flex; gap: 0.5rem; flex-shrink: 0; align-items: center;">
                    ${actions}
                </div>
            </div>
            ${cfg.backend_type === 'neo4j' ? _renderNeo4jSummary(cfg) : ''}
            <div id="kg-test-result-${cfg.id}" style="margin-top: 0.75rem; display: none;"></div>
        </div>`;
    }

    function _renderNeo4jSummary(cfg) {
        const c = cfg.config || {};
        return `
        <div style="margin-top: 0.75rem; padding-top: 0.75rem; border-top: 1px solid var(--border-secondary); display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.5rem;">
            <div>
                <div style="color: var(--text-muted); font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em;">URI</div>
                <div style="color: var(--text-primary); font-size: 0.8rem; font-family: monospace;">${_escHtml(c.uri || 'bolt://localhost:7687')}</div>
            </div>
            <div>
                <div style="color: var(--text-muted); font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em;">Username</div>
                <div style="color: var(--text-primary); font-size: 0.8rem;">${_escHtml(c.username || 'neo4j')}</div>
            </div>
            <div>
                <div style="color: var(--text-muted); font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em;">Database</div>
                <div style="color: var(--text-primary); font-size: 0.8rem;">${_escHtml(c.database || 'neo4j')}</div>
            </div>
        </div>`;
    }

    // ── Test connection ────────────────────────────────────────────────────

    async function testConfig(configId) {
        const resultEl = document.getElementById(`kg-test-result-${configId}`);
        if (!resultEl) return;
        resultEl.style.display = 'block';
        resultEl.innerHTML = '<span style="color: var(--text-muted); font-size: 0.8rem;">Testing connection…</span>';
        try {
            const { data } = await apiPost(`/api/v1/kg-store/configurations/${encodeURIComponent(configId)}/test`, {});
            if (data.success) {
                resultEl.innerHTML = `<span style="color: #4ade80; font-size: 0.8rem;">&#10003; ${_escHtml(data.message)}</span>`;
            } else {
                resultEl.innerHTML = `<span style="color: #f87171; font-size: 0.8rem;">&#10007; ${_escHtml(data.message)}</span>`;
            }
        } catch (e) {
            resultEl.innerHTML = `<span style="color: #f87171; font-size: 0.8rem;">&#10007; ${e.message}</span>`;
        }
    }

    // ── Add / Edit modal ───────────────────────────────────────────────────

    function showAddModal() {
        editingConfigId = null;
        _populateBackendSelect();
        document.getElementById('kg-store-modal-title').textContent = 'Add KG Backend';
        document.getElementById('kg-store-form').reset();
        document.getElementById('kg-store-modal-id').value = '';
        _toggleBackendFields();
        document.getElementById('kg-store-modal').classList.remove('hidden');
    }

    function showEditModal(configId) {
        const cfg = configurations.find(c => c.id === configId);
        if (!cfg) return;
        editingConfigId = configId;
        _populateBackendSelect();
        document.getElementById('kg-store-modal-title').textContent = 'Edit KG Backend';
        document.getElementById('kg-store-modal-id').value = cfg.id;
        document.getElementById('kg-modal-name').value = cfg.name || '';
        const bt = document.getElementById('kg-modal-backend-type');
        bt.value = cfg.backend_type;
        bt.disabled = true;   // cannot change type after creation
        _toggleBackendFields();

        const c = cfg.config || {};
        if (cfg.backend_type === 'neo4j') {
            document.getElementById('kg-modal-neo4j-uri').value      = c.uri      || 'bolt://localhost:7687';
            document.getElementById('kg-modal-neo4j-username').value = c.username || 'neo4j';
            document.getElementById('kg-modal-neo4j-password').value = c.password || '';
            document.getElementById('kg-modal-neo4j-database').value = c.database || 'neo4j';
        }
        document.getElementById('kg-store-modal').classList.remove('hidden');
    }

    function closeModal() {
        document.getElementById('kg-store-modal').classList.add('hidden');
        const bt = document.getElementById('kg-modal-backend-type');
        if (bt) bt.disabled = false;
    }

    function _populateBackendSelect() {
        const sel = document.getElementById('kg-modal-backend-type');
        if (!sel) return;
        sel.innerHTML = availableBackends
            .filter(b => b.allowed_for_tier)
            .map(b => `<option value="${_escAttr(b.backend_type)}">${_escHtml(b.backend_type === 'sqlite' ? 'SQLite (Local)' : b.backend_type === 'neo4j' ? 'Neo4j' : b.backend_type)}</option>`)
            .join('');
        if (sel.options.length === 0) {
            sel.innerHTML = '<option value="">No backends allowed for your tier</option>';
        }
    }

    function toggleBackendFields() { _toggleBackendFields(); }

    function _toggleBackendFields() {
        const bt = document.getElementById('kg-modal-backend-type');
        const neo4jFields = document.getElementById('kg-modal-neo4j-fields');
        if (!bt || !neo4jFields) return;
        neo4jFields.style.display = bt.value === 'neo4j' ? '' : 'none';
    }

    // ── Theme-compliant dialog helpers (no OS-default dialogs) ─────────────
    function _kgNotify(message, type = 'info') {
        if (typeof window.showAppBanner === 'function') window.showAppBanner(message, type);
        else if (typeof window.showNotification === 'function') window.showNotification(type, message);
        else console.log(`[kgStore][${type}] ${message}`);
    }

    function _kgConfirm(message, onConfirm) {
        if (typeof window.showConfirmBanner === 'function') window.showConfirmBanner(message, onConfirm);
        else if (typeof window.showConfirmation === 'function') window.showConfirmation('Confirm', `<p>${message}</p>`, onConfirm);
        else if (window.confirm(message)) onConfirm();
    }

    async function saveConfig() {
        const name = document.getElementById('kg-modal-name').value.trim();
        const backendType = document.getElementById('kg-modal-backend-type').value;
        if (!name) { _kgNotify('Name is required.', 'error'); return; }
        if (!backendType) { _kgNotify('Backend type is required.', 'error'); return; }

        const config = {};
        if (backendType === 'neo4j') {
            config.uri      = document.getElementById('kg-modal-neo4j-uri').value.trim()      || 'bolt://localhost:7687';
            config.username = document.getElementById('kg-modal-neo4j-username').value.trim() || 'neo4j';
            config.password = document.getElementById('kg-modal-neo4j-password').value;
            config.database = document.getElementById('kg-modal-neo4j-database').value.trim() || 'neo4j';
        }

        try {
            let result;
            if (editingConfigId) {
                result = await apiPut(`/api/v1/kg-store/configurations/${encodeURIComponent(editingConfigId)}`, { name, config });
            } else {
                result = await apiPost('/api/v1/kg-store/configurations', { name, backend_type: backendType, config });
            }
            if (result.status === 200 || result.status === 201 || result.data?.status === 'success') {
                closeModal();
                loaded = false;
                await load();
            } else {
                _kgNotify(`Error: ${result.data?.message || 'Unknown error'}`, 'error');
            }
        } catch (e) {
            _kgNotify(`Error: ${e.message}`, 'error');
        }
    }

    async function deleteConfig(configId) {
        if (configId === 'kg-default-sqlite') {
            _kgNotify('The default SQLite backend cannot be deleted.', 'error');
            return;
        }
        _kgConfirm(`Delete KG backend "${configId}"? This cannot be undone.`, async () => {
            try {
                const { data } = await apiDelete(`/api/v1/kg-store/configurations/${encodeURIComponent(configId)}`);
                if (data.status === 'success') {
                    loaded = false;
                    await load();
                } else {
                    _kgNotify(`Error: ${data.message || 'Unknown error'}`, 'error');
                }
            } catch (e) {
                _kgNotify(`Error: ${e.message}`, 'error');
            }
        });
    }

    // ── Profile dropdown population ────────────────────────────────────────

    /**
     * Populate the #profile-modal-kg-store select element.
     * Called by configurationHandler when the profile edit modal opens.
     */
    async function populateProfileSelect(currentConfigId) {
        const sel = document.getElementById('profile-modal-kg-store');
        if (!sel) return;
        sel.innerHTML = '<option value="">Loading…</option>';
        try {
            const result = await apiGet('/api/v1/kg-store/configurations');
            const cfgs = result.configurations || [];
            sel.innerHTML = '<option value="">Default (automatic)</option>' +
                cfgs.map(c => `<option value="${_escAttr(c.id)}">${_escHtml(c.name)}</option>`).join('');
            if (currentConfigId) sel.value = currentConfigId;
        } catch (_) {
            sel.innerHTML = '<option value="">Default (automatic)</option>';
        }
    }

    // ── Helpers ────────────────────────────────────────────────────────────

    function _escHtml(str) {
        if (str == null) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function _escAttr(str) {
        return _escHtml(str);
    }

    function _showBanner(id, message, type) {
        const el = document.getElementById(id);
        if (!el) return;
        el.textContent = message;
        el.style.color = type === 'success' ? '#4ade80' : '#f87171';
        el.style.display = 'block';
        setTimeout(() => { el.style.display = 'none'; }, 4000);
    }

    // ── Public API ─────────────────────────────────────────────────────────

    window.kgStoreHandler = {
        load,
        isLoaded,
        showAddModal,
        showEditModal,
        closeModal,
        saveConfig,
        deleteConfig,
        testConfig,
        toggleBackendFields,
        populateProfileSelect,
        setActiveFilter,
        resetBackendTabs,
    };

}());
