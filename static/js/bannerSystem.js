/**
 * Centralized Banner System for TDA
 * 
 * Two distinct banner areas:
 * 1. Application-level banner (top navigation bar)
 * 2. Conversation-level banner (inside conversation pane)
 */

/**
 * Show application-level banner message
 * Location: Top navigation bar (next to "Uderia Platform" title)
 * 
 * Use for:
 * - Global configuration changes
 * - Authentication/authorization messages
 * - RAG operations (create/delete collections)
 * - Admin operations
 * - Credential management
 * - Access token management
 * - MCP server operations
 * 
 * @param {string} message - Message to display
 * @param {string} type - 'success', 'error', 'warning', or 'info'
 * @param {number} duration - Display duration in ms (default: 5000)
 */
// Reusable floating toast element (created once, reused)
let _toastEl = null;
let _toastTimeout = null;

export function showAppBanner(message, type = 'info', duration = 5000) {
    // Theme-aware colors using Tailwind classes that work with all themes
    const colors = {
        success: 'app-banner-success',
        error: 'app-banner-error',
        warning: 'app-banner-warning',
        info: 'app-banner-info'
    };

    // Detect if any modal overlay is currently visible
    // Matches: .tpl-modal-overlay (dynamic), *-modal-overlay (static), *-modal.fixed (e.g. profile-modal)
    const modalOpen = document.querySelector(
        '.tpl-modal-overlay, [id$="-modal-overlay"]:not(.hidden), [id$="-modal"].fixed:not(.hidden)'
    );

    if (modalOpen) {
        // ── Floating toast mode (above modals) ──
        _showToast(message, colors[type] || colors.info, duration);
    } else {
        // ── Inline navbar mode (original behavior) ──
        _dismissToast();   // clean up any lingering toast
        const statusElement = document.getElementById('header-status-message');
        if (!statusElement) {
            console.warn('[AppBanner] Element not found. Message:', message);
            return;
        }

        if (statusElement.hideTimeout) {
            clearTimeout(statusElement.hideTimeout);
        }

        statusElement.textContent = message;
        statusElement.className = `text-sm px-3 py-1 rounded-md transition-all duration-300 ${colors[type] || colors.info}`;
        statusElement.style.opacity = '1';

        statusElement.hideTimeout = setTimeout(() => {
            statusElement.style.opacity = '0';
            setTimeout(() => { statusElement.textContent = ''; }, 300);
        }, duration);
    }
}

function _showToast(message, colorClass, duration) {
    // Clear previous toast timeout
    if (_toastTimeout) { clearTimeout(_toastTimeout); _toastTimeout = null; }

    // Create or reuse toast element
    if (!_toastEl) {
        _toastEl = document.createElement('div');
        document.body.appendChild(_toastEl);
    }

    _toastEl.textContent = message;
    _toastEl.className = `app-banner-toast ${colorClass}`;

    // Trigger enter animation. Guard against _toastEl being nulled by a concurrent
    // _dismissToast() before this deferred callback runs — rAF can be throttled/paused
    // (backgrounded tab, heavy layout) long enough for the 350ms dismiss cleanup to fire first.
    requestAnimationFrame(() => { if (_toastEl) _toastEl.classList.add('visible'); });

    // Auto-hide
    _toastTimeout = setTimeout(() => {
        _dismissToast();
    }, duration);
}

function _dismissToast() {
    if (_toastTimeout) { clearTimeout(_toastTimeout); _toastTimeout = null; }
    if (_toastEl) {
        _toastEl.classList.remove('visible');
        // Remove from DOM after fade-out
        setTimeout(() => {
            if (_toastEl) { _toastEl.remove(); _toastEl = null; }
        }, 350);
    }
}

/**
 * Show conversation-level banner message
 * Location: Inside the conversation pane (below profile selector)
 * 
 * Use for:
 * - Profile override warnings/errors
 * - Conversation-specific errors
 * - Query submission issues
 * - Context/history warnings
 * - Session-specific messages
 * 
 * @param {string} message - Message to display
 * @param {string} type - 'success', 'error', 'warning', or 'info'
 * @param {boolean} dismissible - Show close button (default: true)
 */
export function showConversationBanner(message, type = 'warning', dismissible = true) {
    const banner = document.getElementById('profile-override-warning-banner');
    const messageElement = document.getElementById('profile-override-warning-message');
    const dismissButton = document.getElementById('dismiss-profile-warning');
    
    if (!banner || !messageElement) {
        console.warn('[ConversationBanner] Elements not found. Message:', message);
        return;
    }
    
    // Style configurations
    const styles = {
        success: {
            bg: 'bg-green-500/20',
            border: 'border-green-400/50',
            icon: 'text-green-500',
            text: 'text-green-100'
        },
        error: {
            bg: 'bg-red-500/20',
            border: 'border-red-400/50',
            icon: 'text-red-500',
            text: 'text-red-100'
        },
        warning: {
            bg: 'bg-yellow-500/20',
            border: 'border-yellow-400/50',
            icon: 'text-yellow-500',
            text: 'text-yellow-100'
        },
        info: {
            bg: 'bg-blue-500/20',
            border: 'border-blue-400/50',
            icon: 'text-blue-500',
            text: 'text-blue-100'
        }
    };
    
    const style = styles[type] || styles.warning;
    
    // Update styling
    banner.className = `flex items-center gap-x-2 px-3 py-1 rounded-md ${style.bg} border ${style.border}`;
    
    // Update icon color
    const icon = banner.querySelector('svg');
    if (icon) {
        icon.className = `h-4 w-4 ${style.icon} flex-shrink-0`;
    }
    
    // Update message
    messageElement.textContent = message;
    messageElement.className = `text-xs ${style.text} font-medium`;
    
    // Show/hide dismiss button
    if (dismissButton) {
        dismissButton.style.display = dismissible ? 'block' : 'none';
    }
    
    // Show banner
    banner.classList.remove('hidden');
}

/**
 * Hide conversation-level banner
 */
export function hideConversationBanner() {
    const banner = document.getElementById('profile-override-warning-banner');
    if (banner) {
        banner.classList.add('hidden');
    }
}

/**
 * Hide application-level banner
 */
export function hideAppBanner() {
    const statusElement = document.getElementById('header-status-message');
    if (statusElement) {
        if (statusElement.hideTimeout) {
            clearTimeout(statusElement.hideTimeout);
        }
        statusElement.style.opacity = '0';
        setTimeout(() => {
            statusElement.textContent = '';
        }, 300);
    }
}

// ─── Maintenance Banner ───────────────────────────────────────────────────────

let _maintenanceInterval = null;

/**
 * Show a persistent full-width maintenance/restart notification banner.
 * Placed below the top nav; stays until dismissed by the user or cancelled by admin.
 *
 * @param {string} message         - Notification message text
 * @param {number} countdownMinutes - Minutes until restart (0 = no countdown)
 * @param {number} downtimeMinutes  - Estimated downtime in minutes (0 = unknown)
 */
export function showMaintenanceBanner(message, countdownMinutes = 0, downtimeMinutes = 0) {
    const banner = document.getElementById('maintenance-banner');
    if (!banner) return;

    // Clear any running countdown
    if (_maintenanceInterval) {
        clearInterval(_maintenanceInterval);
        _maintenanceInterval = null;
    }

    const textEl = document.getElementById('maintenance-banner-text');
    const countdownEl = document.getElementById('maintenance-banner-countdown');
    const downtimeEl = document.getElementById('maintenance-banner-downtime');

    if (textEl) textEl.textContent = message;

    // Downtime segment
    if (downtimeEl) {
        if (downtimeMinutes > 0) {
            downtimeEl.textContent = `Estimated downtime: ~${downtimeMinutes} min`;
            downtimeEl.classList.remove('hidden');
        } else {
            downtimeEl.classList.add('hidden');
        }
    }

    // Countdown segment
    if (countdownEl) {
        if (countdownMinutes > 0) {
            let remaining = Math.round(countdownMinutes * 60); // seconds (integer)

            const tick = () => {
                if (remaining <= 0) {
                    clearInterval(_maintenanceInterval);
                    _maintenanceInterval = null;
                    countdownEl.textContent = '';
                    countdownEl.classList.add('hidden');
                    if (textEl) textEl.textContent = 'System restart imminent — please finish your current work.';
                    return;
                }
                const m = Math.floor(remaining / 60);
                const s = remaining % 60;
                const mm = String(m).padStart(2, '0');
                const ss = String(s).padStart(2, '0');
                countdownEl.textContent = `Restarting in ${mm}:${ss}`;
                remaining--;
            };

            tick(); // Show immediately without waiting 1s
            _maintenanceInterval = setInterval(tick, 1000);
            countdownEl.classList.remove('hidden');
        } else {
            countdownEl.textContent = '';
            countdownEl.classList.add('hidden');
        }
    }

    banner.classList.remove('hidden');
}

/**
 * Hide the maintenance banner and stop any running countdown.
 */
export function hideMaintenanceBanner() {
    if (_maintenanceInterval) {
        clearInterval(_maintenanceInterval);
        _maintenanceInterval = null;
    }
    const banner = document.getElementById('maintenance-banner');
    if (banner) banner.classList.add('hidden');

    const countdownEl = document.getElementById('maintenance-banner-countdown');
    if (countdownEl) { countdownEl.textContent = ''; countdownEl.classList.add('hidden'); }

    const textEl = document.getElementById('maintenance-banner-text');
    if (textEl) textEl.textContent = '';

    const downtimeEl = document.getElementById('maintenance-banner-downtime');
    if (downtimeEl) downtimeEl.classList.add('hidden');
}

/**
 * Show a destructive-confirmation banner in the top navigation.
 *
 * Use for: deleting collections/profiles/skills/extensions/KGs, unpublishing
 * artifacts, removing connections — anything where the user must say yes/no.
 * Theme-compliant via the .app-banner-confirm CSS class.
 *
 * @param {string} message - The question shown to the user
 * @param {Function} onConfirm - Async or sync callback fired when user clicks OK
 * @param {Object} [options] - { confirmText, cancelText }
 */
export function showConfirmBanner(message, onConfirm, options = {}) {
    const banner = document.getElementById('delete-confirmation-banner');
    const messageEl = document.getElementById('delete-confirmation-message');
    const cancelBtn = document.getElementById('delete-confirmation-cancel');
    const okBtn = document.getElementById('delete-confirmation-ok');

    if (!banner || !messageEl || !cancelBtn || !okBtn) {
        console.warn('[ConfirmBanner] Elements not found. Falling back to OS dialog.');
        // Last-resort fallback so callers never silently no-op
        if (window.confirm(message) && onConfirm) onConfirm();
        return;
    }

    messageEl.textContent = message;
    if (options.confirmText) okBtn.textContent = options.confirmText;
    if (options.cancelText)  cancelBtn.textContent = options.cancelText;

    banner.classList.remove('hidden');

    // Replace listeners by cloning to avoid stacking from prior calls
    const newCancelBtn = cancelBtn.cloneNode(true);
    const newOkBtn = okBtn.cloneNode(true);
    cancelBtn.parentNode.replaceChild(newCancelBtn, cancelBtn);
    okBtn.parentNode.replaceChild(newOkBtn, okBtn);

    const hide = () => {
        banner.classList.add('hidden');
        // Reset button labels for the next use
        newOkBtn.textContent = 'OK';
        newCancelBtn.textContent = 'Cancel';
    };

    newCancelBtn.addEventListener('click', hide);
    newOkBtn.addEventListener('click', async () => {
        hide();
        if (onConfirm) await onConfirm();
    });
}

// Make functions available globally for backwards compatibility
window.showAppBanner = showAppBanner;
window.showConversationBanner = showConversationBanner;
window.hideConversationBanner = hideConversationBanner;
window.hideAppBanner = hideAppBanner;
window.showMaintenanceBanner = showMaintenanceBanner;
window.hideMaintenanceBanner = hideMaintenanceBanner;
window.showConfirmBanner = showConfirmBanner;

/**
 * Themed PROMPT modal — the platform's missing dialog primitive.
 *
 * There were confirm and notify primitives (`showConfirmBanner`, `showAppBanner`) but
 * nothing that COLLECTS text, so the one place that needs a written reason reached for
 * `window.prompt()`. That dialog cannot be themed, cannot validate, renders its chrome in
 * the OS locale (a German "Abbrechen" beside English body copy), and gives the value no
 * weight — on a gate whose text is frozen into a cryptographic signature. Adding the
 * primitive fixes the class rather than the instance: never call `window.prompt` again.
 *
 * Deliberately a MODAL, not a banner: this collects an audited judgement, so it earns
 * focus and a backdrop, unlike the slim confirm strip.
 *
 * @returns {Promise<string|null>} the trimmed text, or null if dismissed.
 */
export function showPromptModal(opts = {}) {
    const {
        title = 'Enter a value', body = '', label = '', placeholder = '',
        hint = '', confirmText = 'Save', cancelText = 'Cancel',
        required = true, multiline = true, context = [],
    } = opts;
    const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

    return new Promise((resolve) => {
        const prev = document.activeElement;
        const back = document.createElement('div');
        back.className = 'u-prompt-backdrop';
        back.innerHTML = `
            <div class="u-prompt" role="dialog" aria-modal="true" aria-labelledby="u-prompt-title">
                <h3 class="u-prompt-title" id="u-prompt-title">${esc(title)}</h3>
                ${context.length ? `<div class="u-prompt-ctx">${context.map(
                    (c) => `<div><span class="u-prompt-ctx-k">${esc(c.k)}</span><span class="u-prompt-ctx-v">${esc(c.v)}</span></div>`
                ).join('')}</div>` : ''}
                ${body ? `<p class="u-prompt-body">${esc(body)}</p>` : ''}
                ${label ? `<label class="u-prompt-label" for="u-prompt-input">${esc(label)}</label>` : ''}
                ${multiline
                    ? `<textarea id="u-prompt-input" class="u-prompt-input" rows="3" placeholder="${esc(placeholder)}"></textarea>`
                    : `<input id="u-prompt-input" type="text" class="u-prompt-input" placeholder="${esc(placeholder)}" />`}
                ${hint ? `<p class="u-prompt-hint">${esc(hint)}</p>` : ''}
                <div class="u-prompt-actions">
                    <button type="button" class="card-btn card-btn--sm card-btn--neutral" data-up="cancel">${esc(cancelText)}</button>
                    <button type="button" class="card-btn card-btn--sm card-btn--primary" data-up="ok"${required ? ' disabled' : ''}>${esc(confirmText)}</button>
                </div>
            </div>`;
        document.body.appendChild(back);
        const input = back.querySelector('#u-prompt-input');
        const ok = back.querySelector('[data-up="ok"]');

        const done = (val) => {
            document.removeEventListener('keydown', onKey, true);
            back.remove();
            if (prev && typeof prev.focus === 'function') prev.focus();
            resolve(val);
        };
        // Required means required: the button cannot submit an empty judgement, rather
        // than the caller silently discarding one afterwards.
        const sync = () => { if (required && ok) ok.disabled = !input.value.trim(); };
        const onKey = (ev) => {
            if (ev.key === 'Escape') { ev.stopPropagation(); done(null); }
            // Enter submits a single-line prompt; a textarea keeps Enter for newlines and
            // takes Cmd/Ctrl+Enter instead.
            if (ev.key === 'Enter' && (!multiline || ev.metaKey || ev.ctrlKey)) {
                if (!ok || !ok.disabled) { ev.preventDefault(); done(input.value.trim()); }
            }
        };
        input.addEventListener('input', sync);
        back.querySelector('[data-up="cancel"]').addEventListener('click', () => done(null));
        ok.addEventListener('click', () => { if (!ok.disabled) done(input.value.trim()); });
        back.addEventListener('mousedown', (ev) => { if (ev.target === back) done(null); });
        document.addEventListener('keydown', onKey, true);
        sync();
        setTimeout(() => input.focus(), 0);
    });
}
window.showPromptModal = showPromptModal;
