/**
 * profileColors.js — Single source of truth for IFOC profile-class colours.
 *
 * Every place that presents a profile by name/tag should colour it by its
 * profile_type using these helpers, instead of redefining the colour map locally
 * (which historically drifted across ~5 handlers).
 *
 * Dual surface:
 *   - ES-module exports (for module handlers: `import { getProfileColor } from '../profileColors.js'`).
 *   - window.ProfileColors (for IIFE handlers and component renderers loaded via
 *     dynamic import, which use window.* globals).
 *
 * IFOC palette (mandatory, see CLAUDE.md):
 *   llm_only → Ideate (green) · rag_focused → Focus (blue)
 *   tool_enabled → Optimize (purple) · genie → Coordinate (orange)
 */

export const PROFILE_FALLBACK_COLOR = '#94a3b8'; // slate-400 — unknown / no type

export const IFOC_CONFIG = {
  llm_only:     { color: '#4ade80', label: 'Ideate',     order: 1 },
  rag_focused:  { color: '#3b82f6', label: 'Focus',      order: 2 },
  tool_enabled: { color: '#9333ea', label: 'Optimize',   order: 3 },
  genie:        { color: '#F15F22', label: 'Coordinate', order: 4 },
};

// Flat colour map (back-compat shape for consumers that want { type: hex }).
export const PROFILE_CLASS_COLORS = Object.fromEntries(
  Object.entries(IFOC_CONFIG).map(([k, v]) => [k, v.color])
);

export const IFOC_ORDER = Object.fromEntries(
  Object.entries(IFOC_CONFIG).map(([k, v]) => [k, v.order])
);

// Runtime engine labels that are not IFOC classes in their own right but resolve
// to one. The backend emits `conversation_with_tools` for an llm_only profile that
// has useMcpTools/component tools active — it is still the Ideate class, so its
// chip must stay green (not the unknown-type fallback grey).
export const TYPE_ALIASES = {
  conversation_with_tools: 'llm_only',
};

/** Normalise a runtime engine label to its canonical IFOC profile_type. */
export function canonicalProfileType(profileType) {
  return TYPE_ALIASES[profileType] || profileType;
}

/** Hex colour for a profile_type (fallback grey for unknown). */
export function getProfileColor(profileType) {
  const t = canonicalProfileType(profileType);
  return (IFOC_CONFIG[t] && IFOC_CONFIG[t].color) || PROFILE_FALLBACK_COLOR;
}

/** IFOC label (Ideate/Focus/Optimize/Coordinate) for a profile_type. */
export function getProfileLabel(profileType) {
  const t = canonicalProfileType(profileType);
  return (IFOC_CONFIG[t] && IFOC_CONFIG[t].label) || (profileType || 'profile');
}

/** Resolve a colour from a profile TAG by looking the profile up in configState. */
export function getProfileColorByTag(tag) {
  const p = profileForTag(tag);
  return p ? getProfileColor(p.profile_type) : PROFILE_FALLBACK_COLOR;
}

/** The loaded profile carrying this tag, or null. ONE lookup shape for every
 *  tag-keyed reading, so the colour and the engine class can never disagree. */
export function profileForTag(tag) {
  const t = String(tag ?? '').replace(/^@/, '');
  return (typeof window !== 'undefined' && window.configState && window.configState.profiles || [])
    .find(pr => String(pr.tag ?? '').replace(/^@/, '') === t) || null;
}

/** The IFOC engine class behind a profile tag (llm_only | rag_focused |
 *  tool_enabled | genie), or null when the tag resolves to no loaded profile —
 *  null means "not known", never a guessed class. */
export function profileTypeForTag(tag) {
  const p = profileForTag(tag);
  return p ? (p.profile_type || null) : null;
}

// Expose globally for non-module consumers (IIFE handlers, component renderers).
if (typeof window !== 'undefined') {
  window.ProfileColors = {
    IFOC_CONFIG,
    PROFILE_CLASS_COLORS,
    IFOC_ORDER,
    TYPE_ALIASES,
    PROFILE_FALLBACK_COLOR,
    canonicalProfileType,
    getProfileColor,
    getProfileLabel,
    getProfileColorByTag,
    profileForTag,
    profileTypeForTag,
  };
}
