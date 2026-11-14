/**
 * Component Gate — the ONE frontend answer to "is this component enabled for this profile?"
 *
 * Root cause this closes: conversation affordances derived enablement from *renderer
 * availability* (`hasRenderer`) or from nothing at all, so the Pin-to-Dashboard button
 * appeared on every assistant table for every profile — including profiles with the
 * Dashboard component switched off. See
 * docs/assessments/COMPONENT_ENABLEMENT_ASSESSMENT_2026-08.md.
 *
 * This module NEVER re-derives the rule. It reads the server's already-resolved
 * `profile_enabled` from GET /v1/profiles/<id>/components — the same field the Workspace
 * and Components panels use — so the frontend cannot drift from the backend.
 *
 * Fails CLOSED. An affordance is a claim that a capability exists; making that claim on a
 * failed lookup is exactly the defect being fixed here. The REST layer is the control;
 * this is the honesty of the UI.
 *
 * Plain script (IIFE) so both ES modules (`window.ComponentGate`) and non-module handlers
 * can use it — same pattern as window.renderProfileTag / window.ProfileColors.
 */
(function () {
  'use strict';

  /** profileId -> Promise<Map<componentId, boolean>> */
  const _inflight = new Map();
  /** profileId -> Map<componentId, boolean> */
  const _cache = new Map();

  function _token() {
    try { return localStorage.getItem('tda_auth_token'); } catch { return null; }
  }

  function _load(profileId) {
    if (_cache.has(profileId)) return Promise.resolve(_cache.get(profileId));
    if (_inflight.has(profileId)) return _inflight.get(profileId);

    const tok = _token();
    const p = fetch(`/api/v1/profiles/${encodeURIComponent(profileId)}/components`, {
      headers: tok ? { Authorization: `Bearer ${tok}` } : {},
    })
      .then(r => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(data => {
        const map = new Map();
        for (const c of (data.components || [])) {
          map.set(c.component_id, c.profile_enabled === true);
        }
        _cache.set(profileId, map);   // only a SUCCESSFUL lookup is cached
        return map;
      })
      .catch(err => {
        console.warn('[ComponentGate] enablement lookup failed (failing closed):', err);
        return new Map();             // not cached — retryable
      })
      .finally(() => { _inflight.delete(profileId); });

    _inflight.set(profileId, p);
    return p;
  }

  const ComponentGate = {
    /** Promise<boolean>. Unknown profile or failed lookup -> false (fail closed). */
    isEnabled(componentId, profileId) {
      if (!componentId || !profileId) return Promise.resolve(false);
      return _load(profileId).then(m => m.get(componentId) === true);
    },

    /** Sync, cache-only: true | false | undefined (= not resolved yet). */
    peek(componentId, profileId) {
      const m = _cache.get(profileId);
      return m ? m.get(componentId) === true : undefined;
    },

    /** Warm the cache for a profile (called on profile switch). */
    prime(profileId) {
      return profileId ? _load(profileId).then(() => undefined) : Promise.resolve();
    },

    /** Drop cached answers — call after a profile save changes componentConfig. */
    invalidate(profileId) {
      if (profileId) { _cache.delete(profileId); _inflight.delete(profileId); }
      else { _cache.clear(); _inflight.clear(); }
    },
  };

  window.ComponentGate = ComponentGate;
})();
