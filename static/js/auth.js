/**
 * Authentication Client for Uderia Platform
 * 
 * Handles user authentication, token management, and session persistence.
 * Provides methods for login, logout, registration, and token refresh.
 */

class AuthClient {
    constructor() {
        this.baseUrl = '/api/v1/auth';
        this.tokenKey = 'tda_auth_token';
        this.userKey = 'tda_user';
        this.tokenExpiryKey = 'tda_token_expiry';
        
        // Start auto-refresh timer if authenticated
        if (this.isAuthenticated()) {
            this.startAutoRefresh();
        }
    }

    /**
     * Check if user is currently authenticated
     * @returns {boolean}
     */
    isAuthenticated() {
        const token = this.getToken();
        if (!token) {
            return false;
        }
        
        // Check if token is expired
        const expiry = localStorage.getItem(this.tokenExpiryKey);
        if (expiry && new Date(expiry) < new Date()) {
            console.log('Token expired, clearing session');
            this.clearSession();
            return false;
        }
        
        return true;
    }

    /**
     * Get stored authentication token
     * @returns {string|null}
     */
    getToken() {
        return localStorage.getItem(this.tokenKey);
    }

    /**
     * Get stored user information
     * @returns {object|null}
     */
    getUser() {
        const userJson = localStorage.getItem(this.userKey);
        return userJson ? JSON.parse(userJson) : null;
    }

    /**
     * Store authentication token and user info
     * @param {string} token - JWT token
     * @param {object} user - User object
     */
    setSession(token, user) {
        localStorage.setItem(this.tokenKey, token);
        localStorage.setItem(this.userKey, JSON.stringify(user));
        
        // Calculate expiry (24 hours from now by default)
        const expiry = new Date();
        expiry.setHours(expiry.getHours() + 24);
        localStorage.setItem(this.tokenExpiryKey, expiry.toISOString());
        
        console.log('Session stored:', { username: user.username, expiry: expiry.toISOString() });
        
        // Start auto-refresh
        this.startAutoRefresh();
    }

    /**
     * Store authentication token from OAuth callback
     * Decodes JWT to extract user info and sets session
     * @param {string} token - JWT token from OAuth callback
     */
    setToken(token) {
        if (!token) {
            console.error('Invalid token');
            return false;
        }

        try {
            // Decode JWT payload (without verification - backend verifies signature)
            const parts = token.split('.');
            if (parts.length !== 3) {
                throw new Error('Invalid token format');
            }

            // Decode payload
            const payload = JSON.parse(atob(parts[1]));
            
            // Extract user info from token
            const user = {
                id: payload.user_id,
                username: payload.username,
                email: payload.email || '',
            };

            // Store session
            this.setSession(token, user);
            
            console.log('Token set from OAuth callback:', { username: user.username });
            return true;
        } catch (error) {
            console.error('Error processing OAuth token:', error);
            return false;
        }
    }

    /**
     * Clear stored session data
     */
    clearSession() {
        localStorage.removeItem(this.tokenKey);
        localStorage.removeItem(this.userKey);
        localStorage.removeItem(this.tokenExpiryKey);
        
        // Stop auto-refresh
        if (this.refreshTimer) {
            clearInterval(this.refreshTimer);
            this.refreshTimer = null;
        }
        
        console.log('Session cleared');
    }

    /**
     * Register a new user
     * @param {string} username 
     * @param {string} email 
     * @param {string} password 
     * @param {string} displayName - Optional display name
     * @returns {Promise<{success: boolean, message: string, user?: object, errors?: array}>}
     */
    async register(username, email, password, displayName = '') {
        try {
            const response = await fetch(`${this.baseUrl}/register`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    username,
                    email,
                    password,
                    display_name: displayName
                })
            });

            const data = await response.json();

            if (response.ok && data.status === 'success') {
                return {
                    success: true,
                    message: data.message,
                    user: data.user
                };
            } else {
                return {
                    success: false,
                    message: data.message || 'Registration failed',
                    errors: data.errors || []
                };
            }
        } catch (error) {
            console.error('Registration error:', error);
            return {
                success: false,
                message: 'Network error. Please check your connection.',
                errors: []
            };
        }
    }

    /**
     * Login user
     * @param {string} username 
     * @param {string} password 
     * @returns {Promise<{success: boolean, message: string, user?: object, token?: string}>}
     */
    async login(username, password) {
        try {
            const response = await fetch(`${this.baseUrl}/login`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    username,
                    password
                })
            });

            const data = await response.json();

            if (response.ok && data.status === 'success') {
                // Store session
                this.setSession(data.token, data.user);

                return {
                    success: true,
                    message: data.message,
                    user: data.user,
                    token: data.token
                };
            } else {
                return {
                    success: false,
                    message: data.message || 'Login failed',
                    requires_email_verification: data.requires_email_verification || false
                };
            }
        } catch (error) {
            console.error('Login error:', error);
            return {
                success: false,
                message: 'Network error. Please check your connection.'
            };
        }
    }

    /**
     * Logout user
     * @returns {Promise<{success: boolean, message: string}>}
     */
    async logout() {
        const token = this.getToken();
        
        if (!token) {
            this.clearSession();
            return {
                success: true,
                message: 'Already logged out'
            };
        }

        try {
            const response = await fetch(`${this.baseUrl}/logout`, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json',
                }
            });

            const data = await response.json();

            // Clear session regardless of response
            this.clearSession();

            if (response.ok && data.status === 'success') {
                return {
                    success: true,
                    message: data.message
                };
            } else {
                return {
                    success: true, // Still success since we cleared local session
                    message: 'Logged out locally'
                };
            }
        } catch (error) {
            console.error('Logout error:', error);
            // Clear session even on error
            this.clearSession();
            return {
                success: true,
                message: 'Logged out locally'
            };
        }
    }

    /**
     * Get current user profile
     * @returns {Promise<{success: boolean, user?: object, message?: string}>}
     */
    async getCurrentUser() {
        const token = this.getToken();
        
        if (!token) {
            return {
                success: false,
                message: 'Not authenticated'
            };
        }

        try {
            const response = await fetch(`${this.baseUrl}/me`, {
                method: 'GET',
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json',
                }
            });

            const data = await response.json();

            if (response.ok && data.status === 'success') {
                // Update stored user info
                localStorage.setItem(this.userKey, JSON.stringify(data.user));
                
                return {
                    success: true,
                    user: data.user
                };
            } else {
                // Token might be invalid
                if (response.status === 401) {
                    this.clearSession();
                }
                
                return {
                    success: false,
                    message: data.message || 'Failed to get user info'
                };
            }
        } catch (error) {
            console.error('Get current user error:', error);
            return {
                success: false,
                message: 'Network error'
            };
        }
    }

    /**
     * Refresh authentication token
     * @returns {Promise<{success: boolean, token?: string, message?: string}>}
     */
    async refreshToken() {
        const token = this.getToken();
        
        if (!token) {
            return {
                success: false,
                message: 'Not authenticated'
            };
        }

        try {
            const response = await fetch(`${this.baseUrl}/refresh`, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json',
                }
            });

            const data = await response.json();

            if (response.ok && data.status === 'success') {
                // Update token
                const user = this.getUser();
                this.setSession(data.token, user);
                
                console.log('Token refreshed successfully');
                
                return {
                    success: true,
                    token: data.token
                };
            } else {
                // Token refresh failed, clear session
                if (response.status === 401) {
                    this.clearSession();
                }
                
                return {
                    success: false,
                    message: data.message || 'Token refresh failed'
                };
            }
        } catch (error) {
            console.error('Token refresh error:', error);
            return {
                success: false,
                message: 'Network error'
            };
        }
    }

    /**
     * Change user password
     * @param {string} currentPassword 
     * @param {string} newPassword 
     * @returns {Promise<{success: boolean, message: string}>}
     */
    async changePassword(currentPassword, newPassword) {
        const token = this.getToken();
        
        if (!token) {
            return {
                success: false,
                message: 'Not authenticated'
            };
        }

        try {
            const response = await fetch(`${this.baseUrl}/change-password`, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    current_password: currentPassword,
                    new_password: newPassword
                })
            });

            const data = await response.json();

            if (response.ok && data.status === 'success') {
                return {
                    success: true,
                    message: data.message
                };
            } else {
                return {
                    success: false,
                    message: data.message || 'Password change failed',
                    errors: data.errors || []
                };
            }
        } catch (error) {
            console.error('Password change error:', error);
            return {
                success: false,
                message: 'Network error'
            };
        }
    }

    /**
     * Make authenticated API request
     * @param {string} url - API endpoint URL
     * @param {object} options - Fetch options
     * @returns {Promise<Response>}
     */
    async authenticatedFetch(url, options = {}) {
        const token = this.getToken();
        
        if (!token) {
            throw new Error('Not authenticated');
        }

        // Add authorization header
        const headers = {
            ...options.headers,
            'Authorization': `Bearer ${token}`,
            'Content-Type': 'application/json',
        };

        const response = await fetch(url, {
            ...options,
            headers
        });

        // Handle 401 Unauthorized
        if (response.status === 401) {
            console.warn('Authentication failed, clearing session');
            this.clearSession();
            // Optionally redirect to login
            if (window.location.pathname !== '/login') {
                window.location.href = '/login';
            }
        }

        return response;
    }

    /**
     * Start automatic token refresh timer
     * Refreshes token 5 minutes before expiry
     */
    startAutoRefresh() {
        // Clear existing timer
        if (this.refreshTimer) {
            clearInterval(this.refreshTimer);
        }

        // Check every 5 minutes
        this.refreshTimer = setInterval(async () => {
            const expiry = localStorage.getItem(this.tokenExpiryKey);
            if (!expiry) {
                return;
            }

            const expiryDate = new Date(expiry);
            const now = new Date();
            const minutesUntilExpiry = (expiryDate - now) / (1000 * 60);

            // Refresh if less than 10 minutes until expiry
            if (minutesUntilExpiry < 10 && minutesUntilExpiry > 0) {
                console.log('Auto-refreshing token...');
                const result = await this.refreshToken();
                
                if (!result.success) {
                    console.error('Auto-refresh failed:', result.message);
                }
            } else if (minutesUntilExpiry <= 0) {
                console.log('Token expired, clearing session');
                this.clearSession();
                
                // Redirect to login if not already there
                if (window.location.pathname !== '/login' && window.location.pathname !== '/register') {
                    window.location.href = '/login';
                }
            }
        }, 5 * 60 * 1000); // Check every 5 minutes
    }
}

// ── the session guard — ONE reaction to a dead session, for every caller ──────────
// Most handlers call fetch() directly with the stored token, so a 401 after the server
// revoked the session (idle limit, sign-out elsewhere, expiry) reached each widget on its
// own and each painted an empty state ("No scored runs yet.") while the user kept clicking.
// The server stamps `X-Session-State: invalid` on exactly those 401s (auth.middleware
// .session_invalid_marker); this guard reacts to the marker — never to a bare 401, which
// an endpoint may use for something else — clears the session once and sends the user to
// sign in, saying why. Installed once per page; the login/register pages never redirect.
(function installSessionGuard() {
    if (typeof window === 'undefined' || window.__uderiaSessionGuard) return;
    window.__uderiaSessionGuard = true;
    const _fetch = window.fetch.bind(window);
    let ended = false;
    function endSession() {
        if (ended) return;
        ended = true;
        try {
            if (window.authClient && typeof window.authClient.clearSession === 'function') window.authClient.clearSession();
            else { localStorage.removeItem('tda_auth_token'); localStorage.removeItem('tda_user'); localStorage.removeItem('tda_token_expiry'); }
        } catch (e) { /* signed out locally regardless */ }
        if (window.location.pathname !== '/login' && window.location.pathname !== '/register') {
            window.location.href = '/login?reason=session';
        }
    }
    // The same wrapper stamps this tab's id (UderiaBus.CLIENT_ID) on every same-origin request
    // as `X-Uderia-Client`, so a change notice the server raises for this write comes back
    // marked as this page's own (uderiaBus.js `own`) — an editor can tell its autosave's echo
    // from a write made elsewhere. Cross-origin requests are left untouched (a CORS preflight
    // would refuse the header); a Request object keeps its own headers plus the stamp.
    function _stampClient(input, init) {
        try {
            const id = window.UderiaBus && window.UderiaBus.CLIENT_ID;
            if (!id) return init;
            const url = typeof input === 'string' ? input : (input && typeof input.url === 'string' ? input.url : '');
            if (!(url.startsWith('/') || url.startsWith(window.location.origin + '/') || url === window.location.origin)) return init;
            const headers = new Headers((init && init.headers) || (typeof Request !== 'undefined' && input instanceof Request ? input.headers : undefined));
            if (headers.has('X-Uderia-Client')) return init;
            headers.set('X-Uderia-Client', id);
            return Object.assign({}, init || {}, { headers });
        } catch (e) { return init; }
    }
    // The acts of signing (sign-off, attestation, revocation) are the enterprise package's
    // `trust-signing` entitlement (core/entitlements). The server answers 404 without it; this
    // seam says why before the request leaves the page, once for every call site, and the
    // page keeps the act visible — the core shows trust and verifies signatures, it does not sign.
    const _SIGNING_ACT = /\/api\/v1\/trust\/.+\/(sign-off|attest-deployment)$|\/api\/v1\/trust\/.+\/signature$/;
    function _signingWithheld() {
        const l = window.UDERIA_LICENCE;
        return !!l && !l.all_groups && !(l.entitlements || []).includes('trust-signing');
    }
    window.signingWithheld = _signingWithheld;
    window.SIGNING_WITHHELD_MESSAGE = 'Signing and attestation are part of the enterprise package. This deployment shows trust and verifies signatures; it does not sign.';
    window.fetch = async function (input, init) {
        try {
            const url = typeof input === 'string' ? input : (input && typeof input.url === 'string' ? input.url : '');
            const method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
            if (_signingWithheld() && method !== 'GET' && _SIGNING_ACT.test(url.split('?')[0])) {
                if (window.showNotification) window.showNotification('info', window.SIGNING_WITHHELD_MESSAGE);
                return new Response(JSON.stringify({ status: 'error', error: 'not_licensed', message: window.SIGNING_WITHHELD_MESSAGE }),
                                    { status: 404, headers: { 'Content-Type': 'application/json' } });
            }
        } catch (e) { /* never let the seam break a request */ }
        const res = await _fetch(input, _stampClient(input, init));
        try {
            if (res && res.status === 401 && res.headers && res.headers.get('X-Session-State') === 'invalid'
                && localStorage.getItem('tda_auth_token')) {
                endSession();
            }
        } catch (e) { /* never let the guard break a response */ }
        return res;
    };
})();

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = AuthClient;
}
