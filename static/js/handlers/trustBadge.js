// ─────────────────────────────────────────────────────────────────────────────
// Trust Membrane — the reusable trust badge + detail + guided sign-off (Tier 5.2).
//
// One component, mounted wherever an agent appears: ⚪ Unsigned · 🟢 Trusted ·
// 🟠 Changed. The agent is the unit of trust; the badge is computed compositionally
// over its ingredients. This module owns the *entire* trust UX surface so every
// view renders it identically (consistency is the platform USP).
//
// Design: docs/roadmap/TIER_5.2_TRUST_MEMBRANE.md
//   • badgeHTML(state, opts)        → the pill (theme-aware, monochrome SVG, a11y)
//   • fetchAgent(id) / fetchAll()   → cached trust lookups
//   • openDetail(id, tag, anchor)   → the detail dialog (breakdown · verify · sign off)
//   • signAgentGuided(id, onStep)   → the bottom-up flow (sign ingredients, then agent)
//
// Emits `trust:changed` (detail: {profileId, state}) so any open view can refresh.
// ─────────────────────────────────────────────────────────────────────────────

const TOKEN = () => localStorage.getItem('tda_auth_token');
const API = '/api/v1/trust';
const _esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// Monochrome, single-stroke SVGs (no emoji, currentColor-driven — charter #2).
const ICONS = {
  unsigned: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9" stroke-dasharray="2.6 3"/></svg>',
  trusted: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l7 3v5c0 4.4-3 8-7 10-4-2-7-5.6-7-10V6z"/><path d="M9 12l2 2 4-4"/></svg>',
  changed: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.3 3.9 1.8 18a1.5 1.5 0 0 0 1.3 2.2h17.8a1.5 1.5 0 0 0 1.3-2.2L13.7 3.9a1.5 1.5 0 0 0-2.6 0z"/><path d="M12 9v4"/><circle cx="12" cy="17" r=".6" fill="currentColor"/></svg>',
  // Same shield as `trusted`, but its lower half is dashed: the artefact is verified,
  // the deployment beneath it is not yet attested. Monochrome, currentColor only.
  deployment_unattested: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l7 3v5c0 .7-.08 1.4-.23 2.06"/><path d="M5 6v5c0 4.4 3 8 7 10 1.9-.95 3.5-2.3 4.7-3.9" stroke-dasharray="2.6 3"/><path d="M12 3 5 6"/></svg>',
  verify: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>',
  sign: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>',
  x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>',
  spin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 3a9 9 0 1 0 9 9" class="tb-spin"/></svg>',
  // Industrial "signed" seal — a certified medal (circle + check + ribbon), distinct from the agent shield.
  seal: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="9" r="6"/><path d="M9.3 9.2 11 10.9l3.7-3.7"/><path d="M8.4 14 7 21l5-2.4L17 21l-1.4-7"/></svg>',
};

// State meta — the seal vocabulary. Colour reads from the SAME `--trust-seal-*` tokens the
// ring and roster consume (trust.css), so one integrity state renders one colour on every
// surface in BOTH themes. The raw hex is the DARK-theme token value, kept only as a fallback
// for a context without trust.css (Node/gate execution); the var wins wherever the page has
// it, which is what fixed the "two greens in light mode" divergence (FE-H3).
const META = {
  unsigned: { color: 'var(--trust-seal-unsigned, #94a3b8)', label: 'Unsigned', icon: ICONS.unsigned, tip: 'Shared and usable, but not validated.' },
  trusted: { color: 'var(--trust-seal-trusted, #34d399)', label: 'Trusted', icon: ICONS.trusted, tip: 'Signed and intact — every ingredient is validated.' },
  changed: { color: 'var(--trust-seal-changed, #f59e0b)', label: 'Changed', icon: ICONS.changed, tip: 'Was signed, but an ingredient changed since — re-sign needed.' },
  // The artefact is verified, but nobody has attested THIS deployment's model, tool
  // catalog and connector opt-ins. Distinct from unsigned (real publisher provenance
  // exists) and from changed (nothing drifted) — see core/trust/deployment.py.
  deployment_unattested: { color: 'var(--trust-seal-unattested, #38bdf8)', label: 'Deployment unattested', icon: ICONS.deployment_unattested,
    tip: 'The agent is publisher-signed and intact — but this deployment’s model, tools and connectors have not been attested here.' },
};
const metaFor = (s) => META[s] || META.unsigned;

// ── one-time CSS (theme-aware; motion-respecting) ────────────────────────────
function injectCSS() {
  if (document.getElementById('trust-badge-css')) return;
  const el = document.createElement('style');
  el.id = 'trust-badge-css';
  el.textContent = `
  .tb-badge{display:inline-flex;align-items:center;gap:0.34rem;font-size:0.76rem;font-weight:600;
    padding:0.16rem 0.5rem;border-radius:999px;border:1px solid;line-height:1;cursor:pointer;
    background:transparent;transition:background .15s ease,transform .12s ease,box-shadow .15s ease;white-space:nowrap;}
  .tb-badge:hover{background:color-mix(in srgb, currentColor 10%, transparent);}
  .tb-badge:active{transform:translateY(0.5px);}
  .tb-badge:focus-visible{outline:2px solid currentColor;outline-offset:2px;}
  .tb-badge svg{width:0.92em;height:0.92em;flex:0 0 auto;}
  .tb-badge.tb-lg{font-size:0.86rem;padding:0.28rem 0.7rem;}
  .tb-badge.tb-dot{padding:0.2rem;gap:0;}
  .tb-spin{animation:tb-rot .8s linear infinite;transform-origin:12px 12px;}
  @keyframes tb-rot{to{transform:rotate(360deg);}}
  @media (prefers-reduced-motion:reduce){.tb-spin{animation:none;}.tb-badge{transition:none;}}
  .tb-abadge{display:inline-flex;align-items:center;vertical-align:-0.14em;background:none;border:none;padding:0;color:inherit;cursor:pointer;}
  .tb-abadge svg{width:0.95em;height:0.95em;}
  .tb-abadge:hover{filter:brightness(1.18);}
  .tb-abadge.tb-faint{opacity:0.38;}
  .tb-abadge.tb-faint:hover{opacity:0.95;filter:none;}

  .tb-overlay{position:fixed;inset:0;background:rgba(2,6,23,0.55);backdrop-filter:blur(2px);
    display:flex;align-items:center;justify-content:center;z-index:12000;opacity:0;transition:opacity .16s ease;}
  .tb-overlay.tb-in{opacity:1;}
  .tb-dialog{width:min(460px,94vw);max-height:88vh;overflow:auto;background:var(--card-bg,#0f172a);
    color:var(--text-primary,#e2e8f0);border:1px solid var(--border-primary,#334155);border-radius:14px;
    box-shadow:0 24px 60px rgba(0,0,0,0.5);transform:translateY(8px) scale(.98);transition:transform .18s ease;}
  .tb-overlay.tb-in .tb-dialog{transform:none;}
  .tb-hd{display:flex;align-items:center;gap:0.6rem;padding:1rem 1.1rem 0.6rem;}
  .tb-hd .tb-title{font-weight:700;font-size:1.02rem;flex:1;}
  .tb-hd .tb-close{width:30px;height:30px;display:inline-flex;align-items:center;justify-content:center;
    border-radius:8px;border:1px solid var(--border-secondary,#334155);background:transparent;color:var(--text-muted,#94a3b8);cursor:pointer;}
  .tb-hd .tb-close:hover{background:var(--hover-bg-strong,rgba(255,255,255,0.06));color:var(--text-primary);}
  .tb-hd .tb-close svg{width:16px;height:16px;}
  .tb-sub{padding:0 1.1rem 0.4rem;color:var(--text-muted,#94a3b8);font-size:0.82rem;}
  .tb-body{padding:0.5rem 1.1rem 0.9rem;}
  .tb-sec-label{font-size:0.7rem;text-transform:uppercase;letter-spacing:0.05em;color:var(--text-muted,#94a3b8);margin:0.7rem 0 0.35rem;}
  .tb-ing{display:flex;align-items:center;gap:0.5rem;padding:0.42rem 0.55rem;border-radius:9px;
    border:1px solid var(--border-secondary,#293548);margin-bottom:0.35rem;font-size:0.83rem;}
  .tb-ing .tb-ing-ico{display:inline-flex;}.tb-ing .tb-ing-ico svg{width:1em;height:1em;}
  .tb-ing .tb-ing-kind{color:var(--text-muted,#94a3b8);text-transform:capitalize;min-width:64px;}
  .tb-ing .tb-ing-id{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:ui-monospace,monospace;font-size:0.78rem;}
  .tb-note{font-size:0.82rem;color:var(--text-muted,#94a3b8);margin:0.4rem 0 0.2rem;}
  .tb-actions{display:flex;gap:0.5rem;flex-wrap:wrap;padding:0.4rem 1.1rem 1.05rem;}
  .tb-btn{display:inline-flex;align-items:center;gap:0.4rem;font-size:0.83rem;font-weight:600;padding:0.46rem 0.8rem;
    border-radius:9px;border:1px solid var(--border-primary,#334155);background:transparent;color:var(--text-primary,#e2e8f0);
    cursor:pointer;transition:background .14s ease,border-color .14s ease;}
  .tb-btn svg{width:15px;height:15px;}
  .tb-btn:hover{background:var(--hover-bg-strong,rgba(255,255,255,0.06));}
  .tb-btn:disabled{opacity:0.5;cursor:default;}
  .tb-btn.tb-primary{border-color:transparent;background:var(--teradata-orange,#F15F22);color:#fff;}
  .tb-btn.tb-primary:hover{filter:brightness(1.06);background:var(--teradata-orange,#F15F22);}
  .tb-btn.tb-ok{border-color:color-mix(in srgb, var(--trust-pass, #34d399) 40%, transparent);color:var(--trust-pass, #34d399);}
  .tb-status{padding:0 1.1rem;font-size:0.82rem;min-height:1.1rem;color:var(--text-muted,#94a3b8);}
  .tb-status.tb-good{color:var(--trust-pass, #34d399);}.tb-status.tb-bad{color:var(--trust-warn, #f59e0b);}
  `;
  document.head.appendChild(el);
}

// ── the badge pill ───────────────────────────────────────────────────────────
// badgeHTML(state, {size:'sm'|'lg', label:true, dot:false, title})
function badgeHTML(state, opts = {}) {
  const m = metaFor(state);
  const cls = ['tb-badge', opts.size === 'lg' ? 'tb-lg' : '', opts.dot ? 'tb-dot' : ''].filter(Boolean).join(' ');
  const lbl = (opts.label === false || opts.dot) ? '' : `<span>${m.label}</span>`;
  const title = _esc(opts.title || m.tip);
  // TC-44: theme tooltip (u-tip), never the native grey title box — the standing rule for
  // trust surfaces. TC-88: role="status" is a live-region role and wrong on a button.
  return `<button type="button" class="${cls} u-tip" style="color:${m.color};border-color:${m.color}66"
    aria-label="Trust: ${m.label}. ${title}" data-tip="${title}">${m.icon}${lbl}</button>`;
}

// TC-91: stacked trust dialogs (agent dialog → ingredient popover) each listen for
// Escape on document; without a stack one keypress closed every open dialog at once.
// Each dialog registers its close fn; Escape acts only on the top of the stack.
const _dialogStack = [];

// ── cached lookups ───────────────────────────────────────────────────────────
const _cache = new Map();      // profileId -> trust dict
let _allCache = null;
// In-flight promise memos. A RESULT-only cache is not enough here: renderProfiles()
// paints the four IFOC containers back-to-back and each one calls mountAgentBadges(),
// so all four callers arrive before the first response lands, all miss _allCache, and
// all four issue the same request. /v1/trust/agents resolves every profile
// synchronously, so those duplicates serialise on the server event loop and cost ~4x a
// single call — on the login critical path. Memoising the PROMISE collapses them to one.
// (Same fix, same reason, as ensureVocabulary() in qualityDimensions.js.)
let _allInflight = null;
const _agentInflight = new Map();  // profileId -> Promise

// TC-87: all module caches (_cache/_allCache/_artCache/_signerCache) belong to ONE user.
// A logout/login within a page session previously reused the prior user's signer verdict
// and trust map (sign buttons offered/hidden wrongly until a hard reload). Every cache
// read routes through this token check; a changed token drops everything.
let _cacheToken = null;
function _assertCacheUser() {
  const t = TOKEN();
  if (t !== _cacheToken) {
    _cacheToken = t;
    _cache.clear(); _allCache = null;
    _allInflight = null; _agentInflight.clear();
    try { _artCache.clear(); } catch (_) { /* not yet defined at first call */ }
    _signerCache = null;
  }
}

async function fetchAgent(profileId, { force = false } = {}) {
  _assertCacheUser();   // TC-87
  if (!force && _cache.has(profileId)) return _cache.get(profileId);
  if (!force && _agentInflight.has(profileId)) return _agentInflight.get(profileId);
  const tok = TOKEN();
  let p;
  p = (async () => {
    try {
      const r = await fetch(`${API}/agent/${encodeURIComponent(profileId)}`, { headers: { Authorization: `Bearer ${tok}` } });
      if (!r.ok) return null;
      const j = await r.json();
      const t = j.trust || null;
      // TC-87: only populate the cache if this response still belongs to the cached
      // user. A request begun before a logout/login must never seed the next user's map.
      if (t && TOKEN() === tok) _cache.set(profileId, t);
      return t;
    } catch (_) { return null; }
    // Clear the memo only if it is still OURS: _assertCacheUser() may already have
    // dropped it and a newer request may own the slot.
    finally { if (_agentInflight.get(profileId) === p) _agentInflight.delete(profileId); }
  })();
  _agentInflight.set(profileId, p);
  return p;
}

async function fetchAll({ force = false } = {}) {
  _assertCacheUser();   // TC-87
  if (!force && _allCache) return _allCache;
  if (!force && _allInflight) return _allInflight;
  const tok = TOKEN();
  let p;
  p = (async () => {
    try {
      const r = await fetch(`${API}/agents`, { headers: { Authorization: `Bearer ${tok}` } });
      if (!r.ok) return { agents: [] };
      const j = await r.json();
      // TC-87: a response begun before a logout/login must not seed the next user's map.
      if (TOKEN() === tok) {
        _allCache = j;
        (j.agents || []).forEach(a => _cache.set(a.profile_id, { state: a.state, composition_state: a.composition_state, ingredients: a.ingredients, blocking: a.blocking }));
      }
      return j;
    } catch (_) { return { agents: [] }; }
    // Clear the memo only if it is still OURS (see fetchAgent).
    finally { if (_allInflight === p) _allInflight = null; }
  })();
  _allInflight = p;
  return p;
}

function invalidate(profileId) {
  if (profileId) { _cache.delete(profileId); _agentInflight.delete(profileId); }
  _allCache = null; _allInflight = null; _artCache.clear();
}

// ── Per-artefact "signed" badge (shown next to any artefact name, all categories) ──
const _artCache = new Map();   // "kind:id" -> state

// Small industrial seal, icon-only + tooltip, clickable → the trust popover.
// A green/amber seal means SIGNED (intact / changed-since) and is shown to everyone.
// An UNSIGNED artefact shows a faint "click to sign off" seal ONLY to signers (admins) —
// non-signers see nothing, keeping their view a clean binary (signed or absent).
function artifactBadgeHTML(state, { signer = false } = {}) {
  if (state === 'unsigned') {
    if (!signer) return '';
    return `<button type="button" class="tb-abadge tb-faint u-tip" data-tb-open data-tip="Not signed — click to sign off" aria-label="Not signed — click to sign off" style="color:var(--text-muted,#94a3b8)">${ICONS.seal}</button>`;
  }
  const m = metaFor(state);
  const label = state === 'trusted' ? 'Signed & intact — click for details'
    : state === 'deployment_unattested' ? 'Publisher-signed — this deployment is not attested — click for details'
    : 'Signed — changed since sign-off — click for details';
  return `<button type="button" class="tb-abadge u-tip" data-tb-open style="color:${m.color}" data-tip="${label}" aria-label="${label}">${ICONS.seal}</button>`;
}

// Batch-resolve artefact states (cached). items: [{kind, id}] → {"kind:id": state}.
async function fetchArtifacts(items, { force = false } = {}) {
  _assertCacheUser();   // TC-87
  const need = (force ? items : items.filter(it => it && !_artCache.has(`${it.kind}:${it.id}`))).filter(Boolean);
  if (need.length) {
    try {
      const r = await fetch(`${API}/artifacts`, { method: 'POST', headers: { Authorization: `Bearer ${TOKEN()}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ items: need }) });
      if (r.ok) { const j = await r.json(); Object.entries(j.states || {}).forEach(([k, v]) => _artCache.set(k, v)); }
    } catch (_) {}
  }
  const out = {};
  items.forEach(it => { if (it) { const key = `${it.kind}:${it.id}`; out[key] = _artCache.get(key) || 'unsigned'; } });
  return out;
}

// Scan a container for `[data-trust-artifact-id]` slots and fill each with its badge.
// The roll-out primitive: a renderer just drops
//   <span data-trust-artifact-kind="skill" data-trust-artifact-id="X"></span>
// next to a name, then calls TrustBadge.mountArtifactBadges(container).
async function mountArtifactBadges(root) {
  injectCSS();
  const scope = root || document;
  // Query ALL slots every call (no mount-flag guard): fills are idempotent and the
  // fetch is cached, so re-rendering a list can never leave a slot half-filled — the
  // partial-render race that made some cards show no badge and others a stray one.
  const els = [...scope.querySelectorAll('[data-trust-artifact-id]')];
  if (!els.length) return;
  const items = els.map(e => ({ kind: e.getAttribute('data-trust-artifact-kind'), id: e.getAttribute('data-trust-artifact-id') }));
  const [states, signer] = await Promise.all([fetchArtifacts(items), isSigner()]);
  els.forEach(e => {
    if (!e.isConnected) return;
    const kind = e.getAttribute('data-trust-artifact-kind');
    const id = e.getAttribute('data-trust-artifact-id');
    const st = states[`${kind}:${id}`] || 'unsigned';
    e.innerHTML = artifactBadgeHTML(st, { signer });
    const btn = e.querySelector('[data-tb-open]');
    if (btn) btn.addEventListener('click', (ev) => {
      ev.stopPropagation(); ev.preventDefault();
      openArtifactTrust(kind, id, e.getAttribute('data-trust-artifact-name') || '');
    });
  });
}

const _KIND_LABEL = {
  skill: 'Skill', extension: 'Extension', ontology: 'Ontology',
  knowledge: 'Knowledge repository', planner: 'Planner repository', agent_pack: 'Agent pack',
  assurance_module: 'Assurance module',
};

// Read-only "publisher signed" seal for MARKETPLACE cards. Unlike mountArtifactBadges it
// never offers a sign affordance (you don't sign a publisher's artefact) — it only shows a
// green/amber seal when the source is signed, nothing otherwise. Slots carry
// `data-trust-market-kind` / `data-trust-market-id` (the source artefact).
async function mountMarketplaceTrust(root) {
  injectCSS();
  const scope = root || document;
  const els = [...scope.querySelectorAll('[data-trust-market-id]')];
  if (!els.length) return;
  const items = els.map(e => ({ kind: e.getAttribute('data-trust-market-kind'), id: e.getAttribute('data-trust-market-id') }));
  const states = await fetchArtifacts(items);
  els.forEach(e => {
    if (!e.isConnected) return;
    const st = states[`${e.getAttribute('data-trust-market-kind')}:${e.getAttribute('data-trust-market-id')}`] || 'unsigned';
    if (st !== 'trusted' && st !== 'changed' && st !== 'deployment_unattested') { e.innerHTML = ''; return; }
    const m = metaFor(st);
    const label = st === 'trusted' ? 'Signed by the publisher'
      : st === 'deployment_unattested' ? 'Publisher-signed — deployment not attested here'
      : 'Publisher-signed — changed since';
    e.innerHTML = `<span class="tb-abadge u-tip" style="color:${m.color};cursor:default" data-tip="${label}" role="img" aria-label="${label}">${ICONS.seal}</span>`;
  });
}

/** Attest THIS deployment's half of an agent's trust envelope — the deployer's act.
 *
 *  Distinct from sign-off: signing attests the portable half (composition, posture,
 *  capability) and travels with the artefact; attesting records the resolved model,
 *  the reachable tool catalog and the connector opt-ins ON THIS INSTANCE, which never
 *  travel. A publisher-signed agent lands `deployment_unattested` until this runs.
 *
 *  Exported because it has TWO callers — this module's own detail popover and the Trust
 *  Circle's SIGNED station. The ring used to offer "Sign this agent" for an already
 *  signed agent, i.e. its most prominent action performed an act that could not help.
 *  One implementation, two callers — never a second copy of the endpoint call.
 */
async function attestDeployment(profileId) {
  try {
    const r = await fetch(`${API}/agent/${encodeURIComponent(profileId)}/attest-deployment`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${TOKEN()}`, 'Content-Type': 'application/json' },
      body: '{}' });
    if (r.ok) { invalidate(profileId); return { ok: true }; }
    const j = await r.json().catch(() => ({}));
    // Attesting is OWNERSHIP-scoped, not admin-gated (core/trust/deployment.py: the
    // deployment half is the deployer's own statement of intent). A refusal therefore
    // means "not yours", not "not senior enough" — say the true thing, and prefer the
    // server's own reason so a policy change here never needs a copy edit there.
    if (r.status === 404) return { ok: false, error: j.error || 'You can only attest a deployment you own.' };
    return { ok: false, error: j.error || 'Could not attest this deployment.' };
  } catch (e) {
    return { ok: false, error: 'Could not attest this deployment.' };
  }
}

// Sign a single artefact via its kind's sign-off endpoint (URL uses hyphens: agent_pack →
// agent-pack). Returns {ok, band, note} or {ok:false, error, blocking?}.
async function signArtifact(kind, id) {
  const seg = kind.replace(/_/g, '-');
  const r = await fetch(`${API}/${seg}/${encodeURIComponent(id)}/sign-off`, {
    method: 'POST', headers: { Authorization: `Bearer ${TOKEN()}`, 'Content-Type': 'application/json' } });
  const j = await r.json().catch(() => ({}));
  // TC-37: a 403 is the server's sentence (owner gate, tier gate, separation of duties) —
  // show it. 'admin only' was wrong for the commonest case: an admin signing another
  // user's artefact.
  if (r.status === 403) return { ok: false, error: j.error || 'You are not permitted to sign this artefact.' };
  if (r.status === 404) return { ok: false, error: 'Artefact not found.' };
  if (r.status === 422) {
    if (Array.isArray(j.blocking)) return { ok: false, error: `${j.blocking.length} member(s) must be signed first.`, blocking: j.blocking };
    return { ok: false, error: (j.validation && j.validation.note) || j.reason || 'Not signable.' };
  }
  if (!r.ok || !j.signed) return { ok: false, error: (j.error || 'Sign-off failed.') };
  _artCache.delete(`${kind}:${id}`);
  return { ok: true, band: (j.validation || {}).band, note: (j.validation || {}).note };
}

async function verifyArtifact(kind, id) {
  try {
    const r = await fetch(`${API}/verify`, {
      method: 'POST', headers: { Authorization: `Bearer ${TOKEN()}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind, artifact_id: id }) });
    return r.ok ? await r.json() : null;
  } catch (_) { return null; }
}

// The per-artefact trust popover — state, verify, and (signer-only) sign off / re-sign.
// PROP-009 §5.2 — which answers used this artefact: "Used by 214 answers · 31 with this
// version". Read from the evidence index (the Ontology Suite's Evidence routes); absent
// routes or a failed read leave the line empty, never a guessed count.
function _usageWord(n) { return `${n} answer${n === 1 ? '' : 's'}`; }
function _usageHtml(u) {
  if (!u || typeof u.answers !== 'number') return '';
  if (!u.answers) return `<div class="tb-note tb-usage-line">Not used by any recorded answer yet.</div>`;
  const since = (typeof u.since_current === 'number')
    ? (u.since_current === u.answers ? ' · every one with this version'
      : ` · ${_usageWord(u.since_current)} with this version, ${u.answers - u.since_current} with an earlier one`)
    : '';
  const rows = (u.turns || []).slice(0, 8).map((t) =>
    `<div class="tb-ing"><span class="tb-ing-kind">${_esc(t.profile_tag ? '@' + t.profile_tag : 'answer')}</span>`
    + `<span class="tb-ing-id">${_esc(t.answered_at ? new Date(t.answered_at).toLocaleString() : '')}`
    + ` · turn ${_esc(t.turn)} · ${t.same_as_current ? 'this version' : 'an earlier version'}</span></div>`).join('');
  return `<details class="tb-usage-line"><summary class="tb-note" style="cursor:pointer">Used by ${_usageWord(u.answers)}${since}</summary>${rows}</details>`;
}
async function _fillUsage(host, kind, id) {
  if (!host) return;
  try {
    const r = await fetch(`/api/v1/evidence/artifact/${encodeURIComponent(kind)}/${encodeURIComponent(id)}/usage?limit=8`,
                          { headers: { Authorization: `Bearer ${TOKEN()}` } });
    if (!r.ok) return;
    host.innerHTML = _usageHtml(await r.json());
  } catch (_) { /* the dialog stands without its usage line */ }
}

async function openArtifactTrust(kind, id, name) {
  injectCSS();
  const label = _KIND_LABEL[kind] || kind;
  const overlay = document.createElement('div');
  overlay.className = 'tb-overlay';
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-modal', 'true');
  overlay.setAttribute('aria-label', `Trust for ${label}`);
  overlay.innerHTML = `<div class="tb-dialog"><div class="tb-hd">
      <span class="tb-badge-slot"></span>
      <span class="tb-title">Trust · ${_esc(label)}</span>
      <button class="tb-close" aria-label="Close">${ICONS.x}</button></div>
    <div class="tb-sub"></div><div class="tb-body"></div>
    <div class="tb-status" aria-live="polite"></div>
    <div class="tb-actions"></div></div>`;
  document.body.appendChild(overlay);
  requestAnimationFrame(() => overlay.classList.add('tb-in'));
  const close = () => { overlay.classList.remove('tb-in'); setTimeout(() => overlay.remove(), 180); document.removeEventListener('keydown', onKey);
    const di = _dialogStack.indexOf(close); if (di >= 0) _dialogStack.splice(di, 1); };
  const onKey = (e) => { if (e.key === 'Escape' && _dialogStack[_dialogStack.length - 1] === close) close(); };   // TC-91: topmost only
  document.addEventListener('keydown', onKey);
  _dialogStack.push(close);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
  overlay.querySelector('.tb-close').addEventListener('click', close);

  const signer = await isSigner();
  const render = async () => {
    const states = await fetchArtifacts([{ kind, id }], { force: true });
    const st = states[`${kind}:${id}`] || 'unsigned';
    // #23 — the per-artefact endpoint carries the CAVEAT recorded at sign time, which the
    // batch state map (a kind:id → state string) structurally cannot. One extra call for
    // one artefact, only while its dialog is open. Failure loses the caveat, never the
    // dialog.
    let rec = null;
    try {
      const rr = await fetch(`${API}/artifact/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`,
                             { headers: { Authorization: `Bearer ${TOKEN()}` } });
      if (rr.ok) rec = await rr.json();
    } catch (_) { /* the badge must render even if its caveat cannot */ }
    const m = metaFor(st);
    const word = st === 'unsigned' ? 'Not signed'
      : st === 'trusted' ? 'Signed'
      : st === 'deployment_unattested' ? 'Deployment unattested' : 'Changed';
    overlay.querySelector('.tb-badge-slot').innerHTML =
      `<span class="tb-badge" style="color:${m.color};border-color:${m.color}66;cursor:default;pointer-events:none">${ICONS.seal}<span>${word}</span></span>`;
    overlay.querySelector('.tb-sub').textContent = `${label} · ${_short(id)}${name ? ' · ' + name : ''}`;
    // #23 — the CAVEAT this signature carries, if any. A `partial` band ("content not
    // fully scanned for injection") was recorded at sign time and then rendered
    // pixel-identical to an earned signature, so "signed" meant two different things
    // with no way to tell them apart. Shown as a qualifier, never as a fifth state and
    // never as a different colour (Rule 8.4) — the signature is real; what it covers is
    // what needed saying.
    const cav = st !== 'unsigned' ? (rec && rec.caveat) : null;
    const caveatHtml = cav
      ? `<div class="tb-note" style="border-left:2px solid var(--trust-warn,#f59e0b);padding-left:8px;">
           <strong>Signed with a caveat.</strong> ${_esc(cav.note || cav.band)}
         </div>`
      : '';
    overlay.querySelector('.tb-body').innerHTML = (
      st === 'trusted' ? `<div class="tb-note">Signed and intact — this ${label.toLowerCase()} has not changed since sign-off.</div>`
      : st === 'changed' ? `<div class="tb-note">This ${label.toLowerCase()} was signed, but its content changed since. Re-sign to restore trust.</div>`
      : `<div class="tb-note">This ${label.toLowerCase()} is not signed. ${signer ? 'Sign it off to attest it.' : 'Sign-off is admin-only.'}</div>`
    ) + caveatHtml + `<div class="tb-usage"></div>`;
    _fillUsage(overlay.querySelector('.tb-usage'), kind, id);

    const actions = overlay.querySelector('.tb-actions');
    const status = overlay.querySelector('.tb-status');
    actions.innerHTML = '';
    if (st !== 'unsigned') {
      const vb = _btn(`${ICONS.verify}Verify`, 'tb-btn');
      vb.addEventListener('click', async () => {
        status.className = 'tb-status'; status.textContent = 'Verifying…';
        const v = await verifyArtifact(kind, id);
        if (v && v.verified) { status.className = 'tb-status tb-good'; status.textContent = `Signature verified offline · key ${_short(v.fingerprint || '')}`; }
        else { status.className = 'tb-status tb-bad'; status.textContent = v && v.found ? 'Signature did not verify.' : 'No attestation to verify.'; }
      });
      actions.appendChild(vb);
    }
    if (signer && st !== 'trusted') {
      // TC-86: remember the offered act so a FAILED attempt restores "Re-sign", not "Sign off"
      // (Rule 8.5 — the offered act is the act that is owed).
      const signLabel = `${ICONS.sign}${st === 'changed' ? 'Re-sign' : 'Sign off'}`;
      const sb = _btn(signLabel, 'tb-btn tb-primary');
      sb.addEventListener('click', async () => {
        sb.disabled = true; sb.innerHTML = `${ICONS.spin}Signing…`;
        status.className = 'tb-status'; status.textContent = '';
        const res = await signArtifact(kind, id);
        if (res.ok) {
          status.className = 'tb-status tb-good';
          status.textContent = `Signed${res.band && res.band !== 'clean' ? ' (' + res.band + ')' : ''}.`;
          if (window.TrustBadge) window.TrustBadge.mountArtifactBadges(document);
          await render();
        } else {
          sb.disabled = false; sb.innerHTML = signLabel;
          status.className = 'tb-status tb-bad'; status.textContent = res.error;
          if (Array.isArray(res.blocking) && res.blocking.length) {
            overlay.querySelector('.tb-body').innerHTML +=
              `<div class="tb-sec-label">Members that must be signed first</div>` +
              res.blocking.map(b => `<div class="tb-ing"><span class="tb-ing-kind">${_esc(b.kind)}</span><span class="tb-ing-id">${_esc(_short(b.artifact_id))}</span></div>`).join('');
          }
        }
      });
      actions.appendChild(sb);
    }
  };
  overlay.querySelector('.tb-body').innerHTML = `<div class="tb-note">${ICONS.spin} Resolving…</div>`;
  await render();
}

// Agent (compositional) trust shield for profile LISTS. Same binary rule as the
// artefact seal: shown only when SIGNED (green intact / amber changed) — unsigned
// agents get no badge. Click opens the trust detail. A renderer drops
//   <span data-trust-agent-id="PID" data-trust-agent-tag="TAG"></span>
// next to a profile name, then calls TrustBadge.mountAgentBadges(container).
async function mountAgentBadges(root) {
  injectCSS();
  const scope = root || document;
  const els = [...scope.querySelectorAll('[data-trust-agent-id]')];
  if (!els.length) return;
  const j = await fetchAll();
  const map = {}; (j.agents || []).forEach(a => { map[a.profile_id] = a.state; });
  els.forEach(e => {
    if (!e.isConnected) return;
    const id = e.getAttribute('data-trust-agent-id');
    const st = map[id] || 'unsigned';
    if (st !== 'trusted' && st !== 'changed' && st !== 'deployment_unattested') { e.innerHTML = ''; return; }
    e.innerHTML = badgeHTML(st, { label: false });
    const tag = e.getAttribute('data-trust-agent-tag') || '';
    const btn = e.querySelector('button');
    if (btn) btn.addEventListener('click', (ev) => { ev.stopPropagation(); openDetail(id, tag); });
  });
}

// ── the guided bottom-up sign-off ────────────────────────────────────────────
// Signs every blocking ingredient, then the agent. onStep(msg) narrates progress.
// Returns {ok, state, error}. A 403 carries the server's own reason (TC-37).
async function signAgentGuided(profileId, onStep = () => {}) {
  const auth = { headers: { Authorization: `Bearer ${TOKEN()}`, 'Content-Type': 'application/json' } };
  const t = await fetchAgent(profileId, { force: true });
  const blocking = (t && t.blocking) || [];
  const caveats = [];   // #22 — per-ingredient bands, so a partial signature can say so
  for (const b of blocking) {
    // Name the artefact wherever we ask about it. The id is the fallback, never the
    // headline: the operator reads THIS line when deciding what to do about a blocker,
    // and "knowledge 2" is not something anyone can judge.
    const who = b.label ? `${b.kind} “${b.label}”` : `${b.kind} ${_short(b.artifact_id)}`;
    onStep(`Signing ${who}…`);
    const r = await fetch(`${API}/${b.kind}/${encodeURIComponent(b.artifact_id)}/sign-off`, { method: 'POST', ...auth });
    if (r.status === 403) { const j = await r.json().catch(() => ({})); return { ok: false, error: j.error ? `${who}: ${j.error}` : `You are not permitted to sign ${who}.`, blocker: b }; }   // TC-37
    if (r.status === 422) { const j = await r.json().catch(() => ({})); return { ok: false, error: `${who} is not signable: ${(j.validation || {}).note || 'nonconformant'}`, blocker: b }; }
    if (!r.ok) return { ok: false, error: `Could not sign ${who}.`, blocker: b };
    // #22 — every leaf sign-off returns a 3-band `validation`, and this loop discarded
    // it. So a corpus signed at band `partial` ("content not fully scanned for
    // injection") reported the same unqualified success as an earned one, and the
    // operator learned nothing about what they had just vouched for. Collect the
    // caveats; the caller reports them.
    try {
      const j = await r.json();
      const v = j && j.validation;
      if (v && v.band && v.band !== 'clean') {
        caveats.push({ kind: b.kind, label: b.label || b.artifact_id,
                       band: v.band, status: v.status, note: v.note });
      }
    } catch (_) { /* a body we cannot read must not fail a sign that succeeded */ }
  }
  onStep('Signing the agent…');
  const r = await fetch(`${API}/agent/${encodeURIComponent(profileId)}/sign-off`, { method: 'POST', ...auth });
  const j = await r.json().catch(() => ({}));
  if (r.status === 403) return { ok: false, error: j.error || 'You are not permitted to sign this agent.' };   // TC-37
  if (r.status === 409) {
    const names = (Array.isArray(j.blocking) ? j.blocking : []).map(b => b.label || `${b.kind} ${_short(b.artifact_id)}`);
    return { ok: false, error: names.length ? `Still unsigned: ${names.join(', ')}.` : 'Some ingredients still need signing.' };
  }
  if (!r.ok || !j.signed) return { ok: false, error: (j.error || 'Sign-off failed.') };
  invalidate(profileId);
  const state = (j.trust || {}).state || 'trusted';
  // The AGENT's own verdict carries a band too ("every answer gate is in observe mode"),
  // and it is the one the operator most needs: it describes what they just vouched for.
  const agentV = j.validation || {};
  if (agentV.band && agentV.band !== 'clean') {
    caveats.unshift({ kind: 'agent', label: 'this agent', band: agentV.band,
                      status: agentV.status, note: agentV.note });
  }
  UderiaBus.emit('uderia:trust-changed', { profileId, state });
  return { ok: true, state, caveats };
}

async function verifyAgent(profileId) {
  try {
    const r = await fetch(`${API}/verify`, { method: 'POST', headers: { Authorization: `Bearer ${TOKEN()}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: 'agent', artifact_id: profileId }) });
    return r.ok ? await r.json() : null;
  } catch (_) { return null; }
}

const _short = (id) => String(id || '').length > 22 ? String(id).slice(0, 10) + '…' + String(id).slice(-8) : String(id || '');

// ── the detail dialog (understand · verify · sign off) ───────────────────────
// Is the current user permitted to sign off (a signer = admin tier)? Cached /auth/me lookup.
// (declared early via _assertCacheUser's reset; kept here for readability)
var _signerCache = null;
async function isSigner() {
  _assertCacheUser();   // TC-87
  if (_signerCache !== null) return _signerCache;
  try {
    const r = await fetch('/api/v1/auth/me', { headers: { Authorization: `Bearer ${TOKEN()}` } });
    const j = r.ok ? await r.json() : null; const u = j && j.user;
    _signerCache = !!(u && (u.is_admin || u.profile_tier === 'admin'));
  } catch (_) { _signerCache = false; }
  return _signerCache;
}

async function openDetail(profileId, tag) {
  injectCSS();
  let canSign = false;   // resolved before first render (admin-only sign-off)
  const overlay = document.createElement('div');
  overlay.className = 'tb-overlay';
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-modal', 'true');
  overlay.setAttribute('aria-label', `Trust for @${tag || profileId}`);
  overlay.innerHTML = `<div class="tb-dialog"><div class="tb-hd">
      <span class="tb-badge-slot"></span>
      <span class="tb-title">Trust · @${_esc(tag || profileId)}</span>
      <button class="tb-close" aria-label="Close">${ICONS.x}</button></div>
    <div class="tb-sub"></div><div class="tb-body"></div>
    <div class="tb-status" aria-live="polite"></div>
    <div class="tb-actions"></div></div>`;
  document.body.appendChild(overlay);
  requestAnimationFrame(() => overlay.classList.add('tb-in'));

  const close = () => { overlay.classList.remove('tb-in'); setTimeout(() => overlay.remove(), 180); document.removeEventListener('keydown', onKey);
    const di = _dialogStack.indexOf(close); if (di >= 0) _dialogStack.splice(di, 1); };
  const onKey = (e) => { if (e.key === 'Escape' && _dialogStack[_dialogStack.length - 1] === close) close(); };   // TC-91: topmost only
  document.addEventListener('keydown', onKey);
  _dialogStack.push(close);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
  overlay.querySelector('.tb-close').addEventListener('click', close);

  const render = (t) => {
    const m = metaFor(t ? t.state : 'unsigned');
    overlay.querySelector('.tb-badge-slot').innerHTML = badgeHTML(t ? t.state : 'unsigned', { size: 'lg' });
    overlay.querySelector('.tb-sub').textContent = m.tip;
    const ings = (t && t.ingredients) || [];
    let body = '';
    if (ings.length) {
      body += `<div class="tb-sec-label">Composition — a signed agent contains only signed ingredients</div>`;
      body += ings.map(i => {
        const im = metaFor(i.state);
        return `<div class="tb-ing"><span class="tb-ing-ico" style="color:${im.color}">${im.icon}</span>
          <span class="tb-ing-kind">${_esc(i.kind)}</span><span class="tb-ing-id">${_esc(_short(i.artifact_id))}</span>
          <span class="tb-badge" style="color:${im.color};border-color:${im.color}66;cursor:default;pointer-events:none">${im.label}</span></div>`;
      }).join('');
    } else {
      body += `<div class="tb-note">This agent has no validated ingredients (ontology / skills) — it is trusted on its own attestation.</div>`;
    }
    const blocking = (t && t.blocking) || [];
    if (blocking.length) body += `<div class="tb-note">${blocking.length} ingredient${blocking.length === 1 ? '' : 's'} still need${blocking.length === 1 ? 's' : ''} signing before this agent can be trusted.</div>`;

    // The deployment half — the model, tool catalog and connector opt-ins resolved HERE.
    // It never travels with a pack, so a subscriber attests it themselves. Drift is named
    // rather than merely flagged: an unexplained amber trains people to click through it.
    const dep = t && t.deployment;
    if (dep && dep.required) {
      const dm = metaFor(dep.state === 'trusted' ? 'trusted'
        : dep.state === 'changed' ? 'changed' : 'deployment_unattested');
      body += `<div class="tb-sec-label">This deployment</div>`;
      body += `<div class="tb-ing"><span class="tb-ing-ico" style="color:${dm.color}">${dm.icon}</span>
        <span class="tb-ing-kind">model · tools · connectors</span>
        <span class="tb-badge" style="color:${dm.color};border-color:${dm.color}66;cursor:default;pointer-events:none">${
          dep.state === 'trusted' ? 'Attested' : dep.state === 'changed' ? 'Drifted' : 'Not attested'}</span></div>`;
      if ((dep.drift || []).length) {
        body += `<div class="tb-note">Since it was attested: ${
          dep.drift.map(d => _esc(d)).join(' · ')}</div>`;
      } else if (dep.state !== 'trusted') {
        body += `<div class="tb-note">The artefact is verified, but nobody has attested which model answers, which tools it can reach, or which connectors are enabled on this instance.</div>`;
      }
    }
    overlay.querySelector('.tb-body').innerHTML = body;

    const actions = overlay.querySelector('.tb-actions');
    const status = overlay.querySelector('.tb-status');
    actions.innerHTML = '';
    // Verify (only meaningful once signed)
    if (t && t.state !== 'unsigned') {
      const vb = _btn(`${ICONS.verify}Verify`, 'tb-btn');
      vb.addEventListener('click', async () => {
        status.className = 'tb-status'; status.textContent = 'Verifying signature…';
        const v = await verifyAgent(profileId);
        if (v && v.verified) { status.className = 'tb-status tb-good'; status.textContent = `Signature verified offline · key ${_short(v.fingerprint || '')}`; }
        else { status.className = 'tb-status tb-bad'; status.textContent = v && v.found ? 'Signature did not verify.' : 'No attestation to verify.'; }
      });
      actions.appendChild(vb);
    }
    // Attest this deployment — the DEPLOYER's act, not the author's, so it is offered
    // independently of the artefact sign-off (a subscriber must be able to attest a
    // deployment of a pack somebody else signed).
    // NOT gated on `canSign` (approver tier): attesting a deployment is the OWNER's act,
    // and this dialog only ever opens for the caller's own profiles. Gating it on admin
    // left a non-admin owner of a forked pack with a permanently ◒ badge and no button —
    // the drift detector could never take its baseline. Sign-off below stays tier-gated:
    // that half vouches for the artefact, which a third party legitimately can.
    if (dep && dep.required && dep.state !== 'trusted') {
      const needsRe = dep.state === 'changed';
      const db = _btn(`${ICONS.seal}${needsRe ? 'Re-attest deployment' : 'Attest deployment'}`,
                      `tb-btn${(t && t.state === 'deployment_unattested') ? ' tb-primary' : ''}`);
      db.addEventListener('click', async () => {
        db.disabled = true; db.innerHTML = `${ICONS.spin}Attesting…`;
        status.className = 'tb-status'; status.textContent = '';
        const res = await attestDeployment(profileId);
        if (res.ok) {
          status.className = 'tb-status tb-good';
          status.textContent = 'Deployment attested on this instance.';
          render(await fetchAgent(profileId, { force: true }));
        } else {
          db.disabled = false; db.innerHTML = `${ICONS.seal}Attest deployment`;
          status.className = 'tb-status tb-bad'; status.textContent = res.error;
        }
      });
      actions.appendChild(db);
    }
    // Sign off (guided) — admin only (signer tier). Non-signers see an informative note.
    // `deployment_unattested` means the artefact IS signed, so no re-sign is offered.
    if (!t || (t.state !== 'trusted' && t.state !== 'deployment_unattested')) {
      if (canSign) {
        const agentSignLabel = `${ICONS.sign}${t && t.state === 'changed' ? 'Re-sign' : 'Sign off'}`;   // TC-86
        const sb = _btn(agentSignLabel, 'tb-btn tb-primary');
        sb.addEventListener('click', async () => {
          sb.disabled = true; sb.innerHTML = `${ICONS.spin}Signing…`;
          status.className = 'tb-status'; status.textContent = '';
          const res = await signAgentGuided(profileId, (msg) => { status.textContent = msg; });
          if (res.ok) { status.className = 'tb-status tb-good'; status.textContent = 'Agent signed — now trusted.'; const nt = await fetchAgent(profileId, { force: true }); render(nt); }
          else { sb.disabled = false; sb.innerHTML = agentSignLabel; status.className = 'tb-status tb-bad'; status.textContent = res.error; }
        });
        actions.appendChild(sb);
      } else {
        const note = document.createElement('span');
        note.className = 'tb-status'; note.style.cssText = 'padding:0.46rem 0;';
        note.textContent = 'Sign-off is admin-only.';
        actions.appendChild(note);
      }
    }
    const cfg = _btn('Configure agent', 'tb-btn');
    cfg.addEventListener('click', () => { close(); if (window.loadOntologyView) { /* no-op: stay */ } if (typeof window.openProfileEditor === 'function') window.openProfileEditor(profileId); });
    actions.appendChild(cfg);
  };

  overlay.querySelector('.tb-body').innerHTML = `<div class="tb-note">${ICONS.spin} Resolving trust…</div>`;
  const [t0, signer] = await Promise.all([fetchAgent(profileId, { force: true }), isSigner()]);
  canSign = signer;
  render(t0);
}

function _btn(html, cls) { const b = document.createElement('button'); b.type = 'button'; b.className = cls; b.innerHTML = html; return b; }

// ── public surface ───────────────────────────────────────────────────────────
const TrustBadge = { _usageHtml, injectCSS, badgeHTML, artifactBadgeHTML, fetchAgent, fetchAll, fetchArtifacts, mountArtifactBadges, mountAgentBadges, mountMarketplaceTrust, signArtifact, verifyArtifact, openArtifactTrust, invalidate, openDetail, signAgentGuided, verifyAgent, attestDeployment, ICONS, META, metaFor, colorFor: (s) => metaFor(s).color };
if (typeof window !== 'undefined') window.TrustBadge = TrustBadge;
if (typeof document !== 'undefined') injectCSS();   // TC-88: safe under Node import
export { injectCSS, badgeHTML, artifactBadgeHTML, fetchAgent, fetchAll, fetchArtifacts, mountArtifactBadges, mountAgentBadges, mountMarketplaceTrust, signArtifact, verifyArtifact, openArtifactTrust, invalidate, openDetail, signAgentGuided, verifyAgent, attestDeployment, ICONS, META, metaFor };
export default TrustBadge;
