/**
 * instancePortabilityUI.js
 *
 * Shared front-end for component-instance export / import (portability).
 *
 * ONE implementation of the download + (file-pick → dry-run preflight → remap
 * dialog → commit) flow, reused by every surface that lists instances — the
 * Workspace resource panel, the Components → Flows tab, and any future one. It
 * talks to the generic `/v1/components/<id>/instances/...` endpoints, so the host
 * surface only supplies the component id + a little display metadata and a
 * refresh callback. Theme-aware throughout (CSS variables); monochrome icons.
 *
 * Public API:
 *   exportInstance(componentId, instanceId, label?) -> downloads a .json file
 *   importInstance(meta, { onDone }?)               -> opens the remap dialog
 *     meta = { component_id, display_name?, icon_svg?, icon_accent? }
 */

// ── small self-contained helpers (no cross-module coupling) ────────────────────
function _authHeaders() {
    const t = localStorage.getItem('tda_auth_token');
    return t ? { 'Authorization': `Bearer ${t}` } : {};
}

function _esc(str) {
    return String(str == null ? '' : str)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function _toast(msg, type = 'info') {
    if (typeof window.showAppBanner === 'function') window.showAppBanner(msg, type);
    else console.log(`[InstanceIO] ${type}: ${msg}`);
}

// IFOC colour for a profile type (fallback when a candidate omits its colour).
function _ifocColor(type) {
    return ({
        llm_only: '#4ade80',     // Ideate
        rag_focused: '#3b82f6',  // Focus
        tool_enabled: '#9333ea', // Optimize
        genie: '#F15F22',        // Coordinate
    })[type] || 'var(--text-muted, #94a3b8)';
}

const _ICON_ARROW = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>`;
const _FALLBACK_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>`;

// ── public: export ─────────────────────────────────────────────────────────────
export async function exportInstance(componentId, instanceId, label) {
    try {
        const r = await fetch(`/api/v1/components/${componentId}/instances/${instanceId}/export`, { headers: _authHeaders() });
        if (!r.ok) throw new Error();
        const blob = await r.blob();
        window.FilesKeep.deliver(blob, `${componentId}_${(label || instanceId)}`.replace(/[^A-Za-z0-9_.-]/g, '_') + '.json',
                                 { what: `${componentId} instance`, serverBuilt: true,
                                   saveUrl: `/api/v1/components/${componentId}/instances/${instanceId}/export` });
    } catch (_) {
        _toast('Export failed', 'error');
    }
}

// ── core: preflight → remap dialog → commit, against ANY endpoint ──────────────
// The single materialization path. Reused by file import (the generic instance
// import endpoint, which needs the payload in the body) AND marketplace fork (the
// marketplace install endpoint, which already holds the envelope server-side, so
// `payload` is omitted). `verb` labels the action ("Import" / "Fork"). Both
// endpoints share the two-phase contract: {…, dry_run:true} → preflight report;
// {…, mode:'create', mapping, dry_run:false} → commit.
export async function openRemapDialog({ meta, endpoint, payload = null, verb = 'Import', onDone }) {
    let pre;
    try {
        const r = await fetch(endpoint, {
            method: 'POST',
            headers: { ..._authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify(payload ? { payload, dry_run: true } : { dry_run: true }),
        });
        pre = await r.json();
        if (!r.ok || pre.status === 'error') throw new Error(pre.message || 'Preflight failed');
    } catch (e) {
        _toast(e.message || 'Could not prepare import', 'error');
        return;
    }
    _renderDialog({ meta, endpoint, payload, pre, verb, onDone });
}

// ── public: file import (pick → validate → dialog via the generic endpoint) ─────
export function importInstance(meta, opts = {}) {
    const componentId = meta.component_id;
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json,application/json';
    input.onchange = async () => {
        const file = input.files && input.files[0];
        if (!file) return;
        let payload;
        try { payload = JSON.parse(await file.text()); }
        catch { _toast('That file is not valid JSON', 'error'); return; }
        if (!payload || payload.component_id !== componentId) {
            _toast(`This file is not a ${meta.display_name || componentId} export`, 'error');
            return;
        }
        openRemapDialog({
            meta,
            endpoint: `/api/v1/components/${componentId}/instances/import`,
            payload,
            verb: 'Import',
            onDone: opts.onDone,
        });
    };
    input.click();
}

// ── the remap dialog ───────────────────────────────────────────────────────────
// Renders the preflight report so the user resolves every cross-reference (profile
// tag → their real profile) before committing. Auto-matched refs are pre-selected;
// unmatched refs default to the default profile and are flagged "choose".
function _renderDialog({ meta, endpoint, payload, pre, verb = 'Import', onDone }) {
    _injectCSS();
    const componentId = meta.component_id;
    const accent = meta.icon_accent || 'var(--teradata-orange)';
    const icon = meta.icon_svg || _FALLBACK_ICON;
    const profiles = Array.isArray(pre.profiles) ? pre.profiles : [];
    const candidates = Array.isArray(pre.candidates) ? pre.candidates : [];
    const tools = Array.isArray(pre.tools) ? pre.tools : [];
    const summaryText = pre.summary_text || `${(pre.summary || {}).nodes || 0} items`;
    const needsDecision = profiles.filter(p => p.status !== 'auto').length;

    const candOptions = (selId) => candidates.map(c =>
        `<option value="${_esc(c.id)}"${c.id === selId ? ' selected' : ''}>@${_esc(c.tag)}${c.name && c.name !== c.tag ? ' · ' + _esc(c.name) : ''}</option>`
    ).join('');

    const profileRows = profiles.length ? profiles.map((p) => {
        const col = p.color || _ifocColor(p.profile_type);
        const auto = p.status === 'auto';
        return `
        <div class="ip-map-row" data-ref="${_esc(p.ref_tag)}">
            <span class="ip-map-src" style="--pc:${_esc(col)}">@${_esc(p.ref_tag)}</span>
            <span class="ip-map-arrow">${_ICON_ARROW}</span>
            <select class="ip-map-select">
                <option value="">▸ Inherit default profile</option>
                ${candOptions(p.suggested_id)}
            </select>
            <span class="ip-map-status ${auto ? 'is-auto' : 'is-manual'}">${auto ? 'matched' : 'choose'}</span>
        </div>`;
    }).join('') : `<div class="ip-map-none">No profile references — nothing to remap.</div>`;

    const toolsBlock = tools.length ? `
        <div class="ip-sec-title">Referenced tools <span class="ip-hint">must exist on the mapped profile</span></div>
        <div class="ip-tools">${tools.map(t => `<span class="ip-chip">${_esc(t)}</span>`).join('')}</div>` : '';

    const overlay = document.createElement('div');
    overlay.className = 'ip-modal-overlay';
    overlay.innerHTML = `
      <div class="ip-modal" role="dialog" aria-modal="true">
        <div class="ip-modal-head">
          <span class="ip-modal-icon" style="color:${_esc(accent)}">${icon}</span>
          <div class="ip-modal-titles">
            <div class="ip-modal-title">${_esc(verb)} ${_esc(meta.display_name || componentId)}</div>
            <div class="ip-modal-sub">${_esc(summaryText)}${needsDecision ? ` · ${needsDecision} to map` : ''}</div>
          </div>
          <button class="ip-modal-x" aria-label="Close">&times;</button>
        </div>
        <div class="ip-modal-body">
          <label class="ip-field">
            <span class="ip-label">Name</span>
            <input class="ip-name" type="text" value="${_esc(pre.name || 'Imported')}" />
          </label>
          <div class="ip-sec-title">Profile mapping</div>
          <div class="ip-map-list">${profileRows}</div>
          ${toolsBlock}
        </div>
        <div class="ip-modal-foot">
          <button class="ip-btn ip-cancel">Cancel</button>
          <button class="ip-btn ip-primary">${_esc(verb)}</button>
        </div>
      </div>`;

    document.body.appendChild(overlay);
    const onEsc = (e) => { if (e.key === 'Escape') close(); };
    const close = () => { overlay.remove(); document.removeEventListener('keydown', onEsc); };
    overlay.addEventListener('mousedown', (e) => { if (e.target === overlay) close(); });
    overlay.querySelector('.ip-modal-x').addEventListener('click', close);
    overlay.querySelector('.ip-cancel').addEventListener('click', close);
    document.addEventListener('keydown', onEsc);
    setTimeout(() => overlay.querySelector('.ip-name')?.focus(), 30);

    const primary = overlay.querySelector('.ip-primary');
    primary.addEventListener('click', async () => {
        const name = (overlay.querySelector('.ip-name').value || '').trim() || pre.name || 'Imported';
        const mapping = { profiles: {} };
        overlay.querySelectorAll('.ip-map-row').forEach(rowEl => {
            mapping.profiles[rowEl.getAttribute('data-ref')] = rowEl.querySelector('.ip-map-select').value || null;
        });
        const ING = { Import: 'Importing', Fork: 'Forking', Subscribe: 'Subscribing' };
        const PAST = { Import: 'Imported', Fork: 'Forked', Subscribe: 'Subscribed' };
        primary.disabled = true;
        primary.textContent = `${ING[verb] || verb}…`;
        try {
            // File import sends the payload; marketplace fork omits it (server has it).
            const body = payload
                ? { payload: { ...payload, name }, mode: 'create', mapping, dry_run: false }
                : { name, mode: 'create', mapping, dry_run: false };
            const r = await fetch(endpoint, {
                method: 'POST',
                headers: { ..._authHeaders(), 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const res = await r.json();
            if (!r.ok || res.status === 'error') throw new Error(res.message || `${verb} failed`);
            close();
            const warn = (res.warnings && res.warnings.length) ? ` · ${res.warnings.length} note${res.warnings.length > 1 ? 's' : ''}` : '';
            _toast(`${PAST[verb] || verb} “${res.name}”${warn}`, 'success');
            if (typeof onDone === 'function') onDone(res);
        } catch (e) {
            primary.disabled = false;
            primary.textContent = verb;
            _toast(e.message || `${verb} failed`, 'error');
        }
    });
}

// Exposed so other surfaces (e.g. the marketplace publish modal) can reuse the
// shared `.ip-modal*` styling without duplicating CSS.
export function ensureDialogStyles() { _injectCSS(); }

// ── theme-aware dialog styles (injected once) ──────────────────────────────────
function _injectCSS() {
    if (document.getElementById('instance-portability-styles')) return;
    const css = `
.ip-modal-overlay {
    position: fixed; inset: 0; z-index: 10000; display: flex; align-items: center; justify-content: center;
    background: rgba(0,0,0,0.55); backdrop-filter: blur(2px); padding: 20px;
}
.ip-modal {
    width: 460px; max-width: 94vw; max-height: 88vh; display: flex; flex-direction: column;
    background: var(--card-bg, #1e293b); border: 1px solid var(--border-primary, rgba(148,163,184,0.25));
    border-radius: 12px; box-shadow: 0 16px 48px rgba(0,0,0,0.45); overflow: hidden;
}
.ip-modal-head { display: flex; align-items: center; gap: 10px; padding: 14px 16px; border-bottom: 1px solid var(--border-secondary, rgba(148,163,184,0.12)); }
.ip-modal-icon { display: inline-flex; width: 22px; height: 22px; flex-shrink: 0; }
.ip-modal-icon svg { width: 22px; height: 22px; }
.ip-modal-titles { flex: 1; min-width: 0; }
.ip-modal-title { font-size: 14px; font-weight: 700; color: var(--text-primary); }
.ip-modal-sub { font-size: 11px; color: var(--text-muted); margin-top: 1px; }
.ip-modal-x { background: transparent; border: none; color: var(--text-muted); font-size: 22px; line-height: 1; cursor: pointer; padding: 0 4px; border-radius: 5px; }
.ip-modal-x:hover { color: var(--text-primary); }
.ip-modal-body { padding: 14px 16px; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; }
.ip-field { display: flex; flex-direction: column; gap: 5px; }
.ip-label, .ip-sec-title { font-size: 10px; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase; color: var(--text-muted); }
.ip-sec-title { margin-top: 2px; }
.ip-hint { font-weight: 500; text-transform: none; letter-spacing: 0; margin-left: 6px; opacity: 0.8; }
.ip-name {
    width: 100%; box-sizing: border-box; padding: 8px 10px; font-size: 13px;
    background: var(--bg-primary, rgba(15,23,42,0.6)); color: var(--text-primary);
    border: 1px solid var(--border-primary, rgba(148,163,184,0.25)); border-radius: 7px;
}
.ip-name:focus { outline: none; border-color: var(--teradata-orange); }
.ip-map-list { display: flex; flex-direction: column; gap: 7px; }
.ip-map-none { font-size: 12px; color: var(--text-muted); padding: 4px 2px; }
.ip-map-row { display: flex; align-items: center; gap: 8px; }
.ip-map-src {
    flex-shrink: 0; min-width: 78px; max-width: 120px; padding: 4px 9px; border-radius: 6px;
    font-size: 12px; font-weight: 700; color: var(--pc, var(--text-primary));
    background: color-mix(in srgb, var(--pc, #888) 14%, transparent);
    border: 1px solid color-mix(in srgb, var(--pc, #888) 35%, transparent);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.ip-map-arrow { display: inline-flex; width: 16px; height: 16px; color: var(--text-muted); flex-shrink: 0; }
.ip-map-arrow svg { width: 16px; height: 16px; }
.ip-map-select {
    flex: 1; min-width: 0; padding: 7px 9px; font-size: 12px; border-radius: 7px;
    background: var(--bg-primary, rgba(15,23,42,0.6)); color: var(--text-primary);
    border: 1px solid var(--border-primary, rgba(148,163,184,0.25)); cursor: pointer;
}
.ip-map-select:focus { outline: none; border-color: var(--teradata-orange); }
.ip-map-status { flex-shrink: 0; font-size: 9px; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; padding: 2px 6px; border-radius: 5px; }
.ip-map-status.is-auto { color: #4ade80; background: rgba(74,222,128,0.12); }
.ip-map-status.is-manual { color: #fbbf24; background: rgba(251,191,36,0.14); }
.ip-tools { display: flex; flex-wrap: wrap; gap: 5px; }
.ip-chip {
    font-size: 11px; font-family: ui-monospace, monospace; padding: 2px 8px; border-radius: 5px;
    color: var(--text-muted); background: var(--hover-bg, rgba(148,163,184,0.1));
    border: 1px solid var(--border-secondary, rgba(148,163,184,0.15));
}
.ip-modal-foot { display: flex; justify-content: flex-end; gap: 8px; padding: 12px 16px; border-top: 1px solid var(--border-secondary, rgba(148,163,184,0.12)); }
.ip-btn { padding: 8px 16px; font-size: 12px; font-weight: 600; border-radius: 7px; cursor: pointer; border: 1px solid transparent; transition: background 0.1s ease, opacity 0.1s ease; }
.ip-btn.ip-cancel { background: transparent; color: var(--text-muted); border-color: var(--border-primary, rgba(148,163,184,0.25)); }
.ip-btn.ip-cancel:hover { color: var(--text-primary); background: var(--hover-bg, rgba(148,163,184,0.08)); }
.ip-btn.ip-primary { background: var(--teradata-orange); color: #fff; }
.ip-btn.ip-primary:hover { opacity: 0.9; }
.ip-btn.ip-primary:disabled { opacity: 0.6; cursor: default; }
`;
    const style = document.createElement('style');
    style.id = 'instance-portability-styles';
    style.textContent = css;
    document.head.appendChild(style);
}
